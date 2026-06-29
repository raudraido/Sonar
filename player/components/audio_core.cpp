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
#include <chrono>
#include <curl/curl.h>
#include "qm-dsp/dsp/onsets/DetectionFunction.h"
#include "qm-dsp/dsp/tempotracking/TempoTrackV2.h"

std::atomic<int> current_stream_session{0};
std::atomic<int> current_preload_session{0};

#ifdef _WIN32
  #include <windows.h>
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT
#endif

// Nanosecond timestamps exposed to Python (current_frame_timestamp_ns, read
// via get_position_anchor) must share Python's time.monotonic_ns() clock
// domain, since _get_precise_position_ms() in audio_engine.py subtracts one
// from the other directly. std::chrono::steady_clock on this project's
// MinGW/Windows toolchain does NOT match — it returned a wall-clock-since-
// 1970-like value (~10^18ns) while Python's monotonic_ns() is system-uptime-
// based (~10^13ns), making that subtraction wildly negative and permanently
// floored to 0 by the caller's max(0, ...) clamp: the extrapolated position
// stuck at 0 (looking "frozen") for as long as it took get_position()'s own
// raw, non-extrapolated counter to organically pass that floor again — which
// is exactly the "stays still for a long time after a scratch release"
// symptom. QueryPerformanceCounter is what CPython's time.monotonic_ns()
// itself calls into on Windows, so using it here directly guarantees the two
// sides agree. Linux/macOS's libstdc++/libc++ steady_clock already matches
// Python's clock_gettime(CLOCK_MONOTONIC)-based monotonic_ns() correctly.
#ifdef _WIN32
static long long now_ns() {
    static LARGE_INTEGER freq = [] {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return f;
    }();
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    // Split into whole seconds + remainder before scaling to ns — multiplying
    // counter.QuadPart by 1e9 directly overflows int64 once uptime exceeds
    // ~15 minutes (QuadPart ticks at ~10MHz on typical hardware), silently
    // wrapping to a garbage timestamp that looked like a much-earlier point
    // in time — which is exactly what caused the "frozen after scratch"
    // symptom this function exists to fix in the first place.
    long long whole = (counter.QuadPart / freq.QuadPart) * 1000000000LL;
    long long frac  = (counter.QuadPart % freq.QuadPart) * 1000000000LL / freq.QuadPart;
    return whole + frac;
}
#else
static long long now_ns() {
    using namespace std::chrono;
    return duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count();
}
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
    // Wall-clock instant (steady_clock, nanoseconds) at which current_frame's
    // value became valid — i.e. taken at the top of the audio callback that
    // last wrote it. Lets callers project the position forward to "right
    // now" instead of being stuck at whatever current_frame last jumped to
    // (it only advances once per audio buffer, which can be a large jump —
    // see ma_performance_profile_conservative in audio_init below). Mirrors
    // Mixxx's VisualPlayPosition::m_referenceTime (waveform/visualplayposition.h
    // upstream): a real-time-thread timestamp anchor, not a polled one.
    std::atomic<long long> current_frame_timestamp_ns{0};
    std::atomic<long long> output_latency_frames{0};
    std::atomic<long long> total_frames{0};
    std::atomic<long long> frames_until_switch{-1}; 
    std::atomic<long long> pending_total_frames{0}; 
    std::atomic<bool> track_switched_flag{false};   
    std::atomic<float> master_volume{1.0f};

    // ── Turntable / scratch ──────────────────────────────────────────────
    // Architecture mirrors Mixxx's actual scratch engine (src/engine/
    // positionscratchcontroller.{h,cpp} + CachingReader upstream), not the
    // small forward-only RAM ring buffer this used to resample out of:
    //   1. A dedicated decoder (scratch_decoder), opened from the SAME
    //      in-memory file bytes (engine.file_data) the normal playback
    //      decoder already uses, but completely independent state — never
    //      touches engine.decoder, so gapless preload/transition logic is
    //      untouched. Real seekable random-access across the WHOLE track,
    //      not a small lookahead-only cache that wrapped to wrong/stale
    //      content once you scratched beyond its bounds (the "buffer
    //      issue" symptom).
    //   2. A sliding window of decoded PCM around the current scratch
    //      position (double-buffered — scratch_window[0]/[1] — so the
    //      real-time audio callback always reads a fully-formed buffer
    //      lock-free while producer_loop decodes the *other* one in the
    //      background and atomically flips scratch_active_window once
    //      ready, recentering whenever the cursor nears either edge).
    //   3. A PD (proportional-derivative) position controller, not raw
    //      velocity integration — the UI reports a *target* cumulative
    //      displacement (set_scratch_target_delta, mirrors Mixxx's
    //      "scratch_position" control), and data_callback computes a
    //      smoothly-converging rate toward it each buffer, with explicit
    //      stale-target handling (decays toward 0 if the UI stops sending
    //      updates) instead of trusting one possibly-noisy instantaneous
    //      velocity sample forever.
    std::atomic<bool> is_scratching{false};

    ma_decoder scratch_decoder{};
    std::atomic<bool> scratch_decoder_loaded{false};
    // The scratch decoder's own reported length — for a network-streamed
    // track (see set_scratch_mode's stream_ctx fallback), this is a
    // snapshot of however many bytes had downloaded *at the moment
    // scratching began*, frozen even as the real download keeps growing in
    // the background. It can be meaningfully SHORTER than engine.total_frames
    // (the real full-track length). Every scratch-window seek must clamp to
    // THIS, not total_frames — seeking a decoder into a truncated buffer
    // past what it can actually decode is what caused the app to hang after
    // scratching forward past the downloaded portion (some formats, MP3
    // especially, scan for a sync point with no guaranteed bound on a
    // truncated file).
    std::atomic<long long> scratch_decoder_total_frames{0};

    static constexpr int kScratchWindowSeconds = 20;
    static constexpr size_t kScratchWindowFrames =
            (size_t)kScratchWindowSeconds * SAMPLE_RATE;
    static constexpr long long kScratchRefillMarginFrames =
            (long long)4 * SAMPLE_RATE;  // recenter once within 4s of an edge

    std::vector<float> scratch_window[2];        // interleaved stereo, kScratchWindowFrames each
    std::atomic<long long> scratch_window_start_frame[2]{0, 0};
    std::atomic<int> scratch_active_window{0};   // which of [0]/[1] the audio callback reads
    std::atomic<bool> scratch_refill_in_progress{false};

    // scratch_origin_frame: absolute track frame current_frame was at when
    // scratch mode was entered (set_scratch_mode(1)). scratch_cumulative_delta:
    // unwrapped sum of every rate sample actually applied since then (every
    // audio frame, continuously, not just once per UI mouse-move event) —
    // together these are the single source of truth for "what's actually
    // audible right now" (get_scratch_position_ms), instead of the UI
    // computing its own parallel, purely event-driven position estimate.
    std::atomic<long long> scratch_origin_frame{0};
    std::atomic<double> scratch_cumulative_delta{0.0};

    // PD controller state (mirrors Mixxx's VelocityController/RateIIFilter,
    // simplified — see data_callback's scratch branch for the actual math).
    std::atomic<double> scratch_target_delta{0.0};      // UI-reported target (frames, relative to origin)
    std::atomic<long long> scratch_target_updated_at_ns{0};
    double scratch_pd_last_error = 0.0;                 // audio-thread-local, no atomic needed
    double scratch_pd_filtered_rate = 0.0;
    std::atomic<bool> vis_active{true};

    // ── Metronome (tick/tock debug aid) ─────────────────────────────────
    // Mirrors Ableton's tick/tock metronome — alternates a higher-pitched
    // "tick" every 4th beat (assumed downbeat; we have no real bar/phase
    // detection, just beat positions, so 4/4 is the working assumption)
    // and a lower "tock" on the others. Mixed directly into the real-time
    // audio callback at the exact sample position rather than driven by a
    // Python/Qt timer, so it's actually useful for judging whether the
    // detected beat grid is truly aligned with the audible transient — a
    // GUI-thread-driven click would have enough scheduling jitter to make
    // that judgment call meaningless. metronome_beats is read every
    // callback but only ever written rarely (track load, BPM correction),
    // so a try_lock in the audio callback (skip this callback's clicks
    // rather than block) is safe and avoids any real-time priority
    // inversion risk.
    std::mutex metronome_mutex;
    std::vector<long long> metronome_beats;  // absolute frame positions, sorted ascending
    std::atomic<bool> metronome_enabled{false};
    // Which beat (0-3) within the assumed 4/4 bar gets the "tick" instead
    // of "tock" — see set_metronome_downbeat_offset. Doesn't change any
    // beat's actual timing, only which one is treated as the downbeat: the
    // detector's anchor can land on a noise transient instead of the real
    // first beat of a bar, throwing off which detected beat *should* be
    // the tick even though the grid's timing itself is now correct.
    std::atomic<int> metronome_downbeat_offset{0};

    float vis_left_buf[65536] = {};
    float vis_right_buf[65536] = {};
    int   vis_write = 0;
    float vis_snapshot[4096] = {};  
    std::mutex vis_snapshot_mutex;  
    std::complex<float> fft_working_buf[8192] = {};
    std::atomic<float> current_vu_rms_l{0.0f};
    std::atomic<float> current_vu_rms_r{0.0f};
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

// Decodes AudioEngine::kScratchWindowFrames frames starting at startFrame
// (clamped >= 0) from engine.scratch_decoder into `window`. Used both for
// the initial window (set_scratch_mode, synchronous — a one-time UI-
// triggered transition, not a per-frame operation, so blocking briefly is
// fine) and for background recentering (producer_loop, while scratching —
// see kScratchRefillMarginFrames).
bool fill_scratch_window(std::vector<float>& window, long long startFrame) {
    if (startFrame < 0) startFrame = 0;
    // Clamp to what the scratch decoder can actually decode (see
    // scratch_decoder_total_frames's comment) — for a network-streamed
    // track this can be meaningfully shorter than the real full track,
    // since it's a snapshot frozen at whatever had downloaded when
    // scratching began. Seeking past it risks the decoder hanging while
    // scanning a truncated buffer for a sync point that doesn't exist
    // (this is what caused the app to freeze after scratching forward).
    long long decoderLen = engine.scratch_decoder_total_frames.load();
    if (decoderLen > 0 && startFrame >= decoderLen) {
        startFrame = decoderLen > 0 ? decoderLen - 1 : 0;
        if (startFrame < 0) startFrame = 0;
    }
    if (ma_decoder_seek_to_pcm_frame(&engine.scratch_decoder, (ma_uint64)startFrame) != MA_SUCCESS) {
        return false;
    }
    window.resize(AudioEngine::kScratchWindowFrames * CHANNELS);
    ma_uint64 framesRead = 0;
    ma_decoder_read_pcm_frames(&engine.scratch_decoder, window.data(), AudioEngine::kScratchWindowFrames, &framesRead);
    if (framesRead < AudioEngine::kScratchWindowFrames) {
        std::fill(window.begin() + framesRead * CHANNELS, window.end(), 0.0f);
    }
    return true;
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
    
    // THE SCRATCH RESAMPLER — position controller driving reads from a
    // sliding window (see AudioEngine's scratch fields for the full design
    // rationale: mirrors Mixxx's PositionScratchController + CachingReader,
    // replacing the old fixed-size forward-only ring buffer + raw-velocity
    // approach that could wrap to stale/wrong content and was vulnerable to
    // any single noisy instantaneous-velocity sample).
    //
    // Exponential convergence with a fixed TIME CONSTANT, not a literal
    // per-callback PD gain — Mixxx's own controller has to explicitly
    // renormalize its gains by m_callsPerDt (derived from buffer size) for
    // exactly this reason: a fixed per-callback correction overcorrects
    // when callbacks are infrequent/buffers are large (our engine runs a
    // "conservative" performance profile with larger buffers than typical),
    // causing the position to overshoot the target and oscillate back and
    // forth every callback instead of smoothly settling. Expressing the
    // correction as "close this fraction of the gap per second of real
    // time" is buffer-size-independent by construction — frameCount only
    // changes how much *time* a callback represents, not the rate itself.
    if (scratching) {
        double targetDelta = engine.scratch_target_delta.load(std::memory_order_relaxed);
        double currentDelta = engine.scratch_cumulative_delta.load(std::memory_order_relaxed);
        long long targetUpdatedAtNs = engine.scratch_target_updated_at_ns.load(std::memory_order_relaxed);

        using namespace std::chrono;
        long long nowNs = now_ns();
        double sinceUpdateMs = (double)(nowNs - targetUpdatedAtNs) / 1.0e6;
        double dtSec = (double)frameCount / SAMPLE_RATE;

        double error = targetDelta - currentDelta;
        double rate;
        // If the UI hasn't sent a new target in a while, assume the mouse
        // has stopped and decay toward 0 instead of continuing to chase a
        // now-meaningless stale target — mirrors Mixxx's "assume missing
        // mouse update" / "mouse has stopped" handling (kMoveDelayMax).
        constexpr double kStaleMs = 60.0;
        if (sinceUpdateMs > kStaleMs) {
            double decayAlpha = 1.0 - std::exp(-dtSec / 0.05);  // ~50ms decay time constant
            rate = engine.scratch_pd_filtered_rate * (1.0 - decayAlpha);
            if (std::abs(rate) < 0.01) rate = 0.0;
        } else {
            constexpr double kConvergeTauSec = 0.08;  // close ~63% of the gap every 80ms
            double rawRate = error / (kConvergeTauSec * SAMPLE_RATE);
            rawRate = std::max(-8.0, std::min(8.0, rawRate));
            // Low-pass filter, time-constant based (same buffer-size-
            // independence reasoning as above) — except on strong
            // decelerations, skip filtering those to avoid overshoot.
            constexpr double kFilterTauSec = 0.03;
            double filterAlpha = 1.0 - std::exp(-dtSec / kFilterTauSec);
            if (std::abs(rawRate) - std::abs(engine.scratch_pd_filtered_rate) > -0.1) {
                rate = engine.scratch_pd_filtered_rate +
                        (rawRate - engine.scratch_pd_filtered_rate) * filterAlpha;
            } else {
                rate = rawRate;
            }
        }
        engine.scratch_pd_last_error = error;
        engine.scratch_pd_filtered_rate = rate;

        int activeIdx = engine.scratch_active_window.load(std::memory_order_acquire);
        std::vector<float>& window = engine.scratch_window[activeIdx];
        long long windowStartFrame = engine.scratch_window_start_frame[activeIdx].load(std::memory_order_acquire);
        long long originFrame = engine.scratch_origin_frame.load(std::memory_order_relaxed);
        long long windowFrames = (long long)AudioEngine::kScratchWindowFrames;

        if (window.empty()) {
            memset(out, 0, frameCount * CHANNELS * sizeof(float));
        } else {
            for (ma_uint32 i = 0; i < frameCount; i++) {
                currentDelta += rate;
                long long absFrame = originFrame + (long long)std::llround(currentDelta);
                long long windowIdx = absFrame - windowStartFrame;
                // Safety clamp — should rarely trigger thanks to
                // producer_loop's background recentering, but guards
                // against an edge case (e.g. a refill lagging behind a very
                // fast scratch) indexing out of bounds.
                if (windowIdx < 0) windowIdx = 0;
                if (windowIdx >= windowFrames) windowIdx = windowFrames - 1;

                out[i * 2]     = window[windowIdx * 2];
                out[i * 2 + 1] = window[windowIdx * 2 + 1];
            }
        }
        engine.scratch_cumulative_delta.store(currentDelta, std::memory_order_relaxed);

        // Soft mute if the turntable is fully stopped to prevent static hum
        if (std::abs(rate) < 0.01) {
            memset(out, 0, frameCount * CHANNELS * sizeof(float));
        }
    } else {
        engine.buffer.read(out, frameCount);
    }
    
    // --- True Time-Domain RMS for VU Meter, per channel ---
    float sum_sq_l = 0.0f, sum_sq_r = 0.0f;
    for (ma_uint32 i = 0; i < frameCount; i++) {
        float l = out[i * CHANNELS];
        float r = out[i * CHANNELS + 1];
        sum_sq_l += l * l;
        sum_sq_r += r * r;
    }
    float true_rms_l = std::sqrt(sum_sq_l / frameCount);
    float true_rms_r = std::sqrt(sum_sq_r / frameCount);
    engine.current_vu_rms_l.store(true_rms_l, std::memory_order_relaxed);
    engine.current_vu_rms_r.store(true_rms_r, std::memory_order_relaxed);
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

    // Metronome (tick/tock debug aid) — see AudioEngine's comment above for
    // the full rationale. engine.current_frame still holds *this* callback's
    // starting frame here (it isn't advanced until the Track timing logic
    // block below), so it's the correct base for "which beats fall within
    // this buffer". try_lock rather than lock: metronome_beats is only ever
    // written rarely (track load, BPM correction) from a non-realtime
    // thread, so on the rare occasion it's contended, just skip this
    // callback's clicks rather than risk blocking the audio thread.
    if (!scratching && engine.metronome_enabled.load(std::memory_order_relaxed)) {
        std::unique_lock<std::mutex> mlock(engine.metronome_mutex, std::try_to_lock);
        if (mlock.owns_lock() && !engine.metronome_beats.empty()) {
            const long long callbackStartFrame = engine.current_frame;
            const long long callbackEndFrame = callbackStartFrame + (long long)frameCount;

            auto beginIt = std::lower_bound(engine.metronome_beats.begin(), engine.metronome_beats.end(), callbackStartFrame);
            auto endIt   = std::lower_bound(engine.metronome_beats.begin(), engine.metronome_beats.end(), callbackEndFrame);

            constexpr int kClickFrames = (int)(0.02 * SAMPLE_RATE);  // 20ms
            for (auto it = beginIt; it != endIt; ++it) {
                const long long beatFrame = *it;
                const size_t beatIndex = (size_t)(it - engine.metronome_beats.begin());
                // Every 4th beat is the "tick" (assumed downbeat — we have
                // no real bar/phase detection, just beat positions, so 4/4
                // is the working assumption, same as Ableton's tick/tock).
                // metronome_downbeat_offset shifts *which* beat that is
                // within the bar (0-3), without touching any beat's actual
                // timing — see set_metronome_downbeat_offset.
                const int offset = engine.metronome_downbeat_offset.load(std::memory_order_relaxed);
                const bool isTick = ((long long)(beatIndex % 4) - offset + 4) % 4 == 0;
                const float freq = isTick ? 1500.0f : 900.0f;
                const long long startOffset = beatFrame - callbackStartFrame;

                for (int j = 0; j < kClickFrames; j++) {
                    const long long outIdx = startOffset + j;
                    if (outIdx < 0) continue;
                    if (outIdx >= (long long)frameCount) break;
                    const float t = (float)j / SAMPLE_RATE;
                    const float envelope = std::exp(-t * 60.0f);  // quick percussive decay
                    const float sample = 0.35f * envelope * std::sin(2.0f * (float)M_PI * freq * t);
                    out[outIdx * 2]     = std::clamp(out[outIdx * 2]     + sample, -1.0f, 1.0f);
                    out[outIdx * 2 + 1] = std::clamp(out[outIdx * 2 + 1] + sample, -1.0f, 1.0f);
                }
            }
        }
    }

    // Track timing logic
    if (!scratching) {
        long long frames_left = engine.frames_until_switch;
        if (frames_left > -1) {
            if (frames_left <= frameCount) {
                engine.total_frames = engine.pending_total_frames.load();
                long long new_frames = frameCount - frames_left;
                // current_frame always carries +output_latency_frames on top
                // of the true musical position (see seek()'s identical
                // `target + output_latency_frames`, and get_position()'s
                // `current_frame - output_latency_frames`) — must stay
                // consistent here too, or anything that compares a known
                // musical position directly against current_frame's raw
                // value (e.g. the metronome) drifts by output_latency_frames
                // worth of time until the next seek happens to "fix" it by
                // re-establishing the convention.
                engine.current_frame = new_frames + engine.output_latency_frames.load();
                engine.frames_until_switch = -1;
                engine.track_switched_flag = true;
            } else {
                engine.frames_until_switch -= frameCount;
                engine.current_frame += frameCount;
            }
        } else {
            engine.current_frame += frameCount;
        }
        // Stamp "now" as the instant current_frame's new value became valid
        // — callers (get_position_anchor) project forward from this point
        // using wall-clock elapsed time instead of waiting for the next
        // callback, which is the only way to get sub-buffer-period position
        // resolution out of a counter that only moves once per callback.
        using namespace std::chrono;
        engine.current_frame_timestamp_ns.store(
                now_ns(),
                std::memory_order_relaxed);
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
                // get_position() subtracts output_latency_frames, so offset
                // current_frame here to cancel that out — otherwise the
                // reported position jumps backward by the latency amount
                // the instant playback resumes after a seek.
                engine.current_frame = target + engine.output_latency_frames.load();
                engine.frames_until_switch = -1;
                // Re-anchor current_frame_timestamp_ns to right now — without
                // this, get_position_anchor()'s timestamp stays whatever it
                // was from the last *normal* playback callback (which can be
                // many seconds stale after a scratch-mode drag, since
                // data_callback's "Track timing logic" skips updating this
                // timestamp entirely while engine.is_scratching is true — see
                // below). A caller projecting position forward by elapsed-
                // since-that-stale-timestamp would overshoot by however long
                // the drag lasted, landing playback far past the drop point
                // the instant scratch mode released back to normal playback.
                {
                    using namespace std::chrono;
                    engine.current_frame_timestamp_ns.store(
                            now_ns(),
                            std::memory_order_relaxed);
                }
                
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
                        // scratch_decoder reads directly from engine.file_data's
                        // memory (ma_decoder_init_memory) — must be invalidated
                        // before that memory gets replaced below, or it's left
                        // pointing at freed/moved-from memory.
                        if (engine.scratch_decoder_loaded) {
                            engine.is_scratching.store(false);
                            ma_decoder_uninit(&engine.scratch_decoder);
                            engine.scratch_decoder_loaded = false;
                        }
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
        } else if (engine.is_scratching.load() && engine.scratch_decoder_loaded.load()
                && !engine.scratch_refill_in_progress.load()) {
            // Keep the sliding scratch window centered on the cursor in the
            // background, so the real-time audio callback never blocks on a
            // decoder seek — recenters once the cursor comes within
            // kScratchRefillMarginFrames of either edge of the active window.
            int activeIdx = engine.scratch_active_window.load(std::memory_order_acquire);
            long long windowStart = engine.scratch_window_start_frame[activeIdx].load(std::memory_order_acquire);
            long long cursorFrame = engine.scratch_origin_frame.load() +
                    (long long)std::llround(engine.scratch_cumulative_delta.load(std::memory_order_relaxed));
            long long offsetInWindow = cursorFrame - windowStart;
            long long windowFrames = (long long)AudioEngine::kScratchWindowFrames;

            bool nearStart = offsetInWindow < AudioEngine::kScratchRefillMarginFrames;
            bool nearEnd = offsetInWindow > windowFrames - AudioEngine::kScratchRefillMarginFrames;
            if (nearStart || nearEnd) {
                int inactiveIdx = 1 - activeIdx;
                long long newStart = cursorFrame - windowFrames / 2;
                if (newStart < 0) newStart = 0;
                long long decoderLen = engine.scratch_decoder_total_frames.load();
                if (decoderLen > 0 && newStart > decoderLen - 1) newStart = decoderLen - 1;
                if (newStart < 0) newStart = 0;

                // Dragging past either end pins newStart at the same clamped
                // boundary every iteration (cursorFrame keeps going further
                // negative/past-length, but the window can't follow past 0
                // or the decoder's actual length) — without this check,
                // that re-triggers an identical refill every single
                // producer_loop iteration in a tight spin, repeatedly
                // grabbing decode_mutex and starving anything else that
                // needs it (e.g. seek() on release, called synchronously
                // from the GUI thread — looks exactly like an app freeze).
                if (newStart != windowStart) {
                    engine.scratch_refill_in_progress.store(true);
                    std::lock_guard<std::mutex> lock(engine.decode_mutex);
                    if (engine.is_scratching.load() &&
                            fill_scratch_window(engine.scratch_window[inactiveIdx], newStart)) {
                        engine.scratch_window_start_frame[inactiveIdx].store(newStart, std::memory_order_release);
                        engine.scratch_active_window.store(inactiveIdx, std::memory_order_release);
                    }
                    engine.scratch_refill_in_progress.store(false);
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
            } else {
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
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
        // low_latency forces tiny periods; combined with the visualizer FFT
        // (and metronome synthesis) running in the callback, that's not
        // enough headroom on any platform — causes underruns, audible as
        // glitchy/distorted clicks on transients like the metronome. But
        // ma_performance_profile_conservative overcorrects wildly depending
        // on backend — WASAPI picked 3 periods of 100ms each (300ms total),
        // which is far more buffered-ahead audio than the FFT/metronome ever
        // needed headroom for, and made the waveform's position visibly lag
        // real audio (get_position()'s output_latency_frames compensation
        // is a static, computed-once estimate — a seek/track-start's first
        // audible frame doesn't actually move that fast in practice on a
        // buffer this large). Set the period size explicitly instead: ~23ms
        // x3 periods (~70ms total) is still many times low_latency's buffer
        // but a fraction of conservative's, on every backend.
        config.periodSizeInFrames = 1024;
        config.periods = 3;
        #ifdef __linux__
            ma_backend backends[] = { ma_backend_pulseaudio };
            if (ma_device_init_ex(backends, 1, NULL, &config, &engine.device) != MA_SUCCESS) {
                ma_device_init(NULL, &config, &engine.device);
            }
        #else
            ma_device_init(NULL, &config, &engine.device);
        #endif

        // current_frame counts frames handed to the backend's data callback,
        // but the backend itself buffers ~(internalPeriodSizeInFrames *
        // internalPeriods) frames before they're actually audible (this gap
        // is much larger on Linux/PulseAudio's conservative profile than on
        // other platforms' low-latency profile). Subtract that buffered
        // amount in get_position() so the scrubber tracks audible playback.
        {
            ma_uint64 latency_internal = (ma_uint64)engine.device.playback.internalPeriodSizeInFrames *
                                          (ma_uint64)engine.device.playback.internalPeriods;
            ma_uint32 internal_rate = engine.device.playback.internalSampleRate;
            if (internal_rate > 0) {
                engine.output_latency_frames = (long long)(latency_internal * SAMPLE_RATE / internal_rate);
            }
        }

        ma_device_start(&engine.device);
    }

    // ── Port of Mixxx BeatUtils (beatutils.cpp) ──────────────────────────────
    // All beat positions and lengths are in audio frames (doubles).

    static float bpm_try_snap(float mn, float center, float mx, float fraction) {
        float snap = roundf(center * fraction) / fraction;
        return (snap > mn && snap < mx) ? snap : -1.0f;
    }
    static float bpm_round(float mn, float center, float mx) {
        float s;
        if ((s = bpm_try_snap(mn, center, mx, 1.0f))        > 0) return s;
        if (center < 85.0f   && (s = bpm_try_snap(mn, center, mx, 2.0f))       > 0) return s;
        if (center > 127.0f  && (s = bpm_try_snap(mn, center, mx, 2.0f/3.0f)) > 0) return s;
        if ((s = bpm_try_snap(mn, center, mx, 3.0f))        > 0) return s;
        if ((s = bpm_try_snap(mn, center, mx, 12.0f))       > 0) return s;
        return center;
    }

    struct ConstRegion { double firstBeat; double beatLength; };

    // BeatUtils::retrieveConstRegions — finds phase-coherent tempo regions
    static std::vector<ConstRegion> retrieve_const_regions(
            const std::vector<double>& beats) {
        const double kMaxPhaseErr    = 0.025 * SAMPLE_RATE; // 25 ms in frames
        const double kMaxPhaseErrSum = 0.1   * SAMPLE_RATE; // 100 ms in frames
        const int    kMaxOutliers    = 1;

        if (beats.size() < 2) return {};

        int left  = 0;
        int right = (int)beats.size() - 1;
        std::vector<ConstRegion> regions;

        while (left < (int)beats.size() - 1) {
            double meanBL = (beats[right] - beats[left]) / (right - left);
            int    outliers = 0;
            double ironed   = beats[left];
            double errSum   = 0.0;
            int i = left + 1;
            for (; i <= right; ++i) {
                ironed += meanBL;
                double err = ironed - beats[i];
                errSum += err;
                if (fabs(err) > kMaxPhaseErr) {
                    ++outliers;
                    if (outliers > kMaxOutliers || i == left + 1) break;
                }
                if (fabs(errSum) > kMaxPhaseErrSum) break;
            }
            if (i > right) {
                double borderErr = 0.0;
                if (right > left + 2) {
                    double first = beats[left+1] - beats[left];
                    double last  = beats[right]  - beats[right-1];
                    borderErr = fabs(first + last - 2.0 * meanBL);
                }
                if (borderErr < kMaxPhaseErr / 2.0) {
                    regions.push_back({beats[left], meanBL});
                    left  = right;
                    right = (int)beats.size() - 1;
                    continue;
                }
            }
            --right;
        }
        regions.push_back({beats.back(), 0.0}); // sentinel
        return regions;
    }

    // BeatUtils::makeConstBpm — picks BPM from the longest coherent region,
    // tries to extend it from both ends, then snaps to a clean value.
    // anchorFrameOut, if non-null, receives the first-beat position (in
    // audio frames) of the coherent region the BPM was actually derived
    // from — the phase a beat-grid needs to draw lines that land on real
    // beats instead of just being evenly spaced from track position 0.
    static float make_const_bpm_ex(const std::vector<ConstRegion>& regions, double* anchorFrameOut) {
        const double kMaxPhaseErr   = 0.025 * SAMPLE_RATE;
        const int    kMinBeats      = 16;

        if ((int)regions.size() < 2) return 0.0f;

        // Find longest region
        int    midIdx  = 0;
        double longLen = 0.0, longBL = 0.0;
        for (int i = 0; i < (int)regions.size() - 1; ++i) {
            double len = regions[i+1].firstBeat - regions[i].firstBeat;
            if (len > longLen) { longLen = len; longBL = regions[i].beatLength; midIdx = i; }
        }
        if (longLen == 0.0) return 0.0f;

        int    longN   = (int)(longLen / longBL + 0.5);
        double blMin   = longBL - kMaxPhaseErr / longN;
        double blMax   = longBL + kMaxPhaseErr / longN;
        int    startIdx = midIdx;

        // Extend toward start
        for (int i = 0; i < midIdx; ++i) {
            double len = regions[i+1].firstBeat - regions[i].firstBeat;
            int    nb  = (int)(len / regions[i].beatLength + 0.5);
            if (nb < kMinBeats) continue;
            double tMin = regions[i].beatLength - kMaxPhaseErr / nb;
            double tMax = regions[i].beatLength + kMaxPhaseErr / nb;
            if (longBL <= tMin || longBL >= tMax) continue;
            double newLen  = regions[midIdx+1].firstBeat - regions[i].firstBeat;
            double rMin    = std::max(blMin, tMin), rMax = std::min(blMax, tMax);
            int    maxNB   = (int)round(newLen / rMin);
            int    minNB   = (int)round(newLen / rMax);
            if (minNB != maxNB) continue;
            double newBL   = newLen / minNB;
            if (newBL <= blMin || newBL >= blMax) continue;
            longLen = newLen; longBL = newBL; longN = minNB;
            blMin = longBL - kMaxPhaseErr / longN;
            blMax = longBL + kMaxPhaseErr / longN;
            startIdx = i;
            break;
        }

        // Extend toward end
        for (int i = (int)regions.size() - 2; i > midIdx; --i) {
            double len = regions[i+1].firstBeat - regions[i].firstBeat;
            int    nb  = (int)(len / regions[i].beatLength + 0.5);
            if (nb < kMinBeats) continue;
            double tMin = regions[i].beatLength - kMaxPhaseErr / nb;
            double tMax = regions[i].beatLength + kMaxPhaseErr / nb;
            if (longBL <= tMin || longBL >= tMax) continue;
            double newLen  = regions[i+1].firstBeat - regions[startIdx].firstBeat;
            double rMin    = std::max(blMin, tMin), rMax = std::min(blMax, tMax);
            int    maxNB   = (int)round(newLen / rMin);
            int    minNB   = (int)round(newLen / rMax);
            if (minNB != maxNB) continue;
            double newBL   = newLen / minNB;
            if (newBL <= blMin || newBL >= blMax) continue;
            longLen = newLen; longBL = newBL; longN = minNB;
            break;
        }

        blMin = longBL - kMaxPhaseErr / longN;
        blMax = longBL + kMaxPhaseErr / longN;

        float center = (float)(60.0 * SAMPLE_RATE / longBL);
        float mn     = (float)(60.0 * SAMPLE_RATE / blMax);
        float mx     = (float)(60.0 * SAMPLE_RATE / blMin);
        if (anchorFrameOut) *anchorFrameOut = regions[startIdx].firstBeat;
        return bpm_round(mn, center, mx);
    }
    static float make_const_bpm(const std::vector<ConstRegion>& regions) {
        return make_const_bpm_ex(regions, nullptr);
    }

    // ─────────────────────────────────────────────────────────────────────────

    // Shared by get_file_bpm/get_file_beat_grid — decodes once and runs the
    // QM tempo tracker, returning the actual per-beat onset positions (in
    // audio frames). get_file_beat_grid collapses these into one constant-
    // tempo bpm+anchor (make_const_bpm_ex) and draws a pure evenly-spaced
    // grid from that — matching Mixxx's own default behavior (see
    // get_file_beat_grid's comment) — rather than using these raw positions
    // directly as grid lines, which wandered audibly during quiet passages
    // where the detector has much weaker transient signal to work with.
    static bool analyze_beat_frames(const char* path, std::vector<double>* outBeatFrames) {
        // Decode to mono — the QM detection function operates on mono frames
        ma_decoder decoder;
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, 1, SAMPLE_RATE);
        if (ma_decoder_init_file(path, &config, &decoder) != MA_SUCCESS) return false;

        // Queen Mary tempo tracker parameters (identical to Mixxx defaults)
        const float kStepSecs      = 0.01161f;  // ~512 samples @ 44.1 kHz
        const int   kMaxBinHz      = 50;        // window = nextPow2(44100/50) = 1024
        const int   stepSizeFrames = (int)(SAMPLE_RATE * kStepSecs);
        int windowSize = 1;
        while (windowSize < SAMPLE_RATE / kMaxBinHz) windowSize <<= 1;

        DFConfig dfCfg;
        dfCfg.DFType              = DF_COMPLEXSD;
        dfCfg.stepSize            = stepSizeFrames;
        dfCfg.frameLength         = windowSize;
        dfCfg.dbRise              = 3;
        dfCfg.adaptiveWhitening   = false;
        dfCfg.whiteningRelaxCoeff = -1;
        dfCfg.whiteningFloor      = -1;

        DetectionFunction df(dfCfg);

        // Mixxx's DownmixAndOverlapHelper: pre-center first frame with silence
        std::vector<double> win_buf(windowSize, 0.0);
        int write_pos = windowSize / 2;
        std::vector<double> detResults;
        std::vector<float>  readBuf(CHUNK_SIZE);

        auto feed = [&](const float* src, size_t n) {
            size_t inRead = 0;
            while (inRead < n) {
                size_t avail  = (size_t)(windowSize - write_pos);
                size_t toCopy = std::min(n - inRead, avail);
                if (src)
                    for (size_t i = 0; i < toCopy; i++)
                        win_buf[write_pos + i] = (double)src[inRead + i];
                else
                    for (size_t i = 0; i < toCopy; i++)
                        win_buf[write_pos + i] = 0.0;
                write_pos += (int)toCopy;
                inRead    += toCopy;
                if (write_pos == windowSize) {
                    detResults.push_back(df.processTimeDomain(win_buf.data()));
                    for (int j = 0; j < windowSize - stepSizeFrames; j++)
                        win_buf[j] = win_buf[j + stepSizeFrames];
                    write_pos -= stepSizeFrames;
                }
            }
        };

        while (true) {
            ma_uint64 n = 0;
            ma_decoder_read_pcm_frames(&decoder, readBuf.data(), CHUNK_SIZE, &n);
            if (n == 0) break;
            feed(readBuf.data(), n);
        }
        ma_decoder_uninit(&decoder);

        // Finalize: flush remaining samples with silence (same as Mixxx finalize())
        size_t silenceNeeded = std::max((size_t)(windowSize - write_pos),
                                        (size_t)(windowSize / 2 - 1));
        feed(nullptr, silenceNeeded);

        if ((int)detResults.size() < 6) return false;

        // Skip first 2 frames (noise artifact) — same as Mixxx
        std::vector<double> df_vals(detResults.begin() + 2, detResults.end());

        // Beat period estimation (comb-filter Viterbi)
        std::vector<int> beatPeriod(df_vals.size() / 128 + 1);
        TempoTrackV2 tt((float)SAMPLE_RATE, stepSizeFrames);
        tt.calculateBeatPeriod(df_vals, beatPeriod);

        // Refined beat positions in df-frame units
        std::vector<double> rawBeats;
        tt.calculateBeats(df_vals, beatPeriod, rawBeats);
        if (rawBeats.size() < 2) return false;

        // Convert to audio frames (matches Mixxx's FramePos conversion)
        outBeatFrames->clear();
        outBeatFrames->reserve(rawBeats.size());
        for (double b : rawBeats)
            outBeatFrames->push_back(b * stepSizeFrames + stepSizeFrames / 2);
        return true;
    }

    EXPORT float get_file_bpm(const char* path) {
        std::vector<double> beatFrames;
        if (!analyze_beat_frames(path, &beatFrames)) return 0.0f;
        auto regions = retrieve_const_regions(beatFrames);
        return make_const_bpm_ex(regions, nullptr);
    }

    // Returns the actual number of detected beats written to outBeatMsBuffer
    // (capped at maxBeats), or -1 on failure/no-beats-detected. bpmOut
    // receives the same snapped constant-tempo BPM get_file_bpm would (for
    // display text) — computed from the same single decode, not a separate
    // one. outBeatMsBuffer[i] is the real onset position (ms) of beat i;
    // unlike the old anchor+interval extrapolation, drawing a line at each
    // of these always lands on an actual detected transient.
    // Pure constant-tempo grid — matches Mixxx's actual default behavior
    // (BeatFactory::makePreferredBeats with the default fixedTempo=true
    // preference, src/track/beatfactory.cpp upstream: Beats::fromConstTempo,
    // just bpm + one anchor frame, no per-beat positions at all). Trusting
    // raw per-beat onset detections — including the "snap to grid, fall
    // back to extrapolation" middle ground tried here previously — chases
    // the wrong layer of the problem: a correct constant bpm + anchor
    // already produces a grid that lands on real transients wherever the
    // track actually has a steady beat, with zero per-beat noise to wander
    // during quiet sections, since there's no per-beat data to wander in
    // the first place. The earlier "doesn't land on the kick" complaint
    // this was meant to fix was a wrong-BPM (octave detection) problem, not
    // a wrong-grid-shape problem — Mixxx handles that case the same way
    // this app does: the user manually corrects the BPM.
    EXPORT int get_file_beat_grid(const char* path, float* bpmOut, float* outBeatMsBuffer, int maxBeats) {
        std::vector<double> beatFrames;
        if (!analyze_beat_frames(path, &beatFrames) || beatFrames.empty()) return -1;

        auto regions = retrieve_const_regions(beatFrames);
        double anchorFrame = 0.0;
        float bpm = make_const_bpm_ex(regions, &anchorFrame);
        if (bpm <= 0.0f) return -1;
        if (bpmOut) *bpmOut = bpm;

        ma_decoder lenDecoder;
        ma_decoder_config lenConfig = ma_decoder_config_init(ma_format_f32, 1, SAMPLE_RATE);
        long long totalFrames = 0;
        if (ma_decoder_init_file(path, &lenConfig, &lenDecoder) == MA_SUCCESS) {
            ma_uint64 len = 0;
            ma_decoder_get_length_in_pcm_frames(&lenDecoder, &len);
            ma_decoder_uninit(&lenDecoder);
            totalFrames = (long long)len;
        }
        if (totalFrames <= 0) totalFrames = (long long)beatFrames.back();

        const double beatLength = 60.0 * SAMPLE_RATE / (double)bpm;

        // Don't extrapolate the grid backward past the earliest real
        // detected onset — the onset detector only produces a beat
        // candidate where there's actual transient energy, so
        // beatFrames.front() already naturally marks "where the music
        // starts," skipping any leading silence/intro. Walking the grid
        // all the way back toward frame 0 regardless (as this used to)
        // placed synthetic beat-grid lines — and metronome clicks — inside
        // silence the detector never even saw a transient in. Mixxx's own
        // grid has this same property for the same reason (its anchor
        // comes from a real detected beat, never further extrapolated
        // backward past it).
        const double earliestRealBeat = beatFrames.front();
        double slot = anchorFrame;
        while (slot - beatLength >= earliestRealBeat) slot -= beatLength;

        int count = 0;
        for (; slot <= (double)totalFrames; slot += beatLength) {
            if (slot < 0) continue;
            if (count >= maxBeats) break;
            outBeatMsBuffer[count++] = (float)(slot * 1000.0 / SAMPLE_RATE);
        }
        return count;
    }

    // Per-band (low/mid/high) waveform envelope for scratch-mode's
    // Mixxx-style coloring — same chunked-decode/mean-abs-per-point shape as
    // generate_waveform() above, but additionally splits each sample through
    // a pair of persistent one-pole filters before accumulating, so kicks
    // (low band) and transients (high band) can be colored independently
    // instead of one flat amplitude value.
    EXPORT int generate_waveform_bands(const char* path, float* low_buf, float* mid_buf, float* high_buf, int num_points) {
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

        auto lpAlpha = [](float cutoffHz) {
            float rc = 1.0f / (2.0f * (float)M_PI * cutoffHz);
            float dt = 1.0f / (float)SAMPLE_RATE;
            return dt / (rc + dt);
        };
        const float alphaLow  = lpAlpha(300.0f);
        const float alphaHigh = lpAlpha(4000.0f);

        // Persist across the whole decode (not just one point's worth of
        // frames) so the filters don't reset/click at every point boundary.
        float lpLowState  = 0.0f;
        float lpHighState = 0.0f;

        std::vector<float> temp_buf(4096);

        for (int i = 0; i < num_points; ++i) {
            // RMS per band, not peak — see the matching comment in
            // generate_waveform above; same reasoning, just split 3 ways.
            double sumSqLow = 0.0, sumSqMid = 0.0, sumSqHigh = 0.0;
            ma_uint64 total_samples_read = 0;
            ma_uint64 frames_to_read = frames_per_point;

            while (frames_to_read > 0) {
                ma_uint64 read_chunk = std::min((ma_uint64)4096, frames_to_read);
                ma_uint64 frames_read = 0;
                ma_decoder_read_pcm_frames(&decoder, temp_buf.data(), read_chunk, &frames_read);
                if (frames_read == 0) break;

                for (ma_uint64 j = 0; j < frames_read; ++j) {
                    float x = temp_buf[j];
                    lpLowState += alphaLow * (x - lpLowState);
                    lpHighState += alphaHigh * (x - lpHighState); // low-pass at the high cutoff...
                    float lowSample  = lpLowState;
                    float highSample = x - lpHighState;           // ...then high-pass = original minus its own LPF
                    float midSample  = x - lowSample - highSample;

                    sumSqLow  += static_cast<double>(lowSample)  * lowSample;
                    sumSqMid  += static_cast<double>(midSample)  * midSample;
                    sumSqHigh += static_cast<double>(highSample) * highSample;
                }

                total_samples_read += frames_read;
                frames_to_read -= frames_read;
            }

            low_buf[i]  = (total_samples_read > 0) ? (float)std::sqrt(sumSqLow  / total_samples_read) : 0.0f;
            mid_buf[i]  = (total_samples_read > 0) ? (float)std::sqrt(sumSqMid  / total_samples_read) : 0.0f;
            high_buf[i] = (total_samples_read > 0) ? (float)std::sqrt(sumSqHigh / total_samples_read) : 0.0f;
        }

        ma_decoder_uninit(&decoder);
        return (int)((total_frames * 1000) / SAMPLE_RATE);
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
            // current_frame always carries +output_latency_frames on top of
            // the true musical position — see seek()'s identical convention.
            engine.current_frame = engine.output_latency_frames.load();
            engine.buffer.clear();
            return 1;
        }
        return 0;
    }
    
    EXPORT int play_network_stream(const char* url, long long known_duration_ms) {
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

        // Streamed playback never populates engine.file_data itself (the
        // decoder reads through stream_ctx's growing buffer via
        // MyReadCallback/MySeekCallback instead) — but set_scratch_mode's
        // streaming fallback opportunistically snapshots stream_ctx.buffer
        // INTO engine.file_data the first time a streamed track is
        // scratched, and nothing was clearing that snapshot on the NEXT
        // track. Since that fallback only triggers "if file_data is
        // empty", a stale snapshot from a previous streamed track silently
        // looked valid forever after, and any scratch on a later track
        // played that old track's audio instead. Must clear here so the
        // next scratch attempt re-snapshots fresh from the new track's
        // stream_ctx.buffer instead of reusing leftover stale bytes.
        engine.file_data.clear();
        if (engine.scratch_decoder_loaded) {
            engine.is_scratching.store(false);
            ma_decoder_uninit(&engine.scratch_decoder);
            engine.scratch_decoder_loaded = false;
        }

        std::thread(live_stream_thread, std::string(url), my_session).detach();
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, CHANNELS, SAMPLE_RATE);
        ma_result result = ma_decoder_init(MyReadCallback, MySeekCallback, NULL, &config, &engine.decoder);
        if (result == MA_SUCCESS) {
            engine.decoder_loaded = true;
            if (known_duration_ms > 0) {
                // Caller already knows the duration (from server metadata) —
                // skip ma_decoder_get_length_in_pcm_frames entirely. For MP3,
                // that call has no fast path (no reliable frame-count header
                // like FLAC's STREAMINFO) and falls back to scanning every
                // frame in the file, which over a network stream means
                // waiting on the whole remote file before playback can start.
                engine.total_frames = (known_duration_ms * SAMPLE_RATE) / 1000;
            } else {
                ma_uint64 len;
                if (ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len) == MA_SUCCESS) {
                    engine.total_frames = len;
                } else { engine.total_frames = 0; }
            }
            // current_frame always carries +output_latency_frames on top of
            // the true musical position — see seek()'s identical convention.
            engine.current_frame = engine.output_latency_frames.load();
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
        // scratch_decoder reads directly from engine.file_data's memory
        // (ma_decoder_init_memory) — must be invalidated before that memory
        // is cleared/replaced below.
        if (engine.scratch_decoder_loaded) {
            engine.is_scratching.store(false);
            ma_decoder_uninit(&engine.scratch_decoder);
            engine.scratch_decoder_loaded = false;
        }
        engine.track_switched_flag = false;
        engine.frames_until_switch = -1;
        engine.file_data.clear();
        if (!load_file_to_vector(path, engine.file_data)) return 0;
        if (open_decoder_memory(engine.file_data, &engine.decoder) != MA_SUCCESS) return 0;
        ma_uint64 len;
        ma_decoder_get_length_in_pcm_frames(&engine.decoder, &len);
        engine.total_frames = len;
        // current_frame always carries +output_latency_frames on top of the
        // true musical position — see seek()'s identical convention and the
        // comment on the gapless-switch case above in data_callback.
        engine.current_frame = engine.output_latency_frames.load();
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
        // current_frame always carries +output_latency_frames on top of the
        // true musical position — see seek()'s identical convention.
        engine.current_frame = engine.output_latency_frames.load();
        engine.buffer.clear();
        if (engine.decoder_loaded) ma_decoder_seek_to_pcm_frame(&engine.decoder, 0);
    }
    
    EXPORT void seek(long long ms) { 
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        if (!engine.decoder_loaded) return; 
        engine.seek_target = (ms * SAMPLE_RATE) / 1000; 
    }
    
    EXPORT long long get_position() {
        long long pos = engine.current_frame - engine.output_latency_frames;
        if (pos < 0) pos = 0;
        return (pos * 1000) / SAMPLE_RATE;
    }

    // anchorMsOut: the latency-compensated position (same value get_position()
    // would return) at the instant anchorTimestampNsOut was captured (the top
    // of the audio callback that last advanced current_frame — see
    // current_frame_timestamp_ns above). Callers project the position forward
    // by adding wall-clock-elapsed-since-anchorTimestampNsOut, instead of
    // waiting for current_frame to jump again — current_frame only moves once
    // per audio buffer, which can be far coarser than the display's refresh
    // rate. Mirrors Mixxx's VisualPlayPosition::getAtNextVSync (mixxxdj/mixxx
    // src/waveform/visualplayposition.cpp), simplified: no playRate term
    // since this engine has no time-stretch/pitch-bend playback rate.
    EXPORT void get_position_anchor(long long* anchorMsOut, long long* anchorTimestampNsOut) {
        long long pos = engine.current_frame - engine.output_latency_frames;
        if (pos < 0) pos = 0;
        *anchorMsOut = (pos * 1000) / SAMPLE_RATE;
        *anchorTimestampNsOut = engine.current_frame_timestamp_ns.load(std::memory_order_relaxed);
    }

    EXPORT long long get_duration() { return (engine.total_frames * 1000) / SAMPLE_RATE; }

    // Cheap duration probe (header read only, no decode loop) — lets
    // callers size a waveform-analysis buffer by track length *before*
    // calling generate_waveform/generate_waveform_bands, instead of using a
    // fixed point count for every track regardless of duration (which is
    // what made our waveform resolution far coarser than Mixxx's — see
    // mainWaveformSampleRate=441 in analyzerwaveform.cpp upstream: it scales
    // points-per-second of audio, not points-per-track).
    EXPORT long long get_file_duration_ms(const char* path) {
        ma_decoder decoder;
        ma_decoder_config config = ma_decoder_config_init(ma_format_f32, 1, SAMPLE_RATE);
        if (ma_decoder_init_file(path, &config, &decoder) != MA_SUCCESS) return 0;
        ma_uint64 total_frames = 0;
        ma_result r = ma_decoder_get_length_in_pcm_frames(&decoder, &total_frames);
        ma_decoder_uninit(&decoder);
        if (r != MA_SUCCESS) return 0;
        return (long long)((total_frames * 1000) / SAMPLE_RATE);
    }
    
    EXPORT void get_vis_data(float* output) {
        std::lock_guard<std::mutex> lock(engine.vis_snapshot_mutex);
        for (int i = 0; i < 4096; ++i) output[i] = engine.vis_snapshot[i];
    }

    // --- VU Meter ---
    EXPORT float get_vu_rms_l() {
        return engine.current_vu_rms_l.load(std::memory_order_relaxed);
    }
    EXPORT float get_vu_rms_r() {
        return engine.current_vu_rms_r.load(std::memory_order_relaxed);
    }
    // ----------------------------------------

    EXPORT void set_scratch_mode(int mode) {
        bool active = (mode == 1);
        if (!active) {
            engine.is_scratching.store(false);
            return;
        }

        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        // Dedicated decoder from the SAME in-memory file bytes the normal
        // playback decoder uses — independent state, never touches
        // engine.decoder (gapless preload/transition logic is untouched).
        if (engine.scratch_decoder_loaded) {
            ma_decoder_uninit(&engine.scratch_decoder);
            engine.scratch_decoder_loaded = false;
        }
        // engine.file_data only holds the full track bytes for local-file
        // loads (load_track) and gapless-preloaded tracks. Network-streamed
        // playback (play_network_stream/stream_append) instead reads through
        // stream_ctx's own growing byte buffer via MyReadCallback/
        // MySeekCallback — file_data stays empty there, which silently
        // failed this whole function (is_scratching never became true, so
        // scratching had no audible effect at all and playback just
        // continued normally). Fall back to a snapshot of whatever's been
        // downloaded so far in that case — scratching beyond the
        // downloaded portion just won't have audio yet, the same
        // limitation streaming already has for seeking ahead of the buffer.
        if (engine.file_data.empty()) {
            std::lock_guard<std::mutex> streamLock(stream_ctx.mutex);
            if (stream_ctx.buffer.empty()) return;
            engine.file_data = stream_ctx.buffer;
        }
        if (open_decoder_memory(engine.file_data, &engine.scratch_decoder) != MA_SUCCESS) {
            return;  // is_scratching stays false — safe no-op if no track loaded
        }
        engine.scratch_decoder_loaded = true;
        ma_uint64 scratchLen = 0;
        ma_decoder_get_length_in_pcm_frames(&engine.scratch_decoder, &scratchLen);
        engine.scratch_decoder_total_frames.store((long long)scratchLen);

        // Absolute-position tracking — current_frame, latency-compensated
        // the same way get_position() is, is the best available estimate of
        // "what's actually audible right now" at the instant scratching begins.
        long long origin = engine.current_frame - engine.output_latency_frames.load();
        if (origin < 0) origin = 0;
        if (origin > (long long)scratchLen) origin = (long long)scratchLen;

        long long halfWindow = (long long)(AudioEngine::kScratchWindowFrames / 2);
        long long windowStart = origin - halfWindow;
        if (windowStart < 0) windowStart = 0;

        fill_scratch_window(engine.scratch_window[0], windowStart);
        engine.scratch_window_start_frame[0].store(windowStart, std::memory_order_release);
        engine.scratch_active_window.store(0, std::memory_order_release);
        engine.scratch_refill_in_progress.store(false);

        engine.scratch_origin_frame.store(origin);
        engine.scratch_cumulative_delta.store(0.0, std::memory_order_relaxed);
        engine.scratch_target_delta.store(0.0, std::memory_order_relaxed);
        using namespace std::chrono;
        engine.scratch_target_updated_at_ns.store(
                now_ns(),
                std::memory_order_relaxed);
        engine.scratch_pd_last_error = 0.0;
        engine.scratch_pd_filtered_rate = 0.0;

        // Set last, once the decoder/window/origin above are all actually
        // ready — the audio callback checks this flag every invocation and
        // must never see it true before its dependencies are prepared.
        engine.is_scratching.store(true);
    }

    // The absolute track position (ms) actually being played during a
    // scratch — scratch_origin_frame + the unwrapped sum of every rate
    // sample applied since scratch mode began (see scratch_cumulative_delta's
    // comment above). Returns -1 if not currently scratching (so callers
    // don't mistake a meaningless value for a real position).
    EXPORT double get_scratch_position_ms() {
        if (!engine.is_scratching.load()) return -1.0;
        double frame = (double)engine.scratch_origin_frame.load() +
                engine.scratch_cumulative_delta.load(std::memory_order_relaxed);
        if (frame < 0.0) frame = 0.0;
        double maxFrame = (double)engine.total_frames.load();
        if (maxFrame > 0.0 && frame > maxFrame) frame = maxFrame;
        return frame * 1000.0 / SAMPLE_RATE;
    }

    // Reports the mouse-implied cumulative displacement since scratch start,
    // in milliseconds — mirrors Mixxx's "scratch_position" control object.
    // data_callback runs a PD controller comparing this target against the
    // actually-achieved cumulative_delta to compute a smoothly-converging
    // playback rate every audio buffer, instead of trusting one raw
    // instantaneous velocity sample (which a single closely-spaced/noisy
    // mouse event could spike arbitrarily — the actual root cause of the
    // "jumps somewhere I don't know" bug the old approach had).
    EXPORT void set_scratch_target_delta_ms(double deltaMs) {
        using namespace std::chrono;
        engine.scratch_target_delta.store(deltaMs * SAMPLE_RATE / 1000.0, std::memory_order_relaxed);
        engine.scratch_target_updated_at_ns.store(
                now_ns(),
                std::memory_order_relaxed);
    }

    EXPORT void set_vis_active(int active) {
        engine.vis_active.store(active != 0, std::memory_order_relaxed);
    }

    EXPORT void set_metronome_enabled(int enabled) {
        engine.metronome_enabled.store(enabled != 0, std::memory_order_relaxed);
    }

    // Shifts which beat (0-3) within the assumed 4/4 bar is treated as the
    // tick/downbeat — fixes the case where the beat grid's timing is
    // correct but the detector's anchor landed on a noise transient (or
    // similar) instead of the real first beat of a bar, so "beat 0" of the
    // tick/tock alternation doesn't actually correspond to the perceived
    // downbeat. Does not change any beat's timing at all, only the tick-
    // vs-tock label applied to each. Wraps mod 4 — offsets 0-3 cover every
    // possible phase within one bar.
    EXPORT void set_metronome_downbeat_offset(int offset) {
        engine.metronome_downbeat_offset.store(((offset % 4) + 4) % 4, std::memory_order_relaxed);
    }

    // beat_ms: beat positions in ms, same latency-subtracted convention
    // get_position()/get_scratch_position_ms() use — converted here to the
    // RAW frame numbering engine.current_frame uses internally (which
    // includes output_latency_frames, same as seek()'s
    // `target + output_latency_frames` in producer_loop) so data_callback
    // can compare them directly against engine.current_frame.
    EXPORT void set_metronome_beats(const double* beat_ms, int count) {
        std::vector<long long> frames;
        frames.reserve(count > 0 ? count : 0);
        long long latencyFrames = engine.output_latency_frames.load();
        for (int i = 0; i < count; i++) {
            long long f = (long long)std::llround(beat_ms[i] * SAMPLE_RATE / 1000.0) + latencyFrames;
            frames.push_back(f);
        }
        std::lock_guard<std::mutex> lock(engine.metronome_mutex);
        engine.metronome_beats = std::move(frames);
    }

    EXPORT void cleanup() { 
        engine.running = false; 
        if (engine.producer_thread.joinable()) engine.producer_thread.join(); 
        ma_device_uninit(&engine.device); 
        std::lock_guard<std::mutex> lock(engine.decode_mutex);
        if (engine.decoder_loaded) ma_decoder_uninit(&engine.decoder);
        if (engine.next_decoder_loaded) ma_decoder_uninit(&engine.next_decoder);
        if (engine.scratch_decoder_loaded) ma_decoder_uninit(&engine.scratch_decoder);
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
            // RMS (sqrt of mean square), not plain mean-abs and not pure
            // peak. Mean-abs crushed brief transients surrounded by quiet
            // samples toward zero (quiet-but-peaky passages looked nearly
            // invisible). Pure peak overcorrected: on a modern, heavily-
            // limited master almost every ~20ms bucket's literal sample peak
            // sits near 0dBFS, so the whole waveform pegs near max with no
            // visible shape. RMS tracks a bucket's actual energy/loudness —
            // a single hot sample can't dominate it the way it dominates a
            // max() — while still responding far more to genuine loudness
            // than a flat abs-average does. Standard professional waveform
            // display technique (Audacity, etc.).
            double sumSq = 0.0;
            ma_uint64 total_samples_read = 0;
            ma_uint64 frames_to_read = frames_per_point;

            while (frames_to_read > 0) {
                ma_uint64 read_chunk = std::min((ma_uint64)4096, frames_to_read);
                ma_uint64 frames_read = 0;
                ma_decoder_read_pcm_frames(&decoder, temp_buf.data(), read_chunk, &frames_read);

                if (frames_read == 0) break;

                for (ma_uint64 j = 0; j < frames_read; ++j) {
                    sumSq += static_cast<double>(temp_buf[j]) * temp_buf[j];
                }

                total_samples_read += frames_read;
                frames_to_read -= frames_read;
            }
            out_buffer[i] = (total_samples_read > 0) ? (float)std::sqrt(sumSq / total_samples_read) : 0.0f;
        }
        
        ma_decoder_uninit(&decoder);
        return (int)((total_frames * 1000) / SAMPLE_RATE); 
    }
}