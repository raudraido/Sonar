"""
airplay_manager.py — AirPlay 1 (RAOP via cliraop) + AirPlay 2 (cliap2) support.

Binary sources: music-assistant/server (github.com/music-assistant/server)
PCM audio is decoded by ffmpeg and piped to the binary stdin.
Commands (pause/resume/seek/volume) go through a named FIFO pipe.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import socket
import stat
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# ── Platform detection ────────────────────────────────────────────────────

_SYSTEM  = platform.system().lower().replace('darwin', 'macos')
_MACHINE = platform.machine().lower()
if _MACHINE in ('x86_64', 'amd64'):    _MACHINE = 'x86_64'
elif _MACHINE in ('aarch64', 'arm64'): _MACHINE = 'aarch64'

_BIN_DIR  = os.path.expanduser('~/.config/sonar/bin')
_LIB_DIR  = os.path.expanduser('~/.config/sonar/lib')
_FF5_DIR  = os.path.expanduser('~/.config/sonar/ffmpeg5/lib')

def _cliap2_env() -> dict:
    """Environment with FFmpeg 5.x libs prepended so cliap2 resolves its dependencies."""
    env = os.environ.copy()
    extra = f'{_FF5_DIR}:{_LIB_DIR}'
    existing = env.get('LD_LIBRARY_PATH', '')
    env['LD_LIBRARY_PATH'] = f'{extra}:{existing}' if existing else extra
    return env

_MA_BASE = ('https://raw.githubusercontent.com/music-assistant/server'
            '/main/music_assistant/providers/airplay/bin')

_BIN_URLS: dict[str, str] = {
    'cliraop': f'{_MA_BASE}/cliraop-{_SYSTEM}-{_MACHINE}',
    'cliap2':  f'{_MA_BASE}/cliap2-{_SYSTEM}-{_MACHINE}',
}

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


# ── Binary / ffmpeg management ────────────────────────────────────────────

def get_binary(name: str) -> str:
    """Return path to cliraop or cliap2, downloading from music-assistant if needed."""
    os.makedirs(_BIN_DIR, exist_ok=True)
    path = os.path.join(_BIN_DIR, name)
    if not os.path.isfile(path):
        url = _BIN_URLS.get(name)
        if not url:
            raise RuntimeError(f'No download URL for {name} on {_SYSTEM}/{_MACHINE}')
        print(f'[AirPlay] Downloading {name} …')
        urllib.request.urlretrieve(url, path)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f'[AirPlay] {name} ready → {path}')
    return path


def get_ffmpeg() -> str:
    """Return path to ffmpeg, auto-installing via imageio-ffmpeg if needed."""
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    try:
        import imageio_ffmpeg          # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    import sys
    print('[AirPlay] Installing imageio-ffmpeg (bundles static ffmpeg) …')
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', 'imageio-ffmpeg'],
        check=True, capture_output=True,
    )
    import imageio_ffmpeg              # type: ignore
    return imageio_ffmpeg.get_ffmpeg_exe()


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

    def __init__(self, info: AirPlayDeviceInfo):
        self._info          = info
        self._cli_proc:     Optional[subprocess.Popen] = None
        self._ffmpeg_proc:  Optional[subprocess.Popen] = None
        self._cmd_path      = ''
        self._cmd_fd        = None       # write-end of the command FIFO
        self._volume        = 50
        self._binary: Optional[str]  = None
        self._ffmpeg_bin: Optional[str] = None

    # ── Public ────────────────────────────────────────────────────────────

    def connect(self):
        """Resolve/download binaries eagerly so play_track starts faster."""
        try:
            if not self._binary:
                bin_name = 'cliap2' if self._info.protocol == 'airplay2' else 'cliraop'
                self._binary = get_binary(bin_name)
            if not self._ffmpeg_bin:
                self._ffmpeg_bin = get_ffmpeg()
        except Exception as e:
            print(f'[AirPlay] Binary setup failed: {e}')

    def play_track(self, url: str, track: dict):
        self.stop()
        if not self._binary or not self._ffmpeg_bin:
            self.connect()
        if not self._binary or not self._ffmpeg_bin:
            print('[AirPlay] Missing binaries, cannot play')
            return

        ntp            = _ntp_now()
        self._cmd_path = f'/tmp/sonar-{self._info.protocol}-{self._info.id[-12:]}.cmd'
        _mkfifo(self._cmd_path)

        if self._info.protocol == 'airplay2':
            cli_args = self._cliap2_args(ntp)
        else:
            cli_args = self._cliraop_args(ntp)

        ffmpeg_args = [
            self._ffmpeg_bin, '-y',
            '-i', url,
            '-f', 's16le', '-ar', '44100', '-ac', '2',
            '-loglevel', 'quiet',
            'pipe:1',
        ]

        print(f'[AirPlay] → {self._info.name} ({self._info.protocol}) '
              f'{self._info.address}:{self._info.port}')

        cli_env = _cliap2_env() if self._info.protocol == 'airplay2' else None
        self._cli_proc = subprocess.Popen(
            cli_args, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            env=cli_env,
        )
        self._ffmpeg_proc = subprocess.Popen(
            ffmpeg_args, stdout=self._cli_proc.stdin, stderr=subprocess.DEVNULL,
        )
        # parent process closes its copy of the write-end so the pipe EOF
        # propagates correctly when ffmpeg exits
        self._cli_proc.stdin.close()

        # Open command FIFO for writing in a background thread; blocks until
        # the binary opens it for reading (happens ~immediately after start)
        threading.Thread(target=self._open_cmd_fifo, daemon=True).start()

        # Monitor stderr to log status / detect connection
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def pause(self):               self._send('ACTION=PAUSE')
    def resume(self):              self._send('ACTION=PLAY')
    def seek(self, seconds: float): self._send(f'SEEK={int(seconds)}')

    def set_volume(self, v: float):
        self._volume = int(v * 100)
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

    def _open_cmd_fifo(self):
        try:
            self._cmd_fd = open(self._cmd_path, 'w', buffering=1)
        except Exception as e:
            print(f'[AirPlay] cmd FIFO open failed: {e}')

    def _send(self, cmd: str):
        fd = self._cmd_fd
        if fd:
            try:
                fd.write(cmd + '\n')
                fd.flush()
            except Exception:
                pass

    def _read_stderr(self):
        if not self._cli_proc:
            return
        for raw in self._cli_proc.stderr:
            try:
                line = raw.decode('utf-8', errors='replace').rstrip()
            except Exception:
                continue
            if line:
                print(f'[AirPlay:{self._info.name}] {line}')

    def _cleanup(self):
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
