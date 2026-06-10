import time
import random
import json


from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider
from PyQt6.QtWidgets import (QWidget, QVBoxLayout,
                             QStackedWidget)

from PyQt6.QtCore import (Qt, pyqtSignal, QThread, QRect, QTimer, QRectF,
                          QAbstractListModel, QModelIndex, pyqtSlot, pyqtProperty, QObject, QUrl, Qt)
from player.qml_search import SearchController, SearchKeyFilter, set_window_shortcuts_enabled
from PyQt6.QtGui import (QPainter, QColor, QPainterPath, QImage)

from player import resource_path
from player.workers import GridCoverWorker
from player.widgets import CoverImageProvider, AlbumModel, QMLGridWrapper, QMLMiddleClickScroller


class GridBridge(QObject):
    itemClicked = pyqtSignal(dict)
    playClicked = pyqtSignal(dict)
    artistNameClicked = pyqtSignal(str, str)  # name, artist_id
    visibleRangeChanged = pyqtSignal(int, int)
    accentColorChanged = pyqtSignal(str)
    bgAlphaChanged = pyqtSignal(float)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    skeletonBaseColorChanged  = pyqtSignal(str)
    infoLineCountChanged      = pyqtSignal(int)
    hoverColorChanged         = pyqtSignal(str)
    panelBgChanged            = pyqtSignal(str)
    cardBorderChanged         = pyqtSignal(str)
    fontFamilyChanged         = pyqtSignal(str)
    statusTextChanged         = pyqtSignal(str)
    burgerIconChanged         = pyqtSignal(str)
    cancelScroll = pyqtSignal()
    scrollBy = pyqtSignal(float)
    takeFocus = pyqtSignal()

    def __init__(self, album_model, view):
        super().__init__()
        self.album_model = album_model
        self._view = view
        self.search = SearchController(
            on_active_changed=lambda active: view._set_window_shortcuts_enabled(not active),
            on_text_changed=view._on_grid_search_text_changed)

    @pyqtProperty(QObject, constant=True)
    def searchCtl(self):
        return self.search

    @pyqtSlot(float, float)
    def showSortMenu(self, gx, gy):
        self._view.show_sort_menu_at(gx, gy)

    @pyqtSlot(int, int)
    def reportVisibleRange(self, start, end):
        self.last_start, self.last_end = start, end
        if not hasattr(self, 'scroll_timer'):
            from PyQt6.QtCore import QTimer
            self._scroll_prev = (-1, -1)
            self.scroll_timer = QTimer()
            self.scroll_timer.setInterval(80)
            self.scroll_timer.timeout.connect(self._on_scroll_tick)
        if not self.scroll_timer.isActive():
            self._scroll_prev = (-1, -1)
            self.scroll_timer.start()

    def _on_scroll_tick(self):
        current = (self.last_start, self.last_end)
        if current == self._scroll_prev:
            self.scroll_timer.stop()
            self.visibleRangeChanged.emit(self.last_start, self.last_end)
        else:
            self._scroll_prev = current
        
    @pyqtSlot(int)
    def emitItemClicked(self, idx): 
        if 0 <= idx < len(self.album_model.albums):
            self.itemClicked.emit(self.album_model.albums[idx])
            
    @pyqtSlot(int)
    def emitPlayClicked(self, idx): 
        if 0 <= idx < len(self.album_model.albums):
            self.playClicked.emit(self.album_model.albums[idx])

    @pyqtSlot(str, str)
    def emitArtistNameClicked(self, name, artist_id=""):
        self.artistNameClicked.emit(name, artist_id)

class LivePageWorker(QThread):

    page_ready = pyqtSignal(list, object)

    def __init__(self, client, sort_type, size, offset, query="", reverse_list=False):
        super().__init__()
        self.client = client
        self.sort_type = sort_type
        self.size = size
        self.offset = offset
        self.query = query
        self.reverse_list = reverse_list
        self.is_cancelled = False

    def run(self):
        try:
            if not self.client: return

            # NEW: Check for native album sort by song count
            if not self.query and self.sort_type == 'song_count' and hasattr(self.client, 'get_albums_native_page'):
                is_ascending = not self.reverse_list
                albums, total_count = self.client.get_albums_native_page(
                    sort_by='songCount',
                    order='ASC' if is_ascending else 'DESC',
                    start=self.offset,
                    end=self.offset + self.size
                )
                # Native API handles sorting, so no client-side reversal needed.
                if not self.is_cancelled:
                    # The native API returns items that are mostly compatible.
                    # We just need to ensure the keys match what the UI expects.
                    self.page_ready.emit(albums, total_count)
                return

            if self.query:
                # Search mode: use the dedicated album search endpoint
                albums, total_count = self.client.search_albums(
                    query=self.query,
                    count=self.size,
                    offset=self.offset
                )
            else:
                # Browse mode: use the fast cached sorted list
                albums, total_count = self.client.get_albums_live(
                    sort_type=self.sort_type,
                    size=self.size,
                    offset=self.offset
                )
                
            
            if self.reverse_list and albums:
                albums.reverse()
                
            if self.is_cancelled: return
                
            self.page_ready.emit(albums, total_count)
        except Exception as e:
            print(f"[LivePageWorker] Error loading page: {e}")

class CompilationsWorker(QThread):
    """Fetches compilation albums using Navidrome's native /api/album?compilation=true filter."""
    results_ready = pyqtSignal(list)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.is_cancelled = False

    def run(self):
        try:
            import requests
            if not hasattr(self.client, 'native_jwt') or not self.client.native_jwt:
                if not self.client.authenticate_native():
                    self.results_ready.emit([])
                    return
            headers = {"x-nd-authorization": f"Bearer {self.client.native_jwt}"}
            params = {
                "_start": 0,
                "_end": 100000,
                "_sort": "name",
                "_order": "ASC",
                "compilation": "true",
            }
            r = requests.get(f"{self.client.base_url}/api/album", params=params, headers=headers, timeout=15)
            if r.status_code == 401 and self.client.authenticate_native():
                headers["x-nd-authorization"] = f"Bearer {self.client.native_jwt}"
                r = requests.get(f"{self.client.base_url}/api/album", params=params, headers=headers, timeout=15)
            if self.is_cancelled:
                return
            data = r.json()
            if not isinstance(data, list):
                self.results_ready.emit([])
                return
            # Normalise keys to match what the grid expects
            albums = []
            for a in data:
                a.setdefault('cover_id', a.get('coverArt') or a.get('id'))
                albums.append(a)
            self.results_ready.emit(albums)
        except Exception as e:
            print(f"[CompilationsWorker] Error: {e}")
            self.results_ready.emit([])


class ServerCountWorker(QThread):
    count_ready = pyqtSignal(int)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        
        try:
            if not self.client: return
            
            
            count = self.client.get_fast_album_count() 
            
            if count is not None:
                self.count_ready.emit(count)
        except Exception as e:
            print(f"[ServerCountWorker] Safely caught error: {e}")


# ── AlbumDetail QML support classes ─────────────────────────────────────────

class AlbumDetailTrackModel(QAbstractListModel):
    IS_DISC_HEADER = Qt.ItemDataRole.UserRole + 1
    DISC_LABEL     = Qt.ItemDataRole.UserRole + 2
    TRACK_IDX      = Qt.ItemDataRole.UserRole + 3
    TRACK_ID       = Qt.ItemDataRole.UserRole + 4
    TRACK_NUMBER   = Qt.ItemDataRole.UserRole + 5
    TRACK_TITLE    = Qt.ItemDataRole.UserRole + 6
    ARTIST_NAME    = Qt.ItemDataRole.UserRole + 7
    IS_FAVORITE    = Qt.ItemDataRole.UserRole + 8
    DURATION_STR   = Qt.ItemDataRole.UserRole + 9
    PLAY_COUNT_STR = Qt.ItemDataRole.UserRole + 10
    TRACK_GENRE    = Qt.ItemDataRole.UserRole + 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        if role == self.IS_DISC_HEADER: return r.get('_disc', False)
        if role == self.DISC_LABEL:     return r.get('_disc_label', '')
        if role == self.TRACK_IDX:      return r.get('_idx', 0)
        if role == self.TRACK_ID:       return r.get('_id', '')
        if role == self.TRACK_NUMBER:   return r.get('_num', '')
        if role == self.TRACK_TITLE:    return r.get('_title', '')
        if role == self.ARTIST_NAME:    return r.get('_artist', '')
        if role == self.IS_FAVORITE:    return r.get('_fav', False)
        if role == self.DURATION_STR:   return r.get('_dur', '')
        if role == self.PLAY_COUNT_STR: return r.get('_plays', '-')
        if role == self.TRACK_GENRE:    return r.get('_genre', '')
        return None

    def roleNames(self):
        return {
            self.IS_DISC_HEADER: b"isDiscHeader",
            self.DISC_LABEL:     b"discLabel",
            self.TRACK_IDX:      b"trackIdx",
            self.TRACK_ID:       b"trackId",
            self.TRACK_NUMBER:   b"trackNumber",
            self.TRACK_TITLE:    b"trackTitle",
            self.ARTIST_NAME:    b"artistName",
            self.IS_FAVORITE:    b"isFavorite",
            self.DURATION_STR:   b"durationStr",
            self.PLAY_COUNT_STR: b"playCountStr",
            self.TRACK_GENRE:    b"trackGenre",
        }

    def set_tracks(self, tracks: list):
        disc_groups: dict = {}
        for idx, t in enumerate(tracks):
            disc = int(t.get('discNumber') or t.get('disc') or 1)
            disc_groups.setdefault(disc, []).append((idx, t))
        multi_disc = len(disc_groups) > 1

        rows = []
        for disc_num in sorted(disc_groups.keys()):
            if multi_disc:
                rows.append({'_disc': True, '_disc_label': f'Disc {disc_num}'})
            for disc_pos, (track_idx, t) in enumerate(disc_groups[disc_num], 1):
                raw_star = t.get('starred', False)
                is_fav   = raw_star.lower() in ('true', '1') if isinstance(raw_star, str) else bool(raw_star)
                dur_ms   = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
                secs     = dur_ms // 1000
                plays    = str(t.get('play_count') or 0) if t.get('play_count') else '-'
                num      = str(t.get('track') or (disc_pos if multi_disc else track_idx + 1))
                genre_raw = t.get('genre', '') or ''
                if genre_raw and ' • ' not in genre_raw:
                    for sep in ['; ', ';', ' | ', '|', ' / ', '/']:
                        genre_raw = genre_raw.replace(sep, ' • ')
                genre_parts = [g.strip() for g in genre_raw.split(' • ') if g.strip()]
                genre = ' • '.join(genre_parts[:3])
                rows.append({
                    '_disc': False,
                    '_idx':    track_idx,
                    '_id':     str(t.get('id', '')),
                    '_num':    num,
                    '_title':  t.get('title', ''),
                    '_artist': t.get('artist', ''),
                    '_fav':    is_fav,
                    '_dur':    f"{secs // 60}:{secs % 60:02d}",
                    '_plays':  plays,
                    '_genre':  genre,
                })
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update_favorite(self, track_idx: int, is_fav: bool):
        for i, r in enumerate(self._rows):
            if not r.get('_disc') and r.get('_idx') == track_idx:
                r['_fav'] = is_fav
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.IS_FAVORITE])
                break


class AlbumDetailCoverProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.cache = {}

    ART   = 264  # visible art pixels — matches _RoundedPixmapLabel(264, 264)
    PAD   = 30   # transparent shadow bleed — enough for blurRadius=38 + offset=10
    TOTAL = ART + PAD * 2  # 324

    BTN_D   = 58   # play button diameter — matches footer PlayButton
    BTN_PAD = 20   # shadow bleed around button
    BTN_TOT = BTN_D + BTN_PAD * 2   # 98

    def _btn_shadow(self, hex_color: str):
        import numpy as _np
        from scipy.ndimage import gaussian_filter as _gf
        d, pad, total = self.BTN_D, self.BTN_PAD, self.BTN_TOT
        SIGMA = 7.0   # fades to <1% at BTN_PAD boundary
        SA    = 180
        OY    = 3     # subtle downward offset

        base = QColor("#" + hex_color) if QColor("#" + hex_color).isValid() else QColor(136, 136, 136)
        sr, sg, sb = base.red(), base.green(), base.blue()

        # Rasterise circle at offset position
        mask_img = QImage(total, total, QImage.Format.Format_ARGB32)
        mask_img.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask_img)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad + OY, d, d))
        mp.fillPath(path, QColor(255, 255, 255, 255))
        mp.end()

        ptr = mask_img.bits(); ptr.setsize(total * total * 4)
        alpha_f = _np.frombuffer(ptr, dtype=_np.uint8).reshape((total, total, 4))[:, :, 3].astype(_np.float32) / 255.0
        blurred = _gf(alpha_f, sigma=SIGMA)
        shad_a  = (blurred * SA).clip(0, 255).astype(_np.uint8)

        shad_arr = _np.zeros((total, total, 4), dtype=_np.uint8)
        shad_arr[:, :, 0] = sb
        shad_arr[:, :, 1] = sg
        shad_arr[:, :, 2] = sr
        shad_arr[:, :, 3] = shad_a
        shad_bytes = bytes(shad_arr)
        shad_qimg  = QImage(shad_bytes, total, total, total * 4, QImage.Format.Format_ARGB32)

        img = QImage(total, total, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); p.drawImage(0, 0, shad_qimg); p.end()
        return img, img.size()

    def requestImage(self, cov_id, requestedSize):
        # "btn/<hexcolor>" → circular Gaussian shadow for play button
        if cov_id.startswith("btn/"):
            return self._btn_shadow(cov_id[4:])

        # "art/<id>" prefix → return just the 260×260 rounded art (no shadow)
        art_only = cov_id.startswith("art/")
        if art_only:
            cov_id = cov_id[4:]
        real_id = cov_id.split("?t=")[0]
        data    = self.cache.get(real_id)
        art, pad, total, r = self.ART, self.PAD, self.TOTAL, 10

        # Decode + crop source once regardless of mode
        source = None
        if data:
            src = QImage()
            src.loadFromData(data)
            if not src.isNull():
                src = src.scaled(art, art,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (src.width()  - art) // 2
                oy = (src.height() - art) // 2
                source = src.copy(ox, oy, art, art)

        if art_only:
            # Return 2× resolution so the Canvas always downscales (crisp at zoom 1.08×)
            art2 = art * 2   # 528
            img  = QImage(art2, art2, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            if not data:
                return img, img.size()
            src2 = QImage(); src2.loadFromData(data)
            if src2.isNull():
                return img, img.size()
            src2 = src2.scaled(art2, art2,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            ox2 = (src2.width()  - art2) // 2
            oy2 = (src2.height() - art2) // 2
            src2 = src2.copy(ox2, oy2, art2, art2)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawImage(0, 0, src2)
            painter.end()
            return img, img.size()

        # Full shadow + art image
        img = QImage(total, total, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        if source is None:
            return img, img.size()

        # Extract vibrant shadow colour — same logic as _extract_vibrant_color()
        # in now_playing_info.py: most-saturated pixel of an 8×8 sample, HSL L in (0.1,0.9)
        small  = source.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        best_sat = -1.0
        best_col = QColor(60, 60, 60)
        for sy in range(8):
            for sx in range(8):
                c = QColor(small.pixel(sx, sy))
                _, s, lv, _ = c.getHslF()
                if s > best_sat and 0.1 < lv < 0.9:
                    best_sat = s
                    best_col = c
        sr = best_col.red()   // 3
        sg = best_col.green() // 3
        sb = best_col.blue()  // 3
        SA = 210   # same as now-playing QGraphicsDropShadowEffect alpha
        OY = 10    # same as QGraphicsDropShadowEffect offset(0, 10)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Real Gaussian blur shadow — matches QGraphicsDropShadowEffect(blurRadius=38, offset=(0,10))
        # Step 1: rasterise shadow shape (rounded rect shifted down by OY) into an alpha mask
        import numpy as _np
        from scipy.ndimage import gaussian_filter as _gf
        SIGMA = 10.0   # tighter shadow: fades to ~1% at PAD=30px boundary

        mask_img = QImage(total, total, QImage.Format.Format_ARGB32)
        mask_img.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask_img)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        shadow_shape = QPainterPath()
        shadow_shape.addRoundedRect(QRectF(pad, pad + OY, art, art), r, r)
        mp.fillPath(shadow_shape, QColor(255, 255, 255, 255))
        mp.end()

        # Step 2: extract alpha, apply Gaussian blur
        ptr = mask_img.bits(); ptr.setsize(total * total * 4)
        mask_arr = _np.frombuffer(ptr, dtype=_np.uint8).reshape((total, total, 4))
        alpha_f  = mask_arr[:, :, 3].astype(_np.float32) / 255.0   # copy → safe to blur
        blurred  = _gf(alpha_f, sigma=SIGMA)
        shad_a   = (blurred * SA).clip(0, 255).astype(_np.uint8)

        # Step 3: build BGRA shadow image (Format_ARGB32 on LE = B,G,R,A in bytes)
        shad_arr = _np.zeros((total, total, 4), dtype=_np.uint8)
        shad_arr[:, :, 0] = sb
        shad_arr[:, :, 1] = sg
        shad_arr[:, :, 2] = sr
        shad_arr[:, :, 3] = shad_a
        shad_bytes = bytes(shad_arr)          # keep alive through drawImage
        shad_qimg  = QImage(shad_bytes, total, total, total * 4,
                            QImage.Format.Format_ARGB32)

        # Step 4: composite shadow then art
        painter.drawImage(0, 0, shad_qimg)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(pad, pad, art, art), r, r)
        painter.setClipPath(clip)
        painter.drawImage(pad, pad, source)
        painter.end()

        return img, img.size()


class AlbumIconProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = {}

    def requestImage(self, icon_id, requestedSize):
        parts     = icon_id.rsplit('_', 1)
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


class AlbumDetailBridge(QObject):
    # → QML
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
    albumDataChanged          = pyqtSignal(str, str, str, str, str, bool)  # title,artist,meta,type,covId,isFav
    coverIdChanged            = pyqtSignal(str)
    albumFavoriteChanged      = pyqtSignal(bool)
    playingStatusChanged      = pyqtSignal(str, bool)  # track_id, is_playing
    selectedTrackChanged      = pyqtSignal(int)         # trkIdx → QML highlight
    scrollToModelRow          = pyqtSignal(int)         # model row → QML scroll
    scrollToTopOfView         = pyqtSignal()
    scrollToBottomOfView      = pyqtSignal()

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

    @pyqtSlot(int)
    def navigateRow(self, delta: int):
        rows = self._view._track_model._rows
        if not rows:
            return
        st = self.search.text.lower()
        if st:
            nav = [(i, r['_idx']) for i, r in enumerate(rows)
                   if not r.get('_disc') and (
                       st in r.get('_title', '').lower() or
                       st in r.get('_artist', '').lower() or
                       st in r.get('_genre', '').lower())]
        else:
            nav = [(i, r['_idx']) for i, r in enumerate(rows) if not r.get('_disc')]
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

    @pyqtSlot(str, str)
    def albumArtistClicked(self, name: str, artist_id: str):
        self._view.artist_clicked.emit(name)

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
        if getattr(self._view, 'client', None):
            import threading
            threading.Thread(
                target=lambda: self._view.client.set_favorite(track.get('id'), new_fav),
                daemon=True).start()

    @pyqtSlot()
    def albumFavoriteClicked(self):
        self._view.toggle_header_heart()

    @pyqtSlot()
    def coverClicked(self):
        self._view._show_cover_zoom()

    @pyqtSlot(str, int, int, int)
    def showTooltip(self, text: str, cx: int, above_y: int, below_y: int):
        from PyQt6.QtWidgets import QApplication
        # Cancel any pending debounced hide (spurious Leave → Enter cycle)
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
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
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

    @pyqtSlot(int, float, float)
    def trackContextMenuRequested(self, track_idx: int, global_x: float, global_y: float):
        self._view._show_track_context_menu_at(track_idx, int(global_x), int(global_y))

    @pyqtSlot()
    def favHeaderClicked(self):
        self._view.fav_header_clicked.emit()

    @pyqtSlot(result='QVariantList')
    def getColWidths(self):
        from PyQt6.QtCore import QSettings
        saved = QSettings().value('album_detail/track_col_widths')
        if isinstance(saved, dict):
            return [int(saved.get('artist', 160)), int(saved.get('fav', 68)), int(saved.get('dur', 72)), int(saved.get('plays', 60)), int(saved.get('genre', 140))]
        return [160, 68, 72, 60, 140]

    @pyqtSlot(int, int, int, int, int)
    def saveColWidths(self, artist: int, fav: int, dur: int, plays: int, genre: int):
        from PyQt6.QtCore import QSettings
        QSettings().setValue('album_detail/track_col_widths', {'artist': artist, 'fav': fav, 'dur': dur, 'plays': plays, 'genre': genre})


class _AlbumKeyFilter(SearchKeyFilter):
    """Widget-level key filter — fires regardless of QML focus state.

    Routes typing into the track search box while active; otherwise
    handles tracklist row navigation/play/escape shortcuts.
    """

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


class AlbumDetailView(QWidget):
    play_clicked = pyqtSignal()
    shuffle_clicked = pyqtSignal()
    album_favorite_toggled = pyqtSignal(bool)
    artist_clicked = pyqtSignal(str)
    tracks_loaded = pyqtSignal()
    track_play_signal = pyqtSignal(list, int)
    track_artist_clicked = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)   # track_id, is_fav
    fav_header_clicked = pyqtSignal()
    genre_clicked = pyqtSignal(str)
    _meta_ready = pyqtSignal(str, str)
    _tracks_ready = pyqtSignal(list)
    _album_star_ready = pyqtSignal(bool)
    _track_mem_cache: dict = {}   # class-level LRU: album_id → tracks (shared across instances)
    _TRACK_MEM_MAX = 20

    @classmethod
    def _mem_cache_put(cls, album_id: str, tracks: list):
        cls._track_mem_cache[album_id] = tracks
        if len(cls._track_mem_cache) > cls._TRACK_MEM_MAX:
            cls._track_mem_cache.pop(next(iter(cls._track_mem_cache)))

    def __init__(self, client=None):
        super().__init__()
        self.client  = client
        self._tracks: list = []
        self._album_liked  = False
        self._bg_color     = '14,14,14'
        self.current_album_id = None

        self._track_model   = AlbumDetailTrackModel()
        self._cover_provider = AlbumDetailCoverProvider()
        self._icon_provider  = AlbumIconProvider()
        self._bridge         = AlbumDetailBridge(self)

        self._meta_ready.connect(self._on_meta_ready)
        self._tracks_ready.connect(self._on_tracks_ready)
        self._album_star_ready.connect(self.set_header_heart_state)

        self._qml = QQuickWidget()
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._album_key_filter = _AlbumKeyFilter(self._bridge, self._qml)
        self._qml.installEventFilter(self._album_key_filter)

        engine = self._qml.engine()
        engine.addImageProvider("albumdetailcover", self._cover_provider)
        engine.addImageProvider("albumicons",        self._icon_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("trackModel",  self._track_model)
        ctx.setContextProperty("albumBridge", self._bridge)

        self._qml.setSource(QUrl.fromLocalFile(resource_path("album_detail.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

        # QML-only init complete

    # ── Internal signal handlers ──────────────────────────────────────────────

    def _on_meta_ready(self, artist: str, meta: str):
        b = self._bridge
        # re-emit albumDataChanged with updated artist/meta — keep other fields
        b.albumDataChanged.emit(
            getattr(self, '_cur_title', ''),
            artist if artist else getattr(self, '_cur_artist', ''),
            meta,
            getattr(self, '_cur_type', ''),
            getattr(self, '_cur_cov_id', ''),
            self._album_liked,
        )

    def _on_tracks_ready(self, tracks: list):
        self._tracks = tracks
        self._track_model.set_tracks(tracks)
        self._bridge._selected_trkidx = -1
        self._bridge.selectedTrackChanged.emit(-1)
        self.tracks_loaded.emit()
        # Re-apply playing indicator
        if getattr(self, '_last_playing_id', None):
            self._bridge.playingStatusChanged.emit(
                self._last_playing_id,
                getattr(self, '_last_is_playing', False),
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def toggle_header_heart(self):
        new_state = not self._album_liked
        self.set_header_heart_state(new_state)
        self.album_favorite_toggled.emit(new_state)

    def set_header_heart_state(self, is_liked: bool):
        self._album_liked = is_liked
        self._bridge.albumFavoriteChanged.emit(is_liked)

    def update_playing_status(self, playing_id, is_playing: bool, accent: str):
        self._last_playing_id   = playing_id
        self._last_is_playing   = is_playing
        self._last_playing_accent = accent
        # add_batch_to_ui calls update_indicator while current_index=-1, emitting (None, True).
        # Suppress it — it would clear the row highlight with no track to replace it.
        if not playing_id and is_playing:
            return
        self._bridge.playingStatusChanged.emit(str(playing_id) if playing_id else '', is_playing)

    def _toggle_track_search(self):
        if self._bridge.search.active:
            self._bridge.search.close()
        else:
            self._bridge.search.open()

    def _set_window_shortcuts_enabled(self, enabled: bool):
        set_window_shortcuts_enabled(self, self._qml, enabled)

    def set_bg_color(self, c: str):
        self._bg_color = c
        try:
            r, g, b = (int(x) for x in c.split(','))
            self._qml.setClearColor(QColor(r, g, b))
        except Exception:
            pass

    def set_active_hover(self, color):
        pass  # handled via hoverColor in QML

    def set_accent_color(self, color):
        from player.mixins.visuals import resolve_menu_hover
        theme = getattr(self.window(), 'theme', None)
        self._bridge.accentColorChanged.emit(color)
        self._bridge.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')
        if theme:
            self._bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
            self._bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            self._bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
            self._bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
            self._bridge.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))
            self._bridge.skeletonColorChanged.emit(
                getattr(theme, 'skeleton_base', '#282828'))
            self._bridge.cardBgChanged.emit(
                getattr(theme, 'now_playing_card_bg', '#1e1e1e'))
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

    def load_album(self, album_data: dict):
        import threading, re
        self.current_album_id = album_data.get('id')
        _album_id    = self.current_album_id
        title        = album_data.get('title') or album_data.get('name') or "Unknown Album"
        album_artist = (album_data.get('albumArtist') or album_data.get('album_artist')
                        or album_data.get('artist') or "Unknown Artist")
        alb_type     = str(album_data.get('type') or '').capitalize()

        # Cache fields used by _on_meta_ready
        self._cur_title   = title
        self._cur_artist  = album_artist
        self._cur_type    = alb_type
        self._cur_cov_id  = ''

        self._bridge.search.reset()
        self._bridge.albumDataChanged.emit(title, album_artist, "Loading...", alb_type, '', self._album_liked)
        self._track_model.set_tracks([])
        self._tracks = []

        if not (hasattr(self, 'client') and self.client):
            return

        # ── Instant pre-load: memory cache → disk cache ───────────────────────
        _pre_loaded = False
        _pre_tracks = self._track_mem_cache.get(str(_album_id))
        if _pre_tracks:
            self._on_tracks_ready(_pre_tracks)
            _pre_loaded = True
        else:
            try:
                _disk = self.client._disk_cache_get(f"album_tracks_{_album_id}")
                if _disk:
                    self._on_tracks_ready(_disk)
                    self._mem_cache_put(str(_album_id), _disk)
                    _pre_tracks = _disk
                    _pre_loaded = True
            except Exception:
                pass

        def compute_meta(tracks):
            if not tracks: return "", "Ready."
            found_aa = None; artist_counts = {}; total_sec = 0
            for t in tracks:
                total_sec += t.get('duration_ms', 0) // 1000
                aa = t.get('albumArtist') or t.get('album_artist')
                if aa and not found_aa: found_aa = aa
                a = t.get('artist', '')
                if a:
                    main_a = re.split(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )', a)[0].strip()
                    artist_counts[main_a] = artist_counts.get(main_a, 0) + 1
            n = len(tracks)
            if found_aa: detected = found_aa
            elif artist_counts:
                best = max(artist_counts, key=artist_counts.get)
                detected = best if artist_counts[best] >= (n * 0.4) else "Various Artists"
            else: detected = album_artist
            m, s = divmod(total_sec, 60)
            time_str = f"{m//60} hr {m%60} min" if m >= 60 else f"{m} min {s} sec"
            year = str(album_data.get('year', '')).replace('None', '')
            return detected, " • ".join(p for p in [year, f"{n} songs", time_str] if p)

        if _pre_tracks:
            _detected, _meta_str = compute_meta(_pre_tracks)
            if _detected: self._cur_artist = _detected
            if _meta_str:
                self._bridge.albumDataChanged.emit(
                    self._cur_title, self._cur_artist, _meta_str,
                    self._cur_type, self._cur_cov_id, self._album_liked)

        def _fetch_fresh():
            try:
                cached_meta = ""
                if not _pre_loaded:
                    raw_stale = self.client.get_album_tracks(_album_id)
                    if raw_stale:
                        detected, cached_meta = compute_meta(raw_stale)
                        self._meta_ready.emit(detected, cached_meta)
                        self._tracks_ready.emit(raw_stale)
                        self._mem_cache_put(str(_album_id), raw_stale)
                try: self.client._scan_status_cache = None
                except: pass
                raw_fresh = self.client.get_album_tracks(_album_id, force_refresh=True)
                if not raw_fresh: return
                try:
                    _star = self.client.stale_cache_get(f'album_starred_{_album_id}')
                    if _star is not None:
                        self._album_star_ready.emit(bool(_star))
                except Exception: pass
                try:
                    starred_ids = self.client.get_starred_ids_cached()
                    for t in raw_fresh:
                        t['starred'] = str(t.get('id', '')) in starred_ids
                except Exception: pass
                fresh_detected, fresh_meta = compute_meta(raw_fresh)
                if fresh_meta != cached_meta or not _pre_loaded:
                    self._meta_ready.emit(fresh_detected, fresh_meta)
                self._tracks_ready.emit(raw_fresh)
                self._mem_cache_put(str(_album_id), raw_fresh)
            except Exception as e:
                print(f"[AlbumDetailView] fetch error: {e}")
                self._meta_ready.emit("", "Ready.")

        threading.Thread(target=_fetch_fresh, daemon=True).start()

        # Starred state
        _cached_star = self.client.stale_cache_get(f'album_starred_{_album_id}')
        if _cached_star is not None:
            is_fav = bool(_cached_star)
        else:
            is_fav = bool(album_data.get('starred') or album_data.get('favorite'))
        self.set_header_heart_state(is_fav)

        # Cover art
        cid = album_data.get('cover_id') or album_data.get('coverArt') or album_data.get('id')
        if cid:
            self._cur_cov_id = cid
            from cover_cache import CoverCache
            data = CoverCache.instance().get_full(cid) or CoverCache.instance().get_thumb(cid)
            if data:
                self._cover_provider.cache[cid] = data
                self._bridge.coverIdChanged.emit(cid)
            threading.Thread(target=lambda: self._fetch_cover(cid), daemon=True).start()

    def _show_cover_zoom(self):
        cid = getattr(self, '_cur_cov_id', '')
        data = self._cover_provider.cache.get(cid) if cid else None
        if not data:
            return
        from PyQt6.QtGui import QPixmap
        from now_playing_info import _CoverOverlay
        pix = QPixmap()
        pix.loadFromData(data)
        main = self.window()
        _CoverOverlay(pix, main, cover_id=cid, client=getattr(self, 'client', None))

    def _fetch_cover(self, cid: str):
        if not getattr(self, 'client', None): return
        try:
            from cover_cache import CoverCache
            d = self.client.get_cover_art(cid, size=None)
            if d:
                CoverCache.instance().save_full(cid, d)
                self._cover_provider.cache[cid] = d
                import time
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._bridge.coverIdChanged.emit(f"{cid}?t={time.time()}"))
        except Exception:
            pass

    def _show_track_context_menu_at(self, track_idx: int, global_x: int, global_y: int):
        if not (0 <= track_idx < len(self._tracks)):
            return
        track = self._tracks[track_idx]
        main  = self.window()
        from player.widgets import themed_shadow_menu, popup_menu_at_global
        menu = themed_shadow_menu(self, bg=getattr(self, '_bg_color', None))
        track_id = str(track.get('id', ''))
        artist   = track.get('artist', '')
        menu.add_action('Play Now',     lambda: self.track_play_signal.emit([track], 0), icon_path='img/sub_play.png')
        menu.add_action('Play Next',    lambda: main.play_track_next(track) if hasattr(main, 'play_track_next') else None, icon_path='img/sub_next.png')
        menu.add_action('Add to Queue', lambda: main.add_track_to_queue(track) if hasattr(main, 'add_track_to_queue') else None, icon_path='img/queue.png')
        menu.add_action('Go to Artist', lambda: self.track_artist_clicked.emit(artist) if artist else None,
                        enabled=bool(artist), icon_path='img/sub_artist.png')
        menu.add_action('Start Radio',  lambda: main.start_radio(track) if hasattr(main, 'start_radio') else None, icon_path='img/radio.png')
        playlists = getattr(getattr(main, 'playlists_browser', None), 'all_playlists', None) or []
        if track_id:
            pl_items = [('New Playlist…', lambda: self._add_to_new_playlist(main, [track_id]), 'img/add.png')]
            pl_items += [(f"{pl.get('name','Unnamed')}  ({pl.get('songCount','')})" if pl.get('songCount','') != '' else pl.get('name','Unnamed'),
                          lambda pid=pl.get('id'), pn=pl.get('name',''): self._add_to_existing_playlist(main, pid, pn, [track_id]),
                          'img/playlist.png')
                         for pl in playlists if pl.get('id')]
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')
        tb = getattr(main, 'tracks_browser', None)
        menu.add_action('Get Info', callback=(lambda: tb._show_track_info(track)) if tb else None,
                        enabled=bool(tb), icon_path='img/info.png')
        raw_star = track.get('starred', False)
        is_fav   = raw_star.lower() in ('true', '1') if isinstance(raw_star, str) else bool(raw_star)
        menu.add_action('Remove from Favorites' if is_fav else 'Add to Favorites',
                        lambda i=track_idx: self._bridge.trackFavoriteClicked(i),
                        color='#E91E63',
                        icon_path='img/heart_filled.png' if is_fav else 'img/heart.png')
        popup_menu_at_global(menu, global_x, global_y, window=main)

    def _add_to_new_playlist(self, main, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client: return
        from components import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        accent = getattr(main, 'master_color', '#1DB954')
        dialog = NewPlaylistDialog(self, accent_color=accent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name: return
            import threading
            def _worker():
                try:
                    new_id = client.create_playlist(name, public=dialog.is_public())
                    if new_id: client.add_tracks_to_playlist(new_id, track_ids)
                except Exception as e:
                    print(f"AlbumDetail: create playlist failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _add_to_existing_playlist(self, main, pl_id, pl_name, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client: return
        import threading
        threading.Thread(
            target=lambda: client.add_tracks_to_playlist(pl_id, track_ids),
            daemon=True).start()

    # ─── end of AlbumDetailView ───────────────────────────────────────────────


class _GridKeyFilter(SearchKeyFilter):
    """Widget-level key filter for the albums grid's inline search box.

    Routes typing into the grid search box while active; Return commits the
    search instantly and jumps focus to the first grid item. All other keys
    (arrows, page up/down, space, enter when not searching) fall through
    unhandled to the QML GridView's own Keys.onPressed.
    """

    def __init__(self, view, parent=None):
        super().__init__(view.grid_bridge.search, on_navigate=self._navigate, parent=parent)
        self._view = view

    def _navigate(self, event):
        if self._ctl.active and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._view.focus_first_grid_item()
            return True
        return False


class LibraryGridBrowser(QWidget):
    play_track_signal = pyqtSignal(dict) 
    play_album_signal = pyqtSignal(list) 
    queue_track_signal = pyqtSignal(dict)
    play_next_signal = pyqtSignal(dict)
    switch_to_artist_tab = pyqtSignal(str)
    genre_filter_requested = pyqtSignal(str)
    album_clicked = pyqtSignal(dict)
    
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.last_reload_time = time.time()

        # 1: Add the master opacity box to the entire tab!
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 0; }")

        self.cover_worker = None
        if self.client:
            self.set_client(client)
        
        self.offset = 0
        self.batch_size = 52
        self.is_loading = False
        self.has_more = True
        self.current_query = "" 
        self.current_accent = "#888888"  # Default accent color
        
        # --- PAGINATION SETTINGS ---
        self.page_size = 52
        self.current_page = 1
        self.total_pages = 1
        self.total_items = 0
                
        # Search timer for debounced search
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(400)
        self.search_timer.timeout.connect(self.execute_search)
              
        self.pending_items = {}
        self.nav_history = []
        self.nav_index = -1
        self.current_album_id = None
        self.current_header_cover_id = None
        self.current_album_id = None
        self._active_workers = set()

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.sort_states = {
            'random': True,
            'latest': True,
            'alphabetical': True,
            'favorites': True,
            'compilations': True,
            'song_count': False,
        }
        self.current_sort = 'latest'

        self.stack = QStackedWidget()
        # PREVENT GHOSTING: Tell the stack to be transparent so QML can composite cleanly!
        self.stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.stack.setStyleSheet("background: transparent;")
        self.layout.addWidget(self.stack)


        self.qml_view = QMLGridWrapper()
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        
        # DEMOTE Z-ORDER: Let Spotlight sit on top naturally without breaking the OS!
        self.qml_view.setClearColor(self._qml_bg_color())
        self.qml_view.setStyleSheet("border: none;")
        
        self.album_model = AlbumModel()
        self.grid_bridge = GridBridge(self.album_model, self)

        self.grid_bridge.itemClicked.connect(self.album_clicked.emit)
        self.grid_bridge.playClicked.connect(lambda data: self.start_play_fetch(data['id']))
        self.grid_bridge.visibleRangeChanged.connect(self.check_viewport_qml)
        self.grid_bridge.artistNameClicked.connect(self._on_grid_artist_name_clicked)

        # OMNI-SCROLLER FIX: Python-side middle-click scroller for the QML grid.
        # QMLGridWrapper.verticalScrollBar() returns a DummyScrollBar (no-op), so the
        # standard MiddleClickScroller can't move the view.  This variant pushes pixel
        # deltas via GridBridge.scrollBy which QML maps directly to grid.contentY.
        self.omni_scroller = QMLMiddleClickScroller(self.qml_view, self.grid_bridge)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("albumModel", self.album_model)
        ctx.setContextProperty("bridge", self.grid_bridge)
        
        engine = self.qml_view.engine()
        self.cover_provider = engine.imageProvider("covers")
        if not self.cover_provider:
            self.cover_provider = CoverImageProvider()
            engine.addImageProvider("covers", self.cover_provider)

        self._icon_provider = AlbumIconProvider()
        engine.addImageProvider("albumicons", self._icon_provider)

        self._grid_key_filter = _GridKeyFilter(self)
        self.qml_view.installEventFilter(self._grid_key_filter)

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("album_grid.qml")))
        # Push initial theme typography values to QML immediately
        from PyQt6.QtCore import QTimer as _QTimer
        def _emit_initial_typography():
            theme = getattr(self.window(), 'theme', None)
            if theme and hasattr(self, 'grid_bridge'):
                self.grid_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.grid_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.grid_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.grid_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
        _QTimer.singleShot(0, _emit_initial_typography)
        
        self.grid_view = self.qml_view 
        self.stack.addWidget(self.grid_view)
        
                
      
        self.detail_view = AlbumDetailView(self.client)
        
        
        # Wire up the header buttons
        self.detail_view.play_clicked.connect(self.on_play_all_clicked)
        self.detail_view.shuffle_clicked.connect(self.on_shuffle_album_clicked)
        self.detail_view.album_favorite_toggled.connect(self.on_album_heart_clicked)
        self.detail_view.artist_clicked.connect(self.switch_to_artist_tab)
        self.detail_view.track_artist_clicked.connect(self.switch_to_artist_tab)
        self.detail_view.genre_clicked.connect(self.genre_filter_requested)
        self.detail_view.track_play_signal.connect(self._on_detail_track_play)
        
        self.stack.addWidget(self.detail_view)

        self.is_fetching_next = False

        self.set_accent_color("#888888")


        self.add_to_history({'type': 'root'})

        self.refresh_grid()

    def change_page(self, page):
        if page < 1 or page > self.total_pages: 
            return
        self.current_page = page
        self.load_albums_page(reset=False)

    def _on_grid_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()

    def _set_window_shortcuts_enabled(self, enabled: bool):
        set_window_shortcuts_enabled(self, self.qml_view, enabled)

    def _toggle_track_search(self):
        if self.stack.currentIndex() == 1:
            self.detail_view._toggle_track_search()
            return
        if self.grid_bridge.search.active:
            self.grid_bridge.search.close()
        else:
            self.grid_bridge.search.open()

    def set_status_text(self, text):
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.statusTextChanged.emit(text)


    def focus_first_grid_item(self):
        """Forces an instant search and jumps keyboard focus to the first item."""
        if self.search_timer.isActive():
            self.search_timer.stop()
            self.execute_search()
            
        def apply_focus():
            if self.grid_view.count() > 0:
                self.grid_view.setFocus(Qt.FocusReason.ShortcutFocusReason)
                self.grid_view.setCurrentRow(0)
                
        # Wait 50ms before grabbing focus so the Enter key doesn't bleed into the grid!
        QTimer.singleShot(50, apply_focus)
    
    def execute_search(self):
        """Clears the current view and triggers a fresh server-side search."""
        self.filtered_items = None
        self.load_albums_page(reset=True)

    def _safe_discard_worker(self, worker):
        
        if not worker: return
        worker.is_cancelled = True
        try: worker.page_ready.disconnect()
        except: pass
        
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
            
        self._worker_graveyard.add(worker)
        
        def remove_from_grave():
            if hasattr(self, '_worker_graveyard') and worker in self._worker_graveyard:
                self._worker_graveyard.remove(worker)
                
        try: worker.finished.connect(remove_from_grave)
        except: pass

    def stop_all_workers(self):
        """Quit all running QThread workers — called on app shutdown."""
        workers = set(getattr(self, '_worker_graveyard', set()))
        for w in getattr(self, 'active_chunk_workers', {}).values():
            workers.add(w)
        for attr in ('live_worker', '_compilations_worker', 'cover_worker'):
            w = getattr(self, attr, None)
            if w:
                workers.add(w)
        for w in workers:
            if w.isRunning():
                if hasattr(w, 'stop'):
                    w.stop()  # break internal time.sleep / threading.Event loop
                w.quit()
                if not w.wait(600):
                    w.terminate()
        if hasattr(self, '_worker_graveyard'):
            self._worker_graveyard.clear()
        if hasattr(self, 'active_chunk_workers'):
            self.active_chunk_workers.clear()

    def show_sort_menu_at(self, global_x, global_y):
        """Show dropdown menu with sort options when the burger icon is clicked"""
        from player.widgets import themed_shadow_menu, popup_menu_at_global
        menu = themed_shadow_menu(self)

        def _sort_icon(sort_type):
            is_asc = self.sort_states.get(sort_type, True)
            suffix = 'a' if is_asc else 'd'
            return f"img/sort-{sort_type}-{suffix}.png"

        for sort_type, label in [('random', 'Random'), ('latest', 'Latest'),
                                   ('alphabetical', 'Alphabetical'), ('song_count', 'Song Count')]:
            st = sort_type
            menu.add_action(label, lambda s=st: self.toggle_sort_state(s),
                            icon_path=_sort_icon(st))

        menu.add_action('Favourites',    lambda: self.toggle_sort_state('favorites'),    icon_path='img/heart.png')
        menu.add_action('Compilations',  lambda: self.toggle_sort_state('compilations'), icon_path='img/comp.png')

        popup_menu_at_global(menu, global_x, global_y, window=self.window())

    def toggle_sort_state(self, sort_type):
        """Toggle the sort state and update display"""
        if sort_type in ('favorites', 'compilations'):
            # Pure filters — no asc/desc, just activate
            self.current_sort = sort_type
        elif self.current_sort == sort_type:
            # If clicking the currently active sort, flip its direction
            self.sort_states[sort_type] = not self.sort_states[sort_type]
        else:
            # If switching to a NEW sort, make it active and reset to its default direction
            self.current_sort = sort_type
            self.sort_states[sort_type] = False if sort_type == 'song_count' else True
            
        # CACHE FLUSH FOR RANDOM: Re-randomize every single time it's clicked!
        if sort_type == 'random' and hasattr(self, 'client'):
            # Access the internal .cache dictionary to perform deletions
            keys_to_delete = [k for k in self.client._api_cache.cache.keys() if 'albums_random' in k]
            for k in keys_to_delete:
                del self.client._api_cache.cache[k]
            
        self.true_server_count = 0   # force re-count from new sort endpoint
        self.update_burger_icon()
        self.load_albums_page(reset=True)

    def update_burger_icon(self):
        """Update the burger icon name to reflect the currently active sort."""
        if not hasattr(self, 'current_sort'):
            return

        if self.current_sort == 'favorites':
            name = 'heart'
        elif self.current_sort == 'compilations':
            name = 'comp'
        else:
            is_ascending = self.sort_states.get(self.current_sort, True)
            if self.current_sort == 'song_count':
                name = f"sort-num-{'asc' if is_ascending else 'desc'}"
            else:
                name = f"sort-{self.current_sort}-{'a' if is_ascending else 'd'}"

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.burgerIconChanged.emit(name)

    def load_album(self, album_data):
        """Bridge method: main.py calls this to navigate to an album from other tabs."""
        # Instantly pass the request to the main routing system
        self.show_album_details(album_data)
    
    def _on_initial_count_loaded(self, albums, total_count):
        """Saves the total library size and instantly restarts the grid generation."""
        self.true_server_count = total_count if total_count else 0
        if self.true_server_count and self.client:
            self.client.stale_cache_set('albums_count', self.true_server_count)
        self.load_albums_page()

    def _on_search_loaded(self, albums, total_count=0):
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None
            
        self.album_model.clear()

        if not albums:
            self.set_status_text("0 albums")
            return

        # Safely handle if the API returns 'None' instead of 0!
        tc = total_count if total_count is not None else 0
        display_count = tc if tc > 0 else len(albums)

        self.set_status_text(f"{display_count:,} albums".replace(",", " "))
        self.populate_grid(albums)

        if hasattr(self, 'qml_view') and self.isVisible():
            if not self.grid_bridge.search.active:
                self.qml_view.setFocus()

    def _on_compilations_loaded(self, albums):
        self.album_model.clear()
        if not albums:
            self.set_status_text("0 albums")
            return
        self.set_status_text(f"{len(albums):,} albums".replace(",", " "))
        self.populate_grid(albums)
        if hasattr(self, 'qml_view') and self.isVisible():
            if not self.grid_bridge.search.active:
                self.qml_view.setFocus()

    def _on_grid_artist_name_clicked(self, name, artist_id):
        if artist_id:
            client = getattr(self, 'client', None)
            if client and hasattr(client, '_artist_name_id'):
                client._artist_name_id[name.lower().strip()] = artist_id
        self.switch_to_artist_tab.emit(name)

    def on_artist_name_clicked(self, artist_name):
        self.switch_to_artist_tab.emit(artist_name)

    def show_album_details(self, album_data, record_history=True):
        if record_history:
            self.add_to_history({'type': 'album', 'data': album_data})
            return

        # Switch the UI view
        self.stack.setCurrentIndex(1)

        # Keep track of the ID for grid operations
        self.current_album_id = album_data.get('id')

        self.detail_view.load_album(album_data)

    def on_shuffle_album_clicked(self):
        if not self.current_album_id: return
        if not getattr(self, 'client', None): return
        try:
            tracks = self.client.get_album_tracks(str(self.current_album_id))
            if tracks:
                random.shuffle(tracks)
                self.play_album_signal.emit(tracks)
        except Exception as e:
            print(f"Error fetching album tracks for shuffle: {e}")

    def on_album_heart_clicked(self, is_liked):
        if not self.current_album_id: return
        try:
            # 1. Tell the server to star/unstar the album
            if getattr(self, 'client', None):
                self.client.set_favorite(self.current_album_id, is_liked)
            
            # Update the data inside the QML album_model!
            for i, album in enumerate(self.album_model.albums):
                if album and album.get('id') == self.current_album_id:
                    album['starred'] = is_liked
                    album['favorite'] = is_liked 
                    
                    # Tell QML the data changed just in case
                    idx = self.album_model.index(i, 0)
                    self.album_model.dataChanged.emit(idx, idx, [self.album_model.RAW_DATA_ROLE])
                    break
                    
        except Exception as e: 
            print(f"Error toggling album heart: {e}")
  
    def add_to_history(self, state):
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_history = self.nav_history[:self.nav_index + 1]
        self.nav_history.append(state)
        self.nav_index += 1
        if len(self.nav_history) > 20:
            self.nav_history = self.nav_history[-20:]
            self.nav_index = len(self.nav_history) - 1
        
        self.render_state(state)

    def render_state(self, state):
        s_type = state.get('type')
        self.current_album_id = None 
        if s_type == 'root':
            self.stack.setCurrentIndex(0)
            if self.album_model.rowCount() == 0: self.refresh_grid()
        elif s_type == 'album':
            self.current_album_id = state['data']['id']
            self.show_album_details(state['data'], record_history=False)
        elif s_type == 'artist':
            self.show_artist_details(state['data'], record_history=False)

    def start_play_fetch(self, album_id):
        if not album_id: return
        if not getattr(self, 'client', None): return
        try:
            tracks = self.client.get_album_tracks(str(album_id))
            if tracks:
                self.play_album_signal.emit(tracks)
        except Exception as e:
            print(f"Error fetching album tracks to play: {e}")

    def show_artist_details(self, artist_data, record_history=True):
        self.switch_to_artist_tab.emit(artist_data['name'])

    def _on_detail_track_play(self, tracks, start_index):
        if 0 <= start_index < len(tracks):
            self.play_album_signal.emit([tracks[start_index]])

    def on_play_all_clicked(self):
        if not self.current_album_id: return
        if not getattr(self, 'client', None): return
        try:
            tracks = self.client.get_album_tracks(str(self.current_album_id))
            if tracks:
                self.play_album_signal.emit(tracks)
        except Exception as e:
            print(f"Error fetching album tracks: {e}")

    def _qml_bg_color(self):
        r, g, b = (int(x) for x in getattr(self, '_bg_color', '14,14,14').split(','))
        return QColor(r, g, b)

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")
        if hasattr(self, 'qml_view'):
            self.qml_view.setClearColor(self._qml_bg_color())

    def set_accent_color(self, color):
        if hasattr(self, 'grid_bridge'):
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.grid_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.grid_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.grid_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.grid_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
                self.grid_bridge.skeletonBaseColorChanged.emit(
                    getattr(theme, 'skeleton_base', '#282828'))
                self.grid_bridge.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))

                from player.mixins.visuals import resolve_menu_hover
                self.grid_bridge.hoverColorChanged.emit(resolve_menu_hover(theme))

                border = getattr(theme, 'border_color', '#2a2a2a')
                if not getattr(theme, 'auto_border_from_accent', True):
                    border = getattr(theme, 'manual_border_color', '#2a2a2a')
                self.grid_bridge.cardBorderChanged.emit(border)

                raw_bg = getattr(theme, 'main_panel_bg', '14,14,14')
                try:
                    r, g, b = (int(x) for x in raw_bg.split(','))
                    self.grid_bridge.panelBgChanged.emit('#{:02x}{:02x}{:02x}'.format(r, g, b))
                except Exception:
                    self.grid_bridge.panelBgChanged.emit('#0e0e0e')

        if getattr(self, 'current_accent', None) == color:
            return

        self.current_accent = color

        # Force Python to paint the darkness so the GPU clears its old frames!
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(1.0)

        # Update the detail view behind the scenes
        if hasattr(self, 'detail_view'):
            self.detail_view.set_accent_color(color)

        if hasattr(self, 'update_burger_icon'):
            self.update_burger_icon()

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def check_viewport_qml(self, start_idx, end_idx):
        if self.album_model.rowCount() == 0: return
        if getattr(self, 'current_sort', 'latest') == 'compilations': return

        # Lazy placeholder expansion — triggered 2 rows ahead of visible edge,
        # deferred 80ms so it never fires during active scroll frames.
        total = getattr(self, 'true_server_count', 0)
        current_rows = self.album_model.rowCount()
        if total > current_rows and end_idx >= current_rows - 20:
            if not getattr(self, '_placeholder_expansion_pending', False):
                self._placeholder_expansion_pending = True
                def _expand():
                    self._placeholder_expansion_pending = False
                    rows_now = self.album_model.rowCount()
                    tot_now  = getattr(self, 'true_server_count', 0)
                    if tot_now > rows_now:
                        expand_to = min(rows_now + 100, tot_now)
                        extra = [{'type': 'placeholder', 'title': ''} for _ in range(expand_to - rows_now)]
                        self.album_model.append_albums(extra)
                from PyQt6.QtCore import QTimer as _QT
                _QT.singleShot(80, _expand)



        start_chunk = max(0, start_idx // 50)
        end_chunk = max(0, end_idx // 50)
        visible_chunks = set(range(start_chunk, end_chunk + 1))

        if not hasattr(self, 'loaded_chunks'): self.loaded_chunks = set()
        if not hasattr(self, 'active_chunk_workers'): self.active_chunk_workers = {}

        # 1. GHOST CANCEL
        for chunk, worker in list(self.active_chunk_workers.items()):
            if chunk not in visible_chunks:
                self._safe_discard_worker(worker)
                del self.active_chunk_workers[chunk]
                if chunk in self.loaded_chunks: self.loaded_chunks.remove(chunk)
                    
        # 2. RAM GARBAGE COLLECTION
        for chunk in list(self.loaded_chunks):
            if abs(chunk - start_chunk) > 3: 
                self.loaded_chunks.remove(chunk)
                chunk_start = chunk * 50
                chunk_end = min(chunk_start + 50, self.true_server_count)
                
                for i in range(chunk_start, chunk_end):
                    cur = self.album_model.albums[i]
                    evicted = dict(cur) if isinstance(cur, dict) else {}
                    evicted['type'] = 'placeholder'
                    self.album_model.albums[i] = evicted

                # Tell QML the chunks were wiped to save RAM
                self.album_model.dataChanged.emit(
                    self.album_model.index(chunk_start, 0),
                    self.album_model.index(chunk_end - 1, 0),
                    [self.album_model.TITLE_ROLE, self.album_model.ARTIST_ROLE,
                    self.album_model.YEAR_ROLE, self.album_model.COVER_ID_ROLE,
                    self.album_model.IS_LOADING_ROLE]
                )
                            
        # 3. FETCH VISIBLE
        for chunk in visible_chunks:
            if chunk not in self.loaded_chunks and chunk not in self.active_chunk_workers:
                self.loaded_chunks.add(chunk)
                self.active_chunk_workers[chunk] = self.fetch_chunk(chunk)

    def apply_cover(self, cover_id, image_data):
        # Feed the downloaded bytes into our Image Provider
        if hasattr(self, 'cover_provider'):
            self.cover_provider.image_cache[str(cover_id)] = image_data
            
        # Tell QML the image is ready to be drawn!
        if hasattr(self, 'album_model'):
            self.album_model.update_cover(str(cover_id))

    def populate_grid(self, items):
        self.album_model.clear()
        for item in items:
            cid = item.get('cover_id') or item.get('coverArt') or item.get('id')
            if cid:
                item['cover_id'] = cid
                if self.cover_worker:
                    self.cover_worker.queue_cover(cid, priority=False)
        self.album_model.append_albums(items)

    def _on_chunk_loaded(self, albums, chunk_index):
        # Persist chunk 0 for fast next-session display (default sort only)
        if chunk_index == 0 and albums and self.client and not getattr(self, 'current_query', ''):
            _sort = getattr(self, 'current_sort', 'latest')
            self.client.stale_cache_set(f'albums_chunk_0_{_sort}', albums)
        if hasattr(self, 'active_chunk_workers') and chunk_index in self.active_chunk_workers:
            worker = self.active_chunk_workers.pop(chunk_index)
            self._safe_discard_worker(worker)
            
        if not albums: return
        start_row = chunk_index * 50
        if start_row >= len(self.album_model.albums):
            if hasattr(self, 'loaded_chunks'):
                self.loaded_chunks.discard(chunk_index)
            return
        covers_to_queue = []
        
        client = getattr(self, 'client', None)
        name_id_cache = getattr(client, '_artist_name_id', None) if client else None
        for i, album_data in enumerate(albums):
            target_row = start_row + i
            if target_row >= len(self.album_model.albums): break

            cid = album_data.get('cover_id') or album_data.get('coverArt') or album_data.get('id')
            if cid:
                album_data['cover_id'] = cid
                covers_to_queue.append(cid)

            if name_id_cache is not None:
                aid = album_data.get('artistId') or album_data.get('albumArtistId')
                aname = album_data.get('artist') or album_data.get('albumArtist') or album_data.get('name', '')
                if aid and aname:
                    name_id_cache[aname.lower().strip()] = aid

            self.album_model.albums[target_row] = album_data
            
        self.album_model.dataChanged.emit(
            self.album_model.index(start_row, 0),
            self.album_model.index(start_row + len(albums) - 1, 0),
            [self.album_model.TITLE_ROLE, self.album_model.ARTIST_ROLE,
            self.album_model.YEAR_ROLE, self.album_model.COVER_ID_ROLE,
            self.album_model.IS_LOADING_ROLE]
        )
        
        if hasattr(self, 'cover_worker') and self.cover_worker:
            self.cover_worker.queue_batch(covers_to_queue, priority=True)

    def load_albums_page(self, reset=False):
        # If we just restored from a save file, don't let it reset our page!
        if getattr(self, '_restored_state_waiting', False):
            reset = False 
            self._restored_state_waiting = False
            
        if reset:
            self.current_page = 0

        if not getattr(self, 'client', None): return
        if self.cover_worker: self.cover_worker.queue.clear()

        if hasattr(self, 'active_chunk_workers'):
            for chunk, worker in list(self.active_chunk_workers.items()):
                self._safe_discard_worker(worker)
            self.active_chunk_workers.clear()
            
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None

        self.pending_items.clear()
        self.loaded_chunks = set()
        query = getattr(self, 'current_query', '')

        if query:
            self.show_loading()
            worker = LivePageWorker(self.client, sort_type='newest', size=500, offset=0, query=query)
            worker.page_ready.connect(self._on_search_loaded)
            worker.start()
            self.live_worker = worker
            return

        if getattr(self, 'current_sort', 'latest') == 'compilations':
            self.show_loading()
            if hasattr(self, '_compilations_worker') and self._compilations_worker.isRunning():
                self._compilations_worker.is_cancelled = True
            self._compilations_worker = CompilationsWorker(self.client)
            self._compilations_worker.results_ready.connect(self._on_compilations_loaded)
            self._compilations_worker.start()
            return

        if not hasattr(self, 'true_server_count') or self.true_server_count == 0:
            api_sort = 'newest'
            current_sort = getattr(self, 'current_sort', 'latest')
            if current_sort == 'alphabetical': api_sort = 'alphabeticalByName'
            elif current_sort == 'random': api_sort = 'random'
            elif current_sort == 'favorites': api_sort = 'starred'
            elif current_sort == 'song_count': api_sort = 'song_count'
            worker = LivePageWorker(self.client, sort_type=api_sort, size=1, offset=0, query="")
            worker.page_ready.connect(self._on_initial_count_loaded)
            worker.start()
            self.live_worker = worker
            return

        if self.true_server_count > 0:
            self.set_status_text(f"{self.true_server_count:,} albums".replace(",", " "))
            pending = getattr(self, '_pending_cached_chunk', None)
            _is_random = getattr(self, 'current_sort', 'latest') == 'random'
            if pending:
                self.loaded_chunks.add(0)
            initial = min(50, self.true_server_count)
            placeholders = [{'type': 'placeholder', 'title': ''} for _ in range(initial)]
            self.album_model.clear()
            self.album_model.append_albums(placeholders)
            if pending:
                _cached = pending
                self._pending_cached_chunk = None
                # Defer chunk injection one frame so skeleton renders first, then real data fills in
                from PyQt6.QtCore import QTimer as _QT
                _QT.singleShot(0, lambda: self._on_chunk_loaded(_cached, 0))
                # For random sort: fetch a fresh set in background and cache it for
                # next visit without replacing the current view (next-session refresh)
                if _is_random:
                    import threading as _t
                    def _bg_refresh_random():
                        try:
                            fresh = self.client.get_album_list_sorted('random', size=50, offset=0)
                            if fresh and self.client:
                                self.client.stale_cache_set('albums_chunk_0_random', fresh)
                        except Exception:
                            pass
                    _t.Thread(target=_bg_refresh_random, daemon=True).start()
            self.check_viewport_qml(0, 49)

    def fetch_chunk(self, chunk_index):
        """Fires a background worker and returns it so we can cancel it if needed."""
        api_sort = 'newest'
        current_sort = getattr(self, 'current_sort', 'latest')
        if current_sort == 'alphabetical': api_sort = 'alphabeticalByName'
        elif current_sort == 'random': api_sort = 'random'
        elif current_sort == 'favorites': api_sort = 'starred'
        elif current_sort == 'song_count': api_sort = 'song_count'
        
        query = getattr(self, 'current_query', '')
        
        # Check the UI's Ascending/Descending state!
        is_ascending = self.sort_states.get(getattr(self, 'current_sort', 'latest'), True)
        
        offset = chunk_index * 50
        size = 50
        
        # If descending, we have to read chunks from the END of the server's list backwards!
        # This hack is only for getAlbumList2, which doesn't support descending order.
        # Native API calls handle sorting direction via the 'order' parameter.
        if not is_ascending and getattr(self, 'true_server_count', 0) > 0 and api_sort != 'song_count':
            true_start = self.true_server_count - (chunk_index * 50) - 50
            if true_start < 0:
                size = 50 + true_start  # Shrink the size if we hit the very beginning of the list
                true_start = 0
            offset = true_start
            
        worker = LivePageWorker(
            self.client,
            sort_type=api_sort,
            size=size,
            offset=offset,
            query=query,
            reverse_list=(not is_ascending)
        )
        worker.page_ready.connect(lambda albums, total: self._on_chunk_loaded(albums, chunk_index))
        worker.start()
        return worker

    def filter_grid(self, text):
        self.current_query = ""
        self.grid_bridge.search.reset()
        self.load_albums_page(reset=True)

    def set_client(self, client):
        self.client = client
        if self.client:
            if self.cover_worker:
                self.cover_worker.stop()
                self.cover_worker.cover_ready.disconnect()
                self.cover_worker.quit()
                self.cover_worker.wait(500)
            self.cover_worker = GridCoverWorker(client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()

            # Stale-while-revalidate: show cached count+chunk instantly, refresh behind
            _sort = getattr(self, 'current_sort', 'latest')
            cached_count = client.stale_cache_get('albums_count')
            cached_chunk = client.stale_cache_get(f'albums_chunk_0_{_sort}')
            if cached_count and isinstance(cached_count, int) and cached_count > 0:
                self.true_server_count = cached_count
                self.total_items = cached_count   # prevent descending-sort reload in update_server_count_ui
                self._stale_count_set = True
            else:
                self.true_server_count = 0
                self._stale_count_set = False
            self._pending_cached_chunk = cached_chunk or None

            self.count_worker = ServerCountWorker(client)
            self.count_worker.count_ready.connect(self.update_server_count_ui)
            self.count_worker.start()

            self.refresh_grid()
            
        if hasattr(self, 'detail_view'):
            self.detail_view.client = client

    def update_server_count_ui(self, true_server_count):
        """Updates the status label with the instant server count."""
        self.true_server_count = true_server_count
        if true_server_count and self.client:
            self.client.stale_cache_set('albums_count', true_server_count)
        
        # Only update the base label if we are NOT currently searching
        if not getattr(self, 'current_query', ''):
            self.set_status_text(f"{self.true_server_count:,} albums".replace(",", " "))
            
        # If we are in descending mode but had 0 items when we loaded, trigger a reload now that we have the count!
        is_ascending = self.sort_states.get(getattr(self, 'current_sort', 'latest'), True)
        if not is_ascending and getattr(self, 'total_items', 0) == 0:
            self.total_items = self.true_server_count
            self.load_albums_page(reset=True)
    
    def show_loading(self):
        """Instant visual feedback — show animated skeleton grid before data arrives."""
        self.album_model.clear()
        # Empty title triggers SkeletonCard (animated) in the QML grid
        self.album_model.append_albums(
            [{'type': 'placeholder', 'title': '', 'cover_id': ''} for _ in range(20)]
        )
        self.set_status_text("Loading...")

    def refresh_grid(self):
        # 1. Reset the server count unless stale cache provided it
        if not getattr(self, '_stale_count_set', False):
            self.true_server_count = 0
        self._stale_count_set = False

        # 2. Brutally wipe the API cache so we don't just reload the old albums from memory!
        if hasattr(self, 'client') and self.client and hasattr(self.client, '_api_cache'):
            keys_to_delete = [k for k in self.client._api_cache.cache.keys() if 'albums_' in str(k)]
            for k in keys_to_delete:
                del self.client._api_cache.cache[k]

        self.load_albums_page(reset=True)

    def go_to_root(self):
        self.stack.setCurrentIndex(0)
        
        # Force the OS to send keyboard strokes to the QML grid!
        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()
        
        # Only filter if a search was active!
        if self.current_query != "":
            self.filter_grid("")
            
        self.nav_history = [{'type': 'root'}]
        self.nav_index = 0

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()
            
        # Instantly check if the server has new files when the tab is opened!
        self.check_for_server_updates()

    def check_for_server_updates(self):
        """Silently checks if Navidrome has new files, and auto-refreshes if it does."""
        if not getattr(self, 'client', None): return
        
        import time
        now = time.time()
        # Debounce to prevent spamming the server if the user flicks between tabs rapidly
        if hasattr(self, 'last_update_check') and (now - self.last_update_check < 5):
            return
        self.last_update_check = now
        
        import threading
        def _check():
            try:
                # Ask Navidrome for its current database revision timestamp
                current_scan = self.client.get_server_scan_status()
                
                # Establish the baseline on the first run
                if not hasattr(self, 'last_known_scan_status'):
                    self.last_known_scan_status = current_scan
                    return
                    
                # If the server timestamp changed, it means you added/modified files!
                if current_scan != self.last_known_scan_status and current_scan != 0:
                    print(f"[LibraryGrid] Server changes detected! Auto-refreshing grid...")
                    self.last_known_scan_status = current_scan
                    
                    # Safely tell the main UI thread to refresh the grid
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, self.refresh_grid)
            except Exception as e:
                pass
                
        # Run the check in the background so the UI never lags when switching tabs
        threading.Thread(target=_check, daemon=True).start()
    
    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.cancelScroll.emit()

    def get_state(self):
        """Returns the current state for saving."""
        return {
            'sort': getattr(self, 'current_sort', 'latest'),
            'sort_states': getattr(self, 'sort_states', {}),
        }

    def restore_state(self, state):
        """Applies a saved state before the first load."""
        if not state: return
        
        # Restore the actual variables the UI uses!
        self.current_sort = state.get('sort', 'latest')
        
        saved_sorts = state.get('sort_states', {})
        for k, v in saved_sorts.items():
            self.sort_states[k] = v
            
        self.current_query = state.get('query', '')

        # Update the search bar UI silently
        if hasattr(self, 'grid_bridge') and self.current_query:
            self.grid_bridge.search.restore(self.current_query)


        # Update the sort icon in the burger menu
        if hasattr(self, 'update_burger_icon'):
            self.update_burger_icon()
            
        # Tell the next load cycle to respect our restored data
        self._restored_state_waiting = True
