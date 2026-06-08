from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QPushButton,
                             QListWidget, QListWidgetItem, QAbstractItemView,
                             QAbstractButton, QFrame, QGraphicsOpacityEffect,
                             QApplication, QStyledItemDelegate, QStyleOptionViewItem)
from PyQt6.QtCore import (Qt, pyqtSignal, QThread, QTimer, QSize, QEvent, QRect,
                          QRectF, QSettings, QPropertyAnimation, QParallelAnimationGroup,
                          QEasingCurve, QPoint)
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QPen, QCursor, QBrush, QImage
from albums_browser import GridCoverWorker, GridItemDelegate, resource_path
from player.mixins.visuals import scrollbar_css, install_scroll_reveal, resolve_menu_hover, SmoothScroller, CoverDecodeWorker, SpinRefreshButton
from tracks_browser import MiddleClickScroller

class _ShimmerDelegate(QStyledItemDelegate):
    """Paints animated shimmer cards for skeleton placeholder items."""

    def __init__(self, viewport, base_color="#282828"):
        super().__init__(viewport)
        self._phase    = 0.0
        self._viewport = viewport
        c = QColor(base_color)
        self._base_r = c.red()
        self._base_g = c.green()
        self._base_b = c.blue()
        self._timer    = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)   # ~25 fps

    def _tick(self):
        import math
        self._phase = (self._phase + 0.04) % 1.0
        self._viewport.update()

    def paint(self, painter, option, index):
        album = index.data(Qt.ItemDataRole.UserRole)
        if not (isinstance(album, dict) and album.get('_skeleton')):
            super().paint(painter, option, index)
            return

        import math
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        rect     = option.rect
        padding  = 8
        w        = rect.width()  - padding * 2
        card_h   = w             # square card
        cx, cy   = rect.x() + padding, rect.y() + padding

        phase  = (self._phase + index.row() * 0.18) % 1.0
        factor = 1.0 + 0.12 * math.sin(phase * 2 * math.pi)
        r = min(255, int(self._base_r * factor))
        g = min(255, int(self._base_g * factor))
        b = min(255, int(self._base_b * factor))
        dr = max(0, int(r * 0.85))
        dg = max(0, int(g * 0.85))
        db = max(0, int(b * 0.85))

        # Album art placeholder
        painter.setBrush(QBrush(QColor(r, g, b)))
        painter.drawRoundedRect(QRectF(cx, cy, w, card_h), 6, 6)

        # Title pill
        pill_y = cy + card_h + 7
        painter.setBrush(QBrush(QColor(dr, dg, db)))
        painter.drawRoundedRect(QRectF(cx, pill_y,      w * 0.75, 9),  5, 5)
        painter.drawRoundedRect(QRectF(cx, pill_y + 15, w * 0.50, 7),  4, 4)

        painter.restore()

    def stop(self):
        self._timer.stop()


class _HomeGridDelegate(GridItemDelegate):
    """
    GridItemDelegate for the Home panel's always-on 6-px QScrollArea scrollbar.

    The scrollbar consumes 6 px on the right of the viewport. Without adjustment,
    the right visual gap = 6 (icon pad) + 6 (scrollbar) = 12 px vs 10 px on the left.

    Fix: expand the painting rect 2 px to the right so the effective right icon-pad
    is 4 px → right gap = 4 + 6 (scrollbar) = 10 px = left gap.
    """
    _SB_W = 6   # must match scrollbar_css() width

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        opt.rect = option.rect.adjusted(0, 0, self._SB_W - 4, 0)
        super().paint(painter, opt, index)

# ─────────────────────────────────────────────────────────────────────────────
# Arrow button (same as RelatedArtistRowWidget uses)
# ─────────────────────────────────────────────────────────────────────────────

class _ArrowButton(QAbstractButton):
    def __init__(self, direction, color, parent=None):
        super().__init__(parent)
        self._direction = direction
        self._color = QColor(color)
        self._active = True
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            _theme = getattr(self.window(), 'theme', None)
            p.setBrush(QColor(resolve_menu_hover(_theme)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 12, 12)
        color = self._color if self._active else QColor("#333")
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


def _RefreshButton(color, parent=None):
    """Factory — returns a SpinRefreshButton configured for the home tab."""
    return SpinRefreshButton(
        icon_path=resource_path("img/refresh.png"),
        icon_size=14, btn_size=30, color=color, parent=parent
    )


# ─────────────────────────────────────────────────────────────────────────────
# Drag-grip handle (appears on title row hover)
# ─────────────────────────────────────────────────────────────────────────────

class _GripHandle(QWidget):
    drag_initiated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 20)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._color = QColor("#444")
        self._press_pos = None

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        for row in range(3):
            for col in range(2):
                p.drawEllipse(3 + col * 7, 4 + row * 6, 4, 4)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > 6:
                self._press_pos = None
                self.drag_initiated.emit()

    def mouseReleaseEvent(self, event):
        self._press_pos = None


# ─────────────────────────────────────────────────────────────────────────────
# Horizontal album row
# ─────────────────────────────────────────────────────────────────────────────

class HomeAlbumRowWidget(QWidget):
    album_clicked  = pyqtSignal(dict)
    play_album     = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)
    load_more_requested = pyqtSignal(int)  # emits current offset
    focus_next     = pyqtSignal(int)  # carries current column index
    focus_prev     = pyqtSignal(int)
    drag_requested = pyqtSignal(object)  # emits self

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6) # space for title row shadow and gap to carousel below

        # ── Title row ────────────────────────────────────────────────────
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(6, 0, 4, 0)
        title_layout.setSpacing(8)

        self._grip = _GripHandle()
        self._grip.setVisible(False)
        self._grip.drag_initiated.connect(lambda: self.drag_requested.emit(self))
        title_layout.addWidget(self._grip)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(
            "color: #eee; font-size: 15px; font-weight: bold; background: transparent;")
        title_layout.addWidget(self.lbl_title)
        title_layout.addStretch()

        # Timer to guard against false Leave events when entering child widgets
        self._grip_timer = QTimer(self)
        self._grip_timer.setSingleShot(True)
        self._grip_timer.setInterval(80)
        self._grip_timer.timeout.connect(self._hide_grip_if_outside)

        self.btn_refresh = None
        if with_refresh:
            self.btn_refresh = _RefreshButton(self._accent)
            self.btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.btn_refresh.setToolTip(refresh_tooltip)
            title_layout.addWidget(self.btn_refresh)

        self._btn_left  = _ArrowButton("left",  self._accent)
        self._btn_right = _ArrowButton("right", self._accent)
        self._btn_left.clicked.connect(self._page_left)
        self._btn_right.clicked.connect(self._page_right)
        title_layout.addWidget(self._btn_left)
        title_layout.addWidget(self._btn_right)

        layout.addWidget(title_row)

        # ── Clip container — list widget lives here as a manual child ───────
        # Qt clips child widget painting to parent bounds, so list_widget
        # is invisible when slid outside the carousel during transitions.
        self._carousel = QWidget()
        self._carousel.setStyleSheet("background: transparent;")
        layout.addWidget(self._carousel)

        self.list_widget = QListWidget(self._carousel)
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
        self.delegate = _HomeGridDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self.delegate)
        self.list_widget.itemDoubleClicked.connect(self._on_activated)
        self.list_widget.installEventFilter(self)
        self.list_widget.viewport().installEventFilter(self)

        self._animating  = False
        self._anim_group = None

    # ── Internal helpers ──────────────────────────────────────────────────

    def _animate_page(self, direction):
        """Carousel slide: snapshot old page → move list_widget in from one side → animate."""
        if self._animating:
            return
        self._animating = True

        w    = self._carousel.width()
        ch   = self.list_widget.height()
        in_x =  w if direction == 'right' else -w
        out_x = -w if direction == 'right' else  w

        # 1. Freeze a screenshot of the current page as an overlay label
        snapshot = self.list_widget.grab()
        overlay  = QLabel(self._carousel)
        overlay.setPixmap(snapshot)
        overlay.setFixedSize(w, ch)
        overlay.move(0, 0)
        overlay.show()
        overlay.raise_()

        # 2. Move list_widget to the incoming side and render new content into it
        self.list_widget.move(in_x, 0)
        self._render_page()      # renders self._page into list_widget at (in_x, 0)

        # 3. Animate overlay sliding out, list_widget sliding in — simultaneously
        anim_out = QPropertyAnimation(overlay, b"pos")
        anim_out.setDuration(220)
        anim_out.setStartValue(QPoint(0, 0))
        anim_out.setEndValue(QPoint(out_x, 0))
        anim_out.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_in = QPropertyAnimation(self.list_widget, b"pos")
        anim_in.setDuration(220)
        anim_in.setStartValue(QPoint(in_x, 0))
        anim_in.setEndValue(QPoint(0, 0))
        anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(anim_out)
        self._anim_group.addAnimation(anim_in)

        def _on_done():
            overlay.deleteLater()
            # Ensure list_widget is exactly at (0, 0) after animation
            self.list_widget.move(0, 0)
            self._animating  = False
            self._anim_group = None
            self._render_page()   # re-check load-more trigger now that _animating is False

        self._anim_group.finished.connect(_on_done)
        self._anim_group.start()

    # ── Public API ────────────────────────────────────────────────────────

    def show_skeleton(self):
        """Fill the row with animated shimmer placeholder cards while real data loads."""
        n = max(self._calc_n_cols(), 5)
        self._all_albums   = [{'_skeleton': True}] * n
        self._page         = 0
        self._loading_more = False
        self._all_loaded   = True   # prevent load_more from firing on skeleton
        self._offset       = 0

        # Install shimmer delegate (stops and replaces on populate)
        if not isinstance(self.list_widget.itemDelegate(), _ShimmerDelegate):
            self._real_delegate    = self.list_widget.itemDelegate()
            sk = getattr(getattr(self.window(), 'theme', None), 'skeleton_base', '#282828')
            self._shimmer_delegate = _ShimmerDelegate(self.list_widget.viewport(), base_color=sk)
            self.list_widget.setItemDelegate(self._shimmer_delegate)

        self._render_page()

    def populate(self, albums):
        # Stop shimmer and restore the real delegate before filling with real items
        if isinstance(self.list_widget.itemDelegate(), _ShimmerDelegate):
            self._shimmer_delegate.stop()
            self.list_widget.setItemDelegate(
                getattr(self, '_real_delegate', None) or self.delegate)
            self._shimmer_delegate = None
            self._real_delegate    = None

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
        # Legacy path — kept for callers that still pass raw bytes.
        # Decoding happens here synchronously; prefer apply_cover_pixmap.
        cid = str(cover_id)
        img = QImage()
        img.loadFromData(image_data)
        if img.isNull():
            return
        side = 300
        img = img.scaled(side, side,
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
        x = (img.width()  - side) // 2
        y = (img.height() - side) // 2
        self.apply_cover_pixmap(cid, QPixmap.fromImage(img.copy(x, y, side, side)))

    def apply_cover_pixmap(self, cover_id, pix: QPixmap):
        cid = str(cover_id)
        self._pix_cache[cid] = pix
        cw = self._cell_w()
        for i in range(self.list_widget.count()):
            item  = self.list_widget.item(i)
            album = item.data(Qt.ItemDataRole.UserRole)
            if not album:
                continue
            icid = str(album.get('cover_id') or album.get('coverArt') or album.get('id') or '')
            if icid == cid:
                item.setIcon(self._make_icon(cid, cw))

    def set_draggable(self, enabled: bool):
        self._draggable = enabled
        if not enabled:
            self._grip.setVisible(False)

    def enterEvent(self, event):
        if getattr(self, '_draggable', True):
            self._grip_timer.stop()
            self._grip.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if getattr(self, '_draggable', True):
            self._grip_timer.start()
        super().leaveEvent(event)

    def _hide_grip_if_outside(self):
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            self._grip.setVisible(False)

    def set_accent_color(self, color):
        self._accent = color
        self.delegate.set_master_color(color)
        self._btn_left.set_color(color)
        self._btn_right.set_color(color)
        self._grip.set_color(color)
        theme = getattr(self.window(), 'theme', None)
        if theme:
            self.lbl_title.setStyleSheet(f"color: {theme.font_color_primary}; font-size: 15px; font-weight: bold; background: transparent;")
            if isinstance(self.list_widget.itemDelegate(), _ShimmerDelegate):
                sk = getattr(theme, 'skeleton_base', '#282828')
                c = QColor(sk)
                d = self.list_widget.itemDelegate()
                d._base_r, d._base_g, d._base_b = c.red(), c.green(), c.blue()
        self.list_widget.viewport().update()

    # ── Resize ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._render_page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._animating:
            return
        first_idx = self._page * self._n_cols
        new_n = self._calc_n_cols()
        if new_n != self._n_cols:
            self._n_cols = new_n
            self._page   = first_idx // new_n
        QTimer.singleShot(0, self._render_page)

    # ── Internal ──────────────────────────────────────────────────────────

    def _calc_n_cols(self):
        w = self._carousel.width() or self.list_widget.viewport().width()
        for threshold, n in self._BREAKPOINTS:
            if w >= threshold:
                return n
        return 2

    def _cell_w(self):
        vp_w = self._carousel.width() or self.list_widget.viewport().width()
        n    = self._n_cols
        return vp_w // n if vp_w > 0 else 200

    def _make_icon(self, cid, cw):
        pix = self._pix_cache.get(cid)
        if pix:
            size = cw - 12
            return QIcon(pix.scaled(size, size,
                                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation))
        ph = QPixmap(max(1, cw - 12), max(1, cw - 12))
        sk = getattr(getattr(self.window(), 'theme', None), 'skeleton_base', '#1a1a1a')
        ph.fill(QColor(sk))
        return QIcon(ph)

    def _render_page(self):
        self._n_cols = self._calc_n_cols()
        cw = self._cell_w()
        if cw <= 0:
            return
        ch  = cw + 70
        lw  = self.list_widget
        w   = self._carousel.width() or cw * self._n_cols

        lw.setGridSize(QSize(cw, ch))
        lw.setIconSize(QSize(cw - 12, cw - 12))
        # Preserve x position (may be off-screen during carousel animation)
        lw.setGeometry(lw.x(), 0, w, ch)

        start       = self._page * self._n_cols
        page_albums = self._all_albums[start : start + self._n_cols]

        lw.clear()
        for album in page_albums:
            item = QListWidgetItem()
            if album.get('_skeleton'):
                item.setData(Qt.ItemDataRole.UserRole, album)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            else:
                cid = str(album.get('cover_id') or album.get('coverArt') or album.get('id') or '')
                item.setIcon(self._make_icon(cid, cw))
                item.setData(Qt.ItemDataRole.UserRole, album)
            item.setSizeHint(QSize(cw, ch))
            lw.addItem(item)

        # Size the carousel container and row — always safe since we only
        # change height, not x position of list_widget
        self._carousel.setFixedHeight(ch)
        title_h = self.layout().itemAt(0).sizeHint().height()
        self.setFixedHeight(title_h + self.layout().spacing() + ch)

        if not self._animating:
            self._update_arrows()
            last_page = max(0, len(self._all_albums) - 1) // self._n_cols
            if (not self._all_loaded and not self._loading_more
                    and self._page >= last_page - 1):
                self._loading_more = True
                self.load_more_requested.emit(self._offset)

    def _page_left(self):
        if self._page > 0:
            self._page -= 1
            self._animate_page('left')

    def _page_right(self):
        last_page = max(0, len(self._all_albums) - 1) // self._n_cols
        if self._page < last_page:
            self._page += 1
            self._animate_page('right')

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
        if source is self.list_widget:
            if event.type() == QEvent.Type.FocusOut:
                self.list_widget.clearSelection()
                self.list_widget.setCurrentRow(-1)
                return False
            if event.type() == QEvent.Type.FocusIn:
                if self.list_widget.count() > 0 and self.list_widget.currentRow() < 0:
                    self.list_widget.setCurrentRow(0)  # fallback when no index carried over
                return False

        if source is self.list_widget and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down or (key == Qt.Key.Key_Tab and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                self.focus_next.emit(self.list_widget.currentRow())
                return True
            if key == Qt.Key.Key_Up or (key == Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.focus_prev.emit(self.list_widget.currentRow())
                return True
            if key == Qt.Key.Key_Left:
                if self.list_widget.currentRow() <= 0:
                    self._page_left()
                    if self.list_widget.count() > 0:
                        self.list_widget.setCurrentRow(self.list_widget.count() - 1)
                    return True
                return False  # let QListWidget handle movement within page
            if key == Qt.Key.Key_Right:
                if self.list_widget.currentRow() >= self.list_widget.count() - 1:
                    self._page_right()
                    if self.list_widget.count() > 0:
                        self.list_widget.setCurrentRow(0)
                    return True
                return False  # let QListWidget handle movement within page
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list_widget.currentItem()
                if item:
                    album = item.data(Qt.ItemDataRole.UserRole)
                    if album:
                        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            self.play_album.emit(album)
                        else:
                            self._on_activated(item)
                return True

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

