import time
import math

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QUrl, QSize, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter
from PyQt6.QtQuickWidgets import QQuickWidget

from albums_browser import (GridCoverWorker, AlbumModel, GridBridge,
                              CoverImageProvider, QMLGridWrapper, resource_path)

from tracks_browser import MiddleClickScroller


# ─────────────────────────────────────────────────────────────────────────────
# Background workers
# ─────────────────────────────────────────────────────────────────────────────

class HomeLoaderWorker(QThread):
    data_ready = pyqtSignal(list, list)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            recent = random_mix = []
            if self.client:
                recent    = self.client.get_album_list_sorted(sort_type="newest", size=16)
                random_mix = self.client.get_album_list_sorted(sort_type="random", size=16)
            self.data_ready.emit(recent, random_mix)
        except Exception as e:
            print(f"[Home Worker] Error: {e}")
            self.data_ready.emit([], [])

class RandomMixReloaderWorker(QThread):
    data_ready = pyqtSignal(list)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            result = []
            if self.client:
                result = self.client.get_album_list_sorted(sort_type="random", size=16)
            self.data_ready.emit(result or [])
        except Exception as e:
            print(f"[RandomMixReloader] Error: {e}")
            self.data_ready.emit([])


# ─────────────────────────────────────────────────────────────────────────────
# HomeView
# ─────────────────────────────────────────────────────────────────────────────

class HomeView(QWidget):
    album_clicked  = pyqtSignal(dict)
    play_album     = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)

    
    ROW_HEIGHT = 280

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.last_reload_time = time.time()
        self.current_accent = "#1DB954"

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # ── Cover worker (shared; feeds both rows) ────────────────────────
        self.cover_worker = None
        if self.client:
            self._start_cover_worker()

        # ── Top-level layout ──────────────────────────────────────────────
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar (mirrors albums_browser style)
        self.header_container = QWidget()
        self.header_container.setFixedHeight(45)
        self.header_container.setStyleSheet(
            "QWidget { background-color: #111; border-top-left-radius: 5px; "
            "border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(20, 0, 20, 0)
        main_layout.addWidget(self.header_container)

        # Scroll area
        self.scroll = QScrollArea()
        self.scroll.setObjectName("HomeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.omni_scroller = MiddleClickScroller(self.scroll)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 20, 0, 50)
        self.content_layout.setSpacing(30)
        self.scroll.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll)

        # ── Recently Added section ────────────────────────────────────────
        self.recent_section, _ = self._make_section("Recently Added")
        self.recent_model      = AlbumModel()
        self.recent_bridge     = GridBridge(self.recent_model)
        self.recent_bridge.itemClicked.connect(self.album_clicked.emit)
        self.recent_bridge.playClicked.connect(self.play_album.emit)
        self.recent_bridge.artistClicked.connect(self._on_artist_clicked)
        self.recent_provider   = CoverImageProvider()
        self.recent_qml        = self._make_qml_row(
            self.recent_model, self.recent_bridge, self.recent_provider)
        self.recent_section.layout().addWidget(self.recent_qml)
        self.recent_bridge.indexChanged.connect(self._on_recent_index_changed)
        self.recent_bridge.requestFocusNext.connect(lambda: QTimer.singleShot(0, self._focus_random))
        self.content_layout.addWidget(self.recent_section)

        # ── Random Mix section ────────────────────────────────────────────
        self.random_section, self.btn_refresh = self._make_section(
            "Random Mix", with_refresh=True)
        self.random_model  = AlbumModel()
        self.random_bridge = GridBridge(self.random_model)
        self.random_bridge.itemClicked.connect(self.album_clicked.emit)
        self.random_bridge.playClicked.connect(self.play_album.emit)
        self.random_bridge.artistClicked.connect(self._on_artist_clicked)
        self.random_provider = CoverImageProvider()
        self.random_qml      = self._make_qml_row(
            self.random_model, self.random_bridge, self.random_provider)
        self.random_section.layout().addWidget(self.random_qml)
        self.random_bridge.indexChanged.connect(self._on_random_index_changed)
        self.random_bridge.requestFocusPrev.connect(lambda: QTimer.singleShot(0, self._focus_recent))
        self.content_layout.addWidget(self.random_section)


        self.content_layout.addStretch()

        self.set_accent_color("#888888", 0.3)

        if self.client:
            self.load_data()

    # ── Section builder ───────────────────────────────────────────────────

    def adjust_grid_heights(self):
        """Dynamically calculates and sets the height of the QML widgets."""
        if not hasattr(self, 'scroll') or not self.scroll.viewport(): return
        
        available_width = self.scroll.viewport().width()
        if available_width < 100: return

        qml_left_margin, qml_right_margin, item_gap, base_item_size = 20, 20, 10, 180
        qml_width = available_width - qml_left_margin - qml_right_margin
        
        items_per_row = max(1, int(qml_width // (base_item_size + (item_gap * 2))))
        width_per_item = qml_width / items_per_row
        cell_height = width_per_item + 70

        def get_required_height(model):
            count = model.rowCount()
            if count == 0: return 300
            rows = math.ceil(count / items_per_row)
            return int((rows * cell_height) + 20)

        if hasattr(self, 'recent_qml') and self.recent_model.rowCount() > 0:
            self.recent_qml.setFixedHeight(get_required_height(self.recent_model))
            
        if hasattr(self, 'random_qml') and self.random_model.rowCount() > 0:
            self.random_qml.setFixedHeight(get_required_height(self.random_model))
       
    def _make_section(self, title, with_refresh=False):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(20, 0, 20, 0)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            "color: #eee; font-size: 15px; font-weight: bold; background: transparent;")
        header.addWidget(lbl)
        header.addStretch()

        btn = None
        if with_refresh:
            btn = QPushButton()
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip("Refresh Mix")
            btn.setFixedSize(28, 22)
            btn.setIcon(self.get_tinted_icon("#aaa"))
            btn.setIconSize(QSize(14, 14))
            btn.setStyleSheet(
                "QPushButton { background-color: #333; border: none; border-radius: 4px; }")
            btn.clicked.connect(self.refresh_random_mix)
            btn.installEventFilter(self)
            header.addWidget(btn)

        layout.addLayout(header)
        return container, btn

    # ── QML row builder ───────────────────────────────────────────────────

    def _make_qml_row(self, model, bridge, provider):
        qml = QMLGridWrapper()
        qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        qml.setFixedHeight(self.ROW_HEIGHT)
        qml.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        qml.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        qml.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        qml.setClearColor(Qt.GlobalColor.transparent)
        qml.setStyleSheet("background: transparent; border: none;")

        ctx = qml.rootContext()
        ctx.setContextProperty("albumModel", model)
        ctx.setContextProperty("bridge", bridge)

        engine = qml.engine()
        if not engine.imageProvider("covers"):
            engine.addImageProvider("covers", provider)

        qml.setSource(QUrl.fromLocalFile(resource_path("home_row.qml")))
        return qml
  
    def focus_first_grid(self):
        """Give keyboard focus to the recent row when home tab is activated."""
        self._focus_recent()

    def _focus_recent(self):
        self.recent_qml.setFocus(Qt.FocusReason.OtherFocusReason)
        self.recent_bridge.takeFocus.emit()
        
        # 🟢 FIX: Manually force the scroll. If the index didn't change, 
        # the QML signal won't fire, so we must scroll explicitly.
        current_idx = getattr(self, '_recent_idx', 0)
        self._scroll_to_item(self.recent_qml, current_idx)

    def _focus_random(self):
        self.random_qml.setFocus(Qt.FocusReason.OtherFocusReason)
        self.random_bridge.takeFocus.emit()
        
        # 🟢 FIX: Explicitly scroll to the active item in the bottom grid.
        current_idx = getattr(self, '_random_idx', 0)
        self._scroll_to_item(self.random_qml, current_idx)

    def _on_recent_index_changed(self, idx):
        self._recent_idx = idx
        self._scroll_to_item(self.recent_qml, idx)

    def _on_random_index_changed(self, idx):
        self._random_idx = idx
        self._scroll_to_item(self.random_qml, idx)

    def _scroll_to_item(self, qml_widget, idx):
        """Scroll the Python QScrollArea so the selected grid item is visible."""
        if not hasattr(self, 'scroll') or idx < 0:
            return
        try:
            from PyQt6.QtCore import QPoint # Ensure QPoint is imported
            
            available_width = self.scroll.viewport().width()
            if available_width < 100:
                return
            left_margin, right_margin, item_gap, base_item_size = 20, 20, 10, 180
            qml_width = available_width - left_margin - right_margin
            items_per_row = max(1, int(qml_width // (base_item_size + item_gap * 2)))
            width_per_item = qml_width / items_per_row
            cell_height = width_per_item + 70
            top_margin = 20

            row = idx // items_per_row
            item_y_in_grid = top_margin + row * cell_height
            
            # 🟢 THE FIX: Map the widget's local position to the global Scroll Content!
            mapped_pos = qml_widget.mapTo(self.content_widget, QPoint(0, 0))
            item_y_global = mapped_pos.y() + item_y_in_grid

            sb = self.scroll.verticalScrollBar()
            viewport_top = sb.value()
            viewport_bottom = viewport_top + self.scroll.viewport().height()

            if item_y_global < viewport_top:
                sb.setValue(int(item_y_global))
            elif item_y_global + cell_height > viewport_bottom:
                sb.setValue(int(item_y_global + cell_height - self.scroll.viewport().height()))
        except Exception as e:
            print(f"Scroll Error: {e}")

    def resizeEvent(self, event):
        """Recalculate heights when the user resizes the app window."""
        super().resizeEvent(event)
        self.adjust_grid_heights()
    
    # ── Artist click ──────────────────────────────────────────────────────

    def _on_artist_clicked(self, album_data):
        artist_name = album_data.get('albumArtist') or album_data.get('artist', '')
        if artist_name:
            self.artist_clicked.emit(artist_name)

    # ── Worker safety ─────────────────────────────────────────────────────

    def _safe_discard_worker(self, worker):
        if not worker:
            return
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
        self._worker_graveyard.add(worker)
        try:
            worker.finished.connect(
                lambda: self._worker_graveyard.discard(worker)
                if worker in self._worker_graveyard else None)
        except Exception:
            pass

    def _start_cover_worker(self):
        self.cover_worker = GridCoverWorker(self.client)
        self.cover_worker.cover_ready.connect(self.apply_cover)
        self.cover_worker.start()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self.client and (time.time() - self.last_reload_time) > 30:
            self.load_data()

    def initialize(self, client):
        self.client = client
        if not self.cover_worker:
            self._start_cover_worker()
        else:
            self.cover_worker.client = client
        # Push current accent into both bridges immediately
        self.recent_bridge.accentColorChanged.emit(self.current_accent)
        self.random_bridge.accentColorChanged.emit(self.current_accent)
        self.load_data()

    def load_data(self):
        self.last_reload_time = time.time()
        if getattr(self, 'worker', None) and self.worker.isRunning():
            self._safe_discard_worker(self.worker)
        self.worker = HomeLoaderWorker(self.client)
        self.worker.data_ready.connect(self.populate_ui)
        self.worker.start()

    # ── Populate ──────────────────────────────────────────────────────────
    def populate_ui(self, recent, random_mix):
        self._populate_row(self.recent_model,  self.recent_provider,  recent)
        self._populate_row(self.random_model,  self.random_provider,  random_mix)
        self.adjust_grid_heights()
        if self.isVisible():
            QTimer.singleShot(100, self.focus_first_grid)

    def _populate_row(self, model, provider, albums):
        model.clear()
        for album in albums:
            cid = album.get('cover_id') or album.get('coverArt') or album.get('id')
            if cid:
                album['cover_id'] = cid
                if self.cover_worker:
                    self.cover_worker.queue_cover(cid, priority=False)
        model.append_albums(albums)

    def apply_cover(self, cover_id, image_data):
        """Push downloaded bytes into both providers, then notify both models."""
        cid = str(cover_id)
        self.recent_provider.image_cache[cid] = image_data
        self.random_provider.image_cache[cid] = image_data
        self.recent_model.update_cover(cid)
        self.random_model.update_cover(cid)

    # ── Random-mix refresh ────────────────────────────────────────────────

    def refresh_random_mix(self):
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setIcon(self.get_tinted_icon("#555"))
        self.btn_refresh.setStyleSheet(
            "QPushButton { background-color: #222; border: none; border-radius: 4px; }")

        if getattr(self, 'random_reloader', None) and self.random_reloader.isRunning():
            self._safe_discard_worker(self.random_reloader)

        self.random_reloader = RandomMixReloaderWorker(self.client)
        self.random_reloader.data_ready.connect(self.on_random_mix_refreshed)
        self.random_reloader.start()

    def on_random_mix_refreshed(self, new_mix):
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setIcon(self.get_tinted_icon("#aaa"))
        self.btn_refresh.setStyleSheet(
            "QPushButton { background-color: #333; border: none; border-radius: 4px; }")
        if new_mix:
            self._populate_row(self.random_model, self.random_provider, new_mix)
            self.adjust_grid_heights()

    # ── Theming ───────────────────────────────────────────────────────────

    def set_accent_color(self, color, alpha=0.3):
        self.current_accent = color

    
        self.scroll.setStyleSheet(f"""
            QScrollArea#HomeScroll {{
                background-color: rgba(12, 12, 12, {alpha});
                border: none;
                border-bottom-left-radius: 5px;
                border-bottom-right-radius: 5px;
            }}
            QScrollArea#HomeScroll > QWidget {{ background-color: transparent; }}
            QScrollArea#HomeScroll QWidget  {{ background-color: transparent; }}

            QScrollBar:vertical {{
                border: none; background: rgba(0,0,0,0.05); width: 10px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #333; min-height: 30px; border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:vertical:pressed {{ background: {color}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical {{ background: none; }}
        """)

        # 🟢 Broadcast BOTH color and opacity to QML
        if hasattr(self, 'recent_bridge'):
            self.recent_bridge.accentColorChanged.emit(color)
            self.random_bridge.accentColorChanged.emit(color)
            
            # The new connection to push Alpha to the QML rows
            if hasattr(self.recent_bridge, 'bgAlphaChanged'):
                self.recent_bridge.bgAlphaChanged.emit(alpha)
                self.random_bridge.bgAlphaChanged.emit(alpha)

        if hasattr(self, 'btn_refresh') and self.btn_refresh.underMouse():
            self.btn_refresh.setIcon(self.get_tinted_icon(color))

    def get_tinted_icon(self, color_str):
        base = QPixmap(resource_path("img/refresh.png"))
        if base.isNull():
            return QIcon()
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.drawPixmap(0, 0, base)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pix.rect(), QColor(color_str))
        painter.end()
        return QIcon(pix)

    def eventFilter(self, source, event):
        if hasattr(self, 'btn_refresh') and source == self.btn_refresh:
            if event.type() == QEvent.Type.Enter:
                if self.btn_refresh.isEnabled():
                    self.btn_refresh.setIcon(self.get_tinted_icon(self.current_accent))
            elif event.type() == QEvent.Type.Leave:
                if self.btn_refresh.isEnabled():
                    self.btn_refresh.setIcon(self.get_tinted_icon("#aaa"))
        return super().eventFilter(source, event)
