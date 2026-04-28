import os
import sys
import json
import hashlib
import re

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QListWidget, QListWidgetItem, QLabel, QFrame, QPushButton)

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QEvent, QThread, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap, QColor, QIcon, QPainter, QPainterPath

      

# --- BACKGROUND WORKER FOR SEARCH ---

class SearchWorker(QThread):
    results_ready = pyqtSignal(list, list, list)  # tracks, artists, albums

    def __init__(self, client, query):
        super().__init__()
        self.client = client
        self.query = query
        self.is_cancelled = False

    def run(self):
        import re
        try:
            api_query = self.query.replace('"', '')
            query_words = api_query.lower().split()

            results = self.client.search3(api_query, size=2000, offset=0, artist_count=20, album_count=20)
            if self.is_cancelled:
                return

            raw_tracks  = results.get('song', [])
            raw_artists = results.get('artist', [])
            raw_albums  = results.get('album', [])

            tracks  = [t for t in raw_tracks  if all(w in str(t.get('title', '')).lower() for w in query_words)]
            artists = [a for a in raw_artists if all(w in str(a.get('name') or a.get('artist', '')).lower() for w in query_words)]
            albums  = [a for a in raw_albums  if all(w in str(a.get('title') or a.get('name', '')).lower() for w in query_words)]

            def sort_score(name):
                name = str(name).lower().strip()
                q = api_query.lower().strip()
                if name == q:                                  return (1, len(name))
                if re.search(rf"\b{re.escape(q)}\b", name):
                    return (2, len(name)) if name.startswith(q) else (3, len(name))
                if name.startswith(q):                         return (4, len(name))
                return (5, len(name))

            tracks.sort(key=lambda t: sort_score(t.get('title', '')))
            tracks = tracks[:6]

            for a in artists:
                a['title'] = a.get('name') or a.get('artist') or 'Unknown'
                a['album_count'] = a.get('albumCount') or 0
            artists.sort(key=lambda a: sort_score(a['title']))
            artists = artists[:4]

            for a in albums:
                a['title'] = a.get('title') or a.get('name') or 'Unknown'
                a['artist'] = a.get('artist') or 'Various Artists'
            albums.sort(key=lambda a: sort_score(a['title']))
            albums = albums[:4]

            if not self.is_cancelled:
                self.results_ready.emit(tracks, artists, albums)
        except Exception as e:
            print(f"[SearchWorker] Error: {e}")


# --- BACKGROUND WORKER FOR COVERS ---

class SearchCoverWorker(QThread):
    cover_ready = pyqtSignal(bytes)
    
    def __init__(self, client, cover_id, parent=None):
        super().__init__(parent)
        self.client = client
        self.cover_id = cover_id
        
    def run(self):
        try:
            data = self.client.get_cover_art(self.cover_id, size=150)
            if data:
                cache_dir = "covers_cache"
                if not os.path.exists(cache_dir): os.makedirs(cache_dir)
                
                img_hash = hashlib.md5(data).hexdigest()
                img_path = os.path.join(cache_dir, f"{img_hash}.jpg")
                link_path = os.path.join(cache_dir, f"{self.cover_id}.link")
                
                if not os.path.exists(img_path):
                    with open(img_path, "wb") as f: f.write(data)
                with open(link_path, "w") as f: f.write(img_hash)
                
                self.cover_ready.emit(data)
        except Exception:
            pass

# --- CATEGORY HEADER WIDGET ---

class SearchHeaderRow(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFixedHeight(35)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 10, 0)
        
        lbl = QLabel(title)
        lbl.setStyleSheet("color: #777; font-size: 11px; font-weight: bold; text-transform: uppercase; background: transparent; border: none;")
        layout.addWidget(lbl)

# --- INDIVIDUAL RESULT ROW WIDGET ---

class SearchResultRow(QWidget):
    action_requested = pyqtSignal(dict, str) 

    def __init__(self, item_data, main_window, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.main_window = main_window
        self.setFixedHeight(64)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 15, 8)
        layout.setSpacing(15)

        # 1. Cover Art
        self.cover = QLabel()
        self.cover.setFixedSize(48, 48)
        self.cover.setStyleSheet("background-color: #222; border-radius: 4px; border: none;")
        self.cover.setScaledContents(True)
        self.load_cover()
        layout.addWidget(self.cover)

        # 2. Text Info
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setSpacing(2)
        
        title = item_data.get('title') or "Unknown"
        subtitle = item_data.get('subtitle') or ""
        
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 14px; background: transparent; border: none;")
        
        self.lbl_subtitle = QLabel(subtitle)
        self.lbl_subtitle.setStyleSheet("color: #aaa; font-size: 12px; background: transparent; border: none;")
        
        info_layout.addWidget(self.lbl_title)
        info_layout.addWidget(self.lbl_subtitle)
        info_layout.addStretch()
        layout.addLayout(info_layout, stretch=1)

        # 3. ACTION BUTTONS 
        self.action_container = QWidget()
        self.action_layout = QHBoxLayout(self.action_container)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(8)
        
        mc = getattr(self.main_window, 'master_color', '#1DB954')
        
        # --- A. Standard Play Button ---
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(36, 36)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setStyleSheet(f"""
            QPushButton {{ background-color: {mc}; border-radius: 18px; border: none; padding: 0; }}
            QPushButton:hover {{ background-color: {self.adjust_color(mc, 1.1)}; }}
        """)
        self.set_play_icon()
        
        
        item_type = item_data.get('type', 'track')
        if item_type == 'artist':
            self.btn_play.setToolTip("Play Artist")
        elif item_type == 'album':
            self.btn_play.setToolTip("Play Album")
        else:
            self.btn_play.setToolTip("Play Track")
            
        self.btn_play.installEventFilter(self.main_window)
        
        self.btn_play.clicked.connect(lambda: self.action_requested.emit(self.item_data, 'play_default'))
        self.action_layout.addWidget(self.btn_play)

        # --- B. Secondary Action Button (Play Album OR Enter View) ---
        item_type = item_data.get('type', 'track')
        
        # Track gets the Play Album button
        if item_type == 'track' and item_data.get('albumId'):
            self.btn_album = QPushButton()
            self.btn_album.setFixedSize(36, 36)
            self.btn_album.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_album.setStyleSheet("""
                QPushButton { background-color: transparent; border-radius: 18px; border: none; padding: 0; }
                QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            """)
            
            self.btn_album.setToolTip("Play Full Album (Shift+↵)")
            self.btn_album.installEventFilter(self.main_window)
            
            # Use our new helper!
            self.set_custom_icon(self.btn_album, "img/album.png")
            self.btn_album.clicked.connect(lambda: self.action_requested.emit(self.item_data, 'play_album'))
            self.action_layout.addWidget(self.btn_album)
            
       
        elif item_type in ('album', 'artist'):
            self.btn_enter = QPushButton()
            self.btn_enter.setFixedSize(36, 36)
            self.btn_enter.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_enter.setStyleSheet("""
                QPushButton { background-color: transparent; border-radius: 18px; border: none; padding: 0; }
                QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            """)
            
            view_name = "Artist" if item_type == 'artist' else "Album"
            self.btn_enter.setToolTip(f"Enter {view_name} view (Shift+↵)")
            self.btn_enter.installEventFilter(self.main_window)
            
            # Tint and load the enter.png icon!
            self.set_custom_icon(self.btn_enter, "img/enter.png")
            self.btn_enter.clicked.connect(lambda: self.action_requested.emit(self.item_data, 'enter_view'))
            self.action_layout.addWidget(self.btn_enter)

        self.action_container.hide()

        btn_wrap_layout = QVBoxLayout()
        btn_wrap_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        btn_wrap_layout.addWidget(self.action_container)
        layout.addLayout(btn_wrap_layout)

        self._list_widget = None
        self._list_item = None

    def set_play_icon(self):
        pix = QPixmap(36, 36)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(13, 10); path.lineTo(25, 18); path.lineTo(13, 26); path.closeSubpath()
        painter.fillPath(path, QColor("black"))
        painter.end()
        self.btn_play.setIcon(QIcon(pix))
        self.btn_play.setIconSize(QSize(36, 36))

    def set_custom_icon(self, button, filename):
        mc = getattr(self.main_window, 'master_color', '#1DB954')
        base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
        icon_path = os.path.join(base_path, filename)
        
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path).scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            colored = QPixmap(pixmap.size())
            colored.fill(Qt.GlobalColor.transparent)
            
            painter = QPainter(colored)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, pixmap)
            
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(colored.rect(), QColor(mc))
            painter.end()
            
            button.setIcon(QIcon(colored))
            button.setIconSize(QSize(20, 20)) 
        else:
            button.setText("➔")
            button.setStyleSheet(f"color: {mc}; background: transparent; border: none;")

    def set_list_item(self, list_widget, list_item):
        self._list_widget = list_widget
        self._list_item = list_item

    def enterEvent(self, event):
        if self._list_widget and self._list_item:
            self._list_widget.setCurrentItem(self._list_item)
        super().enterEvent(event)

    def set_active_state(self, is_active):
        if is_active: self.action_container.show()
        else: self.action_container.hide()

    def adjust_color(self, hex_color, factor):
        c = QColor(hex_color)
        h, s, l, a = c.getHsl()
        l = min(int(l * factor), 255)
        c.setHsl(h, s, l, a)
        return c.name()

    def load_cover(self):
        cover_id = self.item_data.get('cover_id')
        if not cover_id: return
        link_path = os.path.join("covers_cache", f"{cover_id}.link")
        if os.path.exists(link_path):
            try:
                with open(link_path, "r") as f: img_hash = f.read().strip()
                img_path = os.path.join("covers_cache", f"{img_hash}.jpg")
                if os.path.exists(img_path):
                    self.cover.setPixmap(QPixmap(img_path))
                    return
            except: pass

        client = getattr(self.main_window, 'navidrome_client', None)
        if client:
            self.cover_worker = SearchCoverWorker(client, cover_id, parent=self.main_window)
            self.cover_worker.cover_ready.connect(self.apply_downloaded_cover)
            self.cover_worker.finished.connect(self.cover_worker.deleteLater)
            self.cover_worker.start()

    def apply_downloaded_cover(self, data):
        pix = QPixmap()
        pix.loadFromData(data)
        self.cover.setPixmap(pix)

# --- MAIN SPOTLIGHT OVERLAY ---

class SpotlightSearch(QWidget):
    play_requested = pyqtSignal(dict)           # Single tracks
    play_multiple_requested = pyqtSignal(list)  # Albums and Artists
    view_requested = pyqtSignal(dict)           # Albums and Artists
    search_opened = pyqtSignal()
    search_closed = pyqtSignal()

    def __init__(self, parent_window, db):
    # Pass None so it becomes a top-level window, store parent_window separately
        super().__init__(None)
        self.parent_window = parent_window
        self.parent_window.installEventFilter(self)
        self.db = db
        self.hide()

        # Frameless + always on top at the OS level, same as QQuickWidget
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._bg_alpha = 0
        self.setStyleSheet("SpotlightSearch { background-color: transparent; }")

        self._dim_timer = QTimer()
        self._dim_timer.setInterval(16)
        self._dim_target = 0
        self._dim_step = 0

        def _dim_tick():
            self._bg_alpha += self._dim_step
            if self._dim_step > 0 and self._bg_alpha >= self._dim_target:
                self._bg_alpha = self._dim_target
                self._dim_timer.stop()
            elif self._dim_step < 0 and self._bg_alpha <= self._dim_target:
                self._bg_alpha = self._dim_target
                self._dim_timer.stop()
            self.update()

        self._dim_timer.timeout.connect(_dim_tick)
        
        self.container = QWidget(self)
        self.container.setFixedWidth(750)
        self.container.setObjectName("SpotlightContainer")
        self.container.setStyleSheet("#SpotlightContainer { background-color: #0d0d0d; border-radius: 8px; border: 1px solid #2a2a2a; }")
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 15, 0, 10)
        container_layout.setSpacing(0)
        
        input_container = QWidget()
        input_container.setStyleSheet("background: transparent; border: none;") 
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(20, 0, 20, 10)
        
        self.input = QLineEdit()
        self.input.setPlaceholderText("Search for songs, artists, or albums...")
        self.input.setStyleSheet("""
            QLineEdit { background: transparent; color: white; font-size: 24px; border: none; padding-bottom: 5px; outline: none; }
            QLineEdit:focus { border: none; outline: none; }
        """)
        self.input.textChanged.connect(self.on_text_changed)
        self.input.installEventFilter(self)
        input_layout.addWidget(self.input)
        container_layout.addWidget(input_container)
        
        self.line = QFrame()
        self.line.setFrameShape(QFrame.Shape.HLine)
        self.line.setFrameShadow(QFrame.Shadow.Plain)
        self.line.setFixedHeight(1)
        self.line.setStyleSheet("background-color: #2a2a2a; border: none;")
        self.line.hide() # 🟢 THE FIX: Hide it by default!
        container_layout.addWidget(self.line)

        self.list_widget = QListWidget()
        self.apply_list_stylesheet()
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        self.list_widget.hide()
        container_layout.addWidget(self.list_widget)
        
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250) 
        self.search_timer.timeout.connect(self.perform_search)

    def apply_quote_filter(self, results, query, category='track'):
        """
        Filters results to strictly enforce "exact phrases" wrapped in double quotes.
        Now it only looks at the specific field based on the category!
        """
        import re
        exact_phrases = [p.lower() for p in re.findall(r'"([^"]+)"', query)]
        
        if not exact_phrases:
            return results
            
        compiled_patterns = [re.compile(rf"\b{re.escape(phrase)}\b") for phrase in exact_phrases]
            
        filtered_results = []
        for item in results:
            # 🟢 Only look at the relevant field for this category
            if category == 'track':
                target_text = str(item.get('title') or '').lower()
            elif category == 'artist':
                target_text = str(item.get('name') or item.get('artist') or '').lower()
            else: # album
                target_text = str(item.get('title') or item.get('name') or '').lower()
            
            if all(pattern.search(target_text) for pattern in compiled_patterns):
                filtered_results.append(item)
                
        return filtered_results
    
    def apply_list_stylesheet(self):
        mc = getattr(self.parent_window, 'master_color', '#1DB954')
        self.list_widget.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; outline: none; margin-top: 5px; }}
            QListWidget::item {{ border-radius: 6px; margin: 2px 10px; border: none; }}
            QListWidget::item:selected {{ background-color: rgba(255, 255, 255, 0.06); border: none; }}
            QScrollBar:vertical {{ border: none; background: rgba(0, 0, 0, 0.05); width: 10px; margin: 0; }} 
            QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }} 
            QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {mc}; }} 
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }} 
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        """)

    def on_selection_changed(self, current, previous):
        if previous:
            widget = self.list_widget.itemWidget(previous)
            if isinstance(widget, SearchResultRow): widget.set_active_state(False)
        if current:
            widget = self.list_widget.itemWidget(current)
            if isinstance(widget, SearchResultRow): widget.set_active_state(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, int(self._bg_alpha)))
        painter.end()

    def show_search(self, initial_char=""):
        self.apply_list_stylesheet()
        
        # Manually match the parent window's geometry since we're no longer a child
        geo = self.parent_window.geometry()
        self.setGeometry(geo)
        self.container.move((self.width() - self.container.width()) // 2,
                            (self.height() - self.container.height()) // 2)
        
        self.input.setText(initial_char)
        self._dim_timer.stop()
        self._bg_alpha = 0
        self._dim_target = 200
        self._dim_step = 200 / 10
        self._was_visible = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.input.setCursorPosition(len(initial_char))
        self._dim_timer.start()
        self.search_opened.emit()

    def hide_search(self):
        if hasattr(self, '_search_worker') and self._search_worker.isRunning():
            self._search_worker.is_cancelled = True
            self._search_worker.finished.connect(self._search_worker.deleteLater)
        if hasattr(self, '_play_artist_worker') and self._play_artist_worker.isRunning():
            self._play_artist_worker.is_cancelled = True
            self._play_artist_worker.finished.connect(self._play_artist_worker.deleteLater)
            try: self._play_artist_worker.tracks_ready.disconnect()
            except: pass
        self.input.clear()
        self.list_widget.clear()
        self.list_widget.hide()
        self.line.hide()
        self.container.setFixedHeight(90)
        self._dim_timer.stop()
        self._dim_target = 0
        self._dim_step = -(200 / 10)
        self._dim_timer.start()
        fade_ms = int(abs(200 / self._dim_step) * self._dim_timer.interval()) + 20
        QTimer.singleShot(fade_ms, self._finish_hide)

    def _finish_hide(self):
        self.hide()
        if self.parent_window and not getattr(self, '_entering_view', False):
            self.parent_window.setFocus()
        self._entering_view = False

    def hideEvent(self, event):
        super().hideEvent(event)
        if getattr(self, '_was_visible', False):
            self.search_closed.emit()
        self._was_visible = False

    def on_text_changed(self, text):
        if not text.strip():
            self.list_widget.hide()
            self.line.hide()
            self.container.setFixedHeight(90)
            self.container.move((self.width() - self.container.width()) // 2, (self.height() - self.container.height()) // 2)
            return
        self.search_timer.start()

    def _unpack_json(self, row_dict):
        final_dict = {}
        if row_dict.get('track_data'):
            try: final_dict.update(json.loads(row_dict['track_data']))
            except: pass
        final_dict.update(row_dict)
        return final_dict

    def perform_search(self):
        query = self.input.text().strip()
        if not query: return

        client = getattr(self.parent_window, 'navidrome_client', None)
        if not client: return

        # Cancel previous worker — keep a reference until the thread exits to avoid
        # "QThread: Destroyed while thread is still running" crash.
        if hasattr(self, '_search_worker') and self._search_worker.isRunning():
            old = self._search_worker
            old.is_cancelled = True
            old.finished.connect(old.deleteLater)

        self._search_worker = SearchWorker(client, query)
        self._search_worker.results_ready.connect(self._on_results)
        self._search_worker.start()

    def _on_results(self, tracks, artists, albums):
        if self.sender() and getattr(self.sender(), 'is_cancelled', False):
            return
        if not self.isVisible():
            return
        query = self.input.text().strip()
        if not query:
            return

        # Quote filter runs on main thread (cheap — no network)
        if '"' in query:
            tracks  = self.apply_quote_filter(tracks,  query, category='track')
            artists = self.apply_quote_filter(artists, query, category='artist')
            albums  = self.apply_quote_filter(albums,  query, category='album')

        self.list_widget.clear()

        def add_header(title):
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(self.list_widget.width(), 35))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_widget.setItemWidget(item, SearchHeaderRow(title))

        def add_row(data_dict):
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(self.list_widget.width(), 64))
            row_widget = SearchResultRow(data_dict, self.parent_window)
            row_widget.action_requested.connect(self._handle_row_action)
            row_widget.set_list_item(self.list_widget, item)
            self.list_widget.setItemWidget(item, row_widget)
            item.setData(Qt.ItemDataRole.UserRole, data_dict)

        if tracks:
            add_header("Tracks")
            for t in tracks:
                t['type'] = 'track'
                t['subtitle'] = t.get('artist') or "Unknown Artist"
                t['cover_id'] = t.get('coverArt') or t.get('albumId')
                add_row(t)

        if artists:
            add_header("Artists")
            for a in artists:
                a['type'] = 'artist'
                cnt = a.get('album_count', 0)
                a['subtitle'] = f"{cnt} album{'s' if cnt != 1 else ''}" if cnt else "Artist"
                a['cover_id'] = a.get('cover_id') or a.get('coverArt') or a.get('id')
                add_row(a)

        if albums:
            add_header("Albums")
            for a in albums:
                a['type'] = 'album'
                a['subtitle'] = a.get('artist') or "Various Artists"
                a['cover_id'] = a.get('coverArt') or a.get('id')
                add_row(a)

        if albums or artists or tracks:
            self.list_widget.show()
            self.line.show()
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.list_widget.setCurrentRow(i); break

            exact_list_height = sum(self.list_widget.item(i).sizeHint().height() for i in range(self.list_widget.count())) + 10
            max_list_height = int(self.parent_window.height() * 0.85)
            actual_list_height = min(exact_list_height, max_list_height)
            self.list_widget.setFixedHeight(actual_list_height)
            self.container.setFixedHeight(90 + actual_list_height)
        else:
            self.list_widget.hide()
            self.line.hide()
            self.container.setFixedHeight(90)

        self.container.move((self.width() - self.container.width()) // 2, (self.height() - self.container.height()) // 2)

    def _handle_row_action(self, data, action_type):
        if action_type == 'play_default':
            self._play_item(data)
        elif action_type == 'play_album':
            fake_album_data = {
                'type': 'album',
                'id': data.get('albumId'),
                'title': data.get('album')
            }
            self._play_item(fake_album_data)
        # Intercept the Enter View click and emit the signal!
        elif action_type == 'enter_view':
            self._entering_view = True
            self.hide_search()
            self.view_requested.emit(data)
    
    def _play_item(self, data):
        self.hide_search()
        item_type = data.get('type')
        client = getattr(self.parent_window, 'navidrome_client', None)
        
        if item_type == 'track':
            self.play_requested.emit(data)
            
        elif item_type == 'album':
            album_id = str(data.get('id', ''))
            if not album_id or not client: return
            try:
                # Direct API call to fetch all tracks for the album
                tracks = client.get_album_tracks(album_id)
                if tracks:
                    self.play_multiple_requested.emit(tracks)
            except Exception as e: 
                print(f"Error fetching album from server: {e}")
                
        elif item_type == 'artist':
            artist_name = data.get('title') or data.get('name') or ''
            if not artist_name or not client: return
            

            from artists_browser import ArtistPlayWorker
            
            print(f"[Spotlight] Starting strict track fetch for {artist_name}...")
            self._play_artist_worker = ArtistPlayWorker(client, artist_name)
            
            # The worker emits a list of tracks, perfectly matching what play_multiple_requested expects
            self._play_artist_worker.tracks_ready.connect(self.play_multiple_requested.emit)
            self._play_artist_worker.start()

    def on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data: self._play_item(data)

    def eventFilter(self, obj, event):
        # Track parent window move/resize to keep overlay aligned
        if obj == self.parent_window:
            if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                if self.isVisible():
                    self.setGeometry(self.parent_window.geometry())
                    self.container.move(
                        (self.width() - self.container.width()) // 2,
                        (self.height() - self.container.height()) // 2
                    )
            return False

        if obj == self.input:

            # --- KEY DOWN ---
            if event.type() == QEvent.Type.KeyPress:
                key = event.key()

                if key == Qt.Key.Key_Escape:
                    self.hide_search()
                    return True

                # 🟢 THE HYBRID OVERRIDE: If the user strikes an action key before the timer finishes,
                # kill the timer and search INSTANTLY!
                timer_was_active = False
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self.search_timer.isActive():
                        self.search_timer.stop()
                        self.perform_search()
                        timer_was_active = True

                        # If they hit an arrow key to force the search, perform_search() already auto-highlighted
                        # the first item. We consume the key here so it doesn't accidentally skip to the 2nd item!
                        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                            return True

                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    if self.list_widget.count() > 0:
                        current_row = self.list_widget.currentRow()

                        if key == Qt.Key.Key_PageDown:
                            next_row = min(current_row + 5, self.list_widget.count() - 1)
                            direction = -1
                        elif key == Qt.Key.Key_PageUp:
                            next_row = max(current_row - 5, 0)
                            direction = 1
                        else:
                            direction = 1 if key == Qt.Key.Key_Down else -1
                            next_row = current_row + direction

                        while 0 <= next_row < self.list_widget.count():
                            if self.list_widget.item(next_row).flags() & Qt.ItemFlag.ItemIsSelectable:
                                self.list_widget.setCurrentRow(next_row)
                                break
                            next_row += direction
                    return True

                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.isAutoRepeat(): return True

                    item = self.list_widget.currentItem()
                    if item:
                        data = item.data(Qt.ItemDataRole.UserRole)
                        if data:
                            # SHIFT + ENTER: Secondary Actions
                            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                                if data.get('type') == 'track' and data.get('albumId'):
                                    self._handle_row_action(data, 'play_album')
                                elif data.get('type') in ('album', 'artist'):
                                    self._handle_row_action(data, 'enter_view')
                                else:
                                    self._play_item(data)

                            # REGULAR ENTER: Play standard item
                            else:
                                self._play_item(data)

                    return True

        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if not self.container.geometry().contains(event.pos()): self.hide_search()
        else: super().mousePressEvent(event)