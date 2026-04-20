"""
airplay_manager.py — AirPlay 1 (RAOP via cliraop) + AirPlay 2 (cliap2) support.

Binaries and shared libraries are bundled under airplay/ next to this file.
No runtime downloads required.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

# ── Platform detection ────────────────────────────────────────────────────

_SYSTEM  = platform.system().lower().replace('darwin', 'macos')
_MACHINE = platform.machine().lower()
if _MACHINE in ('x86_64', 'amd64'):    _MACHINE = 'x86_64'
elif _MACHINE in ('aarch64', 'arm64'): _MACHINE = 'aarch64'

# airplay/ directory sits next to this file
_AIRPLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'airplay')
_BUNDLE_BIN  = os.path.join(_AIRPLAY_DIR, 'bin')
_BUNDLE_LIBS = os.path.join(_AIRPLAY_DIR, f'lib/{_SYSTEM}-{_MACHINE}')
_CREDS_DIR   = os.path.expanduser('~/.config/sonar/credentials')


def _creds_path(device_id: str) -> str:
    os.makedirs(_CREDS_DIR, exist_ok=True)
    return os.path.join(_CREDS_DIR, f'{device_id}.txt')


def load_credentials(device_id: str) -> str:
    """Return stored AirPlay 2 auth key for this device, or ''."""
    try:
        with open(_creds_path(device_id)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''


def save_credentials(device_id: str, key: str):
    with open(_creds_path(device_id), 'w') as f:
        f.write(key.strip())


def _cliap2_env() -> dict:
    """Inject bundled libs so cliap2 finds its FFmpeg 5.x dependencies."""
    env = os.environ.copy()
    existing = env.get('LD_LIBRARY_PATH', '')
    env['LD_LIBRARY_PATH'] = f'{_BUNDLE_LIBS}:{existing}' if existing else _BUNDLE_LIBS
    return env


# ── mDNS service types ────────────────────────────────────────────────────

RAOP_SERVICE    = '_raop._tcp.local.'
AIRPLAY_SERVICE = '_airplay._tcp.local.'

# ── NTP ───────────────────────────────────────────────────────────────────

_NTP_EPOCH = 0x83AA7E80   # seconds between NTP epoch (1900) and Unix epoch (1970)

def _ntp_now() -> int:
    """Return current 64-bit NTP timestamp (upper 32 = seconds, lower 32 = fraction)."""
    t   = time.time()
    sec = int(t)
    us  = int((t - sec) * 1_000_000)
    return ((sec + _NTP_EPOCH) << 32) | int((us << 32) / 1_000_000)


# ── Binary / ffmpeg lookup ────────────────────────────────────────────────

def get_binary(name: str) -> str:
    """Return path to bundled cliraop or cliap2 binary."""
    path = os.path.join(_BUNDLE_BIN, f'{name}-{_SYSTEM}-{_MACHINE}')
    if not os.path.isfile(path):
        raise RuntimeError(
            f'AirPlay binary not found: {path}\n'
            f'Platform {_SYSTEM}/{_MACHINE} may not be supported.'
        )
    # Ensure executable bit is set (git may strip it)
    os.chmod(path, os.stat(path).st_mode | 0o111)
    return path


def get_ffmpeg() -> str:
    """Return system ffmpeg path."""
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    raise RuntimeError('ffmpeg not found. Install it with: sudo apt install ffmpeg')


# ── Device info ───────────────────────────────────────────────────────────

@dataclass
class AirPlayDeviceInfo:
    id:           str            # 'ap_' + MAC without colons
    name:         str
    protocol:     str            # 'airplay1' | 'airplay2'
    address:      str            # IP address string
    port:         int
    hostname:     str            # mDNS hostname (e.g. 'Device.local')
    service_name: str            # full mDNS service name
    txt_props:    dict = field(default_factory=dict)
    credentials:  str = ''       # 192-hex for AP2 auth; auth_secret for RAOP


# ── Device wrapper ────────────────────────────────────────────────────────

class AirPlayDevice:
    """
    Wraps a cliraop (AirPlay 1/RAOP) or cliap2 (AirPlay 2) subprocess.

    Audio path:  ffmpeg → decode stream URL → raw PCM s16le 44100Hz stereo
                 → piped to cliraop/cliap2 stdin → RTP to device

    Commands:    written line-by-line to a named FIFO (--cmdpipe / -cmdpipe)
    """

    _LATENCY_MS = 1000

    def __init__(self, info: AirPlayDeviceInfo, pin_callback=None):
        self._info          = info
        self._cli_proc:     Optional[subprocess.Popen] = None
        self._ffmpeg_proc:  Optional[subprocess.Popen] = None
        self._cmd_path      = ''
        self._cmd_fd        = None       # write-end of the command FIFO
        self._volume        = 50
        self._binary: Optional[str]  = None
        self._ffmpeg_bin: Optional[str] = None
        self._vol_timer: Optional[threading.Timer] = None
        self._art_tmp: Optional[str] = None
        self._current_url:      str  = ''
        self._current_track:    dict = {}
        self._current_subsonic       = None
        self._pin_callback           = pin_callback
        self._meta_ready:       bool = False
        self._play_start_wall:  float = 0.0
        self._play_seek_offset: float = 0.0
        self._progress_stop:    bool  = False
        self._art_server: Optional[HTTPServer] = None

    # ── Public ────────────────────────────────────────────────────────────

    def connect(self):
        """Resolve/download binaries eagerly so play_track starts faster."""
        try:
            if not self._binary:
                bin_name = 'cliap2' if self._info.protocol == 'airplay2' else 'cliraop'
                self._binary = get_binary(bin_name)
            if not self._ffmpeg_bin:
                self._ffmpeg_bin = get_ffmpeg()
            if self._info.protocol == 'airplay2' and not self._info.credentials:
                self._info.credentials = load_credentials(self._info.id)
        except Exception as e:
            print(f'[AirPlay] Binary setup failed: {e}')

    def play_track(self, url: str, track: dict, subsonic=None, seek_s: float = 0.0,
                   ntp_start: int = 0):
        self.stop()
        if not self._binary or not self._ffmpeg_bin:
            self.connect()
        if not self._binary or not self._ffmpeg_bin:
            print('[AirPlay] Missing binaries, cannot play')
            return

        self._current_url      = url
        self._current_track    = track
        self._current_subsonic = subsonic
        self._meta_ready       = False
        self._progress_stop    = False
        self._play_seek_offset = seek_s

        ntp            = ntp_start if ntp_start > 0 else _ntp_now()
        self._cmd_path = f'/tmp/sonar-{self._info.protocol}-{self._info.id[-12:]}.cmd'
        _mkfifo(self._cmd_path)

        if self._info.protocol == 'airplay2':
            cli_args = self._cliap2_args(ntp)
        else:
            cli_args = self._cliraop_args(ntp)

        ffmpeg_args = [self._ffmpeg_bin, '-y']
        if seek_s > 0:
            ffmpeg_args += ['-ss', str(seek_s)]
        ffmpeg_args += [
            '-i', url,
            '-f', 's16le', '-ar', '44100', '-ac', '2',
            '-loglevel', 'quiet',
            'pipe:1',
        ]

        print(f'[AirPlay] → {self._info.name} ({self._info.protocol}) '
              f'{self._info.address}:{self._info.port}'
              + (f' seek={seek_s:.1f}s' if seek_s else ''))

        cli_env = _cliap2_env() if self._info.protocol == 'airplay2' else None
        self._cli_proc = subprocess.Popen(
            cli_args, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            env=cli_env,
        )
        self._ffmpeg_proc = subprocess.Popen(
            ffmpeg_args, stdout=self._cli_proc.stdin, stderr=subprocess.DEVNULL,
        )
        self._cli_proc.stdin.close()

        # Open command FIFO then push track metadata after a short delay
        threading.Thread(
            target=self._open_cmd_fifo_and_meta, args=(track, subsonic), daemon=True,
        ).start()

        # Monitor stderr to log status / detect connection
        threading.Thread(target=self._read_stderr, daemon=True).start()

        # Periodically send PROGRESS updates (AirPlay 2 only)
        if self._info.protocol == 'airplay2':
            threading.Thread(target=self._progress_loop, daemon=True).start()

    def pause(self):               self._send('ACTION=PAUSE')
    def resume(self):              self._send('ACTION=PLAY')
    def seek(self, seconds: float):
        # cliap2 does not support seeking — restart ffmpeg from the new position
        if self._current_url:
            threading.Thread(
                target=self.play_track,
                args=(self._current_url, self._current_track),
                kwargs={'subsonic': self._current_subsonic, 'seek_s': seconds},
                daemon=True,
            ).start()

    def set_volume(self, v: float):
        self._volume = int(v * 100)
        if self._vol_timer:
            self._vol_timer.cancel()
        self._vol_timer = threading.Timer(0.15, self._flush_volume)
        self._vol_timer.daemon = True
        self._vol_timer.start()

    def _flush_volume(self):
        self._send(f'VOLUME={self._volume}')

    def get_volume(self) -> int:
        return self._volume

    def stop(self):
        self._send('ACTION=STOP')
        self._cleanup()

    # ── Internal ──────────────────────────────────────────────────────────

    def _cliraop_args(self, ntp: int) -> list:
        d   = self._info
        txt = d.txt_props
        args = [
            self._binary,
            '-ntpstart', str(ntp),
            '-port',     str(d.port),
            '-latency',  str(self._LATENCY_MS),
            '-volume',   str(self._volume),
            '-dacp',     'D0DA7B50EDA7B501',
            '-activeremote', '1234567890',
            '-cmdpipe',  self._cmd_path,
            '-udn',      d.service_name.split('.')[0],
        ]
        for prop in ('et', 'md', 'am', 'pk', 'pw'):
            if val := txt.get(prop, ''):
                args += [f'-{prop}', val]
        if d.credentials:
            args += ['-secret', d.credentials]
        args += [d.address, '-']
        return args

    def _cliap2_args(self, ntp: int) -> list:
        d      = self._info
        txt_kv = ' '.join(f'"{k}={v}"' for k, v in d.txt_props.items())
        args = [
            self._binary,
            '--name',         d.name,
            '--hostname',     d.hostname,
            '--address',      d.address,
            '--port',         str(d.port),
            '--ntpstart',     str(ntp),
            '--volume',       str(self._volume),
            '--loglevel',     '3',
            '--dacp_id',      'D0DA7B50EDA7B501',
            '--pipe',         '-',
            '--command_pipe', self._cmd_path,
            '--latency',      str(self._LATENCY_MS),
        ]
        if txt_kv:
            args += ['--txt', txt_kv]
        if d.credentials and len(d.credentials) == 192:
            args += ['--auth', d.credentials]
        return args

    def _open_cmd_fifo_and_meta(self, track: dict, subsonic=None):
        try:
            self._cmd_fd = open(self._cmd_path, 'w', buffering=1)
        except Exception as e:
            print(f'[AirPlay] cmd FIFO open failed: {e}')
            return
        # _meta_ready is set by _read_stderr when "player: event_play_start()" is seen
        deadline = time.time() + 10.0
        while not self._meta_ready and time.time() < deadline:
            time.sleep(0.1)
        self._play_start_wall = time.time()
        self._send_metadata(track, subsonic)

    @staticmethod
    def _parse_duration_s(track: dict) -> int:
        duration = track.get('duration', 0)
        if isinstance(duration, str) and ':' in duration:
            parts = duration.split(':')
            try: return int(parts[0]) * 60 + int(parts[1])
            except: return 0
        try: return int(float(duration))
        except: return 0

    def _send_metadata(self, track: dict, subsonic=None):
        self._send_metadata_with_progress(track, 0)

        # Artwork — download bytes then serve via a tiny local HTTP server
        # so cliap2 can fetch a plain http://127.0.0.1:PORT/art.jpg URL
        cover_id = track.get('cover_id') or track.get('coverArt')
        if cover_id and subsonic:
            try:
                art_bytes = subsonic.get_cover_art(cover_id, size=500)
                if art_bytes:
                    art_url = self._serve_artwork(art_bytes)
                    self._send(f'ARTWORK={art_url}')
                    print(f'[AirPlay] Artwork served at {art_url}')
            except Exception as e:
                print(f'[AirPlay] Artwork send failed: {e}')

    def _serve_artwork(self, art_bytes: bytes) -> str:
        """Spin up (or reuse) a per-device HTTP server and return the URL for the image."""
        is_png  = art_bytes[:4] == b'\x89PNG'
        mime    = 'image/png' if is_png else 'image/jpeg'
        ext     = '.png'     if is_png else '.jpg'
        data    = art_bytes  # captured in closure

        # Shut down previous server if any
        if self._art_server:
            try:
                self._art_server.shutdown()
            except Exception:
                pass
            self._art_server = None

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass  # silence access logs

        srv = HTTPServer(('127.0.0.1', 0), _Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self._art_server = srv
        return f'http://127.0.0.1:{port}/art{ext}'

    def _send_metadata_with_progress(self, track: dict, progress_s: int):
        """Send SENDMETA batch with the given playback position in seconds."""
        title      = track.get('title')  or track.get('name', '')
        artist     = track.get('artist', '')
        album      = track.get('album',  '')
        duration_s = self._parse_duration_s(track)

        cmd  = f'TITLE={title}\nARTIST={artist}\nALBUM={album}\n'
        cmd += f'DURATION={duration_s}\nPROGRESS={progress_s}\nACTION=SENDMETA\n'
        self._send_raw(cmd)
        if progress_s == 0:
            print(f'[AirPlay] Metadata: {title} / {artist} ({duration_s}s)')

    def _send(self, cmd: str):
        self._send_raw(cmd + '\n')

    def _send_raw(self, data: str):
        fd = self._cmd_fd
        if fd:
            try:
                fd.write(data)
                fd.flush()
            except BrokenPipeError:
                print(f'[AirPlay:{self._info.name}] Command pipe closed by cliap2')
                self._cmd_fd = None
            except Exception as e:
                print(f'[AirPlay:{self._info.name}] Command send error: {e}')
                self._cmd_fd = None

    def _progress_loop(self):
        """Periodically re-send full SENDMETA with updated PROGRESS.

        Re-sending SENDMETA (not just a bare PROGRESS command) keeps the Apple
        TV Now Playing screen refreshed and advances the seek bar correctly.
        """
        # Wait until _open_cmd_fifo_and_meta sets _play_start_wall
        deadline = time.time() + 15.0
        while self._play_start_wall == 0.0 and not self._progress_stop:
            if time.time() > deadline:
                return
            time.sleep(0.2)

        interval = 5   # seconds between refreshes
        while not self._progress_stop and self._cmd_fd is not None:
            time.sleep(interval)
            if self._progress_stop or self._cmd_fd is None:
                break
            elapsed = int(time.time() - self._play_start_wall + self._play_seek_offset)
            self._send_metadata_with_progress(self._current_track, elapsed)

    def _read_stderr(self):
        if not self._cli_proc:
            return
        for raw in self._cli_proc.stderr:
            try:
                line = raw.decode('utf-8', errors='replace').rstrip()
            except Exception:
                continue
            if not line:
                continue
            print(f'[AirPlay:{self._info.name}] {line}')

            # cliap2 is ready to accept metadata / commands
            if 'event_play_start' in line or 'Starting at' in line:
                self._meta_ready = True

            # PIN pairing required
            if 'Starting device pairing' in line and self._pin_callback:
                self._pin_callback(self._info.name, self._submit_pin)

            # New auth key after successful pairing
            if 'new authorization key is' in line:
                try:
                    key = line.split('new authorization key is')[-1].strip()
                    if key:
                        save_credentials(self._info.id, key)
                        self._info.credentials = key
                        print(f'[AirPlay] Saved credentials for {self._info.name}')
                except Exception as e:
                    print(f'[AirPlay] Failed to save credentials: {e}')

    def _submit_pin(self, pin: str):
        self._send(f'PIN={pin.strip()}')

    def _cleanup(self):
        self._progress_stop   = True
        self._play_start_wall = 0.0
        if self._art_server:
            try:
                self._art_server.shutdown()
            except Exception:
                pass
            self._art_server = None
        for p in (self._ffmpeg_proc, self._cli_proc):
            if p:
                try: p.kill()
                except Exception: pass
        self._cli_proc = self._ffmpeg_proc = None
        if self._cmd_fd:
            try: self._cmd_fd.close()
            except Exception: pass
            self._cmd_fd = None
        if self._cmd_path and os.path.exists(self._cmd_path):
            try: os.remove(self._cmd_path)
            except Exception: pass


# ── mDNS discovery ────────────────────────────────────────────────────────

def _mkfifo(path: str):
    if os.path.exists(path):
        try: os.remove(path)
        except Exception: pass
    os.mkfifo(path)


def discover(timeout: float = 5.0) -> list[AirPlayDeviceInfo]:
    """
    Synchronous mDNS scan for AirPlay 1 and AirPlay 2 devices.
    Deduplicates by MAC; prefers AirPlay 2 when a device advertises both.
    Intended to run in a background thread.
    """
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange  # type: ignore
    except ImportError:
        print('[AirPlay] zeroconf not installed — run: pip install zeroconf')
        return []

    seen: dict[str, AirPlayDeviceInfo] = {}   # mac_hex → info
    lock = threading.Lock()

    def _on_change(zeroconf, service_type, name, state_change):
        if state_change != ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if not info:
            return
        try:
            txt: dict[str, str] = {}
            for k, v in info.properties.items():
                key = k.decode('utf-8', errors='replace') if isinstance(k, bytes) else str(k)
                val = v.decode('utf-8', errors='replace') if isinstance(v, bytes) else (v or '')
                txt[key] = val

            addrs = info.parsed_addresses()
            if not addrs:
                return
            address = addrs[0]

            # device MAC → unique key
            mac_raw = txt.get('deviceid', '').replace(':', '').lower()
            if not mac_raw:
                parts = name.split('.')[0].split('@')
                mac_raw = parts[0].replace(':', '').lower() if len(parts) >= 2 else ''
            if not mac_raw:
                mac_raw = hashlib.md5(name.encode()).hexdigest()[:12]

            # display name from '@DeviceName' in service name
            parts = name.split('.')[0].split('@')
            display = parts[1] if len(parts) >= 2 else name.split('.')[0]

            is_ap2   = service_type == AIRPLAY_SERVICE
            protocol = 'airplay2' if is_ap2 else 'airplay1'

            dev = AirPlayDeviceInfo(
                id           = f'ap_{mac_raw}',
                name         = display,
                protocol     = protocol,
                address      = address,
                port         = info.port,
                hostname     = (info.server or address).rstrip('.'),
                service_name = name,
                txt_props    = txt,
            )

            with lock:
                existing = seen.get(mac_raw)
                # upgrade RAOP → AirPlay2 if we see both
                if not existing or (is_ap2 and existing.protocol == 'airplay1'):
                    seen[mac_raw] = dev
                    print(f'[AirPlay] Found {protocol}: {display} @ {address}:{info.port}')

        except Exception as e:
            print(f'[AirPlay] Discovery parse error ({name}): {e}')

    zc = Zeroconf()
    browsers = [
        ServiceBrowser(zc, RAOP_SERVICE,    handlers=[_on_change]),
        ServiceBrowser(zc, AIRPLAY_SERVICE,  handlers=[_on_change]),
    ]
    time.sleep(timeout)
    zc.close()
    return list(seen.values())
