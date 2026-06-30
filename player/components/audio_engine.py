import array
import ctypes
import os
import platform
import re
import struct
import sys
import time
import tempfile
import requests
import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, QMetaObject, Qt


# ----------------------------------------------------------------------------
# Waveform sample cache — persists the downsampled envelope arrays computed
# by generate_waveform/generate_waveform_bands so replaying a track skips the
# full network download + native decode that previously ran every time (see
# request_waveform/request_waveform_bands below). One binary file per track
# under app_data/waveform_cache, keyed by track_id — kept separate from
# bpm_cache/beatgrid_cache.json since these arrays (up to ~400k floats each)
# would bloat a single JSON file far more than BPM/beat-grid data ever does.
# ----------------------------------------------------------------------------
_WFC_MAGIC = b'WFC1'
_SAFE_ID_RE = re.compile(r'[^A-Za-z0-9_.-]')


def _waveform_cache_dir():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_dir = os.path.join(base_dir, "app_data", "waveform_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _waveform_cache_path(track_id):
    safe = _SAFE_ID_RE.sub('_', str(track_id))[:200]
    return os.path.join(_waveform_cache_dir(), f"{safe}.wfc")


def _read_waveform_cache_raw(path):
    """Returns (duration_ms, num_points, overall_or_None, bands_or_None) from
    a .wfc file, or None if missing/unreadable. bands, if present, is a
    [low, mid, high] list of float lists."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if len(data) < 20 or data[:4] != _WFC_MAGIC:
            return None
        duration_ms, num_points, flags = struct.unpack_from('<qii', data, 4)
        offset = 20
        overall = bands = None
        if flags & 1:
            arr = array.array('f')
            arr.frombytes(data[offset:offset + num_points * 4])
            offset += num_points * 4
            overall = list(arr)
        if flags & 2:
            bands = []
            for _ in range(3):
                arr = array.array('f')
                arr.frombytes(data[offset:offset + num_points * 4])
                offset += num_points * 4
                bands.append(list(arr))
        return duration_ms, num_points, overall, bands
    except Exception:
        return None


def _load_waveform_cache(track_id, expected_duration_ms, expected_num_points, need_overall, need_bands):
    """Returns {'overall': [...]} and/or {'low'/'mid'/'high': [...]} if a
    valid, matching cache entry exists, else None. A mismatched duration
    (track's source file changed) or point count (density formula/track
    length changed) is treated as a miss rather than partially trusted —
    the whole point is correctness, not just speed."""
    raw = _read_waveform_cache_raw(_waveform_cache_path(track_id))
    if not raw:
        return None
    duration_ms, num_points, overall, bands = raw
    if num_points != expected_num_points:
        return None
    if expected_duration_ms and abs(duration_ms - expected_duration_ms) > 1000:
        return None
    if need_overall and overall is None:
        return None
    if need_bands and bands is None:
        return None
    result = {}
    if overall is not None:
        result['overall'] = overall
    if bands is not None:
        result['low'], result['mid'], result['high'] = bands
    return result


def _save_waveform_cache(track_id, duration_ms, num_points, overall=None, bands=None):
    """Writes (or merges into) the .wfc file for track_id. overall is a flat
    float list; bands is a [low, mid, high] list of float lists. Merges with
    whatever's already on disk as long as duration/num_points still match —
    request_waveform and request_waveform_bands each only have one half of
    the data, and both get called back-to-back in scratch mode."""
    path = _waveform_cache_path(track_id)
    raw = _read_waveform_cache_raw(path)
    if raw and raw[0] == duration_ms and raw[1] == num_points:
        if overall is None:
            overall = raw[2]
        if bands is None:
            bands = raw[3]
    flags = (1 if overall is not None else 0) | (2 if bands is not None else 0)
    if flags == 0:
        return
    try:
        with open(path, 'wb') as f:
            f.write(_WFC_MAGIC)
            f.write(struct.pack('<qii', int(duration_ms), int(num_points), flags))
            if overall is not None:
                array.array('f', overall).tofile(f)
            if bands is not None:
                for band in bands:
                    array.array('f', band).tofile(f)
    except Exception as e:
        print(f"[AudioEngine] Could not save waveform cache: {e}")


class AudioEngine(QObject):
    positionChanged     = pyqtSignal(int)   # continuous polled position — may jitter by a few ms
    positionJumped      = pyqtSignal(int)   # discontinuous jump (seek/track-start/stop/loop) — exact
    waveform_generated  = pyqtSignal(list)
    waveform_bands_generated = pyqtSignal(list, list, list)  # low, mid, high envelopes
    durationChanged     = pyqtSignal(int)
    endOfMedia          = pyqtSignal()
    mediaSwitched       = pyqtSignal()
    visualizerDataReady = pyqtSignal(list)
    vuDataReady         = pyqtSignal(float, float)

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

        self.lib.play_network_stream.argtypes    = [ctypes.c_char_p, ctypes.c_longlong]
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
        self.lib.get_position_anchor.argtypes = [
            ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong)]
        self.lib.get_position_anchor.restype = None
        self.lib.get_duration.restype  = ctypes.c_longlong
        self.lib.set_duration.argtypes = [ctypes.c_longlong]
        self.lib.set_duration.restype  = None

        self.lib.set_volume.argtypes = [ctypes.c_int]
        self.lib.set_volume.restype  = None

        self.lib.check_track_switch.restype    = ctypes.c_int
        self.lib.is_transition_pending.restype = ctypes.c_int

        self.lib.get_vis_data.argtypes = [ctypes.POINTER(ctypes.c_float)]
        self.lib.get_vis_data.restype  = None
        
        self.lib.get_vu_rms_l.argtypes = []
        self.lib.get_vu_rms_l.restype = ctypes.c_float
        self.lib.get_vu_rms_r.argtypes = []
        self.lib.get_vu_rms_r.restype = ctypes.c_float

        self.lib.set_scratch_mode.argtypes     = [ctypes.c_int]
        self.lib.set_scratch_mode.restype      = None
        self.lib.set_scratch_target_delta_ms.argtypes = [ctypes.c_double]
        self.lib.set_scratch_target_delta_ms.restype  = None
        self.lib.get_scratch_position_ms.argtypes = []
        self.lib.get_scratch_position_ms.restype  = ctypes.c_double
        self.lib.set_scratch_inertia.argtypes  = [ctypes.c_int]
        self.lib.set_scratch_inertia.restype   = None
        self.lib.get_scratch_rate.argtypes     = []
        self.lib.get_scratch_rate.restype      = ctypes.c_double

        self.lib.set_vis_active.argtypes = [ctypes.c_int]
        self.lib.set_vis_active.restype  = None

        self.lib.set_metronome_enabled.argtypes = [ctypes.c_int]
        self.lib.set_metronome_enabled.restype  = None
        self.lib.set_metronome_beats.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int]
        self.lib.set_metronome_beats.restype  = None
        self.lib.set_metronome_downbeat_offset.argtypes = [ctypes.c_int]
        self.lib.set_metronome_downbeat_offset.restype  = None

        self.lib.generate_waveform.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        self.lib.generate_waveform.restype  = ctypes.c_int

        self.lib.get_file_duration_ms.argtypes = [ctypes.c_char_p]
        self.lib.get_file_duration_ms.restype  = ctypes.c_longlong

        self.lib.get_file_bpm.argtypes = [ctypes.c_char_p]
        self.lib.get_file_bpm.restype  = ctypes.c_float

        self.lib.generate_waveform_bands.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        self.lib.generate_waveform_bands.restype = ctypes.c_int

        self.lib.get_file_beat_grid.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        self.lib.get_file_beat_grid.restype = ctypes.c_int

        # ------------------------------------------------------------------
        # Initialise engine and polling timer
        # ------------------------------------------------------------------
        self.lib.audio_init()

        self.is_playing             = False
        self._is_scratching         = False
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

    _MAX_BEAT_GRID_POINTS = 20000  # generous even for a long, fast-tempo mix

    def analyze_beatgrid(self, path: str):
        """Returns (bpm, [beat_position_ms, ...]) for beat-grid line
        placement, or None if no coherent tempo could be detected. Each
        position is a real detected beat onset (see get_file_beat_grid in
        audio_core.cpp) — unlike extrapolating evenly-spaced lines from a
        single anchor+bpm, this can't drift off real transients due to
        tempo not being perfectly constant or an octave (half/double-tempo)
        detection error compounding over many beats."""
        if not self.lib:
            return None
        bpm_out = ctypes.c_float(0.0)
        beat_array = (ctypes.c_float * self._MAX_BEAT_GRID_POINTS)()
        count = self.lib.get_file_beat_grid(
            path.encode('utf-8'), ctypes.byref(bpm_out), beat_array, self._MAX_BEAT_GRID_POINTS)
        if count <= 0:
            return None
        return (round(bpm_out.value, 2), [round(beat_array[i], 1) for i in range(count)])

    # ------------------------------------------------------------------
    # Waveform generation
    # ------------------------------------------------------------------
    # Points-per-second of audio for waveform analysis resolution — matches
    # Mixxx's mainWaveformSampleRate (analyzerwaveform.cpp upstream: 441
    # visual samples/sec, scaled by track length, not a fixed total point
    # count). A flat 10000-points-per-track default made longer tracks far
    # coarser than this — e.g. ~40 points/sec on a 4-minute song — which is
    # why scratch mode's zoomed-in view showed smooth interpolated blobs
    # instead of each transient's actual jagged shape: there was no finer
    # data underneath to draw.
    _WAVEFORM_DENSITY_PER_SEC = 441
    _WAVEFORM_MIN_POINTS = 2000
    _WAVEFORM_MAX_POINTS = 400000  # ~15 min of audio at full 441/sec density

    def _resolve_waveform_point_count(self, target_path: str, fallback: int) -> int:
        # Prefer the already-known duration of the currently loaded/playing
        # track over re-probing the file — keeps the point count consistent
        # with the cache-hit fast path's _points_for_duration(self.total_ms,
        # ...) call, which never touches the file at all.
        if self.total_ms > 0:
            return self._points_for_duration(self.total_ms, fallback)
        try:
            probe_ms = self.lib.get_file_duration_ms(target_path.encode('utf-8'))
        except Exception:
            probe_ms = 0
        return self._points_for_duration(probe_ms, fallback)

    def _points_for_duration(self, duration_ms, fallback: int) -> int:
        if not duration_ms or duration_ms <= 0:
            return fallback
        computed = int((duration_ms / 1000.0) * self._WAVEFORM_DENSITY_PER_SEC)
        return max(self._WAVEFORM_MIN_POINTS, min(computed, self._WAVEFORM_MAX_POINTS))

    def request_waveform(self, path: str, num_points: int = 3000, track_id=None, light: bool = False):
        """Generate a waveform array in a background thread. Handles stream
        URLs via a temp file. track_id (if given) enables the on-disk cache
        — self.total_ms is already set by the time this is called (track
        load/playback started first), so a cache hit needs neither the
        network download nor the native decode at all.

        light=True (bars/minimal-mode calls) skips the duration-based
        density formula on a cache MISS — decodes at a small fixed point
        count instead (plenty for paintBars()'s already-downsampled bar
        path) — and never writes the result to the on-disk cache, so it
        can't shadow or collide with the full-density cache scratch mode
        creates. A pre-existing full-density cache is still read and used
        normally either way; this only changes what happens when there's
        no cache yet, so bars-only sessions never pay for scratch-density
        analysis they don't need."""
        self._waveform_token = getattr(self, '_waveform_token', 0) + 1
        token = self._waveform_token

        def task():
            if not self.lib:
                return
            if track_id:
                expected_points = self._points_for_duration(self.total_ms, num_points)
                cached = _load_waveform_cache(
                    track_id, self.total_ms, expected_points, need_overall=True, need_bands=False)
                if cached is not None:
                    if token != self._waveform_token:
                        return
                    raw_data = cached['overall']
                    max_val = max(raw_data) if raw_data and max(raw_data) > 0 else 1.0
                    self.waveform_generated.emit([x / max_val for x in raw_data])
                    return
            target_path = path
            is_temp     = False
            if path.startswith('http://') or path.startswith('https://'):
                try:
                    response = requests.get(path, stream=True)
                    fd, target_path = tempfile.mkstemp(suffix=".media")
                    with os.fdopen(fd, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if token != self._waveform_token:
                                break
                            f.write(chunk)
                    is_temp = True
                except Exception as e:
                    print(f"[AudioEngine] Waveform download failed: {e}")
                    return
            if token != self._waveform_token:
                if is_temp:
                    try: os.remove(target_path)
                    except OSError: pass
                return
            actual_num_points = (self._WAVEFORM_MIN_POINTS if light
                    else self._resolve_waveform_point_count(target_path, num_points))
            out_array = (ctypes.c_float * actual_num_points)()
            exact_ms  = self.lib.generate_waveform(target_path.encode('utf-8'), out_array, actual_num_points)
            if is_temp:
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            if token != self._waveform_token:
                return
            if exact_ms > 0:
                self.total_ms = exact_ms
                self.durationChanged.emit(exact_ms)
                raw_data   = list(out_array)
                if track_id and not light:
                    _save_waveform_cache(track_id, exact_ms, actual_num_points, overall=raw_data)
                max_val    = max(raw_data) if max(raw_data) > 0 else 1.0
                self.waveform_generated.emit([x / max_val for x in raw_data])

        threading.Thread(target=task, daemon=True).start()

    def request_waveform_bands(self, path: str, num_points: int = 3000, track_id=None):
        """Like request_waveform, but emits per-band (low/mid/high) envelopes
        for scratch-mode's Mixxx-style coloring — see generate_waveform_bands
        in audio_core.cpp. Uses its own token counter, NOT request_waveform's
        — on_play_started calls both back-to-back in scratch mode, and
        sharing one counter meant this call's increment invalidated
        request_waveform's in-flight token, silently killing its thread
        before it could ever emit waveform_generated (hasRealData stuck
        False forever, permanently showing "ANALYZING WAVEFORM...")."""
        self._waveform_bands_token = getattr(self, '_waveform_bands_token', 0) + 1
        token = self._waveform_bands_token

        def task():
            if not self.lib:
                return
            if track_id:
                expected_points = self._points_for_duration(self.total_ms, num_points)
                cached = _load_waveform_cache(
                    track_id, self.total_ms, expected_points, need_overall=False, need_bands=True)
                if cached is not None:
                    if token != self._waveform_bands_token:
                        return
                    low_data, mid_data, high_data = cached['low'], cached['mid'], cached['high']
                    max_val = max(max(low_data, default=0), max(mid_data, default=0), max(high_data, default=0))
                    max_val = max_val if max_val > 0 else 1.0
                    self.waveform_bands_generated.emit(
                        [x / max_val for x in low_data],
                        [x / max_val for x in mid_data],
                        [x / max_val for x in high_data])
                    return
            target_path = path
            is_temp     = False
            if path.startswith('http://') or path.startswith('https://'):
                try:
                    response = requests.get(path, stream=True)
                    fd, target_path = tempfile.mkstemp(suffix=".media")
                    with os.fdopen(fd, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if token != self._waveform_bands_token:
                                break
                            f.write(chunk)
                    is_temp = True
                except Exception as e:
                    print(f"[AudioEngine] Band-waveform download failed: {e}")
                    return
            if token != self._waveform_bands_token:
                if is_temp:
                    try: os.remove(target_path)
                    except OSError: pass
                return
            actual_num_points = self._resolve_waveform_point_count(target_path, num_points)
            low_array  = (ctypes.c_float * actual_num_points)()
            mid_array  = (ctypes.c_float * actual_num_points)()
            high_array = (ctypes.c_float * actual_num_points)()
            ok = self.lib.generate_waveform_bands(
                target_path.encode('utf-8'), low_array, mid_array, high_array, actual_num_points)
            if is_temp:
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            if token != self._waveform_bands_token or not ok:
                return
            low_data  = list(low_array)
            mid_data  = list(mid_array)
            high_data = list(high_array)
            if track_id and self.total_ms > 0:
                _save_waveform_cache(
                    track_id, self.total_ms, actual_num_points, bands=[low_data, mid_data, high_data])
            max_val = max(max(low_data, default=0), max(mid_data, default=0), max(high_data, default=0))
            max_val = max_val if max_val > 0 else 1.0
            self.waveform_bands_generated.emit(
                [x / max_val for x in low_data],
                [x / max_val for x in mid_data],
                [x / max_val for x in high_data])

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
            self.positionJumped.emit(0)
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
    def _get_precise_position_ms(self):
        """Projects the latency-compensated position forward from the audio
        callback's own timestamp anchor (see get_position_anchor in
        audio_core.cpp) using wall-clock elapsed time, instead of returning
        whatever current_frame last jumped to — current_frame only advances
        once per audio buffer, which can be coarser than this poll's ~62Hz
        rate. Mirrors Mixxx's VisualPlayPosition::getAtNextVSync technique,
        simplified for a fixed (non-time-stretched) playback rate. Falls
        back to the plain counter while scratching (the anchor timestamp
        isn't updated during scratch — see data_callback) or paused (no
        forward motion to project)."""
        anchor_ms = ctypes.c_longlong(0)
        anchor_ns = ctypes.c_longlong(0)
        self.lib.get_position_anchor(ctypes.byref(anchor_ms), ctypes.byref(anchor_ns))
        if not self.is_playing or self._is_scratching or anchor_ns.value == 0:
            return anchor_ms.value
        elapsed_ms = (time.monotonic_ns() - anchor_ns.value) / 1e6
        projected = anchor_ms.value + elapsed_ms
        return max(0, min(int(round(projected)), self.total_ms if self.total_ms > 0 else projected))

    def _poll_status(self):
        try:
            pos = self._get_precise_position_ms()

            if self.lib.check_track_switch() == 1:
                self.total_ms = self.lib.get_duration()
                self.durationChanged.emit(int(self.total_ms))
                self.positionJumped.emit(0)
                self.mediaSwitched.emit()

            self.positionChanged.emit(pos)

            if self._visualizer_active and self.is_playing:
                self.lib.get_vis_data(self.vis_buffer)
                self._vis_list = self.vis_buffer[:700]
                self.visualizerDataReady.emit(self._vis_list)
                rms_l = self.lib.get_vu_rms_l()
                rms_r = self.lib.get_vu_rms_r()
                self.vuDataReady.emit(rms_l, rms_r)

            if time.time() > getattr(self, 'ignore_end_checks_until', 0):
                if self.total_ms > 0 and pos >= (self.total_ms - 200):
                    if self.is_pending_switch() == 0:
                        self.endOfMedia.emit()
                        self.ignore_end_checks_until = time.time() + 2.0
        except Exception as e:
            print(f"[AudioEngine] Polling error: {e}")

    def set_visualizer_active(self, enabled: bool):
        self._visualizer_active = enabled
        self.update_timer.setInterval(16 if enabled else 50)
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

    @pyqtSlot()
    def stop(self):
        if self.lib:
            self.lib.stop()
        self.is_playing = False
        self.update_timer.stop()
        self.positionJumped.emit(0)

    def seek(self, ms: int):
        if self.lib:
            self.lib.seek(int(ms))
        self.positionJumped.emit(ms)
        self.ignore_end_checks_until = time.time() + 2.0

    def set_volume(self, val: int):
        self.volume = val
        if self.lib:
            self.lib.set_volume(int(val))

    # ------------------------------------------------------------------
    # Scratch / turntable
    # ------------------------------------------------------------------
    def set_scratch_mode(self, active: bool):
        self._is_scratching = bool(active)
        if self.lib:
            self.lib.set_scratch_mode(ctypes.c_int(1 if active else 0))

    def set_scratch_target_delta_ms(self, delta_ms: float):
        """Reports the mouse-implied cumulative displacement since scratch
        start, in ms — mirrors Mixxx's "scratch_position" control object.
        audio_core.cpp's data_callback runs a position controller comparing
        this target against the actually-achieved position to compute a
        smoothly-converging playback rate every audio buffer, instead of
        trusting one raw instantaneous velocity sample (which a single
        closely-spaced/noisy mouse event could spike arbitrarily)."""
        if self.lib:
            self.lib.set_scratch_target_delta_ms(ctypes.c_double(float(delta_ms)))

    def get_scratch_position_ms(self):
        """The absolute track position actually being played during a
        scratch — the audio thread's own continuously-integrated position
        (see get_scratch_position_ms in audio_core.cpp), not a UI-side
        estimate. Returns None when not currently scratching. The UI should
        use this as the single source of truth for both the visual cursor
        and the seek target on release, instead of computing its own
        parallel position from raw mouse deltas."""
        if not self.lib:
            return None
        ms = self.lib.get_scratch_position_ms()
        return ms if ms >= 0 else None

    def set_scratch_inertia(self, enable: bool):
        """Starts/stops the "throw"/spinback decay (see set_scratch_inertia
        in audio_core.cpp) — call with True on a fast release instead of
        immediately ending scratch mode, so the platter decays back to
        normal speed instead of snapping straight to it. Still scratching
        (is_scratching stays true) while this is active."""
        if self.lib:
            self.lib.set_scratch_inertia(ctypes.c_int(1 if enable else 0))

    def get_scratch_rate(self):
        """The live, continuously-decaying playback rate during a throw
        release (1.0 == normal forward speed) — see get_scratch_rate in
        audio_core.cpp. Returns None when not currently scratching."""
        if not self.lib:
            return None
        rate = self.lib.get_scratch_rate()
        return rate if rate > -1.0 else None

    # ------------------------------------------------------------------
    # Metronome (tick/tock debug aid)
    # ------------------------------------------------------------------
    def set_metronome_enabled(self, enabled: bool):
        if self.lib:
            self.lib.set_metronome_enabled(1 if enabled else 0)

    def set_metronome_beats(self, beat_positions_ms):
        """beat_positions_ms: the same real detected (or BPM-corrected)
        beat positions driving the visual grid — see get_file_beat_grid in
        audio_core.cpp. Mixed into the real-time audio callback at the
        exact sample position (not driven by a Python/Qt timer), so the
        click is actually trustworthy for judging beat-grid alignment by
        ear, which is the whole point of a debug aid like this."""
        if not self.lib:
            return
        positions = beat_positions_ms or []
        arr = (ctypes.c_double * len(positions))(*positions)
        self.lib.set_metronome_beats(arr, len(positions))

    def set_metronome_downbeat_offset(self, offset: int):
        """Shifts which beat (0-3) within the assumed 4/4 bar is treated as
        the tick/downbeat — fixes a correctly-timed grid whose anchor still
        landed on a noise transient instead of the real first beat of a
        bar, so the tick/tock alternation is musically out of phase even
        though the grid timing itself is right. Doesn't move any beat."""
        if self.lib:
            self.lib.set_metronome_downbeat_offset(int(offset))

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
