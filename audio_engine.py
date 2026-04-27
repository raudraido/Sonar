import ctypes
import os
import platform
import time
import tempfile
import requests
import threading

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QMetaObject, Qt


class AudioEngine(QObject):
    positionChanged     = pyqtSignal(int)
    waveform_generated  = pyqtSignal(list)
    durationChanged     = pyqtSignal(int)
    endOfMedia          = pyqtSignal()
    mediaSwitched       = pyqtSignal()
    visualizerDataReady = pyqtSignal(list)
    vuDataReady         = pyqtSignal(float)

    def __init__(self):
        super().__init__()

        if platform.system() == "Windows":
            lib_path = os.path.join(os.path.dirname(__file__), "audio_core.dll")
            libs_dir = os.path.join(os.path.dirname(__file__), "libs")
            if os.path.isdir(libs_dir):
                os.add_dll_directory(libs_dir)
        else:
            lib_path = os.path.join(os.path.dirname(__file__), "audio_core.so")

        self.lib = ctypes.CDLL(lib_path)

        # ------------------------------------------------------------------
        # ctypes bindings — map Python calls to C++ functions
        # ------------------------------------------------------------------
        self.lib.audio_init.restype  = None
        self.lib.cleanup.restype     = None

        self.lib.load_track.argtypes    = [ctypes.c_char_p]
        self.lib.load_track.restype     = ctypes.c_int
        self.lib.preload_track.argtypes = [ctypes.c_char_p]
        self.lib.preload_track.restype  = None

        self.lib.play_network_stream.argtypes    = [ctypes.c_char_p]
        self.lib.play_network_stream.restype     = ctypes.c_int
        self.lib.preload_network_stream.argtypes = [ctypes.c_char_p]
        self.lib.preload_network_stream.restype  = None

        self.lib.stream_start.restype        = None
        self.lib.stream_append.argtypes      = [ctypes.c_char_p, ctypes.c_int]
        self.lib.stream_append.restype       = None
        self.lib.stream_end.restype          = None
        self.lib.stream_init_decoder.restype = ctypes.c_int

        self.lib.play.restype        = None
        self.lib.audio_pause.restype = None
        self.lib.stop.restype        = None

        self.lib.seek.argtypes         = [ctypes.c_longlong]
        self.lib.seek.restype          = None
        self.lib.get_position.restype  = ctypes.c_longlong
        self.lib.get_duration.restype  = ctypes.c_longlong
        self.lib.set_duration.argtypes = [ctypes.c_longlong]
        self.lib.set_duration.restype  = None

        self.lib.set_volume.argtypes = [ctypes.c_int]
        self.lib.set_volume.restype  = None

        self.lib.check_track_switch.restype    = ctypes.c_int
        self.lib.is_transition_pending.restype = ctypes.c_int

        self.lib.get_vis_data.argtypes = [ctypes.POINTER(ctypes.c_float)]
        self.lib.get_vis_data.restype  = None
        
        self.lib.get_vu_rms.argtypes = []
        self.lib.get_vu_rms.restype = ctypes.c_float

        self.lib.set_scratch_mode.argtypes     = [ctypes.c_int]
        self.lib.set_scratch_mode.restype      = None
        self.lib.set_scratch_velocity.argtypes = [ctypes.c_float]
        self.lib.set_scratch_velocity.restype  = None

        self.lib.set_vis_active.argtypes = [ctypes.c_int]
        self.lib.set_vis_active.restype  = None

        self.lib.generate_waveform.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        self.lib.generate_waveform.restype  = ctypes.c_int

        self.lib.get_file_bpm.argtypes = [ctypes.c_char_p]
        self.lib.get_file_bpm.restype  = ctypes.c_float

        # ------------------------------------------------------------------
        # Initialise engine and polling timer
        # ------------------------------------------------------------------
        self.lib.audio_init()

        self.is_playing             = False
        self.total_ms               = 0
        self.current_buffer         = None
        self.ignore_end_checks_until = 0.0

        self.update_timer = QTimer()
        self.update_timer.setInterval(16)
        self.update_timer.timeout.connect(self._poll_status)

        self.vis_array_type      = ctypes.c_float * 4096
        self.vis_buffer          = self.vis_array_type()
        self._vis_list           = [0.0] * 700
        self._visualizer_active  = True

    # ------------------------------------------------------------------
    # BPM analysis
    # ------------------------------------------------------------------
    def analyze_bpm(self, path: str) -> float:
        """Ask the C++ engine to calculate the BPM of a local file."""
        if not self.lib:
            return 0.0
        return round(self.lib.get_file_bpm(path.encode('utf-8')), 2)

    # ------------------------------------------------------------------
    # Waveform generation
    # ------------------------------------------------------------------
    def request_waveform(self, path: str, num_points: int = 3000):
        """Generate a waveform array in a background thread. Handles stream URLs via a temp file."""
        def task():
            if not self.lib:
                return
            target_path = path
            is_temp     = False
            if path.startswith('http://') or path.startswith('https://'):
                try:
                    response = requests.get(path, stream=True)
                    fd, target_path = tempfile.mkstemp(suffix=".media")
                    with os.fdopen(fd, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            f.write(chunk)
                    is_temp = True
                except Exception as e:
                    print(f"[AudioEngine] Waveform download failed: {e}")
                    return
            out_array = (ctypes.c_float * num_points)()
            exact_ms  = self.lib.generate_waveform(target_path.encode('utf-8'), out_array, num_points)
            if is_temp:
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            if exact_ms > 0:
                self.total_ms = exact_ms
                self.durationChanged.emit(exact_ms)
                raw_data   = list(out_array)
                max_val    = max(raw_data) if max(raw_data) > 0 else 1.0
                self.waveform_generated.emit([x / max_val for x in raw_data])

        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------------
    # Local file loading
    # ------------------------------------------------------------------
    def load_track(self, path: str) -> bool:
        if not self.lib:
            return False
        if self.lib.load_track(path.encode('utf-8')) == 1:
            self.total_ms = self.lib.get_duration()
            self.durationChanged.emit(int(self.total_ms))
            self.positionChanged.emit(0)
            return True
        return False

    def set_duration(self, ms: int):
        if self.lib:
            self.lib.set_duration(int(ms))
        self.total_ms = ms
        self.durationChanged.emit(int(ms))

    # ------------------------------------------------------------------
    # Qt polling timer (~62 fps while playing)
    # ------------------------------------------------------------------
    def _poll_status(self):
        try:
            pos = self.lib.get_position()

            if self.lib.check_track_switch() == 1:
                self.total_ms = self.lib.get_duration()
                if self._visualizer_active:
                    self.durationChanged.emit(int(self.total_ms))
                    self.positionChanged.emit(0)
                self.mediaSwitched.emit()

            if self._visualizer_active:
                self.positionChanged.emit(pos)
                if self.is_playing:
                    self.lib.get_vis_data(self.vis_buffer)
                    self._vis_list = self.vis_buffer[:700]
                    self.visualizerDataReady.emit(self._vis_list)

                    true_rms = self.lib.get_vu_rms()
                    self.vuDataReady.emit(true_rms)

            if time.time() > getattr(self, 'ignore_end_checks_until', 0):
                if self.total_ms > 0 and pos >= (self.total_ms - 200):
                    if self.is_pending_switch() == 0:
                        self.endOfMedia.emit()
                        self.ignore_end_checks_until = time.time() + 2.0
        except Exception as e:
            print(f"[AudioEngine] Polling error: {e}")

    def set_visualizer_active(self, enabled: bool):
        self._visualizer_active = enabled
        self.update_timer.setInterval(16 if enabled else 1000)
        if self.lib:
            self.lib.set_vis_active(1 if enabled else 0)

    def queue_next_track(self, file_path: str):
        if self.lib:
            self.lib.preload_track(str(file_path).encode('utf-8'))

    # ------------------------------------------------------------------
    # Transport controls
    # ------------------------------------------------------------------
    def play(self):
        if self.lib:
            self.lib.play()
        self.is_playing = True
        QMetaObject.invokeMethod(self.update_timer, "start", Qt.ConnectionType.QueuedConnection)

    def pause(self):
        if self.lib:
            self.lib.audio_pause()
        self.is_playing = False
        QMetaObject.invokeMethod(self.update_timer, "stop", Qt.ConnectionType.QueuedConnection)

    def stop(self):
        if self.lib:
            self.lib.stop()
        self.is_playing = False
        self.positionChanged.emit(0)
        QMetaObject.invokeMethod(self.update_timer, "stop", Qt.ConnectionType.QueuedConnection)

    def seek(self, ms: int):
        if self.lib:
            self.lib.seek(int(ms))
        self.positionChanged.emit(ms)
        self.ignore_end_checks_until = time.time() + 2.0

    def set_volume(self, val: int):
        self.volume = val
        if self.lib:
            self.lib.set_volume(int(val))

    # ------------------------------------------------------------------
    # Scratch / turntable
    # ------------------------------------------------------------------
    def set_scratch_mode(self, active: bool):
        if self.lib:
            self.lib.set_scratch_mode(ctypes.c_int(1 if active else 0))

    def set_scratch_velocity(self, velocity: float):
        if self.lib:
            self.lib.set_scratch_velocity(ctypes.c_float(float(velocity)))

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------
    def is_pending_switch(self) -> int:
        if self.lib and hasattr(self.lib, 'is_transition_pending'):
            return self.lib.is_transition_pending()
        return 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self):
        if self.lib:
            self.lib.cleanup()
