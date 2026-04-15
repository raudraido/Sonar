from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QPushButton,
                             QListWidget, QListWidgetItem, QAbstractItemView,
                             QAbstractButton)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer, QSize, QEvent, QRect
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QPen

from albums_browser import GridCoverWorker, GridItemDelegate, resource_path
from tracks_browser import MiddleClickScroller


# ─────────────────────────────────────────────────────────────────────────────
# Background workers
# ─────────────────────────────────────────────────────────────────────────────

_PAGE = 50   # albums per fetch

class HomeLoaderWorker(QThread):
    data_ready = pyqtSignal(list, list, list)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            recent = random_mix = most_played = []
            if self.client:
                recent      = self.client.get_album_list_sorted(sort_type="newest",   size=_PAGE, offset=0)
                random_mix  = self.client.get_album_list_sorted(sort_type="random",   size=_PAGE, offset=0)
                most_played = self.client.get_album_list_sorted(sort_type="frequent", size=_PAGE, offset=0)
            self.data_ready.emit(recent, random_mix, most_played)
        except Exception as e:
            print(f"[Home Worker] Error: {e}")
            self.data_ready.emit([], [], [])


class HomePageWorker(QThread):
    """Fetches one page of albums for a given sort type."""
    page_ready = pyqtSignal(list)

    def __init__(self, client, sort_type, offset):
        super().__init__()
        self.client = client
        self.sort_type = sort_type
        self.offset = offset

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
                result = self.client.get_album_list_sorted(sort_type="random", size=_PAGE, offset=0)
            self.data_ready.emit(result or [])
        except Exception as e:
            print(f"[RandomMixReloader] Error: {e}")
            self.data_ready.emit([])


# ─────────────────────────────────────────────────────────────────────────────
# Arrow button (same as RelatedArtistRowWidget uses)
# ─────────────────────────────────────────────────────────────────────────────

class _ArrowButton(QAbstractButton):
    def __init__(self, direction, color, parent=None):
        super().__init__(parent)
        self._direction = direction
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
        p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy = self.width() / 2, self.height() / 2
        s, o = 6, 3
        if self._direction == "right":
            p.drawLine(int(cx - o), int(cy - s), int(cx + o), int(cy))
            p.drawLine(int(cx + o), int(cy), int(cx - o), int(cy + s))
        else:
            p.drawLine(int(cx + o), int(cy - s), int(cx - o), int(cy))
            p.drawLine(int(cx - o), int(cy), int(cx + o), int(cy + s))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Horizontal album row
# ─────────────────────────────────────────────────────────────────────────────

class HomeAlbumRowWidget(QWidget):
    album_clicked  = pyqtSignal(dict)
    play_album     = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)
    load_more_requested = pyqtSignal(int)  # emits current offset

    # (min container width, number of columns) — same thresholds as Feishin
    _BREAKPOINTS = [(1440, 8), (1280, 7), (1152, 6), (960, 5),
                    (720, 4), (520, 3), (0, 2)]

    def __init__(self, title, with_refresh=False, refresh_tooltip="Refresh"):
        super().__init__()
        self._accent     = "#888888"
        self._pix_cache  = {}     # cover_id -> QPixmap (full size, square-cropped)
        self._all_albums = []     # every album received so far
        self._page       = 0
        self._n_cols     = 5      # updated on resize
        self._loading_more = False
        self._all_loaded   = False
        self._offset       = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 0, 0, 0)
        layout.setSpacing(6)

        # ── Title row ────────────────────────────────────────────────────
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 10, 0)
        title_layout.setSpacing(8)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(
            "color: #eee; font-size: 15px; font-weight: bold; background: transparent;")
        title_layout.addWidget(self.lbl_title)
        title_layout.addStretch()

        self.btn_refresh = None
        if with_refresh:
            self.btn_refresh = QPushButton()
            self.btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_refresh.setToolTip(refresh_tooltip)
            self.btn_refresh.setFixedSize(28, 22)
            self.btn_refresh.setStyleSheet(
                "QPushButton { background-color: #333; border: none; border-radius: 4px; }")
            title_layout.addWidget(self.btn_refresh)

        self._btn_left  = _ArrowButton("left",  self._accent)
        self._btn_right = _ArrowButton("right", self._accent)
        self._btn_left.clicked.connect(self._page_left)
        self._btn_right.clicked.connect(self._page_right)
        title_layout.addWidget(self._btn_left)
        title_layout.addWidget(self._btn_right)

        layout.addWidget(title_row)

        # ── List widget (no scrollbars — pagination replaces items) ──────
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.list_widget.setWrapping(False)
        self.list_widget.setMouseTracking(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item { background: transparent; }
            QListWidget::item:selected { background: transparent; }
        """)

        self.delegate = GridItemDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self.delegate)

        self.list_widget.itemDoubleClicked.connect(self._on_activated)
        self.list_widget.installEventFilter(self)
        self.list_widget.viewport().installEventFilter(self)

        layout.addWidget(self.list_widget)

    # ── Public API ────────────────────────────────────────────────────────

    def populate(self, albums):
        self._all_albums   = list(albums)
        self._page         = 0
        self._loading_more = False
        self._all_loaded   = False
        self._offset       = len(albums)
        self._render_page()

    def append_albums(self, albums):
        self._loading_more = False
        if not albums:
            self._all_loaded = True
            return
        self._all_albums.extend(albums)
        self._offset += len(albums)
        # If the current page now has more data (was the last partial page), refresh it
        self._render_page()

    def apply_cover(self, cover_id, image_data):
        cid = str(cover_id)
        pix = QPixmap()
        pix.loadFromData(image_data)
        if pix.isNull():
            return
        # Crop to square at a fixed cache resolution
        side = 300
        pix = pix.scaled(side, side,
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
        x = (pix.width()  - side) // 2
        y = (pix.height() - side) // 2
        self._pix_cache[cid] = pix.copy(x, y, side, side)
        # Update any visible item that uses this cover
        cw = self._cell_w()
        for i in range(self.list_widget.count()):
            item  = self.list_widget.item(i)
            album = item.data(Qt.ItemDataRole.UserRole)
            icid  = str(album.get('cover_id') or album.get('coverArt') or album.get('id') or '')
            if icid == cid:
                item.setIcon(self._make_icon(cid, cw))

    def set_accent_color(self, color):
        self._accent = color
        self.delegate.set_master_color(color)
        self._btn_left.set_color(color)
        self._btn_right.set_color(color)
        self.list_widget.viewport().update()

    # ── Resize ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._render_page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Recalculate which page to be on so the same first album stays visible
        first_idx = self._page * self._n_cols
        new_n = self._calc_n_cols()
        if new_n != self._n_cols:
            self._n_cols = new_n
            self._page   = first_idx // new_n
        QTimer.singleShot(0, self._render_page)

    # ── Internal ──────────────────────────────────────────────────────────

    def _calc_n_cols(self):
        w = self.list_widget.viewport().width()
        for threshold, n in self._BREAKPOINTS:
            if w >= threshold:
                return n
        return 2

    def _cell_w(self):
        vp_w = self.list_widget.viewport().width()
        n    = self._n_cols
        return vp_w // n if vp_w > 0 else 200

    def _make_icon(self, cid, cw):
        pix = self._pix_cache.get(cid)
        if pix:
            size = cw - 16
            return QIcon(pix.scaled(size, size,
                                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation))
        ph = QPixmap(max(1, cw - 16), max(1, cw - 16))
        ph.fill(QColor("#1a1a1a"))
        return QIcon(ph)

    def _render_page(self):
        self._n_cols = self._calc_n_cols()
        cw = self._cell_w()
        if cw <= 0:
            return
        ch = cw + 50

        start = self._page * self._n_cols
        end   = start + self._n_cols
        page_albums = self._all_albums[start:end]

        self.list_widget.setGridSize(QSize(cw, ch))
        self.list_widget.setIconSize(QSize(cw - 16, cw - 16))
        self.list_widget.setFixedHeight(ch)

        self.list_widget.clear()
        for album in page_albums:
            cid  = str(album.get('cover_id') or album.get('coverArt') or album.get('id') or '')
            item = QListWidgetItem()
            item.setIcon(self._make_icon(cid, cw))
            item.setData(Qt.ItemDataRole.UserRole, album)
            item.setSizeHint(QSize(cw, ch))
            self.list_widget.addItem(item)

        # Force this widget to hug its content exactly — compute directly
        # so we never depend on the layout's cached sizeHint
        title_h = self.layout().itemAt(0).sizeHint().height()
        self.setFixedHeight(title_h + self.layout().spacing() + ch)

        self._update_arrows()

        # Trigger lazy load when reaching the last page
        last_page = max(0, len(self._all_albums) - 1) // self._n_cols
        if (not self._all_loaded and not self._loading_more
                and self._page >= last_page - 1):
            self._loading_more = True
            self.load_more_requested.emit(self._offset)

    def _page_left(self):
        if self._page > 0:
            self._page -= 1
            self._render_page()

    def _page_right(self):
        last_page = max(0, len(self._all_albums) - 1) // self._n_cols
        if self._page < last_page:
            self._page += 1
            self._render_page()

    def _update_arrows(self):
        last_page = max(0, len(self._all_albums) - 1) // self._n_cols
        self._btn_left.setEnabled(self._page > 0)
        self._btn_right.setEnabled(
            self._page < last_page or (not self._all_loaded))

    def _artist_text_rect(self, item):
        """Returns the artist text QRect in viewport coordinates for a given item."""
        rect = self.list_widget.visualItemRect(item)
        cw = self._cell_w()
        icon_height = cw - 20
        icon_bottom = rect.y() + 10 + icon_height - 1
        current_y = icon_bottom + 10 + 20  # skip icon gap + title row
        return QRect(rect.x() + 10, current_y, cw - 20, 20)

    def _play_button_contains(self, item, pos):
        """Returns True if pos is inside the play button circle drawn by the delegate."""
        rect = self.list_widget.visualItemRect(item)
        cw = self._cell_w()
        icon_width = cw - 20
        icon_rect = QRect(rect.x() + 10, rect.y() + 10, icon_width, icon_width)
        center = icon_rect.center()
        play_size = min(60, icon_width // 2)
        radius = play_size // 2
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        return (dx * dx + dy * dy) <= (radius * radius)

    def _on_activated(self, item):
        if not item:
            return
        album = item.data(Qt.ItemDataRole.UserRole)
        if album:
            self.album_clicked.emit(album)

    def eventFilter(self, source, event):
        if source is self.list_widget.viewport():
            etype = event.type()

            if etype == QEvent.Type.MouseMove:
                pos  = event.position().toPoint()
                item = self.list_widget.itemAt(pos)

                # ── Card hover ───────────────────────────────────────────
                card_row = -1
                if item and self.list_widget.visualItemRect(item).contains(pos):
                    card_row = self.list_widget.row(item)
                if card_row != getattr(self, '_card_hover_row', -1):
                    self._card_hover_row = card_row
                    self.delegate.set_hovered_row(card_row)

                # ── Play-button hover ────────────────────────────────────
                play_hovered = (card_row >= 0 and item is not None
                                and self._play_button_contains(item, pos))
                if play_hovered != getattr(self, '_play_btn_hovered', False):
                    self._play_btn_hovered = play_hovered
                    self.delegate.set_play_hovered(play_hovered)

                # ── Artist text hover (cursor + underline) ───────────────
                artist_row = -1
                if card_row >= 0 and item is not None:
                    if self._artist_text_rect(item).contains(pos):
                        artist_row = card_row
                if artist_row != getattr(self, '_artist_hover_row', -1):
                    self._artist_hover_row = artist_row
                    self.delegate.set_hovered_artist_row(artist_row)
                    self.list_widget.viewport().update()
                    if artist_row >= 0:
                        self.list_widget.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                    else:
                        self.list_widget.viewport().unsetCursor()
                return False

            if etype == QEvent.Type.Leave:
                self._card_hover_row   = -1
                self._play_btn_hovered = False
                self._artist_hover_row = -1
                self.delegate.set_hovered_row(-1)
                self.delegate.set_play_hovered(False)
                self.delegate.set_hovered_artist_row(-1)
                self.list_widget.viewport().unsetCursor()
                return False

            if etype == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos  = event.position().toPoint()
                    item = self.list_widget.itemAt(pos)
                    if item:
                        rect = self.list_widget.visualItemRect(item)
                        if rect.contains(pos):
                            album = item.data(Qt.ItemDataRole.UserRole)
                            if album:
                                if self._artist_text_rect(item).contains(pos):
                                    artist = album.get('artist', '') or album.get('albumArtist', '')
                                    if artist:
                                        self.artist_clicked.emit(artist)
                                        return True
                                elif self._play_button_contains(item, pos):
                                    self.play_album.emit(album)
                                    return True
                                else:
                                    self.album_clicked.emit(album)
                                    return True
                return False

        return super().eventFilter(source, event)


# ─────────────────────────────────────────────────────────────────────────────
# HomeView
# ─────────────────────────────────────────────────────────────────────────────

class HomeView(QWidget):
    album_clicked  = pyqtSignal(dict)
    play_album     = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.current_accent = "#1DB954"

        self.cover_worker = None
        if self.client:
            self._start_cover_worker()

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
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
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.omni_scroller = MiddleClickScroller(self.scroll)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 20, 0, 50)
        self.content_layout.setSpacing(10)
        self.scroll.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll)

        # Recently Added row
        self.recent_row = HomeAlbumRowWidget("Recently Added", with_refresh=True, refresh_tooltip="Refresh Recently Added")
        self.recent_row.album_clicked.connect(self.album_clicked.emit)
        self.recent_row.play_album.connect(self.play_album.emit)
        self.recent_row.artist_clicked.connect(self.artist_clicked.emit)
        self.recent_row.load_more_requested.connect(self._load_more_recent)
        self.recent_row.btn_refresh.clicked.connect(self.refresh_recent)
        self.recent_row.btn_refresh.installEventFilter(self)
        self.content_layout.addWidget(self.recent_row)

        # Random Mix row
        self.random_row = HomeAlbumRowWidget("Random Mix", with_refresh=True, refresh_tooltip="Refresh Mix")
        self.random_row.album_clicked.connect(self.album_clicked.emit)
        self.random_row.play_album.connect(self.play_album.emit)
        self.random_row.artist_clicked.connect(self.artist_clicked.emit)
        self.random_row.load_more_requested.connect(self._load_more_random)
        self.random_row.btn_refresh.clicked.connect(self.refresh_random_mix)
        self.random_row.btn_refresh.installEventFilter(self)
        self.content_layout.addWidget(self.random_row)

        # Most Played row
        self.most_played_row = HomeAlbumRowWidget("Most Played")
        self.most_played_row.album_clicked.connect(self.album_clicked.emit)
        self.most_played_row.play_album.connect(self.play_album.emit)
        self.most_played_row.artist_clicked.connect(self.artist_clicked.emit)
        self.most_played_row.load_more_requested.connect(self._load_more_most_played)
        self.content_layout.addWidget(self.most_played_row)

        self.content_layout.addStretch()

        self.set_accent_color("#888888", 0.3)

        if self.client:
            self.load_data()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def initialize(self, client):
        self.client = client
        if not self.cover_worker:
            self._start_cover_worker()
        else:
            self.cover_worker.client = client
        self.load_data()

    def load_data(self):
        if getattr(self, 'worker', None) and self.worker.isRunning():
            self._safe_discard_worker(self.worker)
        self.worker = HomeLoaderWorker(self.client)
        self.worker.data_ready.connect(self.populate_ui)
        self.worker.start()

    def populate_ui(self, recent, random_mix, most_played):
        self.recent_row.populate(recent)
        self.random_row.populate(random_mix)
        self.most_played_row.populate(most_played)
        self._queue_covers(recent + random_mix + most_played)

    def _queue_covers(self, albums):
        for album in albums:
            cid = album.get('cover_id') or album.get('coverArt') or album.get('id')
            if cid and self.cover_worker:
                self.cover_worker.queue_cover(cid)

    def _load_more_recent(self, offset):
        w = HomePageWorker(self.client, "newest", offset)
        w.page_ready.connect(lambda albums: (
            self.recent_row.append_albums(albums),
            self._queue_covers(albums)
        ))
        w.page_ready.connect(lambda _: self._discard_page_worker('_recent_page_worker'))
        self._recent_page_worker = w
        w.start()

    def _load_more_most_played(self, offset):
        w = HomePageWorker(self.client, "frequent", offset)
        w.page_ready.connect(lambda albums: (
            self.most_played_row.append_albums(albums),
            self._queue_covers(albums)
        ))
        w.page_ready.connect(lambda _: self._discard_page_worker('_most_played_page_worker'))
        self._most_played_page_worker = w
        w.start()

    def _load_more_random(self, offset):
        w = HomePageWorker(self.client, "random", offset)
        w.page_ready.connect(lambda albums: (
            self.random_row.append_albums(albums),
            self._queue_covers(albums)
        ))
        w.page_ready.connect(lambda _: self._discard_page_worker('_random_page_worker'))
        self._random_page_worker = w
        w.start()

    def _discard_page_worker(self, attr):
        w = getattr(self, attr, None)
        if w:
            self._safe_discard_worker(w)
            setattr(self, attr, None)

    def apply_cover(self, cover_id, image_data):
        self.recent_row.apply_cover(cover_id, image_data)
        self.random_row.apply_cover(cover_id, image_data)
        self.most_played_row.apply_cover(cover_id, image_data)

    def focus_first_grid(self):
        self.recent_row.list_widget.setFocus(Qt.FocusReason.OtherFocusReason)

    # ── Recently Added refresh ────────────────────────────────────────────

    def refresh_recent(self):
        btn = self.recent_row.btn_refresh
        btn.setEnabled(False)
        btn.setIcon(self._tinted_icon("#555"))
        if getattr(self, 'recent_reloader', None) and self.recent_reloader.isRunning():
            self._safe_discard_worker(self.recent_reloader)
        self.recent_reloader = HomePageWorker(self.client, "newest", 0)
        self.recent_reloader.page_ready.connect(self._on_recent_refreshed)
        self.recent_reloader.start()

    def _on_recent_refreshed(self, new_albums):
        btn = self.recent_row.btn_refresh
        btn.setEnabled(True)
        btn.setIcon(self._tinted_icon("#aaa"))
        if new_albums:
            self.recent_row.populate(new_albums)
            self._queue_covers(new_albums)

    # ── Random mix refresh ────────────────────────────────────────────────

    def refresh_random_mix(self):
        btn = self.random_row.btn_refresh
        btn.setEnabled(False)
        btn.setIcon(self._tinted_icon("#555"))
        if getattr(self, 'random_reloader', None) and self.random_reloader.isRunning():
            self._safe_discard_worker(self.random_reloader)
        self.random_reloader = RandomMixReloaderWorker(self.client)
        self.random_reloader.data_ready.connect(self._on_random_refreshed)
        self.random_reloader.start()

    def _on_random_refreshed(self, new_mix):
        btn = self.random_row.btn_refresh
        btn.setEnabled(True)
        btn.setIcon(self._tinted_icon("#aaa"))
        if new_mix:
            self.random_row.populate(new_mix)
            self._queue_covers(new_mix)

    # ── Workers ───────────────────────────────────────────────────────────

    def _start_cover_worker(self):
        self.cover_worker = GridCoverWorker(self.client)
        self.cover_worker.cover_ready.connect(self.apply_cover)
        self.cover_worker.start()

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

        if hasattr(self, 'recent_row'):
            self.recent_row.set_accent_color(color)
            self.random_row.set_accent_color(color)
            self.most_played_row.set_accent_color(color)
            for row in (self.recent_row, self.random_row):
                btn = row.btn_refresh
                if btn and not btn.underMouse():
                    btn.setIcon(self._tinted_icon("#aaa"))

    def _tinted_icon(self, color_str):
        base = QPixmap(resource_path("img/refresh.png"))
        if base.isNull():
            return QIcon()
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.drawPixmap(0, 0, base)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pix.rect(), QColor(color_str))
        p.end()
        return QIcon(pix)

    def eventFilter(self, source, event):
        if hasattr(self, 'recent_row'):
            for row in (self.recent_row, self.random_row):
                btn = row.btn_refresh
                if btn and source is btn:
                    if event.type() == QEvent.Type.Enter and btn.isEnabled():
                        btn.setIcon(self._tinted_icon(self.current_accent))
                    elif event.type() == QEvent.Type.Leave and btn.isEnabled():
                        btn.setIcon(self._tinted_icon("#aaa"))
        return super().eventFilter(source, event)
