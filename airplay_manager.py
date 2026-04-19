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
        # pin_callback(device_name, submit_fn) called on main thread when PIN needed
        self._pin_callback  = pin_callback

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
    def seek(self, seconds: float): self._send(f'PROGRESS={int(seconds)}')

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
            if not line:
                continue
            print(f'[AirPlay:{self._info.name}] {line}')

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
