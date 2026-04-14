import os
import json
import random
import math
import re
from collections import OrderedDict
import time
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget, 
                             QListWidgetItem, QPushButton, QStackedWidget, QApplication,
                             QLabel, QScrollArea, QSizePolicy, QFrame, QGridLayout,
                             QTreeWidgetItem, QTreeWidget, QHeaderView, QAbstractItemView, QComboBox,
                             QLineEdit, QToolButton, QMenu, QStyledItemDelegate, QStyle, QAbstractButton)

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QPoint, QRect, QRectF, QThread, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QEvent, QAbstractListModel, QModelIndex, QByteArray, pyqtSlot, QObject, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QColor, QCursor, QPainter, QFont, QAction, QBrush, QPainterPath, QPen, QFontMetrics, QPolygon
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider

from albums_browser import (AlbumDetailView, GridItemDelegate, GridCoverWorker, resource_path,
                              CoverImageProvider, QMLGridWrapper, DummyScrollBar,
                              QMLMiddleClickScroller, AlbumModel)

from components import PaginationFooter, SmartSearchContainer
from tracks_browser import MiddleClickScroller


class ArtistPlayWorker(QThread):
    """Fetches all tracks for an artist and emits them sorted for playback."""
    tracks_ready = pyqtSignal(list)

    def __init__(self, client, name):
        super().__init__()
        self.client = client
        self.name = name

    def run(self):
        try:
            raw_tracks = self.client.search_artist_tracks(self.name)
            target = self.name.lower().strip()
            
            
            _split_re = re.compile(r'(?: /// | • | / | feat\. | Feat\. | ft\. | Ft\. | vs\. | Vs\. )')

            def _tokens(s):
                return {p.strip().lower() for p in _split_re.split(s) if p.strip()}

            filtered = []
            for t in raw_tracks:
                artist_tokens = _tokens(str(t.get('artist') or ''))
                alb_artist_tokens = _tokens(str(t.get('albumArtist') or t.get('album_artist') or ''))
                
                if target in artist_tokens or target in alb_artist_tokens:
                    filtered.append(t)

            filtered.sort(key=lambda x: (x.get('album', ''), int(x.get('discNumber', 1)), int(x.get('trackNumber', 0))))
            self.tracks_ready.emit(filtered)
        except Exception as e:
            print(f"Error: {e}")
            self.tracks_ready.emit([])

class LiveArtistDetailWorker(QThread):
    # Emits: (info_dict, top_songs_list, main_albums, singles, appears_on)
    details_ready = pyqtSignal(dict, list, list, list, list)

    def __init__(self, client, artist_id, artist_name):
        super().__init__()
        self.client = client
        self.artist_id = artist_id
        self.artist_name = artist_name

    def run(self):
        try:
            if not self.client: return

            # 1. Resolve ID if missing (for track-only artists)
            if not self.artist_id and self.artist_name:
                search_results = self.client.search_artist_tracks(self.artist_name)
                target_name = self.artist_name.lower().strip()
                for item in search_results:
                    aid = item.get('artistId') or item.get('artist_id')
                    t_art = str(item.get('artist', '')).lower().strip()
                    a_art = str(item.get('albumArtist', '')).lower().strip()
                    if (t_art == target_name or a_art == target_name) and aid:
                        self.artist_id = aid
                        break

            # 2. Fetch Info & Top Songs
            info = {}
            if self.artist_id:
                info = self.client.get_artist(self.artist_id) or {}
                # Merge Last.fm biography from getArtistInfo2 if not already present
                if 'biography' not in info and hasattr(self.client, 'get_artist_info2'):
                    try:
                        extra = self.client.get_artist_info2(self.artist_id) or {}
                        bio = extra.get('biography') or extra.get('bio') or ''
                        if bio:
                            info['biography'] = bio
                        similar = extra.get('similarArtist') or []
                        if isinstance(similar, dict):
                            similar = [similar]
                        if similar:
                            info['similar_artists'] = similar
                    except Exception:
                        pass

            if not info:
                info = {'name': self.artist_name or "Unknown"}

            top_songs = []
            if self.artist_name:
                top_songs = self.client.get_top_songs(self.artist_name, count=5)

            # 3. Categorize own albums (getArtist only returns albums where artist IS album artist)
            raw_albums = info.get('album', [])
            main_albums, singles = [], []
            own_album_ids = set()
            target_lower = (self.artist_name or info.get('name', '')).lower().strip()

            for a in raw_albums:
                aid_str = str(a.get('id', ''))
                if aid_str:
                    own_album_ids.add(aid_str)
                # Navidrome returns releaseTypes as an array e.g. ["Single"], ["EP"], ["Album"]
                # Fall back to singular string fields for older servers
                rtypes_raw = a.get('releaseTypes') or []
                if isinstance(rtypes_raw, list):
                    rtype = ' '.join(rtypes_raw).lower()
                else:
                    rtype = str(rtypes_raw).lower()
                if not rtype:
                    rtype = str(a.get('albumType') or a.get('releaseType') or a.get('type') or '').lower()
                if 'single' in rtype or 'ep' in rtype:
                    singles.append(a)
                else:
                    main_albums.append(a)

            # 4. Discover "appears on" albums via track search
            appears_on = []
            appears_on_ids = set()

            # Splits on common multi-artist separators to get individual artist tokens
            _split_re = re.compile(r'(?: /// | • | / | feat\. | Feat\. | ft\. | Ft\. | vs\. | Vs\. )')

            def _artist_tokens(raw: str) -> set:
                """Return a set of lowercased, stripped artist name tokens from a raw field."""
                parts = _split_re.split(raw)
                return {p.strip().lower() for p in parts if p.strip()}

            try:
                all_tracks = self.client.search_artist_tracks(self.artist_name) if self.artist_name else []
                for t in all_tracks:
                    alb_id = str(t.get('albumId') or t.get('album_id') or '')
                    if not alb_id or alb_id in own_album_ids or alb_id in appears_on_ids:
                        continue

                    track_artist_raw = str(t.get('artist') or '')
                    alb_artist_raw   = str(t.get('albumArtist') or t.get('album_artist') or '')

                    track_tokens = _artist_tokens(track_artist_raw)
                    alb_tokens   = _artist_tokens(alb_artist_raw)

                    # Skip if the target IS the album artist — those belong in main_albums/singles
                    if target_lower in alb_tokens:
                        continue

                    # Only include if the target appears as an exact artist token on this track
                    # This prevents "KiNK" matching "The Kinks", "Kinkisin lilli", etc.
                    if target_lower not in track_tokens:
                        continue

                    appears_on_ids.add(alb_id)
                    appears_on.append({
                        'id': alb_id,
                        'title': t.get('album') or 'Unknown Album',
                        'artist': t.get('albumArtist') or t.get('album_artist') or t.get('artist') or 'Unknown Artist',
                        'albumArtist': alb_artist_raw,
                        'year': str(t.get('year') or ''),
                        'coverArt': t.get('coverArt') or alb_id,
                        'cover_id': t.get('coverArt') or alb_id,
                    })
            except Exception as e:
                print(f"[LiveArtistDetailWorker] appears_on search failed: {e}")

            # Sort all sections by year descending
            def sort_by_year(x):
                try: return int(x.get('year', 0) or 0)
                except: return 0

            main_albums.sort(key=sort_by_year, reverse=True)
            singles.sort(key=sort_by_year, reverse=True)
            appears_on.sort(key=sort_by_year, reverse=True)

            self.details_ready.emit(info, top_songs, main_albums, singles, appears_on)

        except Exception as e:
            print(f"[LiveArtistDetailWorker] Error: {e}")
            self.details_ready.emit({}, [], [], [], [])

class LiveArtistWorker(QThread):
    page_ready = pyqtSignal(list, int, int)

    def __init__(self, client, query, sort_type, is_ascending, page, page_size, random_seed=0):
        super().__init__()
        self.client = client
        self.query = query.lower().strip() if query else ""
        self.sort_type = sort_type
        self.is_ascending = is_ascending
        self.page = page
        self.page_size = page_size
        self.random_seed = random_seed
        self.is_cancelled = False

    def run(self):
        try:
            import math
            if not self.client: return

            if hasattr(self.client, 'get_artists_native_page') and self.sort_type != 'random' and not self.query:
                # BROWSE MODE (no query): use native API for fast server-side sort + pagination
                native_sort = "name"
                if self.sort_type == 'albums_count': native_sort = "albumCount"
                elif self.sort_type == 'most_played': native_sort = "playCount"
                
                native_order = "ASC" if self.is_ascending else "DESC"
                start = (self.page - 1) * self.page_size
                end = start + self.page_size
                
                page_items, total_items = self.client.get_artists_native_page(
                    sort_by=native_sort, order=native_order, start=start, end=end, query=""
                )
                
                if self.is_cancelled: return
                
                total_pages = max(1, math.ceil(total_items / self.page_size))
                self.page_ready.emit(page_items, total_items, total_pages)
                return
            
            # SEARCH MODE (query present) or random sort: fetch all, filter locally, paginate
            # The native API's _q param is unreliable -- only page 1 appears correct because
            # populate_grid re-filters client-side; pages 2+ return unfiltered server data.
            artists = self.client.get_artists_live()
            filtered = [a for a in artists if self.query in a.get('name', '').lower()] if self.query else list(artists)
            import random
            rnd = random.Random(self.random_seed)
            if self.sort_type == 'random':
                rnd.shuffle(filtered)
            elif self.sort_type == 'albums_count':
                filtered.sort(key=lambda a: int(a.get('albumCount', 0) or 0), reverse=not self.is_ascending)
            elif self.sort_type == 'most_played':
                filtered.sort(key=lambda a: int(a.get('playCount', 0) or 0), reverse=not self.is_ascending)
            else:  # alphabetical
                filtered.sort(key=lambda a: (a.get('name') or '').lower(), reverse=not self.is_ascending)
            
            if self.query:
                def get_relevance(a):
                    name_lower = (a.get('name') or '').lower().strip()
                    
                    if name_lower == self.query:
                        return 0  # 1st Tier: Exact Match ("Jam")
                    if name_lower.startswith(self.query + " "):
                        return 1  # 2nd Tier: Starts with exact word ("Jam & Spoon")
                    if name_lower.startswith(self.query):
                        return 2  # 3rd Tier: Starts with substring ("James", "Jamie")
                    if f" {self.query} " in f" {name_lower} ":
                        return 3  # 4th Tier: Contains as whole word ("Pearl Jam")
                    return 4      # 5th Tier: Contains as substring ("Benjamin")
                
                filtered.sort(key=get_relevance)
            
            total_items = len(filtered)
            total_pages = max(1, math.ceil(total_items / self.page_size))
            offset = (min(self.page, total_pages) - 1) * self.page_size
            
            if self.is_cancelled: return
            
            self.page_ready.emit(filtered[offset : offset + self.page_size], total_items, total_pages)
            
        except Exception as e:
            print(f"[LiveArtistWorker] Error: {e}")
            self.page_ready.emit([], 0, 1)

class TrackLoaderWorker(QThread):
    """Fetches the track list for a single album in the background."""
    tracks_ready = pyqtSignal(list, str)   # (tracks, album_id)

    def __init__(self, client, album_id):
        super().__init__()
        self.client   = client
        self.album_id = str(album_id)

    def run(self):
        try:
            tracks = self.client.get_album_tracks(self.album_id)
            self.tracks_ready.emit(tracks or [], self.album_id)
        except Exception as e:
            print(f"[TrackLoaderWorker] Error fetching album {self.album_id}: {e}")
            self.tracks_ready.emit([], self.album_id)

class PopularTrackDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent_color = "#ffffff"

    def update_color(self, color):
        self.accent_color = color

    def paint(self, painter, option, index):
        painter.save()
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver
        
        if index.column() == 1:
            # 1. Tell Qt to draw the CSS background first!
            option.widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
            
            track = index.data(Qt.ItemDataRole.UserRole)
            if not track:
                painter.restore()
                return

            rect = option.rect
            
            # 2. Draw Cover Art
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            cover_size = 40
            margin = (rect.height() - cover_size) // 2
            cover_rect = QRect(rect.x() + 5, rect.y() + margin, cover_size, cover_size)
            
            if icon and not icon.isNull():
                pixmap = icon.pixmap(cover_size, cover_size)
                path = QPainterPath()
                path.addRoundedRect(QRectF(cover_rect), 4, 4) 
                painter.setClipPath(path)
                painter.drawPixmap(cover_rect, pixmap)
                painter.setClipping(False)
            else:
                painter.setBrush(QColor("#222"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(cover_rect, 4, 4)

            # 3. Draw Title (Vertically Centered, No Subtitle)
            text_x = cover_rect.right() + 15
            title = track.get('title', 'Unknown')
            
            painter.setFont(QFont("sans-serif", 12))
            
            # TITLE COLOR: Accent color on hover or select, White normally!
            if is_selected or is_hovered:
                painter.setPen(QColor(self.accent_color))
            else:
                painter.setPen(QColor("#ffffff"))
                
            title_rect = QRect(text_x, rect.y(), rect.width() - text_x, rect.height())
            
            fm = painter.fontMetrics()
            elided_title = fm.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width() - 10)
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)
            
        else:
            super().paint(painter, option, index)
            
        painter.restore()

class AlbumLinkDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered_row = -1
        self.accent_color = "#ffffff"

    def update_color(self, color):
        self.accent_color = color
    
    def set_hovered(self, row):
        self.hovered_row = row

    def paint(self, painter, option, index):
        painter.save()
        option.widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_row_hovered = option.state & QStyle.StateFlag.State_MouseOver
        is_cell_hovered = (index.row() == self.hovered_row)
        
        font = QFont("sans-serif", 11)
        
        # Use the accent color for hovering and selecting!
        if is_cell_hovered:
            font.setUnderline(True)
            painter.setPen(QColor(self.accent_color))
        elif is_row_hovered or is_selected:
            painter.setPen(QColor(self.accent_color))
        else:
            painter.setPen(QColor("#a0a0a0")) 
            
        painter.setFont(font)
        
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            rect = option.rect
            text_rect = QRect(rect.x() + 5, rect.y(), rect.width() - 10, rect.height())
            
            fm = painter.fontMetrics()
            elided_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_text)
        
        painter.restore()

class SongListWidget(QTreeWidget):
    play_track = pyqtSignal(dict)
    album_clicked = pyqtSignal(dict) 

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["#", "Track", "Album", "🕒"])
        self.setHeaderHidden(True) 
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        
        # 1. FORCE Qt to allow StrongFocus on both the widget and viewport!
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.itemClicked.connect(self.on_item_single_clicked) 
        
        self.setFixedHeight(0) 
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        
        self.track_delegate = PopularTrackDelegate(self)
        self.setItemDelegateForColumn(1, self.track_delegate)
        
        self.album_delegate = AlbumLinkDelegate(self)
        self.setItemDelegateForColumn(2, self.album_delegate)
        
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 350)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self.setColumnWidth(0, 45) 
        self.setColumnWidth(3, 70)
        
        self.update_style("#ffffff")

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.pos())
        # If hovering over column 2 (Album), trigger the underline!
        if index.isValid() and index.column() == 2:
            self.album_delegate.set_hovered(index.row())
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.album_delegate.set_hovered(-1)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            
        self.viewport().update() # Force an instant visual refresh
        super().mouseMoveEvent(event)
        
    def leaveEvent(self, event):
        self.album_delegate.set_hovered(-1)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.viewport().update()
        super().leaveEvent(event)

    def update_style(self, accent_color):

        self.track_delegate.update_color(accent_color)
        self.album_delegate.update_color(accent_color)
        

        self.setStyleSheet(f"""
            QTreeWidget {{ 
                background: transparent; 
                border: none; 
                outline: none; 
            }}
            QTreeWidget::item {{ 
                height: 44px; 
                color: #a0a0a0; 
                border-bottom: 1px solid rgba(255,255,255,0.02); 
            }}
            QTreeWidget::item:hover {{ 
                background: rgba(255, 255, 255, 0.06); 
                color: {accent_color}; 
            }}
            QTreeWidget::item:selected {{ 
                background: rgba(255, 255, 255, 0.08); 
                color: {accent_color}; 
            }}
        """)
        self.viewport().update()

    def populate(self, songs, cover_worker=None, pending_items=None):
        self.clear()
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt
        normal_font = QFont("sans-serif", 11)
        
        for i, s in enumerate(songs):
            item = QTreeWidgetItem([str(i+1), "", s.get('album', ''), s.get('duration', '')])
            item.setData(0, Qt.ItemDataRole.UserRole, s)
            item.setData(1, Qt.ItemDataRole.UserRole, s) 
            
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setFont(0, normal_font)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFont(3, normal_font)
            
            self.addTopLevelItem(item)
            
            if cover_worker and pending_items is not None:
                cid = s.get('coverArt') or s.get('albumId')
                if cid:
                    if cid not in pending_items:
                        pending_items[cid] = []
                    pending_items[cid].append(item)
                    cover_worker.queue_cover(cid, priority=True)
        
        row_h = 44
        total_h = (len(songs) * row_h) + 20
        self.setFixedHeight(total_h)

    def on_item_single_clicked(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and col == 2:
            self.album_clicked.emit(data)

    def on_item_double_clicked(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.play_track.emit(data)

class SectionCoverProvider(QQuickImageProvider):
    """Image provider for artist detail section grids. Class-level cache is shared
    across all section instances so a cover loaded once is visible everywhere."""
    _cache = {}

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id, _requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import QRectF
        real_id = id.split("?t=")[0]
        data = self._cache.get(real_id)
        size = 250
        img = QImage(size, size, QImage.Format.Format_ARGB32)
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
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()
        return img, img.size()


class SectionGridBridge(QObject):
    accentColorChanged   = pyqtSignal(str)
    selectIndex          = pyqtSignal(int)
    itemClicked          = pyqtSignal(int)
    playClicked          = pyqtSignal(int)
    artistNameClicked    = pyqtSignal(str)
    contentHeightChanged = pyqtSignal(int)

    def __init__(self, model):
        super().__init__()
        self.model = model

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        self.itemClicked.emit(idx)

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        self.playClicked.emit(idx)

    @pyqtSlot(str)
    def emitArtistNameClicked(self, name):
        self.artistNameClicked.emit(name)

    @pyqtSlot(float)
    def reportContentHeight(self, h):
        self.contentHeightChanged.emit(max(1, int(h) + 1))



class QMLAlbumSectionWidget(QWidget):
    """Replaces AlbumRowWidget — uses a QML GridView for buttery-smooth resize."""
    album_clicked       = pyqtSignal(dict)
    play_album          = pyqtSignal(dict)
    artist_name_clicked = pyqtSignal(str)

    def __init__(self, title, count, albums):
        super().__init__()
        self.is_qml_section = True  # flag used by check_viewport to skip this widget

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 10)
        outer.setSpacing(5)

        # ── title bar ──────────────────────────────────────────────────────
        title_container = QWidget()
        self.title_layout = QHBoxLayout(title_container)
        self.title_layout.setContentsMargins(10, 0, 10, 0)
        self.title_layout.setSpacing(10)
        self.title_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 20px;")
        lbl_count = QLabel(str(count))
        lbl_count.setFixedHeight(22)
        lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_count.setStyleSheet(
            "color: #aaa; background-color: #333; border-radius: 4px;"
            " padding: 0px 8px; font-size: 12px; font-weight: bold;")

        self.title_layout.addWidget(lbl_title)
        self.title_layout.addWidget(lbl_count)
        self.title_layout.addStretch()
        outer.addWidget(title_container)

        # ── QML grid ───────────────────────────────────────────────────────
        self.qml_widget = QQuickWidget()
        self.qml_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.qml_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.qml_widget.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.qml_widget.setClearColor(Qt.GlobalColor.transparent)
        self.qml_widget.setMinimumHeight(10)

        self.album_model = AlbumModel()
        self.bridge = SectionGridBridge(self.album_model)
        self.bridge.itemClicked.connect(self._on_item_clicked)
        self.bridge.playClicked.connect(self._on_play_clicked)
        self.bridge.artistNameClicked.connect(self.artist_name_clicked)
        self.bridge.contentHeightChanged.connect(self._on_content_height)

        engine = self.qml_widget.engine()
        self._cover_provider = SectionCoverProvider()
        engine.addImageProvider("sectioncovers", self._cover_provider)

        ctx = self.qml_widget.rootContext()
        ctx.setContextProperty("sectionAlbumModel", self.album_model)
        ctx.setContextProperty("sectionBridge", self.bridge)

        self.qml_widget.setSource(QUrl.fromLocalFile(resource_path("artist_section_grid.qml")))
        outer.addWidget(self.qml_widget)

        # facade so legacy code doing `row.list_widget.count()` etc. still works
        self.list_widget = _SectionListFacade(self)
        self.full_albums = []  # kept for compat
        self.current_index = 0

        self.populate(albums)

    def items_per_row(self):
        avail = max(1, self.qml_widget.width() - 40)  # leftMargin + rightMargin = 40
        return max(1, int(avail / (180 + 20)))         # baseItemSize + itemGap*2

    def select(self, idx):
        count = self.album_model.rowCount()
        if count == 0:
            return
        idx = max(0, min(idx, count - 1))
        self.current_index = idx
        self.bridge.selectIndex.emit(idx)

    def add_action_widget(self, widget):
        self.title_layout.insertWidget(2, widget)

    def _on_content_height(self, h):
        self.qml_widget.setFixedHeight(h)

    def _on_item_clicked(self, idx):
        if 0 <= idx < len(self.album_model.albums):
            self.album_clicked.emit(self.album_model.albums[idx])

    def _on_play_clicked(self, idx):
        if 0 <= idx < len(self.album_model.albums):
            self.play_album.emit(self.album_model.albums[idx])

    def set_accent_color(self, color):
        self.bridge.accentColorChanged.emit(color)

    def populate(self, albums):
        # Normalise so AlbumModel's cover_id key is always populated
        normalised = []
        for a in albums:
            d = dict(a)
            if not d.get('cover_id'):
                d['cover_id'] = d.get('coverArt') or d.get('id') or ''
            normalised.append(d)
        self.album_model.beginResetModel()
        self.album_model.albums = normalised
        self.album_model.endResetModel()

    def apply_cover(self, cover_id, image_data):
        SectionCoverProvider._cache[cover_id] = image_data
        self.album_model.update_cover(cover_id)


class _SectionListFacade:
    """Minimal QListWidget-compatible shim so code that checks row.list_widget
    still works without changes (count, setFocus, setCurrentRow, etc.)."""
    def __init__(self, section: QMLAlbumSectionWidget):
        self._s = section

    def count(self):
        return len(self._s.album_model.albums)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        self._s.qml_widget.setFocus(reason)
        self._s.bridge.selectIndex.emit(0)

    def setCurrentRow(self, row):
        self._s.bridge.selectIndex.emit(max(0, row))

    def clearSelection(self):
        pass

    def installEventFilter(self, obj):
        self._s.qml_widget.installEventFilter(obj)

    def viewport(self):
        return self._s.qml_widget

    # visualItemRect is only used for scroll-to-item; return the section top instead
    def visualItemRect(self, _item):
        return QRect(0, 0, self._s.qml_widget.width(), self._s.qml_widget.height() // max(1, self.count()))

    def item(self, _idx):
        return None

    def currentRow(self):
        return self._s.current_index


class AlbumRowWidget(QWidget):
    album_clicked = pyqtSignal(dict)
    play_album = pyqtSignal(dict)

    def __init__(self, title, count, albums, lazy_load=False):
        super().__init__()
        self.lazy_load = lazy_load
        self._recalc_in_progress = False
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 5, 0, 10)
        self.layout.setSpacing(5)

        title_container = QWidget()
        self.title_layout = QHBoxLayout(title_container)
        self.title_layout.setContentsMargins(10, 0, 10, 0)
        self.title_layout.setSpacing(10)
        self.title_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 20px;")
        
        lbl_count = QLabel(str(count))
        lbl_count.setFixedHeight(22)
        lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_count.setStyleSheet("color: #aaa; background-color: #333; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")
        
        self.title_layout.addWidget(lbl_title)
        self.title_layout.addWidget(lbl_count)
        self.title_layout.addStretch()
        self.layout.addWidget(title_container)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.list_widget.setWrapping(True)
        
        self.list_widget.setMouseTracking(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.delegate = GridItemDelegate()
        self.list_widget.setItemDelegate(self.delegate)
        self.set_accent_color("#888888")

        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.layout.addWidget(self.list_widget)
        self.populate(albums)

    def add_action_widget(self, widget):
        self.title_layout.insertWidget(2, widget)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.recalc_layout()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(150, self.recalc_layout)

    def adjust_list_height(self):
        self.recalc_layout()

    def recalc_layout(self):
        if self._recalc_in_progress:
            return
        self._recalc_in_progress = True
        try:
            self._do_recalc_layout()
        finally:
            self._recalc_in_progress = False

    def _do_recalc_layout(self):
        # Use widget width directly — viewport().width() lags behind layout propagation
        w = self.width()
        if w <= 0:
            QTimer.singleShot(100, self.recalc_layout)
            return

        min_w = 210
        gap = 10

        n_cols = max(1, (w + gap) // (min_w + gap))
        cell_w = w // n_cols
        cell_h = cell_w + 70

        n_cols_changed = getattr(self, '_last_n_cols', None) != n_cols
        self._last_n_cols = n_cols

        self.list_widget.setIconSize(QSize(cell_w, cell_w))
        self.list_widget.setGridSize(QSize(cell_w, cell_h))

        hint = QSize(cell_w, cell_h)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setSizeHint(hint)

        if n_cols_changed:
            self.list_widget.setViewportMargins(0, gap, 0, gap)
            self.list_widget.setSpacing(0)
            self.list_widget.doItemsLayout()

        count = self.list_widget.count()
        if count == 0:
            self.list_widget.setFixedHeight(0)
            return

        rows = math.ceil(count / n_cols)
        total_height = (rows * cell_h) + (gap * 2)
        self.list_widget.setFixedHeight(total_height)
    
    def set_accent_color(self, color):

        self.list_widget.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 8px; }}
            QListWidget::item:hover {{ background: #222; }}
            QListWidget::item:selected {{ background: transparent; border: none; outline: none; }}
        """)

        if hasattr(self, 'delegate'):
            self.delegate.set_master_color(color)

    def populate(self, albums):
        self.full_albums = albums  
        self.list_widget.clear()
        placeholder = self.get_placeholder_icon()
        
        for album in albums:
            item = QListWidgetItem()
            item.setIcon(placeholder)
            
            
            if getattr(self, 'lazy_load', False):
                item.setData(Qt.ItemDataRole.UserRole, {'type': 'placeholder', 'title': 'Loading...'})
            else:
                item.setData(Qt.ItemDataRole.UserRole, album)
                
            self.list_widget.addItem(item)
            
        self.recalc_layout()

    def on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or data.get('type') == 'placeholder': return

        visual_rect = self.list_widget.visualItemRect(item)
        mouse_pos = self.list_widget.mapFromGlobal(QCursor.pos())
        rect = visual_rect
        icon_width = rect.width() - 20 
        center_x = rect.x() + 10 + (icon_width // 2)
        center_y = rect.y() + 10 + (icon_width // 2)
        play_radius = min(60, icon_width // 2) // 2 
        dist = ((mouse_pos.x() - center_x)**2 + (mouse_pos.y() - center_y)**2) ** 0.5
        
        if dist <= play_radius:
            self.play_album.emit(data)
        else:
            self.album_clicked.emit(data)

    def get_placeholder_icon(self):
        pix = QPixmap(200, 200); pix.fill(QColor("#222"))
        return QIcon(pix)

class CircularArtistDelegate(QStyledItemDelegate):
    """Renders artist cards with a circular photo, hover ring + play button, and name below."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#1db954")

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect     = option.rect
        size     = rect.width() - 20
        img_rect = QRect(rect.x() + 10, rect.y() + 10, size, size)

        is_hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # --- circular clip (same pattern as GridItemDelegate) ---
        clip = QPainterPath()
        clip.addEllipse(img_rect.x(), img_rect.y(), img_rect.width(), img_rect.height())

        painter.save()
        painter.setClipPath(clip)

        # Draw image
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pix = icon.pixmap(size, size)
            painter.drawPixmap(img_rect.x() + (size - pix.width()) // 2,
                               img_rect.y() + (size - pix.height()) // 2, pix)
        else:
            painter.setBrush(QColor("#333333"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(img_rect)

        # Dark hover overlay inside clip
        if is_hovered or is_selected:
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(img_rect)

            # Play button (smaller than album cards)
            center    = img_rect.center()
            play_size = min(40, size // 3)
            play_rect = QRect(0, 0, play_size, play_size)
            play_rect.moveCenter(center)
            painter.setBrush(self.master_color)
            painter.drawEllipse(play_rect)

            tri = play_size // 3
            p1 = QPoint(center.x() - tri // 3, center.y() - tri // 2)
            p2 = QPoint(center.x() - tri // 3, center.y() + tri // 2)
            p3 = QPoint(center.x() + tri // 2 + 2, center.y())
            painter.setBrush(QColor("#111111"))
            painter.drawPolygon(QPolygon([p1, p2, p3]))

        painter.restore()  # end clip

        # Accent ring drawn OUTSIDE clip so full pen width is visible
        if is_hovered or is_selected:
            painter.setPen(QPen(self.master_color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(img_rect.adjusted(-1, -1, 1, 1))

        # Artist name below (unclipped)
        data = index.data(Qt.ItemDataRole.UserRole)
        if data:
            name = data.get('name') or data.get('title') or ''
            text_color = self.master_color.name() if (is_hovered or is_selected) else '#eeeeee'
            painter.setPen(QColor(text_color))
            font = painter.font()
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            fm = QFontMetrics(font)
            text_y = img_rect.bottom() + 10
            text_w = rect.width() - 10
            painter.drawText(
                QRect(rect.x() + 5, text_y, text_w, 20),
                Qt.AlignmentFlag.AlignHCenter,
                fm.elidedText(name, Qt.TextElideMode.ElideRight, text_w)
            )

        painter.restore()

class _ArrowButton(QAbstractButton):
    def __init__(self, direction, color, parent=None):
        super().__init__(parent)
        self._direction = direction  # "left" or "right"
        self._color = QColor(color)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.underMouse():
            p.setBrush(QColor(255, 255, 255, 20))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 5, 5)

        color = self._color if self.isEnabled() else QColor("#333")
        p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

        cx, cy = self.width() / 2, self.height() / 2
        s = 6  # half-height of the chevron
        o = 3  # horizontal offset from center

        if self._direction == "right":
            p.drawLine(int(cx - o), int(cy - s), int(cx + o), int(cy))
            p.drawLine(int(cx + o), int(cy), int(cx - o), int(cy + s))
        else:
            p.drawLine(int(cx + o), int(cy - s), int(cx - o), int(cy))
            p.drawLine(int(cx - o), int(cy), int(cx + o), int(cy + s))

        p.end()


class RelatedArtistRowWidget(QWidget):
    """Single-row horizontally scrollable circular artist strip (max 10 items)."""
    artist_clicked = pyqtSignal(dict)

    CELL_W = 220

    def __init__(self, title, artists):
        super().__init__()
        self._artists = artists
        self._btn_left = None
        self._btn_right = None

        cell_h = self.CELL_W + 50

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 10)
        layout.setSpacing(5)

        # Title row
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(10, 0, 10, 0)
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 20px;")
        lbl_count = QLabel(str(len(artists)))
        lbl_count.setFixedHeight(22)
        lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_count.setStyleSheet("color: #aaa; background-color: #333; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")
        self._accent_color = "#888888"
        self._btn_left  = _ArrowButton("left",  self._accent_color)
        self._btn_right = _ArrowButton("right", self._accent_color)

        self._btn_left.clicked.connect(lambda: self._scroll_by(-self.CELL_W))
        self._btn_right.clicked.connect(lambda: self._scroll_by(self.CELL_W))

        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_count)
        title_layout.addStretch()
        title_layout.addWidget(self._btn_left)
        title_layout.addWidget(self._btn_right)
        layout.addWidget(title_row)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setWrapping(False)
        self.list_widget.setMouseTracking(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.installEventFilter(self)
        self.list_widget.setIconSize(QSize(self.CELL_W, self.CELL_W))
        self.list_widget.setGridSize(QSize(self.CELL_W, cell_h))
        self.list_widget.setFixedHeight(cell_h + 12)
        self.list_widget.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: transparent; }
        """)

        self.delegate = CircularArtistDelegate()
        self.list_widget.setItemDelegate(self.delegate)

        placeholder = QPixmap(self.CELL_W, self.CELL_W)
        placeholder.fill(QColor("#333"))
        placeholder_icon = QIcon(placeholder)

        for artist in artists:
            item = QListWidgetItem()
            item.setIcon(placeholder_icon)
            item.setData(Qt.ItemDataRole.UserRole, artist)
            item.setSizeHint(QSize(self.CELL_W, cell_h))
            self.list_widget.addItem(item)

        self.list_widget.horizontalScrollBar().valueChanged.connect(self._update_arrow_buttons)
        self.list_widget.horizontalScrollBar().rangeChanged.connect(self._update_arrow_buttons)

        layout.addWidget(self.list_widget)

    def _scroll_by(self, delta):
        sb = self.list_widget.horizontalScrollBar()
        sb.setValue(sb.value() + delta)

    def _update_arrow_buttons(self, *_):
        sb = self.list_widget.horizontalScrollBar()
        self._btn_left.setEnabled(sb.value() > sb.minimum())
        self._btn_right.setEnabled(sb.value() < sb.maximum())

    def set_accent_color(self, color):
        self._accent_color = color
        self._btn_left.set_color(color)
        self._btn_right.set_color(color)
        self.delegate.set_master_color(color)

    def eventFilter(self, source, event):
        if source is self.list_widget and event.type() == event.Type.Wheel:
            delta = event.angleDelta()
            scroll = delta.x() if delta.x() != 0 else delta.y()
            self.list_widget.horizontalScrollBar().setValue(
                self.list_widget.horizontalScrollBar().value() - scroll
            )
            return True
        return super().eventFilter(source, event)

class ArtistRichDetailView(QWidget):
    album_clicked = pyqtSignal(dict)
    play_album = pyqtSignal(dict)
    play_multiple_tracks = pyqtSignal(list)
    play_track = pyqtSignal(dict)
    play_artist = pyqtSignal()
    artist_clicked = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        
        

        self.current_accent = "#888888" 
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QWidget { background: transparent; }")
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.omni_scroller = MiddleClickScroller(self.scroll)
        
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 10, 0, 50)
        self.content_layout.setSpacing(20)
        
        # HEADER
        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        header_layout.setSpacing(30)
        
        self.img_label = QLabel()
        self.img_label.setFixedSize(220, 220)
        self.img_label.setStyleSheet("background: #333; border-radius: 110px; border: 2px solid #444;")
        self.img_label.setScaledContents(True)
        
        info_col = QWidget()
        info_layout = QVBoxLayout(info_col)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.lbl_type = QLabel("ARTIST")
        self.lbl_type.setStyleSheet("font-weight: bold; color: #aaa; font-size: 12px; letter-spacing: 1px;")
        
        self.lbl_name = QLabel("Artist Name")
        self.lbl_name.setStyleSheet("font-weight: 900; color: white; font-size: 48px;")
        
        self.lbl_stats = QLabel("Loading...")
        self.lbl_stats.setStyleSheet("color: #ddd; font-size: 14px;")
        
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 20, 0, 0)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        # --- ARTIST VIEW PLAY BUTTON ---
        from albums_browser import resource_path
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(60, 60) 
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setIcon(QIcon(resource_path("img/play.png")))
        self.btn_play.setIconSize(QSize(15, 15)) 
        self.btn_play.clicked.connect(self.play_current_artist_tracks)
        btn_layout.addWidget(self.btn_play)
        self.btn_play.setToolTip("Play All Tracks (Ctrl+↵)")
        
        info_layout.addWidget(self.lbl_type)
        info_layout.addWidget(self.lbl_name)
        info_layout.addWidget(self.lbl_stats)
        info_layout.addWidget(btn_bar)
        
        header_layout.addWidget(self.img_label)
        header_layout.addWidget(info_col)
        header_layout.addStretch()
        
        self.content_layout.addWidget(self.header)

        # ABOUT SECTION (bio from Last.fm / getArtistInfo2) — first after header
        self.lbl_about_header = QLabel()
        self.lbl_about_header.setStyleSheet("color: white; font-size: 20px; font-weight: bold; padding-left: 8px; margin-top: 10px;")
        self.lbl_about_header.hide()
        self.content_layout.addWidget(self.lbl_about_header)

        self._bio_collapsed = True
        self._bio_full_text = ""

        self.lbl_bio = QLabel()
        self.lbl_bio.setWordWrap(True)
        self.lbl_bio.setStyleSheet("color: #bbb; font-size: 14px; line-height: 1.4; padding-left: 8px;")
        self.lbl_bio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_bio.mousePressEvent = lambda e: self._toggle_bio()
        self.lbl_bio.hide()
        self.content_layout.addWidget(self.lbl_bio)

        self.lbl_bio_toggle = QLabel("Show more")
        self.lbl_bio_toggle.setStyleSheet("color: #888; font-size: 13px; padding-left: 8px; padding-top: 2px;")
        self.lbl_bio_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_bio_toggle.mousePressEvent = lambda e: self._toggle_bio()
        self.lbl_bio_toggle.hide()
        self.content_layout.addWidget(self.lbl_bio_toggle)

        # POPULAR TRACKS
        self.lbl_top_tracks = QLabel(" Popular")
        self.lbl_top_tracks.setStyleSheet("color: white; font-weight: bold; font-size: 20px; margin-top: 10px;")
        self.lbl_top_tracks.hide()
        self.content_layout.addWidget(self.lbl_top_tracks)

        self.song_list = SongListWidget()
        self.song_list.play_track.connect(self.play_track.emit)
        self.song_list.album_clicked.connect(self._on_popular_album_clicked)
        self.song_list.installEventFilter(self)
        self.song_list.viewport().installEventFilter(self)
        self.song_list.hide()
        self.content_layout.addWidget(self.song_list)

        self.sections_container = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_container)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(0)

        self.content_layout.addWidget(self.sections_container)

        # RELATED ARTISTS ROW — populated from getArtistInfo2 similarArtist list
        self.related_artists_row = None  # created lazily in set_related_artists

        self.content_layout.addStretch()
        
        self.scroll.setWidget(self.content_widget)
        self.layout.addWidget(self.scroll)
        self.scroll.verticalScrollBar().valueChanged.connect(self.on_scroll)


        # For the artist view, song_list has FocusPolicy.NoFocus so no child
        # widget steals keyboard events. We can simply override keyPressEvent
        # on this widget itself — but we also need it to be able to receive focus.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def on_scroll(self, value):
        if not hasattr(self, 'scroll_timer'):
            from PyQt6.QtCore import QTimer
            self.scroll_timer = QTimer()
            self.scroll_timer.setSingleShot(True)
            self.scroll_timer.timeout.connect(self.check_viewport)
        self.scroll_timer.start(150)

    def check_viewport(self):
        
        if not hasattr(self, 'scroll'): return
        
        scroll_y = self.scroll.verticalScrollBar().value()
        viewport_h = self.scroll.viewport().height()
        
        # Buffer to load rows just before they appear
        buffer = 800 
        safe_top = scroll_y - buffer
        safe_bottom = scroll_y + viewport_h + buffer

        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if not row or not hasattr(row, 'list_widget'): continue
            if getattr(row, 'is_qml_section', False): continue

            lw = row.list_widget
            try:
                # Get Y pos of this list_widget relative to the scrolling content
                lw_y = lw.mapTo(self.content_widget, QPoint(0,0)).y()
            except Exception:
                continue
            
            # If the entire row is way out of bounds, mass unload it
            if lw_y > safe_bottom + 2000 or lw_y + lw.height() < safe_top - 2000:
                for idx in range(lw.count()):
                    item = lw.item(idx)
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if data and data.get('type') != 'placeholder':
                        item.setIcon(row.get_placeholder_icon())
                        item.setData(Qt.ItemDataRole.UserRole, {'type': 'placeholder', 'title': 'Loading...'})
                continue

            for idx in range(lw.count()):
                item = lw.item(idx)
                item_rect = lw.visualItemRect(item)
                item_global_y = lw_y + item_rect.y()
                item_global_bottom = item_global_y + item_rect.height()

                # Inside safe zone? LOAD IT
                if item_global_bottom >= safe_top and item_global_y <= safe_bottom:
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if data and data.get('type') == 'placeholder':
                        real_data = row.full_albums[idx]
                        item.setData(Qt.ItemDataRole.UserRole, real_data)
                        
                        cid = real_data.get('cover_id') or real_data.get('coverArt') or real_data.get('albumId')
                        if cid:
                            real_data['cover_id'] = cid
                            if not hasattr(self, 'pending_items'): self.pending_items = {}
                            if cid not in self.pending_items:
                                self.pending_items[cid] = []
                            self.pending_items[cid].append(item)
                            
                            if getattr(self, 'cover_worker', None):
                                self.cover_worker.queue_cover(cid, priority=True)
                else:
                    # Outside safe zone? UNLOAD IT
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if data and data.get('type') != 'placeholder':
                        item.setIcon(row.get_placeholder_icon())
                        item.setData(Qt.ItemDataRole.UserRole, {'type': 'placeholder', 'title': 'Loading...'})

    def _on_popular_album_clicked(self, data):
        
        alb_data = {
            'id': data.get('albumId'), 
            'title': data.get('album'), 
            'artist': data.get('albumArtist') or data.get('artist'),
            'coverArt': data.get('coverArt') or data.get('albumId'),
            'cover_id': data.get('coverArt') or data.get('albumId')
        }
        if alb_data['id']:
            self.album_clicked.emit(alb_data)  
 
    def eventFilter(self, source, event):
        # --- RELATED ARTISTS: handle click directly on viewport ---
        related = getattr(self, 'related_artists_row', None)
        if related and source is related.list_widget.viewport():
            if event.type() == QEvent.Type.MouseButtonRelease:
                item = related.list_widget.itemAt(event.position().toPoint())
                if item:
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if data:
                        artist_data = {'id': data.get('id'), 'name': data.get('name', '')}
                        # Detect play zone (center circle) vs rest of card
                        visual_rect = related.list_widget.visualItemRect(item)
                        cell_w = RelatedArtistRowWidget.CELL_W
                        img_rect = QRect(visual_rect.x() + 10, visual_rect.y() + 10, cell_w - 20, cell_w - 20)
                        center = img_rect.center()
                        play_size = min(60, img_rect.width() // 2)
                        click_pos = event.position().toPoint()
                        dist = ((click_pos.x() - center.x())**2 + (click_pos.y() - center.y())**2) ** 0.5
                        in_play_zone = dist <= play_size / 2

                        if in_play_zone:
                            def _play(d=artist_data):
                                client = getattr(self, 'client', None)
                                if not client: return
                                w = ArtistPlayWorker(client, d.get('name', ''))
                                w.tracks_ready.connect(self.play_multiple_tracks.emit)
                                w.start()
                                self._related_play_worker = w
                            QTimer.singleShot(0, _play)
                        else:
                            def _navigate(d=artist_data):
                                browser = getattr(self, '_browser', None)
                                if browser:
                                    browser.show_artist_details(d)
                                else:
                                    self.artist_clicked.emit(d)
                            QTimer.singleShot(0, _navigate)
            return False

        if event.type() == QEvent.Type.KeyPress:
            from PyQt6.QtWidgets import QListWidget, QTreeWidget

            # --- 1. HANDLE POPULAR TRACKS MOVEMENT ---
            if source in (self.song_list, self.song_list.viewport()): # Catch BOTH!
                key = event.key()
                tree = self.song_list # Always command the main widget
                
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    current_item = tree.currentItem()
                    current_idx = tree.indexOfTopLevelItem(current_item) if current_item else -1
                    
                    if key == Qt.Key.Key_Down:
                        if current_idx < tree.topLevelItemCount() - 1:
                            # Move down and force the visual highlight
                            next_idx = 0 if current_idx == -1 else current_idx + 1
                            next_item = tree.topLevelItem(next_idx)
                            tree.setCurrentItem(next_item)
                            next_item.setSelected(True) 
                            
                            # THE CAMERA FIX: Smoothly scroll down to keep the track visible!
                            rect = tree.visualItemRect(next_item)
                            pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                            
                            return True
                        else:
                            # Jump seamlessly to the first album grid!
                            for i in range(self.sections_layout.count()):
                                row = self.sections_layout.itemAt(i).widget()
                                if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                                    tree.clearSelection()
                                    tree.setCurrentItem(None)
                                    row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                    row.list_widget.setCurrentRow(0)
                                    
                                    rect = row.list_widget.visualItemRect(row.list_widget.item(0))
                                    pt = row.list_widget.viewport().mapTo(self.content_widget, rect.topLeft())
                                    self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                                    return True
                                    
                    elif key == Qt.Key.Key_Up:
                        if current_idx > 0:
                            # Move up and force the visual highlight
                            prev_item = tree.topLevelItem(current_idx - 1)
                            tree.setCurrentItem(prev_item)
                            prev_item.setSelected(True) 
                            
                            # Smoothly scroll up to keep the track visible!
                            rect = tree.visualItemRect(prev_item)
                            pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                            
                            return True
                        else:
                            self.scroll.verticalScrollBar().setValue(0)
                            return True
                            
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.btn_play.click()
                        return True
                        
                    curr_item = tree.currentItem()
                    if curr_item:
                        data = curr_item.data(0, Qt.ItemDataRole.UserRole)
                        if data: self.play_track.emit(data)
                    return True

                return super().eventFilter(source, event)
            
        
            
            # --- 2. HANDLE ALBUM GRIDS (QListWidget) MOVEMENT ---
            if isinstance(source, QListWidget):
                key = event.key()
                
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                    old_row = source.currentRow()
                    
                    def check_jump_and_scroll():
                        new_row = source.currentRow()
                        
                        def do_scroll(target_grid, target_idx):
                            item = target_grid.item(target_idx)
                            if item:
                                rect = target_grid.visualItemRect(item)
                                pt = target_grid.viewport().mapTo(self.content_widget, rect.topLeft())
                                center_y = pt.y() + rect.height() // 2
                                y_margin = rect.height() // 2 + 40
                                self.scroll.ensureVisible(pt.x(), center_y, 0, y_margin)

                        if old_row == new_row:
                            lists = []
                            for i in range(self.sections_layout.count()):
                                r = self.sections_layout.itemAt(i).widget()
                                if r and hasattr(r, 'list_widget') and r.list_widget.count() > 0:
                                    lists.append(r.list_widget)
                            related = getattr(self, 'related_artists_row', None)
                            if related and related.list_widget.count() > 0:
                                lists.append(related.list_widget)
                                    
                            if source in lists:
                                current_idx = lists.index(source)
                                next_idx = current_idx + (1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Right) else -1)
                                
                                if 0 <= next_idx < len(lists):
                                    source.clearSelection() 
                                    next_grid = lists[next_idx]
                                    next_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                    
                                    target_row = 0
                                    if key == Qt.Key.Key_Right: target_row = 0
                                    elif key == Qt.Key.Key_Left: target_row = next_grid.count() - 1
                                    elif key == Qt.Key.Key_Down:
                                        old_x = source.visualItemRect(source.item(old_row)).x()
                                        best_dist = float('inf')
                                        for i in range(min(15, next_grid.count())): 
                                            dist = abs(next_grid.visualItemRect(next_grid.item(i)).x() - old_x)
                                            if dist < best_dist: best_dist = dist; target_row = i
                                    elif key == Qt.Key.Key_Up:
                                        old_x = source.visualItemRect(source.item(old_row)).x()
                                        last_idx = next_grid.count() - 1
                                        last_y = next_grid.visualItemRect(next_grid.item(last_idx)).y()
                                        best_dist = float('inf'); target_row = last_idx
                                        for i in range(last_idx, -1, -1):
                                            rect = next_grid.visualItemRect(next_grid.item(i))
                                            if rect.y() < last_y: break 
                                            dist = abs(rect.x() - old_x)
                                            if dist < best_dist: best_dist = dist; target_row = i
                                                
                                    next_grid.setCurrentRow(target_row)
                                    do_scroll(next_grid, target_row)
                                    return
                                    
                                # --- THE BRIDGE: Jumping UP from the very first Album Grid! ---
                                elif next_idx < 0 and key == Qt.Key.Key_Up:
                                    # If Popular Tracks exist, seamlessly jump up into them!
                                    if not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
                                        source.clearSelection()
                                        self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                        # Highlight the very last popular track
                                        last_item = self.song_list.topLevelItem(self.song_list.topLevelItemCount() - 1)
                                        self.song_list.setCurrentItem(last_item)
                                        last_item.setSelected(True)
                                        
                                        rect = self.song_list.visualItemRect(last_item)
                                        pt = self.song_list.viewport().mapTo(self.content_widget, rect.topLeft())
                                        self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                                    else:
                                        self.scroll.verticalScrollBar().setValue(0)
                                    return
                                    
                                elif next_idx >= len(lists) and key == Qt.Key.Key_Down:
                                    self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
                                    return
                        
                        do_scroll(source, new_row)

                    QTimer.singleShot(0, check_jump_and_scroll)
                    return False 
                    
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.isAutoRepeat(): return True
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.btn_play.click(); return True
                    curr_item = source.currentItem()
                    if curr_item:
                        data = curr_item.data(Qt.ItemDataRole.UserRole)
                        if data:
                            related = getattr(self, 'related_artists_row', None)
                            if related and source is related.list_widget:
                                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                                    w = ArtistPlayWorker(self.client, data.get('name', ''))
                                    w.tracks_ready.connect(self.play_multiple_tracks.emit)
                                    w.start()
                                    self._related_play_worker = w
                                else:
                                    self.artist_clicked.emit({'id': data.get('id'), 'name': data.get('name', '')})
                            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                                self.play_album.emit(data)
                            else:
                                self.album_clicked.emit(data)
                    return True

            # --- 3. HANDLE QML ALBUM SECTION GRIDS ---
            section = None
            for s in self._get_qml_sections():
                if source is s.qml_widget:
                    section = s
                    break
            if section is not None:
                key = event.key()
                ipr = section.items_per_row()
                count = section.album_model.rowCount()
                idx = section.current_index
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                    col = idx % ipr
                    cur_row = idx // ipr
                    last_row = (count - 1) // ipr

                    if key == Qt.Key.Key_Right:
                        if idx < count - 1:
                            section.select(idx + 1)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        return True
                    elif key == Qt.Key.Key_Left:
                        if idx > 0:
                            section.select(idx - 1)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        return True
                    elif key == Qt.Key.Key_Down:
                        if cur_row < last_row:
                            section.select(min(idx + ipr, count - 1))
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        else:
                            self._qml_jump_next(section, col)
                        return True
                    elif key == Qt.Key.Key_Up:
                        if cur_row > 0:
                            section.select(idx - ipr)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        else:
                            self._qml_jump_prev(section, col)
                        return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.isAutoRepeat(): return True
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.btn_play.click(); return True
                    if 0 <= idx < count:
                        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            self.play_album.emit(section.album_model.albums[idx])
                        else:
                            self.album_clicked.emit(section.album_model.albums[idx])
                    return True
                if key == Qt.Key.Key_Space:
                    if event.isAutoRepeat(): return True
                    if 0 <= idx < count:
                        self.play_album.emit(section.album_model.albums[idx])
                    return True

        return super().eventFilter(source, event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)):
            self.btn_play.animateClick()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_accent_color(self, color, alpha=0.3):
        
        self.current_accent = color
        
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")
        
        scrollbar_style = f"""
            QScrollArea {{ border: none; background: transparent; }} 
            QWidget {{ background: transparent; }}
            QScrollBar:vertical {{ border: none; background: rgba(0, 0, 0, 0.05); width: 10px; margin: 0; }} 
            QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }} 
            QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {color}; }} 
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }} 
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }} 
            
            QScrollBar:horizontal {{ border: none; background: rgba(0, 0, 0, 0.05); height: 10px; margin: 0; }} 
            QScrollBar::handle:horizontal {{ background: #333; min-width: 30px; border-radius: 5px; }} 
            QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {color}; }} 
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }} 
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
        """
        self.scroll.setStyleSheet(scrollbar_style)
        
        play_btn_style = f"""
            QPushButton {{ background-color: {color}; border-radius: 30px; border: none; }} 
            QPushButton:hover {{ background-color: white; }}
        """
        self.btn_play.setStyleSheet(play_btn_style)
        
        if hasattr(self, 'btn_shuffle'):
            import os
            from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon
            from PyQt6.QtCore import QSize
            from albums_browser import resource_path
            
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

        
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'set_accent_color'):
                row.set_accent_color(color)
        self.song_list.update_style(color)

    def set_header_image(self, pixmap):
        size = 220
        circle_pix = QPixmap(size, size)
        circle_pix.fill(QColor(0, 0, 0, 0))

        painter = QPainter(circle_pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - size) // 2
            y = (scaled.height() - size) // 2
            painter.drawPixmap(-x, -y, scaled)
        else:
            painter.setBrush(QColor("#333333"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(0, 0, size, size)

        painter.end()
        self.img_label.setPixmap(circle_pix)

    def set_bio(self, text):
        if text:
            import re as _re
            clean = _re.sub(r'<[^>]+>', '', text).strip()
            clean = _re.sub(r'\s*Read more on Last\.fm\.?\s*$', '', clean, flags=_re.IGNORECASE).strip()
            if clean:
                self._bio_full_text = clean
                self._bio_collapsed = True
                self._render_bio()
                artist = getattr(self, 'current_artist_name', '')
                self.lbl_about_header.setText(f'About {artist}')
                self.lbl_about_header.show()
                return
        self._bio_full_text = ""
        self.lbl_bio.hide()
        self.lbl_bio_toggle.hide()
        self.lbl_about_header.hide()

    def _render_bio(self):
        text = self._bio_full_text
        lines = text.splitlines()
        n = 10  # collapsed line count
        if self._bio_collapsed:
            if len(lines) > n:
                preview = '\n'.join(lines[:n])
            else:
                chars = n * 100
                preview = text[:chars].rsplit(' ', 1)[0] + '…' if len(text) > chars else text
            self.lbl_bio.setText(preview)
            needs_toggle = len(lines) > n or len(text) > n * 100
            self.lbl_bio_toggle.setText("Show more")
            self.lbl_bio_toggle.setVisible(needs_toggle)
        else:
            self.lbl_bio.setText(text)
            self.lbl_bio_toggle.setText("Show less")
            self.lbl_bio_toggle.show()
        self.lbl_bio.show()

    def _toggle_bio(self):
        self._bio_collapsed = not self._bio_collapsed
        self._render_bio()

    def set_related_artists(self, similar_artists):
        # Remove old row if present
        if self.related_artists_row is not None:
            self.content_layout.removeWidget(self.related_artists_row)
            self.related_artists_row.deleteLater()
            self.related_artists_row = None

        if not similar_artists:
            return

        # Cap at 10
        similar_artists = similar_artists[:10]

        # Normalise: ensure each dict has 'title' for the delegate's name display
        normalised = []
        for a in similar_artists:
            normalised.append({
                'id': a.get('id'),
                'name': a.get('name', ''),
                'title': a.get('name', ''),
                'coverArt': a.get('coverArt') or a.get('id'),
                '_is_artist': True,
            })

        row = RelatedArtistRowWidget("Related Artists", normalised)
        row.set_accent_color(self.current_accent)
        row.list_widget.installEventFilter(self)
        row.list_widget.viewport().installEventFilter(self)

        # Register list items in pending_items so apply_cover can find them,
        # then queue the cover fetch
        worker = getattr(self, 'cover_worker', None)
        for i, a in enumerate(normalised):
            cover_id = a.get('coverArt')
            if not cover_id:
                continue
            item = row.list_widget.item(i)
            if item:
                self.pending_items.setdefault(cover_id, []).append(item)
            if worker:
                worker.queue_cover(cover_id)

        # Insert before the stretch (second-to-last item)
        insert_pos = self.content_layout.count() - 1
        self.content_layout.insertWidget(insert_pos, row)
        self.related_artists_row = row

    def set_top_songs(self, songs):
        if songs:
            from PyQt6.QtWidgets import QApplication
            current_focus = QApplication.focusWidget()
            
            was_idle = False
            first_album_grid = None
            for i in range(self.sections_layout.count()):
                row = self.sections_layout.itemAt(i).widget()
                if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                    first_album_grid = row.list_widget
                    break
            
            
            if first_album_grid and current_focus == first_album_grid and first_album_grid.currentRow() <= 0:
                was_idle = True
            elif current_focus in (self, self.scroll, self.scroll.widget(), None) or (current_focus and current_focus.parent() == self.header):
                was_idle = True
            
            self.lbl_top_tracks.show()
            self.song_list.show()
            
            worker = getattr(self, 'cover_worker', None)
            self.song_list.populate(songs, worker, getattr(self, 'pending_items', None))
            
            
            if was_idle:
                if first_album_grid:
                    first_album_grid.clearSelection()
                    first_album_grid.setCurrentRow(-1) # Kill the blue box on the album
                
                self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
                first_item = self.song_list.topLevelItem(0)
                if first_item:
                    self.song_list.setCurrentItem(first_item)
                    first_item.setSelected(True)
                self.scroll.verticalScrollBar().setValue(0) 
            elif current_focus: 
                current_focus.setFocus()
        else:
            self.lbl_top_tracks.hide()
            self.song_list.hide()

    def clear_sections(self):
        while self.sections_layout.count():
            child = self.sections_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # Max albums per QML widget — keeps each texture well under GPU limits
    _QML_CHUNK = 80

    def add_section(self, title, albums, cover_worker, pending_items):
        if not albums: return

        sorted_albums = sorted(albums, key=lambda x: (int(x.get('playCount', 0)), str(x.get('year', '0000'))), reverse=True)

        # Split into chunks to avoid exceeding GPU texture size limits
        chunk_size = self._QML_CHUNK
        chunks = [sorted_albums[i:i + chunk_size] for i in range(0, len(sorted_albums), chunk_size)]

        for chunk_idx, chunk in enumerate(chunks):
            # Only first chunk gets the section title + total count badge
            chunk_title = title if chunk_idx == 0 else ""
            chunk_count = len(sorted_albums) if chunk_idx == 0 else 0

            row = QMLAlbumSectionWidget(chunk_title, chunk_count, chunk)
            row.set_accent_color(self.current_accent)
            row.album_clicked.connect(self.album_clicked.emit)
            row.play_album.connect(self.play_album.emit)
            row.artist_name_clicked.connect(lambda name: self.artist_clicked.emit({'name': name, 'id': None}))
            row.qml_widget.installEventFilter(self)
            self.sections_layout.addWidget(row)

            # Queue covers and track which section owns them
            for album in row.album_model.albums:
                cid = album.get('cover_id') or ''
                if cid:
                    self.pending_qml_sections.setdefault(cid, []).append(row)
                    if cover_worker:
                        cover_worker.queue_cover(cid)

        if self.sections_layout.count() <= len(chunks):
            QTimer.singleShot(100, self.auto_focus)
        
    def _get_qml_sections(self):
        sections = []
        for i in range(self.sections_layout.count()):
            w = self.sections_layout.itemAt(i).widget()
            if isinstance(w, QMLAlbumSectionWidget):
                sections.append(w)
        return sections

    def _qml_scroll_to_cell(self, section, row_index):
        def _do():
            ipr = section.items_per_row()
            avail = max(1, section.qml_widget.width() - 40)
            cell_h = int(avail / ipr + 70)
            cell_y = 10 + row_index * cell_h
            pt = section.qml_widget.mapTo(self.content_widget, QPoint(0, cell_y))
            self.scroll.ensureVisible(pt.x(), pt.y() + cell_h // 2, 0, cell_h // 2 + 20)
        QTimer.singleShot(0, _do)

    def _qml_jump_next(self, from_section, col):
        sections = self._get_qml_sections()
        if from_section not in sections:
            return
        idx = sections.index(from_section)
        if idx + 1 < len(sections):
            nxt = sections[idx + 1]
            target = min(col, nxt.album_model.rowCount() - 1)
            nxt.select(target)
            nxt.qml_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            self._qml_scroll_to_cell(nxt, 0)
        else:
            related = getattr(self, 'related_artists_row', None)
            if related and related.list_widget.count() > 0:
                related.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                related.list_widget.setCurrentRow(min(col, related.list_widget.count() - 1))
            else:
                self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _qml_jump_prev(self, from_section, col):
        sections = self._get_qml_sections()
        if from_section not in sections:
            return
        idx = sections.index(from_section)
        if idx > 0:
            prev = sections[idx - 1]
            count = prev.album_model.rowCount()
            ipr = prev.items_per_row()
            last_row = (count - 1) // ipr
            target = min(last_row * ipr + col, count - 1)
            prev.select(target)
            prev.qml_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            self._qml_scroll_to_cell(prev, last_row)
        elif not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
            self.song_list.setFocus(Qt.FocusReason.OtherFocusReason)
            last_item = self.song_list.topLevelItem(self.song_list.topLevelItemCount() - 1)
            self.song_list.setCurrentItem(last_item)
            last_item.setSelected(True)
            rect = self.song_list.visualItemRect(last_item)
            pt = self.song_list.viewport().mapTo(self.content_widget, rect.topLeft())
            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
        else:
            self.scroll.verticalScrollBar().setValue(0)

    def auto_focus(self):
           
        
        if not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
            self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
            first_item = self.song_list.topLevelItem(0)
            if first_item:
                self.song_list.setCurrentItem(first_item)
                first_item.setSelected(True)
            self.scroll.verticalScrollBar().setValue(0)
            return
            
        # Otherwise, fall back to focusing the first album grid
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                row.list_widget.setCurrentRow(0)
                # Ensure the scrollbar resets to the absolute top
                self.scroll.verticalScrollBar().setValue(0)
                break
       
    def _safe_discard_worker(self, worker):
        if not worker: return
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
        self._worker_graveyard.add(worker)
        try:
            worker.finished.connect(lambda: self._worker_graveyard.discard(worker) if worker in self._worker_graveyard else None)
        except: pass
   
    def load_artist(self, artist_data):
        self.pending_items = {}
        self.pending_qml_sections = {}  # cover_id -> [QMLAlbumSectionWidget]
        self.current_artist_name = artist_data.get('name', 'Unknown')
        self.current_artist_id = artist_data.get('id')

        # 1. INSTANT VISUALS
        self.lbl_name.setText(self.current_artist_name)
        self.lbl_stats.setText("Loading...")
        self.set_bio("")
        self.set_top_songs([])
        self.set_related_artists([])
        self.clear_sections()

        if not getattr(self, '_header_already_loaded', False):
            self.img_label.setStyleSheet("background: #333; border-radius: 110px;")
            self.set_header_image(None)
        self._header_already_loaded = False  
        self._exact_artist_image = False     

        if not hasattr(self, 'cover_worker') and getattr(self, 'client', None):
            from albums_browser import GridCoverWorker
            self.cover_worker = GridCoverWorker(self.client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()

        
        if getattr(self, 'live_detail_worker', None) and self.live_detail_worker.isRunning():
            try: self.live_detail_worker.details_ready.disconnect()
            except: pass
            self._safe_discard_worker(self.live_detail_worker)

        self.live_detail_worker = LiveArtistDetailWorker(
            self.client, 
            self.current_artist_id, 
            self.current_artist_name
        )
        self.live_detail_worker.details_ready.connect(self._on_details_ready)
        self.live_detail_worker.start()

    def _on_details_ready(self, info, top_songs, main_albums, singles, appears_on):
        # 1. Update Bio and Cover
        if info:
            if 'biography' in info:
                self.set_bio(info['biography'])
                
            cover_src = info.get('coverArt') or info.get('id')
            if cover_src:
                self.current_header_cover_id = str(cover_src)
                # Only skip the fetch if the grid pre-applied the exact artist image
                # (_exact_artist_image flag). A placeholder from current_cover_pixmap
                # should still be replaced by the real artist image.
                if not getattr(self, '_exact_artist_image', False) and hasattr(self, 'cover_worker'):
                    self.cover_worker.queue_cover(cover_src, priority=True)
                self._exact_artist_image = False  # consume

        # 2. Update Top Songs (Trusting the server now!)
        self.set_top_songs(top_songs)

        # 3. Update Album Sections
        total_releases = len(main_albums) + len(singles)
        total_appearances = len(appears_on)

        if total_releases == 0 and total_appearances == 0: 
            self.lbl_stats.setText("No releases found")
        elif total_releases == 0 and total_appearances > 0: 
            self.lbl_stats.setText(f"Guest Artist • {total_appearances} appearances")
        else: 
            self.lbl_stats.setText(f"{total_releases} releases • {total_appearances} appearances")

        worker = getattr(self, 'cover_worker', None)
        if main_albums: self.add_section("Albums", main_albums, worker, self.pending_items)
        if singles: self.add_section("Singles & EPs", singles, worker, self.pending_items)
        if appears_on: self.add_section("Appears on & Compilations", appears_on, worker, self.pending_items)

        # 4. Related Artists
        self.set_related_artists(info.get('similar_artists', []) if info else [])

        
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self.check_viewport)

        # Focus jump
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                row.list_widget.setCurrentRow(0)
                break

    def play_current_artist_tracks(self):
        """Fetches all artist tracks and emits them for playback — no parent relay needed."""
        client = getattr(self, 'client', None)
        name   = getattr(self, 'current_artist_name', None) or self.lbl_name.text()
        if not client or not name or name in ('Loading...', 'Artist Name'):
            return
        self._play_artist_worker = ArtistPlayWorker(client, name)
        self._play_artist_worker.tracks_ready.connect(self.play_multiple_tracks.emit)
        self._play_artist_worker.start()
   
    def apply_cover(self, cover_id, image_data):
        from PyQt6.QtGui import QPixmap, QIcon
        from PyQt6.QtCore import Qt

        is_header = getattr(self, 'current_header_cover_id', None) == str(cover_id)

        # Fast bail: nothing waiting for this cover and it's not the detail header
        if (cover_id not in getattr(self, 'pending_items', {}) and
                cover_id not in getattr(self, 'pending_qml_sections', {}) and
                not is_header):
            return

        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        
        if not pixmap.isNull():
            square_pix = pixmap.scaled(400, 400, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            crop_x = (square_pix.width() - 400) // 2
            crop_y = (square_pix.height() - 400) // 2
            square_pix = square_pix.copy(crop_x, crop_y, 400, 400)
            
            icon = QIcon(square_pix)
            
            if cover_id in getattr(self, 'pending_items', {}):
                items = self.pending_items[cover_id]
                for item in items:
                    try:
                        from PyQt6.QtWidgets import QTreeWidgetItem
                        if isinstance(item, QTreeWidgetItem):
                            item.setIcon(1, icon)
                        else:
                            item.setIcon(icon)
                    except: pass
                del self.pending_items[cover_id]

        if cover_id in getattr(self, 'pending_qml_sections', {}):
            for section in self.pending_qml_sections[cover_id]:
                try:
                    section.apply_cover(cover_id, image_data)
                except: pass
            del self.pending_qml_sections[cover_id]

        if is_header and not pixmap.isNull():
            self.set_header_image(pixmap)

class ArtistModel(QAbstractListModel):
    NAME_ROLE      = Qt.ItemDataRole.UserRole + 1
    COVER_ID_ROLE  = Qt.ItemDataRole.UserRole + 2
    RAW_DATA_ROLE  = Qt.ItemDataRole.UserRole + 3
    IS_LOADING_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self):
        super().__init__()
        self.artists = []

    def rowCount(self, parent=QModelIndex()):
        return len(self.artists)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.artists[index.row()]
        if role == self.NAME_ROLE:      return a.get('name') or a.get('artist') or 'Unknown'
        if role == self.COVER_ID_ROLE:  return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE:  return a
        if role == self.IS_LOADING_ROLE: return a.get('type') == 'placeholder'
        return None

    def roleNames(self):
        return {
            self.NAME_ROLE:      b"artistName",
            self.COVER_ID_ROLE:  b"coverId",
            self.RAW_DATA_ROLE:  b"rawData",
            self.IS_LOADING_ROLE: b"isLoading",
        }

    def append_artists(self, new_artists):
        start = len(self.artists)
        self.beginInsertRows(QModelIndex(), start, start + len(new_artists) - 1)
        self.artists.extend(new_artists)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.artists = []
        self.endResetModel()

    def update_cover(self, cover_id):
        import time
        forced_id = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self.artists):
            if a.get('cover_id') == cover_id:
                a['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class ArtistGridBridge(QObject):
    itemClicked         = pyqtSignal(dict)
    playClicked         = pyqtSignal(dict)
    visibleRangeChanged = pyqtSignal(int, int)
    accentColorChanged  = pyqtSignal(str)
    bgAlphaChanged      = pyqtSignal(float)
    cancelScroll        = pyqtSignal()
    scrollBy            = pyqtSignal(float)

    def __init__(self, artist_model):
        super().__init__()
        self.artist_model = artist_model

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
        if 0 <= idx < len(self.artist_model.artists):
            self.itemClicked.emit(self.artist_model.artists[idx])

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        if 0 <= idx < len(self.artist_model.artists):
            self.playClicked.emit(self.artist_model.artists[idx])

class ArtistGridBrowser(QWidget):
    play_track_signal = pyqtSignal(dict) 
    play_album_signal = pyqtSignal(list) 
    queue_track_signal = pyqtSignal(dict)
    play_next_signal = pyqtSignal(dict)
    switch_to_album_tab = pyqtSignal(dict)
    artist_clicked = pyqtSignal(dict)

    def __init__(self, client):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.client = client
        self.last_reload_time = time.time()
        
        self.current_page = 1
        self.total_pages = 1
        self.current_query = ""
       
        self.sort_states = {
            'random': True,
            'most_played': False,
            'alphabetical': True,
            'albums_count': False, 
        }
        self.current_sort = 'most_played'

        self.cover_worker = None
        if self.client:
            self.set_client(client)
        
        self.offset = 0
        self.batch_size = 50
        self.is_loading = False
        self.has_more = True
        self.current_query = "" 
        self.current_accent = "#888888"  # Default accent color
        
        # --- PAGINATION SETTINGS ---
        self.page_size = 50
        self.current_page = 1
        self.total_pages = 1
        self.total_items = 0
        
        # Search timer for debounced search
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(400)
        self.search_timer.timeout.connect(self.execute_search) 
        
        self.pending_items = {}
        self.track_cache = OrderedDict()
        self.nav_history = []  
        self.nav_index = -1   
        self.current_album_id = None 
        self.current_header_cover_id = None
        self.current_artist_id = None
        self._active_workers = set()
        
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- HEADER ---
        self.header_container = QWidget()
        self.header_container.setFixedHeight(50)
        self.header_container.setStyleSheet("QWidget { background-color: #111; border-top-left-radius: 5px; border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        
        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(15, 0, 10, 0) 
        header_layout.setSpacing(15)
        
        self.status_label = QLabel(f"Loading artists...")
        self.status_label.setStyleSheet("color: #888; font-weight: bold; background: transparent; border: none;")

        # --- SMART SEARCH CONTAINER ---
        self.search_container = SmartSearchContainer(placeholder="Search artists...")
        self.search_container.text_changed.connect(self.on_search_text_changed)
        self.search_container.burger_clicked.connect(self.show_sort_menu)
        self.burger_btn = self.search_container.get_burger_btn()
        
        # 🟢 THE FIX: Catch the Enter key inside the search box!
        if hasattr(self.search_container, 'search_input'):
            self.search_container.search_input.returnPressed.connect(self.focus_first_grid_item)
        
        # Main Header Assembly
        header_layout.addWidget(self.status_label)
        header_layout.addStretch() 
        header_layout.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)

        self.main_layout.addWidget(self.header_container)
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)

        self.qml_view = QMLGridWrapper()
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.qml_view.setClearColor(Qt.GlobalColor.transparent)
        self.qml_view.setStyleSheet("background: transparent; border: none;")

        self.artist_model = ArtistModel()
        self.grid_bridge = ArtistGridBridge(self.artist_model)

        self.grid_bridge.itemClicked.connect(self.on_grid_artist_clicked)
        self.grid_bridge.playClicked.connect(self.on_grid_play_clicked)
        self.grid_bridge.visibleRangeChanged.connect(self.check_viewport_qml)

        # 🟢 OMNI-SCROLLER FIX: same as albums_browser — QMLGridWrapper's DummyScrollBar
        # is a no-op, so we push pixel deltas via the bridge instead.
        self.omni_scroller_qml = QMLMiddleClickScroller(self.qml_view, self.grid_bridge)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("artistModel", self.artist_model)
        ctx.setContextProperty("artistBridge", self.grid_bridge)

        engine = self.qml_view.engine()
        self.cover_provider = engine.imageProvider("artistcovers")
        if not self.cover_provider:
            self.cover_provider = CoverImageProvider()
            engine.addImageProvider("artistcovers", self.cover_provider)

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("artist_grid.qml")))

        self.grid_view = self.qml_view  # keep alias so existing code doesn't break
        self.stack.addWidget(self.grid_view)

        # 🟢 NEW: Pass the client
        self.detail_view = AlbumDetailView(self.client)
        
        # 🟢 NEW: Wire up the internal TracksBrowser signals to the main app
        self.detail_view.track_list.play_track.connect(self.play_track_signal.emit)
        self.detail_view.track_list.play_multiple_tracks.connect(self.play_album_signal.emit)
        self.detail_view.track_list.queue_track.connect(self.queue_track_signal.emit)
        self.detail_view.track_list.play_next.connect(self.play_next_signal.emit)
        self.detail_view.track_list.switch_to_album_tab.connect(self.switch_to_album_tab.emit)
        self.detail_view.track_list.switch_to_artist_tab.connect(lambda name: self.show_artist_details({'name': name, 'id': None}))

        # Wire up the header buttons
        self.detail_view.play_clicked.connect(self.on_play_all_clicked)
        self.detail_view.shuffle_clicked.connect(self.on_shuffle_album_clicked)
        self.detail_view.album_favorite_toggled.connect(self.on_album_heart_clicked)
        self.detail_view.artist_clicked.connect(lambda name: self.show_artist_details({'name': name, 'id': None}))
        
        self.stack.addWidget(self.detail_view)
        
        self.artist_view = ArtistRichDetailView()
        self.artist_view._browser = self  # back-reference for related artist navigation
        self.artist_view.album_clicked.connect(self.on_artist_album_clicked)
        self.artist_view.play_album.connect(self.on_play_artist_album)
        self.artist_view.play_multiple_tracks.connect(self.play_album_signal.emit)
        self.artist_view.play_track.connect(self.play_track_signal.emit)
        self.artist_view.play_artist.connect(self.play_current_artist)
        self.artist_view.artist_clicked.connect(self.show_artist_details)
        self.stack.addWidget(self.artist_view) 

      
        self.add_to_history({'type': 'root'})

        self.set_accent_color("#888888")

        
        self.refresh_grid()

    def eventFilter(self, source, event):
        # Catch key presses on the grid
        if source == getattr(self, 'grid_view', None) and event.type() == QEvent.Type.KeyPress:
            
            # If the user typed a normal letter/number
            if event.text().isprintable() and event.text().strip() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                
                
                main_win = self.window()
                if main_win:
                    main_win.keyPressEvent(event)
                
                return True # Stop the grid from stealing the keystroke
                    
        return super().eventFilter(source, event)
  
    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()

    def focus_first_grid_item(self):
        if self.search_timer.isActive():
            self.search_timer.stop()
            self.execute_search()
        def apply_focus():
            if hasattr(self, 'qml_view'):
                self.qml_view.setFocus(Qt.FocusReason.ShortcutFocusReason)
        QTimer.singleShot(50, apply_focus)

    def execute_search(self):
        self.filtered_items = None
        self.load_artists_page(reset=True)

    def _safe_discard_worker(self, worker):
        """Monkey-proof thread disposal: Keeps thread alive in RAM until C++ finishes."""
        if not worker: return
        worker.is_cancelled = True
        try: worker.page_ready.disconnect()
        except: pass
        
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
            
        self._worker_graveyard.add(worker)
        
        # Connect to finished so it cleans itself out of the graveyard safely
        def remove_from_grave():
            if hasattr(self, '_worker_graveyard') and worker in self._worker_graveyard:
                self._worker_graveyard.remove(worker)
                
        try: worker.finished.connect(remove_from_grave)
        except: pass

    def on_scroll(self, value):
        if not hasattr(self, 'scroll_timer'):
            from PyQt6.QtCore import QTimer
            self.scroll_timer = QTimer()
            self.scroll_timer.setSingleShot(True)
            self.scroll_timer.timeout.connect(self.check_viewport)
            
        
        if not self.scroll_timer.isActive():
            self.scroll_timer.start(150)

    def check_viewport(self): 
        pass

    def check_viewport_qml(self, start_idx, end_idx):
        if len(self.artist_model.artists) == 0: return

        start_chunk = max(0, start_idx // 50)
        end_chunk   = max(0, end_idx   // 50)
        visible_chunks = set(range(start_chunk, end_chunk + 1))

        if not hasattr(self, 'loaded_chunks'):        self.loaded_chunks = set()
        if not hasattr(self, 'active_chunk_workers'): self.active_chunk_workers = {}

        # 1. Cancel out-of-view workers
        for chunk, worker in list(self.active_chunk_workers.items()):
            if chunk not in visible_chunks:
                self._safe_discard_worker(worker)
                del self.active_chunk_workers[chunk]
                if chunk in self.loaded_chunks: self.loaded_chunks.remove(chunk)

        # 2. GC: evict far-away chunks back to placeholders
        for chunk in list(self.loaded_chunks):
            if abs(chunk - start_chunk) > 3:
                self.loaded_chunks.remove(chunk)
                cs = chunk * 50
                ce = min(cs + 50, len(self.artist_model.artists))
                for i in range(cs, ce):
                    self.artist_model.artists[i] = {'type': 'placeholder', 'name': 'Loading...'}
                self.artist_model.dataChanged.emit(
                    self.artist_model.index(cs, 0),
                    self.artist_model.index(ce - 1, 0),
                    [self.artist_model.NAME_ROLE, self.artist_model.COVER_ID_ROLE,
                     self.artist_model.IS_LOADING_ROLE]
                )

        # 3. Abort stale cover fetches; immediately load visible covers in dedicated threads
        if hasattr(self, 'cover_worker') and self.cover_worker:
            self.cover_worker.abort_current_batch()
            urgent = []
            for chunk in sorted(visible_chunks):
                if chunk in self.loaded_chunks:
                    cs = chunk * 50
                    ce = min(cs + 50, len(self.artist_model.artists))
                    for artist in self.artist_model.artists[cs:ce]:
                        cid = artist.get('cover_id')
                        if cid and not self.cover_provider.image_cache.get(str(cid)):
                            urgent.append(cid)
            if urgent:
                self.cover_worker.load_urgent(urgent)

        # 4. Fetch visible chunks not yet loaded
        for chunk in sorted(visible_chunks):
            if chunk not in self.loaded_chunks and chunk not in self.active_chunk_workers:
                self.loaded_chunks.add(chunk)
                self.active_chunk_workers[chunk] = self.fetch_chunk(chunk)

    def fetch_chunk(self, chunk_index):
        if hasattr(self, '_chunk_data_cache') and chunk_index in self._chunk_data_cache:
            items = self._chunk_data_cache[chunk_index]
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._on_chunk_loaded(items, chunk_index))
            return None

        query = getattr(self, 'current_query', '')
        sort_type = getattr(self, 'current_sort', 'alphabetical')
        is_ascending = getattr(self, 'sort_states', {}).get(sort_type, True)
        
        # LiveArtistWorker uses "page" math, so chunk 0 = page 1
        worker = LiveArtistWorker(
            self.client, 
            query=query, 
            sort_type=sort_type, 
            is_ascending=is_ascending, 
            page=chunk_index + 1, 
            page_size=50
        )
        worker.page_ready.connect(lambda items, total, pages: self._on_chunk_loaded(items, chunk_index))
        worker.start()
        return worker

    def on_sort_changed(self):
        self.load_artists_page(reset=True)
    
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
        self.create_sort_action(menu, 'most_played', 'Most Played')
        self.create_sort_action(menu, 'alphabetical', 'Alphabetical')
        self.create_sort_action(menu, 'albums_count', 'Albums Count') # 🟢 NEW
        
        # Show menu below burger button
        button_pos = self.burger_btn.mapToGlobal(self.burger_btn.rect().bottomLeft())
        menu.exec(button_pos)
    
    def get_tinted_sort_icon(self, sort_type, is_ascending):
        """Get a tinted icon for the sort menu based on current accent color"""
    
        if sort_type == 'albums_count':
            icon_path = resource_path("img/album.png")
        else:
            suffix = 'a' if is_ascending else 'd'
            icon_path = resource_path(f"img/sort-{sort_type}-{suffix}.png")
        
        try:
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull() and hasattr(self, 'current_accent'):
                # Tint with accent color
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(self.current_accent))
                painter.end()
                return QIcon(pixmap)
        except Exception as e:
            print(f"Error tinting sort icon: {e}")
        
        # Fallback to untinted
        return QIcon(icon_path)
    
    def update_burger_icon(self):
        """Update burger button to show the currently active sort icon"""
        if not hasattr(self, 'current_sort'):
            return
        
        
        if self.current_sort == 'albums_count':
            icon_path = resource_path("img/album.png")
        else:
            is_ascending = self.sort_states.get(self.current_sort, True)
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
        # The Live Worker handles math now, so we just return the tracked variable!
        return getattr(self, 'total_items', 0)

    def create_sort_action(self, menu, sort_type, label):
        """Create a toggleable sort action with icon"""
        is_ascending = self.sort_states[sort_type]
        icon = self.get_tinted_sort_icon(sort_type, is_ascending)
        action = QAction(icon, f"  {label}", self)
        
        action.triggered.connect(lambda checked=False, st=sort_type: self.toggle_sort_state(st))
        menu.addAction(action)

    def toggle_sort_state(self, sort_type):
        """Toggle the sort state and update display"""
        import time
        if self.current_sort == sort_type:
            # If already on this sort, just flip the direction
            self.sort_states[sort_type] = not self.sort_states[sort_type]
            if sort_type == 'random': self.random_seed = time.time()
        else:
        
            self.current_sort = sort_type
            
            
            if sort_type in ['most_played', 'albums_count']:
                self.sort_states[sort_type] = False
            else:
                self.sort_states[sort_type] = True
                
            if sort_type == 'random': self.random_seed = time.time()
            
        self.update_burger_icon()
        self.load_artists_page(reset=True)

    def load_artists_page(self, reset=False):
        
        if getattr(self, '_restored_state_waiting', False):
            reset = False 
            self._restored_state_waiting = False
            
        if reset:
            self.current_page = 0

        if not getattr(self, 'client', None): return
        if self.cover_worker: self.cover_worker.queue.clear()

        if reset:
            self._chunk_data_cache = {}

        # 1. MASS CANCEL: Kill any currently running workers safely
        if hasattr(self, 'active_chunk_workers'):
            for chunk, worker in list(self.active_chunk_workers.items()):
                self._safe_discard_worker(worker)
            self.active_chunk_workers.clear()
            
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None

        # 2. Reset our tracking variables
        self.artist_model.clear()
        self.pending_items.clear()
        self.loaded_chunks = set()

        query = getattr(self, 'current_query', '')
        sort_type = getattr(self, 'current_sort', 'alphabetical')
        is_ascending = getattr(self, 'sort_states', {}).get(sort_type, True)
        
        # 2. If it's a search, dump everything instantly (no virtualization)
        if query:
            worker = LiveArtistWorker(self.client, query, sort_type, is_ascending, page=1, page_size=500)
            worker.page_ready.connect(self._on_search_loaded)
            worker.start()
            self.live_worker = worker
            return

        # 3. IF BROWSING: Find the library size first
        if not hasattr(self, 'true_server_count') or self.true_server_count == 0:
            worker = LiveArtistWorker(self.client, query="", sort_type=sort_type, is_ascending=is_ascending, page=1, page_size=1)
            worker.page_ready.connect(self._on_initial_count_loaded)
            worker.start()
            self.live_worker = worker
            return

        total_count = self.true_server_count
        self.status_label.setText(f"{total_count:,} artists")
        
        # 4. Inject Placeholders
        if total_count > 0:
            placeholders = [{'type': 'placeholder', 'name': 'Loading...'} for _ in range(total_count)]
            self.artist_model.append_artists(placeholders)
            self.check_viewport_qml(0, 50)

    def _on_initial_count_loaded(self, items, total_items, total_pages):
        self.true_server_count = total_items if total_items else 0
        self.load_artists_page()

    def _on_search_loaded(self, items, total_items, total_pages):
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None
        self.artist_model.clear()
        self.status_label.setText(f"{len(items)} result{'s' if len(items) != 1 else ''}")
        self.populate_grid(items)
        # Only grab focus if the search bar isn't actively being typed into
        search_has_focus = (hasattr(self, 'search_container') and
                            hasattr(self.search_container, 'search_input') and
                            self.search_container.search_input.hasFocus())
        if not search_has_focus and hasattr(self, 'qml_view') and self.isVisible():
            self.qml_view.setFocus()

    def _on_chunk_loaded(self, artists, chunk_index):
        """Callback when a chunk of 50 artists arrives from the server."""
        is_expected = hasattr(self, 'active_chunk_workers') and chunk_index in self.active_chunk_workers
        if is_expected:
            worker = self.active_chunk_workers.pop(chunk_index)
            self._safe_discard_worker(worker)
        else:
            return  # stale delivery after reset, discard

        if not artists: return

        if not hasattr(self, '_chunk_data_cache'):
            self._chunk_data_cache = {}
        self._chunk_data_cache[chunk_index] = artists

        start_row = chunk_index * 50
        covers_to_queue = []

        for i, artist_data in enumerate(artists):
            target_row = start_row + i
            if target_row >= len(self.artist_model.artists): break

            cid = artist_data.get('artistImageUrl') or artist_data.get('cover_id') or \
                  artist_data.get('coverArt') or artist_data.get('id')
            if cid:
                artist_data['cover_id'] = cid
                covers_to_queue.append(cid)

            self.artist_model.artists[target_row] = artist_data

        items_written = min(len(artists), len(self.artist_model.artists) - start_row)
        if items_written > 0:
            self.artist_model.dataChanged.emit(
                self.artist_model.index(start_row, 0),
                self.artist_model.index(start_row + items_written - 1, 0),
                [self.artist_model.NAME_ROLE, self.artist_model.COVER_ID_ROLE,
                 self.artist_model.IS_LOADING_ROLE]
            )

        if hasattr(self, 'cover_worker') and self.cover_worker:
            self.cover_worker.load_urgent(covers_to_queue)

    def resizeEvent(self, event): 
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()

    def check_for_updates(self):
        pass
    
    def set_client(self, client):
        self.client = client
        if self.client:
            if self.cover_worker:
                self.cover_worker.terminate()
            self.cover_worker = GridCoverWorker(client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()
            self.refresh_grid()

    def refresh_grid(self):
        # 1. Clear the UI memory caches
        self.all_artists_cache = [] 
        self.all_artists_sort = None
        self._live_artists_cache = None
        self._live_artists_randomized = False
        
        # 2. Clear the server count so it asks the server for the new total!
        self.true_server_count = 0
        
        # 3. Brutally wipe the API cache for artists
        if hasattr(self, 'client') and self.client and hasattr(self.client, '_api_cache'):
            keys_to_delete = [k for k in self.client._api_cache.cache.keys() if 'getArtists' in k]
            for k in keys_to_delete:
                del self.client._api_cache.cache[k]
                
        self.load_artists_page(reset=True)

    def load_next_batch(self):
        if self.is_loading or not self.has_more: return
        self.is_loading = True
        QApplication.processEvents()
        
        # Get sort from burger menu
        db_sort = "alphabeticalByName"
        if self.current_sort == "random": 
            db_sort = "random"
        elif self.current_sort == "most_played": 
            db_sort = "play_count"
        elif self.current_sort == "alphabetical": 
            db_sort = "alphabeticalByName"

        # --- Apply descending suffix if state is False ---
        if self.current_sort != 'random':
            if not self.sort_states.get(self.current_sort, True):
                db_sort += "_desc"

        try:
            # Fetch all artists from API once and cache in memory
            if not getattr(self, '_live_artists_cache', None):
                if not self.client:
                    self.is_loading = False
                    return
                self._live_artists_cache = self.client.get_all_artists_index()
                self._live_artists_randomized = False

            all_artists = list(self._live_artists_cache)

            # Apply search filter
            if self.current_query:
                q = self.current_query.lower()
                all_artists = [a for a in all_artists if q in (a.get('name') or '').lower()]

            # Apply sort
            if self.current_sort == "random":
                import random as _rnd
                if not self._live_artists_randomized:
                    _rnd.shuffle(self._live_artists_cache)
                    self._live_artists_randomized = True
                all_artists = list(self._live_artists_cache)
            elif self.current_sort == "alphabetical":
                rev = not self.sort_states.get('alphabetical', True)
                all_artists = sorted(all_artists, key=lambda a: (a.get('name') or '').lower(), reverse=rev)
            elif self.current_sort == "albums_count":
                rev = not self.sort_states.get('albums_count', False)
                all_artists = sorted(all_artists, key=lambda a: int(a.get('albumCount', 0)), reverse=not rev)
            else:  # most_played — no play-count data without DB, fall back to alphabetical
                all_artists = sorted(all_artists, key=lambda a: (a.get('name') or '').lower())

            # Paginate
            items = all_artists[self.offset: self.offset + self.batch_size]
            if len(items) < self.batch_size:
                self.has_more = False

            self.offset += len(items)
            self.populate_grid(items)
            self.recalc_grid_layout()
            
        except Exception as e:
            print(f"Error loading artists from DB: {e}")
            self.has_more = False
            
        self.is_loading = False

    def populate_grid(self, items):
        split_regex = re.compile(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )')
        processed = []
        seen = set()

        for item_data in items:
            name = item_data.get('name') or item_data.get('artist') or ""
            parts = split_regex.split(name)
            tokens = [p.strip() for p in parts if p.strip()]

            for token in tokens:
                token_lower = token.lower()
                if self.current_query and self.current_query.lower() not in token_lower:
                    continue
                if token_lower not in seen:
                    seen.add(token_lower)
                    new_item = dict(item_data)
                    new_item['name'] = token
                    new_item['artist'] = token
                    if len(tokens) > 1:
                        new_item['id'] = None
                    processed.append(new_item)

        self.artist_model.append_artists(processed)

        for artist_data in processed:
            cid = artist_data.get('artistImageUrl') or artist_data.get('cover_id') or \
                  artist_data.get('coverArt') or artist_data.get('id')
            if cid:
                artist_data['cover_id'] = cid
                if self.cover_worker:
                    self.cover_worker.queue_cover(cid, priority=False)

    def filter_grid(self, text):
        if hasattr(self, 'search_container'):
            self.current_query = text
            self.search_container.set_text(text)

        self.load_artists_page(reset=True)

    def start_worker(self, worker):
        self._active_workers.add(worker)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        worker.start()

    def _cleanup_worker(self, worker):
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def show_artist_details(self, artist_data, record_history=True):
        if not artist_data: return
        if record_history: self.add_to_history({'type': 'artist', 'data': artist_data})

        if hasattr(self, 'search_container'): self.search_container.hide()
        if hasattr(self, 'burger_btn'): self.burger_btn.hide()

        self.stack.setCurrentIndex(2)
        self.artist_view.setFocus()
        self.artist_view.client = self.client

        # Share the grid's cover_worker so the detail view uses the same
        # singleton CoverCache. Connect only once to avoid duplicate emissions.
        if self.cover_worker and not getattr(self.artist_view, '_cover_worker_connected', False):
            self.artist_view.cover_worker = self.cover_worker
            try:
                self.cover_worker.cover_ready.connect(self.artist_view.apply_cover)
            except Exception:
                pass
            self.artist_view._cover_worker_connected = True

        self.artist_view.load_artist(artist_data)
        # Scroll back to top so the user sees the new artist header
        self.artist_view.scroll.verticalScrollBar().setValue(0)

    def apply_cover(self, cover_id, image_data):
        # Feed bytes into the QML image provider
        if hasattr(self, 'cover_provider'):
            self.cover_provider.image_cache[str(cover_id)] = image_data

        # Tell QML to redraw that cover
        if hasattr(self, 'artist_model'):
            self.artist_model.update_cover(str(cover_id))

        # Also apply to the artist detail header if it matches
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        if not pixmap.isNull():
            if getattr(self, 'current_header_cover_id', None) == str(cover_id):
                self.artist_view.set_header_image(pixmap)

    def set_accent_color(self, color, alpha=0.3):
        self.current_accent = color

        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(alpha)

        self.detail_view.set_accent_color(color, alpha)
        self.artist_view.set_accent_color(color, alpha)

        if hasattr(self, 'search_btn'):
            try:
                from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon
                icon_path = resource_path("img/search.png")
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    painter = QPainter(pixmap)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    painter.fillRect(pixmap.rect(), QColor(color))
                    painter.end()
                    self.search_btn.setIcon(QIcon(pixmap))
            except: pass

        if hasattr(self, 'search_container'): self.search_container.set_accent_color(color)
        if hasattr(self, 'burger_btn'): self.update_burger_icon()
 
    def on_grid_artist_clicked(self, data):
        """Called by bridge when user clicks an artist card."""
        if not data: return
        self.artist_clicked.emit(data)

    def on_grid_play_clicked(self, data):
        """Called by bridge when user clicks the play button on an artist card."""
        if not data: return
        artist_name = data.get('name') or data.get('artist')
        if artist_name:
            self._grid_play_worker = ArtistPlayWorker(self.client, artist_name)
            self._grid_play_worker.tracks_ready.connect(self.play_album_signal.emit)
            self._grid_play_worker.start()

    def on_artist_album_clicked(self, album_data): 
        self.switch_to_album_tab.emit(album_data)
    
    def on_play_artist_album(self, album_data): 
        self.start_play_fetch(album_data['id'])

    def play_current_artist(self):
        """Live fetches all tracks for the artist and plays them."""
        artist_name = self.artist_view.lbl_name.text()
        if not artist_name or artist_name == "Loading...":
            return
        self.play_worker = ArtistPlayWorker(self.client, artist_name)
        self.play_worker.tracks_ready.connect(self.play_album_signal.emit)
        self.play_worker.start()

    def _sort_and_play(self, tracks, info, artist_id, target_name):
        if not tracks: return
        
        album_prio = {}
        raw_albums = info.get('album', [])
        split_regex = re.compile(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )')

        def _tokens(s):
            return {p.strip().lower() for p in split_regex.split(s) if p.strip()}

        def get_priority(alb_data):
            a_id = str(alb_data.get('artistId', ''))
            check_tokens = _tokens(str(alb_data.get('albumArtist') or alb_data.get('artist') or ''))
            is_main = (artist_id and a_id == str(artist_id)) or (target_name in check_tokens)
            r_types = alb_data.get('releaseTypes', [])
            if isinstance(r_types, list) and r_types: rtype = str(r_types[0]).lower()
            else: rtype = str(alb_data.get('albumType') or alb_data.get('releaseType') or '').lower()
            is_comp = alb_data.get('isCompilation', False) or 'compilation' in rtype
            if not is_main or is_comp: return 2
            if 'single' in rtype or 'ep' in rtype: return 1
            return 0

        for a in raw_albums:
            aid_str = str(a.get('id', ''))
            if aid_str: album_prio[aid_str] = get_priority(a)

        filtered = []
        for t in tracks:
            t_artist_id       = str(t.get('artistId') or t.get('artist_id') or '')
            artist_tokens     = _tokens(str(t.get('artist') or ''))
            alb_artist_tokens = _tokens(str(t.get('albumArtist') or t.get('album_artist') or ''))
            # Keep only tracks that truly belong to this artist
            if not ((artist_id and t_artist_id == str(artist_id))
                    or target_name in artist_tokens
                    or target_name in alb_artist_tokens):
                continue
            filtered.append(t)

        def sort_key(t):
            alb_id = str(t.get('albumId') or t.get('album_id') or '')
            if alb_id in album_prio:
                prio = album_prio[alb_id]
            else:
                alb_artist_raw = str(t.get('albumArtist') or t.get('album_artist') or '')
                is_comp = t.get('isCompilation', False) or 'various' in alb_artist_raw.lower()
                prio = 2 if is_comp else 0
            disc  = int(t.get('discNumber') or t.get('disc_number') or 1)
            track = int(t.get('trackNumber') or t.get('track') or 0)
            return (prio, (t.get('album') or '').lower(), disc, track)

        filtered.sort(key=sort_key)
        self.play_album_signal.emit(filtered)
    
    def start_play_fetch(self, album_id):
        worker = TrackLoaderWorker(self.client, album_id)
        worker.tracks_ready.connect(lambda tracks, aid: self.play_album_signal.emit(tracks) if tracks else None)
        self.start_worker(worker)

    def resolve_tracks(self, album_data):
        album_id = album_data['id']
        if album_id in self.track_cache:
            self.track_cache.move_to_end(album_id)
            self.on_tracks_loaded(self.track_cache[album_id], album_id)
            return
        if not self.client:
             self.detail_view.lbl_meta.setText("Offline: Cannot load tracks")
             return
        worker = TrackLoaderWorker(self.client, album_id)
        worker.tracks_ready.connect(self.on_tracks_loaded)
        self.start_worker(worker)

    def on_tracks_loaded(self, tracks, album_id):
        if not tracks: 
            self.detail_view.lbl_meta.setText("No tracks found.")
            return
            
        self.track_cache[album_id] = tracks
        # Keep the cache bounded — drop oldest entry when over 30 albums
        if len(self.track_cache) > 30:
            oldest_key = next(iter(self.track_cache))
            del self.track_cache[oldest_key]
        total_sec = 0
        all_discs = set()
        model_items = []
        
        for t in tracks:
            dur_val = t.get('duration', 0)
            seconds = 0
            
            # Handle both 213 (int) and "3:33" (string)
            if isinstance(dur_val, int):
                seconds = dur_val
            elif isinstance(dur_val, str):
                if ":" in dur_val:
                    try:
                        parts = dur_val.split(':')
                        if len(parts) == 2: # MM:SS
                            seconds = int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3: # HH:MM:SS
                            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    except ValueError:
                        seconds = 0
                elif dur_val.isdigit():
                    seconds = int(dur_val)

            t['_calc_seconds'] = seconds
            total_sec += seconds
            
            # Format for display column
            m, s = divmod(seconds, 60)
            t['_display_dur'] = f"{m}:{s:02d}"
            
            # Metadata for UI
            all_discs.add(t.get('discNumber', 1) or t.get('disc_number', 1))
            is_fav = t.get('starred', False) or t.get('favorite', False)
            t['_display_fav'] = "♥" if is_fav else "♡"
            t['_fav_color'] = "#E91E63" if is_fav else "#555"
            
            artist_str = t.get('artist', 'Unknown') or ""
            t['_artist_tokens'] = [p for p in re.split(r'( • | / | feat\. | vs\. |, )', artist_str) if p]

        # Update Meta Label
        m, s = divmod(total_sec, 60)
        h, m = divmod(m, 60)
        time_str = f"{h} hr {m} min" if h else f"{m} min {s} sec"
        
        current_txt = self.detail_view.lbl_meta.text().split(' • ')[0]
        self.detail_view.lbl_meta.setText(f"{current_txt} • {len(tracks)} songs • {time_str}")
        
        # Populate Model
        self.full_track_context = tracks
        show_headers = len(all_discs) > 1
        current_disc = None
        for i, t in enumerate(tracks):
            disc_num = t.get('discNumber', 1) or t.get('disc_number', 1)
            t['track_display'] = t.get('track') or t.get('trackNumber') or (i + 1)
            if show_headers and disc_num != current_disc:
                current_disc = disc_num
                model_items.append({'type': 'header', 'disc': current_disc})
            t['type'] = 'track'
            model_items.append(t)
            
        self.detail_view.model.set_items(model_items)

    def on_track_clicked(self, index):
        user_data = index.data(Qt.ItemDataRole.UserRole)
        if user_data and user_data.get('type') == 'track':
            if index.column() == 2: 
                track = user_data['data']
                aid = track.get('artistId') or track.get('artist_id')
                name = track.get('artist')
                if aid: self.show_artist_details({'id': aid, 'name': name})
                return
            self.play_track_signal.emit(user_data['data'])

    def on_play_all_clicked(self):
        if hasattr(self, 'full_track_context'): self.play_album_signal.emit(self.full_track_context)
    
    def on_shuffle_album_clicked(self):
        if hasattr(self, 'full_track_context'): tracks = list(self.full_track_context); random.shuffle(tracks); self.play_album_signal.emit(tracks)
    
    def on_album_heart_clicked(self, is_liked): 
        pass
    
    def on_track_single_clicked(self, index):
        if index.column() == 3: 
            user_data = index.data(Qt.ItemDataRole.UserRole)
            if not user_data or user_data.get('type') != 'track': return
            
            track_data = user_data['data']
            new_state = not track_data.get('starred', False)
            track_data['starred'] = new_state
            track_data['_display_fav'] = "♥" if new_state else "♡"
            track_data['_fav_color'] = "#E91E63" if new_state else "#555"
            
            user_data['data'] = track_data
            self.detail_view.model.setData(index, user_data, Qt.ItemDataRole.UserRole)
            self.detail_view.tree.update(index)
            
            try:
                val = 1 if new_state else 0
            except: pass
            
            if self.client: self.client.set_favorite(track_data.get('id'), new_state)

    def add_to_history(self, state):
        if self.nav_index < len(self.nav_history) - 1: self.nav_history = self.nav_history[:self.nav_index + 1]
        self.nav_history.append(state); self.nav_index += 1
    
    def on_nav_back(self):
        if self.nav_index > 0:
            self.nav_index -= 1
            self.render_state(self.nav_history[self.nav_index])
            
    def on_nav_fwd(self):
        if self.nav_index < len(self.nav_history) - 1: self.nav_index += 1; self.render_state(self.nav_history[self.nav_index])

    def render_state(self, state):
        s_type = state.get('type')
        if state['type'] == 'root':
            self.status_label.show()
            if hasattr(self, 'search_container'):
                self.search_container.show()
                self.search_container.show_search()
                self.search_container.show_burger()
            self.stack.setCurrentIndex(0)
        elif s_type == 'artist':
            self.show_artist_details(state['data'], record_history=False)
        elif s_type == 'album':
            self.show_album_details(state['data'], record_history=False)

    def go_to_root(self):
        self.status_label.show()
        if hasattr(self, 'search_container'):
            self.search_container.show()
            self.search_container.show_search()
            self.search_container.show_burger()

        self.stack.setCurrentIndex(0)

        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()

        # ONLY reload the grid if the user was actively looking at search results!
        is_searching = bool(getattr(self, 'current_query', ""))
        if is_searching:
            self.current_query = ""
            if hasattr(self, 'search_container'):
                self.search_container.search_input.blockSignals(True)
                self.search_container.search_input.clear()
                self.search_container.search_input.blockSignals(False)
            self.load_artists_page(reset=True)

        self.nav_history = [{'type': 'root'}]
        self.nav_index = 0
    
    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.cancelScroll.emit()

    def get_state(self):
        """Returns the current state for saving."""
        return {
            'sort': getattr(self, 'current_sort', 'most_played'),
            'sort_states': getattr(self, 'sort_states', {}),
            'query': getattr(self, 'current_query', '')
        }

    def restore_state(self, state):
        """Applies a saved state before the first load."""
        if not state: return
        
    
        self.current_sort = state.get('sort', 'most_played')
        
        saved_sorts = state.get('sort_states', {})
        for k, v in saved_sorts.items():
            self.sort_states[k] = v
            
        self.current_query = state.get('query', '')
        
        if hasattr(self, 'search_container') and self.current_query:
            self.search_container.search_input.blockSignals(True)
            self.search_container.search_input.setText(self.current_query)
            self.search_container.search_input.blockSignals(False)
            
        if hasattr(self, 'update_burger_icon'):
            self.update_burger_icon()
            
        self._restored_state_waiting = True