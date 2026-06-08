"""
home.py — QML-based home tab (horizontally scrolling album rows).

Workers and disk-cache helpers are unchanged.  All PyQt widget code
(HomeAlbumRowWidget, _ShimmerDelegate, _ArrowButton, etc.) is replaced by
home.qml + the thin Python bridge/model/provider classes below.
"""
import os as _os
import sys as _sys
import json as _json
import time
import random as _rnd

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import (Qt, pyqtSignal, QThread, QSettings, QTimer,
                          QAbstractListModel, QModelIndex,
                          QObject, pyqtSlot, QUrl)
from PyQt6.QtGui  import QImage, QPainter, QPainterPath, QColor
from PyQt6.QtCore import QRectF
from PyQt6.QtQuick import QQuickImageProvider
from PyQt6.QtQuickWidgets import QQuickWidget

from albums_browser import GridCoverWorker, resource_path

_PAGE        = 50
_RANDOM_PAGE = 100


# ── Disk-cache helpers (unchanged) ──────────────────────────────────────────

def _home_cache_path():
    base = getattr(_sys, '_MEIPASS', _os.path.dirname(_os.path.abspath(__file__)))
    d    = _os.path.join(base, 'app_data')
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, 'home_cache.json')

def _home_cache_read():
    try:
        with open(_home_cache_path(), 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception:
        return {}

def _home_cache_write(data: dict):
    try:
        with open(_home_cache_path(), 'w', encoding='utf-8') as f:
            _json.dump(data, f)
    except Exception:
        pass


# ── Workers (unchanged) ─────────────────────────────────────────────────────

class HomeLoaderWorker(QThread):
    recent_ready      = pyqtSignal(list)
    random_ready      = pyqtSignal(list)
    most_played_ready = pyqtSignal(list)
    data_ready        = pyqtSignal(list, list, list)  # kept for compat

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            if not self.client:
                self.data_ready.emit([], [], [])
                return

            cached = _home_cache_read()
            if cached.get('recent'):      self.recent_ready.emit(cached['recent'])
            if cached.get('random'):      self.random_ready.emit(cached['random'])
            if cached.get('most_played'): self.most_played_ready.emit(cached['most_played'])

            has_random_cache = bool(cached.get('random'))

            def _fetch(sort_type, size=_PAGE):
                return self.client.get_album_list_sorted(
                    sort_type=sort_type, size=size, offset=0) or []

            futures_map = {}
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_recent      = pool.submit(_fetch, "newest")
                f_most_played = pool.submit(_fetch, "frequent")
                futures_map[f_recent]      = ('recent',      self.recent_ready)
                futures_map[f_most_played] = ('most_played', self.most_played_ready)
                if not has_random_cache:
                    f_random = pool.submit(_fetch, "random", _RANDOM_PAGE)
                    futures_map[f_random] = ('random', self.random_ready)
                results = {}
                for future in as_completed(futures_map):
                    key, sig = futures_map[future]
                    data = future.result() or []
                    results[key] = data
                    sig.emit(data)

            _home_cache_write({
                'recent':      results.get('recent', []),
                'random':      results.get('random', cached.get('random', [])),
                'most_played': results.get('most_played', []),
            })
            self.data_ready.emit(
                results.get('recent', []),
                results.get('random', []),
                results.get('most_played', []),
            )
        except Exception as e:
            print(f"[Home Worker] Error: {e}")
            self.data_ready.emit([], [], [])


class HomePageWorker(QThread):
    page_ready = pyqtSignal(list)

    def __init__(self, client, sort_type, offset):
        super().__init__()
        self.client    = client
        self.sort_type = sort_type
        self.offset    = offset

    def run(self):
        try:
            result = []
            if self.client:
                result = self.client.get_album_list_sorted(
                    sort_type=self.sort_type, size=_PAGE, offset=self.offset)
            self.page_ready.emit(result or [])
        except Exception as e:
            print(f"[HomePageWorker] Error: {e}")
            self.page_ready.emit([])


class RandomMixReloaderWorker(QThread):
    data_ready = pyqtSignal(list)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            result = []
            if self.client:
                result = self.client.get_album_list_sorted(
                    sort_type="random", size=_RANDOM_PAGE, offset=0)
            self.data_ready.emit(result or [])
        except Exception as e:
            print(f"[RandomMixReloader] Error: {e}")
            self.data_ready.emit([])


# ── QAbstractListModel ───────────────────────────────────────────────────────

class HomeAlbumModel(QAbstractListModel):
    TITLE_ROLE    = Qt.ItemDataRole.UserRole + 1
    ARTIST_ROLE   = Qt.ItemDataRole.UserRole + 2
    YEAR_ROLE     = Qt.ItemDataRole.UserRole + 3
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 4
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 5
    IS_LOADING_ROLE  = Qt.ItemDataRole.UserRole + 6
    SONG_COUNT_ROLE  = Qt.ItemDataRole.UserRole + 7
    ARTIST_ID_ROLE   = Qt.ItemDataRole.UserRole + 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._albums = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._albums)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._albums):
            return None
        a = self._albums[index.row()]
        if role == self.IS_LOADING_ROLE:  return bool(a.get('_skeleton'))
        if role == self.TITLE_ROLE:
            return '' if a.get('_skeleton') else (a.get('title') or a.get('name') or 'Unknown')
        if role == self.ARTIST_ROLE:     return a.get('artist') or a.get('albumArtist') or ''
        if role == self.YEAR_ROLE:       return str(a.get('year') or a.get('minYear') or '').replace('None', '')
        if role == self.COVER_ID_ROLE:   return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE:   return a
        if role == self.SONG_COUNT_ROLE:
            n = a.get('songCount') or a.get('trackCount') or ''
            return f"{n} tracks" if n else ''
        if role == self.ARTIST_ID_ROLE:  return a.get('artistId') or a.get('albumArtistId') or ''
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE:      b"albumTitle",
            self.ARTIST_ROLE:     b"albumArtist",
            self.YEAR_ROLE:       b"albumYear",
            self.COVER_ID_ROLE:   b"coverId",
            self.RAW_DATA_ROLE:   b"rawData",
            self.IS_LOADING_ROLE: b"isLoading",
            self.SONG_COUNT_ROLE: b"albumSongCount",
            self.ARTIST_ID_ROLE:  b"albumArtistId",
        }

    def set_albums(self, albums):
        self.beginResetModel()
        self._albums = list(albums)
        self.endResetModel()

    def append_albums(self, albums):
        if not albums:
            return
        start = len(self._albums)
        self.beginInsertRows(QModelIndex(), start, start + len(albums) - 1)
        self._albums.extend(albums)
        self.endInsertRows()

    def update_cover(self, cover_id):
        forced = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self._albums):
            raw = str(a.get('cover_id') or a.get('coverArt') or a.get('id') or '')
            if raw == cover_id:
                a['coverId_forced'] = forced
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])


# ── Image provider (serves cover art to QML via image://homecovers/<id>) ────

class HomeIconProvider(QQuickImageProvider):
    """Provides accent-colour-tinted PNG icons. URL: image://homeicons/<name>_<RRGGBB>"""
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = {}

    def requestImage(self, icon_id, requestedSize):
        parts = icon_id.rsplit('_', 1)
        name      = parts[0]
        color_hex = ('#' + parts[1]) if len(parts) > 1 else '#ffffff'
        cache_key = f"{name}_{color_hex}"
        if cache_key in self._cache:
            img = self._cache[cache_key]
            return img, img.size()
        path = resource_path(f"img/{name}.png")
        base = QImage(path)
        if base.isNull():
            empty = QImage(1, 1, QImage.Format.Format_ARGB32)
            empty.fill(Qt.GlobalColor.transparent)
            return empty, empty.size()
        result = QImage(base.size(), QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(0, 0, base)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(result.rect(), QColor(color_hex))
        p.end()
        self._cache[cache_key] = result
        return result, result.size()


class HomeCoverProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.cache = {}  # cover_id → raw bytes

    def requestImage(self, id, requestedSize):
        real_id = id.split("?t=")[0]
        data    = self.cache.get(real_id)

        size = 250
        img  = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)

        if data:
            source = QImage()
            source.loadFromData(data)
            if not source.isNull():
                source = source.scaled(size, size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                painter = QPainter(img)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()

        return img, img.size()


# ── Bridge (QML ↔ Python event routing) ─────────────────────────────────────

class HomeBridge(QObject):
    # → QML (theme / spinner updates)
    accentColorChanged        = pyqtSignal(str)
    skeletonColorChanged      = pyqtSignal(str)
    hoverColorChanged         = pyqtSignal(str)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    recentSpinChanged         = pyqtSignal(bool)
    randomSpinChanged         = pyqtSignal(bool)

    def __init__(self, recent_model, random_model, most_played_model):
        super().__init__()
        self._models = {
            'recent':      recent_model,
            'random':      random_model,
            'most_played': most_played_model,
        }
        self._view = None  # set by HomeView after construction

    # ← QML slots ────────────────────────────────────────────────────────────

    @pyqtSlot(str, int)
    def albumClicked(self, row_id, idx):
        m = self._models.get(row_id)
        if m and 0 <= idx < len(m._albums) and not m._albums[idx].get('_skeleton'):
            self._view.album_clicked.emit(m._albums[idx])

    @pyqtSlot(str, int)
    def playClicked(self, row_id, idx):
        m = self._models.get(row_id)
        if m and 0 <= idx < len(m._albums) and not m._albums[idx].get('_skeleton'):
            self._view.play_album.emit(m._albums[idx])

    @pyqtSlot(str, str)
    def artistNameClicked(self, name, artist_id):
        self._view.artist_clicked.emit(name)

    @pyqtSlot()
    def refreshRecent(self):
        self._view._refresh_recent()

    @pyqtSlot()
    def refreshRandom(self):
        self._view._refresh_random()

    @pyqtSlot(str, int)
    def loadMore(self, row_id, offset):
        self._view._load_more(row_id, offset)

    @pyqtSlot(str)
    def saveRowOrder(self, order):
        QSettings("Icosahedron", "Icosahedron").setValue('home_row_order', order)


# ── HomeView — thin wrapper around QQuickWidget ──────────────────────────────

class HomeView(QWidget):
    album_clicked  = pyqtSignal(dict)
    play_album     = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)

    def __init__(self, client=None):
        super().__init__()
        self.client = client

        # Per-row album models
        self.recent_model      = HomeAlbumModel()
        self.random_model      = HomeAlbumModel()
        self.most_played_model = HomeAlbumModel()

        # Cover image provider (shared across all rows)
        self.cover_provider = HomeCoverProvider()

        # Bridge
        self.bridge = HomeBridge(
            self.recent_model, self.random_model, self.most_played_model)
        self.bridge._view = self

        # QML widget
        self._qml = QQuickWidget()
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        self.icon_provider = HomeIconProvider()

        engine = self._qml.engine()
        engine.addImageProvider("homecovers", self.cover_provider)
        engine.addImageProvider("homeicons",  self.icon_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("recentModel",     self.recent_model)
        ctx.setContextProperty("randomModel",     self.random_model)
        ctx.setContextProperty("mostPlayedModel", self.most_played_model)
        ctx.setContextProperty("homeBridge",      self.bridge)
        ctx.setContextProperty("savedRowOrder",   self._load_row_order())

        self._qml.setSource(QUrl.fromLocalFile(resource_path("home.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

        self._random_buffer        = []
        self._random_refresh_count = 0
        self._worker_graveyard     = set()

        if client:
            self._start_cover_worker()
            self.load_data()

    # ── Public API (called by main window) ───────────────────────────────────

    def initialize(self, client):
        self.client = client
        if not getattr(self, 'cover_worker', None):
            self._start_cover_worker()
        else:
            self.cover_worker.client = client
        self.load_data()

    def set_accent_color(self, color):
        from player.mixins.visuals import resolve_menu_hover
        self.bridge.accentColorChanged.emit(color)
        theme = getattr(self.window(), 'theme', None)
        self.bridge.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')
        if theme:
            self.bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
            self.bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            self.bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
            self.bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
            self.bridge.skeletonColorChanged.emit(
                getattr(theme, 'skeleton_base', '#282828'))

    def set_bg_color(self, c):
        self._bg_color = c
        try:
            r, g, b = (int(x) for x in c.split(','))
            self._qml.setClearColor(QColor(r, g, b))
        except Exception:
            pass

    def focus_first_grid(self):
        self._qml.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── Data loading ─────────────────────────────────────────────────────────

    def load_data(self):
        if getattr(self, '_loader', None) and self._loader.isRunning():
            self._safe_discard_worker(self._loader)

        for m in (self.recent_model, self.random_model, self.most_played_model):
            m.set_albums([{'_skeleton': True}] * 8)

        self._loader = HomeLoaderWorker(self.client)
        self._loader.recent_ready.connect(self._on_recent_loaded)
        self._loader.random_ready.connect(self._on_random_loaded)
        self._loader.most_played_ready.connect(self._on_most_played_loaded)
        self._loader.start()

    def _on_recent_loaded(self, albums):
        self.recent_model.set_albums(albums)
        self._queue_covers(albums)

    def _on_random_loaded(self, albums):
        if albums:
            self._random_buffer = list(albums)
        self.random_model.set_albums(albums or self._random_buffer)
        self._queue_covers(albums)

    def _on_most_played_loaded(self, albums):
        self.most_played_model.set_albums(albums)
        self._queue_covers(albums)

    # ── Refresh ──────────────────────────────────────────────────────────────

    def _refresh_recent(self):
        self.bridge.recentSpinChanged.emit(True)
        self.recent_model.set_albums([{'_skeleton': True}] * 8)
        if getattr(self, '_recent_loader', None) and self._recent_loader.isRunning():
            self._safe_discard_worker(self._recent_loader)
        w = HomePageWorker(self.client, "newest", 0)
        w.page_ready.connect(self._on_recent_refreshed)
        self._recent_loader = w
        w.start()

    def _on_recent_refreshed(self, albums):
        self.bridge.recentSpinChanged.emit(False)
        if albums:
            self.recent_model.set_albums(albums)
            self._queue_covers(albums)

    def _refresh_random(self):
        self.bridge.randomSpinChanged.emit(True)
        self.random_model.set_albums([{'_skeleton': True}] * 8)
        self._random_refresh_count += 1
        buf = self._random_buffer
        if buf:
            _rnd.shuffle(buf)
            def _populate():
                self.random_model.set_albums(buf)
                self._queue_covers(buf)
                if self._random_refresh_count % 3 == 0:
                    self._fetch_random_background()
                else:
                    self.bridge.randomSpinChanged.emit(False)
            QTimer.singleShot(50, _populate)
        else:
            self._fetch_random_background()

    def _fetch_random_background(self):
        if getattr(self, '_random_loader', None) and self._random_loader.isRunning():
            self._safe_discard_worker(self._random_loader)
        w = RandomMixReloaderWorker(self.client)
        w.data_ready.connect(self._on_random_refreshed)
        self._random_loader = w
        w.start()

    def _on_random_refreshed(self, albums):
        self.bridge.randomSpinChanged.emit(False)
        if albums:
            self._random_buffer = list(albums)
            self.random_model.set_albums(albums)
            self._queue_covers(albums)

    # ── Load more ────────────────────────────────────────────────────────────

    def _load_more(self, row_id, offset):
        attr = f'_loading_more_{row_id}'
        if getattr(self, attr, False):
            return
        model_map = {
            'recent':      (self.recent_model,      "newest"),
            'random':      (self.random_model,      "random"),
            'most_played': (self.most_played_model, "frequent"),
        }
        if row_id not in model_map:
            return
        model, sort = model_map[row_id]
        if any(a.get('_skeleton') for a in model._albums):
            return
        setattr(self, attr, True)
        w = HomePageWorker(self.client, sort, offset)
        def _on_page(albums, m=model, a=attr):
            setattr(self, a, False)
            m.append_albums(albums)
            self._queue_covers(albums)
        w.page_ready.connect(_on_page)
        wattr = f'_page_worker_{row_id}'
        if getattr(self, wattr, None):
            self._safe_discard_worker(getattr(self, wattr))
        setattr(self, wattr, w)
        w.start()

    # ── Cover art ────────────────────────────────────────────────────────────

    def _start_cover_worker(self):
        self.cover_worker = GridCoverWorker(self.client)
        self.cover_worker.cover_ready.connect(self._on_cover_ready)
        self.cover_worker.start()

    def _on_cover_ready(self, cover_id, image_data):
        cid = str(cover_id)
        self.cover_provider.cache[cid] = image_data
        for m in (self.recent_model, self.random_model, self.most_played_model):
            m.update_cover(cid)

    def _queue_covers(self, albums):
        if not getattr(self, 'cover_worker', None):
            return
        for album in albums:
            cid = str(album.get('cover_id') or album.get('coverArt') or album.get('id') or '')
            if cid and cid not in self.cover_worker.queue:
                self.cover_worker.queue.append(cid)

    # ── Row order (registry) ─────────────────────────────────────────────────

    def _load_row_order(self):
        return QSettings("Icosahedron", "Icosahedron").value(
            'home_row_order', 'recent,random,most_played')

    # ── Worker lifecycle ─────────────────────────────────────────────────────

    def _safe_discard_worker(self, worker):
        if not worker:
            return
        self._worker_graveyard.add(worker)
        try:
            worker.finished.connect(
                lambda: self._worker_graveyard.discard(worker))
        except Exception:
            pass
