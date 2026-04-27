#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include <vector>
#include <thread>
#include <atomic>
#include <mutex>
#include <cstring>
#include <algorithm>
#include <string>
#include <fstream>
#include <iostream>
#include <condition_variable>
#include <complex>
#include <cmath>
#include <curl/curl.h>
#include "SoundTouch/BPMDetect.h"

std::atomic<int> current_stream_session{0};
std::atomic<int> current_preload_session{0};

#ifdef _WIN32
  #include <windows.h>
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT
#endif

#define SAMPLE_RATE 44100
#define CHANNELS 2
#define BUFFER_SECONDS 10  // 🟢 10-Second DJ Scratch RAM Buffer
#define CHUNK_SIZE 16384 

struct RingBuffer {
    std::vector<float> data;
    size_t size;
    std::atomic<size_t> write_pos{0};
    std::atomic<size_t> read_pos{0};
    std::atomic<size_t> available{0};

    void init(size_t seconds) {
        size = seconds * SAMPLE_RATE * CHANNELS;
        data.resize(size, 0.0f);
        clear();
    }
    void clear() {
        write_pos = 0; read_pos = 0; available = 0;
        std::fill(data.begin(), data.end(), 0.0f);
    }
    void write(const float* input, size_t frames) {
        size_t samples = frames * CHANNELS;
        size_t current_write = write_pos.load(std::memory_order_relaxed);
        size_t chunk1 = std::min(samples, size - current_write);
        memcpy(data.data() + current_write, input, chunk1 * sizeof(float));
        if (chunk1 < samples) memcpy(data.data(), input + chunk1, (samples - chunk1) * sizeof(float));
        size_t next_write = (current_write + samples) % size;
        write_pos.store(next_write, std::memory_order_release);
        available.fetch_add(frames, std::memory_order_release);
    }
    void read(float* output, size_t frames) {
        size_t avail = available.load(std::memory_order_acquire);
        if (avail == 0) {
            memset(output, 0, frames * CHANNELS * sizeof(float));
            return;
        }
        size_t to_read = std::min(frames, avail);
        size_t samples = to_read * CHANNELS;
        size_t current_read = read_pos.load(std::memory_order_relaxed);
        size_t chunk1 = std::min(samples, size - current_read);
        memcpy(output, data.data() + current_read, chunk1 * sizeof(float));
        if (chunk1 < samples) memcpy(output + chunk1, data.data(), (samples - chunk1) * sizeof(float));
        size_t next_read = (current_read + samples) % size;
        read_pos.store(next_read, std::memory_order_release);
        available.fetch_sub(to_read, std::memory_order_release);
        if (to_read < frames) {
            size_t missing = (frames - to_read) * CHANNELS;
            memset(output + samples, 0, missing * sizeof(float));
        }
    }
};

struct StreamContext {
    std::vector<char> buffer;
    size_t read_cursor = 0;
    std::mutex mutex;
    std::condition_variable cv;
    bool finished = false; 
    
    void write(const char* data, size_t size) {
        std::lock_guard<std::mutex> lock(mutex);
        size_t current_size = buffer.size();
        buffer.resize(current_size + size);
        memcpy(buffer.data() + current_size, data, size);
        cv.notify_all(); 
    }
    ma_result read(void* pBufferOut, size_t bytesToRead, size_t* pBytesRead) {
        std::unique_lock<std::mutex> lock(mutex);
        size_t total_read = 0;
        char* out = (char*)pBufferOut;
        while (total_read < bytesToRead) {
            size_t available = (read_cursor < buffer.size()) ? (buffer.size() - read_cursor) : 0;
            if (available > 0) {
                size_t to_read = std::min(bytesToRead - total_read, available);
                memcpy(out + total_read, buffer.data() + read_cursor, to_read);
                read_cursor += to_read;
                total_read += to_read;
            } else if (finished) break; 
            else {
                if (cv.wait_for(lock, std::chrono::seconds(5)) == std::cv_status::timeout) break;
            }
        }
        if (pBytesRead) *pBytesRead = total_read;
        if (total_read == 0 && finished) return MA_AT_END;
        return MA_SUCCESS;
    }
    ma_result seek(ma_int64 offset, ma_seek_origin origin) {
        std::lock_guard<std::mutex> lock(mutex);
        long long new_pos = 0;
        if (origin == ma_seek_origin_start) new_pos = offset;
        else if (origin == ma_seek_origin_current) new_pos = read_cursor + offset;
        else if (origin == ma_seek_origin_end) {
            if (!finished) return MA_ERROR;
            new_pos = buffer.size() + offset;
        }
        if (new_pos < 0) return MA_ERROR;
        read_cursor = new_pos; 
        return MA_SUCCESS;
    }
};

static StreamContext stream_ctx;

ma_result MyReadCallback(ma_decoder* pDecoder, void* pBufferOut, size_t bytesToRead, size_t* pBytesRead) { return stream_ctx.read(pBufferOut, bytesToRead, pBytesRead); }
ma_result MySeekCallback(ma_decoder* pDecoder, ma_int64 offset, ma_seek_origin origin) { return stream_ctx.seek(offset, origin); }

struct AudioEngine {
    ma_device device;
    std::mutex decode_mutex; 
    std::vector<char> file_data;      
    std::vector<char> next_file_data; 
    ma_decoder decoder;
    bool decoder_loaded = false; 
    ma_decoder next_decoder;
    std::atomic<bool> next_decoder_loaded{false};
    std::atomic<bool> loading_next{false};
    
    RingBuffer buffer;
    std::thread producer_thread;
    std::atomic<bool> running{true};
    std::atomic<bool> playing{false};
    std::atomic<long long> seek_target{-1}; 
    std::atomic<bool> is_seeking{false}; 
    std::atomic<long long> current_frame{0};
    std::atomic<long long> total_frames{0};
    std::atomic<long long> frames_until_switch{-1}; 
    std::atomic<long long> pending_total_frames{0}; 
    std::atomic<bool> track_switched_flag{false};   
    std::atomic<float> master_volume{1.0f};

    // Turntable Variables
    std::atomic<bool> is_scratching{false};
    std::atomic<float> scratch_velocity{0.0f};
    std::atomic<float> scratch_cursor{0.0f};
    std::atomic<bool> vis_active{true};

    float vis_left_buf[65536] = {}; 
    float vis_right_buf[65536] = {};
    int   vis_write = 0;
    float vis_snapshot[4096] = {};  
    std::mutex vis_snapshot_mutex;  
    std::complex<float> fft_working_buf[8192] = {};
    std::atomic<float> current_vu_rms{0.0f};
};

static AudioEngine engine;

bool load_file_to_vector(const char* path, std::vector<char>& buffer) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file.is_open()) return false;
    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);
    if (size <= 0) return false;
    buffer.resize(size);
    if (!file.read(buffer.data(), size)) return false;
    return true;
}

ma_result open_decoder_memory(const std::vector<char>& buffer, ma_decoder* pDecoder) {
    ma_decoder_config config = ma_decoder_config_init(ma_format_f32, CHANNELS, SAMPLE_RATE);
    return ma_decoder_init_memory(buffer.data(), buffer.size(), &config, pDecoder);
}

static const int VIS_FFT_N = 8192;
static const int VIS_LATENCY_SAMPLES = 2048;
static float g_hanning[VIS_FFT_N];

static void init_hanning_window() {
    for (int i = 0; i < VIS_FFT_N; i++) {
        double z = (2.0 * M_PI * i) / (VIS_FFT_N - 1);
        g_hanning[i] = (float)(0.21557895 - 0.41663158 * cos(z) + 0.277263158 * cos(2.0 * z) - 0.083578947 * cos(3.0 * z) + 0.006947368 * cos(4.0 * z));
    }
}

static void fft_inplace(std::complex<float>* a, int n) {
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        double ang_step = -2.0 * M_PI / len;
        for (int i = 0; i < n; i += len) {
            for (int j = 0; j < len / 2; j++) {
                double angle = ang_step * j;
                std::complex<float> w((float)cos(angle), (float)sin(angle));
                std::complex<float> u = a[i + j];
                std::complex<float> v = a[i + j + len / 2] * w;
                a[i + j]           = u + v;
                a[i + j + len / 2] = u - v;
            }
        }
    }
}

void data_callback(ma_device* pDevice, void* pOutput, const void* pInput, ma_uint32 frameCount) {
    bool scratching = engine.is_scratching.load();
    
    // Allow audio engine to output sound even when paused IF we are scratching!
    if (!engine.playing && !scratching) {
        memset(pOutput, 0, frameCount * CHANNELS * sizeof(float));
        return;
    }
    
    float* out = (float*)pOutput;
    
    // THE SCRATCH RESAMPLER
    if (scratching) {
        float v = engine.scratch_velocity.load();
        float cursor = engine.scratch_cursor.load();
        size_t max_frames = engine.buffer.size / CHANNELS;
        
        for (ma_uint32 i = 0; i < frameCount; i++) {
            cursor += v;
            
            // Loop the 10-second RAM buffer mathematically
            while (cursor < 0) cursor += max_frames;
            while (cursor >= max_frames) cursor -= max_frames;
            
            int idx = (int)cursor;
            out[i*2]   = engine.buffer.data[idx*2];
            out[i*2+1] = engine.buffer.data[idx*2+1];
        }
        engine.scratch_cursor.store(cursor);
        
        // Soft mute if the turntable is fully stopped to prevent static hum
        if (std::abs(v) < 0.01f) {
            memset(out, 0, frameCount * CHANNELS * sizeof(float));
        }
    } else {
        engine.buffer.read(out, frameCount);
    }
    
    // --- ADD THIS BLOCK: True Time-Domain RMS for VU Meter ---
    float sum_squares = 0.0f;
    for (ma_uint32 i = 0; i < frameCount; i++) {
        // Proper Mono Summation: (L + R) / 2
        float mono = (out[i * CHANNELS] + out[i * CHANNELS + 1]) * 0.5f;
        sum_squares += mono * mono;
    }
    float true_rms = std::sqrt(sum_squares / frameCount);
    engine.current_vu_rms.store(true_rms, std::memory_order_relaxed);
    // ---------------------------------------------------------
    
    // FFT Visualizer — skip entirely when nobody is watching (e.g. minimized)
    static int fft_skip = 0;
    if (engine.vis_active.load(std::memory_order_relaxed) && (++fft_skip & 1) == 0) {
        int frames = (int)frameCount;
        for (int i = 0; i < frames; i++) {
            engine.vis_left_buf[engine.vis_write] = out[i * CHANNELS];
            engine.vis_right_buf[engine.vis_write] = out[i * CHANNELS + 1];
            engine.vis_write = (engine.vis_write + 1) % 65536;
        }

        int read_start = engine.vis_write - VIS_FFT_N - VIS_LATENCY_SAMPLES;
        while (read_start < 0) read_start += 65536;
        read_start %= 65536;
        float norm = 1.0f / (VIS_FFT_N / 2);
        static float left_magnitudes[4096];

        for (int i = 0; i < VIS_FFT_N; i++) {
            int src = (read_start + i) % 65536;
            engine.fft_working_buf[i] = std::complex<float>(engine.vis_left_buf[src] * g_hanning[i], 0.0f);
        }
        fft_inplace(engine.fft_working_buf, VIS_FFT_N);
        for (int i = 0; i < 4096; i++) left_magnitudes[i] = std::abs(engine.fft_working_buf[i]) * norm;

        for (int i = 0; i < VIS_FFT_N; i++) {
            int src = (read_start + i) % 65536;
            engine.fft_working_buf[i] = std::complex<float>(engine.vis_right_buf[src] * g_hanning[i], 0.0f);
        }
        fft_inplace(engine.fft_working_buf, VIS_FFT_N);

        {
            std::lock_guard<std::mutex> lock(engine.vis_snapshot_mutex);
            for (int i = 0; i < 4096; i++) {
                float right_mag = std::abs(engine.fft_working_buf[i]) * norm;
                engine.vis_snapshot[i] = std::max(left_magnitudes[i], right_mag);
            }
        }
    }

    float raw_vol = engine.master_volume;
    float vol = raw_vol * raw_vol * raw_vol;
    if (vol < 0.999f) {
        size_t samples = frameCount * CHANNELS;
        for (size_t i = 0; i < samples; ++i) out[i] *= vol;
    }
    
    // Track timing logic
    if (!scratching) {
        long long frames_left = engine.frames_until_switch;
        if (frames_left > -1) {
            if (frames_left <= frameCount) {
                engine.total_frames = engine.pending_total_frames.load();
                long long new_frames = frameCount - frames_left;
                engine.current_frame = new_frames; 
                engine.frames_until_switch = -1; 
                engine.track_switched_flag = true;
            } else {
                engine.frames_until_switch -= frameCount;
                engine.current_frame += frameCount;
            }
        } else {
            engine.current_frame += frameCount;
        }
    }
}

void producer_loop() {
    float temp_buf[CHUNK_SIZE * CHANNELS];
    int grace_period = 0; 

    while (engine.running) {
        if (grace_period > 0) grace_period--;

        long long target = engine.seek_target.exchange(-1);
        if (target != -1) {
            std::lock_guard<std::mutex> lock(engine.decode_mutex);
            if (engine.decoder_loaded) {
                ma_decoder_seek_to_pcm_frame(&engine.decoder, target);
                engine.is_seeking = true; 
                engine.buffer.clear();    
                engine.current_frame = target;
                engine.frames_until_switch = -1;
                
                ma_uint64 frames_read = 0;
                for(int k=0; k<4; k++) {
                     ma_decoder_read_pcm_frames(&engine.decoder, temp_buf, CHUNK_SIZE, &frames_read);
                     if (frames_read > 0) engine.buffer.write(temp_buf, frames_read);
                }
                engine.is_seeking = false; 
                grace_period = 20; 
            }
        }

        // Stop decoding new audio into the buffer if we are scratching!
        if (engine.playing && !engine.is_seeking && !engine.is_scratching.load()) {
            size_t avail_frames = engine.buffer.available.load(std::memory_order_relaxed);
            size_t total_frames = engine.buffer.size / CHANNELS;
            if (avail_frames > total_frames * 0.80) {
                // Buffer very full — sleep proportionally longer to avoid busy-spinning
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
                continue;
            }
            if (avail_frames > total_frames * 0.50) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }

            std::unique_lock<std::mutex> lock(engine.decode_mutex);
            if (engine.decoder_loaded) {
                ma_uint64 frames_read = 0;
                ma_result result = ma_decoder_read_pcm_frames(&engine.decoder, temp_buf, CHUNK_SIZE, &frames_read);
                lock.unlock(); 

                if (frames_read > 0) engine.buffer.write(temp_buf, frames_read);

                if (result != MA_SUCCESS || frames_read == 0) {
                    lock.lock(); 
                    if (grace_period > 0) {
                        lock.unlock();
                        std::this_thread::sleep_for(std::chrono::milliseconds(10));
                        continue; 
                    }

                    if (engine.next_decoder_loaded) {
                        ma_decoder_uninit(&engine.decoder);
                        engine.file_data = std::move(engine.next_file_data);
                        engine.decoder = engine.next_decoder;
                        engine.next_decoder_loaded = false;
                        
                        ma_uint64 len;
                        ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len);
                        engine.pending_total_frames = len;
                        engine.frames_until_switch = engine.buffer.available;
                        
                        lock.unlock();
                        continue; 
                    }
                    lock.unlock();
                }
            } else {
                lock.unlock();
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    }
}

struct PreloadContext {
    std::vector<char>* buffer;
    int session;
};

void background_loader(std::string path, int session) {
    std::vector<char> temp_data;
    // Load into an isolated temporary buffer first
    if (load_file_to_vector(path.c_str(), temp_data)) {
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        // Only apply if the user hasn't clicked a new song in the meantime
        if (session == current_preload_session.load()) {
            if (engine.next_decoder_loaded) {
                ma_decoder_uninit(&engine.next_decoder);
                engine.next_decoder_loaded = false;
            }
            engine.next_file_data = std::move(temp_data);
            if (open_decoder_memory(engine.next_file_data, &engine.next_decoder) == MA_SUCCESS) {
                engine.next_decoder_loaded = true;
            }
        }
    }
    if (session == current_preload_session.load()) engine.loading_next = false;
}

size_t curl_stream_write_cb(void* ptr, size_t size, size_t nmemb, void* userdata) {
    int session = (int)(intptr_t)userdata;
    if (session != current_stream_session.load()) return 0; 
    size_t bytes = size * nmemb;
    if (bytes > 0) stream_ctx.write((const char*)ptr, bytes);
    return bytes;
}

size_t curl_preload_write_cb(void* ptr, size_t size, size_t nmemb, void* userdata) {
    PreloadContext* ctx = static_cast<PreloadContext*>(userdata);
    if (ctx->session != current_preload_session.load()) return 0; // Instantly abort old downloads!
    size_t bytes = size * nmemb;
    size_t current_size = ctx->buffer->size();
    ctx->buffer->resize(current_size + bytes);
    memcpy(ctx->buffer->data() + current_size, ptr, bytes);
    return bytes;
}

void live_stream_thread(std::string url, int session) {
    CURL* curl = curl_easy_init();
    if (curl) {
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_stream_write_cb);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, (void*)(intptr_t)session);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
        curl_easy_perform(curl);
        curl_easy_cleanup(curl);
    }
    if (session == current_stream_session.load()) {
        std::lock_guard<std::mutex> lock(stream_ctx.mutex);
        stream_ctx.finished = true;
        stream_ctx.cv.notify_all();
    }
}

void background_stream_loader(std::string url, int session) {
    std::vector<char> temp_data;
    PreloadContext ctx = { &temp_data, session };
    
    CURL* curl = curl_easy_init();
    if (curl) {
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_preload_write_cb);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &ctx);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
        curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
        CURLcode res = curl_easy_perform(curl);
        curl_easy_cleanup(curl);
        
        // 🟢 ONLY touch the audio engine buffers once the download is 100% finished and locked
        if (session == current_preload_session.load() && !temp_data.empty() && res == CURLE_OK) {
            std::lock_guard<std::mutex> lock(engine.decode_mutex);
            if (session == current_preload_session.load()) {
                if (engine.next_decoder_loaded) {
                    ma_decoder_uninit(&engine.next_decoder);
                    engine.next_decoder_loaded = false;
                }
                engine.next_file_data = std::move(temp_data);
                if (open_decoder_memory(engine.next_file_data, &engine.next_decoder) == MA_SUCCESS) {
                    engine.next_decoder_loaded = true;
                }
            }
        }
    }
    if (session == current_preload_session.load()) engine.loading_next = false;
}

extern "C" {
    EXPORT void audio_init() {
        init_hanning_window();
        engine.buffer.init(BUFFER_SECONDS);
        engine.running = true;
        engine.producer_thread = std::thread(producer_loop);
        
        ma_device_config config = ma_device_config_init(ma_device_type_playback);
        config.playback.format = ma_format_f32;
        config.playback.channels = CHANNELS;
        config.sampleRate = SAMPLE_RATE;
        config.dataCallback = data_callback;
        #ifdef __linux__
            // low_latency forces tiny periods; combined with FFT in the callback it causes underruns
            config.performanceProfile = ma_performance_profile_conservative;
            ma_backend backends[] = { ma_backend_pulseaudio };
            if (ma_device_init_ex(backends, 1, NULL, &config, &engine.device) != MA_SUCCESS) {
                ma_device_init(NULL, &config, &engine.device);
            }
        #else
            config.performanceProfile = ma_performance_profile_low_latency;
            ma_device_init(NULL, &config, &engine.device);
        #endif
        ma_device_start(&engine.device);
    }

    EXPORT float get_file_bpm(const char* path) {
        ma_decoder decoder;
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, CHANNELS, SAMPLE_RATE); 
        if (ma_decoder_init_file(path, &config, &decoder) != MA_SUCCESS) {
            return 0.0f; // Decode failed
        }
        
        soundtouch::BPMDetect bpmDetector(CHANNELS, SAMPLE_RATE);
        std::vector<float> temp_buf(CHUNK_SIZE * CHANNELS);
        
        while (true) {
            ma_uint64 frames_read = 0;
            ma_decoder_read_pcm_frames(&decoder, temp_buf.data(), CHUNK_SIZE, &frames_read);
            if (frames_read == 0) break; // EOF reached
            bpmDetector.inputSamples(temp_buf.data(), (int)frames_read);
        }
        
        ma_decoder_uninit(&decoder);
        return bpmDetector.getBpm();
    }

    EXPORT void stream_start() {
        std::lock_guard<std::mutex> lock(stream_ctx.mutex);
        stream_ctx.buffer.clear();
        stream_ctx.read_cursor = 0;
        stream_ctx.finished = false;
        if (engine.decoder_loaded) ma_decoder_uninit(&engine.decoder);
        engine.decoder_loaded = false;
        engine.playing = false;
    }

    EXPORT void stream_append(const char* data, int size) {
        if (size > 0) stream_ctx.write(data, size);
    }

    EXPORT void stream_end() {
        std::lock_guard<std::mutex> lock(stream_ctx.mutex);
        stream_ctx.finished = true;
        stream_ctx.cv.notify_all();
    }

    EXPORT int stream_init_decoder() {
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, CHANNELS, SAMPLE_RATE);
        ma_result result = ma_decoder_init(MyReadCallback, MySeekCallback, NULL, &config, &engine.decoder);
        if (result == MA_SUCCESS) {
            engine.decoder_loaded = true;
            ma_uint64 len;
            if (ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len) == MA_SUCCESS) {
                engine.total_frames = len;
            } else {
                engine.total_frames = 0; 
            }
            engine.current_frame = 0;
            engine.buffer.clear();
            return 1;
        }
        return 0;
    }
    
    EXPORT int play_network_stream(const char* url) {
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        current_preload_session++;
        engine.playing = false;
        int my_session = ++current_stream_session;
        {
            std::lock_guard<std::mutex> slock(stream_ctx.mutex);
            stream_ctx.buffer.clear();
            stream_ctx.buffer.shrink_to_fit(); 
            stream_ctx.read_cursor = 0;
            stream_ctx.finished = false;
        }
        if (engine.decoder_loaded) { ma_decoder_uninit(&engine.decoder); engine.decoder_loaded = false; }
        
        // Prevent old preloads from executing during stream buffering!
        if (engine.next_decoder_loaded) { ma_decoder_uninit(&engine.next_decoder); engine.next_decoder_loaded = false; }
        engine.next_file_data.clear();
        
        std::thread(live_stream_thread, std::string(url), my_session).detach();
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, CHANNELS, SAMPLE_RATE);
        ma_result result = ma_decoder_init(MyReadCallback, MySeekCallback, NULL, &config, &engine.decoder);
        if (result == MA_SUCCESS) {
            engine.decoder_loaded = true;
            ma_uint64 len;
            if (ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len) == MA_SUCCESS) {
                engine.total_frames = len;
            } else { engine.total_frames = 0; }
            engine.current_frame = 0;
            engine.buffer.clear();
            return 1;
        }
        return 0;
    }

    EXPORT void preload_network_stream(const char* url) {
        engine.loading_next = true;
        int my_session = ++current_preload_session;
        std::thread(background_stream_loader, std::string(url), my_session).detach();
    }
    
    EXPORT int load_track(const char* path) {
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        current_preload_session++; 
        engine.playing = false;
        engine.seek_target = -1;
        engine.is_seeking = false;
        if (engine.decoder_loaded) { ma_decoder_uninit(&engine.decoder); engine.decoder_loaded = false; }
        if (engine.next_decoder_loaded) { ma_decoder_uninit(&engine.next_decoder); engine.next_decoder_loaded = false; }
        engine.track_switched_flag = false;
        engine.frames_until_switch = -1;
        engine.file_data.clear();
        if (!load_file_to_vector(path, engine.file_data)) return 0;
        if (open_decoder_memory(engine.file_data, &engine.decoder) != MA_SUCCESS) return 0;
        ma_uint64 len;
        ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len);
        engine.total_frames = len;
        engine.current_frame = 0;
        engine.buffer.clear();
        float temp_buf[CHUNK_SIZE * CHANNELS];
        for(int i=0; i<4; i++) {
            ma_uint64 frames_read = 0;
            ma_decoder_read_pcm_frames(&engine.decoder, temp_buf, CHUNK_SIZE, &frames_read);
            if (frames_read > 0) engine.buffer.write(temp_buf, frames_read);
        }
        engine.decoder_loaded = true;
        return 1;
    }
    
    EXPORT void preload_track(const char* path) {
        engine.loading_next = true;
        int my_session = ++current_preload_session; 
        std::string s_path = path;
        std::thread(background_loader, s_path, my_session).detach();
    }
    
    EXPORT int check_track_switch() {
        if (engine.track_switched_flag) { engine.track_switched_flag = false; return 1; }
        return 0;
    }
    
    EXPORT int is_transition_pending() { return (engine.frames_until_switch > -1) ? 1 : 0; }
    
    EXPORT void set_volume(int percent) {
        if (percent < 0) percent = 0; if (percent > 100) percent = 100;
        engine.master_volume = percent / 100.0f;
    }
    
    EXPORT void set_duration(long long ms) { if (ms > 0) engine.total_frames = (ms * SAMPLE_RATE) / 1000; }
    
    EXPORT void play() { engine.playing = true; }
    
    EXPORT void audio_pause() { engine.playing = false; }
    
    EXPORT void stop() { 
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        engine.playing = false; 
        engine.current_frame = 0; 
        engine.buffer.clear(); 
        if (engine.decoder_loaded) ma_decoder_seek_to_pcm_frame(&engine.decoder, 0); 
    }
    
    EXPORT void seek(long long ms) { 
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        if (!engine.decoder_loaded) return; 
        engine.seek_target = (ms * SAMPLE_RATE) / 1000; 
    }
    
    EXPORT long long get_position() { return (engine.current_frame * 1000) / SAMPLE_RATE; }
    
    EXPORT long long get_duration() { return (engine.total_frames * 1000) / SAMPLE_RATE; }
    
    EXPORT void get_vis_data(float* output) {
        std::lock_guard<std::mutex> lock(engine.vis_snapshot_mutex);
        for (int i = 0; i < 4096; ++i) output[i] = engine.vis_snapshot[i];
    }

    // --- VU Meter ---
    EXPORT float get_vu_rms() {
        return engine.current_vu_rms.load(std::memory_order_relaxed);
    }
    // ----------------------------------------

    EXPORT void set_scratch_mode(int mode) {
        bool active = (mode == 1);
        engine.is_scratching.store(active);
        if (active) {
            size_t read_floats = engine.buffer.read_pos.load();
            engine.scratch_cursor.store((float)(read_floats / CHANNELS));
            engine.scratch_velocity.store(0.0f);
        }
    }
    
    EXPORT void set_scratch_velocity(float vel) {
        engine.scratch_velocity.store(vel);
    }

    EXPORT void set_vis_active(int active) {
        engine.vis_active.store(active != 0, std::memory_order_relaxed);
    }

    EXPORT void cleanup() { 
        engine.running = false; 
        if (engine.producer_thread.joinable()) engine.producer_thread.join(); 
        ma_device_uninit(&engine.device); 
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        if (engine.decoder_loaded) ma_decoder_uninit(&engine.decoder); 
        if (engine.next_decoder_loaded) ma_decoder_uninit(&engine.next_decoder);
        engine.file_data.clear();
        engine.next_file_data.clear();
    }

    EXPORT int generate_waveform(const char* path, float* out_buffer, int num_points) {
        ma_decoder decoder;
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, 1, SAMPLE_RATE); 
        if (ma_decoder_init_file(path, &config, &decoder) != MA_SUCCESS) return 0;
        
        ma_uint64 total_frames;
        if (ma_decoder_get_length_in_pcm_frames(&decoder, &total_frames) != MA_SUCCESS || total_frames == 0) {
            ma_decoder_uninit(&decoder); 
            return 0;
        }
        
        ma_uint64 frames_per_point = total_frames / num_points;
        if (frames_per_point == 0) frames_per_point = 1;
        
        std::vector<float> temp_buf(4096);
        
        for (int i = 0; i < num_points; ++i) {
            double sum_abs = 0.0;
            ma_uint64 total_samples_read = 0;
            ma_uint64 frames_to_read = frames_per_point;
            
            while (frames_to_read > 0) {
                ma_uint64 read_chunk = std::min((ma_uint64)4096, frames_to_read);
                ma_uint64 frames_read = 0;
                ma_decoder_read_pcm_frames(&decoder, temp_buf.data(), read_chunk, &frames_read);
                
                if (frames_read == 0) break; 
                
                for (ma_uint64 j = 0; j < frames_read; ++j) {
                    sum_abs += std::abs(temp_buf[j]);
                }
                
                total_samples_read += frames_read;
                frames_to_read -= frames_read;
            }
            out_buffer[i] = (total_samples_read > 0) ? (float)(sum_abs / total_samples_read) : 0.0f; 
        }
        
        ma_decoder_uninit(&decoder);
        return (int)((total_frames * 1000) / SAMPLE_RATE); 
    }
}