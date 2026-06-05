import time
import os
from player.mixins.visuals import scrollbar_css, install_scroll_reveal, menu_hover, apply_menu_palette, resolve_menu_hover, SmoothScroller
import sys
import random
import re
import math
import json


from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQml import QQmlContext
from PyQt6.QtQuick import QQuickImageProvider
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
                             QListWidgetItem, QPushButton, QLabel, 
                             QStackedWidget, QStyle, QStyledItemDelegate, QApplication,
                             QTreeWidget, QTreeWidgetItem, QHeaderView, QFrame, QSizePolicy,
                             QMenu, QStyleOptionViewItem, QAbstractItemView,
                             QLineEdit, QToolButton, QScrollArea) 

from PyQt6.QtCore import (Qt, QSize, pyqtSignal, QThread, QRect, QPoint, QTimer,
                          QEvent, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QAbstractListModel, QModelIndex, QByteArray, pyqtSlot, QObject, QUrl, Qt, QVariantAnimation, QSettings)
from PyQt6.QtGui import (QIcon, QPixmap, QPainter, QColor, QFontMetrics,
                         QBrush, QPen, QPolygon, QPainterPath, QCursor, QFont, QAction,
                         QTextDocument, QAbstractTextDocumentLayout, QPalette)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

from components import PaginationFooter, SmartSearchContainer
from tracks_browser import TracksBrowser, MiddleClickScroller
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import os as _os
_COVER_WORKERS = min(6, (_os.cpu_count() or 2) + 2)

_ARTIST_SEP_RE = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. )')

def _split_artist(artist: str):
    return [(p, bool(_ARTIST_SEP_RE.match(p))) for p in _ARTIST_SEP_RE.split(artist) if p]


def _square_cover(pix: QPixmap, size: int = 220) -> QPixmap:
    """Scale-to-fill and centre-crop to an exact square, then round corners."""
    if pix.isNull():
        return pix
    scaled = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    x = (scaled.width()  - size) // 2
    y = (scaled.height() - size) // 2
    return _round_pixmap(scaled.copy(x, y, size, size))


def _round_pixmap(pix: QPixmap, radius: int = 12) -> QPixmap:
    """Return a copy of pix with rounded corners clipped into the image."""
    if pix.isNull():
        return pix
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pix.width(), pix.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


class GridBridge(QObject):
    itemClicked = pyqtSignal(dict)
    playClicked = pyqtSignal(dict)
    artistClicked = pyqtSignal(dict)
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
    cancelScroll = pyqtSignal()
    scrollBy = pyqtSignal(float)
    indexChanged = pyqtSignal(int)
    requestFocusNext = pyqtSignal()
    requestFocusPrev = pyqtSignal()
    takeFocus = pyqtSignal()
    
    def __init__(self, album_model):
        super().__init__()
        self.album_model = album_model
        
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

    @pyqtSlot(int)
    def emitArtistClicked(self, idx):
        if 0 <= idx < len(self.album_model.albums):
            self.artistClicked.emit(self.album_model.albums[idx])

    @pyqtSlot(str, str)
    def emitArtistNameClicked(self, name, artist_id=""):
        self.artistNameClicked.emit(name, artist_id)

    @pyqtSlot(int)
    def emitIndexChanged(self, idx):
        self.indexChanged.emit(idx)

    @pyqtSlot()
    def emitRequestFocusNext(self):
        self.requestFocusNext.emit()

    @pyqtSlot()
    def emitRequestFocusPrev(self):
        self.requestFocusPrev.emit()

class CoverImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.image_cache = {} 
        
    def requestImage(self, id, requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import Qt, QRectF
        
        real_id = id.split("?t=")[0] 
        data = self.image_cache.get(real_id)
        
        
        size = 250
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        
        if data: 
            source = QImage()
            source.loadFromData(data)
            if not source.isNull():
                source = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                
                # Slice the corners off the QImage perfectly!
                painter = QPainter(img)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
                
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()
                
        return img, img.size()

class AlbumModel(QAbstractListModel):
    TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
    ARTIST_ROLE = Qt.ItemDataRole.UserRole + 2
    YEAR_ROLE = Qt.ItemDataRole.UserRole + 3
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 4
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 5
    IS_LOADING_ROLE = Qt.ItemDataRole.UserRole + 6
    SONG_COUNT_ROLE = Qt.ItemDataRole.UserRole + 7
    ARTIST_ID_ROLE = Qt.ItemDataRole.UserRole + 8

    def __init__(self):
        super().__init__()
        self.albums = []

    def rowCount(self, parent=QModelIndex()): return len(self.albums)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.albums[index.row()]
        if role == self.TITLE_ROLE:
            if a.get('type') == 'placeholder': return ''
            return a.get('title') or a.get('name') or 'Unknown'
        if role == self.ARTIST_ROLE: return a.get('artist') or a.get('albumArtist') or ''
        if role == self.YEAR_ROLE: return str(a.get('year') or a.get('minYear') or a.get('maxYear') or '').replace('None', '')
        if role == self.COVER_ID_ROLE: return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return a
        if role == self.IS_LOADING_ROLE: return a.get('type') == 'placeholder'
        if role == self.SONG_COUNT_ROLE:
            n = a.get('songCount') or a.get('trackCount') or ''
            return f"{n} tracks" if n else ''
        if role == self.ARTIST_ID_ROLE:
            return a.get('artistId') or a.get('albumArtistId') or ''
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE: b"albumTitle", self.ARTIST_ROLE: b"albumArtist",
            self.YEAR_ROLE: b"albumYear", self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData", self.IS_LOADING_ROLE: b"isLoading",
            self.SONG_COUNT_ROLE: b"albumSongCount",
            self.ARTIST_ID_ROLE: b"albumArtistId",
        }
        
    def append_albums(self, new_albums):
        start = len(self.albums)
        self.beginInsertRows(QModelIndex(), start, start + len(new_albums) - 1)
        self.albums.extend(new_albums)
        self.endInsertRows()
        
    def clear(self):
        self.beginResetModel()
        self.albums = []
        self.endResetModel()
        
    def update_cover(self, cover_id):
        import time
        forced_id = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self.albums):
            if a.get('cover_id') == cover_id:
                a['coverId_forced'] = forced_id 
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class LiveAlbumWorker(QThread):
    results_ready = pyqtSignal(list, int, int, int)

    def __init__(self, client, sort_type, page, page_size, search_query=""):
        super().__init__()
        self.client = client
        self.sort_type = sort_type
        self.page = page
        self.page_size = page_size
        self.search_query = search_query
        self.is_cancelled = False

    def run(self):
        try:
            if not self.client: return
            
            offset = (self.page - 1) * self.page_size

            if self.search_query:
                albums, total_items = self.client.search_albums(self.search_query, count=self.page_size, offset=offset)
                if total_items is None:
                    total_items = len(albums)
                total_pages = 1 if len(albums) < self.page_size else self.page + 1
            
            else:
                albums, total_items = self.client.get_albums_live(
                    sort_type=self.sort_type, 
                    size=self.page_size, 
                    offset=offset
                )
                
                if total_items:
                    total_pages = max(1, math.ceil(total_items / self.page_size))
                else:

                    total_items = 0
                    total_pages = 1

            if not self.is_cancelled:
                self.results_ready.emit(albums, total_items, total_pages, self.page)

        except Exception as e:
            print(f"[LiveAlbumWorker] Error: {e}")
            if not self.is_cancelled:
                self.results_ready.emit([], 0, 1, 1)

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

class GridCoverWorker(QThread):
    """
    Downloads thumb-size (300 px) cover art for grid/list/row display.
    Uses CoverCache for all disk I/O — no direct file access here.
    Emits cover_ready(cover_id, raw_bytes) on the main thread.
    """
    cover_ready = pyqtSignal(str, bytes)

    def __init__(self, client):
        super().__init__()
        self.client  = client
        self.queue   = []
        self.running = True
        self._abort_requested  = False
        self._urgent_in_flight = set()

        from cover_cache import CoverCache
        self._cache  = CoverCache.instance()

    def queue_cover(self, cover_id, priority=False):
        cid = str(cover_id)


        resolved = getattr(self, '_cover_aliases', {}).get(cid, cid)

        
        data = self._cache.get_thumb(resolved)
        if data:
            self.cover_ready.emit(cid, data)
            return
        if resolved != cid:
            data = self._cache.get_thumb(cid)
            if data:
                self.cover_ready.emit(cid, data)
                return

        
        if cid not in self.queue:
            if priority:
                self.queue.insert(0, cid)
            else:
                self.queue.append(cid)

    def _download_task(self, cover_id):

        cid = str(cover_id)

        # 1. Disk cache (instant)
        data = self._cache.get_thumb(cid)
        if data:
            return cid, data

        # 2. Network download
        try:
            from cover_cache import THUMB_SIZE
            data = self.client.get_cover_art(cid, size=THUMB_SIZE)
            if data:
                self._cache.save_thumb(cid, data)

                if not hasattr(self, '_cover_aliases'):
                    self._cover_aliases = {}
                if cid.startswith('ar-'):
                    bare = cid[3:]
                    self._cover_aliases[bare] = cid
                    self._cache.save_thumb(bare, data)
                else:
                    ar_id = f'ar-{cid}'
                    self._cover_aliases[ar_id] = cid
                    self._cache.save_thumb(ar_id, data)

                return cid, data
        except Exception:
            pass

        return cid, None

    def abort_current_batch(self):
        """Clear the queue and cancel any not-yet-running futures on the next loop tick."""
        self.queue.clear()
        self._abort_requested = True

    def load_urgent(self, cover_ids):
        """Move visible covers to the front of the download queue for priority processing."""
        for cid in reversed(cover_ids):
            cid = str(cid)
            data = self._cache.get_thumb(cid)
            if data:
                self.cover_ready.emit(cid, data)
                continue
            if cid in self.queue:
                self.queue.remove(cid)
            self.queue.insert(0, cid)

    def queue_batch(self, cover_ids, priority=False):
        """Queue covers without any main-thread disk reads — worker handles cache checks."""
        for cid in (reversed(cover_ids) if priority else cover_ids):
            cid = str(cid)
            if cid not in self.queue:
                if priority:
                    self.queue.insert(0, cid)
                else:
                    self.queue.append(cid)

    def run(self):
        with ThreadPoolExecutor(max_workers=_COVER_WORKERS) as executor:
            futures = []

            while self.running:
                try:
                    if getattr(self, '_abort_requested', False):
                        self._abort_requested = False
                        for f in futures:
                            f.cancel()
                        futures = [f for f in futures if not f.done() and not f.cancelled()]

                    while self.queue and len(futures) < _COVER_WORKERS:
                        cover_id = str(self.queue.pop(0))

                        data = self._cache.get_thumb(cover_id)
                        if data:
                            self.cover_ready.emit(cover_id, data)
                        else:
                            futures.append(executor.submit(self._download_task, cover_id))

                    if futures:
                        done, not_done = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                        for future in done:
                            try:
                                res_id, data = future.result()
                                if data:
                                    self.cover_ready.emit(res_id, data)
                            except Exception:
                                pass
                        futures = list(not_done)
                    else:
                        time.sleep(0.1)
                except Exception as e:
                    print(f"GridWorker loop error safely caught: {e}")
                    time.sleep(0.5)

    def stop(self):
        self.running = False

class GridItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#1db954")
        self.hovered_artist_row = -1
        self.clickable_artist = True   # set False to disable subtitle hover/underline
        self.show_play_btn   = True    # set False to hide the hover play button

        # Animation state
        self._hovered_row   = -1
        self._hover_progress = 0.0   # 0.0 = not hovered, 1.0 = fully hovered
        self._play_progress  = 0.0   # 0.0 = play btn not hovered, 1.0 = fully hovered

        self._hover_anim = QVariantAnimation()
        self._hover_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._hover_anim.valueChanged.connect(self._on_hover_value)

        self._play_anim = QVariantAnimation()
        self._play_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._play_anim.valueChanged.connect(self._on_play_value)

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent()
        w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)

    def _primary_px(self):
        t = self._theme()
        return getattr(t, 'font_size_primary', 14) if t else 14

    def _secondary_px(self):
        t = self._theme()
        return getattr(t, 'font_size_secondary', 12) if t else 12

    def _primary_color(self):
        t = self._theme()
        return getattr(t, 'font_color_primary', '#eeeeee') if t else '#eeeeee'

    def _secondary_color(self):
        t = self._theme()
        return getattr(t, 'font_color_secondary', '#cccccc') if t else '#cccccc'

    def set_hovered_artist_row(self, row):
        self.hovered_artist_row = row

    # ── Animation helpers ─────────────────────────────────────────────────

    def _on_hover_value(self, value):
        self._hover_progress = value
        self._request_repaint()

    def _on_play_value(self, value):
        self._play_progress = value
        self._request_repaint()

    def _request_repaint(self):
        lw = self.parent()
        if lw and hasattr(lw, 'viewport'):
            lw.viewport().update()

    def _animate(self, anim, current, target, full_duration=150):
        anim.stop()
        distance = abs(target - current)
        if distance < 0.001:
            return
        anim.setDuration(max(1, int(full_duration * distance)))
        anim.setStartValue(float(current))
        anim.setEndValue(float(target))
        anim.start()

    def set_hovered_row(self, row):
        if row == self._hovered_row:
            return
        self._hovered_row = row
        if row >= 0:
            # Snap any leftover progress from a previous item to 0 instantly,
            # then animate the new item in from 0.
            self._hover_progress = 0.0
            self._play_progress  = 0.0
            self._play_anim.stop()
            self._animate(self._hover_anim, 0.0, 1.0)
        else:
            self._animate(self._hover_anim, self._hover_progress, 0.0)
            self._animate(self._play_anim,  self._play_progress,  0.0)

    def set_play_hovered(self, is_hovered):
        target = 1.0 if is_hovered else 0.0
        self._animate(self._play_anim, self._play_progress, target)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        icon_width  = rect.width() - 12
        icon_height = icon_width
        icon_x      = rect.x() + 6
        icon_rect   = QRect(icon_x, rect.y() + 4, icon_width, icon_height)

        path = QPainterPath()
        path.addRoundedRect(icon_rect.x(), icon_rect.y(), icon_rect.width(), icon_rect.height(), 10, 10)

        # ── Cover image ───────────────────────────────────────────────────
        painter.save()
        painter.setClipPath(path)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pix = icon.pixmap(icon_width, icon_height)
            px = icon_rect.x() + (icon_width - pix.width()) // 2
            py = icon_rect.y() + (icon_height - pix.height()) // 2
            painter.drawPixmap(px, py, pix)

        # Determine animation progress for this item
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_this_hovered = (index.row() == self._hovered_row)
        hover_p = self._hover_progress if is_this_hovered else (1.0 if is_selected else 0.0)
        play_p  = self._play_progress  if is_this_hovered else 0.0

        # Dark overlay: opacity 0 → 0.4  (QML: color "#000", opacity 0→0.4)
        if hover_p > 0:
            painter.setBrush(QColor(0, 0, 0, int(hover_p * 102)))
            painter.setPen(QPen(self.master_color, 2))
            painter.drawPath(path)
        painter.restore()  # remove clip

        # ── Play button: scale 0.8→1.0, opacity 0→0.8 (+0.2 when on btn) ─
        if hover_p > 0 and self.show_play_btn:
            center    = icon_rect.center()
            play_size = min(60, icon_width // 2)
            scale     = 0.8 + play_p * 0.2          # matches QML scale behaviour
            scaled_sz = max(4, int(play_size * scale))
            play_rect = QRect(0, 0, scaled_sz, scaled_sz)
            play_rect.moveCenter(center)

            play_opacity = hover_p * 0.8 + play_p * 0.2   # 0→0.8, then 0.8→1.0
            painter.setOpacity(play_opacity)
            painter.setBrush(self.master_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(play_rect)

            tri_size = scaled_sz // 3
            cx, cy = center.x(), center.y()
            p1 = QPoint(cx - tri_size // 3, cy - tri_size // 2)
            p2 = QPoint(cx - tri_size // 3, cy + tri_size // 2)
            p3 = QPoint(cx + tri_size // 2 + 2, cy)
            painter.setBrush(QColor("#111111"))
            painter.drawPolygon(QPolygon([p1, p2, p3]))
            painter.setOpacity(1.0)

        # ── Text ──────────────────────────────────────────────────────────
        data = index.data(Qt.ItemDataRole.UserRole)
        if data:
            title  = data.get('title') or data.get('name') or "Unknown"
            artist = data.get('artist', '')
            year   = str(data.get('year', ''))

            text_width = rect.width() - 20
            text_x     = rect.x() + 10
            current_y  = icon_rect.bottom() + 10

            # Title: interpolate primary color → accent on hover
            mc  = self.master_color
            pc  = QColor(self._primary_color())
            r   = int(pc.red()   + (mc.red()   - pc.red())   * hover_p)
            g   = int(pc.green() + (mc.green() - pc.green()) * hover_p)
            b   = int(pc.blue()  + (mc.blue()  - pc.blue())  * hover_p)
            painter.setPen(QColor(r, g, b))
            font = painter.font(); font.setBold(True); font.setPixelSize(self._primary_px()); painter.setFont(font)
            fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()),
                             Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(title, Qt.TextElideMode.ElideRight, text_width))

            current_y += fm.height() + 2
            font.setBold(False); font.setPixelSize(self._secondary_px())
            artist_hovered = self.clickable_artist and (index.row() == self.hovered_artist_row)
            if artist_hovered:
                font.setUnderline(True)
                painter.setPen(QColor(self.master_color))
            else:
                font.setUnderline(False)
                painter.setPen(QColor(self._secondary_color()))
            painter.setFont(font); fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()),
                             Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(artist, Qt.TextElideMode.ElideRight, text_width))
            font.setUnderline(False); painter.setFont(font)

            current_y += fm.height() + 2
            painter.setPen(QColor(self._secondary_color()))
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()), Qt.AlignmentFlag.AlignLeft, fm.elidedText(year, Qt.TextElideMode.ElideRight, text_width))

        painter.restore()

_DETAIL_ARTIST_SEP_RE = re.compile(
    r'( /// | • | / |,\s+| feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. )'
)

_GENRE_SEP_RE = re.compile(r' /// | • | / |,\s*|;\s*')

def _split_genres(text):
    """Return list of (text, is_sep) pairs, splitting on bullet/comma/semicolon/slash."""
    parts = [p.strip() for p in _GENRE_SEP_RE.split(text.strip()) if p.strip()]
    result = []
    for i, p in enumerate(parts):
        result.append((p, False))
        if i < len(parts) - 1:
            result.append((' • ', True))
    return result

class ClickableArtistLabel(QWidget):
    artist_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts = []       # list of (text, is_separator)
        self.artist_rects = [] # list of (text, QRect) for non-separators only
        self.hovered_artist = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(20)
        self.setMaximumHeight(30)

    def setText(self, text):
        if not text or text == "Loading...":
            self._parts = [(text, False)] if text else []
        else:
            self._parts = [
                (p, bool(_DETAIL_ARTIST_SEP_RE.match(p)))
                for p in _DETAIL_ARTIST_SEP_RE.split(text) if p
            ]
        self.artist_rects = []
        self.hovered_artist = None
        self.updateGeometry()
        self.update()

    def text(self):
        return "".join(p for p, _ in self._parts)

    def _primary_px(self) -> int:
        theme = getattr(self.window(), 'theme', None)
        return getattr(theme, 'font_size_primary', 14) if theme else 14

    def sizeHint(self):
        font = QFont()
        font.setPixelSize(self._primary_px())
        font.setBold(True)
        fm = QFontMetrics(font)
        text = self.text()
        return QSize(fm.horizontalAdvance(text) if text else 100, fm.height() + 4)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setPixelSize(self._primary_px())
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        x = 0
        y = fm.ascent() + 2
        artist_color = QColor(getattr(self, '_color', '#cccccc'))
        self.artist_rects = []
        for text, is_sep in self._parts:
            is_hovered = (not is_sep and self.hovered_artist == text)
            font.setUnderline(is_hovered)
            painter.setFont(font)
            painter.setPen(QColor("#777777") if is_sep else artist_color)
            w = fm.horizontalAdvance(text)
            if not is_sep:
                self.artist_rects.append((text, QRect(x, 0, w, self.height())))
            painter.drawText(x, y, text)
            x += w
        painter.end()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        old_hovered = self.hovered_artist
        self.hovered_artist = None
        for artist, rect in self.artist_rects:
            if rect.contains(pos):
                self.hovered_artist = artist
                break
        if old_hovered != self.hovered_artist:
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for artist, rect in self.artist_rects:
                if rect.contains(event.pos()):
                    self.artist_clicked.emit(artist)
                    break

    def leaveEvent(self, event):
        if self.hovered_artist is not None:
            self.hovered_artist = None
            self.update()

class _TrackHeader(QHeaderView):
    _FLEX_COL   = 1   # TITLE — Stretch, absorbs space
    _FLEX_NEXT  = 2   # ARTIST — resized when TITLE's right boundary is dragged
    _HANDLE_PX  = 5   # hit-test tolerance in pixels

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._accent = QColor('#555555')
        self._left_drag = False
        self._left_start_x = 0
        self._left_start_w = 0

    def set_accent(self, color: str):
        self._accent = QColor(color)
        self.update()

    def _theme(self):
        w = self.window() if hasattr(self, 'window') else None
        return getattr(w, 'theme', None)
    def _secondary_px(self):
        t = self._theme(); return getattr(t, 'font_size_secondary', 12) if t else 12
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#555555') if t else '#555555'
    def _border_qcolor(self):
        t = self._theme()
        if t is None:
            return QColor('#2a2a2a')
        if getattr(t, 'auto_border_from_accent', True):
            return QColor(getattr(t, 'accent', '#cccccc')).darker(250)
        return QColor(getattr(t, 'manual_border_color', '#2a2a2a'))

    def _flex_boundary_x(self):
        return self.sectionViewportPosition(self._FLEX_COL) + self.sectionSize(self._FLEX_COL)

    def _near_flex_boundary(self, x):
        return abs(x - self._flex_boundary_x()) <= self._HANDLE_PX

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._near_flex_boundary(event.pos().x()):
            self._left_drag = True
            self._left_start_x = event.pos().x()
            self._left_start_w = self.sectionSize(self._FLEX_NEXT)
            self.setCursor(Qt.CursorShape.SplitHCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._left_drag:
            delta = event.pos().x() - self._left_start_x
            # drag right → ARTIST shrinks (direction = -1, matching psysonic)
            new_w = max(80, self._left_start_w - delta)
            self.resizeSection(self._FLEX_NEXT, new_w)
            event.accept()
            return
        if self._near_flex_boundary(event.pos().x()):
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._left_drag:
            self._left_drag = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, Qt.GlobalColor.transparent)

        text = self.model().headerData(logical_index, Qt.Orientation.Horizontal) or ''
        f = QFont(); f.setPixelSize(self._secondary_px()); f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(self._secondary_color()))
        h_align = Qt.AlignmentFlag.AlignHCenter if logical_index in (0, 3, 5) else Qt.AlignmentFlag.AlignLeft
        painter.drawText(rect.adjusted(4, 0, -4, -8),
                         h_align | Qt.AlignmentFlag.AlignBottom, text)

        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        if logical_index > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border_qcolor(), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(rect.right(), rect.top() - 5, rect.right(), rect.bottom() - 8)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.restore()


class _TrackListDelegate(QStyledItemDelegate):
    """Adds 8px top gap on row 0; draws hover/selection/playing backgrounds manually."""
    def __init__(self, parent=None, heart_col=3):
        super().__init__(parent)
        self._heart_col = heart_col   # column index that shows ♥/♡ (-1 = none)
        self.playing_row = -1
        self.accent = QColor('#cccccc')
        self._active_hover = QColor(204, 204, 204, 45)  # updated via set_active_hover
        self.font_size = 14
        self._movie = None
        self._is_playing = False
        self._hover_artist = None   # (row, part_text)
        self._hover_genre  = None   # (row, genre_text)
        self._heart_filled_pix = QPixmap()
        self._heart_empty_pix  = QPixmap()
        self._kbd_row = -1
        self.max_genres = -1   # -1 = no limit

    def set_font_size(self, size: int):
        self.font_size = size

    def _primary_px(self) -> int:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_size_primary', self.font_size) if _theme else self.font_size

    def _secondary_px(self) -> int:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_size_secondary', max(self.font_size - 2, 10)) if _theme else max(self.font_size - 2, 10)

    def _primary_color(self) -> str:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_color_primary', '#dddddd') if _theme else '#dddddd'

    def _secondary_color(self) -> str:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_color_secondary', '#aaaaaa') if _theme else '#aaaaaa'

    def _theme(self):
        _p = self.parent()
        return getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None

    def _hover_qcolor(self) -> QColor:
        return QColor(resolve_menu_hover(self._theme()))

    def set_movie(self, movie):
        self._movie = movie

    def set_heart_pixmaps(self, filled: QPixmap, empty: QPixmap):
        self._heart_filled_pix = filled
        self._heart_empty_pix  = empty

    def set_active_hover(self, color: 'QColor'):
        self._active_hover = color
        if self.parent():
            self.parent().viewport().update()

    def set_playing(self, row: int, accent: str, is_playing: bool = True):
        self.playing_row = row
        self._is_playing = is_playing
        self.accent = QColor(accent)
        if self.parent():
            self.parent().viewport().update()

    def sizeHint(self, option, index):
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        from PyQt6.QtWidgets import QStyle
        draw_rect = option.rect
        is_playing_row = (index.row() == self.playing_row)

        # Disc separator rows — draw label in col 1 only, skip everything else
        user_data = index.sibling(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(user_data, dict) and user_data.get('_is_disc_header'):
            if index.column() == 1:
                text = index.data() or ''
                view = option.widget
                full_w = view.viewport().width() if view else draw_rect.width()
                span_rect = draw_rect.__class__(draw_rect.left(), draw_rect.y(), full_w - draw_rect.left(), draw_rect.height())
                f = QFont()
                f.setPixelSize(self._secondary_px())
                f.setBold(True)
                fm = QFontMetrics(f)
                painter.save()
                painter.setFont(f)
                painter.setPen(QColor(self._secondary_color()))
                painter.drawText(span_rect.left() + 4, span_rect.center().y() + fm.ascent() // 2, text)
                painter.restore()
            return

        # Draw background once per row (col 0 only) spanning full width
        if index.column() == 0:
            is_kbd = (index.row() == self._kbd_row)
            if option.state & QStyle.StateFlag.State_MouseOver:
                color = self._hover_qcolor()
            elif is_kbd:
                color = self._active_hover
            else:
                color = None
            if color:
                view = option.widget
                full_w = view.viewport().width() if view else option.rect.width()
                row_rect = option.rect.__class__(0, option.rect.y(), full_w, option.rect.height())
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(row_rect, 6, 6)
                painter.restore()

            # Draw playing GIF instead of track number
            if is_playing_row and self._movie and self._is_playing:
                pix = self._movie.currentPixmap()
                if not pix.isNull():
                    px = draw_rect.left() + (draw_rect.width() - pix.width()) // 2
                    py = draw_rect.top()  + (draw_rect.height() - pix.height()) // 2
                    painter.drawPixmap(px, py, pix)
                return  # skip text rendering for col 0 on playing row

        # Col 3 — draw heart pixmap centered
        if self._heart_col >= 0 and index.column() == self._heart_col:
            is_fav = index.data() == '♥'
            pix = self._heart_filled_pix if is_fav else self._heart_empty_pix
            if not pix.isNull():
                px = draw_rect.center().x() - pix.width() // 2
                py = draw_rect.center().y() - pix.height() // 2
                painter.drawPixmap(px, py, pix)
            return

        # Col 2 — draw artist parts manually so we can underline on hover
        if index.column() == 2:
            artist = index.data() or ''
            f = QFont()
            f.setPixelSize(self._secondary_px())
            fm = QFontMetrics(f)
            ax = draw_rect.left() + 4
            ay = draw_rect.center().y()
            right_edge = draw_rect.right() - 4
            row = index.row()
            painter.save()
            painter.setFont(f)
            for part, is_sep in _split_artist(artist):
                pw = fm.horizontalAdvance(part)
                available = right_edge - ax
                if ax + pw > right_edge:
                    if available > 0:
                        elided = fm.elidedText(part, Qt.TextElideMode.ElideRight, available)
                        painter.setPen(QColor(120, 120, 120) if is_sep else (self.accent if row == self.playing_row else QColor(self._secondary_color())))
                        painter.drawText(ax, ay + fm.ascent() // 2, elided)
                    break
                hovered = (not is_sep and self._hover_artist == (row, part.strip()))
                if is_sep:
                    painter.setPen(QColor(120, 120, 120))
                else:
                    painter.setPen(self.accent if row == self.playing_row else QColor(self._secondary_color()))
                painter.drawText(ax, ay + fm.ascent() // 2, part)
                if hovered:
                    painter.drawLine(ax, ay + fm.ascent() // 2 + 2, ax + pw, ay + fm.ascent() // 2 + 2)
                ax += pw
            painter.restore()
            return

        # Col 1 — title at theme primary font size
        if index.column() == 1:
            text = index.data() or ''
            f = QFont()
            f.setPixelSize(self._primary_px())
            f.setBold(True)
            fm = QFontMetrics(f)
            painter.save()
            painter.setFont(f)
            painter.setPen(QColor(index.data(Qt.ItemDataRole.ForegroundRole)) if index.data(Qt.ItemDataRole.ForegroundRole) else QColor(self._primary_color()))
            x = draw_rect.left() + 4
            y = draw_rect.center().y() + fm.ascent() // 2
            painter.drawText(x, y, fm.elidedText(text, Qt.TextElideMode.ElideRight, draw_rect.width() - 8))
            painter.restore()
            return

        # Col 4 — genre, each part separately clickable with hover underline
        if index.column() == 4:
            genre_text = index.data() or ''
            if genre_text:
                f = QFont()
                f.setPixelSize(self._secondary_px())
                fm = QFontMetrics(f)
                ax = draw_rect.left() + 4
                ay = draw_rect.center().y()
                right_edge = draw_rect.right() - 4
                row = index.row()
                painter.save()
                painter.setFont(f)
                genre_count = 0
                for part, is_sep in _split_genres(genre_text):
                    if self.max_genres > 0 and genre_count >= self.max_genres:
                        break
                    if not is_sep:
                        genre_count += 1
                    pw = fm.horizontalAdvance(part)
                    available = right_edge - ax
                    if ax >= right_edge:
                        break
                    if ax + pw > right_edge and available > 0:
                        elided = fm.elidedText(part, Qt.TextElideMode.ElideRight, available)
                        painter.setPen(QColor(120, 120, 120) if is_sep else QColor(self._secondary_color()))
                        painter.drawText(ax, ay + fm.ascent() // 2, elided)
                        break
                    hovered = (not is_sep and self._hover_genre == (row, part))
                    painter.setPen(QColor(120, 120, 120) if is_sep else QColor(self._secondary_color()))
                    painter.drawText(ax, ay + fm.ascent() // 2, part)
                    if hovered:
                        painter.drawLine(ax, ay + fm.ascent() // 2 + 2, ax + pw, ay + fm.ascent() // 2 + 2)
                    ax += pw
                painter.restore()
                return

        # All other columns — strip hover/selected so super() doesn't re-draw background
        opt = option.__class__(option)
        opt.rect = draw_rect
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver & ~QStyle.StateFlag.State_Selected
        f = QFont(opt.font)
        f.setPixelSize(self._secondary_px())
        opt.font = f
        from PyQt6.QtGui import QPalette
        pal = QPalette(opt.palette)
        pal.setColor(QPalette.ColorRole.Text, QColor(self._secondary_color()))
        opt.palette = pal
        super().paint(painter, opt, index)


class AlbumDetailView(QWidget):
    play_clicked = pyqtSignal()
    shuffle_clicked = pyqtSignal()
    album_favorite_toggled = pyqtSignal(bool)
    artist_clicked = pyqtSignal(str)
    tracks_loaded = pyqtSignal()
    track_play_signal = pyqtSignal(list, int)
    track_artist_clicked = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)   # track_id, is_fav
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
        self.client = client
        self._last_playing_id = None
        self._last_is_playing = False
        self._last_playing_accent = '#888888'
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 0; }")

        from now_playing_info import _Card, _RoundedPixmapLabel

        # 1. MASTER LAYOUT
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ─── HEADER CARD ───────────────────────────────────────────────────────
        _header_wrapper = QWidget()
        _header_wrapper.setObjectName('_hw')
        _header_wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _header_wrapper.setStyleSheet('#_hw { background: transparent; }')
        _hw_lo = QVBoxLayout(_header_wrapper)
        _hw_lo.setContentsMargins(12, 12, 6, 0)
        _hw_lo.setSpacing(0)

        self.header_container = _Card()
        _hw_lo.addWidget(self.header_container)

        header_lo = QHBoxLayout(self.header_container)
        header_lo.setContentsMargins(28, 28, 28, 28)
        header_lo.setSpacing(28)

        self.cover_label = _RoundedPixmapLabel(264, 264, radius=10, show_glow=True, zoomable=True)
        header_lo.addWidget(self.cover_label)

        meta_container = QWidget()
        meta_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        meta_container.setStyleSheet('background: transparent;')
        meta_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header_lo.addWidget(meta_container, 1, Qt.AlignmentFlag.AlignTop)

        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(5)

        self.lbl_type = QLabel("")
        self.lbl_type.setFixedHeight(16)
        self.lbl_type.setStyleSheet("color: #ddd; font-weight: bold; font-size: 11px;")

        self.lbl_title = QLabel("Album Title")
        self.lbl_title.setStyleSheet("color: #dddddd; font-weight: bold; font-size: 32px; background: transparent; margin: 0; padding: 0;")
        self.lbl_title.setWordWrap(True)

        self.lbl_meta = QLabel("Loading...")
        self.lbl_meta.setStyleSheet("color: #aaa; font-weight: bold; font-size: 13px;")

        self.lbl_artist = ClickableArtistLabel()
        self.lbl_artist.artist_clicked.connect(self.artist_clicked.emit)
        self._meta_ready.connect(lambda artist, meta: (
            self.lbl_artist.setText(artist) if artist else None,
            self.lbl_meta.setText(meta)
        ))
        
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 15, 0, 0)
        btn_layout.setSpacing(2)
        
        # --- ALBUM VIEW PLAY BUTTON ---
        from player.widgets import PlayButton as _PlayButton
        self.btn_play = _PlayButton()
        self.btn_play.setFixedSize(60, 60)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setIcon(QIcon(resource_path("img/play.png")))  # tinted in set_accent_color
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.ensure_glow()
        self.btn_play.setToolTip("Play Album (Ctrl+Enter)")
        self.btn_play.clicked.connect(self.play_clicked.emit)
        
        _icon_btn_style = (
            'QPushButton { background: transparent; border: none; border-radius: 4px; }'
            ' QPushButton:hover { background: rgba(255, 255, 255, 0.1); }'
        )

        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setFlat(True)
        self.btn_shuffle.setFixedSize(36, 36)
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_shuffle.setStyleSheet(_icon_btn_style)
        self.btn_shuffle.setToolTip("Shuffle")
        self.btn_shuffle.clicked.connect(self.shuffle_clicked.emit)

        self.btn_like = QPushButton()
        self.btn_like.setFlat(True)
        self.btn_like.setFixedSize(36, 36)
        self.btn_like.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_like.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_like.setStyleSheet(_icon_btn_style)
        self.btn_like.setIcon(QIcon(self._make_heart_pix(resource_path('img/heart.png'), '#666666', size=22)))
        self.btn_like.setIconSize(QSize(22, 22))
        self.btn_like.setToolTip("Add to Favorites")
        self.btn_like.clicked.connect(self.toggle_header_heart)
        
        btn_layout.addWidget(self.btn_play)
        btn_layout.addWidget(self.btn_shuffle) 
        btn_layout.addWidget(self.btn_like)
        btn_layout.addStretch()
        
        meta_layout.addWidget(self.lbl_type)
        meta_layout.addWidget(self.lbl_title)
        meta_layout.addWidget(self.lbl_artist)
        meta_layout.addWidget(self.lbl_meta)
        meta_layout.addWidget(btn_row)
        
        # ─── TRACK LIST ─────────────────────────────────────────────────────────
        self.track_tree = QTreeWidget()
        self.track_tree.setColumnCount(8)
        self.track_tree.setHeaderLabels(['#', 'TITLE', 'ARTIST', 'FAVORITE', 'GENRE', 'DURATION', 'PLAYS', 'ALBUM'])
        self.track_tree.setRootIsDecorated(False)
        self.track_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.track_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._track_delegate = _TrackListDelegate(self.track_tree)
        from PyQt6.QtGui import QMovie
        from PyQt6.QtCore import QSize as _QSize
        self._playing_movie = QMovie(resource_path("img/playing.gif"))
        self._playing_movie.setScaledSize(_QSize(30, 30))
        self._playing_movie.frameChanged.connect(lambda: self.track_tree.viewport().update())
        self._track_delegate.set_movie(self._playing_movie)
        self._track_delegate.set_heart_pixmaps(
            self._make_heart_pix(resource_path("img/heart_filled.png"), "#E91E63"),
            self._make_heart_pix(resource_path("img/heart.png"), "#555555")
        )
        self.track_tree.setItemDelegate(self._track_delegate)
        self.track_tree.setStyleSheet("""
            QTreeWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QTreeWidget::item {
                height: 38px;
                border: none;
                padding-left: 4px;
            }
            QTreeWidget::item:hover { background: transparent; }
            QTreeWidget::item:selected { background: transparent; }
            QHeaderView { background: transparent; border: none; }
        """)
        self._track_header = _TrackHeader(self.track_tree)
        self.track_tree.setHeader(self._track_header)
        hdr = self._track_header
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 8):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 50)
        hdr.resizeSection(2, 180)
        hdr.resizeSection(3, 76)
        hdr.resizeSection(4, 130)
        hdr.resizeSection(5, 76)
        hdr.resizeSection(6, 60)
        hdr.resizeSection(7, 180)
        self.track_tree.setColumnHidden(7, True)   # Album hidden in album view
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(30)
        self._col_save_timer = QTimer(self)
        self._col_save_timer.setSingleShot(True)
        self._col_save_timer.setInterval(400)
        self._col_save_timer.timeout.connect(self._save_col_widths)
        self._restore_col_widths()
        hdr.sectionResized.connect(self._clamp_track_columns)
        self.track_tree.viewport().setAutoFillBackground(False)
        self.track_tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.track_tree.itemDoubleClicked.connect(self._on_track_double_clicked)
        self._tracks: list = []
        self._tracks_ready.connect(self._load_tracks)
        self._album_star_ready.connect(self.set_header_heart_state)
        self.track_tree.installEventFilter(self)
        self.track_tree.viewport().installEventFilter(self)
        self.track_tree.setMouseTracking(True)
        self.track_tree.viewport().setMouseTracking(True)

        _track_container = QWidget()
        _track_container.setStyleSheet("background: transparent;")
        self._tc_layout = QVBoxLayout(_track_container)
        self._tc_layout.setContentsMargins(16, 0, 16, 0)
        self._tc_layout.setSpacing(0)
        self._tc_layout.addWidget(self.track_tree)

        # ── Search bar ───────────────────────────────────────────────────────────
        _sb_container = QWidget()
        _sb_container.setObjectName('_sb')
        _sb_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _sb_container.setStyleSheet('#_sb { background: transparent; }')
        _sbl = QHBoxLayout(_sb_container)
        _sbl.setContentsMargins(16, 8, 16, 8)
        _sbl.setSpacing(0)
        _sbl.addStretch(1)
        self.search_container = SmartSearchContainer(placeholder="Search tracks…")
        self.search_container.hide_burger()
        self.search_container.text_changed.connect(self._filter_tracks)
        self.search_container.search_input.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.search_container.search_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.search_container.search_input.installEventFilter(self)
        _sbl.addWidget(self.search_container)

        # ── Track card ───────────────────────────────────────────────────────────
        self.track_card = _Card()
        _track_card_lo = QVBoxLayout(self.track_card)
        _track_card_lo.setContentsMargins(0, 0, 0, 0)
        _track_card_lo.setSpacing(0)
        _track_card_lo.addWidget(_sb_container)
        _track_card_lo.addWidget(_track_container)

        _track_wrapper = QWidget()
        _track_wrapper.setObjectName('_tw')
        _track_wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _track_wrapper.setStyleSheet('#_tw { background: transparent; }')
        _tw_lo = QVBoxLayout(_track_wrapper)
        _tw_lo.setContentsMargins(12, 10, 6, 12)
        _tw_lo.setSpacing(0)
        _tw_lo.addWidget(self.track_card)

        # ── Single outer scroll area (scrolls header + tracks together) ──────────
        _scroll_content = QWidget()
        _scroll_content.setObjectName('_sc')
        _scroll_content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _scroll_content.setStyleSheet('#_sc { background: transparent; }')
        _sc_lo = QVBoxLayout(_scroll_content)
        _sc_lo.setContentsMargins(0, 0, 0, 0)
        _sc_lo.setSpacing(0)
        _sc_lo.addWidget(_header_wrapper)
        _sc_lo.addWidget(_track_wrapper)
        _sc_lo.addStretch(1)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area.setWidget(_scroll_content)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(self._update_track_right_margin)
        self.omni_scroller = MiddleClickScroller(self.scroll_area)
        SmoothScroller(self.scroll_area)

        main_layout.addWidget(self.scroll_area, 1)

    # ── Track search ──────────────────────────────────────────────────────────

    def _toggle_track_search(self):
        si = self.search_container.search_input
        opening = si.maximumWidth() == 0
        if opening:
            si.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.search_container.toggle_search()
        if not opening:
            si.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ── Track list ────────────────────────────────────────────────────────────

    def _apply_header_style(self, accent: str):
        self.track_tree.header().setStyleSheet("""
            QHeaderView::section {{
                background: transparent;
                color: #555;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                border: none;
                border-bottom: 1px solid rgba(255,255,255,0.08);
                padding: 6px 4px 14px 4px;
            }}
            QHeaderView::section:first {{
                border-left: none;
            }}
        """)

    _COL_MIN      = {0: 50, 1: 100, 2: 80, 3: 50, 4: 50, 5: 50}
    _MARGIN_BASE  = 16
    _SCROLLBAR_W  = 6

    def _update_track_right_margin(self, min_val, max_val):
        right = self._MARGIN_BASE - self._SCROLLBAR_W if max_val > min_val else self._MARGIN_BASE
        l, t, _, b = self._tc_layout.getContentsMargins()
        self._tc_layout.setContentsMargins(l, t, right, b)
    _COL_SETTINGS_KEY = 'tracklist/col_widths'

    def _save_col_widths(self):
        hdr = self._track_header
        widths = {str(i): hdr.sectionSize(i) for i in range(hdr.count()) if i != 1}
        QSettings().setValue(self._COL_SETTINGS_KEY, widths)

    def _restore_col_widths(self):
        saved = QSettings().value(self._COL_SETTINGS_KEY)
        if not isinstance(saved, dict):
            return
        hdr = self._track_header
        hdr.blockSignals(True)
        for col_str, width in saved.items():
            col = int(col_str)
            if col != 1:
                hdr.resizeSection(col, max(self._COL_MIN.get(col, 30), int(width)))
        hdr.blockSignals(False)

    def _clamp_track_columns(self, logical_index, _, new_size):
        if logical_index == 1:  # flex/stretch col — Qt manages it
            return
        hdr = self._track_header
        view_w = self.track_tree.viewport().width()
        title_min = self._COL_MIN[1]
        other_fixed = sum(
            hdr.sectionSize(i) for i in range(hdr.count())
            if i not in (1, logical_index)
        )
        col_min = self._COL_MIN.get(logical_index, hdr.minimumSectionSize())
        max_size = max(col_min, view_w - title_min - other_fixed)
        clamped = max(col_min, min(new_size, max_size))
        if clamped != new_size:
            hdr.blockSignals(True)
            hdr.resizeSection(logical_index, clamped)
            hdr.blockSignals(False)
        self._col_save_timer.start()

    def _load_tracks(self, tracks: list):
        self._tracks = tracks
        # Collapse search bar when loading a new album
        si = self.search_container.search_input
        if si.maximumWidth() > 0:
            si.setMinimumWidth(0)
            si.setMaximumWidth(0)
            si.clear()
        self.track_tree.setUpdatesEnabled(False)
        self.track_tree.clear()

        _theme = getattr(self.window(), 'theme', None)
        _pri_color = QColor(getattr(_theme, 'font_color_primary',   '#dddddd') if _theme else '#dddddd')
        _sec_color = QColor(getattr(_theme, 'font_color_secondary', '#aaaaaa') if _theme else '#aaaaaa')

        # Group by disc number
        disc_groups: dict = {}
        for idx, t in enumerate(tracks):
            disc = int(t.get('discNumber') or t.get('disc') or 1)
            disc_groups.setdefault(disc, []).append((idx, t))
        multi_disc = len(disc_groups) > 1

        total_rows = 0
        for disc_num in sorted(disc_groups.keys()):
            if multi_disc:
                disc_item = QTreeWidgetItem(['', f'Disc {disc_num}', '', '', '', ''])
                disc_item.setFirstColumnSpanned(True)
                disc_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                disc_item.setData(0, Qt.ItemDataRole.UserRole, {'_is_disc_header': True})
                f = disc_item.font(1); f.setBold(True); disc_item.setFont(1, f)
                disc_item.setForeground(1, _sec_color)
                self.track_tree.addTopLevelItem(disc_item)
                total_rows += 1

            for disc_pos, (track_idx, t) in enumerate(disc_groups[disc_num], 1):
                num      = str(t.get('track') or (disc_pos if multi_disc else track_idx + 1))
                title    = t.get('title', '')
                artist   = t.get('artist', '')
                raw_star = t.get('starred', False)
                heart    = '♥' if (raw_star and str(raw_star).lower() not in ('false', '0', 'none', '')) else '♡'
                dur_ms   = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
                secs     = dur_ms // 1000
                duration = f"{secs // 60}:{secs % 60:02d}"
                genre    = t.get('genre', '') or ''
                plays    = str(t.get('play_count') or 0) if t.get('play_count') else '-'
                album    = t.get('album', '') or ''
                item = QTreeWidgetItem([num, title, artist, heart, genre, duration, plays, album])
                item.setData(0, Qt.ItemDataRole.UserRole, {'_track_idx': track_idx, 'id': str(t.get('id', ''))})
                item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(3, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(4, Qt.AlignmentFlag.AlignLeft   | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                for col in range(8):
                    item.setForeground(col, _pri_color if col == 1 else _sec_color)
                self.track_tree.addTopLevelItem(item)
                total_rows += 1

        self.track_tree.setUpdatesEnabled(True)
        row_h = self.track_tree.sizeHintForRow(0) if tracks else 38
        hdr_h = self.track_tree.header().height()
        self.track_tree.setFixedHeight(hdr_h + row_h * total_rows + 4)
        self.tracks_loaded.emit()
        if self._last_playing_id is not None:
            self.update_playing_status(self._last_playing_id, self._last_is_playing, self._last_playing_accent)

    def _filter_tracks(self, text: str):
        query = text.lower().strip()
        visible = 0
        for i in range(self.track_tree.topLevelItemCount()):
            item = self.track_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('_is_disc_header'):
                continue
            if not query:
                item.setHidden(False)
                visible += 1
            else:
                match = query in item.text(1).lower() or query in item.text(2).lower()
                item.setHidden(not match)
                if match:
                    visible += 1
        # Hide disc headers whose tracks are all hidden
        for i in range(self.track_tree.topLevelItemCount()):
            item = self.track_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not (isinstance(data, dict) and data.get('_is_disc_header')):
                continue
            # Check if any sibling track between this header and the next is visible
            j = i + 1
            any_visible = False
            while j < self.track_tree.topLevelItemCount():
                sibling = self.track_tree.topLevelItem(j)
                sdata = sibling.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(sdata, dict) and sdata.get('_is_disc_header'):
                    break
                if not sibling.isHidden():
                    any_visible = True
                    break
                j += 1
            item.setHidden(not any_visible)
            if any_visible:
                visible += 1
        row_h = self.track_tree.sizeHintForRow(0) if self._tracks else 38
        hdr_h = self.track_tree.header().height()
        self.track_tree.setFixedHeight(hdr_h + row_h * visible + 4)

    def _artist_part_at(self, pos) -> str:
        item = self.track_tree.itemAt(pos)
        if not item:
            return ''
        col2_x = self.track_tree.columnViewportPosition(2)
        col2_w = self.track_tree.columnWidth(2)
        if not (col2_x <= pos.x() <= col2_x + col2_w):
            return ''
        artist = item.text(2)
        f = QFont(); f.setPointSize(10)
        fm = QFontMetrics(f)
        ax = col2_x + 4
        for part, is_sep in _split_artist(artist):
            pw = fm.horizontalAdvance(part)
            if not is_sep and ax <= pos.x() < ax + pw:
                return part.strip()
            ax += pw
        return ''

    def _move_kbd_selection(self, delta: int):
        count = self.track_tree.topLevelItemCount()
        if not count:
            return
        start = self._track_delegate._kbd_row
        # If current row is hidden (search filter), start from beginning/end
        if start >= 0 and self.track_tree.topLevelItem(start).isHidden():
            start = -1 if delta > 0 else count
        row = start + delta
        while 0 <= row < count:
            item = self.track_tree.topLevelItem(row)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            is_header = isinstance(data, dict) and data.get('_is_disc_header')
            if not is_header and not item.isHidden():
                break
            row += delta
        if 0 <= row < count:
            self._track_delegate._kbd_row = row
            self.track_tree.viewport().update()
            # Scroll the outer QScrollArea so the row stays in view
            item = self.track_tree.topLevelItem(row)
            item_rect = self.track_tree.visualItemRect(item)
            item_pos  = self.track_tree.viewport().mapTo(self.scroll_area.widget(), item_rect.topLeft())
            self.scroll_area.ensureVisible(item_pos.x(), item_pos.y(), 0, item_rect.height())
        else:
            # At boundary — scroll the view to top or bottom
            sb = self.scroll_area.verticalScrollBar()
            sb.setValue(sb.minimum() if delta < 0 else sb.maximum())

    def _play_kbd_selected(self):
        row = self._track_delegate._kbd_row
        if 0 <= row < self.track_tree.topLevelItemCount():
            item = self.track_tree.topLevelItem(row)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and not data.get('_is_disc_header'):
                track_idx = data.get('_track_idx', -1)
                if 0 <= track_idx < len(self._tracks):
                    self.track_play_signal.emit(self._tracks, track_idx)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        _sc = getattr(self, 'search_container', None)
        si = getattr(_sc, 'search_input', None) if _sc else None
        if si and obj is si and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                self._move_kbd_selection(-1); return True
            if key == Qt.Key.Key_Down:
                self._move_kbd_selection(1); return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._play_kbd_selected(); return True
            if key == Qt.Key.Key_Escape:
                self.search_container.search_input.clear()
                self._toggle_track_search(); return True
        if obj is self.track_tree and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key.Key_Up:
                self._move_kbd_selection(-1); return True
            if key == Qt.Key.Key_Down:
                self._move_kbd_selection(1); return True
            if key == Qt.Key.Key_PageUp:
                row_h = self.track_tree.sizeHintForRow(0) or 1
                page  = max(1, self.scroll_area.viewport().height() // row_h)
                self._move_kbd_selection(-page); return True
            if key == Qt.Key.Key_PageDown:
                row_h = self.track_tree.sizeHintForRow(0) or 1
                page  = max(1, self.scroll_area.viewport().height() // row_h)
                self._move_kbd_selection(page); return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self.play_clicked.emit(); return True
                self._play_kbd_selected(); return True
        if obj is self.track_tree.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos  = event.position().toPoint()
                item = self.track_tree.itemAt(pos)
                row  = self.track_tree.indexOfTopLevelItem(item) if item else -1
                part = self._artist_part_at(pos)
                new_hover = (row, part) if part else None
                if new_hover != self._track_delegate._hover_artist:
                    self._track_delegate._hover_artist = new_hover
                    self.track_tree.viewport().update()
                genre_part = self._genre_part_at(pos)
                new_hover_genre = (row, genre_part) if genre_part else None
                if new_hover_genre != self._track_delegate._hover_genre:
                    self._track_delegate._hover_genre = new_hover_genre
                    self.track_tree.viewport().update()
                self.track_tree.viewport().setCursor(
                    Qt.CursorShape.PointingHandCursor if (part or genre_part) else Qt.CursorShape.ArrowCursor
                )
            elif event.type() == QEvent.Type.Leave:
                changed = False
                if self._track_delegate._hover_artist is not None:
                    self._track_delegate._hover_artist = None
                    changed = True
                if self._track_delegate._hover_genre is not None:
                    self._track_delegate._hover_genre = None
                    changed = True
                if changed:
                    self.track_tree.viewport().update()
            elif event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos  = event.position().toPoint()
                    item = self.track_tree.itemAt(pos)
                    if item:
                        col3_x = self.track_tree.columnViewportPosition(3)
                        col3_w = self.track_tree.columnWidth(3)
                        if col3_x <= pos.x() <= col3_x + col3_w:
                            self._toggle_track_fav(item)
                            return True
                        genre_part = self._genre_part_at(pos)
                        if genre_part:
                            self.genre_clicked.emit(genre_part)
                            return True
                    part = self._artist_part_at(pos)
                    if part:
                        self.track_artist_clicked.emit(part)
                        return True
            elif event.type() == QEvent.Type.ContextMenu:
                self._show_track_context_menu(event.pos())
                return True
        return super().eventFilter(obj, event)

    def _genre_part_at(self, pos) -> str:
        """Return the specific genre token under pos, or empty string."""
        item = self.track_tree.itemAt(pos)
        if not item:
            return ''
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict) and item_data.get('_is_disc_header'):
            return ''
        col4_x = self.track_tree.columnViewportPosition(4)
        col4_w = self.track_tree.columnWidth(4)
        if not (col4_x <= pos.x() <= col4_x + col4_w):
            return ''
        genre_text = item.text(4) or ''
        if not genre_text:
            return ''
        f = QFont(); f.setPointSize(10)
        fm = QFontMetrics(f)
        ax = col4_x + 4
        for part, is_sep in _split_genres(genre_text):
            pw = fm.horizontalAdvance(part)
            if not is_sep and ax <= pos.x() < ax + pw:
                return part
            ax += pw
        return ''

    def _toggle_track_fav(self, item):
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict) and item_data.get('_is_disc_header'):
            return
        row = item_data.get('_track_idx') if isinstance(item_data, dict) else None
        if row is None or not (0 <= row < len(self._tracks)):
            return
        track = self._tracks[row]
        raw   = track.get('starred', False)
        cur   = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new   = not cur
        track['starred'] = new
        item.setText(3, '♥' if new else '♡')
        self.track_tree.viewport().update()
        self.favorite_toggled.emit(str(track.get('id', '')), new)
        if getattr(self, 'client', None):
            import threading
            threading.Thread(target=lambda: self.client.set_favorite(track.get('id'), new), daemon=True).start()

    def _show_track_context_menu(self, pos):
        item = self.track_tree.itemAt(pos)
        if not item:
            return
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict) and item_data.get('_is_disc_header'):
            return
        idx = item_data.get('_track_idx') if isinstance(item_data, dict) else None
        if idx is None or not (0 <= idx < len(self._tracks)):
            return
        track = self._tracks[idx]
        main  = self.window()

        from player.widgets import ShadowContextMenu
        _theme = getattr(main, 'theme', None)
        bg  = getattr(self, '_bg_color', getattr(_theme, 'main_panel_bg', '14,14,14'))
        bc  = getattr(_theme, 'border_color',        '#2a2a2a') if _theme else '#2a2a2a'
        fg  = getattr(_theme, 'font_color_primary',  '#dddddd') if _theme else '#dddddd'
        fg2 = getattr(_theme, 'font_color_secondary','#555555') if _theme else '#555555'
        px  = getattr(_theme, 'font_size_primary',   14)        if _theme else 14
        acc = getattr(_theme, 'accent',              '#cccccc') if _theme else '#cccccc'
        if _theme and not getattr(_theme, 'auto_border_from_accent', True):
            bc = getattr(_theme, 'manual_border_color', '#2a2a2a')
        hov = resolve_menu_hover(_theme)

        menu = ShadowContextMenu(self)
        menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)

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
                        lambda i=idx: self._toggle_track_fav(self.track_tree.topLevelItem(i)),
                        color='#E91E63',
                        icon_path='img/heart_filled.png' if is_fav else 'img/heart.png')

        gp = self.track_tree.viewport().mapToGlobal(pos)
        menu.exec_at(gp.__class__(gp.x() - menu._PAD, gp.y() - menu._PAD), window=main)

    def _add_to_new_playlist(self, main, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client:
            return
        from components import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        accent = getattr(main, 'master_color', '#1DB954')
        dialog = NewPlaylistDialog(self, accent_color=accent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name:
                return
            import threading
            def _worker():
                try:
                    new_id = client.create_playlist(name, public=dialog.is_public())
                    if new_id:
                        client.add_tracks_to_playlist(new_id, track_ids)
                except Exception as e:
                    print(f"AlbumDetail: create playlist failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _add_to_existing_playlist(self, main, pl_id, pl_name, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client:
            return
        import threading
        threading.Thread(
            target=lambda: client.add_tracks_to_playlist(pl_id, track_ids),
            daemon=True
        ).start()

    @staticmethod
    def _make_heart_pix(path: str, color: str, size: int = 16) -> QPixmap:
        base = QPixmap(path)
        if base.isNull():
            return QPixmap()
        base = base.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.drawPixmap(0, 0, base)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pix.rect(), QColor(color))
        p.end()
        return pix

    def _on_track_double_clicked(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get('_is_disc_header'):
            return
        track_idx = data.get('_track_idx', -1) if isinstance(data, dict) else self.track_tree.indexOfTopLevelItem(item)
        if 0 <= track_idx < len(self._tracks):
            self.track_play_signal.emit(self._tracks, track_idx)

    # ── Blurred background ────────────────────────────────────────────────────


    # ─────────────────────────────────────────────────────────────────────────



    def toggle_header_heart(self):
        is_liked = getattr(self, '_album_liked', False)
        new_state = not is_liked
        self.set_header_heart_state(new_state)
        self.album_favorite_toggled.emit(new_state)

    def set_header_heart_state(self, is_liked):
        self._album_liked = is_liked
        theme = getattr(self.window(), 'theme', None)
        _hov = resolve_menu_hover(theme)
        _btn_style = (
            f'QPushButton {{ background: transparent; border: none; border-radius: 4px; }}'
            f' QPushButton:hover {{ background: {_hov}; }}'
        )
        self.btn_like.setStyleSheet(_btn_style)
        if is_liked:
            self.btn_like.setIcon(QIcon(self._make_heart_pix(resource_path('img/heart_filled.png'), '#E91E63', size=22)))
        else:
            self.btn_like.setIcon(QIcon(self._make_heart_pix(resource_path('img/heart.png'), '#666666', size=22)))
        self.btn_like.setIconSize(QSize(22, 22))

    def update_playing_status(self, playing_id, is_playing, accent: str):
        self._last_playing_id = playing_id
        self._last_is_playing = is_playing
        self._last_playing_accent = accent
        playing_track_idx = next((i for i, t in enumerate(self._tracks) if str(t.get('id')) == str(playing_id)), -1) if is_playing else -1
        accent_color = QColor(accent)
        _theme = getattr(self.window(), 'theme', None)
        default_color = QColor(getattr(_theme, 'font_color_primary', '#dddddd') if _theme else '#dddddd')
        sec_color = QColor(getattr(_theme, 'font_color_secondary', '#aaaaaa') if _theme else '#aaaaaa')
        playing_tree_row = -1
        for i in range(self.track_tree.topLevelItemCount()):
            item = self.track_tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('_is_disc_header'):
                continue
            track_idx = data.get('_track_idx', -1) if isinstance(data, dict) else -1
            row_is_playing = (track_idx == playing_track_idx and playing_track_idx >= 0)
            if row_is_playing:
                playing_tree_row = i
            for col in range(self.track_tree.columnCount()):
                if row_is_playing:
                    item.setForeground(col, accent_color)
                elif col == 1:
                    item.setForeground(col, default_color)
                else:
                    item.setForeground(col, sec_color)
        self._track_delegate.set_playing(playing_tree_row, accent, is_playing)
        if playing_tree_row >= 0 and is_playing:
            self._playing_movie.start()
        else:
            self._playing_movie.stop()

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")

    def set_active_hover(self, color: 'QColor'):
        if hasattr(self, '_track_delegate'):
            self._track_delegate.set_active_hover(color)

    def set_accent_color(self, color):
        if hasattr(self, 'lbl_artist'):
            _sec = getattr(getattr(self.window(), 'theme', None), 'font_color_secondary', '#aaaaaa')
            self.lbl_artist.set_color(_sec)
        if hasattr(self, '_track_header'):
            self._track_header.set_accent(color)
        if hasattr(self, 'header_container') and hasattr(self.header_container, 'set_border'):
            theme = getattr(self.window(), 'theme', None)
            border  = getattr(theme, 'border_color',        '#2a2a2a') if theme else '#2a2a2a'
            card_bg = getattr(theme, 'now_playing_card_bg', '#1e1e1e') if theme else '#1e1e1e'
            self.header_container.set_border(border)
            self.header_container.set_bg(card_bg)
        if hasattr(self, 'track_card') and hasattr(self.track_card, 'set_border'):
            theme = getattr(self.window(), 'theme', None)
            border  = getattr(theme, 'border_color',        '#2a2a2a') if theme else '#2a2a2a'
            card_bg = getattr(theme, 'now_playing_card_bg', '#1e1e1e') if theme else '#1e1e1e'
            self.track_card.set_border(border)
            self.track_card.set_bg(card_bg)
        if hasattr(self, '_track_delegate'):
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self._track_delegate.set_font_size(theme.font_size_primary)
        if hasattr(self, 'lbl_meta') or hasattr(self, 'lbl_title'):
            theme = getattr(self.window(), 'theme', None)
            pri_color = getattr(theme, 'font_color_primary',  '#dddddd') if theme else '#dddddd'
            sec_size  = getattr(theme, 'font_size_secondary', 12)        if theme else 12
            sec_color = getattr(theme, 'font_color_secondary','#aaaaaa') if theme else '#aaaaaa'
            if hasattr(self, 'lbl_title'):
                pri_size = getattr(theme, 'font_size_primary', 17) if theme else 17
                self.lbl_title.setStyleSheet(f"color: {color}; font-weight: bold; font-size: {pri_size + 15}px; background: transparent; margin: 0; padding: 0;")
            if hasattr(self, 'lbl_meta'):
                self.lbl_meta.setStyleSheet(f"color: {sec_color}; font-weight: bold; font-size: {sec_size}px;")
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")

        self.btn_play.apply_accent(color, getattr(self.window(), 'theme', None))
        
        
        scrollbar_style = f"""
            QScrollArea {{ background: transparent; border: none; }}
            QWidget#ScrollContent {{ background: transparent; }}
            {scrollbar_css(color, hide_horizontal=True)}
        """
        self.scroll_area.setStyleSheet(scrollbar_style)
        if not hasattr(self, '_scroll_reveal'):
            self._scroll_reveal = install_scroll_reveal(self.scroll_area.viewport(), self.scroll_area.verticalScrollBar())
        self._scroll_reveal.color = color

        if hasattr(self, 'search_container'):
            self.search_container.set_accent_color(color)

        theme = getattr(self.window(), 'theme', None)
        _hov = resolve_menu_hover(theme)
        _btn_style = (
            f'QPushButton {{ background: transparent; border: none; border-radius: 4px; }}'
            f' QPushButton:hover {{ background: {_hov}; }}'
        )
        if hasattr(self, 'btn_shuffle'):
            self.btn_shuffle.setStyleSheet(_btn_style)
            sec_color = getattr(theme, 'font_color_secondary', '#888888') if theme else '#888888'
            icon_path = resource_path("img/shuffle.png")
            if os.path.exists(icon_path):
                pixmap = QPixmap(icon_path)
                colored = QPixmap(pixmap.size())
                colored.fill(QColor(0, 0, 0, 0))
                painter = QPainter(colored)
                painter.drawPixmap(0, 0, pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(colored.rect(), QColor(sec_color))
                painter.end()
                self.btn_shuffle.setIcon(QIcon(colored))
                self.btn_shuffle.setIconSize(QSize(22, 22))
        if hasattr(self, 'btn_like'):
            is_liked = getattr(self, '_album_liked', False)
            self.btn_like.setStyleSheet(_btn_style)
            heart_path = 'img/heart_filled.png' if is_liked else 'img/heart.png'
            heart_color = '#E91E63' if is_liked else '#666666'
            self.btn_like.setIcon(QIcon(self._make_heart_pix(resource_path(heart_path), heart_color, size=22)))
            self.btn_like.setIconSize(QSize(22, 22))


    def load_album(self, album_data):
        self.current_album_id = album_data.get('id')
        title = album_data.get('title') or album_data.get('name') or "Unknown Album"
        album_artist = album_data.get('albumArtist') or album_data.get('album_artist') or album_data.get('artist') or "Unknown Artist"

        self.lbl_title.setText(title)
        self.lbl_artist.setText(album_artist)
        self.lbl_meta.setText("Loading...")
        self.track_tree.clear()
        self.track_tree.setFixedHeight(self.track_tree.header().height())

        if not (hasattr(self, 'client') and self.client):
            return

        import threading, re
        _album_id = self.current_album_id
        _album_data = album_data

        # ── Instant pre-load: memory cache → disk cache ───────────────────
        _pre_loaded = False
        _pre_tracks = self._track_mem_cache.get(str(_album_id))
        if _pre_tracks:
            self._load_tracks(_pre_tracks)
            _pre_loaded = True
        else:
            try:
                _disk = self.client._disk_cache_get(f"album_tracks_{_album_id}")
                if _disk:
                    self._load_tracks(_disk)
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
            year = str(_album_data.get('year', '')).replace('None', '')
            return detected, " • ".join(p for p in [year, f"{n} songs", time_str] if p)

        # Show meta instantly from cached tracks — no need to wait for network
        if _pre_tracks:
            _detected, _meta_str = compute_meta(_pre_tracks)
            if _detected:
                self.lbl_artist.setText(_detected)
            if _meta_str:
                self.lbl_meta.setText(_meta_str)

        def _fetch_fresh():
            try:
                # If not pre-loaded, do a stale load first (no network, no starred blocking)
                cached_meta = ""
                if not _pre_loaded:
                    raw_stale = self.client.get_album_tracks(_album_id)
                    if raw_stale:
                        detected, cached_meta = compute_meta(raw_stale)
                        self._meta_ready.emit(detected, cached_meta)
                        self._tracks_ready.emit(raw_stale)
                        self._mem_cache_put(str(_album_id), raw_stale)

                # Fresh fetch from server
                try: self.client._scan_status_cache = None
                except: pass
                raw_fresh = self.client.get_album_tracks(_album_id, force_refresh=True)
                if not raw_fresh: return

                # Album star status — read from stale_cache saved by get_album_tracks
                try:
                    _star = self.client.stale_cache_get(f'album_starred_{_album_id}')
                    if _star is not None:
                        self._album_star_ready.emit(bool(_star))
                except Exception: pass
                # Enrich starred — use session cache (fetched once, not per album open)
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
            
        # Prefer cached starred status (from last getAlbum); fall back to album_data
        _cached_star = self.client.stale_cache_get(f'album_starred_{_album_id}')
        if _cached_star is not None:
            is_fav = bool(_cached_star)
        else:
            is_fav = bool(album_data.get('starred') or album_data.get('favorite'))
        self.set_header_heart_state(is_fav)
        
        # 3. FAST COVER ART
        from PyQt6.QtGui import QPixmap
        self.cover_label.set_pixmap(QPixmap())
        cid = album_data.get('cover_id') or album_data.get('coverArt') or album_data.get('id')
        if cid:
            self.cover_label.set_cover_meta(cid, self.client)
            from cover_cache import CoverCache
            data = CoverCache.instance().get_full(cid) or CoverCache.instance().get_thumb(cid)
            if data:
                pix = QPixmap()
                pix.loadFromData(data)
                self.cover_label.set_pixmap(pix)
            import threading
            def fetch():
                if not getattr(self, 'client', None): return
                try:
                    d = self.client.get_cover_art(cid, size=None)
                    if d:
                        CoverCache.instance().save_full(cid, d)
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(0, lambda: self.update_cover(d))
                except: pass
            threading.Thread(target=fetch, daemon=True).start()


    def update_cover(self, data):
        from PyQt6.QtGui import QPixmap
        pix = QPixmap()
        pix.loadFromData(data)
        self.cover_label.set_pixmap(pix)

class DummyScrollBar:
    def value(self): return 0
    def setValue(self, val): pass
    def setStyleSheet(self, style): pass
    def setSingleStep(self, step): pass

class QMLGridWrapper(QQuickWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dummy_scroll = DummyScrollBar()
        
    def verticalScrollBar(self): return self._dummy_scroll
    
    def viewport(self): return self

    # Silently absorb all legacy QListWidget commands from main.py!
    def setLayoutMode(self, *args): pass
    
    def setUniformItemSizes(self, *args): pass
    
    def setBatchSize(self, *args): pass
    
    def setSpacing(self, *args): pass
    
    def setGridSize(self, *args): pass
    
    def setIconSize(self, *args): pass
    
    def setMovement(self, *args): pass
    
    def setVerticalScrollMode(self, *args): pass
    
    def setViewMode(self, *args): pass
    
    def doItemsLayout(self, *args): pass
    
    def clear(self): pass
    
    def count(self): return 0
    
    def currentItem(self): return None
    
    def currentRow(self): return -1
    
    def setCurrentRow(self, *args): pass
    
    def setCurrentItem(self, *args): pass
    
    def item(self, *args): return None
    
    def setResizeMode(self, mode):
        from PyQt6.QtQuickWidgets import QQuickWidget
        if isinstance(mode, QQuickWidget.ResizeMode):
            super().setResizeMode(mode)

class QMLMiddleClickScroller(QObject):
    """
    Middle-click omni-scroller for QMLGridWrapper.
    Mirrors MiddleClickScroller but pushes pixel deltas via GridBridge.scrollBy
    instead of writing to a QScrollBar (which is a no-op stub on QMLGridWrapper).
    """
    def __init__(self, qml_widget, bridge):
        super().__init__(qml_widget)
        self.target = qml_widget
        self.bridge = bridge
        self.is_scrolling = False
        self.origin_y = 0
        self.click_time = 0

        self.timer = QTimer(self)
        self.timer.start(7)
        self.timer.timeout.connect(self._process_scroll)

        # Monitor the widget itself (QQuickWidget has no separate viewport)
        self.target.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.target and event.type() == QEvent.Type.Hide:
            if self.is_scrolling:
                self._stop()
            return False

        if obj == self.target:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    if self.is_scrolling:
                        self._stop()
                    else:
                        self._start(event.globalPosition().toPoint().y())
                    return True          # swallow — QML native handler not needed
                elif self.is_scrolling:
                    self._stop()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.MiddleButton and self.is_scrolling:
                    if time.time() - self.click_time > 0.2:
                        self._stop()
                    return True

        return super().eventFilter(obj, event)

    def _start(self, start_y):
        self.is_scrolling = True
        self.origin_y = start_y
        self.click_time = time.time()
        self.target.setCursor(Qt.CursorShape.SizeVerCursor)

    def _stop(self):
        self.is_scrolling = False
        self.target.unsetCursor()
        self.bridge.cancelScroll.emit()     # also kills the QML-side cursor if active

    def _process_scroll(self):
        if not self.is_scrolling:
            return

        buttons = QApplication.mouseButtons()
        if (not self.target.isVisible()
                or not QApplication.activeWindow()
                or (buttons & Qt.MouseButton.LeftButton)
                or (buttons & Qt.MouseButton.RightButton)):
            self._stop()
            return

        delta = QCursor.pos().y() - self.origin_y
        deadzone = 15
        if abs(delta) < deadzone:
            return

        speed = (abs(delta) - deadzone) * 0.03
        direction = 1 if delta > 0 else -1
        self.bridge.scrollBy.emit(speed * direction)

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
        self.all_albums_cache = []      # Full sorted list, sliced per page
        self.all_albums_sort = None     # Sort key used to build the cache

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # --- HEADER ---
        self.header_container = QWidget()
        self.header_container.setFixedHeight(50)
        self.header_container.setStyleSheet("QWidget { background-color: #111; border-top-left-radius: 5px; border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        
        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(15, 0, 10, 0) 
        header_layout.setSpacing(15)
        
        self.status_label = QLabel(f"Loading albums...")
        self.status_label.setStyleSheet("color: #888888; font-weight: bold; background: transparent; border: none;")
        
        
        self.sort_states = {
            'random': True,
            'latest': True,
            'alphabetical': True,
            'favorites': True,
            'compilations': True,
            'song_count': False,
        }
        self.current_sort = 'latest'
        
        
        # --- SMART SEARCH CONTAINER ---
        self.search_container = SmartSearchContainer(placeholder="Search albums...")
        self.search_container.text_changed.connect(self.on_search_text_changed)
        self.search_container.burger_clicked.connect(self.show_sort_menu)
        self.burger_btn = self.search_container.get_burger_btn()
        
        # Catch the Enter key inside the search box!
        if hasattr(self.search_container, 'search_input'):
            self.search_container.search_input.returnPressed.connect(self.focus_first_grid_item)
            self.search_container.search_input.installEventFilter(self)

        
        # Main Header Assembly
        header_layout.addWidget(self.status_label)
        header_layout.addStretch() 
        header_layout.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)

        self.layout.addWidget(self.header_container)

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
        self.grid_bridge = GridBridge(self.album_model)

        self.grid_bridge.itemClicked.connect(self.album_clicked.emit)
        self.grid_bridge.playClicked.connect(lambda data: self.start_play_fetch(data['id']))
        self.grid_bridge.visibleRangeChanged.connect(self.check_viewport_qml)
        self.grid_bridge.artistClicked.connect(self.on_grid_artist_clicked)
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
        
        self.artist_detail_list = QListWidget()
        self.artist_detail_list.setStyleSheet("QListWidget { background: transparent; border: none; } QListWidget::item { color: #ddd; padding: 10px; border-bottom: 1px solid #333; }")
        self.artist_detail_list.itemClicked.connect(self.on_artist_album_clicked)
        self.stack.addWidget(self.artist_detail_list) 

        
        
        self.is_fetching_next = False

        self.set_accent_color("#888888")


        self.add_to_history({'type': 'root'})

        self.refresh_grid()
      
    def on_grid_artist_clicked(self, album_data):
        artist_name = album_data.get('albumArtist') or album_data.get('artist', '')
        if artist_name:
            # You already have this signal set up, we just fire it!
            self.switch_to_artist_tab.emit(artist_name)
    
    def eventFilter(self, source, event):
        from PyQt6.QtCore import Qt, QEvent
        from PyQt6.QtGui import QKeyEvent
        
        is_search_box = hasattr(self, 'search_container') and source == getattr(self.search_container, 'search_input', None)

        # THE TEXT BOX SHIELD: Protect the search box from global shortcuts!
        if is_search_box:
            if event.type() == QEvent.Type.ShortcutOverride:
                if isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Backspace:
                    event.accept() 
                    return True
            # Let the text box process normal typing without stealing it!
            if event.type() == QEvent.Type.KeyPress:
                return False 

        # THE SPOTLIGHT FIX: Route normal typing to the global search ONLY if we aren't already in a text box
        if event.type() == QEvent.Type.KeyPress:
            if event.text().isprintable() and event.text().strip() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                main_win = self.window()
                if main_win:
                    main_win.keyPressEvent(event)
                return True 
                    
        return super().eventFilter(source, event)
    
    def change_page(self, page):
        if page < 1 or page > self.total_pages: 
            return
        self.current_page = page
        self.load_albums_page(reset=False)

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()
    
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

    def on_sort_changed(self):
        self.load_albums_page(reset=True)

    def show_sort_menu(self):
        """Show dropdown menu with sort options when burger is clicked"""
        from player.widgets import ShadowContextMenu
        from player.mixins.visuals import resolve_menu_hover
        _theme = getattr(self.window(), 'theme', None)
        _bg  = getattr(_theme, 'main_panel_bg',       '14,14,14') if _theme else '14,14,14'
        _bc  = getattr(_theme, 'border_color',         '#2a2a2a') if _theme else '#2a2a2a'
        _fg  = getattr(_theme, 'font_color_primary',   '#dddddd') if _theme else '#dddddd'
        _fg2 = getattr(_theme, 'font_color_secondary', '#555555') if _theme else '#555555'
        _px  = getattr(_theme, 'font_size_primary',    14)        if _theme else 14
        _acc = getattr(_theme, 'accent',               '#cccccc') if _theme else '#cccccc'
        _hov = resolve_menu_hover(_theme)

        menu = ShadowContextMenu(self)
        menu.configure(_bg, _bc, _fg, _fg2, _hov, _px, accent=_acc)

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

        gp = self.burger_btn.mapToGlobal(self.burger_btn.rect().bottomLeft())
        menu.exec_at(gp.__class__(gp.x() - menu._PAD, gp.y() - menu._PAD), window=self.window())

    def update_cover(self, data):
        from PyQt6.QtGui import QPixmap
        pix = QPixmap()
        pix.loadFromData(data)
        self.cover_label.setPixmap(_square_cover(pix))

    def get_tinted_sort_icon(self, sort_type, is_ascending):
        """Get a tinted icon for the sort menu based on current accent color"""
        if sort_type == 'song_count':
            suffix = 'asc' if is_ascending else 'desc'
            icon_path = resource_path(f"img/sort-num-{suffix}.png")
        else:
            suffix = 'a' if is_ascending else 'd'
            icon_path = resource_path(f"img/sort-{sort_type}-{suffix}.png")
        try:
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull() and hasattr(self, 'current_accent'):
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(self.current_accent))
                painter.end()
                return QIcon(pixmap)
        except Exception as e:
            print(f"Error tinting sort icon: {e}")
        
        # Fallback to untinted
        return QIcon(icon_path)

    def create_sort_action(self, menu, sort_type, label):
        """Create a toggleable sort action with icon"""
        # Get current state (True = ascending, False = descending)

        is_ascending = self.sort_states[sort_type]
        
        # Get tinted icon based on state
        icon = self.get_tinted_sort_icon(sort_type, is_ascending)

        # Create action
        action = QAction(icon, f"  {label}", self)
        action.triggered.connect(lambda: self.toggle_sort_state(sort_type))
        menu.addAction(action)

    def _get_tinted_icon(self, icon_path):
        """Load an icon and tint it with the current accent color."""
        try:
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull() and hasattr(self, 'current_accent'):
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(self.current_accent))
                painter.end()
                return QIcon(pixmap)
        except Exception:
            pass
        return QIcon(icon_path)

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
            
        self.all_albums_cache = []   # invalidate cache
        self.all_albums_sort = None
        self.true_server_count = 0   # force re-count from new sort endpoint
        self.update_burger_icon()
        self.load_albums_page(reset=True)

    def update_burger_icon(self):
        """Update burger button to show the currently active sort icon"""
        if not hasattr(self, 'current_sort'):
            return
        
        # Pure-filter sorts use their own icons
        if self.current_sort == 'favorites':
            self.burger_btn.setIcon(self._get_tinted_icon(resource_path("img/heart.png")))
            return
        if self.current_sort == 'compilations':
            self.burger_btn.setIcon(self._get_tinted_icon(resource_path("img/comp.png")))
            return

        # Get the current sort state (ascending/descending)
        is_ascending = self.sort_states.get(self.current_sort, True)

        # Load the icon for current sort
        if self.current_sort == 'song_count':
            suffix = 'asc' if is_ascending else 'desc'
            icon_path = resource_path(f"img/sort-num-{suffix}.png")
        else:
            suffix = 'a' if is_ascending else 'd'
            icon_path = resource_path(f"img/sort-{self.current_sort}-{suffix}.png")
        
        try:
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                # Tint with accent color if available
                if hasattr(self, 'current_accent'):
                    painter = QPainter(pixmap)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    painter.fillRect(pixmap.rect(), QColor(self.current_accent))
                    painter.end()

                self.burger_btn.setIcon(QIcon(pixmap))
        except Exception as e:
            print(f"Error updating burger icon: {e}")
 
    def get_filtered_count(self):
        # Count comes from the live API pagination — not needed for local DB queries
        return getattr(self, 'total_items', 0)

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
            self.status_label.setText("0 albums")
            return

        # Safely handle if the API returns 'None' instead of 0!
        tc = total_count if total_count is not None else 0
        display_count = tc if tc > 0 else len(albums)
        
        self.status_label.setText(f"{display_count:,} albums".replace(",", " "))
        self.populate_grid(albums)
        
        if hasattr(self, 'qml_view') and self.isVisible():
            search_input = getattr(getattr(self, 'search_container', None), 'search_input', None)
            if search_input is None or not search_input.hasFocus():
                self.qml_view.setFocus()

    def _on_compilations_loaded(self, albums):
        self.album_model.clear()
        if not albums:
            self.status_label.setText("0 albums")
            return
        self.status_label.setText(f"{len(albums):,} albums".replace(",", " "))
        self.populate_grid(albums)
        if hasattr(self, 'qml_view') and self.isVisible():
            search_input = getattr(getattr(self, 'search_container', None), 'search_input', None)
            if search_input is None or not search_input.hasFocus():
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
        
        if hasattr(self, 'search_container'):
            self.search_container.hide_search()
            self.search_container.hide_burger()
        
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

    def on_nav_back(self):
        # If a hardcoded Back command comes through, ignore it if we are typing!
        from PyQt6.QtWidgets import QApplication, QLineEdit
        focused = QApplication.focusWidget()
        if isinstance(focused, QLineEdit) and hasattr(self, 'search_container'):
            if focused == getattr(self.search_container, 'search_input', None):
                return
                
        if self.nav_index > 0:
            self.nav_index -= 1
            self.render_state(self.nav_history[self.nav_index])

    def on_nav_fwd(self):
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_index += 1
            self.render_state(self.nav_history[self.nav_index])
   
    def render_state(self, state):
        s_type = state.get('type')
        self.current_album_id = None 
        if s_type == 'root':
            if hasattr(self, '_show_controls_timer') and self._show_controls_timer:
                 self._show_controls_timer.stop()
                 
            self._show_controls_timer = QTimer()
            self._show_controls_timer.setSingleShot(True)
            self._show_controls_timer.timeout.connect(self.ensure_grid_controls_visible)
            self._show_controls_timer.start(50)
            
            self.stack.setCurrentIndex(0)
            if self.album_model.rowCount() == 0: self.refresh_grid()
        elif s_type == 'album':
            self.current_album_id = state['data']['id']
            self.show_album_details(state['data'], record_history=False)
        elif s_type == 'artist':
            self.show_artist_details(state['data'], record_history=False)

    def on_grid_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data: return
        
        visual_rect = self.grid_view.visualItemRect(item)
        mouse_pos = self.grid_view.mapFromGlobal(QCursor.pos())
        rect = visual_rect
        icon_width = rect.width() - 20 
        center_x = rect.x() + 10 + (icon_width // 2)
        center_y = rect.y() + 10 + (icon_width // 2)
        play_radius = min(60, icon_width // 2) // 2 
        dist = ((mouse_pos.x() - center_x)**2 + (mouse_pos.y() - center_y)**2) ** 0.5
        
        if dist <= play_radius:
            self.start_play_fetch(data['id'])
        else:
            self.album_clicked.emit(data)

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

    def on_artist_album_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and data['type'] == 'album_drill':
            self.add_to_history({'type': 'album', 'data': data['data']})

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
        if hasattr(self, 'status_label'):
            _theme = getattr(self.window(), 'theme', None)
            _sec_color = getattr(_theme, 'font_color_secondary', '#888888') if _theme else '#888888'
            self.status_label.setStyleSheet(
                f"color: {_sec_color}; font-weight: bold; background: transparent; border: none;"
            )

        if hasattr(self, 'grid_bridge'):
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.grid_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.grid_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.grid_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.grid_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
                self.grid_bridge.skeletonBaseColorChanged.emit(
                    getattr(theme, 'skeleton_base', '#282828'))

        if getattr(self, 'current_accent', None) == color:
            return

        self.current_accent = color

        # Force Python to paint the darkness so the GPU clears its old frames!
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")
        if hasattr(self, 'header_container'):
            self.header_container.setStyleSheet(
                "QWidget { background-color: transparent; border: none; }"
            )

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(1.0)

        # Update the detail view behind the scenes
        if hasattr(self, 'detail_view'):
            self.detail_view.set_accent_color(color)
        
        # Keep the search bar and burger menu colors synced
        in_detail = self.stack.currentIndex() != 0
        if hasattr(self, 'search_container'):
            h = self.search_container.isHidden()
            self.search_container.set_accent_color(color)
            if h or in_detail: self.search_container.hide()
            
        if hasattr(self, 'burger_btn'):
            h = self.burger_btn.isHidden()
            if hasattr(self, 'update_burger_icon'):
                self.update_burger_icon()
            if h or in_detail: self.burger_btn.hide()
  
    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)
    
    def ensure_grid_controls_visible(self):
        if self.stack.currentIndex() == 0:
            self.header_container.show()
            self.status_label.show()
            
            if hasattr(self, 'search_container'):
                self.search_container.show()
                self.search_container.show_search()
                self.search_container.show_burger()

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

    def _on_song_count_loaded(self, albums, total, cache_key):
        """Called when the full sorted list is ready. Caches it and repopulates in chunks."""
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None
        if not albums:
            return
        self.all_albums_cache = albums
        self.all_albums_sort = cache_key
        self.status_label.setText(f"{len(albums):,} albums".replace(",", " "))
        # Reset model: seed each slot with real metadata so text shows immediately;
        # type='placeholder' keeps IS_LOADING=True so QML still shows image skeleton.
        placeholders = [dict(a, type='placeholder') for a in albums]
        self.album_model.clear()
        self.album_model.append_albums(placeholders)
        self._fill_song_count_chunks(albums)

    def _fill_song_count_chunks(self, albums, chunk_size=50, chunk_index=0):
        """Populate the model chunk by chunk via QTimer so the main thread isn't blocked."""
        start = chunk_index * chunk_size
        if start >= len(albums):
            return
        end = min(start + chunk_size, len(albums))
        client = getattr(self, 'client', None)
        name_id_cache = getattr(client, '_artist_name_id', None) if client else None
        covers_to_queue = []
        for i, album in enumerate(albums[start:end]):
            row = start + i
            if row >= len(self.album_model.albums):
                break
            cid = album.get('cover_id') or album.get('coverArt') or album.get('id')
            if cid:
                album['cover_id'] = cid
                covers_to_queue.append(cid)
            if name_id_cache is not None:
                aid = album.get('artistId') or album.get('albumArtistId')
                aname = album.get('artist') or album.get('albumArtist') or album.get('name', '')
                if aid and aname:
                    name_id_cache[aname.lower().strip()] = aid
            self.album_model.albums[row] = album
        self.album_model.dataChanged.emit(
            self.album_model.index(start, 0),
            self.album_model.index(end - 1, 0),
            [self.album_model.TITLE_ROLE, self.album_model.ARTIST_ROLE,
             self.album_model.YEAR_ROLE, self.album_model.COVER_ID_ROLE,
             self.album_model.IS_LOADING_ROLE]
        )
        if hasattr(self, 'cover_worker') and self.cover_worker:
            for cid in reversed(covers_to_queue):
                self.cover_worker.queue_cover(cid)
        if end < len(albums):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(16, lambda: self._fill_song_count_chunks(albums, chunk_size, chunk_index + 1))

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
            self.status_label.setText(f"{self.true_server_count:,} albums".replace(",", " "))
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
            self.check_viewport_qml(0, 50)

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
        self.current_query = ""; self.search_container.search_input.clear()
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
            self.status_label.setText(f"{self.true_server_count:,} albums".replace(",", " "))
            
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
        if hasattr(self, 'status_label'):
            self.status_label.setText("Loading...")

    def refresh_grid(self):
        # 1. Reset the server count unless stale cache provided it
        if not getattr(self, '_stale_count_set', False):
            self.true_server_count = 0
        self._stale_count_set = False
        self.all_albums_cache = []
        self.all_albums_sort = None

        # 2. Brutally wipe the API cache so we don't just reload the old albums from memory!
        if hasattr(self, 'client') and self.client and hasattr(self.client, '_api_cache'):
            keys_to_delete = [k for k in self.client._api_cache.cache.keys() if 'albums_' in str(k)]
            for k in keys_to_delete:
                del self.client._api_cache.cache[k]

        self.load_albums_page(reset=True)

    def go_to_root(self):
        self.status_label.show()
        if hasattr(self, 'search_container'):
            self.search_container.show()
            self.search_container.show_search()
            self.search_container.show_burger()
        
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
        if hasattr(self, 'search_container') and self.current_query:
            self.search_container.search_input.blockSignals(True)
            self.search_container.search_input.setText(self.current_query)
            self.search_container.search_input.blockSignals(False)
            
        # Update the sort icon in the burger menu
        if hasattr(self, 'update_burger_icon'):
            self.update_burger_icon()
            
        # Tell the next load cycle to respect our restored data
        self._restored_state_waiting = True