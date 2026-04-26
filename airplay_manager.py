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

_CREDS_DIR = os.path.join(os.path.expanduser('~'), '.config', 'sonar', 'credentials')


def _creds_path(device_id: str) -> str:
    os.makedirs(_CREDS_DIR, exist_ok=True)
    safe_id = device_id.replace(':', '').replace('/', '_').replace('\\', '_')
    return os.path.join(_CREDS_DIR, f'{safe_id}.txt')


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

    def __init__(self, info: AirPlayDeviceInfo, pin_callback=None, error_callback=None):
        self._info           = info
        self._pin_callback   = pin_callback
        self._error_callback = error_callback
        self._atv            = None   # pyatv AppleTV object
        self._volume         = 50
        self._stream_future  = None   # currently running stream_file future
        self._dev_id         = None
        self._bridge         = None

    # ── Public ────────────────────────────────────────────────────────────

    def connect(self):
        _run(self._async_connect()).result(timeout=15)

    def register_listeners(self, dev_id: str, bridge):
        self._dev_id = dev_id
        self._bridge = bridge
        if self._atv:
            self._start_push_updater()

    def play_track(self, url: str, track: dict, subsonic=None,
                   seek_s: float = 0.0, ntp_start: int = 0, **_kw):
        # stream_file() runs for the full duration of the song — never block here.
        # Cancel any previous stream so we don't pile up concurrent streams.
        if self._stream_future and not self._stream_future.done():
            self._stream_future.cancel()
        self._stream_future = _run(self._async_play(url, track, seek_s))
        self._stream_future.add_done_callback(self._on_stream_done)

    def _on_stream_done(self, fut):
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc:
            import traceback
            tb = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            print(f'[AirPlay] Stream error for {self._info.name!r}:\n{tb}')
            if self._error_callback:
                self._error_callback(self._info.name, str(exc))

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
        if self._stream_future and not self._stream_future.done():
            self._stream_future.cancel()
        self._stream_future = None
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

    def _start_push_updater(self):
        from pyatv.const import DeviceState
        dev_id = self._dev_id
        bridge = self._bridge
        atv    = self._atv

        class _Listener:
            def playstatus_update(self, updater, playstatus):
                state_map = {
                    DeviceState.Playing: 'playing',
                    DeviceState.Paused:  'paused',
                }
                state = state_map.get(playstatus.device_state)
                if state:
                    bridge.dlna_playstate.emit(dev_id, state)
            def playstatus_error(self, updater, exception):
                print(f'[AirPlay] push update error: {exception}')

        async def _start():
            atv.push_updater.listener = _Listener()
            atv.push_updater.start()

        _run(_start())

    async def _async_connect(self):
        import pyatv
        from pyatv.const import Protocol
        from pyatv import exceptions as _exc

        config = self._info._pyatv_config
        if config is None:
            raise RuntimeError(f'No pyatv config for {self._info.name!r}')

        # stream_file() always routes through the RAOP service internally (even on
        # AirPlay 2), so credentials must be on the RAOP service or verify_connection
        # falls back to hap_transient which Apple TV rejects with 470.
        proto = Protocol.RAOP
        loop  = asyncio.get_event_loop()

        # Apply stored credentials to the RAOP service
        creds = self._info.credentials or load_credentials(self._info.id)
        if creds:
            svc = config.get_service(Protocol.RAOP)
            if svc:
                try:
                    svc.credentials = creds
                except Exception:
                    pass

        # Always try a direct connect first — many devices (Mac, open speakers)
        # work without pairing; only fall through to pairing on an explicit auth error.
        try:
            self._atv = await pyatv.connect(config, loop)
            print(f'[AirPlay] Connected to {self._info.name!r} ({self._info.protocol})')
            return
        except _exc.AuthenticationError as e:
            print(f'[AirPlay] Auth required for {self._info.name!r}: {e}')
            # Stale saved credentials may have triggered this — clear them so pairing
            # starts fresh rather than re-presenting bad creds to the device.
            if creds:
                print(f'[AirPlay] Clearing stale credentials for {self._info.name!r}')
                save_credentials(self._info.id, '')
                self._info.credentials = ''
                svc = config.get_service(proto)
                if svc:
                    try:
                        svc.credentials = None
                    except Exception:
                        pass
        except _exc.PairingError as e:
            print(f'[AirPlay] Pairing error connecting to {self._info.name!r}: {e}')
            raise

        # Connect failed with AuthenticationError — attempt pairing then retry
        try:
            await self._do_pairing(config, proto, loop)
        except _exc.PairingError as e:
            # Some devices (macOS AirPlay target) don't support HAP pairing via
            # pyatv; surface a clear message rather than a cryptic traceback.
            raise RuntimeError(
                f'{self._info.name!r} rejected pairing ({e}). '
                'This device may not support pyatv pairing — '
                'try an Apple TV or AirPlay-certified speaker instead.'
            ) from e
        except _exc.AuthenticationError as e:
            # HTTP 470 / auth failure during pairing handshake (e.g. wrong PIN or
            # device rejected the HAP exchange).
            raise RuntimeError(
                f'Could not pair with {self._info.name!r}: the device rejected the '
                f'authorization request ({e}).\n\n'
                'Please check that the PIN was entered correctly and try again.'
            ) from e

        try:
            self._atv = await pyatv.connect(config, loop)
            print(f'[AirPlay] Connected to {self._info.name!r} after pairing')
        except (_exc.AuthenticationError, _exc.PairingError) as e:
            # Credentials from pairing didn't take — wipe them so the next attempt
            # re-pairs from scratch rather than looping on a bad credential.
            save_credentials(self._info.id, '')
            self._info.credentials = ''
            raise RuntimeError(
                f'Paired with {self._info.name!r} but the connection still requires '
                f'authorization ({e}).\n\nPlease try connecting again.'
            ) from e

    async def _do_pairing(self, config, protocol, loop):
        """Run the pyatv pairing handshake, prompting for PIN via pin_callback."""
        import pyatv

        print(f'[AirPlay] Starting pairing with {self._info.name!r} …')
        pairing = await pyatv.pair(config, protocol, loop)
        await pairing.begin()

        try:
            if pairing.device_provides_pin:
                # Device shows PIN on screen — ask the user to type it in
                pin_ready  = asyncio.Event()
                pin_holder = []

                def submit_fn(pin_str: str):
                    pin_holder.append(pin_str.strip())
                    loop.call_soon_threadsafe(pin_ready.set)

                if self._pin_callback:
                    self._pin_callback(self._info.name, submit_fn)
                else:
                    raise RuntimeError('PIN required but no pin_callback registered')

                await asyncio.wait_for(pin_ready.wait(), timeout=120.0)
                pairing.pin(int(pin_holder[0]))
            else:
                # App-side PIN — pyatv picks one; we'd need to show it to the user.
                # Most AirPlay 2 devices use device_provides_pin=True, so this is rare.
                pairing.pin(1234)
                print('[AirPlay] Using default PIN 1234 (app-side pairing)')

            await pairing.finish()
        finally:
            await pairing.close()

        # Save credentials so we don't pair again
        svc = config.get_service(protocol)
        if svc and svc.credentials:
            save_credentials(self._info.id, svc.credentials)
            self._info.credentials = svc.credentials
            print(f'[AirPlay] Saved credentials for {self._info.name!r}')

    async def _async_play(self, url: str, track: dict, seek_s: float = 0.0):
        if not self._atv:
            await self._async_connect()

        import pyatv.interface as _iface
        import aiohttp

        artwork = None
        cover_url = track.get('cover_url') or ''
        if cover_url.startswith('http'):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(cover_url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            artwork = await r.read()
            except Exception as e:
                print(f'[AirPlay] artwork fetch failed: {e}')

        def _to_seconds(val):
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).strip()
            if ':' in s:
                try:
                    parts = s.split(':')
                    if len(parts) == 2:
                        return float(parts[0]) * 60 + float(parts[1])
                    if len(parts) == 3:
                        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                except ValueError:
                    return None
            try:
                return float(s)
            except ValueError:
                return None

        duration = _to_seconds(track.get('duration') or track.get('total_time'))
        metadata = _iface.MediaMetadata(
            title=track.get('title') or None,
            artist=track.get('artist') or None,
            album=track.get('album') or None,
            artwork=artwork,
            duration=duration,
        )

        # miniaudio's MP3 decoder needs to seek during init (to skip ID3 tags and
        # find MPEG sync). Streaming HTTP responses aren't fully seekable, so large
        # ID3 blocks (embedded artwork > 32KB) cause DecodeError. Download to a
        # temp file first for formats other than FLAC/WAV which are fine via URL.
        import os as _os, tempfile as _tmp
        suffix = (track.get('suffix') or '').lower() or \
                 url.rsplit('.', 1)[-1].split('?')[0].split('&')[0].lower()
        tmp_path = None
        play_src = url
        if suffix not in ('flac', 'wav'):
            try:
                fd, tmp_path = _tmp.mkstemp(suffix=f'.{suffix or "mp3"}')
                _os.close(fd)
                print(f'[AirPlay] Downloading {suffix} to temp file for seekable decode…')
                async with aiohttp.ClientSession() as _s:
                    async with _s.get(url, timeout=aiohttp.ClientTimeout(total=300)) as _r:
                        with open(tmp_path, 'wb') as _f:
                            async for _chunk in _r.content.iter_chunked(65536):
                                _f.write(_chunk)
                play_src = tmp_path
                print(f'[AirPlay] Downloaded {_os.path.getsize(tmp_path)//1024}KB')
            except Exception as _e:
                print(f'[AirPlay] Temp download failed ({_e}), trying direct URL')
                play_src = url
                if tmp_path and _os.path.exists(tmp_path):
                    _os.unlink(tmp_path)
                tmp_path = None

        print(f'[AirPlay] → {self._info.name!r}  {url!r}')
        try:
            await self._atv.stream.stream_file(play_src, metadata=metadata)
        except Exception as e:
            e_str = str(e).lower()
            if not ('auth' in e_str or '470' in e_str or 'credentials' in e_str or 'verify' in e_str):
                raise

            # stream_file auth failure — RAOP service needs credentials.
            # Always pair with Protocol.RAOP so the credentials land on the right service.
            import pyatv
            from pyatv.const import Protocol
            from pyatv import exceptions as _exc

            proto = Protocol.RAOP
            loop  = asyncio.get_event_loop()
            config = self._info._pyatv_config

            try:
                print(f'[AirPlay] Stream auth failed — attempting pairing with {self._info.name!r} …')
                await self._do_pairing(config, proto, loop)
            except _exc.PairingError as pe:
                raise RuntimeError(
                    f'Cannot stream to {self._info.name!r}: pairing rejected.\n\n'
                    'macOS AirPlay receivers require MFi hardware authentication '
                    'that pyatv cannot provide.\n'
                    'Please use an Apple TV, HomePod, or AirPlay-certified speaker.'
                ) from pe
            except _exc.AuthenticationError as ae:
                raise RuntimeError(
                    f'Could not pair with {self._info.name!r}: authorization failed ({ae}).\n\n'
                    'Please check that the PIN was entered correctly and try again.'
                ) from ae

            # Reconnect with freshly stored credentials then retry
            try:
                await self._atv.close()
            except Exception:
                pass
            try:
                self._atv = await pyatv.connect(config, loop)
            except (_exc.AuthenticationError, _exc.PairingError) as ce:
                save_credentials(self._info.id, '')
                self._info.credentials = ''
                raise RuntimeError(
                    f'Paired with {self._info.name!r} but reconnect failed ({ce}).\n\n'
                    'Please try connecting again.'
                ) from ce
            await self._atv.stream.stream_file(play_src, metadata=metadata)
        finally:
            if tmp_path and _os.path.exists(tmp_path):
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass

        if seek_s > 1.0:
            await asyncio.sleep(2.0)
            try:
                await self._atv.remote_control.set_position(int(seek_s))
            except Exception:
                pass  # set_position not supported on all AirPlay devices



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

    _MAC_PREFIXES = ('MacBook', 'iMac', 'MacPro', 'MacMini', 'Mac Pro', 'Mac Mini')

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

            # Skip macOS AirPlay receivers — they require MFi hardware auth
            model = svc.properties.get('model', '') or config.device_info.model or ''
            if any(model.startswith(p) for p in _MAC_PREFIXES):
                print(f'[AirPlay] Skipping {config.name!r} (macOS receiver, model={model!r})')
                continue

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
