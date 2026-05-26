"""
now_playing_info.py — Rich "Now Playing" info tab.

Card-based layout:
  ① Track card (full width) — album art + title + meta + chips
  ② Bottom row:
       Left  — "FROM THIS ALBUM" card
       Right — "ABOUT THE ARTIST" card (top)
                "ON TOUR" card          (bottom)
"""

import re

_ARTIST_SEP = re.compile(r'\s*(?:///|•|feat\.|Feat\.|vs\.)\s*')

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QSizePolicy, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRectF, QTimer
from PyQt6.QtGui import QPixmap, QColor, QPainter, QPainterPath, QBrush, QPen, QFont, QFontMetrics

from player.mixins.visuals import resolve_menu_hover

# ── Caches ────────────────────────────────────────────────────────────────────
_artist_info_cache:  dict = {}
_artist_img_cache:   dict = {}
_top_songs_cache:    dict = {}
_album_tracks_cache: dict = {}
_bit_cache:          dict = {}

TOUR_LIMIT   = 5
ALBUM_SHOW_N = 5
BIO_LINES    = 4


# ── Workers ───────────────────────────────────────────────────────────────────

class _ArtistInfoWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, client, artist_id):
        super().__init__()
        self._client    = client
        self._artist_id = artist_id

    def run(self):
        if self._artist_id in _artist_info_cache:
            self.done.emit(_artist_info_cache[self._artist_id])
            return
        try:
            info = self._client.get_artist_info2(self._artist_id) or {}
        except Exception:
            info = {}
        _artist_info_cache[self._artist_id] = info
        self.done.emit(info)


class _ImageWorker(QThread):
    done = pyqtSignal(QPixmap)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        if self._url in _artist_img_cache:
            self.done.emit(_artist_img_cache[self._url])
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                self._url, headers={'User-Agent': 'Icosahedron/1.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            pix = QPixmap()
            pix.loadFromData(data)
            if not pix.isNull():
                _artist_img_cache[self._url] = pix
                self.done.emit(pix)
                return
        except Exception:
            pass
        self.done.emit(QPixmap())


class _AlbumTracksWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, client, album_id: str):
        super().__init__()
        self._client   = client
        self._album_id = album_id

    def run(self):
        if self._album_id in _album_tracks_cache:
            self.done.emit(_album_tracks_cache[self._album_id])
            return
        try:
            tracks = self._client.get_album_tracks(self._album_id) or []
        except Exception:
            tracks = []
        _album_tracks_cache[self._album_id] = tracks
        self.done.emit(tracks)


class _TopSongsWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, client, artist_name: str):
        super().__init__()
        self._client = client
        self._name   = artist_name

    def run(self):
        key = self._name.strip().lower()
        if key in _top_songs_cache:
            self.done.emit(_top_songs_cache[key])
            return
        try:
            tracks = self._client.get_top_songs(self._name, count=5) or []
        except Exception:
            tracks = []
        _top_songs_cache[key] = tracks
        self.done.emit(tracks)


class _BandsintownWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, artist_name: str):
        super().__init__()
        self._name = artist_name

    def run(self):
        key = self._name.strip().lower()
        if key in _bit_cache:
            self.done.emit(_bit_cache[key])
            return
        events = self._fetch(self._name)
        _bit_cache[key] = events
        self.done.emit(events)

    @staticmethod
    def _fetch(name: str) -> list:
        import urllib.request, urllib.parse, json
        try:
            encoded = urllib.parse.quote(name)
            url = (
                f"https://rest.bandsintown.com/artists/{encoded}/events"
                "?app_id=js_app_id"
            )
            req = urllib.request.Request(url, headers={'User-Agent': 'Icosahedron/1.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            if not isinstance(data, list):
                return []
            out = []
            for ev in data:
                venue = ev.get('venue', {})
                out.append({
                    'datetime':     ev.get('datetime', ''),
                    'venueName':    venue.get('name', ''),
                    'venueCity':    venue.get('city', ''),
                    'venueRegion':  venue.get('region', ''),
                    'venueCountry': venue.get('country', ''),
                    'url':          ev.get('url', ''),
                })
            return out
        except Exception:
            return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_qcolor(s: str) -> QColor:
    """Parse #rrggbb, #aarrggbb, or rgba(r,g,b,a) into a QColor."""
    s = s.strip()
    if s.startswith('rgba('):
        try:
            parts = s[5:-1].split(',')
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = int(float(parts[3])) if len(parts) > 3 else 255
            return QColor(r, g, b, a)
        except Exception:
            return QColor(42, 42, 42)
    c = QColor(s)
    return c if c.isValid() else QColor(42, 42, 42)


def _fmt_dur(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ''
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f'{h}:{m:02d}:{sec:02d}' if h else f'{m}:{sec:02d}'


def _fmt_total(secs) -> str:
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return ''
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h:
        return f'{h}h {m}m' if m else f'{h}h'
    return f'{m}m'


def _parse_dur(value) -> int:
    """Return duration in seconds from either an int/float or a M:SS / H:MM:SS string."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    try:
        parts = s.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(float(s))
    except (ValueError, IndexError):
        return 0


# ── Card base widget ──────────────────────────────────────────────────────────

class _Card(QWidget):
    _STYLE = 'QWidget#_Card {{ background: {bg}; border-radius: 10px; border: 1px solid {border}; }}'
    _DEFAULT_BG     = '#1e1e1e'
    _DEFAULT_BORDER = '#2a2a2a'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('_Card')
        self._bg     = self._DEFAULT_BG
        self._border = self._DEFAULT_BORDER
        self._refresh()

    def _refresh(self):
        self.setStyleSheet(self._STYLE.format(bg=self._bg, border=self._border))

    def set_border(self, border: str):
        self._border = border
        self._refresh()

    def set_bg(self, bg: str):
        self._bg = bg
        self._refresh()


# ── Rounded pixmap widget ─────────────────────────────────────────────────────

class _RoundedPixmapLabel(QWidget):
    def __init__(self, w: int, h: int, radius: int = 8, parent=None):
        super().__init__(parent)
        self._pix    = None
        self._radius = radius
        self.setFixedSize(w, h)

    def set_pixmap(self, pix: QPixmap):
        self._pix = pix
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), self._radius, self._radius)
        p.setClipPath(path)
        if self._pix and not self._pix.isNull():
            scaled = self._pix.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox = (scaled.width() - r.width()) // 2
            oy = (scaled.height() - r.height()) // 2
            p.drawPixmap(0, 0, scaled, ox, oy, r.width(), r.height())
        else:
            p.fillRect(r, QColor(45, 45, 45))
        p.end()


# ── Hover chip (similar artists) ─────────────────────────────────────────────

class _HoverChip(QWidget):
    clicked = pyqtSignal()

    def __init__(self, text: str, fg: str, border_color: str, font_size: int,
                 hover_color: QColor = None, parent=None):
        super().__init__(parent)
        self._hovered      = False
        self._hover_color  = hover_color or QColor(255, 255, 255, 25)
        self._border_color = _parse_qcolor(border_color)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(8, 3, 8, 3)
        lo.setSpacing(0)
        lbl = QLabel(text)
        lbl.setStyleSheet(f'color: {fg}; font-size: {font_size}px; background: transparent;')
        lo.addWidget(lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QBrush(QColor(255, 255, 255, 20)))
        p.setPen(QPen(self._border_color, 1))
        p.drawRoundedRect(r, 4, 4)
        if self._hovered:
            p.setBrush(QBrush(self._hover_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 4, 4)
        p.end()


# ── Track row ─────────────────────────────────────────────────────────────────

class _TrackRow(QWidget):
    play_requested = pyqtSignal(dict)

    def __init__(self, track: dict, index: int, accent: str,
                 fg: str, fg2: str, is_current: bool = False,
                 hover_color: QColor = None, parent=None):
        super().__init__(parent)
        self._track       = track
        self._hovered     = False
        self._hover_color = hover_color or QColor(255, 255, 255, 25)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(6, 0, 8, 0)
        lo.setSpacing(6)

        num_lbl = QLabel(str(index))
        num_lbl.setFixedWidth(22)
        num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num_lbl.setStyleSheet(
            f"color: {accent if is_current else fg2};"
            f" font-size: 11px; font-weight: {'bold' if is_current else 'normal'};"
            " background: transparent;"
        )
        lo.addWidget(num_lbl)

        title_lbl = QLabel(track.get('title', 'Unknown'))
        title_lbl.setStyleSheet(
            f"color: {accent if is_current else fg};"
            f" font-size: 12px; font-weight: {'bold' if is_current else 'normal'};"
            " background: transparent;"
        )
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lo.addWidget(title_lbl, 1)

        dur = _fmt_dur(track.get('duration', ''))
        if dur:
            dur_lbl = QLabel(dur)
            dur_lbl.setFixedWidth(38)
            dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dur_lbl.setStyleSheet(f"color: {fg2}; font-size: 11px; background: transparent;")
            lo.addWidget(dur_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.play_requested.emit(self._track)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QBrush(self._hover_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 4, 4)
            p.end()
        super().paintEvent(event)


# ── Top-song row (title + album subtitle) ─────────────────────────────────────

class _TopSongRow(QWidget):
    play_requested = pyqtSignal(dict)

    def __init__(self, track: dict, index: int, accent: str,
                 fg: str, fg2: str, is_current: bool = False,
                 hover_color: QColor = None, parent=None):
        super().__init__(parent)
        self._track       = track
        self._hovered     = False
        self._hover_color = hover_color or QColor(255, 255, 255, 25)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(6, 0, 8, 0)
        lo.setSpacing(6)

        num_lbl = QLabel(str(index))
        num_lbl.setFixedWidth(22)
        num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num_lbl.setStyleSheet(
            f"color: {accent if is_current else fg2};"
            f" font-size: 11px; font-weight: {'bold' if is_current else 'normal'};"
            " background: transparent;"
        )
        lo.addWidget(num_lbl)

        text_w = QWidget()
        text_w.setStyleSheet('background: transparent;')
        text_lo = QVBoxLayout(text_w)
        text_lo.setContentsMargins(0, 0, 0, 0)
        text_lo.setSpacing(1)

        title_lbl = QLabel(track.get('title', 'Unknown'))
        title_lbl.setStyleSheet(
            f"color: {accent if is_current else fg};"
            f" font-size: 12px; font-weight: {'bold' if is_current else 'normal'};"
            " background: transparent;"
        )
        text_lo.addWidget(title_lbl)

        album = track.get('album', '')
        if album:
            album_lbl = QLabel(album)
            album_lbl.setStyleSheet(
                f"color: {fg2}; font-size: 10px; background: transparent;"
            )
            text_lo.addWidget(album_lbl)

        lo.addWidget(text_w, 1)

        dur = _fmt_dur(track.get('duration', ''))
        if dur:
            dur_lbl = QLabel(dur)
            dur_lbl.setFixedWidth(38)
            dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dur_lbl.setStyleSheet(f"color: {fg2}; font-size: 11px; background: transparent;")
            lo.addWidget(dur_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.play_requested.emit(self._track)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QBrush(self._hover_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 4, 4)
            p.end()
        super().paintEvent(event)


# ── Tour date row ─────────────────────────────────────────────────────────────

class _TourRow(QWidget):
    def __init__(self, event: dict, accent: str, fg: str, fg2: str,
                 hover_color: QColor = None, parent=None):
        super().__init__(parent)
        self._url         = event.get('url', '')
        self._hovered     = False
        self._hover_color = hover_color or QColor(255, 255, 255, 25)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(50)
        if self._url:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(0, 2, 0, 2)
        lo.setSpacing(10)

        dt = event.get('datetime', '')
        month = day = ''
        if dt:
            try:
                from datetime import datetime as _dt
                d     = _dt.fromisoformat(dt.replace('Z', '+00:00'))
                month = d.strftime('%b').upper()
                day   = str(d.day)
            except Exception:
                pass

        cal = QWidget()
        cal.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cal.setFixedSize(38, 42)
        cal.setStyleSheet('background: rgba(255,255,255,0.07); border-radius: 6px;')
        cal_lo = QVBoxLayout(cal)
        cal_lo.setContentsMargins(0, 3, 0, 3)
        cal_lo.setSpacing(0)
        m_lbl = QLabel(month)
        m_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lbl.setStyleSheet(
            f'color: {accent}; font-size: 9px; font-weight: bold; background: transparent;'
        )
        d_lbl = QLabel(day)
        d_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d_lbl.setStyleSheet(
            f'color: {fg}; font-size: 14px; font-weight: bold; background: transparent;'
        )
        cal_lo.addWidget(m_lbl)
        cal_lo.addWidget(d_lbl)
        lo.addWidget(cal)

        meta_w = QWidget()
        meta_w.setStyleSheet('background: transparent;')
        meta_lo = QVBoxLayout(meta_w)
        meta_lo.setContentsMargins(0, 0, 0, 0)
        meta_lo.setSpacing(1)
        venue_lbl = QLabel(event.get('venueName', '') or 'TBA')
        venue_lbl.setStyleSheet(f'color: {fg}; font-size: 12px; background: transparent;')
        meta_lo.addWidget(venue_lbl)
        place = ', '.join(
            p for p in [
                event.get('venueCity'), event.get('venueRegion'), event.get('venueCountry')
            ] if p
        )
        place_lbl = QLabel(place)
        place_lbl.setStyleSheet(f'color: {fg2}; font-size: 10px; background: transparent;')
        meta_lo.addWidget(place_lbl)
        lo.addWidget(meta_w, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._url:
            try:
                import subprocess, sys
                if sys.platform == 'win32':
                    subprocess.Popen(['start', self._url], shell=True)
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open', self._url])
                else:
                    subprocess.Popen(['xdg-open', self._url])
            except Exception:
                pass
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QBrush(self._hover_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 6, 6)
            p.end()
        super().paintEvent(event)


# ── Main panel ────────────────────────────────────────────────────────────────

class NowPlayingInfoTab(QWidget):
    """Rich info panel for the Now Playing tab.

    Public API
    ──────────
    set_client(client)
    load_track(track: dict)
    set_accent_color(color: str)
    apply_theme(theme)
    set_bg_color(color: str)
    """

    artist_clicked = pyqtSignal(str)
    album_clicked  = pyqtSignal(dict)
    play_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client        = None
        self._current_track: dict = {}
        self._accent        = '#cccccc'
        self._fg            = '#dddddd'
        self._fg2           = '#888888'
        self._bg            = '14,14,14'
        self._border_color  = '#2a2a2a'
        self._card_bg       = '#1e1e1e'
        self._hover_color         = QColor(255, 255, 255, 25)
        self._font_size_secondary = 12
        self._settings            = QSettings('Icosahedron', 'Icosahedron')
        self._top_artists: list[str] = []
        self._top_page_idx: int      = 0
        self._pending_track: dict | None = None

        self._w_info:  _ArtistInfoWorker  | None = None
        self._w_img:   _ImageWorker       | None = None
        self._w_cover: _ImageWorker       | None = None
        self._w_album: _AlbumTracksWorker | None = None
        self._w_bit:   _BandsintownWorker | None = None
        self._w_top:   _TopSongsWorker    | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('NowPlayingInfoTab')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        outer.addWidget(self._scroll)

        self._content = QWidget()
        self._content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content.setObjectName('NPContent')
        self._content.setStyleSheet('QWidget#NPContent { background: transparent; }')
        root = QVBoxLayout(self._content)
        root.setContentsMargins(12, 12, 12, 24)
        root.setSpacing(10)
        self._scroll.setWidget(self._content)

        # ── Track card (full width) ───────────────────────────────────
        self._track_card    = _Card()
        self._track_card_lo = QHBoxLayout(self._track_card)
        self._track_card_lo.setContentsMargins(14, 14, 14, 14)
        self._track_card_lo.setSpacing(14)
        root.addWidget(self._track_card)

        # ── Two-column layout ─────────────────────────────────────────
        # Left col:  album card → top songs card
        # Right col: artist card → tour card
        self._album_card    = _Card()
        self._album_card_lo = QVBoxLayout(self._album_card)
        self._album_card_lo.setContentsMargins(14, 14, 14, 14)
        self._album_card_lo.setSpacing(4)

        self._top_card    = _Card()
        self._top_card_lo = QVBoxLayout(self._top_card)
        self._top_card_lo.setContentsMargins(14, 14, 14, 14)
        self._top_card_lo.setSpacing(4)

        self._artist_card    = _Card()
        self._artist_card_lo = QVBoxLayout(self._artist_card)
        self._artist_card_lo.setContentsMargins(14, 14, 14, 14)
        self._artist_card_lo.setSpacing(6)

        self._tour_card    = _Card()
        self._tour_card_lo = QVBoxLayout(self._tour_card)
        self._tour_card_lo.setContentsMargins(14, 14, 14, 14)
        self._tour_card_lo.setSpacing(4)

        left_col_w  = QWidget()
        left_col_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_col_w.setStyleSheet('background: transparent;')
        left_col_lo = QVBoxLayout(left_col_w)
        left_col_lo.setContentsMargins(0, 0, 0, 0)
        left_col_lo.setSpacing(10)
        left_col_lo.addWidget(self._album_card)
        left_col_lo.addWidget(self._top_card)
        left_col_lo.addStretch(1)

        right_col_w  = QWidget()
        right_col_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_col_w.setStyleSheet('background: transparent;')
        right_col_lo = QVBoxLayout(right_col_w)
        right_col_lo.setContentsMargins(0, 0, 0, 0)
        right_col_lo.setSpacing(10)
        right_col_lo.addWidget(self._artist_card)
        right_col_lo.addWidget(self._tour_card)
        right_col_lo.addStretch(1)

        cols_w  = QWidget()
        cols_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cols_w.setStyleSheet('background: transparent;')
        cols_lo = QHBoxLayout(cols_w)
        cols_lo.setContentsMargins(0, 0, 0, 0)
        cols_lo.setSpacing(10)
        cols_lo.addWidget(left_col_w,  1)
        cols_lo.addWidget(right_col_w, 1)
        root.addWidget(cols_w)

        root.addStretch(1)

        self._cover_art_lbl:    _RoundedPixmapLabel | None = None
        self._artist_photo_lbl: _RoundedPixmapLabel | None = None

        self._show_empty('No track playing')

    # ── Public API ─────────────────────────────────────────────────────

    def set_client(self, client):
        self._client = client

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_track is not None:
            track, self._pending_track = self._pending_track, None
            self.load_track(track)

    def load_track(self, track: dict):
        if not self.isVisible():
            self._pending_track = track
            return
        self._pending_track = None
        tid = track.get('id')
        if tid and tid == self._current_track.get('id'):
            return
        self._current_track = track
        self._cancel_workers()

        self._build_track_card(track)
        self._build_album_card([], None)
        self._build_artist_card({})
        self._build_tour_card([])

        artist_name = track.get('artist', '')
        parts = [p.strip() for p in _ARTIST_SEP.split(artist_name) if p.strip()]
        self._top_artists   = parts if parts else ([artist_name] if artist_name else [])
        self._top_page_idx  = 0
        self._build_top_card([])

        if not self._client:
            return

        artist_id = track.get('artist_id') or track.get('artistId')
        album_id  = track.get('albumId')    or track.get('album_id')
        cover_id  = track.get('cover_id')   or track.get('coverArt')

        if artist_id:
            self._w_info = _ArtistInfoWorker(self._client, artist_id)
            self._w_info.done.connect(self._on_artist_info)
            self._w_info.start()

        if album_id:
            self._w_album = _AlbumTracksWorker(self._client, album_id)
            self._w_album.done.connect(self._on_album_tracks)
            self._w_album.start()

        if cover_id:
            try:
                url = self._client.get_cover_art_url(cover_id, 400)
                if url in _artist_img_cache:
                    if self._cover_art_lbl:
                        self._cover_art_lbl.set_pixmap(_artist_img_cache[url])
                else:
                    self._w_cover = _ImageWorker(url)
                    self._w_cover.done.connect(self._on_cover_art)
                    self._w_cover.start()
            except Exception:
                pass

        if artist_name and self._bit_enabled():
            self._w_bit = _BandsintownWorker(artist_name)
            self._w_bit.done.connect(self._on_tour_events)
            self._w_bit.start()

        if self._top_artists:
            first = self._top_artists[0]
            key   = first.strip().lower()
            if key in _top_songs_cache:
                self._build_top_card(_top_songs_cache[key])
            else:
                self._w_top = _TopSongsWorker(self._client, first)
                self._w_top.done.connect(self._on_top_songs)
                self._w_top.start()

    def set_accent_color(self, color: str):
        self._accent = color
        self._scroll.verticalScrollBar().setStyleSheet(
            'QScrollBar:vertical { border: none; background: transparent; width: 4px; margin: 0; }'
            f'QScrollBar::handle:vertical {{ background: {color}; border-radius: 2px; min-height: 20px; }}'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }'
        )

    def apply_theme(self, theme):
        if theme is None:
            return
        self._accent              = getattr(theme, 'accent', self._accent)
        self._fg                  = getattr(theme, 'font_color_primary', self._fg)
        self._fg2                 = getattr(theme, 'font_color_secondary', self._fg2)
        self._font_size_secondary = getattr(theme, 'font_size_secondary', self._font_size_secondary)
        self._border_color        = getattr(theme, 'border_color', self._border_color)
        self._card_bg      = getattr(theme, 'now_playing_card_bg', self._card_bg)
        raw_bg = getattr(theme, 'main_panel_bg', self._bg)
        self._bg = str(raw_bg)
        self.setStyleSheet(f'#NowPlayingInfoTab {{ background: rgb({self._bg}); }}')
        for card in (self._track_card, self._top_card,
                     self._album_card, self._artist_card, self._tour_card):
            card.set_border(self._border_color)
            card.set_bg(self._card_bg)
        hover_str = resolve_menu_hover(theme)
        self._hover_color = _parse_qcolor(hover_str)

    def set_bg_color(self, color: str):
        self._bg = color
        self.setStyleSheet(f'#NowPlayingInfoTab {{ background: rgb({color}); }}')

    # ── Slots ──────────────────────────────────────────────────────────

    def _on_artist_info(self, info: dict):
        bio_raw = info.get('biography') or info.get('bio') or ''
        bio     = re.sub(r'<a [^>]*>.*?</a>\.?', '', bio_raw, flags=re.IGNORECASE).strip()
        similar = info.get('similarArtist', []) or []
        self._build_artist_card(info, bio=bio, similar=similar)

        img_url = (
            info.get('largeImageUrl') or
            info.get('mediumImageUrl') or
            info.get('smallImageUrl') or ''
        )
        if img_url:
            if img_url in _artist_img_cache:
                if self._artist_photo_lbl:
                    self._artist_photo_lbl.set_pixmap(_artist_img_cache[img_url])
            else:
                self._w_img = _ImageWorker(img_url)
                self._w_img.done.connect(self._on_artist_img)
                self._w_img.start()

    def _on_artist_img(self, pix: QPixmap):
        if self._artist_photo_lbl and not pix.isNull():
            self._artist_photo_lbl.set_pixmap(pix)

    def _on_cover_art(self, pix: QPixmap):
        if self._cover_art_lbl and not pix.isNull():
            self._cover_art_lbl.set_pixmap(pix)

    def _on_album_tracks(self, tracks: list):
        self._build_album_card(tracks, self._current_track.get('id'))

    def _on_tour_events(self, events: list):
        self._build_tour_card(events)

    def _on_top_songs(self, tracks: list):
        self._build_top_card(tracks)

    # ── Card builders ──────────────────────────────────────────────────

    def _build_track_card(self, track: dict):
        self._clear_layout(self._track_card_lo)
        self._cover_art_lbl = None

        art = _RoundedPixmapLabel(176, 176, radius=10)
        self._cover_art_lbl = art
        self._track_card_lo.addWidget(art)

        right_w = QWidget()
        right_w.setStyleSheet('background: transparent;')
        right_lo = QVBoxLayout(right_w)
        right_lo.setContentsMargins(0, 0, 0, 0)
        right_lo.setSpacing(4)
        self._track_card_lo.addWidget(right_w, 1)

        title = track.get('title', 'Unknown')
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(
            f'color: {self._fg}; font-size: 17px; font-weight: bold; background: transparent;'
        )
        right_lo.addWidget(t_lbl)

        artist = track.get('artist', '')
        album  = track.get('album', '')
        year   = str(track.get('year', '') or '')
        meta_parts = [p for p in [artist, album, year] if p]
        meta_lbl = QLabel(' • '.join(meta_parts))
        meta_lbl.setStyleSheet(
            f'color: {self._fg2}; font-size: 12px; background: transparent;'
        )
        right_lo.addWidget(meta_lbl)

        # Chip row
        chips_w = QWidget()
        chips_w.setStyleSheet('background: transparent;')
        chips_lo = QHBoxLayout(chips_w)
        chips_lo.setContentsMargins(0, 2, 0, 0)
        chips_lo.setSpacing(5)

        genre   = track.get('genre', '')
        bitrate = track.get('bitRate') or track.get('bit_rate')
        dur     = _fmt_dur(track.get('duration', ''))

        if genre:
            chips_lo.addWidget(self._chip(genre))
        if bitrate:
            try:
                br  = int(bitrate)
                fmt = 'FLAC' if br > 900 else 'MP3'
                chips_lo.addWidget(self._chip(fmt))
                chips_lo.addWidget(self._chip(f'{br} kbps'))
            except (TypeError, ValueError):
                pass
        if dur:
            chips_lo.addWidget(self._chip(dur))
        chips_lo.addStretch(1)
        right_lo.addWidget(chips_w)

        right_lo.addStretch(1)

    def _build_album_card(self, tracks: list, current_id):
        self._clear_layout(self._album_card_lo)

        hdr = QLabel('FROM THIS ALBUM')
        hdr.setStyleSheet(
            f'color: {self._accent}; font-size: 10px; font-weight: bold;'
            ' letter-spacing: 1.5px; background: transparent;'
        )
        self._album_card_lo.addWidget(hdr)

        if not tracks:
            ph = QLabel('Loading…')
            ph.setStyleSheet(
                f'color: {self._fg2}; font-size: 11px; background: transparent; padding: 6px 0;'
            )
            self._album_card_lo.addWidget(ph)
            return

        tracks = sorted(
            tracks,
            key=lambda t: (int(t.get('discNumber', 1) or 1),
                           int(t.get('trackNumber', 0) or 0)),
        )

        album_name = tracks[0].get('album', '') if tracks else ''
        if album_name:
            alb_lbl = QLabel(album_name)
            alb_lbl.setStyleSheet(
                f'color: {self._fg}; font-size: 12px; font-weight: bold; background: transparent;'
            )
            self._album_card_lo.addWidget(alb_lbl)

        current_num = 0
        total_secs  = 0
        for tr in tracks:
            total_secs += _parse_dur(tr.get('duration', 0))
            if tr.get('id') == current_id:
                current_num = int(tr.get('trackNumber', 0) or 0)

        stats_parts = []
        if current_num:
            stats_parts.append(f'Track {current_num} of {len(tracks)}')
        else:
            stats_parts.append(f'{len(tracks)} tracks')
        if total_secs:
            stats_parts.append(_fmt_total(total_secs))

        stats_lbl = QLabel(' · '.join(stats_parts))
        stats_lbl.setStyleSheet(
            f'color: {self._fg2}; font-size: 11px; background: transparent; padding-bottom: 2px;'
        )
        self._album_card_lo.addWidget(stats_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            'border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 2px 0;'
        )
        sep.setFixedHeight(1)
        self._album_card_lo.addWidget(sep)

        # Compute a window of ALBUM_SHOW_N tracks centred on the current track.
        current_idx = next(
            (i for i, t in enumerate(tracks) if t.get('id') == current_id),
            None,
        )
        if current_idx is not None and len(tracks) > ALBUM_SHOW_N:
            half  = ALBUM_SHOW_N // 2
            start = max(0, current_idx - half)
            end   = start + ALBUM_SHOW_N
            if end > len(tracks):
                end   = len(tracks)
                start = max(0, end - ALBUM_SHOW_N)
        else:
            start = 0
            end   = min(ALBUM_SHOW_N, len(tracks))

        hidden_rows = []
        for i, tr in enumerate(tracks):
            row = _TrackRow(
                tr,
                int(tr.get('trackNumber', 0) or 0) or (i + 1),
                accent=self._accent, fg=self._fg, fg2=self._fg2,
                is_current=(tr.get('id') == current_id),
                hover_color=self._hover_color,
            )
            row.play_requested.connect(self.play_requested)
            if not (start <= i < end):
                row.hide()
                hidden_rows.append(row)
            self._album_card_lo.addWidget(row)

        if hidden_rows:
            show_state = [False]
            n          = len(hidden_rows)
            more_btn   = QPushButton(f'Show {n} more')
            more_btn.setFlat(True)
            more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            more_btn.setStyleSheet(
                f'QPushButton {{ color: {self._accent}; font-size: 11px; background: transparent;'
                f' border: none; text-align: left; padding: 4px 0px; }}'
                f'QPushButton:hover {{ color: {self._fg}; }}'
            )

            def _toggle_album(*, rows=hidden_rows, btn=more_btn, count=n):
                show_state[0] = not show_state[0]
                for r in rows:
                    r.setVisible(show_state[0])
                btn.setText('Show less' if show_state[0] else f'Show {count} more')

            more_btn.clicked.connect(_toggle_album)
            self._album_card_lo.addWidget(more_btn)

    def _build_top_card(self, tracks: list):
        self._clear_layout(self._top_card_lo)

        n   = len(self._top_artists)
        idx = self._top_page_idx
        name = self._top_artists[idx] if self._top_artists else ''

        # Header row
        hrow = QWidget()
        hrow.setStyleSheet('background: transparent;')
        hrow_lo = QHBoxLayout(hrow)
        hrow_lo.setContentsMargins(0, 0, 0, 0)
        hrow_lo.setSpacing(4)

        hdr = QLabel('MOST PLAYED BY THIS ARTIST')
        hdr.setStyleSheet(
            f'color: {self._accent}; font-size: 10px; font-weight: bold;'
            ' letter-spacing: 1.5px; background: transparent;'
        )
        hrow_lo.addWidget(hdr)
        hrow_lo.addStretch(1)

        if n > 1:
            btn_style = (
                f'QPushButton {{ color: {self._accent}; background: transparent; border: none;'
                f' font-size: 16px; padding: 0 2px; }}'
                f'QPushButton:hover {{ color: white; }}'
                f'QPushButton:disabled {{ color: #444; }}'
            )
            btn_prev = QPushButton('‹')
            btn_prev.setFixedSize(22, 18)
            btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_prev.setStyleSheet(btn_style)
            btn_prev.setEnabled(idx > 0)
            btn_prev.clicked.connect(lambda: self._nav_top_page(self._top_page_idx - 1))

            page_lbl = QLabel(f'{idx + 1}/{n}')
            page_lbl.setStyleSheet(
                f'color: {self._fg2}; font-size: 11px; font-weight: bold; background: transparent;'
            )

            btn_next = QPushButton('›')
            btn_next.setFixedSize(22, 18)
            btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_next.setStyleSheet(btn_style)
            btn_next.setEnabled(idx < n - 1)
            btn_next.clicked.connect(lambda: self._nav_top_page(self._top_page_idx + 1))

            hrow_lo.addWidget(btn_prev)
            hrow_lo.addWidget(page_lbl)
            hrow_lo.addWidget(btn_next)

        if name:
            go_btn = QPushButton('Go to Artist ↗')
            go_btn.setFlat(True)
            go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            go_btn.setStyleSheet(
                f'QPushButton {{ color: {self._fg2}; font-size: 11px; background: transparent;'
                f' border: none; padding: 0 0 0 6px; }}'
                f'QPushButton:hover {{ color: {self._accent}; }}'
            )
            go_btn.clicked.connect(lambda: self.artist_clicked.emit(name))
            hrow_lo.addWidget(go_btn)

        self._top_card_lo.addWidget(hrow)

        if not tracks:
            ph = QLabel('Loading…')
            ph.setStyleSheet(
                f'color: {self._fg2}; font-size: 11px; background: transparent; padding: 6px 0;'
            )
            self._top_card_lo.addWidget(ph)
            return

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            'border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 2px 0;'
        )
        sep.setFixedHeight(1)
        self._top_card_lo.addWidget(sep)

        current_id = self._current_track.get('id')
        for i, tr in enumerate(tracks[:5]):
            row = _TopSongRow(
                tr, i + 1,
                accent=self._accent, fg=self._fg, fg2=self._fg2,
                is_current=(tr.get('id') == current_id),
                hover_color=self._hover_color,
            )
            row.play_requested.connect(self.play_requested)
            self._top_card_lo.addWidget(row)

        if name:
            credit = QLabel(f'Top tracks from {name}')
            credit.setStyleSheet(
                f'color: {self._fg2}; font-size: 10px; background: transparent; padding-top: 4px;'
            )
            self._top_card_lo.addWidget(credit)

    def _nav_top_page(self, idx: int):
        if not (0 <= idx < len(self._top_artists)):
            return
        self._top_page_idx = idx
        artist_name = self._top_artists[idx]
        key = artist_name.strip().lower()
        if key in _top_songs_cache:
            self._build_top_card(_top_songs_cache[key])
            return
        self._build_top_card([])
        if self._w_top and self._w_top.isRunning():
            try:
                self._w_top.done.disconnect()
            except Exception:
                pass
            self._w_top.quit()
        self._w_top = _TopSongsWorker(self._client, artist_name)
        self._w_top.done.connect(self._on_top_songs)
        self._w_top.start()

    def _build_artist_card(self, info: dict, bio: str = '', similar: list = None):
        self._clear_layout(self._artist_card_lo)
        self._artist_photo_lbl = None

        hdr = QLabel('ABOUT THE ARTIST')
        hdr.setStyleSheet(
            f'color: {self._accent}; font-size: 10px; font-weight: bold;'
            ' letter-spacing: 1.5px; background: transparent;'
        )
        self._artist_card_lo.addWidget(hdr)

        # Photo + name row
        namerow_w = QWidget()
        namerow_w.setStyleSheet('background: transparent;')
        namerow_lo = QHBoxLayout(namerow_w)
        namerow_lo.setContentsMargins(0, 0, 0, 0)
        namerow_lo.setSpacing(10)

        photo = _RoundedPixmapLabel(88, 88, radius=44)
        self._artist_photo_lbl = photo
        namerow_lo.addWidget(photo)

        artist_name = (
            self._current_track.get('artist', '') or
            info.get('name', '')
        )
        name_lbl = QLabel(artist_name)
        name_lbl.setStyleSheet(
            f'color: {self._fg}; font-size: 14px; font-weight: bold; background: transparent;'
        )
        namerow_lo.addWidget(name_lbl, 1)
        self._artist_card_lo.addWidget(namerow_w)

        if not bio:
            return

        bio_lbl = QLabel(bio)
        bio_lbl.setWordWrap(True)
        bio_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        bio_lbl.setStyleSheet(
            f'color: {self._fg2}; font-size: {self._font_size_secondary}px;'
            f' background: transparent; line-height: 150%;'
        )
        bio_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        _mf   = QFont()
        _mf.setPixelSize(self._font_size_secondary)
        fm    = QFontMetrics(_mf)
        max_h = fm.lineSpacing() * BIO_LINES + fm.leading()
        bio_lbl.setMaximumHeight(max_h)
        self._artist_card_lo.addWidget(bio_lbl)

        # Placeholder inserted here so the toggle button sits above similar artists
        toggle_slot_w  = QWidget()
        toggle_slot_w.setStyleSheet('background: transparent;')
        toggle_slot_lo = QVBoxLayout(toggle_slot_w)
        toggle_slot_lo.setContentsMargins(0, 0, 0, 0)
        toggle_slot_lo.setSpacing(0)
        self._artist_card_lo.addWidget(toggle_slot_w)

        expanded = [False]
        accent   = self._accent
        fg       = self._fg

        def _check_overflow():
            try:
                if not bio_lbl or bio_lbl.sizeHint().height() <= max_h + 2:
                    return
            except RuntimeError:
                return
            toggle = QPushButton('Read more')
            toggle.setFlat(True)
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.setStyleSheet(
                f'QPushButton {{ color: {accent}; font-size: 11px; background: transparent;'
                f' border: none; text-align: left; padding: 2px 0px; }}'
                f'QPushButton:hover {{ color: {fg}; }}'
            )

            def _do_toggle():
                try:
                    expanded[0] = not expanded[0]
                    bio_lbl.setMaximumHeight(16_777_215 if expanded[0] else max_h)
                    toggle.setText('Show less' if expanded[0] else 'Read more')
                except RuntimeError:
                    pass

            toggle.clicked.connect(_do_toggle)
            try:
                toggle_slot_lo.addWidget(toggle)
            except RuntimeError:
                toggle.deleteLater()

        QTimer.singleShot(0, _check_overflow)

        if similar:
            sim_w = QWidget()
            sim_w.setStyleSheet('background: transparent;')
            sim_lo = QHBoxLayout(sim_w)
            sim_lo.setContentsMargins(0, 4, 0, 0)
            sim_lo.setSpacing(5)
            for item in (similar or [])[:6]:
                name = item.get('name', '') if isinstance(item, dict) else str(item)
                if name:
                    chip = _HoverChip(
                        name, self._fg2, self._border_color,
                        self._font_size_secondary, self._hover_color,
                    )
                    chip.clicked.connect(lambda _name=name: self.artist_clicked.emit(_name))
                    sim_lo.addWidget(chip)
            sim_lo.addStretch(1)
            self._artist_card_lo.addWidget(sim_w)

    def _build_tour_card(self, events: list):
        self._clear_layout(self._tour_card_lo)

        hdr = QLabel('ON TOUR')
        hdr.setStyleSheet(
            f'color: {self._accent}; font-size: 10px; font-weight: bold;'
            ' letter-spacing: 1.5px; background: transparent;'
        )
        self._tour_card_lo.addWidget(hdr)

        if not self._bit_enabled():
            self._tour_card_lo.addWidget(self._make_tour_optin())
            return

        if not events:
            lbl = QLabel('No upcoming shows')
            lbl.setStyleSheet(
                f'color: {self._fg2}; font-size: 12px; background: transparent; padding: 6px 0;'
            )
            self._tour_card_lo.addWidget(lbl)
            return

        visible   = events[:TOUR_LIMIT]
        hidden_ev = events[TOUR_LIMIT:]

        for ev in visible:
            self._tour_card_lo.addWidget(
                _TourRow(ev, accent=self._accent, fg=self._fg, fg2=self._fg2,
                         hover_color=self._hover_color)
            )

        hidden_rows = []
        for ev in hidden_ev:
            row = _TourRow(ev, accent=self._accent, fg=self._fg, fg2=self._fg2,
                           hover_color=self._hover_color)
            row.hide()
            self._tour_card_lo.addWidget(row)
            hidden_rows.append(row)

        if hidden_rows:
            show_state = [False]
            n          = len(hidden_rows)
            more_btn   = QPushButton(f'Show {n} more')
            more_btn.setFlat(True)
            more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            more_btn.setStyleSheet(
                f'QPushButton {{ color: {self._accent}; font-size: 11px; background: transparent;'
                f' border: none; text-align: left; padding: 4px 0px; }}'
            )

            def _toggle_tour(*, rows=hidden_rows, btn=more_btn, count=n):
                show_state[0] = not show_state[0]
                for r in rows:
                    r.setVisible(show_state[0])
                btn.setText('Show less' if show_state[0] else f'Show {count} more')

            more_btn.clicked.connect(_toggle_tour)
            self._tour_card_lo.addWidget(more_btn)

        credit = QLabel('Tour data via Bandsintown')
        credit.setStyleSheet(
            f'color: {self._fg2}; font-size: 10px; background: transparent; padding-top: 4px;'
        )
        self._tour_card_lo.addWidget(credit)

    def _make_tour_optin(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet('background: transparent;')
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 4, 0, 0)
        lo.setSpacing(6)

        title = QLabel('See upcoming shows?')
        title.setStyleSheet(
            f'color: {self._fg}; font-size: 12px; font-weight: bold; background: transparent;'
        )
        lo.addWidget(title)

        desc = QLabel(
            'Loads tour dates from Bandsintown.\n'
            'Only the artist name leaves your device.'
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f'color: {self._fg2}; font-size: 11px; background: transparent;')
        lo.addWidget(desc)

        btn = QPushButton('Enable tour dates')
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.setStyleSheet(
            f'QPushButton {{ background: {self._accent}; color: #111; border: none;'
            f' border-radius: 6px; font-size: 12px; font-weight: bold; padding: 0px 16px; }}'
            f'QPushButton:hover {{ background: {self._accent}; }}'
        )
        btn.clicked.connect(self._enable_bandsintown)
        lo.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return w

    # ── Utilities ──────────────────────────────────────────────────────

    def _chip(self, text: str, font_size: int = 10) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f'color: {self._fg2}; background: rgba(255,255,255,0.08); border-radius: 4px;'
            f' font-size: {font_size}px; padding: 2px 8px;'
        )
        return lbl

    def _clear_layout(self, lo):
        while lo.count():
            item = lo.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _show_empty(self, msg: str):
        for lo in (self._track_card_lo, self._top_card_lo,
                   self._album_card_lo, self._artist_card_lo, self._tour_card_lo):
            self._clear_layout(lo)
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f'color: {self._fg2}; font-size: 13px; background: transparent; padding: 16px;'
        )
        self._track_card_lo.addWidget(lbl)

    def _bit_enabled(self) -> bool:
        return bool(int(self._settings.value('bandsintown_enabled', 0) or 0))

    def _enable_bandsintown(self):
        self._settings.setValue('bandsintown_enabled', 1)
        artist_name = self._current_track.get('artist', '')
        if artist_name:
            self._w_bit = _BandsintownWorker(artist_name)
            self._w_bit.done.connect(self._on_tour_events)
            self._w_bit.start()

    def _cancel_workers(self):
        for attr in ('_w_info', '_w_img', '_w_cover', '_w_album', '_w_bit', '_w_top'):
            w = getattr(self, attr, None)
            if w and w.isRunning():
                try:
                    w.done.disconnect()
                except Exception:
                    pass
                w.quit()
            setattr(self, attr, None)
