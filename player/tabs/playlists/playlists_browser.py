import json
import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QStackedWidget, QApplication)
from PyQt6.QtCore import (Qt, pyqtSignal, QThread, QTimer,
                          QAbstractListModel, QModelIndex, pyqtSlot, pyqtProperty,
                          QObject, QUrl, QPoint, QSettings)
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtQuick import QQuickView
from PyQt6.QtQuickWidgets import QQuickWidget
from player.mixins.visuals import resolve_menu_hover

from PyQt6.QtQuick import QQuickImageProvider
from player.widgets import CoverImageProvider, QMLGridWrapper, AlbumDetailCoverProvider, AlbumIconProvider, themed_shadow_menu, popup_menu_at_global
from player import resource_path
from player.workers import GridCoverWorker
from player.components.shared_widgets import SmartSearchContainer
from player.qml_search import SearchController, SearchKeyFilter, set_window_shortcuts_enabled
from player.scroll_tuning import scroll_tuning

class PlaylistModel(QAbstractListModel):
    TITLE_ROLE    = Qt.ItemDataRole.UserRole + 1
    SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 3
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self):
        super().__init__()
        self.playlists = []

    def rowCount(self, parent=QModelIndex()): 
        return len(self.playlists)

    def data(self, index, role):
        if not index.isValid(): return None
        p = self.playlists[index.row()]
        if role == self.TITLE_ROLE:    return p.get('title') or p.get('name') or 'Unknown'
        if role == self.SUBTITLE_ROLE: return p.get('subtitle', '')
        if role == self.COVER_ID_ROLE: return p.get('coverId_forced') or p.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return p
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE:    b"playlistTitle",
            self.SUBTITLE_ROLE: b"playlistSubtitle",
            self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData",
        }

    def reset_data(self, new_playlists):
        self.beginResetModel()
        self.playlists = new_playlists
        self.endResetModel()

    def update_cover(self, cover_id):
        import time
        forced_id = f"{cover_id}?t={time.time()}"
        for i, p in enumerate(self.playlists):
            if p.get('cover_id') == cover_id:
                p['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class PlaylistBridge(QObject):
    itemClicked        = pyqtSignal(dict)
    playClicked        = pyqtSignal(dict)
    itemRightClicked   = pyqtSignal(int)
    backgroundRightClicked = pyqtSignal()
    accentColorChanged        = pyqtSignal(str)
    bgAlphaChanged            = pyqtSignal(float)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    keyTextForwarded          = pyqtSignal(str)
    slashPressed              = pyqtSignal()
    dimChanged                = pyqtSignal(bool)

    def __init__(self, playlist_model):
        super().__init__()
        self.playlist_model = playlist_model

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        if 0 <= idx < len(self.playlist_model.playlists):
            self.itemClicked.emit(self.playlist_model.playlists[idx])

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        if 0 <= idx < len(self.playlist_model.playlists):
            self.playClicked.emit(self.playlist_model.playlists[idx])

    @pyqtSlot(int)
    def emitItemRightClicked(self, idx):
        self.itemRightClicked.emit(idx)

    @pyqtSlot()
    def emitBackgroundRightClicked(self):
        self.backgroundRightClicked.emit()

    @pyqtSlot(str)
    def forwardKeyText(self, text):
        self.keyTextForwarded.emit(text)

    @pyqtSlot()
    def forwardSlash(self):
        self.slashPressed.emit()

class PlaylistsWorker(QThread):
    results_ready = pyqtSignal(list)
    def __init__(self, client):
        super().__init__()
        self.client = client
    
    def run(self):
        if not self.client: return
        playlists = self.client.get_playlists()
        self.results_ready.emit(playlists)

class PlaylistTracksWorker(QThread):
    results_ready = pyqtSignal(dict, list)
    def __init__(self, client, playlist_data):
        super().__init__()
        self.client = client
        self.playlist_data = playlist_data
    
    def run(self):
        if not self.client: return
        tracks = self.client.get_playlist_tracks(self.playlist_data.get('id'))
        self.results_ready.emit(self.playlist_data, tracks)

class DragDropHelper(QObject):
    orderChanged = pyqtSignal()
    sig_drag_started = pyqtSignal(QPixmap, QPoint)
    sig_drag_moved = pyqtSignal(QPoint)
    sig_drag_ended = pyqtSignal()

# ── Playlist detail — QML-based (mirrors AlbumDetailView) ─────────────────────

class _TrackThumbProvider(QQuickImageProvider):
    """Serves per-track thumbnails from CoverCache, fetching from the network on cache miss.
    requestImage is called on a QML background thread so network I/O here is safe."""
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._client = None

    def set_client(self, client):
        self._client = client

    def requestImage(self, cid, _requestedSize):
        from PyQt6.QtGui import QImage
        from player.components.cover_cache import CoverCache, THUMB_SIZE
        data = CoverCache.instance().get_thumb(cid)
        if not data and self._client:
            try:
                data = self._client.get_cover_art(cid, size=THUMB_SIZE)
                if data:
                    CoverCache.instance().save_thumb(cid, data)
            except Exception:
                pass
        if data:
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                return img, img.size()
        empty = QImage(1, 1, QImage.Format.Format_ARGB32)
        empty.fill(Qt.GlobalColor.transparent)
        return empty, empty.size()


class PlaylistDetailTrackModel(QAbstractListModel):
    TRACK_IDX      = Qt.ItemDataRole.UserRole + 1
    TRACK_ID       = Qt.ItemDataRole.UserRole + 2
    TRACK_NUMBER   = Qt.ItemDataRole.UserRole + 3
    TRACK_TITLE    = Qt.ItemDataRole.UserRole + 4
    ARTIST_NAME    = Qt.ItemDataRole.UserRole + 5
    IS_FAVORITE    = Qt.ItemDataRole.UserRole + 6
    DURATION_STR   = Qt.ItemDataRole.UserRole + 7
    PLAY_COUNT_STR = Qt.ItemDataRole.UserRole + 8
    TRACK_GENRE    = Qt.ItemDataRole.UserRole + 9
    COVER_ART_ID   = Qt.ItemDataRole.UserRole + 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        if role == self.TRACK_IDX:      return r.get('_idx', 0)
        if role == self.TRACK_ID:       return r.get('_id', '')
        if role == self.TRACK_NUMBER:   return r.get('_num', '')
        if role == self.TRACK_TITLE:    return r.get('_title', '')
        if role == self.ARTIST_NAME:    return r.get('_artist', '')
        if role == self.IS_FAVORITE:    return r.get('_fav', False)
        if role == self.DURATION_STR:   return r.get('_dur', '')
        if role == self.PLAY_COUNT_STR: return r.get('_plays', '-')
        if role == self.TRACK_GENRE:    return r.get('_genre', '')
        if role == self.COVER_ART_ID:   return r.get('_cover_id', '')
        return None

    def roleNames(self):
        return {
            self.TRACK_IDX:      b"trackIdx",
            self.TRACK_ID:       b"trackId",
            self.TRACK_NUMBER:   b"trackNumber",
            self.TRACK_TITLE:    b"trackTitle",
            self.ARTIST_NAME:    b"artistName",
            self.IS_FAVORITE:    b"isFavorite",
            self.DURATION_STR:   b"durationStr",
            self.PLAY_COUNT_STR: b"playCountStr",
            self.TRACK_GENRE:    b"trackGenre",
            self.COVER_ART_ID:   b"coverArtId",
        }

    def set_tracks(self, tracks: list):
        rows = []
        for idx, t in enumerate(tracks):
            raw_star = t.get('starred', False)
            is_fav   = raw_star.lower() in ('true', '1') if isinstance(raw_star, str) else bool(raw_star)
            dur_ms   = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
            secs     = dur_ms // 1000
            plays    = str(t.get('play_count') or 0) if t.get('play_count') else '-'
            num      = str(t.get('track') or (idx + 1))
            genre_raw = t.get('genre', '') or ''
            for sep in ['; ', ';', ' | ', '|', ' / ', '/']:
                genre_raw = genre_raw.replace(sep, ' • ')
            genre_parts = [g.strip() for g in genre_raw.split(' • ') if g.strip()]
            rows.append({
                '_idx':      idx,
                '_id':       str(t.get('id', '')),
                '_num':      num,
                '_title':    t.get('title', ''),
                '_artist':   t.get('artist', ''),
                '_fav':      is_fav,
                '_dur':      f"{secs // 60}:{secs % 60:02d}",
                '_plays':    plays,
                '_genre':    ' • '.join(genre_parts[:3]),
                '_cover_id': str(t.get('coverArt') or t.get('albumId') or ''),
            })
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update_favorite(self, track_idx: int, is_fav: bool):
        for i, r in enumerate(self._rows):
            if r.get('_idx') == track_idx:
                r['_fav'] = is_fav
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.IS_FAVORITE])
                break

    def move_track(self, from_idx: int, to_idx: int):
        if from_idx == to_idx: return
        n = len(self._rows)
        if not (0 <= from_idx < n) or not (0 <= to_idx < n): return
        dest = to_idx + 1 if from_idx < to_idx else to_idx
        self.beginMoveRows(QModelIndex(), from_idx, from_idx, QModelIndex(), dest)
        item = self._rows.pop(from_idx)
        self._rows.insert(to_idx, item)
        self.endMoveRows()


class PlaylistDetailBridge(QObject):
    # → QML theme
    accentColorChanged        = pyqtSignal(str)
    hoverColorChanged         = pyqtSignal(str)
    skeletonColorChanged      = pyqtSignal(str)
    cardBgChanged             = pyqtSignal(str)
    cardBorderChanged         = pyqtSignal(str)
    panelBgChanged            = pyqtSignal(str)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    fontFamilyChanged         = pyqtSignal(str)
    # → QML data
    playlistDataChanged       = pyqtSignal(str, str, str, str)  # title, owner, meta, covId
    coverIdChanged            = pyqtSignal(str)
    publicStateChanged        = pyqtSignal(bool)
    playingStatusChanged      = pyqtSignal(str, bool)
    selectedTrackChanged      = pyqtSignal(int)
    scrollToModelRow          = pyqtSignal(int)
    scrollToTopOfView         = pyqtSignal()
    scrollToBottomOfView      = pyqtSignal()
    # → QML column visibility
    showTrackChanged          = pyqtSignal(bool)
    showTitleChanged          = pyqtSignal(bool)
    showArtistChanged         = pyqtSignal(bool)
    showFavChanged            = pyqtSignal(bool)
    showGenreChanged          = pyqtSignal(bool)
    showDurChanged            = pyqtSignal(bool)
    showPlaysChanged          = pyqtSignal(bool)

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._selected_trkidx = -1
        self.search = SearchController(
            on_active_changed=lambda active: view._set_window_shortcuts_enabled(not active))

    @pyqtProperty(QObject, constant=True)
    def searchCtl(self):
        return self.search

    @pyqtSlot()
    def playClicked(self):
        self._view.play_clicked.emit()

    @pyqtSlot()
    def shuffleClicked(self):
        self._view.shuffle_clicked.emit()

    @pyqtSlot()
    def togglePublic(self):
        self._view._toggle_public()

    @pyqtSlot()
    def favHeaderClicked(self):
        pass  # playlists have no per-view fav-sort

    @pyqtSlot(int)
    def navigateRow(self, delta: int):
        rows = self._view._track_model._rows
        if not rows:
            return
        st = self.search.text.lower()
        if st:
            nav = [(i, r['_idx']) for i, r in enumerate(rows)
                   if st in r.get('_title', '').lower()
                   or st in r.get('_artist', '').lower()
                   or st in r.get('_genre', '').lower()]
        else:
            nav = [(i, r['_idx']) for i, r in enumerate(rows)]
        if not nav:
            return
        pos = next((p for p, (_, idx) in enumerate(nav) if idx == self._selected_trkidx), -1)
        if pos == -1:
            new_pos = 0 if delta > 0 else len(nav) - 1
        else:
            new_pos = pos + delta
            if new_pos < 0:
                self.scrollToTopOfView.emit()
                return
            if new_pos >= len(nav):
                self.scrollToBottomOfView.emit()
                return
        mr, ti = nav[new_pos]
        self._selected_trkidx = ti
        self.selectedTrackChanged.emit(ti)
        self.scrollToModelRow.emit(mr)

    @pyqtSlot()
    def playSelected(self):
        if self._selected_trkidx >= 0:
            self._view.track_play_signal.emit(self._view._tracks, self._selected_trkidx)

    @pyqtSlot(int)
    def trackPlayClicked(self, track_idx: int):
        tracks = self._view._tracks
        if 0 <= track_idx < len(tracks):
            self._selected_trkidx = track_idx
            self.selectedTrackChanged.emit(track_idx)
            self._view.track_play_signal.emit(tracks, track_idx)

    @pyqtSlot(str)
    def trackArtistClicked(self, name: str):
        self._view.track_artist_clicked.emit(name)

    @pyqtSlot(str)
    def trackGenreClicked(self, genre: str):
        self._view.genre_clicked.emit(genre)

    @pyqtSlot(int)
    def trackFavoriteClicked(self, track_idx: int):
        tracks = self._view._tracks
        if not (0 <= track_idx < len(tracks)):
            return
        track   = tracks[track_idx]
        raw     = track.get('starred', False)
        cur_fav = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new_fav = not cur_fav
        track['starred'] = new_fav
        self._view._track_model.update_favorite(track_idx, new_fav)
        self._view.favorite_toggled.emit(str(track.get('id', '')), new_fav)
        client = getattr(self._view, 'client', None)
        if client:
            threading.Thread(
                target=lambda: client.set_favorite(track.get('id'), new_fav),
                daemon=True).start()

    @pyqtSlot(int, float, float)
    def trackContextMenuRequested(self, track_idx: int, global_x: float, global_y: float):
        self._view._show_track_context_menu_at(track_idx, int(global_x), int(global_y))

    @pyqtSlot(str, int, int, int)
    def showTooltip(self, text: str, cx: int, above_y: int, below_y: int):
        t = getattr(self, '_tip_hide_timer', None)
        if t and t.isActive():
            t.stop()
        for w in QApplication.topLevelWidgets():
            tf = getattr(w, '_tooltip_filter', None)
            if tf:
                tf._qml_mode = True
                tf._ensure_tip().show_at(cx, above_y, below_y, text)
                break

    @pyqtSlot()
    def hideTooltip(self):
        def _do_hide():
            for w in QApplication.topLevelWidgets():
                tf = getattr(w, '_tooltip_filter', None)
                if tf:
                    tf._qml_mode = False
                    if tf._tip and tf._tip.isVisible():
                        tf._tip.hide()
                    break
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(_do_hide)
        t.start(120)
        self._tip_hide_timer = t

    @pyqtSlot(result='QVariantList')
    def getColWidths(self):
        saved = QSettings().value('playlist_detail/track_col_widths')
        if isinstance(saved, dict):
            return [int(saved.get('artist', 160)), int(saved.get('fav', 68)),
                    int(saved.get('dur', 72)), int(saved.get('plays', 60)), int(saved.get('genre', 140))]
        return [160, 68, 72, 60, 140]

    @pyqtSlot(int, int, int, int, int)
    def saveColWidths(self, artist: int, fav: int, dur: int, plays: int, genre: int):
        QSettings().setValue('playlist_detail/track_col_widths',
                             {'artist': artist, 'fav': fav, 'dur': dur, 'plays': plays, 'genre': genre})

    @pyqtSlot(result='QVariantList')
    def getColVisibility(self):
        saved = QSettings().value('playlist_detail/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        return [bool(saved.get('track',  True)),  bool(saved.get('title',  True)),
                bool(saved.get('artist', True)),  bool(saved.get('fav',    True)),
                bool(saved.get('genre',  True)),  bool(saved.get('dur',    True)),
                bool(saved.get('plays',  True))]

    @pyqtSlot(float, float)
    def burgerClicked(self, gx: float, gy: float):
        saved = QSettings().value('playlist_detail/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        cols = [
            ('track',  'Track',    self.showTrackChanged),
            ('title',  'Title',    self.showTitleChanged),
            ('artist', 'Artist',   self.showArtistChanged),
            ('fav',    'Favorite', self.showFavChanged),
            ('genre',  'Genre',    self.showGenreChanged),
            ('dur',    'Duration', self.showDurChanged),
            ('plays',  'Plays',    self.showPlaysChanged),
        ]
        menu = themed_shadow_menu(self._view)
        for key, label, sig in cols:
            vis = bool(saved.get(key, True))
            text = ('✓  ' if vis else '    ') + label
            menu.add_action(text, lambda k=key, v=vis, s=sig: self._set_col_vis(k, not v, s))
        popup_menu_at_global(menu, int(gx), int(gy))

    def _set_col_vis(self, key: str, visible: bool, signal):
        saved = QSettings().value('playlist_detail/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        saved[key] = visible
        QSettings().setValue('playlist_detail/col_visibility', saved)
        signal.emit(visible)

    @pyqtSlot(int, int)
    def reorderTrack(self, from_idx: int, to_idx: int):
        view   = self._view
        tracks = view._tracks
        if not (0 <= from_idx < len(tracks)) or not (0 <= to_idx < len(tracks)):
            return
        track = tracks.pop(from_idx)
        tracks.insert(to_idx, track)
        view._track_model.move_track(from_idx, to_idx)
        if self._selected_trkidx == from_idx:
            self._selected_trkidx = to_idx
            self.selectedTrackChanged.emit(to_idx)
        pl_id  = view.current_playlist_id
        client = getattr(view, 'client', None)
        if pl_id and client:
            n   = len(tracks)
            ids = [str(t.get('id', '')) for t in tracks]
            threading.Thread(target=_persist_track_reorder,
                             args=(client, pl_id, n, ids), daemon=True).start()

    @pyqtSlot()
    def favHeaderClicked(self):
        pass  # future: sort by favourite


def _persist_track_reorder(client, pl_id, original_len, new_ids):
    try:
        client.update_playlist_tracks(pl_id, original_len, new_ids)
    except Exception as e:
        print(f"PlaylistDetail: reorder persist failed: {e}")


class _PlaylistKeyFilter(SearchKeyFilter):
    def __init__(self, bridge, qml_widget, parent=None):
        super().__init__(bridge.search, on_navigate=self._navigate, parent=parent)
        self._b   = bridge
        self._qml = qml_widget

    def _navigate(self, event):
        key  = event.key()
        mods = event.modifiers()
        b    = self._b
        if key == Qt.Key.Key_Down:
            b.navigateRow(1);  return True
        if key == Qt.Key.Key_Up:
            b.navigateRow(-1); return True
        if key == Qt.Key.Key_PageDown:
            b.navigateRow(max(5, self._qml.height() // 42)); return True
        if key == Qt.Key.Key_PageUp:
            b.navigateRow(-max(5, self._qml.height() // 42)); return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ControlModifier:
                b.playClicked(); return True
            if b._selected_trkidx >= 0:
                b.playSelected(); return True
        if key == Qt.Key.Key_Escape and b._selected_trkidx >= 0:
            b._selected_trkidx = -1
            b.selectedTrackChanged.emit(-1)
            return True
        return False


class PlaylistDetailView(QWidget):
    play_clicked         = pyqtSignal()
    shuffle_clicked      = pyqtSignal()
    track_play_signal    = pyqtSignal(list, int)
    track_artist_clicked = pyqtSignal(str)
    favorite_toggled     = pyqtSignal(str, bool)
    genre_clicked        = pyqtSignal(str)
    play_next_signal     = pyqtSignal(dict)
    queue_track_signal   = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client               = None
        self.current_playlist_id  = None
        self._tracks: list        = []
        self._is_public           = False
        self._bg_color            = '14,14,14'

        self._track_model        = PlaylistDetailTrackModel()
        self._cover_provider     = AlbumDetailCoverProvider()
        self._icon_provider      = AlbumIconProvider()
        self._track_thumb_prov   = _TrackThumbProvider()
        self._bridge             = PlaylistDetailBridge(self)

        self._qml_view = QQuickView()
        self._qml_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._qml = QWidget.createWindowContainer(self._qml_view, self)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._key_filter = _PlaylistKeyFilter(self._bridge, self._qml)
        self._qml.installEventFilter(self._key_filter)
        self._qml_view.installEventFilter(self._key_filter)

        engine = self._qml_view.engine()
        engine.addImageProvider("playlistdetailcover", self._cover_provider)
        engine.addImageProvider("playlisttrackcovers", self._track_thumb_prov)
        engine.addImageProvider("playlisticons",       self._icon_provider)
        engine.addImageProvider("albumicons",          self._icon_provider)  # SearchBar.qml hardcodes albumicons

        ctx = self._qml_view.rootContext()
        ctx.setContextProperty("playlistTrackModel",  self._track_model)
        ctx.setContextProperty("playlistDetailBridge", self._bridge)
        ctx.setContextProperty("scrollTuning",         scroll_tuning)

        self._qml_view.setSource(QUrl.fromLocalFile(
            resource_path("player/tabs/playlists/playlist_detail.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_tracks(self) -> list:
        return self._tracks

    def load_playlist(self, data: dict):
        self.current_playlist_id = data.get('id')
        self._is_public = bool(data.get('public', False))
        title  = data.get('title') or data.get('name') or 'Unknown Playlist'
        owner  = data.get('owner', '')

        self._bridge.search.reset()
        self._bridge._selected_trkidx = -1
        self._bridge.selectedTrackChanged.emit(-1)
        self._bridge.playlistDataChanged.emit(title, owner, "Loading...", '')
        self._bridge.publicStateChanged.emit(self._is_public)
        self._track_model.set_tracks([])
        self._tracks = []

        # Cover — try cache first
        cid = str(data.get('cover_id') or data.get('coverArt') or '')
        if cid:
            from player.components.cover_cache import CoverCache
            img = CoverCache.instance().get_full(cid) or CoverCache.instance().get_thumb(cid)
            if img:
                self._cover_provider.cache[cid] = img
                self._bridge.coverIdChanged.emit(cid)
            threading.Thread(target=lambda: self._fetch_cover(cid), daemon=True).start()

    def populate_tracks(self, playlist_data: dict, tracks: list):
        self._tracks = tracks
        self._track_model.set_tracks(tracks)
        if self.client:
            self._track_thumb_prov.set_client(self.client)
        self._bridge._selected_trkidx = -1
        self._bridge.selectedTrackChanged.emit(-1)

        title  = playlist_data.get('title') or playlist_data.get('name') or 'Unknown Playlist'
        owner  = playlist_data.get('owner', '')
        dur    = sum(t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000 for t in tracks)
        secs   = dur // 1000
        hrs, rem = divmod(secs, 3600)
        mins = rem // 60
        time_str = f"{hrs} hr {mins} min" if hrs else f"{mins} min"
        meta = f"{len(tracks)} songs • {time_str}"

        cid = str(playlist_data.get('cover_id') or playlist_data.get('coverArt') or '')
        self._bridge.playlistDataChanged.emit(title, owner, meta, cid)
        self._bridge.publicStateChanged.emit(bool(playlist_data.get('public', False)))
        self._is_public = bool(playlist_data.get('public', False))

        if cid and cid not in self._cover_provider.cache:
            threading.Thread(target=lambda: self._fetch_cover(cid), daemon=True).start()

    def _fetch_cover(self, cid: str):
        try:
            client = getattr(self, 'client', None)
            if not client:
                return
            data = client.get_cover_art(cid)
            if data:
                self._cover_provider.cache[cid] = data
                from player.components.cover_cache import CoverCache
                CoverCache.instance().save_full(cid, data)
                self._bridge.coverIdChanged.emit(cid)
        except Exception as e:
            print(f"[PlaylistDetailView] cover fetch error: {e}")

    def _toggle_public(self):
        new_state = not self._is_public
        self._is_public = new_state
        self._bridge.publicStateChanged.emit(new_state)
        client = getattr(self, 'client', None)
        pid    = self.current_playlist_id
        if client and pid:
            def _save():
                try:
                    import requests
                    params = client._get_auth_params()
                    params['playlistId'] = pid
                    params['public'] = 'true' if new_state else 'false'
                    requests.get(f"{client.base_url}/rest/updatePlaylist",
                                 params=params, timeout=10)
                except Exception as e:
                    print(f"[PlaylistDetailView] toggle public error: {e}")
            threading.Thread(target=_save, daemon=True).start()

    def _show_track_context_menu_at(self, track_idx: int, gx: int, gy: int):
        if not (0 <= track_idx < len(self._tracks)):
            return
        track  = self._tracks[track_idx]
        main   = self.window()
        menu   = themed_shadow_menu(self, bg=getattr(self, '_bg_color', None))
        track_id = str(track.get('id', ''))
        artist   = track.get('artist', '')

        menu.add_action('Play Now',
                        lambda: self.track_play_signal.emit([track], 0),
                        icon_path='img/sub_play.png')
        menu.add_action('Play Next',
                        lambda: main.play_track_next(track) if hasattr(main, 'play_track_next') else None,
                        icon_path='img/sub_next.png')
        menu.add_action('Add to Queue',
                        lambda: main.add_track_to_queue(track) if hasattr(main, 'add_track_to_queue') else None,
                        icon_path='img/queue.png')
        menu.add_action('Go to Artist',
                        lambda: self.track_artist_clicked.emit(artist) if artist else None,
                        enabled=bool(artist), icon_path='img/sub_artist.png')
        menu.add_action('Start Radio',
                        lambda: main.start_radio(track) if hasattr(main, 'start_radio') else None,
                        icon_path='img/radio.png')
        playlists = getattr(getattr(main, 'playlists_browser', None), 'all_playlists', None) or []
        if track_id:
            pl_items = [('New Playlist…', lambda: self._add_to_new_playlist(main, [track_id]), 'img/add.png')]
            pl_items += [(f"{pl.get('name','Unnamed')}  ({pl.get('songCount','')})" if pl.get('songCount','') != '' else pl.get('name','Unnamed'),
                          lambda pid=pl.get('id'): self._add_to_existing_playlist(main, pid, [track_id]),
                          'img/playlist.png')
                         for pl in playlists if pl.get('id')]
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')
        tb = getattr(main, 'tracks_browser', None)
        menu.add_action('Get Info',
                        callback=(lambda: tb._show_track_info(track)) if tb else None,
                        enabled=bool(tb), icon_path='img/info.png')
        raw_star = track.get('starred', False)
        is_fav   = raw_star.lower() in ('true', '1') if isinstance(raw_star, str) else bool(raw_star)
        menu.add_action('Remove from Favorites' if is_fav else 'Add to Favorites',
                        lambda i=track_idx: self._bridge.trackFavoriteClicked(i),
                        color='#E91E63',
                        icon_path='img/heart_filled.png' if is_fav else 'img/heart.png')
        popup_menu_at_global(menu, gx, gy, window=main)

    def _add_to_new_playlist(self, main, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client: return
        from player.components.shared_widgets import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        accent = getattr(main, 'master_color', '#1DB954')
        dialog = NewPlaylistDialog(self, accent_color=accent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name: return
            def _worker():
                try:
                    new_id = client.create_playlist(name, public=dialog.is_public())
                    if new_id: client.add_tracks_to_playlist(new_id, track_ids)
                except Exception as e:
                    print(f"PlaylistDetail: create playlist failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _add_to_existing_playlist(self, main, pl_id, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client: return
        threading.Thread(
            target=lambda: client.add_tracks_to_playlist(pl_id, track_ids),
            daemon=True).start()

    def _set_window_shortcuts_enabled(self, enabled: bool):
        set_window_shortcuts_enabled(self, self._qml, enabled)

    def update_playing_status(self, playing_id, is_playing: bool, _accent: str = ''):
        self._last_playing_id = playing_id
        self._last_is_playing = is_playing
        if not playing_id and is_playing:
            return
        self._bridge.playingStatusChanged.emit(str(playing_id) if playing_id else '', is_playing)

    def set_bg_color(self, c: str):
        self._bg_color = c
        try:
            r, g, b = (int(x) for x in c.split(','))
            self._qml_view.setColor(QColor(r, g, b))
        except Exception:
            pass

    def set_accent_color(self, color: str):
        theme = getattr(self.window(), 'theme', None)
        self._bridge.accentColorChanged.emit(color)
        self._bridge.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')
        if theme:
            self._bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
            self._bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            self._bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
            self._bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
            self._bridge.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))
            self._bridge.skeletonColorChanged.emit(getattr(theme, 'skeleton_base', '#282828'))
            self._bridge.cardBgChanged.emit(getattr(theme, 'now_playing_card_bg', '#1e1e1e'))
            border = getattr(theme, 'border_color', '#2a2a2a')
            if not getattr(theme, 'auto_border_from_accent', True):
                border = getattr(theme, 'manual_border_color', '#2a2a2a')
            self._bridge.cardBorderChanged.emit(border)
            raw_bg = getattr(theme, 'main_panel_bg', '14,14,14')
            try:
                r, g, b = (int(x) for x in raw_bg.split(','))
                self._bridge.panelBgChanged.emit('#{:02x}{:02x}{:02x}'.format(r, g, b))
            except Exception:
                self._bridge.panelBgChanged.emit('#0e0e0e')


class PlaylistsBrowser(QWidget):
    play_track_signal = pyqtSignal(dict)
    play_album_signal = pyqtSignal(list) 
    queue_track_signal = pyqtSignal(dict)
    play_next_signal = pyqtSignal(dict)
    switch_to_artist_tab = pyqtSignal(str)
    playlist_clicked = pyqtSignal(dict, object)
    album_clicked = pyqtSignal(dict)

    def __init__(self, client=None):
        super().__init__()
        self.client = client
        self.current_accent = "#0066cc"
        self.current_query = ""
        self.all_playlists = []

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("PlaylistsBrowser")
        self.setStyleSheet("#PlaylistsBrowser { background-color: rgba(12, 12, 12, 0.3); border-radius: 0; }")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- 1. HEADER ---
        self.header_container = QWidget()
        self.header_container.setFixedHeight(50)
        self.header_container.setStyleSheet("QWidget { background-color: #111; border-top-left-radius: 5px; border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        
        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(15, 0, 10, 0) 
        header_layout.setSpacing(15)
        
        self.status_label = QLabel("0 Playlists")
        self.status_label.setStyleSheet("color: #888; font-weight: bold; background: transparent; border: none;")
        
        self.search_container = SmartSearchContainer(placeholder="Search playlists...")
        self.search_container.text_changed.connect(self.on_search_text_changed)
        
        self.burger_btn = self.search_container.get_burger_btn()
        self.burger_btn.setToolTip("Create New Playlist")
        
        
        try: self.burger_btn.clicked.disconnect()
        except: pass
        
        self.burger_btn.clicked.connect(self.on_add_playlist_clicked)
        
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()
        header_layout.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)

        self.main_layout.addWidget(self.header_container)
        
        # --- 2. MAIN CONTENT STACK ---
        self.stack = QStackedWidget()

        # QML Grid View
        self.qml_view = QMLGridWrapper()
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_view.setClearColor(self._qml_bg_color())
        self.qml_view.setStyleSheet("border: none;")

        self.playlist_model = PlaylistModel()
        self.grid_bridge = PlaylistBridge(self.playlist_model)

        self.grid_bridge.itemClicked.connect(self.on_playlist_data_clicked)
        self.grid_bridge.playClicked.connect(self.on_playlist_play_clicked)
        self.grid_bridge.itemRightClicked.connect(self.show_item_context_menu)
        self.grid_bridge.backgroundRightClicked.connect(self.show_bg_context_menu)
        self.grid_bridge.keyTextForwarded.connect(self._on_key_forwarded)
        self.grid_bridge.slashPressed.connect(self._on_slash_pressed)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("playlistModel", self.playlist_model)
        ctx.setContextProperty("playlistBridge", self.grid_bridge)

        engine = self.qml_view.engine()
        self.cover_provider = CoverImageProvider()
        engine.addImageProvider("plcovers", self.cover_provider)

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("player/tabs/playlists/playlist_grid.qml")))

        self.grid_view = self.qml_view  # alias so existing code stays compatible
        
        # Detail View
        self.detail_view = PlaylistDetailView()
        self.detail_view.play_clicked.connect(self.play_current_playlist)
        self.detail_view.shuffle_clicked.connect(self.shuffle_current_playlist)
        self.detail_view.track_play_signal.connect(
            lambda tracks, idx: self.play_album_signal.emit([tracks[idx]] if 0 <= idx < len(tracks) else []))
        self.detail_view.queue_track_signal.connect(self.queue_track_signal.emit)
        self.detail_view.play_next_signal.connect(self.play_next_signal.emit)
        self.detail_view.track_artist_clicked.connect(self.switch_to_artist_tab.emit)
        
        self.stack.addWidget(self.grid_view)
        self.stack.addWidget(self.detail_view)
        self.main_layout.addWidget(self.stack)

        self.nav_history = []
        self.nav_index = -1
        self.current_album_id = None
        self.cover_worker = None
        self.add_to_history({'type': 'root'})
    
    def show_bg_context_menu(self):
        """Shows menu when clicking empty space in the grid."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        
        _theme = getattr(self.window(), 'theme', None)
        _bc  = getattr(_theme, 'border_color',       '#444444')
        _fg  = getattr(_theme, 'font_color_primary', '#dddddd')
        _px  = getattr(_theme, 'font_size_primary',  14)
        _acc = getattr(_theme, 'accent',              '#ffffff')
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: #222; color: {_fg}; font-size: {_px}px; border: 1px solid {_bc}; border-radius: 4px; padding: 4px; }} QMenu::item {{ padding: 6px 20px; border-radius: 4px; }} QMenu::item:selected {{ background-color: {resolve_menu_hover(_theme)}; color: {_fg}; }}")

        add_action = menu.addAction("Add New Playlist")
        
        action = menu.exec(QCursor.pos())
        if action == add_action:
            self.on_add_playlist_clicked()

    def show_item_context_menu(self, idx):
        """Shows rename/delete menu when right-clicking a playlist."""
        if not (0 <= idx < len(self.playlist_model.playlists)): return
        playlist = self.playlist_model.playlists[idx]
        
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        
        _theme = getattr(self.window(), 'theme', None)
        _bc  = getattr(_theme, 'border_color',       '#444444')
        _fg  = getattr(_theme, 'font_color_primary', '#dddddd')
        _px  = getattr(_theme, 'font_size_primary',  14)
        _acc = getattr(_theme, 'accent',              '#ffffff')
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: #222; color: {_fg}; font-size: {_px}px; border: 1px solid {_bc}; border-radius: 4px; padding: 4px; }} QMenu::item {{ padding: 6px 20px; border-radius: 4px; }} QMenu::item:selected {{ background-color: {resolve_menu_hover(_theme)}; color: {_fg}; }}")

        rename_action = menu.addAction("Rename Playlist")
        delete_action = menu.addAction("Delete Playlist")
        
        action = menu.exec(QCursor.pos())
        
        if action == rename_action:
            self.rename_playlist_dialog(playlist)
        elif action == delete_action:
            self.delete_playlist_dialog(playlist)

    def rename_playlist_dialog(self, playlist):
        from PyQt6.QtWidgets import QInputDialog, QMessageBox, QLineEdit
        
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Rename Playlist")
        dialog.setLabelText("Enter a new name:")
        
        # Pre-fill the current name
        current_name = playlist.get('name', playlist.get('title', ''))
        dialog.setTextValue(current_name)
        dialog.setTextEchoMode(QLineEdit.EchoMode.Normal)
        
        # Use your custom dark styling
        dialog.setStyleSheet("""
            QInputDialog, QDialog { background-color: #121212; }
            QLabel { color: white; font-size: 13px; font-weight: bold; }
            QLineEdit { background-color: #222; color: white; border: 1px solid #444; border-radius: 4px; padding: 5px; font-size: 13px; }
            QPushButton { background-color: #333; color: white; border: none; border-radius: 4px; padding: 6px 15px; font-weight: bold; }
            QPushButton:hover { background-color: #555; }
        """)
        
        if dialog.exec() == QInputDialog.DialogCode.Accepted:
            new_name = dialog.textValue().strip()
            if new_name and new_name != current_name:
                try:
                    self.client.rename_playlist(playlist.get('id'), new_name)
                    self.load_playlists()
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to rename: {e}")

    def delete_playlist_dialog(self, playlist):
        from PyQt6.QtWidgets import QMessageBox
        
        name = playlist.get('name', playlist.get('title', ''))
        
        # 1. Create the message box manually
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Playlist")
        msg_box.setText(f"Delete '{name}'?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        
        # 2. Apply the solid dark stylesheet
        dark_style = """
            QMessageBox, QDialog {
                background-color: #121212;
            }
            QLabel {
                color: white;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #333;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 15px;
                font-weight: bold;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """
        msg_box.setStyleSheet(dark_style)
        
        # 3. Execute and check the result
        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            try:
                self.client.delete_playlist(playlist.get('id'))
                self.load_playlists()
            except Exception as e:
                # Make sure the error popup is also styled nicely!
                err_box = QMessageBox(self)
                err_box.setWindowTitle("Error")
                err_box.setText(f"Failed to delete: {e}")
                err_box.setIcon(QMessageBox.Icon.Warning)
                err_box.setStyleSheet(dark_style)
                err_box.exec()
    
    def on_add_playlist_clicked(self):
        """Opens the custom frameless dialog to create a new playlist and refreshes the grid."""
        from player.components.shared_widgets import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        import threading
        from PyQt6.QtCore import QMetaObject, Qt
        
        if not self.client: 
            return
        
        accent = getattr(self, 'current_accent', "#1DB954")
        _theme = getattr(self.window(), 'theme', None)
        _bg = getattr(_theme, 'main_panel_bg', '30,30,30') if _theme else '30,30,30'
        bg_color = f"rgb({_bg})"
        border_color = getattr(_theme, 'border_color',         '#333333') if _theme else '#333333'
        border_width = getattr(_theme, 'border_width',         1)         if _theme else 1
        fg_primary   = getattr(_theme, 'font_color_primary',   '#dddddd') if _theme else '#dddddd'
        fg_secondary = getattr(_theme, 'font_color_secondary', '#999999') if _theme else '#999999'
        hover_color  = resolve_menu_hover(_theme)

        dialog = NewPlaylistDialog(self.window(), accent_color=accent, bg_color=bg_color,
                                   border_color=border_color, border_width=border_width,
                                   fg_primary=fg_primary, fg_secondary=fg_secondary,
                                   hover_color=hover_color)

        main_win = self.window()
        if hasattr(main_win, 'show_dim'):
            main_win.show_dim()
        result = dialog.exec()
        if hasattr(main_win, 'hide_dim'):
            main_win.hide_dim()

        if result == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name: 
                return
            
        
            def worker():
                try:
                    self.client.create_playlist(name)
                    
                    # Safely tell the main thread to refresh the grid using your existing method!
                    QMetaObject.invokeMethod(self, "load_playlists", Qt.ConnectionType.QueuedConnection)
                        
                except Exception as e:
                    print(f"Failed to create playlist: {e}")
                    
            threading.Thread(target=worker, daemon=True).start()
    
    def _on_key_forwarded(self, text):
        """QML forwards printable keystrokes to Spotlight."""
        main_win = self.window()
        if main_win and hasattr(main_win, 'keyPressEvent'):
            from PyQt6.QtGui import QKeyEvent
            from PyQt6.QtCore import QEvent
            fake = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, text)
            main_win.keyPressEvent(fake)

    def _on_slash_pressed(self):
        """QML forwards '/' to open local search."""
        if hasattr(self, 'search_container'):
            self.search_container.show_search()
            QTimer.singleShot(50, self.search_container.search_input.setFocus)

    def eventFilter(self, source, event):
        return super().eventFilter(source, event)

    def add_to_history(self, state):
        if not hasattr(self, 'nav_history'):
            self.nav_history = []
            self.nav_index = -1
            
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_history = self.nav_history[:self.nav_index + 1]
        self.nav_history.append(state)
        self.nav_index += 1
        
        if len(self.nav_history) > 20:
            self.nav_history = self.nav_history[-20:]
            self.nav_index = len(self.nav_history) - 1
            
        self.render_state(state)

    def on_nav_back(self):
        if hasattr(self, 'nav_index') and self.nav_index > 0:
            self.nav_index -= 1
            self.render_state(self.nav_history[self.nav_index])

    def on_nav_fwd(self):
        if hasattr(self, 'nav_index') and self.nav_index < len(self.nav_history) - 1:
            self.nav_index += 1
            self.render_state(self.nav_history[self.nav_index])
       
    def set_client(self, client):
        self.client = client
        self.detail_view.client = client
        self.detail_view._track_thumb_prov.set_client(client)
        if not self.cover_worker:
            self.cover_worker = GridCoverWorker(client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()
        # Eagerly fetch playlists in the background so they're available for
        # the right-click context menu even before the Playlists tab is opened.
        if self.client:
            self.load_playlists()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, 'all_playlists', []) and self.client:
            self.load_playlists()
        else:
            self.qml_view.setFocus()
    
    @pyqtSlot()
    def load_playlists(self):
        if not self.client: return
        self.worker = PlaylistsWorker(self.client)
        self.worker.results_ready.connect(self.on_playlists_loaded)
        self.worker.start()

    def on_playlists_loaded(self, playlists):
        self.all_playlists = playlists
        self.refresh_grid()
    
    @pyqtSlot()
    def refresh_grid(self):
        query = self.current_query.lower()
        filtered = [p for p in getattr(self, 'all_playlists', []) if query in p.get('name', '').lower()]

        processed = []
        for p in filtered:
            p_data = p.copy()
            p_data['title']    = p.get('name', 'Unknown Playlist')
            p_data['subtitle'] = f"{p.get('songCount', 0)} tracks"
            p_data['cover_id'] = p.get('coverArt', '')
            processed.append(p_data)

        self.playlist_model.reset_data(processed)
        self.status_label.setText(f"{len(processed)} Playlists")

        if self.cover_worker:
            for p_data in processed:
                cid = p_data.get('cover_id')
                if cid:
                    self.cover_worker.queue_cover(cid, priority=False)

        search_has_focus = (hasattr(self, 'search_container') and
                            hasattr(self.search_container, 'search_input') and
                            self.search_container.search_input.hasFocus())
        if not search_has_focus and hasattr(self, 'qml_view') and self.isVisible():
            self.qml_view.setFocus()

    def start_play_fetch(self, data):
        self.instant_play_worker = PlaylistTracksWorker(self.client, data)
        self.instant_play_worker.results_ready.connect(self._on_instant_play_ready)
        self.instant_play_worker.start()

    def _on_instant_play_ready(self, playlist_data, tracks):
        if tracks and hasattr(self, 'play_album_signal'):
            self.play_album_signal.emit(tracks)

    def play_current_playlist(self):
        tracks = getattr(self.detail_view, '_tracks', [])
        if tracks:
            self.play_album_signal.emit(tracks)

    def shuffle_current_playlist(self):
        tracks = list(getattr(self.detail_view, '_tracks', []))
        if tracks:
            import random
            random.shuffle(tracks)
            self.play_album_signal.emit(tracks)

    def render_state(self, state):
        s_type = state.get('type')
        self.current_album_id = None # Tricks main.py into knowing we left detail view
        
        if s_type == 'root':
            main_win = self.window()
            if hasattr(main_win, 'update_indicator'):
                main_win.update_indicator()
            self.go_to_root(record_history=False)
        elif s_type == 'playlist':
            self.open_playlist_detail(state['data'], state.get('pixmap'), record_history=False)

    def on_playlist_data_clicked(self, data):
        """Called by bridge when user clicks a playlist card."""
        if not data: return
        
        
        pixmap = None
        cid = str(data.get('cover_id') or data.get('coverArt') or '')
        
        if cid and hasattr(self, 'cover_provider'):
            img_data = self.cover_provider.image_cache.get(cid)
            if img_data:
                from PyQt6.QtGui import QPixmap
                pix = QPixmap()
                pix.loadFromData(img_data)
                if not pix.isNull():
                    pixmap = pix
                    
        # Now we emit BOTH the data and the actual image!
        self.playlist_clicked.emit(data, pixmap)

    def on_playlist_play_clicked(self, data):
        """Called by bridge when user clicks the play button on a playlist card."""
        if not data: return
        self.start_play_fetch(data)

    def show_album_details(self, data, record_history=True):
        self.open_playlist_detail(data, None, record_history)

    def open_playlist_detail(self, data, pixmap, record_history=True):
        if record_history:
            self.add_to_history({'type': 'playlist', 'data': data, 'pixmap': pixmap})
            return

        self.current_album_id = data.get('id')

        # Seed the cover provider cache from the grid's image cache before load_playlist
        cid = str(data.get('cover_id') or data.get('coverArt') or '')
        if cid and hasattr(self, 'cover_provider'):
            img_data = self.cover_provider.image_cache.get(cid)
            if img_data:
                self.detail_view._cover_provider.cache[cid] = img_data

        if hasattr(self, 'main_layout'):
            for i in range(self.main_layout.count()):
                widget = self.main_layout.itemAt(i).widget()
                if widget and widget != getattr(self, 'stack', None):
                    widget.hide()

        self.stack.setCurrentIndex(1)
        self.detail_view.load_playlist(data)

        self.tracks_worker = PlaylistTracksWorker(self.client, data)
        self.tracks_worker.results_ready.connect(self.detail_view.populate_tracks)
        self.tracks_worker.start()

    def go_to_root(self, record_history=True):
        if record_history:
            self.add_to_history({'type': 'root'})
            return
            
        if hasattr(self, 'main_layout'):
            for i in range(self.main_layout.count()):
                widget = self.main_layout.itemAt(i).widget()
                if widget and widget != getattr(self, 'stack', None):
                    widget.show()
            
        if hasattr(self, 'search_container'):
            self.search_container.show_search()
            self.search_container.show_burger()
            
        self.stack.setCurrentIndex(0)

        if hasattr(self, 'qml_view') and self.isVisible():
            self.qml_view.setFocus()

        if getattr(self, 'current_query', ""):
            self.current_query = ""
            if hasattr(self, 'search_container'):
                self.search_container.search_input.blockSignals(True)
                self.search_container.search_input.clear()
                self.search_container.search_input.blockSignals(False)
        
        self.nav_history = [{'type': 'root'}]
        self.nav_index = 0
        self.current_album_id = None
        
        self.load_playlists()

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.refresh_grid()

    def apply_cover(self, cover_id, image_data):
        if hasattr(self, 'cover_provider'):
            self.cover_provider.image_cache[str(cover_id)] = image_data
        if hasattr(self, 'playlist_model'):
            self.playlist_model.update_cover(str(cover_id))

    def _qml_bg_color(self):
        r, g, b = (int(x) for x in getattr(self, '_bg_color', '14,14,14').split(','))
        return QColor(r, g, b)

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")
        self.detail_view.set_bg_color(c)
        if hasattr(self, 'qml_view'):
            self.qml_view.setClearColor(self._qml_bg_color())

    def set_accent_color(self, color):
        self.current_accent = color
        self.setStyleSheet(f"#PlaylistsBrowser {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")
        if hasattr(self, 'header_container'):
            self.header_container.setStyleSheet(
                "QWidget { background-color: transparent; border-bottom: 1px solid rgba(255,255,255,0.06); }"
            )
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(1.0)
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.grid_bridge.fontColorPrimaryChanged.emit(getattr(theme, 'font_color_primary',    '#eeeeee'))
                self.grid_bridge.fontColorSecondaryChanged.emit(getattr(theme, 'font_color_secondary','#999999'))
                self.grid_bridge.fontSizePrimaryChanged.emit(getattr(theme, 'font_size_primary',      13))
                self.grid_bridge.fontSizeSecondaryChanged.emit(getattr(theme, 'font_size_secondary',  12))
        if hasattr(self, 'status_label'):
            _theme = getattr(self.window(), 'theme', None)
            _sec_color = getattr(_theme, 'font_color_secondary', '#888888') if _theme else '#888888'
            self.status_label.setStyleSheet(
                f"color: {_sec_color}; font-weight: bold; background: transparent; border: none;"
            )
        self.detail_view.set_accent_color(color)
        if hasattr(self, 'search_container'):
            from player.mixins.visuals import resolve_menu_hover
            _theme = getattr(self.window(), 'theme', None)
            self.search_container.apply_input_theme(
                bg           = getattr(_theme, 'main_panel_bg',        '14,14,14') if _theme else '14,14,14',
                border_color = getattr(_theme, 'border_color',         '#2a2a2a')  if _theme else '#2a2a2a',
                border_width = getattr(_theme, 'border_width',         1)          if _theme else 1,
                fg_primary   = getattr(_theme, 'font_color_primary',   '#dddddd')  if _theme else '#dddddd',
                fg_secondary = getattr(_theme, 'font_color_secondary', '#999999')  if _theme else '#999999',
                hover_color  = resolve_menu_hover(_theme),
                accent_color = color,
            )
        if hasattr(self, 'burger_btn'):
            try:
                from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QPen
                from PyQt6.QtCore import Qt
                
                # Create a crisp 24x24 transparent canvas
                pixmap = QPixmap(24, 24)
                pixmap.fill(QColor(0, 0, 0, 0))
                
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                
                # Draw a clean, modern Plus sign matching the master color
                pen = QPen(QColor(color))
                pen.setWidth(3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                
                painter.drawLine(12, 4, 12, 20) # Vertical line
                painter.drawLine(4, 12, 20, 12) # Horizontal line
                painter.end()
                
                self.burger_btn.setIcon(QIcon(pixmap))
            except Exception as e: 
                print(f"Failed to draw plus icon: {e}")