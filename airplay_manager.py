"""
airplay_manager.py — AirPlay 1 + AirPlay 2 via pyatv (cross-platform).

Replaces the old cliraop/cliap2 binary approach with pure-Python pyatv,
which works on Linux and Windows without any bundled native binaries.

Requires: pip install pyatv
"""
from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

# ── Credentials storage ───────────────────────────────────────────────────

_CREDS_DIR = os.path.expanduser('~/.config/sonar/credentials')


def _creds_path(device_id: str) -> str:
    os.makedirs(_CREDS_DIR, exist_ok=True)
    return os.path.join(_CREDS_DIR, f'{device_id}.txt')


def load_credentials(device_id: str) -> str:
    try:
        with open(_creds_path(device_id)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''


def save_credentials(device_id: str, creds: str):
    with open(_creds_path(device_id), 'w') as f:
        f.write(creds.strip())


# ── Shared asyncio event loop (background thread) ─────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or not _loop.is_running():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True)
            t.start()
    return _loop


def _run(coro):
    """Submit a coroutine to the background loop and return a Future."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop())


# ── Device info ───────────────────────────────────────────────────────────

@dataclass
class AirPlayDeviceInfo:
    id:            str
    name:          str
    protocol:      str             # 'airplay1' | 'airplay2'
    address:       str
    port:          int
    hostname:      str
    service_name:  str
    txt_props:     dict = field(default_factory=dict)
    credentials:   str  = ''
    _pyatv_config: object = field(default=None, repr=False)


# ── Device wrapper ────────────────────────────────────────────────────────

class AirPlayDevice:
    """
    Wraps a pyatv connection to an AirPlay 1 or AirPlay 2 device.
    Public API matches what cast_manager expects.
    """

    def __init__(self, info: AirPlayDeviceInfo, pin_callback=None):
        self._info         = info
        self._pin_callback = pin_callback
        self._atv          = None   # pyatv AppleTV object
        self._volume       = 50

    # ── Public ────────────────────────────────────────────────────────────

    def connect(self):
        _run(self._async_connect()).result(timeout=15)

    def play_track(self, url: str, track: dict, subsonic=None,
                   seek_s: float = 0.0, ntp_start: int = 0, **_kw):
        _run(self._async_play(url, seek_s)).result(timeout=30)

    def pause(self):
        if self._atv:
            _run(self._atv.remote_control.pause())

    def resume(self):
        if self._atv:
            _run(self._atv.remote_control.play())

    def seek(self, seconds: float):
        if self._atv:
            _run(self._atv.remote_control.set_position(int(seconds)))

    def set_volume(self, v: float):
        self._volume = int(v * 100)
        if self._atv:
            _run(self._atv.audio.set_volume(self._volume))

    def get_volume(self) -> int:
        if self._atv:
            try:
                vol = self._atv.audio.volume
                if vol is not None:
                    self._volume = int(vol)
            except Exception:
                pass
        return self._volume

    def stop(self):
        if self._atv:
            try:
                _run(self._atv.remote_control.stop()).result(timeout=5)
            except Exception:
                pass
            try:
                _run(self._atv.close()).result(timeout=5)
            except Exception:
                pass
            self._atv = None

    # ── Internal ──────────────────────────────────────────────────────────

    async def _async_connect(self):
        import pyatv
        from pyatv.const import Protocol

        config = self._info._pyatv_config
        if config is None:
            raise RuntimeError(f'No pyatv config for {self._info.name!r}')

        # Apply stored credentials so the device doesn't ask to pair again
        creds = self._info.credentials or load_credentials(self._info.id)
        if creds:
            for proto in (Protocol.AirPlay, Protocol.RAOP):
                svc = config.get_service(proto)
                if svc:
                    try:
                        svc.credentials = creds
                    except Exception:
                        pass

        self._atv = await pyatv.connect(config, asyncio.get_event_loop())
        print(f'[AirPlay] Connected to {self._info.name!r} ({self._info.protocol})')

    async def _async_play(self, url: str, seek_s: float = 0.0):
        if not self._atv:
            await self._async_connect()

        from pyatv.const import MediaType
        print(f'[AirPlay] → {self._info.name!r}  {url!r}')
        await self._atv.stream.stream_url(url, mediatype=MediaType.Music)

        if seek_s > 1.0:
            await asyncio.sleep(2.0)
            try:
                await self._atv.remote_control.set_position(int(seek_s))
            except Exception as e:
                print(f'[AirPlay] Seek failed (ignored): {e}')


# ── mDNS discovery ────────────────────────────────────────────────────────

def discover(timeout: float = 5.0) -> list[AirPlayDeviceInfo]:
    """
    Scan the local network for AirPlay devices using pyatv.
    Returns AirPlayDeviceInfo list; prefers AirPlay 2 when a device
    advertises both protocols.
    """
    try:
        import pyatv
        from pyatv.const import Protocol
    except ImportError:
        print('[AirPlay] pyatv not installed — run: pip install pyatv')
        return []

    async def _scan():
        loop = asyncio.get_event_loop()
        # Scan for AirPlay and RAOP separately then merge by identifier
        results = await pyatv.scan(loop, timeout=timeout)
        return results

    try:
        configs = _run(_scan()).result(timeout=timeout + 5)
    except Exception as e:
        print(f'[AirPlay] Discovery error: {e}')
        return []

    seen: dict[str, AirPlayDeviceInfo] = {}  # identifier → info

    for config in configs:
        try:
            from pyatv.const import Protocol
            has_ap2  = config.get_service(Protocol.AirPlay) is not None
            has_raop = config.get_service(Protocol.RAOP)    is not None
            if not has_ap2 and not has_raop:
                continue

            svc      = config.get_service(Protocol.AirPlay) or config.get_service(Protocol.RAOP)
            protocol = 'airplay2' if has_ap2 else 'airplay1'

            dev = AirPlayDeviceInfo(
                id            = f'ap_{config.identifier}',
                name          = config.name,
                protocol      = protocol,
                address       = str(config.address),
                port          = svc.port,
                hostname      = str(config.address),
                service_name  = config.name,
                _pyatv_config = config,
            )

            existing = seen.get(config.identifier)
            # Upgrade airplay1 → airplay2 if the same device advertises both
            if not existing or (has_ap2 and existing.protocol == 'airplay1'):
                seen[config.identifier] = dev
                print(f'[AirPlay] Found {protocol}: {config.name} @ {config.address}:{svc.port}')

        except Exception as e:
            print(f'[AirPlay] Parse error for {config.name}: {e}')

    return list(seen.values())
