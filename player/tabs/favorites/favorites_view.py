"""favorites_view.py — Favorites tab: starred artists, albums, top artists
and songs. QML (Carousel.qml + TrackListView.qml), per UI_MANIFEST.md."""
import threading
import time
from collections import Counter

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QLineEdit, QFrame, QPushButton
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, pyqtSlot, pyqtProperty, QTimer, QPoint,
                          QAbstractListModel, QModelIndex, QObject, QUrl, QSettings)
from PyQt6.QtGui import QColor, QPainter as _QPainter, QCursor
from PyQt6.QtQuick import QQuickView

from player.tabs.tracks.tracks_browser import _checkmark_svg_path
from player import resource_path
from player.workers import GridCoverWorker
from player.mixins.visuals import scrollbar_css, resolve_menu_hover
from player.widgets import (AlbumModel, AlbumIconProvider, AlbumDetailCoverProvider,
                             CoverImageProvider, TrackThumbProvider,
                             themed_shadow_menu, popup_menu_at_global)
from player.qml_search import SearchController, SearchKeyFilter, set_window_shortcuts_enabled
from player.scroll_tuning import scroll_tuning


class _GenrePopup(QFrame):
    """Genre filter popup — same shadow/border style as ShadowContextMenu."""
    selection_changed = pyqtSignal(set)
    _PAD = 24   # shadow padding

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.WindowType.Popup |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        pad = self._PAD
        self.setFixedWidth(240 + 2 * pad)
        self._genres: list[str] = []
        self._selected: set[str] = set()
        self._paint_bg = QColor(20, 20, 20)
        self._paint_bc = QColor(42, 42, 42)

        lo = QVBoxLayout(self)
        lo.setContentsMargins(pad + 8, pad + 8, pad + 8, pad + 8)
        lo.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText('Search genres…')
        self._search.textChanged.connect(self._rebuild)
        lo.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFixedHeight(200)
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.itemClicked.connect(self._on_item_clicked)
        lo.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._ok_btn = QPushButton('Apply')
        self._ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_btn.clicked.connect(self.hide)
        self._clear_btn = QPushButton('Clear')
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear)
        btn_row.addStretch()
        btn_row.addWidget(self._clear_btn)
        btn_row.addWidget(self._ok_btn)
        lo.addLayout(btn_row)

    def apply_theme(self, theme, accent: str, hov: str):
        bg  = getattr(theme, 'main_panel_bg', '20,20,20') if theme else '20,20,20'
        bc  = getattr(theme, 'border_color',  '#2a2a2a') if theme else '#2a2a2a'
        fg2 = getattr(theme, 'font_color_secondary', '#888888') if theme else '#888888'
        try:
            self._paint_bg = QColor(*[int(x) for x in bg.split(',')])
        except Exception:
            self._paint_bg = QColor(20, 20, 20)
        if theme and not getattr(theme, 'auto_border_from_accent', True):
            self._paint_bc = QColor(getattr(theme, 'manual_border_color', '#2a2a2a'))
        else:
            self._paint_bc = QColor(bc)
        self.setStyleSheet(f"""
            QLineEdit {{
                background: rgb({bg}); color: {fg2}; border: 1px solid {bc};
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
            }}
            QListWidget {{
                background: transparent; border: none; color: {fg2}; font-size: 13px;
            }}
            QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
            QListWidget::item:hover {{ background: {hov}; }}
            QListWidget::item:selected {{ background: transparent; color: {fg2}; }}
            QListWidget::item:focus {{ background: transparent; outline: none; }}
            QListWidget::item:selected:active {{ background: transparent; }}
            QListWidget:focus {{ outline: none; border: none; }}
            QListWidget::indicator {{
                width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid {bc}; background: rgb({bg});
            }}
            QListWidget::indicator:checked {{
                background: rgb({bg});
                image: url("{_checkmark_svg_path(accent)}");
            }}
            QPushButton {{
                background: transparent; color: {fg2}; border: 1px solid {bc};
                border-radius: 4px; padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {hov}; }}
            {scrollbar_css(accent)}
        """)
        from PyQt6.QtGui import QPalette as _Pal
        pal = self._search.palette()
        pal.setColor(_Pal.ColorRole.PlaceholderText, QColor(fg2))
        self._search.setPalette(pal)
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtCore import QRectF
        p = _QPainter(self)
        p.setRenderHint(_QPainter.RenderHint.Antialiasing)
        pad = self._PAD
        content = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        BLUR = 22; OY = 8; MAX_A = 45
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(16, 0, -1):
            t = i / 16; alpha = int(MAX_A * (1 - t) ** 2); ex = BLUR * t
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(content.adjusted(-ex*.7, -ex*.4+OY*(1-t), ex*.7, ex+OY*t),
                              8 + ex*.2, 8 + ex*.2)
        p.setPen(self._paint_bc)
        p.setBrush(self._paint_bg)
        p.drawRoundedRect(content, 8, 8)
        p.end()

    def set_genres(self, genres: list[str], selected: set[str]):
        self._genres = sorted(genres)
        self._selected = set(selected)
        self._rebuild()

    def _rebuild(self):
        q = self._search.text().lower()
        self._list.blockSignals(True)
        self._list.clear()
        for g in self._genres:
            if q and q not in g.lower():
                continue
            item = QListWidgetItem(g)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if g in self._selected else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem):
        # Only toggle when clicking the TEXT area — checkbox click already
        # toggles via itemChanged; toggling here too would reverse it.
        vp_pos = self._list.viewport().mapFromGlobal(QCursor.pos())
        item_rect = self._list.visualItemRect(item)
        if vp_pos.x() - item_rect.left() > 22:
            new_state = (Qt.CheckState.Unchecked
                         if item.checkState() == Qt.CheckState.Checked
                         else Qt.CheckState.Checked)
            item.setCheckState(new_state)

    def _on_item_changed(self, item: QListWidgetItem):
        g = item.text()
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(g)
        else:
            self._selected.discard(g)
        self.selection_changed.emit(set(self._selected))

    def _clear(self):
        self._selected.clear()
        self._rebuild()
        self.selection_changed.emit(set())


class _StarredWorker(QThread):
    done = pyqtSignal(dict)   # {'songs': [...], 'albums': [...], 'artists': [...]}

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            data = self._client.get_starred_all()
        except Exception as e:
            print(f"[Favorites] fetch error: {e}")
            data = {'songs': [], 'albums': [], 'artists': []}
        self.done.emit(data)


def _format_date_added(raw: str) -> str:
    """ISO 8601 'created' timestamp -> '15 Dec 2024' (matches the Tracks tab)."""
    if not raw:
        return ''
    try:
        import platform as _platform
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(raw.replace('Z', '+00:00'))
        fmt = '%#d %b %Y' if _platform.system() == 'Windows' else '%-d %b %Y'
        return dt.strftime(fmt)
    except Exception:
        return raw[:10]


class FavoritesTrackModel(QAbstractListModel):
    """Role set mirrors PlaylistDetailTrackModel — kept local (not imported
    cross-tab) since each *DetailTrackModel is per-tab by convention."""
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
    ALBUM_NAME     = Qt.ItemDataRole.UserRole + 11
    ALBUM_ID       = Qt.ItemDataRole.UserRole + 12
    ALBUM_TRACK_NO = Qt.ItemDataRole.UserRole + 13
    YEAR_STR       = Qt.ItemDataRole.UserRole + 14
    DATE_ADDED_STR = Qt.ItemDataRole.UserRole + 15
    BPM_STR        = Qt.ItemDataRole.UserRole + 16

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
        if role == self.ALBUM_NAME:     return r.get('_album', '')
        if role == self.ALBUM_ID:       return r.get('_album_id', '')
        if role == self.ALBUM_TRACK_NO: return r.get('_album_track_no', '')
        if role == self.YEAR_STR:       return r.get('_year', '')
        if role == self.DATE_ADDED_STR: return r.get('_date_added', '')
        if role == self.BPM_STR:        return r.get('_bpm', '')
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
            self.ALBUM_NAME:     b"albumName",
            self.ALBUM_ID:       b"albumId",
            self.ALBUM_TRACK_NO: b"albumTrackNo",
            self.YEAR_STR:       b"yearStr",
            self.DATE_ADDED_STR: b"dateAddedStr",
            self.BPM_STR:        b"bpmStr",
        }

    @staticmethod
    def _build_rows(tracks: list) -> list:
        rows = []
        for idx, t in enumerate(tracks):
            raw_star = t.get('starred', False)
            is_fav   = raw_star.lower() in ('true', '1') if isinstance(raw_star, str) else bool(raw_star)
            dur_ms   = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
            secs     = dur_ms // 1000
            plays    = str(t.get('play_count') or 0) if t.get('play_count') else '-'
            genre_raw = t.get('genre', '') or ''
            for sep in ['; ', ';', ' | ', '|', ' / ', '/']:
                genre_raw = genre_raw.replace(sep, ' • ')
            genre_parts = [g.strip() for g in genre_raw.split(' • ') if g.strip()]
            album_track_no = t.get('trackNumber') or t.get('track') or ''
            raw_bpm = t.get('bpm') or ''
            try:
                bpm_val = float(raw_bpm)
                bpm_str = f"{bpm_val:.1f}" if bpm_val > 0 else ''
            except (ValueError, TypeError):
                bpm_str = ''
            rows.append({
                '_idx':       idx,
                '_id':        str(t.get('id', '')),
                '_num':       str(idx + 1),
                '_title':     t.get('title', ''),
                '_artist':    t.get('artist', ''),
                '_fav':       is_fav,
                '_dur':       f"{secs // 60}:{secs % 60:02d}",
                '_plays':     plays,
                '_genre':     ' • '.join(genre_parts[:3]),
                '_cover_id':  str(t.get('coverArt') or t.get('albumId') or ''),
                '_album':     t.get('album', ''),
                '_album_id':  str(t.get('albumId', '')),
                '_album_track_no': str(album_track_no) if album_track_no else '',
                '_year':           str(t.get('year') or ''),
                '_date_added':     _format_date_added(t.get('created') or ''),
                '_bpm':            bpm_str,
            })
        return rows

    def set_tracks(self, tracks: list):
        self.beginResetModel()
        self._rows = self._build_rows(tracks)
        self.endResetModel()

    def update_tracks(self, tracks: list):
        """Replace the row set without ever doing a full beginResetModel().
        A reset briefly collapses the view to zero rows, which clamps
        contentY back near the top and never restores it — this updates
        overlapping rows in place and only inserts/removes the row-count
        delta, so filtering/clearing filters doesn't reset scroll position."""
        new_rows  = self._build_rows(tracks)
        old_count = len(self._rows)
        new_count = len(new_rows)
        common    = min(old_count, new_count)
        if new_count > old_count:
            self.beginInsertRows(QModelIndex(), old_count, new_count - 1)
            self._rows = new_rows
            self.endInsertRows()
        elif new_count < old_count:
            self.beginRemoveRows(QModelIndex(), new_count, old_count - 1)
            self._rows = new_rows
            self.endRemoveRows()
        else:
            self._rows = new_rows
        if common:
            self.dataChanged.emit(self.index(0, 0), self.index(common - 1, 0))

    def update_favorite(self, track_idx: int, is_fav: bool):
        for i, r in enumerate(self._rows):
            if r.get('_idx') == track_idx:
                r['_fav'] = is_fav
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.IS_FAVORITE])
                break

    def update_bpm(self, track_id: str, bpm_str: str):
        for i, r in enumerate(self._rows):
            if r.get('_id') == track_id:
                r['_bpm'] = bpm_str
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.BPM_STR])
                break

    def reorder_tracks(self, tracks: list):
        """Re-sort in-place via dataChanged — does not reset scroll position
        (unlike set_tracks' beginResetModel, which collapses the view to zero
        rows and clamps contentY back near the top)."""
        self._rows = self._build_rows(tracks)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0))

    def remove_track(self, track_idx: int):
        """Remove exactly one row (e.g. un-favoriting) without resetting the
        whole model, so scroll position is preserved."""
        for i, r in enumerate(self._rows):
            if r.get('_idx') == track_idx:
                self.beginRemoveRows(QModelIndex(), i, i)
                self._rows.pop(i)
                self.endRemoveRows()
                for j in range(i, len(self._rows)):
                    self._rows[j]['_idx'] = j
                    self._rows[j]['_num'] = str(j + 1)
                if i < len(self._rows):
                    self.dataChanged.emit(self.index(i, 0), self.index(len(self._rows) - 1, 0),
                                          [self.TRACK_IDX, self.TRACK_NUMBER])
                break


class _FavoritesKeyFilter(SearchKeyFilter):
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
                b.playAllClicked(); return True
            if b._selected_trkidx >= 0:
                b.playSelected(); return True
        if key == Qt.Key.Key_Escape and b._selected_trkidx >= 0:
            b._selected_trkidx = -1
            b.selectedTrackChanged.emit(-1)
            return True
        return False


class FavoritesBridge(QObject):
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
    showAlbumChanged          = pyqtSignal(bool)
    showTrackNoChanged        = pyqtSignal(bool)
    showYearChanged           = pyqtSignal(bool)
    showDateChanged           = pyqtSignal(bool)
    showBpmChanged            = pyqtSignal(bool)
    # → QML sort
    sortStateChanged          = pyqtSignal(str, str)   # col, 'asc'|'desc'|''
    # → QML favorites-page state
    tracksLoadingChanged       = pyqtSignal(bool)
    songsStatusChanged         = pyqtSignal(str)
    genreFilterActiveChanged   = pyqtSignal(bool)
    clearFiltersVisibleChanged = pyqtSignal(bool)

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._sort_col = ''
        self._sort_dir = ''
        self._selected_trkidx = -1
        self.search = SearchController(
            on_active_changed=lambda active: set_window_shortcuts_enabled(
                view, view._qml, not active))

    @pyqtProperty(QObject, constant=True)
    def searchCtl(self):
        return self.search

    # ── Carousel slots ──────────────────────────────────────────────────────

    @pyqtSlot(int)
    def artistCardClicked(self, idx):
        self._view._on_artist_card_clicked(idx)

    @pyqtSlot(int)
    def albumCardClicked(self, idx):
        self._view._on_album_card_clicked(idx)

    @pyqtSlot(int)
    def albumPlayClicked(self, idx):
        self._view._on_album_play_clicked(idx)

    @pyqtSlot(int)
    def topArtistCardClicked(self, idx):
        self._view._on_top_artist_card_clicked(idx)

    @pyqtSlot(str, str)
    def artistNameClicked(self, name, artist_id):
        self._view.artist_clicked.emit(name)

    # ── Favorite-songs header controls ─────────────────────────────────────

    @pyqtSlot()
    def playAllClicked(self):
        self._view._on_play_all()

    @pyqtSlot()
    def shuffleClicked(self):
        self._view._on_shuffle_all()

    @pyqtSlot(float, float)
    def genreFilterClicked(self, gx, gy):
        self._view._toggle_genre_popup(int(gx), int(gy))

    @pyqtSlot()
    def clearFiltersClicked(self):
        self._view._clear_all_filters()

    # ── Track list slots (TrackListView.qml contract) ──────────────────────

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
        songs = self._view._songs
        if 0 <= self._selected_trkidx < len(songs):
            self._view.play_track.emit(songs[self._selected_trkidx])

    @pyqtSlot(int)
    def trackPlayClicked(self, track_idx: int):
        songs = self._view._songs
        if 0 <= track_idx < len(songs):
            self._selected_trkidx = track_idx
            self.selectedTrackChanged.emit(track_idx)
            self._view.play_track.emit(songs[track_idx])

    @pyqtSlot(str)
    def trackArtistClicked(self, name: str):
        view = self._view
        QTimer.singleShot(0, lambda: view.artist_clicked.emit(name))

    @pyqtSlot(str)
    def trackGenreClicked(self, genre: str):
        view = self._view
        QTimer.singleShot(0, lambda: view.genre_clicked.emit(genre))

    @pyqtSlot(int)
    def trackFavoriteClicked(self, track_idx: int):
        self._view._toggle_track_favorite(track_idx)

    @pyqtSlot(int, float, float)
    def trackContextMenuRequested(self, track_idx: int, global_x: float, global_y: float):
        self._view._show_track_context_menu_at(track_idx, int(global_x), int(global_y))

    @pyqtSlot(str, str)
    def trackAlbumClicked(self, album_id: str, album_name: str):
        if not album_id:
            return
        album_data = {'id': album_id, 'title': album_name, 'coverArt': album_id, 'cover_id': album_id}
        main = self._view.window()
        if hasattr(main, 'navigate_to_album'):
            QTimer.singleShot(0, lambda: main.navigate_to_album(album_data))

    @pyqtSlot()
    def reorderTrack(self, *_):
        pass  # row-reorder disabled (enableRowReorder: false)

    @pyqtSlot()
    def albumHeaderClicked(self):
        pass

    @pyqtSlot()
    def favHeaderClicked(self):
        pass

    @pyqtSlot(str)
    def colHeaderClicked(self, col: str):
        if self._sort_col == col:
            next_dir = {'': 'desc', 'desc': 'asc', 'asc': ''}[self._sort_dir]
            self._sort_dir = next_dir
            if not next_dir:
                self._sort_col = ''
        else:
            self._sort_col = col
            self._sort_dir = 'desc'
        QSettings().setValue('favorites/sort_col', self._sort_col)
        QSettings().setValue('favorites/sort_dir', self._sort_dir)
        self.sortStateChanged.emit(self._sort_col, self._sort_dir)
        self._view._apply_sort(self._sort_col, self._sort_dir)

    @pyqtSlot(result='QVariantList')
    def getSortState(self):
        col  = QSettings().value('favorites/sort_col', '') or ''
        dir_ = QSettings().value('favorites/sort_dir', '') or ''
        if dir_ not in ('asc', 'desc'):
            dir_ = ''
        self._sort_col = col if dir_ else ''
        self._sort_dir = dir_
        return [self._sort_col, self._sort_dir]

    @pyqtSlot(result='QVariantList')
    def getColWidths(self):
        saved = QSettings().value('favorites/track_col_widths')
        if isinstance(saved, dict):
            return [int(saved.get('track', 450)), int(saved.get('title', 200)), int(saved.get('artist', 99)),
                    int(saved.get('fav', 81)), int(saved.get('dur', 86)), int(saved.get('plays', 56)),
                    int(saved.get('genre', 182)), int(saved.get('album', 265)),
                    int(saved.get('trackno', 50)), int(saved.get('year', 56)),
                    int(saved.get('date', 110)), int(saved.get('bpm', 56))]
        return [450, 200, 99, 81, 86, 56, 182, 265, 50, 56, 110, 56]

    @pyqtSlot(int, int, int, int, int, int, int, int, int, int, int, int)
    def saveColWidths(self, track: int, title: int, artist: int, fav: int, dur: int, plays: int, genre: int, album: int,
                      trackno: int, year: int, date: int, bpm: int):
        QSettings().setValue('favorites/track_col_widths',
                             {'track': track, 'title': title, 'artist': artist, 'fav': fav,
                              'dur': dur, 'plays': plays, 'genre': genre, 'album': album,
                              'trackno': trackno, 'year': year, 'date': date, 'bpm': bpm})

    @pyqtSlot(result='QVariantList')
    def getColVisibility(self):
        saved = QSettings().value('favorites/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        if not saved:
            return [True, False, False, False, True, True, True, True, False, False, False, False]
        return [bool(saved.get('track',  True)), bool(saved.get('title',  True)),
                bool(saved.get('artist', True)),  bool(saved.get('fav',    True)),
                bool(saved.get('genre',  True)),  bool(saved.get('dur',    True)),
                bool(saved.get('plays',  True)),  bool(saved.get('album',  False)),
                bool(saved.get('trackno', False)), bool(saved.get('year',  False)),
                bool(saved.get('date',    False)), bool(saved.get('bpm',   False))]

    @pyqtSlot(float, float)
    def burgerClicked(self, gx: float, gy: float):
        saved = QSettings().value('favorites/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        cols = [
            ('track',   'Track',      self.showTrackChanged,   True),
            ('title',   'Title',      self.showTitleChanged,   True),
            ('artist',  'Artist',     self.showArtistChanged,  True),
            ('fav',     'Favorite',   self.showFavChanged,     True),
            ('genre',   'Genre',      self.showGenreChanged,   True),
            ('dur',     'Duration',   self.showDurChanged,     True),
            ('plays',   'Plays',      self.showPlaysChanged,   True),
            ('album',   'Album',      self.showAlbumChanged,   False),
            ('trackno', 'No.',        self.showTrackNoChanged, False),
            ('year',    'Year',       self.showYearChanged,    False),
            ('date',    'Date Added', self.showDateChanged,    False),
            ('bpm',     'BPM',        self.showBpmChanged,     False),
        ]
        menu = themed_shadow_menu(self._view, bg=getattr(self._view, '_bg_color', None))
        for key, label, sig, default_vis in cols:
            vis = bool(saved.get(key, default_vis))
            menu.add_action(label, lambda k=key, v=vis, s=sig: self._set_col_vis(k, not v, s),
                            icon_path='img/yes.png' if vis else '')
        popup_menu_at_global(menu, gx, gy, window=self._view.window())

    def _set_col_vis(self, key: str, visible: bool, signal):
        saved = QSettings().value('favorites/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        saved[key] = visible
        QSettings().setValue('favorites/col_visibility', saved)
        signal.emit(visible)

    @pyqtSlot(result='QVariantList')
    def getColOrder(self):
        default = ["track", "title", "artist", "album", "fav", "genre", "dur", "plays", "trackno", "year", "date", "bpm"]
        known = set(default)
        saved = QSettings().value('favorites/col_order')
        if isinstance(saved, list) and set(saved) <= known and len(saved) > 0:
            result = [c for c in saved if c in known]
            for c in default:
                if c not in result:
                    result.append(c)
            return result
        return default

    @pyqtSlot('QVariantList')
    def saveColOrder(self, order):
        QSettings().setValue('favorites/col_order', list(order))


class FavoritesView(QWidget):
    album_clicked  = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)
    genre_clicked  = pyqtSignal(str)
    play_album     = pyqtSignal(dict)
    play_track     = pyqtSignal(dict)
    play_all       = pyqtSignal(object)   # emits list[dict] → play_whole_album
    shuffle_all    = pyqtSignal(object)   # emits list[dict] → play_whole_album shuffled

    # Skip re-fetching everything from the server on every tab visit —
    # only refresh if data is missing or older than this many seconds.
    _REFRESH_STALE_SECS = 30

    def __init__(self, client=None, parent=None):
        super().__init__(parent)
        self._client       = client
        self._accent       = '#888888'
        self._bg_color     = '14,14,14'
        self._worker       = None
        self._cover_worker = None
        self._last_refresh_at = 0.0
        self._songs          = []
        self._songs_original = []
        self._selected_genres: set = set()
        self._selected_artist: str = ''
        self._genre_popup = None

        s = QSettings()
        self._sort_col = s.value('favorites/sort_col', '') or ''
        self._sort_dir = s.value('favorites/sort_dir', '') or ''
        if self._sort_dir not in ('asc', 'desc'):
            self._sort_dir = ''
        if not self._sort_dir:
            self._sort_col = ''

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('FavoritesPanel')

        self._artists_model     = AlbumModel()
        self._albums_model      = AlbumModel()
        self._top_artists_model = AlbumModel()
        self._track_model       = FavoritesTrackModel()

        self._cover_provider    = CoverImageProvider()
        self._icon_provider     = AlbumIconProvider()
        self._playbtn_provider  = AlbumDetailCoverProvider()
        self._track_thumb_prov  = TrackThumbProvider()

        self._bridge = FavoritesBridge(self)

        self._qml_view = QQuickView()
        self._qml_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._qml = QWidget.createWindowContainer(self._qml_view, self)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._key_filter = _FavoritesKeyFilter(self._bridge, self._qml)
        self._qml.installEventFilter(self._key_filter)
        self._qml_view.installEventFilter(self._key_filter)

        engine = self._qml_view.engine()
        engine.addImageProvider("homecovers",          self._cover_provider)
        engine.addImageProvider("favoritesicons",       self._icon_provider)
        engine.addImageProvider("albumicons",           self._icon_provider)  # SearchBar.qml hardcodes albumicons
        engine.addImageProvider("homeicons",            self._icon_provider)  # Carousel.qml hardcodes homeicons (arrows/refresh)
        engine.addImageProvider("favoritesplaybtn",     self._playbtn_provider)
        engine.addImageProvider("favoritestrackcovers", self._track_thumb_prov)

        ctx = self._qml_view.rootContext()
        ctx.setContextProperty("favoritesArtistsModel",    self._artists_model)
        ctx.setContextProperty("favoritesAlbumsModel",      self._albums_model)
        ctx.setContextProperty("favoritesTopArtistsModel",  self._top_artists_model)
        ctx.setContextProperty("favoritesTrackModel",       self._track_model)
        ctx.setContextProperty("favoritesBridge",           self._bridge)
        ctx.setContextProperty("scrollTuning",              scroll_tuning)

        self._qml_view.setSource(QUrl.fromLocalFile(
            resource_path("player/tabs/favorites/favorites.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

        self.set_accent_color('#888888')

        if self._client:
            QTimer.singleShot(0, self.refresh)

    # ── Public API ────────────────────────────────────────────────────────

    def set_client(self, client):
        self._client = client
        if client:
            self.refresh()

    def set_accent_color(self, color: str):
        self._accent = color
        b = self._bridge
        theme = getattr(self.window(), 'theme', None)
        b.accentColorChanged.emit(color)
        b.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')
        if theme:
            b.fontSizePrimaryChanged.emit(theme.font_size_primary)
            b.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            b.fontColorPrimaryChanged.emit(theme.font_color_primary)
            b.fontColorSecondaryChanged.emit(theme.font_color_secondary)
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
        if self._genre_popup:
            hov = resolve_menu_hover(theme)
            self._genre_popup.apply_theme(theme, color, hov)

    def set_bg_color(self, c: str):
        self._bg_color = c
        try:
            r, g, b = (int(x) for x in c.split(','))
            self._qml_view.setColor(QColor(r, g, b))
        except Exception:
            pass

    def refresh(self):
        if not self._client:
            return
        if self._worker and self._worker.isRunning():
            return
        self._last_refresh_at = time.monotonic()
        self._selected_artist = ''
        self._selected_genres = set()
        self._bridge.genreFilterActiveChanged.emit(False)
        self._bridge.clearFiltersVisibleChanged.emit(False)
        if self._genre_popup and self._genre_popup._genres:
            self._genre_popup.set_genres(self._genre_popup._genres, set())
        self._bridge.tracksLoadingChanged.emit(True)
        self._track_thumb_prov.set_client(self._client)
        self._worker = _StarredWorker(self._client)
        self._worker.done.connect(self._on_data)
        self._worker.start()

    # ── Internal — data load ────────────────────────────────────────────────

    def refresh_track_bpm(self, track_id: str, bpm: float):
        """Live-update a single row's BPM as soon as tempo analysis finishes
        — no need to leave and re-open Favorites for it to show up."""
        bpm_str = f"{bpm:.1f}" if bpm > 0 else ''
        for s in self._songs_original:
            if str(s.get('id', '')) == track_id:
                s['bpm'] = bpm
                break
        self._track_model.update_bpm(track_id, bpm_str)

    def _on_data(self, data: dict):
        self._bridge.tracksLoadingChanged.emit(False)
        songs   = data.get('songs',   [])
        albums  = data.get('albums',  [])
        artists = data.get('artists', [])

        song_cover_lookup: dict = {}
        for s in songs:
            name = s.get('artist', '')
            aid  = s.get('artist_id') or s.get('artistId', '')
            if name and name not in song_cover_lookup and aid:
                song_cover_lookup[name] = aid if aid.startswith('ar-') else f'ar-{aid}'

        artist_items = []
        for a in artists:
            card = self._artist_to_card(a)
            name = a.get('name', '')
            if name in song_cover_lookup:
                card['coverArt'] = song_cover_lookup[name]
            artist_items.append(card)
        self._artists_model.set_albums(artist_items)

        album_items = [self._album_to_card(a) for a in albums]
        self._albums_model.set_albums(album_items)

        counts = Counter(s.get('artist', '') for s in songs if s.get('artist'))
        artist_lookup = {a.get('name', ''): a for a in artists}
        top = sorted(counts.items(), key=lambda x: -x[1])[:16]
        top_items = []
        for name, count in top:
            a = artist_lookup.get(name, {})
            cid = song_cover_lookup.get(name) or a.get('id', '')
            top_items.append({
                'id':           a.get('id', ''),
                'title':        name,
                'artist':       f'{count} song{"s" if count != 1 else ""}',
                'coverArt':     cid,
                '_is_artist':   True,
                '_artist_name': name,
            })
        self._top_artists_model.set_albums(top_items)

        # Detected BPM (player.bpm_cache, from live tempo analysis) always
        # beats the ID3 tag value — same merge tracks_browser.py does.
        win = self.window()
        bpm_cache = getattr(win, 'bpm_cache', {}) if win else {}
        if bpm_cache:
            for s in songs:
                sid = str(s.get('id', ''))
                if sid in bpm_cache:
                    s['bpm'] = bpm_cache[sid]

        self._songs_original = list(songs)
        self._apply_filters()

        all_genres: set = set()
        for s in songs:
            for g in (s.get('genre', '') or '').split('•'):
                g = g.strip()
                if g:
                    all_genres.add(g)
        if self._genre_popup is None:
            self._ensure_genre_popup()
        self._genre_popup.set_genres(sorted(all_genres), self._selected_genres)

        if self._client:
            self._ensure_cover_worker()
            all_cover_ids = set()
            for c in artist_items + album_items + top_items:
                cid = c.get('coverArt', '')
                if cid:
                    all_cover_ids.add(cid)
            for cid in all_cover_ids:
                self._cover_worker.queue_cover(cid)

    def _ensure_cover_worker(self):
        if self._cover_worker and self._cover_worker.isRunning():
            return
        self._cover_worker = GridCoverWorker(self._client)
        self._cover_worker.cover_ready.connect(self._on_cover)
        self._cover_worker.start()

    def _on_cover(self, cover_id: str, image_data: bytes):
        self._cover_provider.image_cache[str(cover_id)] = image_data
        for m in (self._artists_model, self._albums_model, self._top_artists_model):
            m.update_cover(str(cover_id))

    # ── Genre filter ──────────────────────────────────────────────────────

    def _ensure_genre_popup(self):
        if self._genre_popup is None:
            self._genre_popup = _GenrePopup(self)
            self._genre_popup.hide()
            self._genre_popup.selection_changed.connect(self._on_genres_changed)
            orig_hide = self._genre_popup.hideEvent
            def _on_popup_hide(e):
                orig_hide(e)
                self._bridge.genreFilterActiveChanged.emit(bool(self._selected_genres))
            self._genre_popup.hideEvent = _on_popup_hide
            theme = getattr(self.window(), 'theme', None)
            self._genre_popup.apply_theme(theme, self._accent, resolve_menu_hover(theme))

    def _toggle_genre_popup(self, gx: int, gy: int):
        self._ensure_genre_popup()
        if self._genre_popup.isVisible():
            self._genre_popup.hide()
            self._bridge.genreFilterActiveChanged.emit(bool(self._selected_genres))
            return
        pad = self._genre_popup._PAD
        self._genre_popup.move(QPoint(gx - pad, gy - pad))
        self._genre_popup.show()
        self._genre_popup.raise_()
        self._bridge.genreFilterActiveChanged.emit(True)

    def _on_genres_changed(self, genres: set):
        self._selected_genres = genres
        self._bridge.genreFilterActiveChanged.emit(True)
        self._apply_filters()

    # ── Artist filter (Top Artists carousel) ──────────────────────────────

    def _on_top_artist_card_clicked(self, idx: int):
        albums = self._top_artists_model.albums
        if not (0 <= idx < len(albums)):
            return
        card = albums[idx]
        name = card.get('_artist_name', card.get('title', ''))
        self._selected_artist = '' if self._selected_artist == name else name
        if self._selected_artist and self._selected_genres:
            self._selected_genres = set()
            self._bridge.genreFilterActiveChanged.emit(False)
            if self._genre_popup:
                self._genre_popup.set_genres(self._genre_popup._genres, set())
        self._apply_filters()

    def _clear_all_filters(self):
        self._selected_artist = ''
        self._selected_genres = set()
        self._bridge.genreFilterActiveChanged.emit(False)
        if self._genre_popup:
            self._genre_popup.set_genres(self._genre_popup._genres, set())
        self._apply_filters()

    def _on_play_all(self):
        if self._songs:
            self.play_all.emit(self._songs)

    def _on_shuffle_all(self):
        import random
        songs = list(self._songs)
        if songs:
            random.shuffle(songs)
            self.shuffle_all.emit(songs)

    # ── Combined filter + sort application ─────────────────────────────────

    @staticmethod
    def _sort_songs(songs: list, col: str, dir_: str) -> list:
        if not (dir_ and col):
            return songs
        def sort_key(pair):
            _, t = pair
            if col == 'title':   return t.get('title',  '').lower()
            if col == 'artist':  return t.get('artist', '').lower()
            if col == 'album':   return t.get('album',  '').lower()
            if col == 'dur':     return t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
            if col == 'plays':   return int(t.get('play_count') or 0)
            if col == 'fav':     return int(bool(t.get('starred', False)))
            if col == 'trackno':
                try:    return int(t.get('trackNumber') or t.get('track') or 0)
                except (TypeError, ValueError): return 0
            if col == 'year':
                try:    return int(t.get('year') or 0)
                except (TypeError, ValueError): return 0
            if col == 'date':    return t.get('created') or ''
            if col == 'bpm':
                try:    return float(t.get('bpm') or 0)
                except (TypeError, ValueError): return 0
            return 0
        paired = sorted(enumerate(songs), key=sort_key, reverse=(dir_ == 'desc'))
        return [t for _, t in paired]

    def _apply_filters(self):
        """Artist/genre filter changed — row set/count changes, but
        update_tracks avoids a full reset so scroll position is preserved."""
        all_songs = list(self._songs_original)
        songs = all_songs
        if self._selected_artist:
            songs = [s for s in songs if s.get('artist', '') == self._selected_artist]
        if self._selected_genres:
            def _genre_match(s):
                g = s.get('genre', '') or ''
                return any(sel in g for sel in self._selected_genres)
            songs = [s for s in songs if _genre_match(s)]
        self._songs = self._sort_songs(songs, self._sort_col, self._sort_dir)
        self._track_model.update_tracks(self._songs)
        self._update_status_label(len(all_songs), len(songs))

    def _apply_sort(self, col: str, dir_: str):
        """Re-sort the currently filtered rows in place — same row count, so
        use reorder_tracks (no model reset) to preserve scroll position."""
        self._songs = self._sort_songs(self._songs, col, dir_)
        self._track_model.reorder_tracks(self._songs)

    def _update_status_label(self, total: int, shown: int):
        parts = []
        if self._selected_artist:
            parts.append(self._selected_artist)
        if self._selected_genres:
            parts.append(', '.join(sorted(self._selected_genres)))
        if parts or shown != total:
            self._bridge.songsStatusChanged.emit(
                f'Showing {shown} of {total}' + (f'  ({" · ".join(parts)})' if parts else ''))
        else:
            self._bridge.songsStatusChanged.emit('')
        self._bridge.clearFiltersVisibleChanged.emit(bool(self._selected_artist or self._selected_genres))

    # ── Track interactions ──────────────────────────────────────────────────

    def _toggle_track_favorite(self, track_idx: int):
        if not (0 <= track_idx < len(self._songs)):
            return
        track   = self._songs[track_idx]
        raw     = track.get('starred', False)
        cur_fav = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new_fav = not cur_fav
        track_id = str(track.get('id', ''))
        client = getattr(self, '_client', None)
        if client:
            threading.Thread(
                target=lambda: client.set_favorite(track_id, new_fav), daemon=True).start()
        if not new_fav:
            # Un-favoriting removes just this row — this view shows only
            # favorites — without resetting/scrolling the rest of the list.
            self._songs_original = [s for s in self._songs_original
                                    if str(s.get('id', '')) != track_id]
            self._songs.pop(track_idx)
            self._track_model.remove_track(track_idx)
            self._update_status_label(len(self._songs_original), len(self._songs))
        else:
            track['starred'] = True
            for s in self._songs_original:
                if str(s.get('id', '')) == track_id:
                    s['starred'] = True
            self._track_model.update_favorite(track_idx, True)

    def _show_track_context_menu_at(self, track_idx: int, gx: int, gy: int):
        if not (0 <= track_idx < len(self._songs)):
            return
        track = self._songs[track_idx]
        main  = self.window()

        menu = themed_shadow_menu(self, bg=self._bg_color)
        menu.add_action('Play Now',     lambda: self.play_track.emit(track),     icon_path='img/sub_play.png')
        menu.add_action('Play Next',    lambda: main.play_track_next(track) if hasattr(main, 'play_track_next') else None,    icon_path='img/sub_next.png')
        menu.add_action('Add to Queue', lambda: main.add_track_to_queue(track) if hasattr(main, 'add_track_to_queue') else None, icon_path='img/queue.png')
        _artist = track.get('artist', '')
        menu.add_action('Go to Artist', lambda: self.artist_clicked.emit(_artist) if _artist else None,
                        enabled=bool(_artist), icon_path='img/sub_artist.png')
        _album_data = {'id': track.get('albumId', ''), 'title': track.get('album', ''),
                       'artist': track.get('artist', ''), 'coverArt': track.get('cover_id', '')}
        menu.add_action('Open Album', lambda: self.album_clicked.emit(_album_data) if _album_data.get('id') else None,
                        enabled=bool(track.get('albumId')), icon_path='img/album.png')
        menu.add_action('Start Radio',   lambda: main.start_radio(track) if hasattr(main, 'start_radio') else None, icon_path='img/radio.png')

        is_fav   = bool(track.get('starred', True))
        track_id = str(track.get('id', ''))

        playlists = getattr(getattr(main, 'playlists_browser', None), 'all_playlists', None) or []
        if track_id:
            pl_items = [('New Playlist…',
                         lambda: threading.Thread(target=lambda: None, daemon=True).start(),
                         'img/add.png')]
            for pl in playlists:
                pid = pl.get('id')
                if not pid: continue
                cnt = pl.get('songCount', '')
                lbl = f"{pl.get('name','Unnamed')}  ({cnt})" if cnt != '' else pl.get('name','Unnamed')
                def _add(_, _pid=pid):
                    c = getattr(main, 'navidrome_client', None)
                    if c: threading.Thread(
                        target=lambda: c.add_tracks_to_playlist(_pid, [track_id]), daemon=True).start()
                pl_items.append((lbl, _add, 'img/playlist.png'))
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')

        tb = getattr(main, 'tracks_browser', None)
        menu.add_action('Get Info',
                        callback=(lambda: tb._show_track_info(track)) if tb else None,
                        enabled=bool(tb), icon_path='img/info.png')

        _HEART_COLOR = '#E91E63'
        fav_label = 'Remove from Favorites' if is_fav else 'Add to Favorites'
        fav_icon  = 'img/heart_filled.png'  if is_fav else 'img/heart.png'
        menu.add_action(fav_label, lambda: self._toggle_track_favorite(track_idx),
                        color=_HEART_COLOR, icon_path=fav_icon)

        popup_menu_at_global(menu, gx, gy, window=self.window())

    # ── Carousel click routing ──────────────────────────────────────────────

    def _on_artist_card_clicked(self, idx: int):
        albums = self._artists_model.albums
        if not (0 <= idx < len(albums)):
            return
        card = albums[idx]
        if card.get('_is_artist') and card.get('_artist_name'):
            self.artist_clicked.emit(card['_artist_name'])
        else:
            self.album_clicked.emit(card)

    def _on_album_card_clicked(self, idx: int):
        albums = self._albums_model.albums
        if 0 <= idx < len(albums):
            self.album_clicked.emit(albums[idx])

    def _on_album_play_clicked(self, idx: int):
        albums = self._albums_model.albums
        if 0 <= idx < len(albums):
            self.play_album.emit(albums[idx])

    # ── Card data builders ──────────────────────────────────────────────────

    @staticmethod
    def _artist_to_card(a: dict) -> dict:
        n = a.get('albumCount', a.get('album_count', ''))
        subtitle = f"{n} Album{'s' if n != 1 else ''}" if n else ''
        artist_id = a.get('id', '')
        ar_id = artist_id if artist_id.startswith('ar-') else f'ar-{artist_id}'
        cid = a.get('artistImageUrl') or a.get('coverArt') or ar_id
        return {
            'id':           artist_id,
            'title':        a.get('name', ''),
            'artist':       subtitle,
            'coverArt':     cid,
            '_is_artist':   True,
            '_artist_name': a.get('name', ''),
        }

    @staticmethod
    def _album_to_card(a: dict) -> dict:
        return {
            'id':       a.get('id', ''),
            'title':    a.get('name', a.get('title', '')),
            'artist':   a.get('artist', a.get('artistName', '')),
            'year':     a.get('year', ''),
            'coverArt': a.get('coverArt', ''),
            'starred':  a.get('starred', True),
        }

    def showEvent(self, event):
        super().showEvent(event)
        # Refresh so new favorites appear, but skip it if we just refreshed
        # (e.g. fast tab-shuffling) to avoid piling up redundant network
        # calls and worker threads on top of other tabs' own background work.
        if time.monotonic() - self._last_refresh_at >= self._REFRESH_STALE_SECS:
            self.refresh()
