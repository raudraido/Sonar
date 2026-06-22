"""
now_playing_info.py — Rich "Now Playing" info tab. QML (now_playing.qml),
per UI_MANIFEST.md — track card, album-tracks card, top-songs card, artist
card (bio + similar artists, paginated), tour card (Bandsintown). The data
layer (6 background workers + the cover-zoom overlay) is unchanged from the
QWidget version; only the rendering moved to QML.
"""

import re
import time

_ARTIST_SEP = re.compile(r'\s*(?:///|•|feat\.|Feat\.|vs\.)\s*')
_GENRE_SEP  = re.compile(r' /// | • | / |,\s*|;\s*')
# Capturing group keeps the separator text itself (" feat. ", " vs. ", " • ",
# " /// ", " / ") in the split result, so the original wording survives —
# mirrors the JS regex TrackListView.qml uses for the same artist string.
_ARTIST_TOKEN_RE = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. )')

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, pyqtSlot, pyqtProperty, QSettings, QTimer,
                          QObject, QUrl, QEvent)
from PyQt6.QtGui import QPixmap, QColor, QPainter, QIcon
from PyQt6.QtQuick import QQuickView

from player import resource_path
from player.mixins.visuals import resolve_menu_hover
from player.widgets import AlbumIconProvider, AlbumDetailCoverProvider, PixmapImageProvider
from player.scroll_tuning import scroll_tuning

# ── Caches ────────────────────────────────────────────────────────────────────
_artist_info_cache:  dict = {}
_artist_img_cache:   dict = {}
_top_songs_cache:    dict = {}
_album_tracks_cache: dict = {}
_bit_cache:          dict = {}

TOUR_LIMIT   = 5
ALBUM_SHOW_N = 5


# ── Workers ───────────────────────────────────────────────────────────────────

class _ArtistInfoWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, client, artist_id=None, artist_name=None):
        super().__init__()
        self._client      = client
        self._artist_id   = artist_id
        self._artist_name = artist_name

    def run(self):
        aid = self._artist_id
        if not aid and self._artist_name:
            cache_key = f'name:{self._artist_name.strip().lower()}'
            if cache_key in _artist_info_cache:
                self.done.emit(_artist_info_cache[cache_key])
                return
            try:
                result = self._client.search3(self._artist_name, artist_count=1, album_count=0, size=0) or {}
                artists = result.get('artist', [])
                if artists:
                    aid = artists[0].get('id')
            except Exception:
                pass
            if not aid:
                _artist_info_cache[cache_key] = {}
                self.done.emit({})
                return

        if aid in _artist_info_cache:
            self.done.emit(_artist_info_cache[aid])
            return
        try:
            info = self._client.get_artist_info2(aid) or {}
        except Exception:
            info = {}
        _artist_info_cache[aid] = info
        if self._artist_name:
            _artist_info_cache[f'name:{self._artist_name.strip().lower()}'] = info
        self.done.emit(info)


class _ImageWorker(QThread):
    done = pyqtSignal(QPixmap)

    def __init__(self, url: str, cover_id: str | None = None):
        super().__init__()
        self._url      = url
        self._cover_id = cover_id

    def run(self):
        if self._url in _artist_img_cache:
            self.done.emit(_artist_img_cache[self._url])
            return
        # Check CoverCache before making a network request
        if self._cover_id:
            try:
                from player.components.cover_cache import CoverCache
                cache = CoverCache.instance()
                data = cache.get_full(self._cover_id) or cache.get_thumb(self._cover_id)
                if data:
                    pix = QPixmap()
                    pix.loadFromData(data)
                    if not pix.isNull():
                        _artist_img_cache[self._url] = pix
                        self.done.emit(pix)
                        return
            except Exception:
                pass
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


class _FullResCoverWorker(QThread):
    done = pyqtSignal(QPixmap)

    def __init__(self, client, cover_id: str):
        super().__init__()
        self._client   = client
        self._cover_id = cover_id

    def run(self):
        key = f'full_res:{self._cover_id}'
        if key in _artist_img_cache:
            self.done.emit(_artist_img_cache[key])
            return
        try:
            from player.components.cover_cache import CoverCache
            data = CoverCache.instance().get_full(self._cover_id)
            if data:
                pix = QPixmap()
                pix.loadFromData(data)
                if not pix.isNull():
                    _artist_img_cache[key] = pix
                    self.done.emit(pix)
                    return
        except Exception:
            pass
        try:
            data = self._client.get_cover_art(self._cover_id, size=None)
            if data:
                pix = QPixmap()
                pix.loadFromData(data)
                if not pix.isNull():
                    _artist_img_cache[key] = pix
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


class _SongDetailWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, client, song_id):
        super().__init__()
        self._client  = client
        self._song_id = song_id

    def run(self):
        try:
            raw = self._client.get_song(self._song_id) or {}
        except Exception:
            raw = {}
        self.done.emit(raw)


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

def _extract_vibrant_color(pix: QPixmap) -> QColor:
    """Sample pixmap at 8x8 and return the most saturated pixel."""
    small = pix.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    img = small.toImage()
    best_sat = -1.0
    best = QColor(80, 80, 80)
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            _, s, l, _ = c.getHslF()
            if s > best_sat and 0.1 < l < 0.9:
                best_sat = s
                best = c
    return best


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


def _tokenize(parts: list[str]) -> list[dict]:
    """[name, name, ...] -> [{text, isSep}, ...] with ' • ' separators
    interleaved — the data shape now_playing.qml's token Repeaters expect."""
    tokens = []
    for i, p in enumerate(parts):
        if i > 0:
            tokens.append({'text': ' • ', 'isSep': True})
        tokens.append({'text': p, 'isSep': False})
    return tokens


def _tokenize_artist(artist: str) -> list[dict]:
    """Raw artist string -> [{text, isSep}, ...], keeping the original
    separator wording ("feat.", "vs.", "///", "•", "/") instead of
    normalizing everything to ' • ' like _tokenize() does."""
    if not artist:
        return []
    parts = [p for p in _ARTIST_TOKEN_RE.split(artist) if p != '']
    return [{'text': p, 'isSep': bool(_ARTIST_TOKEN_RE.fullmatch(p))} for p in parts]


# ── Cover-zoom overlay (Pattern A — top-level window, see UI_MANIFEST.md §3) ─

class _CoverOverlay(QWidget):
    """Full-window dimmed overlay showing album art large on click.

    A top-level frameless window rather than a child widget: QQuickView-backed
    views (createWindowContainer) always paint above regular child widgets,
    so a child overlay would be hidden behind them.
    """
    def __init__(self, pixmap, parent, cover_id=None, client=None):
        super().__init__(None,
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint)
        self._pixmap = pixmap
        self._worker = None
        self._parent_window = parent
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.geometry())
        self.setCursor(Qt.CursorShape.ArrowCursor)
        parent.installEventFilter(self)
        # No QObject parent (top-level window), so keep a strong Python ref
        # on the main window — otherwise this gets garbage-collected right
        # after __init__ returns, killing the still-running cover worker thread.
        parent._cover_overlay = self
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        if cover_id and client:
            self._worker = _FullResCoverWorker(client, cover_id)
            self._worker.done.connect(self._on_hires)
            self._worker.start()

    def _on_hires(self, pix: QPixmap):
        if not pix.isNull():
            self._pixmap = pix
            self.update()

    def eventFilter(self, obj, event):
        if obj is self._parent_window and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.setGeometry(self._parent_window.geometry())
        return False

    def paintEvent(self, _):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(0, 0, 0, 175))
        if self._pixmap and not self._pixmap.isNull():
            max_dim = min(int(self.width() * 0.55), int(self.height() * 0.65))
            scaled = self._pixmap.scaled(max_dim, max_dim,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
            x = (self.width()  - scaled.width())  // 2
            y = (self.height() - scaled.height()) // 2
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, scaled.width(), scaled.height()), 14, 14)
            p.setClipPath(path)
            p.drawPixmap(x, y, scaled)
        p.end()

    def mousePressEvent(self, _):
        self._close()

    def keyPressEvent(self, _):
        self._close()

    def _close(self):
        self._parent_window.removeEventFilter(self)
        if getattr(self._parent_window, '_cover_overlay', None) is self:
            self._parent_window._cover_overlay = None
        self.close()
        self.deleteLater()


# ── Bridge ────────────────────────────────────────────────────────────────────

class NowPlayingBridge(QObject):
    # → QML theme
    accentColorChanged        = pyqtSignal(str)
    hoverColorChanged         = pyqtSignal(str)
    skeletonColorChanged      = pyqtSignal(str)
    cardBgChanged              = pyqtSignal(str)
    cardBorderChanged          = pyqtSignal(str)
    panelBgChanged             = pyqtSignal(str)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    fontFamilyChanged         = pyqtSignal(str)

    # → QML track card
    noTrackChanged       = pyqtSignal(bool)
    trackTitleChanged    = pyqtSignal(str)
    coverKeyChanged       = pyqtSignal(str)
    coverGlowColorChanged = pyqtSignal(str)
    artistTokensChanged   = pyqtSignal(list)
    albumNameChanged      = pyqtSignal(str)
    albumIdChanged        = pyqtSignal(str)
    yearTextChanged       = pyqtSignal(str)
    genreTokensChanged    = pyqtSignal(list)
    infoTextChanged       = pyqtSignal(str)
    isFavoriteChanged     = pyqtSignal(bool)
    lastfmUrlChanged      = pyqtSignal(str)
    wikiUrlChanged        = pyqtSignal(str)

    # → QML album-tracks card
    albumTracksChanged  = pyqtSignal(list, int, int)
    albumMetaChanged    = pyqtSignal(str)
    albumLoadingChanged = pyqtSignal(bool)

    # → QML top-songs card
    topSongsChanged        = pyqtSignal(list)
    topSongsLoadingChanged = pyqtSignal(bool)
    topSongsArtistChanged  = pyqtSignal(str)
    topSongsPageChanged    = pyqtSignal(int, int)

    # → QML artist card
    artistPageChanged     = pyqtSignal(int, int)
    artistPageNameChanged = pyqtSignal(str)
    artistPhotoKeyChanged = pyqtSignal(str)
    artistBioChanged      = pyqtSignal(str)
    similarArtistsChanged = pyqtSignal(list)

    # → QML tour card
    bandsintownEnabledChanged = pyqtSignal(bool)
    tourEventsChanged         = pyqtSignal(list)

    def __init__(self, view):
        super().__init__()
        self._view = view

    # ← QML slots ────────────────────────────────────────────────────────────

    @pyqtSlot()
    def coverClicked(self):
        self._view._on_cover_clicked()

    @pyqtSlot()
    def heartClicked(self):
        self._view._on_heart_clicked()

    @pyqtSlot()
    def lyricsRequested(self):
        self._view.lyrics_requested.emit()

    @pyqtSlot(str)
    def artistClicked(self, name: str):
        self._view.artist_clicked.emit(name)

    @pyqtSlot(str, str)
    def albumClicked(self, album_id: str, album_name: str):
        self._view.album_clicked.emit({'id': album_id, 'name': album_name, 'title': album_name})

    @pyqtSlot(str)
    def genreClicked(self, genre: str):
        self._view.genre_clicked.emit(genre)

    @pyqtSlot(str)
    def yearClicked(self, year: str):
        if year:
            self._view.year_clicked.emit(year)

    @pyqtSlot(int, str)
    def trackPlayClicked(self, index: int, source: str):
        rows = self._view._album_tracks_sorted if source == 'album' else self._view._top_songs_tracks
        if 0 <= index < len(rows):
            self._view.play_requested.emit(rows[index])

    @pyqtSlot(int)
    def topSongsPageRequested(self, idx: int):
        self._view._nav_top_page(idx)

    @pyqtSlot(int)
    def artistPageRequested(self, idx: int):
        self._view._nav_artist_page(idx)

    @pyqtSlot()
    def enableBandsintownClicked(self):
        self._view._enable_bandsintown()


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

    artist_clicked    = pyqtSignal(str)
    album_clicked     = pyqtSignal(dict)
    genre_clicked     = pyqtSignal(str)
    year_clicked      = pyqtSignal(str)
    favorite_toggled  = pyqtSignal(str, bool)   # (track_id, new_starred_state)
    lyrics_requested  = pyqtSignal()
    play_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client        = None
        self._current_track: dict = {}
        self._accent        = '#cccccc'
        self._fg            = '#dddddd'
        self._fg2           = '#888888'
        self._bg            = '14,14,14'
        self._settings            = QSettings('Icosahedron', 'Icosahedron')
        self._top_artists: list[str] = []
        self._top_page_idx: int      = 0
        self._artist_page_idx: int   = 0
        self._pending_track: dict | None = None
        self._cover_pixmap: QPixmap | None = None
        self._cover_id: str | None = None
        self._album_tracks_sorted: list = []
        self._top_songs_tracks:    list = []
        self._cover_overlay = None

        self._w_info:  _ArtistInfoWorker  | None = None
        self._w_img:   _ImageWorker       | None = None
        self._w_cover: _ImageWorker       | None = None
        self._w_album: _AlbumTracksWorker | None = None
        self._w_bit:   _BandsintownWorker | None = None
        self._w_top:   _TopSongsWorker    | None = None
        self._w_song:  _SongDetailWorker  | None = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('NowPlayingInfoTab')

        self._bridge = NowPlayingBridge(self)

        self._qml_view = QQuickView()
        self._qml_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._qml = QWidget.createWindowContainer(self._qml_view, self)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._pixmap_provider = PixmapImageProvider()
        self._icon_provider   = AlbumIconProvider()
        self._glow_provider   = AlbumDetailCoverProvider()

        engine = self._qml_view.engine()
        engine.addImageProvider("nowplayingpix",  self._pixmap_provider)
        engine.addImageProvider("nowplayingglow", self._glow_provider)
        engine.addImageProvider("homeicons",      self._icon_provider)

        ctx = self._qml_view.rootContext()
        ctx.setContextProperty("nowPlayingBridge", self._bridge)
        ctx.setContextProperty("scrollTuning",     scroll_tuning)

        self._qml_view.setSource(QUrl.fromLocalFile(
            resource_path("player/tabs/now_playing/now_playing.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

        self._bridge.noTrackChanged.emit(True)

    # ── Public API ─────────────────────────────────────────────────────

    def set_client(self, client):
        self._client = client

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_track is not None:
            track, self._pending_track = self._pending_track, None
            self.load_track(track)

    def load_track(self, track: dict):
        tid = track.get('id')
        if tid and tid == self._current_track.get('id'):
            return
        if not self.isVisible():
            self._pending_track = track
            return
        self._pending_track = None
        self._current_track = track
        self._cover_pixmap  = None
        self._cover_id       = None
        self._cancel_workers()

        b = self._bridge
        b.noTrackChanged.emit(False)
        self._emit_track_card(track)
        self._emit_album_card([], None)
        self._emit_artist_card({})
        self._emit_tour_card([])

        artist_name = track.get('artist', '')
        parts = [p.strip() for p in _ARTIST_SEP.split(artist_name) if p.strip()]
        self._top_artists     = parts if parts else ([artist_name] if artist_name else [])
        self._top_page_idx    = 0
        self._artist_page_idx = 0
        self._emit_top_card([])

        if not self._client:
            return

        artist_id = track.get('artist_id') or track.get('artistId')
        album_id  = track.get('albumId')    or track.get('album_id')
        cover_id  = track.get('cover_id')   or track.get('coverArt')

        first_artist = self._top_artists[0] if self._top_artists else ''
        if artist_id:
            self._w_info = _ArtistInfoWorker(self._client, artist_id=artist_id, artist_name=first_artist)
        elif first_artist:
            self._w_info = _ArtistInfoWorker(self._client, artist_name=first_artist)
        if self._w_info:
            self._w_info.done.connect(self._on_artist_info)
            self._w_info.start()

        if album_id:
            self._w_album = _AlbumTracksWorker(self._client, album_id)
            self._w_album.done.connect(self._on_album_tracks)
            self._w_album.start()

        if cover_id:
            self._cover_id = cover_id
            try:
                url = self._client.get_cover_art_url(cover_id, 400)
                if url in _artist_img_cache:
                    self._on_cover_art(_artist_img_cache[url])
                else:
                    self._w_cover = _ImageWorker(url, cover_id=cover_id)
                    self._w_cover.done.connect(self._on_cover_art)
                    self._w_cover.start()
            except Exception:
                pass

        song_id = track.get('id')
        if song_id and (track.get('samplingRate') is None or track.get('bitDepth') is None):
            self._w_song = _SongDetailWorker(self._client, song_id)
            self._w_song.done.connect(self._on_song_detail)
            self._w_song.start()

        first_for_tour = self._top_artists[0] if self._top_artists else artist_name
        if first_for_tour and self._bit_enabled():
            self._w_bit = _BandsintownWorker(first_for_tour)
            self._w_bit.done.connect(self._on_tour_events)
            self._w_bit.start()

        if self._top_artists:
            first = self._top_artists[0]
            key   = first.strip().lower()
            if key in _top_songs_cache:
                self._emit_top_card(_top_songs_cache[key])
            else:
                self._w_top = _TopSongsWorker(self._client, first)
                self._w_top.done.connect(self._on_top_songs)
                self._w_top.start()

    def set_accent_color(self, color: str):
        self._accent = color
        self._bridge.accentColorChanged.emit(color)
        theme = getattr(self.window(), 'theme', None)
        self._bridge.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')

    def apply_theme(self, theme):
        if theme is None:
            return
        b = self._bridge
        self._accent = getattr(theme, 'accent', self._accent)
        self._fg     = getattr(theme, 'font_color_primary', self._fg)
        self._fg2    = getattr(theme, 'font_color_secondary', self._fg2)
        raw_bg = getattr(theme, 'main_panel_bg', self._bg)
        self._bg = str(raw_bg)

        b.accentColorChanged.emit(self._accent)
        b.fontColorPrimaryChanged.emit(self._fg)
        b.fontColorSecondaryChanged.emit(self._fg2)
        b.fontSizePrimaryChanged.emit(getattr(theme, 'font_size_primary', 17))
        b.fontSizeSecondaryChanged.emit(getattr(theme, 'font_size_secondary', 12))
        b.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))
        b.skeletonColorChanged.emit(getattr(theme, 'skeleton_base', '#282828'))
        b.cardBgChanged.emit(getattr(theme, 'now_playing_card_bg', '#1e1e1e'))
        border = getattr(theme, 'border_color', '#2a2a2a')
        if not getattr(theme, 'auto_border_from_accent', True):
            border = getattr(theme, 'manual_border_color', '#2a2a2a')
        b.cardBorderChanged.emit(border)
        raw_bg = getattr(theme, 'main_panel_bg', '14,14,14')
        try:
            r, g, bb = (int(x) for x in raw_bg.split(','))
            b.panelBgChanged.emit('#{:02x}{:02x}{:02x}'.format(r, g, bb))
        except Exception:
            b.panelBgChanged.emit('#0e0e0e')
        b.hoverColorChanged.emit(resolve_menu_hover(theme))

    def set_bg_color(self, color: str):
        self._bg = color
        try:
            r, g, bb = (int(x) for x in color.split(','))
            self._qml_view.setColor(QColor(r, g, bb))
        except Exception:
            pass

    # ── Slots ──────────────────────────────────────────────────────────

    def _on_artist_info(self, info: dict):
        bio_raw = info.get('biography') or info.get('bio') or ''
        bio     = re.sub(r'<a [^>]*>.*?</a>\.?', '', bio_raw, flags=re.IGNORECASE).strip()
        similar = info.get('similarArtist', []) or []
        self._emit_artist_card(info, bio=bio, similar=similar)

        img_url = (
            info.get('largeImageUrl') or
            info.get('mediumImageUrl') or
            info.get('smallImageUrl') or ''
        )
        if img_url:
            if img_url in _artist_img_cache:
                self._on_artist_img(_artist_img_cache[img_url])
            else:
                self._w_img = _ImageWorker(img_url)
                self._w_img.done.connect(self._on_artist_img)
                self._w_img.start()

    def _on_artist_img(self, pix: QPixmap):
        if pix.isNull():
            return
        name = self._top_artists[self._artist_page_idx] if self._top_artists else 'artist'
        key = name.strip().lower()
        # Key must match exactly what QML requests: "image://nowplayingpix/artist:<key>".
        self._pixmap_provider.cache[f'artist:{key}'] = pix
        self._bridge.artistPhotoKeyChanged.emit(key)

    def _on_cover_art(self, pix: QPixmap):
        if pix.isNull() or not self._cover_id:
            return
        self._current_track['_cover_pixmap'] = pix
        self._cover_pixmap = pix
        key = self._cover_id
        # Key must match exactly what QML requests: "image://nowplayingpix/cover:<key>".
        self._pixmap_provider.cache[f'cover:{key}'] = pix
        self._bridge.coverKeyChanged.emit(key)
        glow = _extract_vibrant_color(pix)
        self._bridge.coverGlowColorChanged.emit(glow.name())

    def _on_album_tracks(self, tracks: list):
        self._emit_album_card(tracks, self._current_track.get('id'))

    def _on_tour_events(self, events: list):
        self._emit_tour_card(events)

    def _on_top_songs(self, tracks: list):
        self._emit_top_card(tracks)

    def _on_song_detail(self, raw: dict):
        if not raw:
            return
        changed = False
        for field in ('samplingRate', 'bitDepth'):
            if raw.get(field) is not None and self._current_track.get(field) is None:
                self._current_track[field] = raw[field]
                changed = True
        if changed:
            self._emit_track_card(self._current_track)

    def _on_cover_clicked(self):
        if not self._cover_pixmap or self._cover_pixmap.isNull():
            return
        _CoverOverlay(self._cover_pixmap, self.window(), cover_id=self._cover_id, client=self._client)

    def _on_heart_clicked(self):
        t = self._current_track
        raw = t.get('starred')
        cur = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new = not cur
        t['starred'] = new
        self._bridge.isFavoriteChanged.emit(new)
        self.favorite_toggled.emit(t.get('id', ''), new)

    def _nav_top_page(self, idx: int):
        if not (0 <= idx < len(self._top_artists)):
            return
        self._top_page_idx = idx
        artist_name = self._top_artists[idx]
        key = artist_name.strip().lower()
        if key in _top_songs_cache:
            self._emit_top_card(_top_songs_cache[key])
            return
        self._emit_top_card([])
        if self._w_top and self._w_top.isRunning():
            try:
                self._w_top.done.disconnect()
            except Exception:
                pass
            self._w_top.quit()
        self._w_top = _TopSongsWorker(self._client, artist_name)
        self._w_top.done.connect(self._on_top_songs)
        self._w_top.start()

    def _nav_artist_page(self, idx: int):
        if not (0 <= idx < len(self._top_artists)):
            return
        self._artist_page_idx = idx
        self._emit_artist_card({})
        name = self._top_artists[idx]

        # Artist bio
        cache_key = f'name:{name.strip().lower()}'
        if cache_key in _artist_info_cache:
            self._on_artist_info(_artist_info_cache[cache_key])
        else:
            if self._w_info and self._w_info.isRunning():
                try:
                    self._w_info.done.disconnect()
                except Exception:
                    pass
                self._w_info.quit()
            self._w_info = _ArtistInfoWorker(self._client, artist_name=name)
            self._w_info.done.connect(self._on_artist_info)
            self._w_info.start()

        # Tour dates
        if self._bit_enabled():
            self._emit_tour_card([])
            bit_key = name.strip().lower()
            if bit_key in _bit_cache:
                self._emit_tour_card(_bit_cache[bit_key])
            else:
                if self._w_bit and self._w_bit.isRunning():
                    try:
                        self._w_bit.done.disconnect()
                    except Exception:
                        pass
                    self._w_bit.quit()
                self._w_bit = _BandsintownWorker(name)
                self._w_bit.done.connect(self._on_tour_events)
                self._w_bit.start()

    # ── Card data emitters ───────────────────────────────────────────────

    def _emit_track_card(self, track: dict):
        b = self._bridge
        b.trackTitleChanged.emit(track.get('title', 'Unknown'))
        b.coverKeyChanged.emit('')
        b.coverGlowColorChanged.emit('')

        artist   = track.get('artist', '')
        album    = track.get('album', '')
        album_id = track.get('albumId') or track.get('album_id', '')
        year     = str(track.get('year', '') or '')

        b.artistTokensChanged.emit(_tokenize_artist(artist))
        b.albumNameChanged.emit(album)
        b.albumIdChanged.emit(album_id)
        b.yearTextChanged.emit(year)

        genre = track.get('genre', '')
        genre_tokens = [p.strip() for p in _GENRE_SEP.split(genre.strip()) if p.strip()] if genre else []
        b.genreTokensChanged.emit(_tokenize(genre_tokens))

        info_parts = []
        bitrate = track.get('bitRate') or track.get('bit_rate')
        if bitrate:
            try:
                br  = int(bitrate)
                info_parts.append('FLAC' if br > 900 else 'MP3')
                info_parts.append(f'{br} kbps')
            except (TypeError, ValueError):
                pass
        sample_rate = track.get('samplingRate') or track.get('sampleRate')
        if sample_rate:
            try:
                khz = int(sample_rate) / 1000
                info_parts.append((f'{khz:.1f}'.rstrip('0').rstrip('.')) + ' kHz')
            except (TypeError, ValueError):
                pass
        bit_depth = track.get('bitDepth') or track.get('bit_depth')
        if bit_depth:
            try:
                info_parts.append(f'{int(bit_depth)}-bit')
            except (TypeError, ValueError):
                pass
        dur = _fmt_dur(_parse_dur(track.get('duration', 0))) or _fmt_dur(track.get('duration_ms', 0) / 1000)
        if dur:
            info_parts.append(dur)
        b.infoTextChanged.emit('   '.join(info_parts))

        b.isFavoriteChanged.emit(bool(track.get('starred')))

        first_artist = _ARTIST_SEP.split(artist)[0].strip() if artist else ''
        if first_artist:
            import urllib.parse
            b.lastfmUrlChanged.emit(f'https://www.last.fm/music/{urllib.parse.quote_plus(first_artist)}')
            wiki_name = urllib.parse.quote(first_artist.replace(' ', '_'), safe='_')
            b.wikiUrlChanged.emit(f'https://en.wikipedia.org/wiki/{wiki_name}')
        else:
            b.lastfmUrlChanged.emit('')
            b.wikiUrlChanged.emit('')

    def _emit_album_card(self, tracks: list, current_id):
        b = self._bridge
        album_name = self._current_track.get('album', '')

        if not tracks:
            self._album_tracks_sorted = []
            b.albumLoadingChanged.emit(True)
            b.albumMetaChanged.emit('')
            b.albumTracksChanged.emit([], 0, 0)
            return

        sorted_tracks = sorted(
            tracks,
            key=lambda t: (int(t.get('discNumber', 1) or 1),
                           int(t.get('trackNumber', 0) or 0)),
        )
        self._album_tracks_sorted = sorted_tracks

        _album_name = sorted_tracks[0].get('album', '') or album_name
        current_num = 0
        total_secs  = 0
        for tr in sorted_tracks:
            total_secs += _parse_dur(tr.get('duration', 0))
            if tr.get('id') == current_id:
                current_num = int(tr.get('trackNumber', 0) or 0)
        stats_parts = []
        if current_num:
            stats_parts.append(f'Track {current_num} of {len(sorted_tracks)}')
        else:
            stats_parts.append(f'{len(sorted_tracks)} tracks')
        if total_secs:
            stats_parts.append(_fmt_total(total_secs))
        meta_line = _album_name
        if stats_parts:
            meta_line += ('  ·  ' if _album_name else '') + '  ·  '.join(stats_parts)
        b.albumMetaChanged.emit(meta_line)

        current_idx = next(
            (i for i, t in enumerate(sorted_tracks) if t.get('id') == current_id),
            None,
        )
        if current_idx is not None and len(sorted_tracks) > ALBUM_SHOW_N:
            half  = ALBUM_SHOW_N // 2
            start = max(0, current_idx - half)
            end   = start + ALBUM_SHOW_N
            if end > len(sorted_tracks):
                end   = len(sorted_tracks)
                start = max(0, end - ALBUM_SHOW_N)
        else:
            start = 0
            end   = min(ALBUM_SHOW_N, len(sorted_tracks))

        rows = []
        for i, tr in enumerate(sorted_tracks):
            rows.append({
                'num':       str(int(tr.get('trackNumber', 0) or 0) or (i + 1)),
                'title':     tr.get('title', 'Unknown'),
                'duration':  _fmt_dur(_parse_dur(tr.get('duration', 0))),
                'isCurrent': tr.get('id') == current_id,
            })
        b.albumLoadingChanged.emit(False)
        b.albumTracksChanged.emit(rows, start, end)

    def _emit_top_card(self, tracks: list):
        b = self._bridge
        n   = len(self._top_artists)
        idx = self._top_page_idx
        name = self._top_artists[idx] if self._top_artists else ''

        b.topSongsArtistChanged.emit(name)
        b.topSongsPageChanged.emit(idx, n)

        if not tracks:
            self._top_songs_tracks = []
            b.topSongsLoadingChanged.emit(True)
            b.topSongsChanged.emit([])
            return

        current_id = self._current_track.get('id')
        top5 = tracks[:5]
        self._top_songs_tracks = top5
        rows = [{
            'title':     tr.get('title', 'Unknown'),
            'album':     tr.get('album', ''),
            'duration':  _fmt_dur(tr.get('duration', '')),
            'isCurrent': tr.get('id') == current_id,
        } for tr in top5]
        b.topSongsLoadingChanged.emit(False)
        b.topSongsChanged.emit(rows)

    def _emit_artist_card(self, info: dict, bio: str = '', similar: list = None):
        b = self._bridge
        b.artistPhotoKeyChanged.emit('')

        n   = len(self._top_artists)
        idx = self._artist_page_idx
        current_artist_name = self._top_artists[idx] if self._top_artists else (
            self._current_track.get('artist', '') or info.get('name', '')
        )
        b.artistPageChanged.emit(idx, n)
        b.artistPageNameChanged.emit(current_artist_name or info.get('name', ''))
        b.artistBioChanged.emit(bio)

        names = []
        for item in (similar or [])[:6]:
            name = item.get('name', '') if isinstance(item, dict) else str(item)
            if name:
                names.append(name)
        b.similarArtistsChanged.emit(names)

    def _emit_tour_card(self, events: list):
        b = self._bridge
        b.bandsintownEnabledChanged.emit(self._bit_enabled())
        if not self._bit_enabled():
            b.tourEventsChanged.emit([])
            return

        rows = []
        for ev in events:
            dt = ev.get('datetime', '')
            month = day = ''
            if dt:
                try:
                    from datetime import datetime as _dt
                    d     = _dt.fromisoformat(dt.replace('Z', '+00:00'))
                    month = d.strftime('%b').upper()
                    day   = str(d.day)
                except Exception:
                    pass
            place = ', '.join(
                p for p in [ev.get('venueCity'), ev.get('venueRegion'), ev.get('venueCountry')] if p
            )
            rows.append({
                'month': month, 'day': day,
                'venue': ev.get('venueName', '') or 'TBA',
                'place': place,
                'url':   ev.get('url', ''),
            })
        b.tourEventsChanged.emit(rows)

    # ── Utilities ──────────────────────────────────────────────────────

    def _bit_enabled(self) -> bool:
        return bool(int(self._settings.value('bandsintown_enabled', 0) or 0))

    def _enable_bandsintown(self):
        self._settings.setValue('bandsintown_enabled', 1)
        self._bridge.bandsintownEnabledChanged.emit(True)
        artist_name = self._top_artists[self._artist_page_idx] if self._top_artists else self._current_track.get('artist', '')
        if artist_name:
            self._w_bit = _BandsintownWorker(artist_name)
            self._w_bit.done.connect(self._on_tour_events)
            self._w_bit.start()

    def _cancel_workers(self):
        for attr in ('_w_info', '_w_img', '_w_cover', '_w_album', '_w_bit', '_w_top', '_w_song'):
            w = getattr(self, attr, None)
            if w and w.isRunning():
                try:
                    w.done.disconnect()
                except Exception:
                    pass
                w.quit()
