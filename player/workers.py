"""
player/workers.py — All background QThread workers.

Each class handles one specific async task and communicates
results back to the main thread via Qt signals.
"""
import os
import time
import queue
import tempfile
import platform
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from io import BytesIO
from mutagen import File
from PIL import Image, ImageFilter

from cover_cache import CoverCache, THUMB_SIZE

_COVER_WORKERS = min(6, (os.cpu_count() or 2) + 2)

class BPMWorker(QThread):
    # --- Signal now sends (bpm_value, track_id)
    bpm_ready = pyqtSignal(float, str) 
    
    def __init__(self, audio_engine, track_data):
        super().__init__()
        self.audio_engine = audio_engine
        self.track_data = track_data 
        
    def run(self):
        temp_file_path = None
        # Use 'id' for streams, or 'path' for local files as the unique key
        track_id = str(self.track_data.get('id') or self.track_data.get('path', 'unknown'))
        
        try:
            target_path = self.track_data.get('path', '')
            stream_url = self.track_data.get('stream_url', '')
            
            if target_path and os.path.exists(target_path):
                analyze_path = target_path
            elif stream_url:
                temp_dir = tempfile.gettempdir()
                # Create a secure temp file in the Windows %TEMP% folder
                temp_file_path = os.path.join(temp_dir, f"sonar_bpm_{track_id}.mp3")
                
                response = requests.get(stream_url, stream=True, timeout=10)
                response.raise_for_status()
                with open(temp_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                analyze_path = temp_file_path
            else:
                self.bpm_ready.emit(0.0, track_id)
                return

            bpm = self.audio_engine.analyze_bpm(analyze_path)
            self.bpm_ready.emit(bpm, track_id)
            
        except Exception as e:
            print(f"[BPM] ❌ Analyzer Error: {e}")
            self.bpm_ready.emit(0.0, track_id)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try: os.remove(temp_file_path)
                except: pass



class SyncCheckWorker(QThread):
    """Checks the server for library updates without blocking the UI."""
    status_ready = pyqtSignal(int, int) # Returns: (timestamp, tab_index)

    def __init__(self, client, tab_index):
        super().__init__()
        self.client = client
        self.tab_index = tab_index

    def run(self):
        try:
            # Asks the server when it was last updated
            stamp = self.client.get_scan_status()
            self.status_ready.emit(stamp if stamp else 0, self.tab_index)
        except Exception:
            self.status_ready.emit(0, self.tab_index)



class PlaybackManager(QThread):
    # Signals to report back to UI safely
    track_started = pyqtSignal(dict) 
    error_occurred = pyqtSignal(str)

    def __init__(self, audio_engine):
        super().__init__()
        self.audio_engine = audio_engine
        self.command_queue = queue.Queue()
        self.active_download_thread = None
        
        # Generation ID: Helps us ignore old requests if user spam-clicks
        self.current_gen = 0  
        self._stop_flag = False
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def play_request(self, track_data):
        """Called by UI. Returns instantly."""
        self.current_gen += 1
        
        # Clear any pending requests (Debounce)
        with self.command_queue.mutex:
            self.command_queue.queue.clear()
            
        self.command_queue.put({
            'type': 'play',
            'data': track_data,
            'gen': self.current_gen
        })

    def stop_request(self):
        """Called by UI. Returns instantly."""
        self.current_gen += 1
        with self.command_queue.mutex:
            self.command_queue.queue.clear()
        self.command_queue.put({'type': 'stop', 'gen': self.current_gen})

    def queue_request(self, path):
        self.command_queue.put({
            'type': 'queue',
            'path': path
        })
    
    def run(self):
        while True:
            cmd = self.command_queue.get()
            
            
            if cmd['type'] == 'quit':
                self.command_queue.task_done()
                break
            
            # --- ONLY STOP IF IT'S A PLAY/STOP CMD ---
            if cmd['type'] in ('play', 'stop'):
                self._stop_flag = True
                if self.active_download_thread and self.active_download_thread.is_alive():
                    self.active_download_thread.join(timeout=0.2)
                self.audio_engine.stop()
                self._stop_flag = False

            # Check obsolescence only for play commands
            if 'gen' in cmd and cmd['gen'] != self.current_gen:
                self.command_queue.task_done()
                continue

            # --- EXECUTE ---
            if cmd['type'] == 'play':
                self._handle_play(cmd['data'], cmd['gen'])
            
            elif cmd['type'] == 'queue':
                
                print(f"Manager: Queuing next track {cmd['path']}")
                self.audio_engine.queue_next_track(cmd['path'])
            
            self.command_queue.task_done()

    def _handle_play(self, track_data, gen):
        t0 = track_data.get('debug_t0', time.time())
        try:
            stream_url = track_data.get('stream_url')
            
            # --- NETWORK STREAM LOGIC ---
            if stream_url:
                print(f"[TIMING] +{time.time() - t0:.3f}s | Passing URL to Native C++ Engine")
                
                res = self.audio_engine.lib.play_network_stream(stream_url.encode('utf-8'))

                if res == 1:
                    print(f"[TIMING] +{time.time() - t0:.3f}s | Native Decoder Init SUCCESS. Playing now.")
                    if 'duration_ms' in track_data:
                        self.audio_engine.set_duration(track_data['duration_ms'])
                    self.audio_engine.play()
                    self.track_started.emit(track_data)
                else:
                    print(f"[TIMING] +{time.time() - t0:.3f}s | Native Decoder Init FAILED")
            
            # --- LOCAL FILE LOGIC ---
            else:
                path = track_data.get('path')
                if path and os.path.exists(path):
                    print(f"[TIMING] +{time.time() - t0:.3f}s | Loading Local Track")
                    
                    res = self.audio_engine.load_track(path)
                    
                    if res:
                        self.audio_engine.play()
                        self.track_started.emit(track_data)
                    else:
                        print("Local Native Decoder Init FAILED")
                        
        except Exception as e:
            print(f"Manager Error: {e}")



class BlurWorker(QThread):
    # --- (Blurred_Bg_QImage, Cover_Art_QImage, Raw_Bytes, Hex_Color)
    finished = pyqtSignal(QImage, QImage, object, str) 

    def __init__(self, path, blur_radius, overlay_alpha, default_color, calc_color=True, raw_data_override=None, target_size=None, art_size=500):
        super().__init__()
        self.path = path
        self.blur_radius = blur_radius
        self.overlay_alpha = overlay_alpha
        self.default_color = default_color
        self.calc_color = calc_color 
        self.raw_data_override = raw_data_override 
        self.target_size = target_size
        self.art_size = art_size

    def run(self):
        try:
            if self.isInterruptionRequested(): return

            raw_art = None
            if self.raw_data_override: 
                raw_art = self.raw_data_override
            elif self.path and self.path.lower().endswith(('.jpg', '.jpeg', '.png')):
                with open(self.path, "rb") as f: raw_art = f.read()
            elif self.path:
                try:
                    audio_file = File(self.path)
                    if audio_file and hasattr(audio_file, 'tags'):
                        if hasattr(audio_file.tags, 'keys'): 
                            for key in audio_file.tags.keys():
                                if key.startswith('APIC'): raw_art = audio_file.tags[key].data; break 
                        if not raw_art and 'covr' in audio_file.tags: raw_art = audio_file.tags['covr'][0]
                        if not raw_art and hasattr(audio_file, 'pictures') and audio_file.pictures: raw_art = audio_file.pictures[0].data
                except: pass
            
            if self.isInterruptionRequested(): return

            # 🟢 Pre-decode and pre-scale the album cover on the background thread!
            cover_qimg = QImage()
            if raw_art:
                cover_qimg.loadFromData(raw_art)
                if not cover_qimg.isNull() and self.art_size > 0:
                    cover_qimg = cover_qimg.scaled(self.art_size, self.art_size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)

            dominant_hex = self.default_color 
            if raw_art:
                img = Image.open(BytesIO(raw_art))
                
                # --- Color Calculation ---
                if self.calc_color:
                    try:
                        small = img.resize((100, 100))
                        quantized = small.quantize(colors=1)
                        palette = quantized.getpalette()[:3]
                        r, g, b = palette
                        r_norm, g_norm, b_norm = r / 255.0, g / 255.0, b / 255.0
                        max_val = max(r_norm, g_norm, b_norm)
                        min_val = min(r_norm, g_norm, b_norm)
                        diff = max_val - min_val
                        if max_val == min_val: h = 0
                        elif max_val == r_norm: h = (60 * ((g_norm - b_norm) / diff) + 360) % 360
                        elif max_val == g_norm: h = (60 * ((b_norm - r_norm) / diff) + 120) % 360
                        else: h = (60 * ((r_norm - g_norm) / diff) + 240) % 360
                        
                        s = 0 if max_val == 0 else (diff / max_val)
                        v = max_val
                        if s < 0.5: s = 0.5
                        elif s < 0.7: s = min(1.0, s * 1.3)
                        if v < 0.6: v = 0.7
                        elif v > 0.85: v = 0.8
                        
                        c = v * s
                        x = c * (1 - abs(((h / 60) % 2) - 1))
                        m = v - c
                        if 0 <= h < 60: r_prime, g_prime, b_prime = c, x, 0
                        elif 60 <= h < 120: r_prime, g_prime, b_prime = x, c, 0
                        elif 120 <= h < 180: r_prime, g_prime, b_prime = 0, c, x
                        elif 180 <= h < 240: r_prime, g_prime, b_prime = 0, x, c
                        elif 240 <= h < 300: r_prime, g_prime, b_prime = x, 0, c
                        else: r_prime, g_prime, b_prime = c, 0, x
                        
                        r, g, b = int((r_prime + m) * 255), int((g_prime + m) * 255), int((b_prime + m) * 255)
                        dominant_hex = f"#{r:02x}{g:02x}{b:02x}"
                    except: dominant_hex = "#cccccc" 

                if self.isInterruptionRequested(): return

                # --- Blur Operation ---
                # 200×200 is visually identical after the final upscale, uses 4× less CPU
                low_res = img.resize((200, 200)) 
                blurred = low_res.filter(ImageFilter.GaussianBlur(radius=self.blur_radius))
                blurred = blurred.convert('RGB')
                
                if self.isInterruptionRequested(): return
                overlay = Image.new('RGB', blurred.size, (10, 10, 10))
                final = Image.blend(blurred, overlay, self.overlay_alpha)
                
                qimg = QImage(final.tobytes(), final.size[0], final.size[1], QImage.Format.Format_RGB888).copy()
                if self.target_size and not qimg.isNull():
                    scaled = qimg.scaled(self.target_size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    cx = (scaled.width() - self.target_size.width()) // 2
                    cy = (scaled.height() - self.target_size.height()) // 2
                    qimg = scaled.copy(cx, cy, self.target_size.width(), self.target_size.height())
                
                # 🟢 Emit BOTH pre-scaled images!
                self.finished.emit(qimg, cover_qimg, raw_art, dominant_hex)
            else: 
                if self.calc_color: dominant_hex = "#cccccc" 
                self.finished.emit(QImage(), QImage(), None, dominant_hex)
        except Exception as e: 
            print(f"Blur error: {e}")
            self.finished.emit(QImage(), QImage(), None, "#cccccc")



class MetadataWorker(QThread):
    progress = pyqtSignal(list)
    finished = pyqtSignal()
    
    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = file_paths
        
    def run(self):
        batch = []
        for path in self.file_paths:
            try:
                audio = File(path)
                filename_with_ext = os.path.basename(path)
                filename_base = os.path.splitext(filename_with_ext)[0]
                title = filename_base; artist = "Unknown Artist"; sec = 0; bitrate = 0; sample_rate = 0

                if audio:
                    if 'TIT2' in audio: title = str(audio['TIT2'][0])
                    elif 'title' in audio: title = str(audio['title'][0])
                    if 'TPE1' in audio: artist = " /// ".join([str(x) for x in audio['TPE1']])
                    elif 'artist' in audio: artist = " /// ".join([str(x) for x in audio['artist']])
                    if hasattr(audio, 'info'):
                        sec = int(audio.info.length)
                        if hasattr(audio.info, 'bitrate') and audio.info.bitrate: bitrate = int(audio.info.bitrate / 1000)
                        if hasattr(audio.info, 'sample_rate') and audio.info.sample_rate: sample_rate = audio.info.sample_rate

                if artist == "Unknown Artist":
                    parts = filename_base.split(" - ")
                    if len(parts) >= 3: artist = parts[1].strip(); title = " - ".join(parts[2:]).strip() 
                    elif len(parts) == 2: artist = parts[0].strip(); title = parts[1].strip()

                duration = f"{sec // 60}:{sec % 60:02d}"
                batch.append({'path': path, 'title': title, 'artist': artist, 'duration': duration, 'bitrate': bitrate, 'sample_rate': sample_rate})
                if len(batch) >= 20: self.progress.emit(batch); batch = []
            except: pass
        if batch: self.progress.emit(batch)
        self.finished.emit()



class PlaylistCoverWorker(QThread):
    """
    Pre-warms the thumb cache (300 px) for every track in the queue.
    Uses a CPU-aware parallel pool instead of a hard-coded 10 threads.
    """
    cover_downloaded = pyqtSignal()

    def __init__(self, client):
        super().__init__()
        self.client  = client
        
        self.queue   = []
        self.running = True
        self.seen    = set()
        from cover_cache import CoverCache
        self._cache  = CoverCache.instance()

    def queue_covers(self, cover_ids):
        added = False
        for cid in cover_ids:
            if cid and cid not in self.seen:
                # Skip if thumb already on disk — RowDelegate will find it
                if not self._cache.has_thumb(str(cid)):
                    self.seen.add(cid)
                    self.queue.append(cid)
                    added = True
                else:
                    self.seen.add(cid)
        if added and not self.isRunning():
            self.start()

    def _download_task(self, cid):
        """Runs in parallel on one of the 10 background threads"""
        if not self.client or not self.running:
            return False
        try:
            from cover_cache import THUMB_SIZE
            data = self.client.get_cover_art(cid, size=THUMB_SIZE)
            if data:
                self._cache.save_thumb(cid, data)
                return True
        except Exception:
            pass
        return False

    def run(self):
        with ThreadPoolExecutor(max_workers=_COVER_WORKERS) as executor:
            futures = set()
            
            while self.running:
                # Fill the pool with up to _COVER_WORKERS parallel tasks
                while self.queue and len(futures) < _COVER_WORKERS:
                    cid = str(self.queue.pop(0))
                    futures.add(executor.submit(self._download_task, cid))
                
                # If there are no active downloads and the queue is empty, we are done
                if not futures:
                    if not self.queue:
                        break
                    time.sleep(0.1)
                    continue
                
                # Wait for at least ONE cover to finish downloading
                done, futures = wait(futures, return_when=FIRST_COMPLETED, timeout=0.5)
                
                for f in done:
                    # If it successfully downloaded, instantly tell the UI to repaint!
                    if f.result() is True:
                        self.cover_downloaded.emit()



class PlaylistLoaderWorker(QThread):
    progress = pyqtSignal(list)
    finished = pyqtSignal()
    
    def __init__(self, saved_data):
        super().__init__()
        self.saved_data = saved_data

    def run(self):
        batch = []
        for track in self.saved_data:
            path = track.get('path')
            stream_url = track.get('stream_url')
            if (path and os.path.exists(path)) or stream_url: batch.append(track)
            if len(batch) >= 50: self.progress.emit(batch); batch = []
        if batch: self.progress.emit(batch)
        self.finished.emit()



class CoverLoaderWorker(QThread):
    """
    Loads FULL-SIZE (800 px) cover art for the main now-playing display.

    Critical rule: ONLY the full-size tier is consulted.  A thumb cached
    by PlaylistCoverWorker at 300 px is intentionally ignored so that the
    main display always receives a crisp 800 px image.
    """
    finished = pyqtSignal(bytes)

    def __init__(self, client, cover_id):
        super().__init__()
        self.client   = client
        self.cover_id = str(cover_id)
        from cover_cache import CoverCache
        self._cache   = CoverCache.instance()

    def run(self):
        cid = self.cover_id

        # 1. Try full-size disk/memory cache — guaranteed to be 800 px
        data = self._cache.get_full(cid)
        if data:
            self.finished.emit(data)
            return

        # 2. Fetch from server at full resolution
        try:
            from cover_cache import FULL_SIZE
            data = self.client.get_cover_art(cid, size=FULL_SIZE)
            if data:
                self._cache.save_full(cid, data)
                self.finished.emit(data)
        except Exception as e:
            print(f"[CoverLoaderWorker] Network error for {cid}: {e}")



class CrossPlatformMediaKeyListener(QThread):
    """
    Cross-platform media key listener. 
    Uses evdev on Linux and pynput on Windows.
    Signals are emitted on the Qt main thread.
    """
    sig_play_pause = pyqtSignal()
    sig_stop       = pyqtSignal()
    sig_next       = pyqtSignal()
    sig_prev       = pyqtSignal()

    def run(self):
        if platform.system() == "Windows":
            self._run_windows_listener()
        elif platform.system() == "Linux":
            self._run_linux_evdev()
        else:
            print("[MediaKeys] Unsupported OS for background media keys.")

    def _run_windows_listener(self):
        print("[MediaKeys] Starting Windows pynput listener...")
        try:
            from pynput import keyboard
        except ImportError:
            print("[MediaKeys] pynput not installed. Run: pip install pynput")
            return

        def on_press(key):
            if key == keyboard.Key.media_play_pause:
                self.sig_play_pause.emit()
            elif key == keyboard.Key.media_next:
                self.sig_next.emit()
            elif key == keyboard.Key.media_previous:
                self.sig_prev.emit()
            elif key == keyboard.Key.media_stop:
                self.sig_stop.emit()

        # The listener blocks, so it keeps the thread alive
        with keyboard.Listener(on_press=on_press) as listener:
            self.windows_listener = listener
            listener.join()

    def _run_linux_evdev(self):
        # Your original Linux logic
        DEVICE_PATH = '/dev/input/event5'
        CODE_MAP = {
            164: 'play_pause', 163: 'next',
            165: 'prev', 166: 'stop'
        }
        print(f"[MediaKeys] Opening {DEVICE_PATH} ...", flush=True)
        try:
            import evdev, selectors
            device = evdev.InputDevice(DEVICE_PATH)
            sel = selectors.DefaultSelector()
            sel.register(device, selectors.EVENT_READ)

            while not self.isInterruptionRequested():
                ready = sel.select(timeout=0.5)
                for key, _ in ready:
                    try:
                        for event in device.read():
                            if event.type != evdev.ecodes.EV_KEY or event.value != 1:
                                continue
                            action = CODE_MAP.get(event.code)
                            if action == 'play_pause': self.sig_play_pause.emit()
                            elif action == 'next': self.sig_next.emit()
                            elif action == 'prev': self.sig_prev.emit()
                            elif action == 'stop': self.sig_stop.emit()
                    except OSError:
                        break
        except Exception as e:
            print(f"[MediaKeys] Linux Error: {e}", flush=True)

    def stop_listener(self):
        if hasattr(self, 'windows_listener'):
            self.windows_listener.stop()
        self.requestInterruption()
        self.quit()
        self.wait()


class SongRefreshWorker(QThread):
    """Fetches fresh song metadata from Navidrome and emits if anything changed."""
    refreshed = pyqtSignal(int, dict)   # (playlist_index, fresh_data)

    def __init__(self, client, track_id, playlist_index):
        super().__init__()
        self.client = client
        self.track_id = track_id
        self.playlist_index = playlist_index

    def run(self):
        try:
            fresh = self.client.get_song(self.track_id)
            if fresh:
                self.refreshed.emit(self.playlist_index, fresh)
        except Exception as e:
            print(f"[SongRefreshWorker] {e}")

