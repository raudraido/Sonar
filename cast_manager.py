"""
cast_manager.py — Multi-protocol cast manager (Chromecast + DLNA/UPnP).

Click the cast button → popup appears above it listing all discovered devices.
Selecting a device streams the current track's Navidrome URL directly to it.
Play/pause/seek/track-change events are relayed automatically.
"""

import asyncio
import hashlib
import html
import os
import re
import shutil
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
import urllib.request
import urllib.error
import ssl

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QSizePolicy, QSlider,
)
from PyQt6.QtGui import QColor, QPixmap, QPainter

# ── Optional protocol libraries ───────────────────────────────────────────

try:
    import pychromecast
    from pychromecast.discovery import CastBrowser, SimpleCastListener
    _HAVE_CC = True

    # Monkey-patch older versions of pychromecast to fix a known mDNS bug
    # that crashes discovery with: "cannot access local variable 'host'"
    import pychromecast.discovery
    if not getattr(pychromecast.discovery, '_patched_for_host_bug', False):
        _orig_get_info = getattr(pychromecast.discovery, 'get_info_from_service', None)
        if _orig_get_info:
            def _safe_get_info(*args, **kwargs):
                try:
                    return _orig_get_info(*args, **kwargs)
                except UnboundLocalError as e:
                    if 'host' in str(e): return None
                    raise
            pychromecast.discovery.get_info_from_service = _safe_get_info
            pychromecast.discovery._patched_for_host_bug = True
except ImportError:
    _HAVE_CC = False

try:
    import aiohttp
    from async_upnp_client.search import async_search
    _HAVE_DLNA = True
except ImportError:
    _HAVE_DLNA = False

try:
    import pyatv as _pyatv_mod   # noqa: F401 — just check presence
    from airplay_manager import AirPlayDevice, AirPlayDeviceInfo, discover as _ap_discover
    _HAVE_AP = True
except ImportError:
    _HAVE_AP = False


# ── Dedicated asyncio loop (for DLNA async operations) ────────────────────

_cast_loop: Optional[asyncio.AbstractEventLoop] = None
_cast_loop_lock = threading.Lock()

def _get_loop() -> asyncio.AbstractEventLoop:
    global _cast_loop
    with _cast_loop_lock:
        if _cast_loop is None or not _cast_loop.is_running():
            _cast_loop = asyncio.new_event_loop()
            t = threading.Thread(target=_cast_loop.run_forever, daemon=True)
            t.daemon = True
            t.start()
        return _cast_loop

def _run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop())


# ── Helpers ───────────────────────────────────────────────────────────────

def _content_type(track: dict) -> str:
    suffix = (track.get('suffix') or '').lower()
    if not suffix:
        path = track.get('path', '') or track.get('stream_url', '')
        suffix = path.rsplit('.', 1)[-1].split('?')[0].lower()
    return {
        'flac': 'audio/flac',
        'mp3':  'audio/mpeg',
        'ogg':  'audio/ogg',
        'opus': 'audio/ogg',
        'aac':  'audio/aac',
        'm4a':  'audio/mp4',
        'wav':  'audio/wav',
    }.get(suffix, 'audio/mpeg')

_DLNA_PN = {
    'audio/mpeg': 'DLNA.ORG_PN=MP3;',
    'audio/mp4':  'DLNA.ORG_PN=AAC_ISO;',
    'audio/aac':  'DLNA.ORG_PN=AAC_ISO;',
}
# Streaming transfer mode + background transfer + connection stall + DLNA v1.5
_DLNA_FLAGS = '01700000000000000000000000000000'

def _protocol_info(ct: str) -> str:
    pn = _DLNA_PN.get(ct, '')
    return f'http-get:*:{ct}:{pn}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={_DLNA_FLAGS}'

def _didl(url: str, title: str, artist: str, album: str, ct: str, art_url: str = '') -> str:
    def e(s): return html.escape(str(s or ''))
    print(f'[DLNA didl] art_url={art_url!r}')
    art_tag = f'<upnp:albumArtURI>{e(art_url)}</upnp:albumArtURI>' if art_url else ''
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="0">'
        f'<dc:title>{e(title)}</dc:title>'
        f'<dc:creator>{e(artist)}</dc:creator>'
        f'<upnp:artist>{e(artist)}</upnp:artist>'
        f'<upnp:album>{e(album)}</upnp:album>'
        f'{art_tag}'
        f'<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
        f'<res protocolInfo="{_protocol_info(ct)}">{e(url)}</res>'
        f'</item></DIDL-Lite>'
    )


# ── Device data ───────────────────────────────────────────────────────────

@dataclass
class DeviceInfo:
    id:            str
    name:          str
    protocol:      str          # 'chromecast' | 'dlna' | 'airplay1' | 'airplay2'
    location:      str = ''     # DLNA description URL
    avt_url:       str = ''     # cached AVTransport control URL
    rc_url:        str = ''     # cached RenderingControl control URL
    avt_event_url: str = ''     # GENA event subscription URL for AVTransport
    rc_event_url:  str = ''     # GENA event subscription URL for RenderingControl
    _cc:           object = field(default=None, repr=False)
    _browser:      object = field(default=None, repr=False)
    _ap:           object = field(default=None, repr=False)


# ── Chromecast wrapper ────────────────────────────────────────────────────

class _ChromecastDevice:
    def __init__(self, cc):
        self._cc = cc

    def connect(self):
        # Browser lifecycle is managed by CastManager — do NOT stop it here.
        # All devices from one scan share the same browser; stopping early
        # kills cc.wait() for every other device in the batch.
        self._cc.wait(timeout=8)

    def register_listeners(self, dev_id: str, bridge):
        cc = self._cc
        mc = cc.media_controller

        class _MediaListener:
            def new_media_status(self, status):
                state_map = {'PLAYING': 'playing', 'PAUSED': 'paused'}
                state = state_map.get(status.player_state)
                if state:
                    bridge.dlna_playstate.emit(dev_id, state)

        class _CastListener:
            def new_cast_status(self, status):
                if status.volume_level is not None:
                    bridge.device_volume.emit(dev_id, int(status.volume_level * 100))

        mc.register_status_listener(_MediaListener())
        cc.register_status_listener(_CastListener())

    def play_track(self, url: str, track: dict, seek_s: float = 0.0, **_kw):
        ct = _content_type(track)
        mc = self._cc.media_controller
        thumb = track.get('cover_url') or ''
        print(f'[CC] play_media url={url[:80]}  ct={ct}  seek={seek_s}')
        print(f'[CC] thumb={thumb[:80] if thumb else None}')
        print(f'[CC] cc.status={self._cc.status}')
        try:
            mc.play_media(
                url, ct,
                title=track.get('title', ''),
                thumb=thumb if thumb.startswith('http') else None,
                current_time=seek_s,
                autoplay=True,
                stream_type="BUFFERED",
                metadata={
                    'metadataType': 3,   # MUSIC_TRACK
                    'albumName': track.get('album', ''),
                    'artist':    track.get('artist', ''),
                },
            )
            mc.block_until_active(timeout=15)
        except Exception as e:
            print(f'[CC] play_track error: {e}')

    def get_volume(self) -> int:
        try:
            level = self._cc.status.volume_level
            return int(level * 100) if level is not None else 50
        except Exception:
            return 50

    def pause(self):
        try: self._cc.media_controller.pause()
        except Exception as e: print(f'[Cast] pause error: {e}')
    def resume(self):
        try: self._cc.media_controller.play()
        except Exception as e: print(f'[Cast] resume error: {e}')
    def seek(self, s):
        try: self._cc.media_controller.seek(s)
        except Exception as e: print(f'[Cast] seek error: {e}')
    def stop(self):
        try: self._cc.quit_app()
        except Exception: pass
    def set_volume(self, v: float):
        try: self._cc.set_volume(max(0.0, min(1.0, v)))
        except Exception as e: print(f'[Cast] set_volume error: {e}')


# ── DLNA wrapper (raw SOAP — works with nested sub-device layouts) ────────

class _DLNADevice:
    _AVT_TYPE = 'urn:schemas-upnp-org:service:AVTransport:1'
    _RC_TYPE  = 'urn:schemas-upnp-org:service:RenderingControl:1'

    def __init__(self, location: str, avt_url: str = '', rc_url: str = '',
                 avt_event_url: str = '', rc_event_url: str = ''):
        self._location      = location
        self._avt_url       = avt_url       or None
        self._rc_url        = rc_url        or None
        self._avt_event_url = avt_event_url or None
        self._rc_event_url  = rc_event_url  or None
        self._avt_sid       = None
        self._rc_sid        = None
        self._renewal_task  = None
        self._on_event      = None   # callable(type: str, value) set by CastManager
        self._event_path    = None   # proxy path registered for this device

    async def _ensure(self):
        if self._avt_url:
            return
        from urllib.parse import urlparse, urljoin
        last_err = None
        # Retry for up to ~25 s — receiver may need time to boot from standby
        delays = [2, 3, 4, 5, 6, 5]   # 6 attempts, total ≤ 25 s of waiting
        for attempt, wait in enumerate([0] + delays):
            if wait:
                print(f'[DLNA] Device not ready, retrying in {wait}s… (attempt {attempt+1})')
                await asyncio.sleep(wait)
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        self._location, timeout=aiohttp.ClientTimeout(total=6)
                    ) as r:
                        xml = await r.text()

                parsed   = urlparse(self._location)
                base_url = f'{parsed.scheme}://{parsed.netloc}'

                def _svc_urls(svc_prefix):
                    for blk in re.findall(r'<service>.*?</service>', xml, re.DOTALL):
                        if not re.search(
                            rf'<serviceType>{re.escape(svc_prefix)}:\d+</serviceType>', blk
                        ):
                            continue
                        cm = re.search(r'<controlURL>([^<]+)</controlURL>',   blk)
                        em = re.search(r'<eventSubURL>([^<]+)</eventSubURL>', blk)
                        ctrl  = urljoin(base_url, cm.group(1).strip()) if cm else None
                        event = urljoin(base_url, em.group(1).strip()) if em else None
                        return ctrl, event
                    return None, None

                avt_ctrl, avt_event = _svc_urls('urn:schemas-upnp-org:service:AVTransport')
                rc_ctrl,  rc_event  = _svc_urls('urn:schemas-upnp-org:service:RenderingControl')
                self._avt_url       = avt_ctrl
                self._rc_url        = rc_ctrl
                if not self._avt_event_url and avt_event:
                    self._avt_event_url = avt_event
                if not self._rc_event_url and rc_event:
                    self._rc_event_url = rc_event
                if self._avt_url:
                    print(f'[DLNA] AVT={self._avt_url}  RC={self._rc_url}')
                    return
                last_err = RuntimeError('AVTransport service not found in device description')
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError('DLNA device did not respond')

    async def _soap(self, ctrl_url: str, service_type: str, action: str, **kwargs):
        """Send a SOAP action; all kwarg values are XML-escaped automatically."""
        args = ''.join(
            f'<{k}>{html.escape(str(v))}</{k}>' for k, v in kwargs.items()
        )
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            '<s:Body>'
            f'<u:{action} xmlns:u="{service_type}">{args}</u:{action}>'
            '</s:Body></s:Envelope>'
        )
        headers = {
            'Content-Type': 'text/xml; charset="utf-8"',
            'SOAPAction':   f'"{service_type}#{action}"',
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                ctrl_url, data=body.encode('utf-8'), headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                text = await r.text()
                if r.status >= 400:
                    raise Exception(f'SOAP {action} → HTTP {r.status}: {text[:300]}')
                return text

    async def _avt(self, action: str, **kw):
        await self._ensure()
        await self._soap(self._avt_url, self._AVT_TYPE, action, InstanceID=0, **kw)

    async def _rc(self, action: str, **kw):
        await self._ensure()
        await self._soap(self._rc_url, self._RC_TYPE, action, InstanceID=0, **kw)

    async def async_play_track(self, url: str, track: dict):
        ct   = _content_type(track)
        art  = track.get('cover_url') or ''
        meta = _didl(url, track.get('title', ''), track.get('artist', ''),
                     track.get('album', ''), ct, art_url=art)
        # Reset transport state first — many receivers start faster from a clean state
        try:
            await self._avt('Stop')
        except Exception:
            pass
        last_err = None
        for attempt in range(3):
            try:
                await self._avt('SetAVTransportURI',
                                CurrentURI=url, CurrentURIMetaData=meta)
                await self._avt('Play', Speed=1)
                return
            except Exception as e:
                last_err = e
                print(f'[DLNA] play attempt {attempt+1}/3 failed: {e}')
                if attempt < 2:
                    self._avt_url = None   # force re-fetch description on next try
                    await asyncio.sleep(3)
        raise last_err

    async def async_pause(self):   await self._avt('Pause')
    async def async_resume(self):  await self._avt('Play', Speed=1)
    async def async_stop(self):    await self._avt('Stop')
    async def async_seek(self, s: float):
        h, r = divmod(int(s), 3600); m, sec = divmod(r, 60)
        await self._avt('Seek', Unit='REL_TIME', Target=f'{h:02d}:{m:02d}:{sec:02d}')
    async def async_get_volume(self) -> int:
        try:
            await self._ensure()
            if not self._rc_url:
                return 50
            xml = await self._soap(self._rc_url, self._RC_TYPE,
                                   'GetVolume', InstanceID=0, Channel='Master')
            m = re.search(r'<CurrentVolume>(\d+)</CurrentVolume>', xml or '')
            vol = int(m.group(1)) if m else None
            print(f'[DLNA] GetVolume response: {vol}  raw={xml[:200] if xml else None}')
            return vol if vol is not None else 50
        except Exception as e:
            print(f'[DLNA] GetVolume failed: {e}')
            return 50

    async def async_set_volume(self, pct: int):
        await self._rc('SetVolume', Channel='Master', DesiredVolume=pct)

    # ── GENA eventing ────────────────────────────────────────────────────────

    def _handle_event(self, body: bytes):
        """Called from proxy HTTP thread when a NOTIFY arrives."""
        if not self._on_event:
            return
        try:
            text = body.decode('utf-8', errors='replace')
            m = re.search(r'<LastChange>(.*?)</LastChange>', text, re.DOTALL)
            if not m:
                return
            inner = html.unescape(m.group(1))
            ts = re.search(r'<TransportState[^>]*\bval="([^"]+)"', inner)
            if ts:
                state_map = {
                    'PLAYING':          'playing',
                    'PAUSED_PLAYBACK':  'paused',
                    'STOPPED':          'stopped',
                    'NO_MEDIA_PRESENT': 'stopped',
                }
                state = state_map.get(ts.group(1))
                if state:
                    self._on_event('transport', state)
            vol = re.search(r'<Volume[^>]*\bchannel="Master"[^>]*\bval="(\d+)"', inner)
            if not vol:
                vol = re.search(r'<Volume[^>]*\bval="(\d+)"[^>]*\bchannel="Master"', inner)
            if vol:
                self._on_event('volume', int(vol.group(1)))
        except Exception as e:
            print(f'[DLNA] event parse error: {e}')

    async def async_subscribe(self, callback_url: str, on_event):
        self._on_event = on_event
        timeout_s = 1800
        hdrs = {'NT': 'upnp:event', 'CALLBACK': f'<{callback_url}>', 'TIMEOUT': f'Second-{timeout_s}'}
        for attr, url in (('_avt_sid', self._avt_event_url), ('_rc_sid', self._rc_event_url)):
            if not url:
                continue
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.request('SUBSCRIBE', url, headers=hdrs,
                                         timeout=aiohttp.ClientTimeout(total=5)) as r:
                        setattr(self, attr, r.headers.get('SID'))
                        print(f'[DLNA] subscribed {url.split("/")[-1]}  SID={getattr(self, attr)}')
            except Exception as e:
                print(f'[DLNA] subscribe {url} failed: {e}')
        if self._avt_sid or self._rc_sid:
            self._renewal_task = asyncio.create_task(self._renew_loop(timeout_s))

    async def _renew_loop(self, timeout_s: int):
        await asyncio.sleep(timeout_s - 60)
        while True:
            for url, sid_attr in ((self._avt_event_url, '_avt_sid'), (self._rc_event_url, '_rc_sid')):
                sid = getattr(self, sid_attr, None)
                if not (url and sid):
                    continue
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.request('SUBSCRIBE', url,
                                             headers={'SID': sid, 'TIMEOUT': f'Second-{timeout_s}'},
                                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                            if r.status == 200:
                                setattr(self, sid_attr, r.headers.get('SID', sid))
                except Exception as e:
                    print(f'[DLNA] renew failed: {e}')
            await asyncio.sleep(timeout_s - 60)

    async def async_unsubscribe(self):
        if self._renewal_task:
            self._renewal_task.cancel()
            self._renewal_task = None
        for url, sid_attr in ((self._avt_event_url, '_avt_sid'), (self._rc_event_url, '_rc_sid')):
            sid = getattr(self, sid_attr, None)
            if url and sid:
                try:
                    async with aiohttp.ClientSession() as s:
                        await s.request('UNSUBSCRIBE', url, headers={'SID': sid},
                                        timeout=aiohttp.ClientTimeout(total=3))
                except Exception:
                    pass
        self._avt_sid = self._rc_sid = None

    def get_volume(self) -> int:
        try:
            return _run_async(self.async_get_volume()).result(timeout=5)
        except Exception:
            return 50

    def play_track(self, url, track, **_kw):
        f = _run_async(self.async_play_track(url, track))
        f.add_done_callback(
            lambda fut: print(f'[DLNA] play_track error: {fut.exception()}')
            if fut.exception() else None
        )
        return f   # caller may .result(timeout=...) to block
    def pause(self):       _run_async(self.async_pause())
    def resume(self):      _run_async(self.async_resume())
    def stop(self):        _run_async(self.async_stop())
    def seek(self, s):     _run_async(self.async_seek(s))
    def set_volume(self, v: float): _run_async(self.async_set_volume(int(v * 100)))


# ── Local HTTP proxy (adds DLNA streaming headers Navidrome doesn't set) ─

def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]

class _StreamProxy:
    """
    Thin HTTP proxy: receiver fetches http://PLAYER_IP:PORT/KEY,
    proxy forwards to Navidrome and injects DLNA streaming headers so the
    receiver starts audio immediately instead of buffering first.
    """
    def __init__(self):
        self._urls: dict = {}
        self._event_callbacks: dict = {}   # path → callable(body: bytes)
        self._lock = threading.Lock()
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):    proxy._serve(self, head_only=False)
            def do_HEAD(self):   proxy._serve(self, head_only=True)
            def do_NOTIFY(self): proxy._notify(self)
            def do_OPTIONS(self):
                self.send_response(204)
                origin_header = self.headers.get('Origin', '*')
                self.send_header('Access-Control-Allow-Origin', origin_header)
                self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                req_headers = self.headers.get('Access-Control-Request-Headers')
                if req_headers:
                    self.send_header('Access-Control-Allow-Headers', req_headers)
                else:
                    self.send_header('Access-Control-Allow-Headers', '*')
                self.send_header('Access-Control-Max-Age', '86400')
                self.end_headers()
            def log_message(self, *a): pass  # silence request log

        self._server = ThreadingHTTPServer(('0.0.0.0', 0), _Handler)
        self._port   = self._server.server_address[1]
        self._ip     = _local_ip()
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()
        print(f'[DLNAProxy] listening on {self._ip}:{self._port}')

    def register_event(self, path: str, callback) -> str:
        with self._lock:
            self._event_callbacks[path] = callback
        return f'http://{self._ip}:{self._port}/{path}'

    def unregister_event(self, path: str):
        with self._lock:
            self._event_callbacks.pop(path, None)

    def _notify(self, handler: BaseHTTPRequestHandler):
        path = handler.path.lstrip('/')
        length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(length) if length else b''
        handler.send_response(200)
        handler.end_headers()
        with self._lock:
            cb = self._event_callbacks.get(path)
        if cb:
            try:
                cb(body)
            except Exception as e:
                print(f'[DLNAProxy] event callback error: {e}')

    def url_for(self, navidrome_url: str, is_chromecast: bool = False, content_type: str = '') -> str:
        key = hashlib.md5(navidrome_url.encode()).hexdigest()
        if is_chromecast:
            key += "_cc"
        with self._lock:
            self._urls[key] = navidrome_url
            
        ext = 'mp3'
        if 'flac' in content_type: ext = 'flac'
        elif 'ogg' in content_type: ext = 'ogg'
        elif 'mp4' in content_type or 'm4a' in content_type or 'aac' in content_type: ext = 'm4a'
        elif 'wav' in content_type: ext = 'wav'
        
        return f'http://{self._ip}:{self._port}/{key}.{ext}'

    def _serve(self, handler: BaseHTTPRequestHandler, head_only: bool):
        key = handler.path.lstrip('/')
        key = key.split('.')[0]
        is_chromecast = key.endswith('_cc')
        with self._lock:
            origin = self._urls.get(key, '')
        print(f'[DLNAProxy] {handler.command} {handler.path} → {origin[:80] if origin else "NOT FOUND"}')
        if not origin:
            handler.send_response(404); handler.end_headers(); return

        if not origin.startswith('http'):
            # --- Handle Local File Casting ---
            if not os.path.isfile(origin):
                handler.send_response(404); handler.end_headers(); return
            
            try:
                file_size = os.path.getsize(origin)
                range_header = handler.headers.get('Range')
                
                start = 0
                end = file_size - 1
                status = 200
                
                if range_header and range_header.startswith('bytes='):
                    range_match = re.match(r'bytes=(\d*)-(\d*)', range_header)
                    if range_match:
                        start_str = range_match.group(1)
                        end_str = range_match.group(2)
                        if start_str:
                            start = int(start_str)
                            if end_str:
                                end = int(end_str)
                        elif end_str:
                            start = max(0, file_size - int(end_str))
                        status = 206
                        
                if start >= file_size:
                    handler.send_response(416)
                    handler.send_header('Content-Range', f'bytes */{file_size}')
                    origin_header = handler.headers.get('Origin', '*')
                    handler.send_header('Access-Control-Allow-Origin', origin_header)
                    handler.end_headers()
                    return
                
                content_length = end - start + 1
                
                handler.send_response(status)
                
                suffix = origin.rsplit('.', 1)[-1].lower()
                ct = {
                    'flac': 'audio/flac', 'mp3': 'audio/mpeg', 'ogg': 'audio/ogg',
                    'opus': 'audio/ogg', 'aac': 'audio/aac', 'm4a': 'audio/mp4',
                    'wav': 'audio/wav'
                }.get(suffix, 'audio/mpeg')
                
                handler.send_header('Content-Type', ct)
                handler.send_header('Content-Length', str(content_length))
                handler.send_header('Accept-Ranges', 'bytes')
                if status == 206:
                    handler.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                
                if not is_chromecast:
                    pn = _DLNA_PN.get(ct, '')
                    cf = f'{pn}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={_DLNA_FLAGS}'
                    handler.send_header('transferMode.dlna.org', 'Streaming')
                    handler.send_header('contentFeatures.dlna.org', cf)
                origin_header = handler.headers.get('Origin', '*')
                handler.send_header('Access-Control-Allow-Origin', origin_header)
                handler.send_header('Access-Control-Expose-Headers', 'Content-Type, Content-Length, Content-Range, Accept-Ranges, transferMode.dlna.org, contentFeatures.dlna.org')
                handler.end_headers()
                
                if handler.command != 'HEAD':
                    with open(origin, 'rb') as f:
                        f.seek(start)
                        bytes_left = content_length
                        while bytes_left > 0:
                            chunk = f.read(min(65536, bytes_left))
                            if not chunk:
                                break
                            try:
                                handler.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                break
                            bytes_left -= len(chunk)
            except Exception as e:
                print(f"[CastProxy] Local file error: {e}")
            return

        # Forward all headers from receiver → origin (except Host and Connection)
        fwd_headers = {}
        for k, v in handler.headers.items():
            if k.lower() not in ('host', 'connection', 'cache-control', 'accept-encoding'):
                fwd_headers[k] = v
        fwd_headers['Accept-Encoding'] = 'identity'  # Prevent downstream gzip compression from destroying chunk lengths
                
        req = urllib.request.Request(origin, headers=fwd_headers, method=handler.command)
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        except urllib.error.HTTPError as e:
            resp = e  # urllib treats 206 as HTTPError sometimes; read it like a normal response!
        except Exception as e:
            print(f"[CastProxy] Remote URL error: {e}")
            handler.send_response(502); handler.end_headers(); return

        status = getattr(resp, 'status', getattr(resp, 'code', 200))
        ct = resp.headers.get('Content-Type', 'audio/mpeg').split(';')[0].strip()
        is_image = ct.startswith('image/')

        # For images: read fully, transcode to JPEG (DLNA receivers don't support WebP)
        if is_image:
            raw = resp.read()
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(raw)).convert('RGB')
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=90)
                body = buf.getvalue()
            except Exception:
                body = raw
            handler.send_response(status)
            handler.send_header('Content-Type', 'image/jpeg')
            handler.send_header('Content-Length', str(len(body)))
            handler.send_header('Access-Control-Allow-Origin', '*')
            handler.end_headers()
            if handler.command != 'HEAD':
                handler.wfile.write(body)
            return

        handler.send_response(status)

        # Pass through Navidrome headers (skip hop-by-hop)
        skip = {'transfer-encoding', 'connection', 'keep-alive', 'access-control-allow-origin', 'access-control-expose-headers'}
        for name, value in resp.headers.items():
            if name.lower() not in skip:
                handler.send_header(name, value)

        # Inject DLNA streaming headers (audio only)
        if not is_chromecast:
            pn = _DLNA_PN.get(ct, '')
            cf = f'{pn}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={_DLNA_FLAGS}'
            handler.send_header('transferMode.dlna.org', 'Streaming')
            handler.send_header('contentFeatures.dlna.org', cf)
        origin_header = handler.headers.get('Origin', '*')
        handler.send_header('Access-Control-Allow-Origin', origin_header)
        handler.send_header('Access-Control-Expose-Headers', 'Content-Type, Content-Length, Content-Range, Accept-Ranges, transferMode.dlna.org, contentFeatures.dlna.org')
        handler.end_headers()

        if handler.command != 'HEAD':
            try:
                shutil.copyfileobj(resp, handler.wfile, length=65536)
            except (BrokenPipeError, ConnectionResetError):
                pass  # receiver closed connection (e.g. after seek)

_proxy: Optional['_StreamProxy'] = None
_proxy_lock = threading.Lock()

def _get_proxy() -> '_StreamProxy':
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = _StreamProxy()
        return _proxy


# ── Qt signal bridge (safe cross-thread → main thread) ───────────────────

class _Bridge(QObject):
    devices_found    = pyqtSignal(list)        # list[DeviceInfo]
    refresh_ui       = pyqtSignal()
    device_state     = pyqtSignal(str, bool)   # (dev_id, is_connected)
    device_volume    = pyqtSignal(str, int)    # (dev_id, 0-100)
    airplay_pin_req  = pyqtSignal(str, object) # (device_name, submit_fn)
    show_error       = pyqtSignal(str, str)    # (title, message)
    dlna_playstate   = pyqtSignal(str, str)    # (dev_id, 'playing'|'paused'|'stopped')


# ── Volume slider style ───────────────────────────────────────────────────

_SLIDER_SS = """
QSlider { background:transparent; }
QSlider::groove:horizontal {
    height:3px; background:rgba(255,255,255,0.18); border-radius:2px;
}
QSlider::sub-page:horizontal {
    background:rgba(255,255,255,0.65); border-radius:2px;
}
QSlider::handle:horizontal {
    width:14px; height:14px; margin:-6px 0;
    border-radius:7px; background:white;
}
"""


# ── Per-device row ────────────────────────────────────────────────────────

_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img')

def _proto_pixmap(protocol: str | None, color: str = '#ffffff') -> 'QPixmap':
    from PyQt6.QtGui import QPixmap, QPainter
    names = {
        'chromecast': 'cast.png',
        'dlna':       'dlna.png',
        'airplay1':   'airplay.png',
        'airplay2':   'airplay.png',
    }
    fname = names.get(protocol or '', 'cast.png')
    src = QPixmap(os.path.join(_ICON_DIR, fname))
    if src.isNull():
        return src
    src = src.scaled(22, 22,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    # tint: paint the color over the pixmap using the alpha of the original
    tinted = QPixmap(src.size())
    tinted.fill(Qt.GlobalColor.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), QColor(color))
    p.end()
    return tinted


class _DeviceRow(QWidget):
    toggled        = pyqtSignal(object)        # DeviceInfo | None
    volume_changed = pyqtSignal(object, int)   # (DeviceInfo | None, 0–100)

    def __init__(self, dev, is_active: bool, volume: int = 50,
                 show_toggle: bool = True, accent_color: str = '#ffffff', parent=None):
        super().__init__(parent)
        self._dev = dev
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        # type icon
        icon = QLabel()
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet('background:transparent;')
        if dev is not None:
            px = _proto_pixmap(dev.protocol, accent_color)
            if not px.isNull():
                icon.setPixmap(px)
        lay.addWidget(icon)

        # name
        name = dev.name if dev is not None else 'This device'
        self._name_lbl = QLabel(name)
        self._name_lbl.setStyleSheet(
            'color:#ddd; font-size:13px; background:transparent;'
        )
        lay.addWidget(self._name_lbl, 1)

        # volume slider — always visible for local, visible when active for cast
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(volume)
        self._slider.setFixedWidth(110)
        self._slider.setStyleSheet(_SLIDER_SS)
        self._slider.setVisible(is_active or dev is None)
        self._slider.valueChanged.connect(lambda v: self.volume_changed.emit(self._dev, v))
        lay.addWidget(self._slider)

        # checkmark / empty box (only for toggleable cast devices)
        self._ck = None
        if show_toggle:
            self._ck = QLabel()
            self._ck.setFixedSize(20, 20)
            self._ck.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._ck.setCursor(Qt.CursorShape.PointingHandCursor)
            self._update_check(is_active)
            lay.addWidget(self._ck)

        c = QColor(accent_color)
        self._normal_bg = 'background:transparent;'
        self._hover_bg  = f'background:rgba({c.red()},{c.green()},{c.blue()},0.12);'
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(self._normal_bg)

    def _update_check(self, active: bool):
        if self._ck is None:
            return
        if active:
            self._ck.setText('✓')
            self._ck.setStyleSheet(
                'color:#eee; font-size:15px; font-weight:bold; background:transparent;'
            )
        else:
            self._ck.setText('')
            self._ck.setStyleSheet(
                'background:rgba(255,255,255,0.10);'
                ' border:1px solid rgba(255,255,255,0.20); border-radius:3px;'
            )

    def set_active(self, active: bool, volume: int = 50):
        self._slider.setVisible(active or self._dev is None)
        if active:
            self._slider.blockSignals(True)
            self._slider.setValue(volume)
            self._slider.blockSignals(False)
        self._update_check(active)

    def enterEvent(self, e):
        self.setStyleSheet(self._hover_bg); super().enterEvent(e)
    def leaveEvent(self, e):
        self.setStyleSheet(self._normal_bg); super().leaveEvent(e)
    def mousePressEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton
                and self._ck is not None
                and self._ck.underMouse()):
            self.toggled.emit(self._dev)
        super().mousePressEvent(e)


# ── Popup ─────────────────────────────────────────────────────────────────

class _CastPopup(QFrame):
    toggled        = pyqtSignal(object)        # DeviceInfo | None
    volume_changed = pyqtSignal(object, int)   # (DeviceInfo | None, 0–100)

    def __init__(self, parent=None):
        # Created once at startup and reused — no per-click HWND creation flash.
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._on_close_cb = None
        self._active_ids  = set()
        self._rows: dict  = {}
        self._accent      = '#ffffff'
        self._scan_lbl    = None
        self.setObjectName('CastPopup')
        self.setMinimumWidth(320)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            'QFrame#CastPopup {'
            '  background: #111;'
            '  border: 1px solid #2a2a2a;'
            '  border-radius: 12px;'
            '}'
        )

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 8)
        self._lay.setSpacing(0)

        # ── fixed header (updated in place on each refresh) ───────────────
        self._build_header()
        self._lay.addWidget(self._sep())

        # ── "This device" — permanent, no toggle ─────────────────────────
        local_row = _DeviceRow(None, is_active=True, volume=50, show_toggle=False, accent_color=self._accent)
        local_row.volume_changed.connect(self.volume_changed)
        self._lay.addWidget(local_row)
        self._rows['__local__'] = local_row

        self._lay.addWidget(self._sep())
        # layout indices 0–3 are the fixed section; 4+ are dynamic

    # ── Content refresh (called each time the popup is shown) ─────────────

    def refresh(self, track, cover_pixmap, local_volume, devices,
                active_ids, still_scanning, accent_color):
        self._active_ids = set(active_ids)
        self._accent     = accent_color

        self._update_header(track, cover_pixmap)

        local_row = self._rows['__local__']
        local_row._slider.blockSignals(True)
        local_row._slider.setValue(local_volume)
        local_row._slider.blockSignals(False)

        self._clear_dynamic()

        if devices:
            for dev in devices:
                self._add_device_row(dev)
            if still_scanning:
                self._scan_lbl = QLabel('  Refreshing…')
                self._scan_lbl.setStyleSheet(
                    'color:#444; font-size:11px; padding:4px 14px; background:transparent;'
                )
                self._lay.addWidget(self._scan_lbl)
        else:
            text = '  Scanning…' if still_scanning else '  No devices found'
            self._scan_lbl = QLabel(text)
            self._scan_lbl.setStyleSheet(
                'color:#444; font-size:12px; padding:10px 14px; background:transparent;'
            )
            self._lay.addWidget(self._scan_lbl)

        self.adjustSize()

    def _clear_dynamic(self):
        """Remove all device rows (except local) and the scan/status label."""
        for dev_id in list(self._rows.keys()):
            if dev_id != '__local__':
                self._rows.pop(dev_id).setParent(None)
        if self._scan_lbl is not None:
            self._scan_lbl.setParent(None)
            self._scan_lbl = None
        # Belt-and-suspenders: drop anything still sitting past the fixed 4 items
        while self._lay.count() > 4:
            item = self._lay.takeAt(4)
            if item and item.widget():
                item.widget().setParent(None)

    # ── Header ────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = QWidget()
        hdr.setStyleSheet('background:transparent;')
        h = QHBoxLayout(hdr)
        h.setContentsMargins(14, 12, 14, 10)
        h.setSpacing(12)

        self._hdr_art = QLabel()
        self._hdr_art.setFixedSize(50, 50)
        self._hdr_art.setStyleSheet('border-radius:6px; background:#2d2d2d;')
        pix = QPixmap(50, 50); pix.fill(QColor('#2d2d2d'))
        self._hdr_art.setPixmap(pix)
        h.addWidget(self._hdr_art)

        txt = QVBoxLayout()
        txt.setSpacing(3)
        txt.setContentsMargins(0, 0, 0, 0)

        self._hdr_title = QLabel('Nothing playing')
        self._hdr_title.setStyleSheet(
            'color:#efefef; font-size:13px; font-weight:bold; background:transparent;'
        )
        self._hdr_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        txt.addWidget(self._hdr_title)

        self._hdr_sub = QLabel('')
        self._hdr_sub.setStyleSheet('color:#888; font-size:11px; background:transparent;')
        self._hdr_sub.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._hdr_sub.hide()
        txt.addWidget(self._hdr_sub)

        txt.addStretch()
        h.addLayout(txt, 1)
        self._lay.addWidget(hdr)

    def _update_header(self, track, cover_pixmap):
        if cover_pixmap and not cover_pixmap.isNull():
            scaled = cover_pixmap.scaled(
                50, 50,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width()  - 50) // 2
            y = (scaled.height() - 50) // 2
            self._hdr_art.setPixmap(scaled.copy(x, y, 50, 50))
        else:
            pix = QPixmap(50, 50); pix.fill(QColor('#2d2d2d'))
            self._hdr_art.setPixmap(pix)

        if track:
            title  = track.get('title') or 'Unknown'
            artist = track.get('artist') or ''
            album  = track.get('album')  or ''
            sub    = f"{artist}  —  {album}" if album else artist
        else:
            title, sub = 'Nothing playing', ''

        self._hdr_title.setText(title)
        if sub:
            self._hdr_sub.setText(sub)
            self._hdr_sub.show()
        else:
            self._hdr_sub.hide()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _sep(self):
        s = QWidget(); s.setFixedHeight(1)
        s.setStyleSheet('background:rgba(255,255,255,0.07);')
        return s

    def _add_device_row(self, dev: DeviceInfo):
        if dev.id in self._rows:
            return
        row = _DeviceRow(dev, is_active=(dev.id in self._active_ids),
                         volume=50, show_toggle=True, accent_color=self._accent)
        row.toggled.connect(self.toggled)
        row.volume_changed.connect(self.volume_changed)
        self._rows[dev.id] = row
        self._lay.addWidget(row)

    def add_devices(self, devices: list):
        if self._scan_lbl is not None:
            self._scan_lbl.setParent(None)
            self._scan_lbl = None
        for dev in devices:
            self._add_device_row(dev)
        if len(self._rows) == 1:   # only local row
            lbl = QLabel('  No devices found')
            lbl.setStyleSheet(
                'color:#444; font-size:12px; padding:10px 14px; background:transparent;'
            )
            self._lay.addWidget(lbl)
        self.adjustSize()

    def update_device_state(self, dev_id: str, active: bool, volume: int = 50):
        row = self._rows.get(dev_id)
        if row:
            row.set_active(active, volume)
            self.adjustSize()
        if active:
            self._active_ids.add(dev_id)
        else:
            self._active_ids.discard(dev_id)

    # ── Show / dismiss ────────────────────────────────────────────────────

    def show_near(self, button):
        from PyQt6.QtCore import QPoint, QTimer
        from PyQt6.QtWidgets import QApplication

        if self.layout():
            self.layout().activate()
        hint = self.sizeHint()
        w = max(hint.width(), self.minimumWidth(), 320)
        h = hint.height() if hint.height() > 0 else 200

        g = button.mapTo(self.parent(), QPoint(0, 0))
        x = g.x() + button.width() // 2 - w // 2
        y = g.y() - h - 8
        pw = self.parent().width()
        x = max(8, min(x, pw - w - 8))
        y = max(8, y)

        self.resize(w, h)
        self.move(x, y)
        self.show()
        self.raise_()

        QTimer.singleShot(300, lambda: QApplication.instance().installEventFilter(self))

    def _dismiss(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self)
        self.hide()
        if self._on_close_cb:
            self._on_close_cb()

    def eventFilter(self, _, event):
        from PyQt6.QtCore import QEvent, QRect
        if event.type() == QEvent.Type.MouseButtonPress:
            pos = event.globalPosition().toPoint()
            tl  = self.mapToGlobal(self.rect().topLeft())
            br  = self.mapToGlobal(self.rect().bottomRight())
            if not QRect(tl, br).contains(pos):
                self._dismiss()
        return False


# ── Main manager ──────────────────────────────────────────────────────────

class CastManager:
    _RESCAN_AFTER = 30

    def __init__(self, main_window):
        self._win                       = main_window
        self._active_devices: dict      = {}    # dev_id → device obj
        self._active_ids: set           = set()
        self._popup_closed_at           = 0.0   # monotonic time of last popup hide
        self._browser                   = None
        self._devices: list             = []    # cached discovered DeviceInfo list
        self._scan_time                 = 0.0
        self._pending_scans             = 0
        self._ui_bridge                 = _Bridge(main_window)
        self._ui_bridge.refresh_ui.connect(main_window.refresh_ui_styles)
        self._ui_bridge.device_state.connect(self._on_device_state_main)
        self._ui_bridge.device_volume.connect(self._on_device_volume_main)
        self._ui_bridge.airplay_pin_req.connect(self._on_airplay_pin_main)
        self._ui_bridge.show_error.connect(self._on_show_error_main)
        self._ui_bridge.dlna_playstate.connect(self._on_dlna_playstate_main)

        # Create the popup once now so Windows initialises its HWND at startup,
        # not on the user's first click (which caused the flash).
        parent = main_window.centralWidget() or main_window
        self._popup = _CastPopup(parent=parent)
        self._popup.toggled.connect(self._on_toggle)
        self._popup.volume_changed.connect(self._on_volume_changed)
        self._popup._on_close_cb = self._on_popup_hidden
        # Pre-warm: force the native window handle to be created now, then hide.
        self._popup.move(-9999, -9999)
        self._popup.show()
        self._popup.hide()

        if _HAVE_CC or _HAVE_DLNA or _HAVE_AP:
            self._start_scan()

    # ── Public API ────────────────────────────────────────────────────────

    def _on_popup_hidden(self):
        import time as _t
        self._popup_closed_at = _t.monotonic()

    def show_picker(self):
        if not (_HAVE_CC or _HAVE_DLNA or _HAVE_AP):
            self._no_lib_msg(); return

        import time as _t

        # Toggle: if already visible, dismiss it.
        if self._popup.isVisible():
            self._popup._dismiss()
            return

        # Guard against re-opening within 250 ms of a dismiss (same click).
        if _t.monotonic() - self._popup_closed_at < 0.25:
            return

        stale = _t.time() - self._scan_time > self._RESCAN_AFTER

        idx      = getattr(self._win, 'current_index', -1)
        pl       = getattr(self._win, 'playlist_data', [])
        track    = pl[idx] if 0 <= idx < len(pl) else None
        vol      = getattr(getattr(self._win, 'vol_slider', None), 'value', lambda: 50)()
        cover_px = getattr(self._win, 'current_cover_pixmap', None)

        self._popup.refresh(
            track=track,
            cover_pixmap=cover_px,
            local_volume=vol,
            devices=list(self._devices),
            active_ids=self._active_ids,
            still_scanning=(self._pending_scans > 0 or stale),
            accent_color=getattr(self._win, 'master_color', '#ffffff'),
        )
        self._popup.show_near(self._win.cast_btn)

        if self._active_devices:
            threading.Thread(target=self._sync_volumes, daemon=True).start()

        if stale:
            self._start_scan()

    # How many ms ahead of now we schedule AirPlay 2 to start so PC and
    # AirPlay begin playing at the same wall-clock moment.
    _AP2_SYNC_MS = 1500

    def has_airplay2(self) -> bool:
        """Return True if any active device is AirPlay 2."""
        for dev_id in list(self._active_devices):
            dev_info = next((d for d in self._devices if d.id == dev_id), None)
            if dev_info and dev_info.protocol == 'airplay2':
                return True
        return False

    def relay_track(self, track: dict, ntp_start: int = 0):
        url = track.get('stream_url') or track.get('path', '')
        if not url or not self._active_devices: return
        sc = getattr(self._win, 'navidrome_client', None)
        if sc and not track.get('cover_url'):
            cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
            if cover_id:
                track = dict(track)
                navidrome_art = sc.get_cover_art_url(cover_id, size=500)
                proxy = _get_proxy()
                key = hashlib.md5(navidrome_art.encode()).hexdigest()
                with proxy._lock:
                    proxy._urls[key] = navidrome_art
                track['cover_url'] = f'http://{proxy._ip}:{proxy._port}/{key}.jpg'
                print(f'[DLNA relay_track] cover_url={track["cover_url"]}')
        ct = _content_type(track)
        for dev_id, dev in list(self._active_devices.items()):
            dev_info = next((d for d in self._devices if d.id == dev_id), None)
            is_cc = (dev_info and dev_info.protocol == 'chromecast')
            if dev_info and dev_info.protocol in ('dlna', 'chromecast'):
                cast_url = self._dlna_url(url, is_chromecast=is_cc, ct=ct)
            else:
                cast_url = url  # AirPlay: pass raw URL, pyatv fetches directly
            kw = {'subsonic': sc}
            
            pos_ms = getattr(self._win, 'last_engine_pos', 0)
            if pos_ms > 500:
                kw['seek_s'] = pos_ms / 1000.0
                
            if ntp_start > 0 and dev_info and dev_info.protocol == 'airplay2':
                kw['ntp_start'] = ntp_start
            threading.Thread(
                target=dev.play_track, args=(cast_url, track), kwargs=kw,
                daemon=True,
            ).start()

    def relay_pause(self):
        for dev in list(self._active_devices.values()):
            threading.Thread(target=dev.pause, daemon=True).start()

    def relay_play(self):
        for dev in list(self._active_devices.values()):
            threading.Thread(target=dev.resume, daemon=True).start()

    def relay_seek(self, seconds: float):
        for dev in list(self._active_devices.values()):
            threading.Thread(target=dev.seek, args=(seconds,), daemon=True).start()

    def relay_volume(self, value: int):
        for dev in list(self._active_devices.values()):
            threading.Thread(
                target=dev.set_volume, args=(value / 100.0,), daemon=True
            ).start()

    def is_connected(self) -> bool:
        return bool(self._active_devices)

    # ── Discovery ─────────────────────────────────────────────────────────

    def _start_scan(self):
        """Start a background discovery pass; results arrive via _on_scan_results."""
        if self._pending_scans > 0:
            return   # already scanning
        self._devices.clear()
        self._pending_scans = (_HAVE_CC + _HAVE_DLNA + _HAVE_AP)
        bridge = _Bridge(self._win)
        bridge.devices_found.connect(self._on_scan_results)
        if _HAVE_CC:
            threading.Thread(target=self._discover_cc, args=(bridge,), daemon=True).start()
        if _HAVE_DLNA:
            _run_async(self._discover_dlna(bridge))
        if _HAVE_AP:
            threading.Thread(target=self._discover_airplay, args=(bridge,), daemon=True).start()

    def _on_scan_results(self, devices: list):
        import time as _t
        self._devices.extend(devices)
        self._pending_scans = max(0, self._pending_scans - 1)
        if self._pending_scans == 0:
            self._scan_time = _t.time()
        # Feed the popup with newly discovered devices only while it's open
        if self._popup.isVisible() and devices:
            self._popup.add_devices(devices)

    def _discover_cc(self, bridge: _Bridge):
        try:
            chromecasts, browser = pychromecast.get_chromecasts(timeout=5)
            # Manager owns the browser; keep it alive until all cc.wait() finish.
            # Stop the old browser only if no Chromecast devices are currently active.
            if self._browser and not any(
                isinstance(d, _ChromecastDevice)
                for d in self._active_devices.values()
            ):
                try: pychromecast.discovery.stop_discovery(self._browser)
                except Exception: pass
            self._browser = browser
            devices = [
                DeviceInfo(
                    id=str(cc.uuid),
                    name=cc.cast_info.friendly_name,
                    protocol='chromecast',
                    _cc=cc,
                )
                for cc in chromecasts
            ]
            bridge.devices_found.emit(devices)
        except Exception as e:
            print(f'[Cast] CC discovery error: {e}')
            bridge.devices_found.emit([])

    async def _discover_dlna(self, bridge: _Bridge):
        devices = []
        seen_locations = set()

        async def _on_response(headers):
            st       = headers.get('ST', '')
            location = headers.get('LOCATION', '')
            if not location or 'MediaRenderer' not in st:
                return
            if location in seen_locations:
                return
            seen_locations.add(location)
            usn  = headers.get('USN', location)
            name, avt, rc, avt_event, rc_event = await self._dlna_device_info(location)
            devices.append(DeviceInfo(id=usn, name=name, protocol='dlna',
                                      location=location, avt_url=avt, rc_url=rc,
                                      avt_event_url=avt_event, rc_event_url=rc_event))

        try:
            await async_search(
                async_callback=_on_response,
                timeout=5,
                search_target='ssdp:all',
            )
        except Exception as e:
            print(f'[Cast] DLNA discovery error: {e}')
        bridge.devices_found.emit(devices)

    async def _dlna_device_info(self, location: str):
        """Fetch device XML once; return (name, avt_url, rc_url). Caches on DeviceInfo."""
        from urllib.parse import urlparse, urljoin
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(location, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    xml = await r.text()
            parsed   = urlparse(location)
            base_url = f'{parsed.scheme}://{parsed.netloc}'

            def _svc(svc_prefix):
                for blk in re.findall(r'<service>.*?</service>', xml, re.DOTALL):
                    if not re.search(
                        rf'<serviceType>{re.escape(svc_prefix)}:\d+</serviceType>', blk
                    ):
                        continue
                    cm = re.search(r'<controlURL>([^<]+)</controlURL>',   blk)
                    em = re.search(r'<eventSubURL>([^<]+)</eventSubURL>', blk)
                    ctrl  = urljoin(base_url, cm.group(1).strip()) if cm else ''
                    event = urljoin(base_url, em.group(1).strip()) if em else ''
                    return ctrl, event
                return '', ''

            name_m    = re.search(r'<friendlyName>([^<]+)</friendlyName>', xml)
            name      = name_m.group(1).strip() if name_m else location.split('/')[2]
            avt, avt_event = _svc('urn:schemas-upnp-org:service:AVTransport')
            rc,  rc_event  = _svc('urn:schemas-upnp-org:service:RenderingControl')
            print(f'[DLNA] Discovered {name!r}  AVT={avt}  RC={rc}')
            return name, avt, rc, avt_event, rc_event
        except Exception as e:
            print(f'[DLNA] device_info error for {location}: {e}')
            return location.split('/')[2], '', '', '', ''

    def _discover_airplay(self, bridge: _Bridge):
        try:
            ap_devices = _ap_discover(timeout=5.0)
            devices = [
                DeviceInfo(
                    id=d.id, name=d.name, protocol=d.protocol,
                    _ap=d,
                )
                for d in ap_devices
            ]
            bridge.devices_found.emit(devices)
        except Exception as e:
            print(f'[Cast] AirPlay discovery error: {e}')
            bridge.devices_found.emit([])

    # ── Toggle / volume handlers (main thread, called by popup signals) ──────

    def _on_toggle(self, dev: Optional[DeviceInfo]):
        """Called on main thread when user clicks a device row checkbox."""
        if dev is None:
            return   # "This device" has no toggle
        if dev.id in self._active_ids:
            threading.Thread(
                target=self._disconnect_device, args=(dev.id,), daemon=True
            ).start()
        else:
            threading.Thread(
                target=self._connect_device, args=(dev,), daemon=True
            ).start()

    def _on_volume_changed(self, dev_or_none, value: int):
        """Called on main thread when a volume slider moves."""
        if dev_or_none is None:
            # Local device — set audio engine directly, bypassing _cast_relay_volume
            sl = getattr(self._win, 'vol_slider', None)
            if sl:
                sl.blockSignals(True)
                sl.setValue(value)
                sl.blockSignals(False)
            ae = getattr(self._win, 'audio_engine', None)
            if ae:
                ae.set_volume(value)
            muted = (value == 0)
            if muted != getattr(self._win, 'is_muted', False):
                self._win.is_muted = muted
                self._win.update_volume_icon()
            if not muted:
                self._win.last_volume = value
        else:
            dev_obj = self._active_devices.get(dev_or_none.id)
            if dev_obj:
                threading.Thread(
                    target=dev_obj.set_volume, args=(value / 100.0,), daemon=True
                ).start()

    def _sync_volumes(self):
        """Background: read actual volume from each active device and push to popup."""
        for dev_id, dev_obj in list(self._active_devices.items()):
            try:
                vol = dev_obj.get_volume()
                if vol > 0:   # skip if device reports 0 (likely unimplemented/error)
                    self._ui_bridge.device_volume.emit(dev_id, vol)
            except Exception:
                pass

    def _on_airplay_pin(self, device_name: str, submit_fn):
        """Called from stderr-reader thread — marshal to main thread via signal."""
        self._ui_bridge.airplay_pin_req.emit(device_name, submit_fn)

    def _on_airplay_pin_main(self, device_name: str, submit_fn):
        """Main-thread: show PIN dialog and forward the result to cliap2."""
        from PyQt6.QtWidgets import QInputDialog
        pin, ok = QInputDialog.getText(
            self._win, 'AirPlay Pairing',
            f'Enter the 4-digit PIN shown on {device_name}:',
        )
        if ok and pin.strip():
            threading.Thread(target=submit_fn, args=(pin.strip(),), daemon=True).start()

    def _on_show_error_main(self, title: str, message: str):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self._win, title, message)

    def _on_device_state_main(self, dev_id: str, connected: bool):
        """Runs on main thread; updates popup row and cast button color."""
        if self._popup:
            self._popup.update_device_state(dev_id, connected)
        self._win._cast_connected = bool(self._active_devices)
        self._win.refresh_ui_styles()

    def _on_device_volume_main(self, dev_id: str, volume: int):
        """Runs on main thread; syncs the popup slider to the device's actual volume."""
        if self._popup:
            row = self._popup._rows.get(dev_id)
            if row:
                row._slider.blockSignals(True)
                row._slider.setValue(volume)
                row._slider.blockSignals(False)

    def _on_dlna_playstate_main(self, _dev_id: str, state: str):
        """Runs on main thread; mirrors DLNA device play/pause to the local player."""
        ae = getattr(self._win, 'audio_engine', None)
        if not ae:
            return
        if state == 'paused' and ae.is_playing:
            ae.pause()
            if hasattr(self._win, 'smooth_timer'):
                self._win.smooth_timer.stop()
            if hasattr(self._win, 'seek_bar'):
                self._win.seek_bar.is_playing = False
            self._win.refresh_ui_styles()
        elif state == 'playing' and not ae.is_playing:
            ae.play()
            if hasattr(self._win, 'smooth_timer'):
                self._win.smooth_timer.start()
            if hasattr(self._win, 'seek_bar'):
                self._win.seek_bar.is_playing = True
            self._win.refresh_ui_styles()

    # ── Connection / disconnection (background threads) ───────────────────

    @staticmethod
    def _dlna_url(url: str, is_chromecast: bool = False, ct: str = '') -> str:
        """Wrap URL through local proxy that injects DLNA streaming headers."""
        if not url:
            return url
        return _get_proxy().url_for(url, is_chromecast, ct)

    async def _dlna_play_chain(self, d: '_DLNADevice', url: str, track: dict,
                               pos_ms: int, paused: bool) -> int:
        """Async: play → seek → pause → return actual volume. Runs in cast loop."""
        ct = _content_type(track)
        if not track.get('cover_url'):
            sc = getattr(self._win, 'navidrome_client', None)
            cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
            print(f'[DLNA play_chain] sc={sc!r} cover_id={cover_id!r}')
            if sc and cover_id:
                navidrome_art = sc.get_cover_art_url(cover_id, size=500)
                proxy = _get_proxy()
                key = hashlib.md5(navidrome_art.encode()).hexdigest()
                with proxy._lock:
                    proxy._urls[key] = navidrome_art
                track = dict(track)
                track['cover_url'] = f'http://{proxy._ip}:{proxy._port}/{key}.jpg'
                print(f'[DLNA play_chain] cover_url={track["cover_url"]}')
        print(f'[DLNA play_chain] final cover_url={track.get("cover_url")!r}')
        await d.async_play_track(self._dlna_url(url, is_chromecast=False, ct=ct), track)
        if pos_ms > 500:
            await asyncio.sleep(1.0)   # let stream buffer
            try:
                await d.async_seek(pos_ms / 1000.0)
            except Exception as e:
                print(f'[Cast] DLNA seek failed (ignored): {e}')
        if paused:
            await d.async_pause()
        return await d.async_get_volume()

    def _connect_device(self, dev: DeviceInfo):
        try:
            print(f'[Cast] Connecting to {dev.name!r} ({dev.protocol}) …')
            if dev.protocol == 'chromecast':
                d = _ChromecastDevice(dev._cc)
                d.connect()   # cc.wait(); browser stays alive in self._browser
                d.register_listeners(dev.id, self._ui_bridge)
            elif dev.protocol in ('airplay1', 'airplay2'):
                d = AirPlayDevice(
                    dev._ap,
                    pin_callback=self._on_airplay_pin,
                    error_callback=lambda name, msg: self._ui_bridge.show_error.emit(
                        f'AirPlay – {name}', msg
                    ),
                )
                d.connect()
            else:
                d = _DLNADevice(dev.location, avt_url=dev.avt_url, rc_url=dev.rc_url,
                                avt_event_url=dev.avt_event_url, rc_event_url=dev.rc_event_url)

            self._active_devices[dev.id] = d
            self._active_ids.add(dev.id)
            self._win._cast_connected = True
            self._refresh_ui()
            self._ui_bridge.device_state.emit(dev.id, True)
            print(f'[Cast] Connected to {dev.name!r}')

            # Snapshot playback state now (before any async delay)
            idx    = getattr(self._win, 'current_index', -1)
            pl     = getattr(self._win, 'playlist_data', [])
            track  = pl[idx] if 0 <= idx < len(pl) else None
            url    = (track.get('stream_url') or track.get('path', '')) if track else ''
            pos_ms = getattr(self._win, 'last_engine_pos', 0)
            ae     = getattr(self._win, 'audio_engine', None)
            paused = ae and not ae.is_playing

            if dev.protocol == 'dlna':
                # Set up GENA event subscription
                if d._avt_event_url or d._rc_event_url:
                    proxy       = _get_proxy()
                    event_path  = f'dlna-event/{hashlib.md5(dev.location.encode()).hexdigest()[:12]}'
                    d._event_path = event_path
                    dev_id_cap  = dev.id
                    bridge_cap  = self._ui_bridge
                    def _on_dlna_event(etype, value, _did=dev_id_cap, _br=bridge_cap):
                        if etype == 'transport':
                            _br.dlna_playstate.emit(_did, value)
                        elif etype == 'volume':
                            _br.device_volume.emit(_did, value)
                    callback_url = proxy.register_event(event_path, d._handle_event)
                    d._on_event  = _on_dlna_event
                    _run_async(d.async_subscribe(callback_url, _on_dlna_event))

                # Fire async chain (play → seek → pause → read volume) — non-blocking
                if url:
                    dev_id  = dev.id
                    bridge  = self._ui_bridge
                    fut = _run_async(
                        self._dlna_play_chain(d, url, track, pos_ms, paused)
                    )
                    def _on_chain_done(f):
                        try:
                            vol = f.result()
                            if vol and vol > 0:
                                bridge.device_volume.emit(dev_id, vol)
                        except Exception as e:
                            print(f'[Cast] DLNA play chain error: {e}')
                    fut.add_done_callback(_on_chain_done)
            elif dev.protocol in ('airplay1', 'airplay2'):
                d.register_listeners(dev.id, self._ui_bridge)
                # stream_file() runs for the full song — fire-and-forget; errors
                # arrive via error_callback as a Qt signal on the main thread.
                if url:
                    ct = _content_type(track)
                    # Pass the raw URL directly — pyatv fetches it itself via requests.
                    # Routing through the local proxy breaks miniaudio's MP3 decoder
                    # (the proxy strips Content-Length, so miniaudio can't seek for init).
                    ap_url = url
                    if not track.get('cover_url'):
                        sc = getattr(self._win, 'navidrome_client', None)
                        cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
                        if sc and cover_id:
                            navidrome_art = sc.get_cover_art_url(cover_id, size=500)
                            proxy = _get_proxy()
                            key = hashlib.md5(navidrome_art.encode()).hexdigest()
                            with proxy._lock:
                                proxy._urls[key] = navidrome_art
                            track = dict(track)
                            track['cover_url'] = f'http://{proxy._ip}:{proxy._port}/{key}.jpg'
                    d.play_track(ap_url, track)
                vol = d.get_volume()
                if vol > 0:
                    self._ui_bridge.device_volume.emit(dev.id, vol)
            else:
                # Chromecast: already blocking-connected; do play in this thread
                if url:
                    ct = _content_type(track)
                    cast_url = self._dlna_url(url, is_chromecast=True, ct=ct)
                    if not track.get('cover_url'):
                        sc = getattr(self._win, 'navidrome_client', None)
                        cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
                        if sc and cover_id:
                            navidrome_art = sc.get_cover_art_url(cover_id, size=500)
                            proxy = _get_proxy()
                            key = hashlib.md5(navidrome_art.encode()).hexdigest()
                            with proxy._lock:
                                proxy._urls[key] = navidrome_art
                            track = dict(track)
                            track['cover_url'] = f'http://{proxy._ip}:{proxy._port}/{key}.jpg'
                    print(f'[CC] connect → play  raw_url={url[:80]}')
                    print(f'[CC] cast_url={cast_url[:80]}  ct={ct}  paused={paused}')
                    d.play_track(cast_url, track, seek_s=(pos_ms / 1000.0) if pos_ms > 500 else 0.0)
                    if paused:
                        d.pause()
                vol = d.get_volume()
                if vol > 0:
                    self._ui_bridge.device_volume.emit(dev.id, vol)

        except Exception as e:
            print(f'[Cast] Connect failed: {e}')
            if not isinstance(e, RuntimeError):
                import traceback; traceback.print_exc()
            self._active_devices.pop(dev.id, None)
            self._active_ids.discard(dev.id)
            if not self._active_devices:
                self._win._cast_connected = False
            self._refresh_ui()
            self._ui_bridge.device_state.emit(dev.id, False)
            self._ui_bridge.show_error.emit(f'Cannot connect to {dev.name}', str(e))

    def _disconnect_device(self, dev_id: str):
        dev_obj = self._active_devices.pop(dev_id, None)
        if dev_obj:
            try: dev_obj.stop()
            except Exception: pass
            if isinstance(dev_obj, _DLNADevice):
                if dev_obj._event_path and _proxy is not None:
                    _proxy.unregister_event(dev_obj._event_path)
                _run_async(dev_obj.async_unsubscribe())
        self._active_ids.discard(dev_id)
        if not self._active_devices:
            self._win._cast_connected = False
        self._refresh_ui()
        self._ui_bridge.device_state.emit(dev_id, False)

    def _disconnect_all(self):
        for dev_obj in list(self._active_devices.values()):
            try: dev_obj.stop()
            except Exception: pass
        self._active_devices.clear()
        self._active_ids.clear()
        if self._browser:
            try: pychromecast.discovery.stop_discovery(self._browser)
            except Exception: pass
            self._browser = None
        self._win._cast_connected = False
        self._refresh_ui()

    def _refresh_ui(self):
        self._ui_bridge.refresh_ui.emit()

    # ── Fallback ──────────────────────────────────────────────────────────

    def _no_lib_msg(self):
        from PyQt6.QtWidgets import QMessageBox
        missing = []
        if not _HAVE_CC:   missing.append('pychromecast')
        if not _HAVE_DLNA: missing.append('async-upnp-client')
        if not _HAVE_AP:   missing.append('pyatv')
        QMessageBox.information(
            self._win, 'Cast unavailable',
            f"Install missing libraries:\n  pip install {' '.join(missing)}",
        )
