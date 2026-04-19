"""
cast_manager.py — Multi-protocol cast manager (Chromecast + DLNA/UPnP).

Click the cast button → popup appears above it listing all discovered devices.
Selecting a device streams the current track's Navidrome URL directly to it.
Play/pause/seek/track-change events are relayed automatically.
"""

import asyncio
import hashlib
import html
import re
import shutil
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
import urllib.request

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
except ImportError:
    _HAVE_CC = False

try:
    import aiohttp
    from async_upnp_client.search import async_search
    _HAVE_DLNA = True
except ImportError:
    _HAVE_DLNA = False


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

def _didl(url: str, title: str, artist: str, album: str, ct: str) -> str:
    def e(s): return html.escape(str(s or ''))
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="0">'
        f'<dc:title>{e(title)}</dc:title>'
        f'<dc:creator>{e(artist)}</dc:creator>'
        f'<upnp:artist>{e(artist)}</upnp:artist>'
        f'<upnp:album>{e(album)}</upnp:album>'
        f'<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
        f'<res protocolInfo="{_protocol_info(ct)}">{e(url)}</res>'
        f'</item></DIDL-Lite>'
    )


# ── Device data ───────────────────────────────────────────────────────────

@dataclass
class DeviceInfo:
    id:       str
    name:     str
    protocol: str          # 'chromecast' | 'dlna'
    location: str = ''     # DLNA description URL
    avt_url:  str = ''     # cached AVTransport control URL (skip _ensure() on connect)
    rc_url:   str = ''     # cached RenderingControl control URL
    _cc:      object = field(default=None, repr=False)
    _browser: object = field(default=None, repr=False)


# ── Chromecast wrapper ────────────────────────────────────────────────────

class _ChromecastDevice:
    def __init__(self, cc):
        self._cc = cc

    def connect(self):
        # Browser lifecycle is managed by CastManager — do NOT stop it here.
        # All devices from one scan share the same browser; stopping early
        # kills cc.wait() for every other device in the batch.
        self._cc.wait(timeout=8)

    def play_track(self, url: str, track: dict):
        ct = _content_type(track)
        mc = self._cc.media_controller
        mc.play_media(
            url, ct,
            title=track.get('title', ''),
            metadata={
                'metadataType': 3,   # MUSIC_TRACK
                'albumName': track.get('album', ''),
                'artist':    track.get('artist', ''),
            },
        )
        mc.block_until_active(timeout=10)

    def get_volume(self) -> int:
        try:
            level = self._cc.status.volume_level
            return int(level * 100) if level is not None else 50
        except Exception:
            return 50

    def pause(self):   self._cc.media_controller.pause()
    def resume(self):  self._cc.media_controller.play()
    def seek(self, s): self._cc.media_controller.seek(s)
    def stop(self):
        try: self._cc.quit_app()
        except Exception: pass
    def set_volume(self, v: float): self._cc.set_volume(max(0.0, min(1.0, v)))


# ── DLNA wrapper (raw SOAP — works with nested sub-device layouts) ────────

class _DLNADevice:
    _AVT_TYPE = 'urn:schemas-upnp-org:service:AVTransport:1'
    _RC_TYPE  = 'urn:schemas-upnp-org:service:RenderingControl:1'

    def __init__(self, location: str, avt_url: str = '', rc_url: str = ''):
        self._location = location
        self._avt_url  = avt_url or None   # pre-cached from discovery; skips _ensure() HTTP GET
        self._rc_url   = rc_url  or None

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

                def _ctrl_url(service_type):
                    m = re.search(
                        rf'<serviceType>{re.escape(service_type)}</serviceType>'
                        r'.*?<controlURL>([^<]+)</controlURL>',
                        xml, re.DOTALL,
                    )
                    return urljoin(base_url, m.group(1).strip()) if m else None

                self._avt_url = _ctrl_url(self._AVT_TYPE)
                self._rc_url  = _ctrl_url(self._RC_TYPE)
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
        meta = _didl(url, track.get('title', ''), track.get('artist', ''),
                     track.get('album', ''), ct)
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

    def get_volume(self) -> int:
        try:
            return _run_async(self.async_get_volume()).result(timeout=5)
        except Exception:
            return 50

    def play_track(self, url, track):
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
        self._lock = threading.Lock()
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  proxy._serve(self, head_only=False)
            def do_HEAD(self): proxy._serve(self, head_only=True)
            def log_message(self, *a): pass  # silence request log

        self._server = ThreadingHTTPServer(('0.0.0.0', 0), _Handler)
        self._port   = self._server.server_address[1]
        self._ip     = _local_ip()
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()
        print(f'[DLNAProxy] listening on {self._ip}:{self._port}')

    def url_for(self, navidrome_url: str) -> str:
        key = hashlib.md5(navidrome_url.encode()).hexdigest()
        with self._lock:
            self._urls[key] = navidrome_url
        return f'http://{self._ip}:{self._port}/{key}'

    def _serve(self, handler: BaseHTTPRequestHandler, head_only: bool):
        key = handler.path.lstrip('/')
        with self._lock:
            origin = self._urls.get(key, '')
        if not origin:
            handler.send_response(404); handler.end_headers(); return

        # Forward all headers from receiver → Navidrome (except Host)
        fwd_headers = {k: v for k, v in handler.headers.items()
                       if k.lower() != 'host'}
        req = urllib.request.Request(origin, headers=fwd_headers)
        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as e:
            handler.send_response(e.code); handler.end_headers(); return
        except Exception:
            handler.send_response(502); handler.end_headers(); return

        handler.send_response(resp.status if hasattr(resp, 'status') else 200)
        ct = resp.headers.get('Content-Type', 'audio/mpeg').split(';')[0].strip()

        # Pass through Navidrome headers (skip hop-by-hop)
        skip = {'transfer-encoding', 'connection', 'keep-alive'}
        for name, value in resp.headers.items():
            if name.lower() not in skip:
                handler.send_header(name, value)

        # Inject DLNA streaming headers — this is why the receiver plays instantly
        pn = _DLNA_PN.get(ct, '')
        cf = f'{pn}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={_DLNA_FLAGS}'
        handler.send_header('transferMode.dlna.org', 'Streaming')
        handler.send_header('contentFeatures.dlna.org', cf)
        handler.end_headers()

        if not head_only:
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

class _DeviceRow(QWidget):
    toggled        = pyqtSignal(object)        # DeviceInfo | None
    volume_changed = pyqtSignal(object, int)   # (DeviceInfo | None, 0–100)

    _PROTO_ICON = {'chromecast': '📺', 'dlna': '🔊'}

    def __init__(self, dev, is_active: bool, volume: int = 50,
                 show_toggle: bool = True, parent=None):
        super().__init__(parent)
        self._dev = dev
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        # type icon
        char = '🖥' if dev is None else self._PROTO_ICON.get(dev.protocol, '🔊')
        icon = QLabel(char)
        icon.setFixedWidth(24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet('font-size:15px; background:transparent; color:#aaa;')
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

        self._normal_bg = 'background:transparent;'
        self._hover_bg  = 'background:rgba(255,255,255,0.07);'
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

    def __init__(self, active_ids: set, local_volume: int,
                 current_track: Optional[dict], cover_pixmap=None, parent=None,
                 initial_devices: list = None, still_scanning: bool = True):
        super().__init__(parent, Qt.WindowType.Popup)
        self._active_ids = set(active_ids)
        self._rows: dict = {}     # dev_id → _DeviceRow
        self.setObjectName('CastPopup')
        self.setMinimumWidth(320)
        self.setStyleSheet(
            '#CastPopup {'
            '  background:#1e1e1e;'
            '  border:1px solid rgba(255,255,255,0.12);'
            '  border-radius:12px;'
            '}'
        )
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 8)
        self._lay.setSpacing(0)

        # ── track header ──────────────────────────────────────────────────
        self._build_header(current_track, cover_pixmap)
        self._lay.addWidget(self._sep())

        # ── "This device" — always active, no toggle ──────────────────────
        local_row = _DeviceRow(None, is_active=True, volume=local_volume,
                               show_toggle=False)
        local_row.volume_changed.connect(self.volume_changed)
        self._lay.addWidget(local_row)
        self._rows['__local__'] = local_row

        self._lay.addWidget(self._sep())

        # ── discovered devices ────────────────────────────────────────────
        self._scan_lbl = None
        if initial_devices:
            for dev in initial_devices:
                self._add_device_row(dev)
            if still_scanning:
                self._scan_lbl = QLabel('  Refreshing…')
                self._scan_lbl.setStyleSheet(
                    'color:#444; font-size:11px; padding:4px 14px; background:transparent;'
                )
                self._lay.addWidget(self._scan_lbl)
        else:
            self._scan_lbl = QLabel('  Scanning…')
            self._scan_lbl.setStyleSheet(
                'color:#444; font-size:12px; padding:10px 14px; background:transparent;'
            )
            self._lay.addWidget(self._scan_lbl)

    def _build_header(self, track: Optional[dict], cover_pixmap=None):
        hdr = QWidget()
        hdr.setStyleSheet('background:transparent;')
        h = QHBoxLayout(hdr)
        h.setContentsMargins(14, 12, 14, 10)
        h.setSpacing(12)

        # cover art
        art = QLabel()
        art.setFixedSize(50, 50)
        art.setStyleSheet('border-radius:6px; background:#2d2d2d;')
        if cover_pixmap and not cover_pixmap.isNull():
            scaled = cover_pixmap.scaled(
                50, 50,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            # centre-crop to exactly 50×50
            x = (scaled.width()  - 50) // 2
            y = (scaled.height() - 50) // 2
            art.setPixmap(scaled.copy(x, y, 50, 50))
        else:
            pix = QPixmap(50, 50)
            pix.fill(QColor('#2d2d2d'))
            art.setPixmap(pix)
        h.addWidget(art)

        # title + artist
        txt = QVBoxLayout()
        txt.setSpacing(3)
        txt.setContentsMargins(0, 0, 0, 0)
        if track:
            title  = track.get('title') or 'Unknown'
            artist = track.get('artist') or ''
            album  = track.get('album')  or ''
            sub    = f"{artist}  —  {album}" if album else artist
        else:
            title, sub = 'Nothing playing', ''

        t = QLabel(title)
        t.setStyleSheet(
            'color:#efefef; font-size:13px; font-weight:bold; background:transparent;'
        )
        t.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        txt.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet('color:#888; font-size:11px; background:transparent;')
            s.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            txt.addWidget(s)
        txt.addStretch()
        h.addLayout(txt, 1)
        self._lay.addWidget(hdr)

    def _sep(self):
        s = QWidget(); s.setFixedHeight(1)
        s.setStyleSheet('background:rgba(255,255,255,0.07);')
        return s

    def _add_device_row(self, dev: DeviceInfo):
        if dev.id in self._rows:
            return
        row = _DeviceRow(dev, is_active=(dev.id in self._active_ids),
                         volume=50, show_toggle=True)
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
        # show "No devices found" only if still nothing besides local row
        if len(self._rows) == 1:
            lbl = QLabel('  No devices found')
            lbl.setStyleSheet(
                'color:#444; font-size:12px; padding:10px 14px; background:transparent;'
            )
            self._lay.addWidget(lbl)
        self.adjustSize()

    def update_device_state(self, dev_id: str, active: bool, volume: int = 50):
        """Called from main thread to reflect a connect/disconnect in the row UI."""
        row = self._rows.get(dev_id)
        if row:
            row.set_active(active, volume)
            self.adjustSize()
        if active:
            self._active_ids.add(dev_id)
        else:
            self._active_ids.discard(dev_id)

    def show_near(self, button):
        self.adjustSize()
        g   = button.mapToGlobal(QPoint(0, 0))
        x   = g.x() + button.width() // 2 - self.width() // 2
        y   = g.y() - self.height() - 8
        scr = button.screen().availableGeometry()
        x   = max(scr.left() + 8, min(x, scr.right() - self.width() - 8))
        y   = max(scr.top()  + 8, y)
        self.move(x, y)
        self.show()
        self.raise_()


# ── Main manager ──────────────────────────────────────────────────────────

class CastManager:
    _RESCAN_AFTER = 30

    def __init__(self, main_window):
        self._win                       = main_window
        self._active_devices: dict      = {}    # dev_id → device obj
        self._active_ids: set           = set()
        self._popup                     = None
        self._browser                   = None
        self._devices: list             = []    # cached discovered DeviceInfo list
        self._scan_time                 = 0.0
        self._pending_scans             = 0
        self._ui_bridge                 = _Bridge(main_window)
        self._ui_bridge.refresh_ui.connect(main_window.refresh_ui_styles)
        self._ui_bridge.device_state.connect(self._on_device_state_main)
        self._ui_bridge.device_volume.connect(self._on_device_volume_main)
        if _HAVE_CC or _HAVE_DLNA:
            self._start_scan()

    # ── Public API ────────────────────────────────────────────────────────

    def show_picker(self):
        if not (_HAVE_CC or _HAVE_DLNA):
            self._no_lib_msg(); return

        import time as _t
        stale = _t.time() - self._scan_time > self._RESCAN_AFTER

        idx      = getattr(self._win, 'current_index', -1)
        pl       = getattr(self._win, 'playlist_data', [])
        track    = pl[idx] if 0 <= idx < len(pl) else None
        vol      = getattr(getattr(self._win, 'vol_slider', None), 'value', lambda: 50)()
        cover_px = getattr(self._win, 'current_cover_pixmap', None)

        self._popup = _CastPopup(
            active_ids=self._active_ids,
            local_volume=vol,
            current_track=track,
            cover_pixmap=cover_px,
            parent=self._win,
            initial_devices=list(self._devices),
            still_scanning=(self._pending_scans > 0 or stale),
        )
        self._popup.toggled.connect(self._on_toggle)
        self._popup.volume_changed.connect(self._on_volume_changed)
        self._popup.show_near(self._win.cast_btn)

        # Refresh volume sliders for already-connected devices in background
        if self._active_devices:
            threading.Thread(target=self._sync_volumes, daemon=True).start()

        if stale:
            self._start_scan()

    def relay_track(self, track: dict):
        url = track.get('stream_url') or track.get('path', '')
        if not url or not self._active_devices: return
        for dev_id, dev in list(self._active_devices.items()):
            dev_info = next((d for d in self._devices if d.id == dev_id), None)
            cast_url = self._dlna_url(url) if (dev_info and dev_info.protocol == 'dlna') else url
            threading.Thread(target=dev.play_track, args=(cast_url, track), daemon=True).start()

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
        self._pending_scans = (_HAVE_CC + _HAVE_DLNA)  # booleans sum as ints
        bridge = _Bridge(self._win)
        bridge.devices_found.connect(self._on_scan_results)
        if _HAVE_CC:
            threading.Thread(target=self._discover_cc, args=(bridge,), daemon=True).start()
        if _HAVE_DLNA:
            _run_async(self._discover_dlna(bridge))

    def _on_scan_results(self, devices: list):
        import time as _t
        self._devices.extend(devices)
        self._pending_scans = max(0, self._pending_scans - 1)
        if self._pending_scans == 0:
            self._scan_time = _t.time()
        # Feed any open popup with newly discovered devices
        if self._popup and devices:
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
            name, avt, rc = await self._dlna_device_info(location)
            devices.append(DeviceInfo(id=usn, name=name, protocol='dlna',
                                      location=location, avt_url=avt, rc_url=rc))

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

            def _ctrl(svc):
                m = re.search(
                    rf'<serviceType>{re.escape(svc)}</serviceType>'
                    r'.*?<controlURL>([^<]+)</controlURL>',
                    xml, re.DOTALL,
                )
                return urljoin(base_url, m.group(1).strip()) if m else ''

            name_m = re.search(r'<friendlyName>([^<]+)</friendlyName>', xml)
            name   = name_m.group(1).strip() if name_m else location.split('/')[2]
            avt    = _ctrl('urn:schemas-upnp-org:service:AVTransport:1')
            rc     = _ctrl('urn:schemas-upnp-org:service:RenderingControl:1')
            print(f'[DLNA] Discovered {name!r}  AVT={avt}  RC={rc}')
            return name, avt, rc
        except Exception as e:
            print(f'[DLNA] device_info error for {location}: {e}')
            return location.split('/')[2], '', ''

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

    # ── Connection / disconnection (background threads) ───────────────────

    @staticmethod
    def _dlna_url(url: str) -> str:
        """Wrap URL through local proxy that injects DLNA streaming headers."""
        if not url:
            return url
        return _get_proxy().url_for(url)

    async def _dlna_play_chain(self, d: '_DLNADevice', url: str, track: dict,
                               pos_ms: int, paused: bool) -> int:
        """Async: play → seek → pause → return actual volume. Runs in cast loop."""
        await d.async_play_track(self._dlna_url(url), track)
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
            else:
                d = _DLNADevice(dev.location, avt_url=dev.avt_url, rc_url=dev.rc_url)

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
            else:
                # Chromecast: already blocking-connected; do play+seek in this thread
                import time
                if url:
                    d.play_track(url, track)
                    if pos_ms > 500:
                        deadline = time.time() + 10
                        while time.time() < deadline:
                            time.sleep(0.5)
                            try:
                                state = d._cc.media_controller.status.player_state
                                if state in ('PLAYING', 'PAUSED'):
                                    break
                            except Exception:
                                break
                        try:
                            d.seek(pos_ms / 1000.0)
                        except Exception as seek_err:
                            print(f'[Cast] Seek failed (ignored): {seek_err}')
                    if paused:
                        d.pause()
                vol = d.get_volume()
                if vol > 0:
                    self._ui_bridge.device_volume.emit(dev.id, vol)

        except Exception as e:
            import traceback
            print(f'[Cast] Connect failed: {e}')
            traceback.print_exc()
            self._active_devices.pop(dev.id, None)
            self._active_ids.discard(dev.id)
            if not self._active_devices:
                self._win._cast_connected = False
            self._refresh_ui()
            self._ui_bridge.device_state.emit(dev.id, False)

    def _disconnect_device(self, dev_id: str):
        dev_obj = self._active_devices.pop(dev_id, None)
        if dev_obj:
            try: dev_obj.stop()
            except Exception: pass
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
        QMessageBox.information(
            self._win, 'Cast unavailable',
            f"Install missing libraries:\n  pip install {' '.join(missing)}",
        )
