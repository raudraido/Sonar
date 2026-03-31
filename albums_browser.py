import time
import os
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
                          QEvent, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QAbstractListModel, QModelIndex, QByteArray, pyqtSlot, QObject, QUrl, Qt)
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


class GridBridge(QObject):
    itemClicked = pyqtSignal(dict)
    playClicked = pyqtSignal(dict)
    artistClicked = pyqtSignal(dict)
    artistNameClicked = pyqtSignal(str)
    visibleRangeChanged = pyqtSignal(int, int)
    accentColorChanged = pyqtSignal(str)
    bgAlphaChanged = pyqtSignal(float)
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
            self.scroll_timer = QTimer()
            self.scroll_timer.setSingleShot(True)
            self.scroll_timer.timeout.connect(lambda: self.visibleRangeChanged.emit(self.last_start, self.last_end))
            
        # 👇 🟢 THE THROTTLE FIX: Only start the timer if it isn't already counting down!
        if not self.scroll_timer.isActive():
            self.scroll_timer.start(150)
        
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

    @pyqtSlot(str)
    def emitArtistNameClicked(self, name):
        self.artistNameClicked.emit(name)

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

    def __init__(self):
        super().__init__()
        self.albums = []

    def rowCount(self, parent=QModelIndex()): return len(self.albums)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.albums[index.row()]
        if role == self.TITLE_ROLE: return a.get('title') or a.get('name') or 'Unknown'
        if role == self.ARTIST_ROLE: return a.get('artist', '')
        if role == self.YEAR_ROLE: return str(a.get('year', '')).replace('None', '')
        if role == self.COVER_ID_ROLE: return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return a 
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE: b"albumTitle", self.ARTIST_ROLE: b"albumArtist",
            self.YEAR_ROLE: b"albumYear", self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData"
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

class SongCountWorker(QThread):
    """Fetches all albums and sorts them by song count client-side."""
    page_ready = pyqtSignal(list, object)

    def __init__(self, client, total_count, ascending=True):
        super().__init__()
        self.client = client
        self.total_count = total_count
        self.ascending = ascending
        self.is_cancelled = False

    def run(self):
        try:
            if not self.client: return
            all_albums = []
            page_size = 500
            offset = 0
            while offset < self.total_count:
                if self.is_cancelled: return
                albums, _ = self.client.get_albums_live(sort_type='newest', size=page_size, offset=offset)
                if not albums:
                    break
                all_albums.extend(albums)
                offset += page_size
            all_albums.sort(key=lambda a: a.get('songCount', 0), reverse=not self.ascending)
            if not self.is_cancelled:
                self.page_ready.emit(all_albums, len(all_albums))
        except Exception as e:
            print(f"[SongCountWorker] Error: {e}")

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

    def run(self):
        with ThreadPoolExecutor(max_workers=_COVER_WORKERS) as executor:
            futures = []

            while self.running:
                try:
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
        self.master_color = QColor("#1db954") # Default color fallback
        
    def set_master_color(self, color):
        self.master_color = QColor(color)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = option.rect
        icon_width = rect.width() - 20 
        icon_height = icon_width 
        icon_x = rect.x() + 10
        icon_rect = QRect(icon_x, rect.y() + 10, icon_width, icon_height)
        
        path = QPainterPath()
        path.addRoundedRect(icon_rect.x(), icon_rect.y(), icon_rect.width(), icon_rect.height(), 10, 10)
        
        painter.save()
        painter.setClipPath(path)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull(): 
            pix = icon.pixmap(icon_width, icon_height)
            px = icon_rect.x() + (icon_width - pix.width()) // 2
            py = icon_rect.y() + (icon_height - pix.height()) // 2
            painter.drawPixmap(px, py, pix)
        
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver
        is_selected = option.state & QStyle.StateFlag.State_Selected
        
        if is_hovered or is_selected:
            painter.setBrush(QColor(0, 0, 0, 120))
            
            painter.setPen(QPen(self.master_color, 4)) 
            painter.drawPath(path) 
        painter.restore()

        if is_hovered or is_selected:
            center = icon_rect.center()
            play_size = min(60, icon_width // 2)
            play_rect = QRect(0, 0, play_size, play_size); play_rect.moveCenter(center)
            
            
            painter.setBrush(self.master_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(play_rect)
            
            tri_size = play_size // 3
            p1 = QPoint(center.x() - tri_size // 3, center.y() - tri_size // 2)
            p2 = QPoint(center.x() - tri_size // 3, center.y() + tri_size // 2)
            p3 = QPoint(center.x() + tri_size // 2 + 2, center.y())
            
            painter.setBrush(QColor("#111111")) 
            painter.drawPolygon(QPolygon([p1, p2, p3]))

        data = index.data(Qt.ItemDataRole.UserRole)
        if data:
            title = data.get('title') or data.get('name') or "Unknown"
            artist = data.get('artist', '')
            year = str(data.get('year', ''))

            text_width = rect.width() - 20; text_x = rect.x() + 10; current_y = icon_rect.bottom() + 10
            
           
            text_color = self.master_color.name() if (is_selected or is_hovered) else "#eeeeee"
            
            painter.setPen(QColor(text_color))
            font = painter.font(); font.setBold(True); font.setPointSize(10); painter.setFont(font)
            fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, 20), Qt.AlignmentFlag.AlignLeft, fm.elidedText(title, Qt.TextElideMode.ElideRight, text_width))
            
            current_y += 20 
            painter.setPen(QColor("#cccccc")); font.setBold(False); font.setPointSize(9); painter.setFont(font); fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, 20), Qt.AlignmentFlag.AlignLeft, fm.elidedText(artist, Qt.TextElideMode.ElideRight, text_width))
            
            current_y += 18
            painter.setPen(QColor("#999999"))
            painter.drawText(QRect(text_x, current_y, text_width, 20), Qt.AlignmentFlag.AlignLeft, fm.elidedText(year, Qt.TextElideMode.ElideRight, text_width))

        painter.restore()

class ClickableArtistLabel(QWidget):
    artist_clicked = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.artists = []
        self.artist_rects = []
        self.hovered_artist = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(20)
        self.setMaximumHeight(30)
        
    def setText(self, text):
        if not text or text == "Loading...":
            self.artists = [text] if text else []
        else:
            self.artists = [a.strip() for a in text.split(" • ") if a.strip()]
        self.artist_rects = []
        self.updateGeometry()
        self.update()
    
    def text(self):
        return " • ".join(self.artists) if self.artists else ""
    
    def sizeHint(self):
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        fm = QFontMetrics(font)
        text = self.text()
        width = fm.horizontalAdvance(text) if text else 100
        height = fm.height() + 4
        return QSize(width, height)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        x = 0
        y = fm.ascent() + 2
        self.artist_rects = []
        for i, artist in enumerate(self.artists):
            is_hovered = (self.hovered_artist == artist)
            painter.setPen(QColor("#cccccc"))
            font.setUnderline(is_hovered)
            painter.setFont(font)
            width = fm.horizontalAdvance(artist)
            rect = QRect(x, 0, width, self.height())
            self.artist_rects.append((artist, rect))
            painter.drawText(x, y, artist)
            x += width
            if i < len(self.artists) - 1:
                painter.setPen(QColor("#777"))
                font.setUnderline(False)
                painter.setFont(font)
                separator = " • "
                painter.drawText(x, y, separator)
                x += fm.horizontalAdvance(separator)
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
            pos = event.pos()
            for artist, rect in self.artist_rects:
                if rect.contains(pos):
                    self.artist_clicked.emit(artist)
                    break
    
    def leaveEvent(self, event):
        if self.hovered_artist is not None:
            self.hovered_artist = None
            self.update()

class AlbumDetailView(QWidget):
    play_clicked = pyqtSignal()
    shuffle_clicked = pyqtSignal()
    album_favorite_toggled = pyqtSignal(bool)
    artist_clicked = pyqtSignal(str)
    _meta_ready = pyqtSignal(str, str) 
    def __init__(self, client=None):
        super().__init__()
        self.client = client 
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")
        
        # 1. MASTER LAYOUT & SCROLL AREA
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QWidget#ScrollContent { background: transparent; }
        """)
        self.omni_scroller = MiddleClickScroller(self.scroll_area)
        
        # 2. SCROLL CONTENT WIDGET
        self.content_widget = QWidget()
        self.content_widget.setObjectName("ScrollContent")
        self.content_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(20)

        # ─── HEADER ────────────────────────────────────────────────────────────
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(25)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(220, 220)
        self.cover_label.setStyleSheet("background-color: #222; border-radius: 8px; border: 1px solid #333;")
        self.cover_label.setScaledContents(True)

        meta_container = QWidget()
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 10, 0, 10)
        meta_layout.setSpacing(5)

        self.lbl_type = QLabel("ALBUM")
        self.lbl_type.setStyleSheet("color: #ddd; font-weight: bold; font-size: 11px;")

        self.lbl_title = QLabel("Album Title")
        self.lbl_title.setStyleSheet("color: white; font-weight: 900; font-size: 36px;")
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
        btn_layout.setSpacing(15)
        
        # --- ALBUM VIEW PLAY BUTTON ---
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(60, 60) 
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        self.btn_play.setIcon(QIcon(resource_path("img/play.png")))
        self.btn_play.setIconSize(QSize(15, 15)) # Slightly smaller icon to fit the 40px button
        self.btn_play.clicked.connect(self.play_clicked.emit)
        
        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setFixedSize(40, 40)
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        self.btn_shuffle.setStyleSheet("QPushButton { outline: none; background: transparent; border: 2px solid #555; border-radius: 20px; } QPushButton:hover { border-color: white; }")
        self.btn_shuffle.clicked.connect(self.shuffle_clicked.emit)
        
        self.btn_like = QPushButton("♡")
        self.btn_like.setFixedSize(40, 40)
        self.btn_like.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_like.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        self.btn_like.setStyleSheet("QPushButton { outline: none; background: transparent; color: #aaa; font-size: 24px; border: 2px solid #555; border-radius: 20px; } QPushButton:hover { border-color: white; color: white; }")
        self.btn_like.clicked.connect(self.toggle_header_heart)
        
        btn_layout.addWidget(self.btn_play)
        btn_layout.addWidget(self.btn_shuffle) 
        btn_layout.addWidget(self.btn_like)
        btn_layout.addStretch()
        
        meta_layout.addWidget(self.lbl_type)
        meta_layout.addWidget(self.lbl_title)
        meta_layout.addWidget(self.lbl_meta)
        meta_layout.addWidget(self.lbl_artist)
        meta_layout.addWidget(btn_row)
        meta_layout.addStretch()
        
        header_layout.addWidget(self.cover_label)
        header_layout.addWidget(meta_container)
        
        self.layout.addWidget(header_container)

        # ─── TRACKS BROWSER ───────────────────────────────────────────────────
        from tracks_browser import TracksBrowser
        self.track_list = TracksBrowser(client)
        
        # Activate the new compact mode for albums!
        if hasattr(self.track_list, 'set_album_mode'):
            self.track_list.set_album_mode(True)
            
        self.layout.addWidget(self.track_list)
        self.layout.addStretch() 
        
        self.scroll_area.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll_area)
        

        self.track_list.tree.installEventFilter(self)
        
    def eventFilter(self, source, event):
        if source is self.track_list.tree and event.type() == QEvent.Type.KeyPress:
            from PyQt6.QtCore import Qt, QTimer
            
            
            if event.text() == "/":
                # 1. Pan the camera to the absolute top of the album header
                self.scroll_area.verticalScrollBar().setValue(0)
                
                # 2. Visually expand the search box and force the text cursor inside it
                if hasattr(self.track_list, 'search_container'):
                    self.track_list.search_container.show_search()
                    # A tiny 50ms delay guarantees the blinking cursor grabs focus after the UI updates!
                    QTimer.singleShot(50, self.track_list.search_container.search_input.setFocus)
                    
                # 3. Consume the key so the "/" doesn't actually get typed into the search box!
                return True 
            
            key = event.key()
            
            # SHIFT+ENTER: Play the entire album
            if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.btn_play.animateClick()
                return True  
                
            # THE CAMERA TRACKER & SCI-FI EDGE SCROLL
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                tree = self.track_list.tree
                old_item = tree.currentItem()
                old_idx = tree.indexOfTopLevelItem(old_item) if old_item else -1
                
                # Manually step Page Up/Down because the native tree is stretched infinitely!
                if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    jump_size = 12 
                    if key == Qt.Key.Key_PageDown:
                        target_idx = min(tree.topLevelItemCount() - 1, old_idx + jump_size)
                    else:
                        target_idx = max(0, old_idx - jump_size)
                        
                    if target_idx >= 0:
                        tree.setCurrentItem(tree.topLevelItem(target_idx))
                
                def check_scroll():
                    new_item = tree.currentItem()
                    new_idx = tree.indexOfTopLevelItem(new_item) if new_item else -1
                    
                    if old_idx == new_idx:
                        # BOUNDARY HIT: We hit the ceiling or floor!
                        if key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp) and new_idx <= 0:
                            self.scroll_area.verticalScrollBar().setValue(0)
                            return
                        elif key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown) and new_idx >= tree.topLevelItemCount() - 1:
                            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
                            return
                    
                    if new_item:
                        rect = tree.visualItemRect(new_item)
                        pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                        self.scroll_area.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                        
                if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    check_scroll()
                    return True
                    
                QTimer.singleShot(0, check_scroll)
                return False
                
        return super().eventFilter(source, event)

    def adjust_tree_height(self):
        try:
            tree = self.track_list.tree
            count = tree.topLevelItemCount()
            if count == 0: return

            header_h = tree.header().height()
            rows_h = count * 75
            natural_h = header_h + rows_h + 10

            # Cap at 800px so Qt never allocates a backing buffer for 25 000+px.
            # The outer QScrollArea already scrolls the page; the tree uses its
            # own scrollbar only when it has more rows than the cap allows.
            MAX_TREE_H = 800
            capped_h = min(natural_h, MAX_TREE_H)

            tree.setMinimumHeight(capped_h)
            tree.setMaximumHeight(natural_h)     # allow expansion up to full height
            self.track_list.setMinimumHeight(capped_h + 10)
            self.track_list.setMaximumHeight(natural_h + 60)
        except Exception:
            pass

    def toggle_header_heart(self):
        is_liked = self.btn_like.text() == "♥"
        new_state = not is_liked
        self.set_header_heart_state(new_state)
        self.album_favorite_toggled.emit(new_state)

    def set_header_heart_state(self, is_liked):
        if is_liked:
            self.btn_like.setText("♥")
            self.btn_like.setStyleSheet("QPushButton { background: transparent; color: #E91E63; font-size: 24px; border: 2px solid #E91E63; border-radius: 20px; } QPushButton:hover { border-color: #ff4081; color: #ff4081; }")
        else:
            self.btn_like.setText("♡")
            self.btn_like.setStyleSheet("QPushButton { background: transparent; color: #aaa; font-size: 24px; border: 2px solid #555; border-radius: 20px; } QPushButton:hover { border-color: white; color: white; }")
    
    def set_accent_color(self, color, alpha=0.3):
        self.track_list.set_accent_color(color, alpha)
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")
        
        # Dynamically style the Album play button with the Master Color!
        play_btn_style = f"""
            QPushButton {{ 
                background-color: {color}; 
                border-radius: 30px; 
                border: none; 
            }} 
            QPushButton:hover {{ 
                background-color: white; 
            }}
        """
        self.btn_play.setStyleSheet(play_btn_style)
        
        
        scrollbar_style = f"""
            QScrollArea {{ background: transparent; border: none; }}
            QWidget#ScrollContent {{ background: transparent; }}
            QScrollBar:vertical {{ border: none; background: rgba(0, 0, 0, 0.05); width: 10px; margin: 0; }} 
            QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }} 
            QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {color}; }} 
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }} 
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }} 
            QScrollBar:horizontal {{ height: 0px; }}
        """
        self.scroll_area.setStyleSheet(scrollbar_style)
        
        
        icon_path = resource_path("img/shuffle.png")
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            colored = QPixmap(pixmap.size())
            colored.fill(QColor(0,0,0,0))
            painter = QPainter(colored)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(colored.rect(), QColor(color))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            
            self.btn_shuffle.setIcon(QIcon(colored))
            self.btn_shuffle.setIconSize(QSize(22, 22))
    
    def load_album(self, album_data):
        self.current_album_id = album_data.get('id')
        title = album_data.get('title') or album_data.get('name') or "Unknown Album"
        album_artist = album_data.get('albumArtist') or album_data.get('album_artist') or album_data.get('artist') or "Unknown Artist"
        
        self.lbl_title.setText(title)
        self.lbl_artist.setText(album_artist)
        self.lbl_meta.setText("Loading...")
        
        # 1. TRUTH CHECK FOR COMPILATIONS & META — fetch live from API in background
        if hasattr(self, 'client') and self.client:
            import threading, re
            _album_id = self.current_album_id
            _album_data = album_data
            
            def _fetch_meta():
                try:
                    import copy
                    
                    # Calculate the metadata so we can run it on both Stale and Fresh data!
                    def compute_meta(tracks):
                        if not tracks: return "", "Ready."
                        found_aa = None
                        artist_counts = {}
                        total_sec = 0
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
                        if m >= 60:
                            h2, m2 = divmod(m, 60)
                            time_str = f"{h2} hr {m2} min"
                        else:
                            time_str = f"{m} min {s} sec"
                            
                        year = str(_album_data.get('year', '')).replace('None', '')
                        meta_parts = [p for p in [year, f"{n} songs", time_str] if p]
                        return detected, " • ".join(meta_parts)

                    # ─────────────────────────────────────────────────────────
                    # PHASE 1: THE INSTANT "STALE" LOAD
                    # ─────────────────────────────────────────────────────────
                    raw_cached = self.client.get_album_tracks(_album_id)
                    cached_detected, cached_meta = compute_meta(raw_cached)
                    
                    # Instantly draw the UI header with cached math!
                    self._meta_ready.emit(cached_detected, cached_meta)
                    
                    # ─────────────────────────────────────────────────────────
                    # PHASE 2 & 3: BACKGROUND WIPE & SEAMLESS SWAP
                    # ─────────────────────────────────────────────────────────
                    try:
                        # 1. Brutal Wipe to force Navidrome to give us fresh data
                        try: self.client.reset_caches()
                        except: pass
                        try: self.client._api_cache.cache.clear()
                        except: pass
                        try:
                            if hasattr(self.client, 'session') and hasattr(self.client.session, 'cache'):
                                self.client.session.cache.clear()
                        except: pass
                        try:
                            for attr_name in dir(self.client):
                                attr = getattr(self.client, attr_name)
                                if callable(attr) and hasattr(attr, 'cache_clear'): attr.cache_clear()
                        except: pass
                        
                        # 2. Fetch fresh tracks and recalculate
                        raw_fresh = self.client.get_album_tracks(_album_id)
                        if not raw_fresh: return
                        
                        fresh_detected, fresh_meta = compute_meta(raw_fresh)
                        
                        # 3. THE SWAP: If a track was added/removed, or duration changed, redraw the UI!
                        if fresh_meta != cached_meta or fresh_detected != cached_detected:
                            self._meta_ready.emit(fresh_detected, fresh_meta)
                            
                    except Exception as e:
                        print(f"[AlbumDetailView] Silent background sync failed: {e}")

                except Exception as e:
                    print(f"[AlbumDetailView] Meta fetch error: {e}")
                    self._meta_ready.emit("", "Ready.")



            threading.Thread(target=_fetch_meta, daemon=True).start()
            
        is_fav = album_data.get('starred', False) or album_data.get('favorite', False)
        self.set_header_heart_state(is_fav)
        
        # 3. FAST COVER ART
        from PyQt6.QtGui import QPixmap
        self.cover_label.setPixmap(QPixmap())
        cid = album_data.get('cover_id') or album_data.get('coverArt') or album_data.get('id')
        if cid:
            from cover_cache import CoverCache
            data = CoverCache.instance().get_full(cid) or CoverCache.instance().get_thumb(cid)
            if data:
                pix = QPixmap()
                pix.loadFromData(data)
                self.cover_label.setPixmap(pix)
            else:
                import threading
                def fetch():
                    if not getattr(self, 'client', None): return
                    try:
                        d = self.client.get_cover_art(cid, size=800)
                        if d:
                            CoverCache.instance().save_full(cid, d)
                            from PyQt6.QtCore import QTimer
                            QTimer.singleShot(0, lambda: self.update_cover(d))
                    except: pass
                threading.Thread(target=fetch, daemon=True).start()

        # 4. LOAD TRACKS
        # (Because we injected them into the DB above, this will now load them instantly!)
        self.track_list.load_album_view(self.current_album_id)
        self.track_list.tree.setFocus(Qt.FocusReason.OtherFocusReason)

    def update_cover(self, data):
        from PyQt6.QtGui import QPixmap
        pix = QPixmap()
        pix.loadFromData(data)
        self.cover_label.setPixmap(pix)

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
    album_clicked = pyqtSignal(dict)
    
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.last_reload_time = time.time()

        # 1: Add the master opacity box to the entire tab!
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")

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
        self.status_label.setStyleSheet("color: #888; font-weight: bold; background: transparent; border: none;")
        
        
        self.sort_states = {
            'random': True,
            'latest': True,
            'alphabetical': True,
            'favorites': True,
            'song_count': True,
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
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)  # required for transparent QQuickWidget
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.qml_view.setClearColor(Qt.GlobalColor.transparent)
        self.qml_view.setStyleSheet("background: transparent; border: none;")
        
        self.album_model = AlbumModel()
        self.grid_bridge = GridBridge(self.album_model)

        self.grid_bridge.itemClicked.connect(self.album_clicked.emit)
        self.grid_bridge.playClicked.connect(lambda data: self.start_play_fetch(data['id']))
        self.grid_bridge.visibleRangeChanged.connect(self.check_viewport_qml)
        self.grid_bridge.artistClicked.connect(self.on_grid_artist_clicked)
        self.grid_bridge.artistNameClicked.connect(self.switch_to_artist_tab.emit)

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
        
        self.grid_view = self.qml_view 
        self.stack.addWidget(self.grid_view)
        
                
      
        self.detail_view = AlbumDetailView(self.client)
        
        
        self.detail_view.track_list.play_track.connect(self.play_track_signal.emit)
        self.detail_view.track_list.play_multiple_tracks.connect(self.play_album_signal.emit)
        self.detail_view.track_list.queue_track.connect(self.queue_track_signal.emit)
        self.detail_view.track_list.play_next.connect(self.play_next_signal.emit)
        self.detail_view.track_list.switch_to_artist_tab.connect(self.switch_to_artist_tab.emit)
        self.detail_view.track_list.switch_to_album_tab.connect(self.album_clicked.emit)

        # Wire up the header buttons
        self.detail_view.play_clicked.connect(self.on_play_all_clicked)
        self.detail_view.shuffle_clicked.connect(self.on_shuffle_album_clicked)
        self.detail_view.album_favorite_toggled.connect(self.on_album_heart_clicked)
        self.detail_view.artist_clicked.connect(self.switch_to_artist_tab.emit)
        
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
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a1a;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                color: #ddd;
                padding: 8px 12px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #333;
            }
            QMenu::icon {
                padding-left: 4px;
            }
        """)
        
        # Create actions for each sort type
        self.create_sort_action(menu, 'random', 'Random')
        self.create_sort_action(menu, 'latest', 'Latest')
        self.create_sort_action(menu, 'alphabetical', 'Alphabetical')
        self.create_sort_action(menu, 'song_count', 'Song Count')

        menu.addSeparator()

        # Favorites filter (heart icon, no asc/desc)
        heart_icon = self._get_tinted_icon(resource_path("img/heart.png"))
        fav_action = QAction(heart_icon, "  Favourites", self)
        fav_action.triggered.connect(lambda: self.toggle_sort_state('favorites'))
        menu.addAction(fav_action)
        
        # Show menu below burger button
        button_pos = self.burger_btn.mapToGlobal(self.burger_btn.rect().bottomLeft())
        menu.exec(button_pos)

    def update_cover(self, data):
        from PyQt6.QtGui import QPixmap
        pix = QPixmap()
        pix.loadFromData(data)
        self.cover_label.setPixmap(pix)
    
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
        if sort_type == 'favorites':
            # Favorites is a pure filter — no asc/desc, just activate it
            self.current_sort = 'favorites'
        elif self.current_sort == sort_type:
            # If clicking the currently active sort, flip its direction
            self.sort_states[sort_type] = not self.sort_states[sort_type]
        else:
            # If switching to a NEW sort, make it active and set to default direction
            self.current_sort = sort_type
            self.sort_states[sort_type] = True
            
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
        
        # Favorites uses its own icon
        if self.current_sort == 'favorites':
            self.burger_btn.setIcon(self._get_tinted_icon(resource_path("img/heart.png")))
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
        
        self.status_label.setText(f"{display_count:,} albums")
        self.populate_grid(albums)
        
        if hasattr(self, 'qml_view') and self.isVisible():
            search_input = getattr(getattr(self, 'search_container', None), 'search_input', None)
            if search_input is None or not search_input.hasFocus():
                self.qml_view.setFocus()

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
        
        # Delegate ALL the heavy lifting to the AlbumDetailView!
        # It already has the perfect math for track lengths, artist counting, and cover fetching.
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

    def on_play_all_clicked(self):
        if not self.current_album_id: return
        if not getattr(self, 'client', None): return
        try:
            tracks = self.client.get_album_tracks(str(self.current_album_id))
            if tracks:
                self.play_album_signal.emit(tracks)
        except Exception as e:
            print(f"Error fetching album tracks: {e}")

    def set_accent_color(self, color, alpha=0.3):
        if getattr(self, 'current_accent', None) == color and getattr(self, 'current_alpha', None) == alpha:
            return
            
        self.current_accent = color
        self.current_alpha = alpha

        # Force Python to paint the darkness so the GPU clears its old frames!
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")

        # Broadcast BOTH the color and the opacity directly to the QML Engine!
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(alpha)
            
        # Update the detail view behind the scenes
        if hasattr(self, 'detail_view'):
            self.detail_view.set_accent_color(color, alpha)
        
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
                    self.album_model.albums[i] = {'type': 'placeholder', 'title': 'Loading...'}
                
                # Tell QML the chunks were wiped to save RAM
                self.album_model.dataChanged.emit(
                    self.album_model.index(chunk_start, 0), 
                    self.album_model.index(chunk_end - 1, 0),
                    [self.album_model.TITLE_ROLE, self.album_model.ARTIST_ROLE,
                    self.album_model.YEAR_ROLE, self.album_model.COVER_ID_ROLE]
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
        if hasattr(self, 'active_chunk_workers') and chunk_index in self.active_chunk_workers:
            worker = self.active_chunk_workers.pop(chunk_index)
            self._safe_discard_worker(worker)
            
        if not albums: return
        start_row = chunk_index * 50
        covers_to_queue = []
        
        for i, album_data in enumerate(albums):
            target_row = start_row + i
            if target_row >= len(self.album_model.albums): break
            
            cid = album_data.get('cover_id') or album_data.get('coverArt') or album_data.get('id')
            if cid:
                album_data['cover_id'] = cid
                covers_to_queue.append(cid)
                
            self.album_model.albums[target_row] = album_data
            
        self.album_model.dataChanged.emit(
            self.album_model.index(start_row, 0), 
            self.album_model.index(start_row + len(albums) - 1, 0),
            [self.album_model.TITLE_ROLE, self.album_model.ARTIST_ROLE,
            self.album_model.YEAR_ROLE, self.album_model.COVER_ID_ROLE]
        )
        
        if hasattr(self, 'cover_worker') and self.cover_worker:
            for cid in reversed(covers_to_queue):
                self.cover_worker.queue_cover(cid, priority=True)

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
            worker = LivePageWorker(self.client, sort_type='newest', size=500, offset=0, query=query)
            worker.page_ready.connect(self._on_search_loaded)
            worker.start()
            self.live_worker = worker
            return

        if not hasattr(self, 'true_server_count') or self.true_server_count == 0:
            api_sort = 'newest'
            if getattr(self, 'current_sort', 'latest') == 'alphabetical': api_sort = 'alphabeticalByName'
            elif getattr(self, 'current_sort', 'latest') == 'random': api_sort = 'random'
            elif getattr(self, 'current_sort', 'latest') == 'favorites': api_sort = 'starred'
            # song_count uses 'newest' for count fetching, sorting happens client-side
            worker = LivePageWorker(self.client, sort_type=api_sort, size=1, offset=0, query="")
            worker.page_ready.connect(self._on_initial_count_loaded)
            worker.start()
            self.live_worker = worker
            return

        self.status_label.setText(f"{self.true_server_count:,} albums")

        if getattr(self, 'current_sort', 'latest') == 'song_count' and self.true_server_count > 0:
            is_ascending = self.sort_states.get('song_count', True)
            worker = SongCountWorker(self.client, self.true_server_count, ascending=is_ascending)
            worker.page_ready.connect(self._on_search_loaded)
            worker.start()
            self.live_worker = worker
            return

        if self.true_server_count > 0:
            placeholders = [{'type': 'placeholder', 'title': 'Loading...'} for _ in range(self.true_server_count)]
            self.album_model.clear()
            self.album_model.append_albums(placeholders)
            self.check_viewport_qml(0, 50)

    def fetch_chunk(self, chunk_index):
        """Fires a background worker and returns it so we can cancel it if needed."""
        api_sort = 'newest'
        if getattr(self, 'current_sort', 'latest') == 'alphabetical': api_sort = 'alphabeticalByName'
        elif getattr(self, 'current_sort', 'latest') == 'random': api_sort = 'random'
        elif getattr(self, 'current_sort', 'latest') == 'favorites': api_sort = 'starred'
        
        query = getattr(self, 'current_query', '')
        
        # Check the UI's Ascending/Descending state!
        is_ascending = self.sort_states.get(getattr(self, 'current_sort', 'latest'), True)
        
        offset = chunk_index * 50
        size = 50
        
        # If descending, we have to read chunks from the END of the server's list backwards!
        if not is_ascending and getattr(self, 'true_server_count', 0) > 0:
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
            
            
            self.count_worker = ServerCountWorker(client)
            self.count_worker.count_ready.connect(self.update_server_count_ui)
            self.count_worker.start()
            
            self.refresh_grid()
            
        if hasattr(self, 'detail_view') and hasattr(self.detail_view, 'track_list'):
            self.detail_view.client = client 
            self.detail_view.track_list.client = client

    def update_server_count_ui(self, true_server_count):
        """Updates the status label with the instant server count."""
        self.true_server_count = true_server_count
        
        # Only update the base label if we are NOT currently searching
        if not getattr(self, 'current_query', ''):
            self.status_label.setText(f"{self.true_server_count:,} albums")
            
        # If we are in descending mode but had 0 items when we loaded, trigger a reload now that we have the count!
        is_ascending = self.sort_states.get(getattr(self, 'current_sort', 'latest'), True)
        if not is_ascending and getattr(self, 'total_items', 0) == 0:
            self.total_items = self.true_server_count
            self.load_albums_page(reset=True)
    
    def refresh_grid(self):
        # 1. Reset the server count so we ask the server exactly how many albums exist right now!
        self.true_server_count = 0
        
        # 2. Brutally wipe the API cache so we don't just reload the old albums from memory!
        if hasattr(self, 'client') and self.client and hasattr(self.client, '_api_cache'):
            # 👇 THE FIX: Search for 'albums_', not 'getAlbumList2'!
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
            'query': getattr(self, 'current_query', '')
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