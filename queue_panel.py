"""
queue_panel.py — Floating queue overlay panel, anchored above the footer.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QAbstractItemView, QStyledItemDelegate, QMenu,
    QPushButton, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect, QRectF, QPoint, QSettings, QEvent, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont, QFontMetrics, QAction, QPen, QMovie, QPixmap
import re
from player.mixins.visuals import scrollbar_css, install_scroll_reveal, menu_hover

_ARTIST_SEP_RE = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. )')

def _split_artist(artist: str):
    """Returns list of (text, is_separator) tuples."""
    return [(p, bool(_ARTIST_SEP_RE.match(p)))
            for p in _ARTIST_SEP_RE.split(artist) if p]

ROW_H = 53
NUM_W = 32
FAV_W = 28
DUR_W = 50


# ── Resize handle: top edge (height) ─────────────────────────────────────────

class _ResizeHandle(QWidget):
    resize_delta = pyqtSignal(int)   # positive = grow taller

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setMouseTracking(True)
        self._press_y: int | None = None
        self._hovered = False

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_y = event.globalPosition().toPoint().y()

    def mouseMoveEvent(self, event):
        if self._press_y is not None:
            cur_y = event.globalPosition().toPoint().y()
            delta = self._press_y - cur_y   # drag up → positive → grow
            self._press_y = cur_y
            self.resize_delta.emit(delta)

    def mouseReleaseEvent(self, event):
        self._press_y = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = 100 if self._hovered else 45
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(180, 180, 180, alpha))
        cx, cy = self.width() // 2, self.height() // 2
        for dx in (-12, -6, 0, 6, 12):
            p.drawEllipse(QPoint(cx + dx, cy), 2, 2)
        p.end()


# ── Resize handle: right edge (width) ────────────────────────────────────────

class _ResizeHandleRight(QWidget):
    resize_delta = pyqtSignal(int)   # positive = grow wider

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(8)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self._press_x: int | None = None
        self._hovered = False

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = event.globalPosition().toPoint().x()

    def mouseMoveEvent(self, event):
        if self._press_x is not None:
            cur_x = event.globalPosition().toPoint().x()
            delta = cur_x - self._press_x   # drag right → positive → wider
            self._press_x = cur_x
            self.resize_delta.emit(delta)

    def mouseReleaseEvent(self, event):
        self._press_x = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = 80 if self._hovered else 0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(180, 180, 180, alpha))
        p.drawRoundedRect(2, 0, 4, self.height(), 2, 2)
        p.end()


# ── Bottom tab buttons ────────────────────────────────────────────────────────

class _TabButton(QPushButton):
    def __init__(self, icon_path, label, parent=None):
        super().__init__(parent)
        self._icon_path = icon_path
        self._accent    = '#ffffff'
        self._hovered   = False

        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('QPushButton { background: transparent; border: none; }')

        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(0, 6, 0, 4)
        vbox.setSpacing(2)
        vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_lbl = QLabel()
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        vbox.addWidget(self._icon_lbl)

        self._txt_lbl = QLabel(label)
        self._txt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._txt_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._txt_lbl.setStyleSheet('background: transparent; font-size: 10px; font-weight: bold;')
        vbox.addWidget(self._txt_lbl)

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(inner)

        self.toggled.connect(lambda _: self._refresh())
        self._refresh()

    def set_accent(self, color):
        self._accent = color
        self._refresh()

    def enterEvent(self, event):
        self._hovered = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh()
        super().leaveEvent(event)

    def _refresh(self):
        if self.isChecked():
            color = self._accent
        elif self._hovered:
            color = '#aaaaaa'
        else:
            color = '#555555'

        self._txt_lbl.setStyleSheet(
            f'color: {color}; background: transparent; font-size: 10px; font-weight: bold;'
        )
        pix = QPixmap(self._icon_path)
        if not pix.isNull():
            pix = pix.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            out = QPixmap(pix.size())
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.drawPixmap(0, 0, pix)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(color))
            p.end()
            self._icon_lbl.setPixmap(out)


# ── Row delegate ─────────────────────────────────────────────────────────────

class _QueueDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_H)

    def paint(self, painter, option, index):
        panel = self._panel
        data  = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        is_current = data.get('_is_current', False)
        is_past    = data.get('_is_past', False)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = option.rect

        from PyQt6.QtWidgets import QStyle
        if option.state & QStyle.StateFlag.State_Selected:
            halo_color = QColor(255, 255, 255, 18)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            halo_color = QColor(255, 255, 255, 8)
        else:
            halo_color = None
        if halo_color:
            view = option.widget
            vp_w = view.viewport().width() if view else r.width()
            sb = view.verticalScrollBar() if view else None
            sb_w = sb.width() if (sb and sb.isVisible()) else 0
            # right inset shrinks by scrollbar width so visual gap = 8px to widget edge
            right_inset = max(1, 8 - sb_w)
            halo = QRectF(8, r.top(), vp_w - 8 - right_inset, r.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo_color)
            painter.drawRoundedRect(halo, 6, 6)

        # Track number / playing GIF
        num_rect = QRect(r.left() + 6, r.top(), NUM_W, r.height())
        if is_current and panel._is_playing:
            pix = panel._playing_movie.currentPixmap()
            if not pix.isNull():
                px = num_rect.left() + (num_rect.width() - pix.width()) // 2
                py = num_rect.top() + (num_rect.height() - pix.height()) // 2
                painter.drawPixmap(px, py, pix)
        else:
            f_num = QFont()
            f_num.setPixelSize(11)
            f_num.setBold(is_current)
            painter.setFont(f_num)
            if is_current:
                painter.setPen(QColor(panel._accent_color))
            else:
                painter.setPen(QColor(100, 100, 100, 90 if is_past else 170))
            painter.drawText(num_rect, Qt.AlignmentFlag.AlignCenter,
                             str(data.get('_num', '')))

        # Duration — normalise seconds int to m:ss if needed
        raw_dur = data.get('duration', '')
        try:
            secs = int(float(raw_dur))
            dur  = f"{secs // 60}:{secs % 60:02d}"
        except (ValueError, TypeError):
            dur = str(raw_dur)
        f_dur = QFont()
        f_dur.setPixelSize(12)
        painter.setFont(f_dur)
        if is_current:
            painter.setPen(QColor(panel._accent_color))
        else:
            painter.setPen(QColor(160, 160, 160, 70 if is_past else 120))
        dur_rect = QRect(r.right() - DUR_W - 8, r.top(), DUR_W, r.height())
        painter.drawText(dur_rect,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, dur)

        # Favorite heart
        raw_starred = data.get('starred', False)
        is_fav = raw_starred.lower() in ('true', '1') if isinstance(raw_starred, str) else bool(raw_starred)
        heart_pix = panel._heart_filled_pix if is_fav else panel._heart_empty_pix
        if not heart_pix.isNull():
            fav_cx = r.right() - DUR_W - FAV_W - 8 + FAV_W // 2
            fav_cy = r.top() + r.height() // 2
            pw, ph = heart_pix.width(), heart_pix.height()
            painter.drawPixmap(fav_cx - pw // 2, fav_cy - ph // 2, heart_pix)

        # Text column
        text_x = r.left() + NUM_W + 10
        text_w = r.width() - NUM_W - FAV_W - DUR_W - 28

        # Title
        f_title = QFont()
        f_title.setPixelSize(panel._primary_px)
        f_title.setBold(is_current)
        painter.setFont(f_title)
        if is_current:
            painter.setPen(QColor(panel._accent_color))
        else:
            c = QColor(panel._primary_color)
            c.setAlpha(90 if is_past else 210)
            painter.setPen(c)
        title = data.get('title', data.get('name', 'Unknown'))
        title_e = QFontMetrics(f_title).elidedText(title, Qt.TextElideMode.ElideRight, text_w)
        painter.drawText(QRect(text_x, r.top() + 8, text_w, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_e)

        # Artist — split on separators, draw each part
        f_art = QFont()
        f_art.setPixelSize(panel._secondary_px)
        painter.setFont(f_art)
        fm_art = QFontMetrics(f_art)
        artist = data.get('artist', '')
        ax   = text_x
        ay   = r.top() + 30
        row  = data.get('_num', -1) - 1  # 0-based row index
        for part, is_sep in _split_artist(artist):
            pw = fm_art.horizontalAdvance(part)
            if ax + pw > r.right() - DUR_W - 8:
                break
            hovered = (not is_sep and
                       panel._hover_artist == (row, part.strip()))
            if is_sep:
                painter.setPen(QColor(120, 120, 120, 160))
            elif is_current:
                painter.setPen(QColor(panel._accent_color))
            elif is_past:
                c = QColor(panel._secondary_color)
                c.setAlpha(60)
                painter.setPen(c)
            else:
                c = QColor(panel._secondary_color)
                c.setAlpha(210)
                painter.setPen(c)
            painter.drawText(QRect(ax, ay, pw, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, part)
            if hovered:
                ul_y = ay + fm_art.ascent() + 2
                painter.drawLine(ax, ul_y, ax + pw, ul_y)
            ax += pw

        painter.restore()


# ── Main panel widget ─────────────────────────────────────────────────────────

class QueuePanel(QWidget):
    play_index       = pyqtSignal(int)
    play_next_index  = pyqtSignal(int)
    remove_index     = pyqtSignal(int)
    close_requested  = pyqtSignal()
    artist_clicked   = pyqtSignal(str)
    favorite_toggled = pyqtSignal(int)   # playlist index
    reordered        = pyqtSignal(list, int)  # new track list, new current index

    _MIN_H = 180
    _MAX_H = 900

    def __init__(self, parent=None, embedded=False):
        super().__init__(parent)
        self._embedded         = embedded
        self._accent_color     = '#cccccc'
        self._primary_px       = 14
        self._primary_color    = '#dddddd'
        self._secondary_px     = 12
        self._secondary_color  = '#777777'
        self._settings         = QSettings()
        self._hover_artist     = None  # (row, part_text) currently hovered
        self._pending_refresh  = None  # (playlist_data, current_index, is_playing) deferred while hidden
        self._pending_load     = None  # (artist_id, artist_name) deferred while hidden or info tab inactive
        self._info_load_timer  = QTimer(self)
        self._info_load_timer.setSingleShot(True)
        self._info_load_timer.setInterval(350)
        self._info_load_timer.timeout.connect(self._do_load_artist_info)
        self._heart_filled_pix = self._make_heart_pix("img/heart_filled.png", "#E91E63")
        self._heart_empty_pix  = self._make_heart_pix("img/heart.png", "#555555")

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('QueuePanel')
        self.setStyleSheet(
            '#QueuePanel {'
            '  background: rgba(14,14,14,0.96);'
            '  border: none;'
            '  border-radius: 0px;'
            '  outline: none;'
            '}'
        )

        # Root: content column + right-edge resize strip
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Content column ────────────────────────────────────────────────────
        content = QWidget()
        content.setObjectName('QueueContent')
        content.setStyleSheet('QWidget#QueueContent { background: transparent; border: none; }')
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # Top resize handle (height) — not used in embedded mode
        if not embedded:
            self._handle = _ResizeHandle(self)
            self._handle.resize_delta.connect(self._on_resize_delta)
            col.addWidget(self._handle)
        else:
            self._handle = None

        # Header bar
        self._panel_header = QWidget()
        header = self._panel_header
        header.setFixedHeight(62)
        header.setStyleSheet(
            'QWidget { background: transparent; border-bottom: 1px solid rgba(255,255,255,0.07); }'
        )
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(14, 0, 8, 0)
        hbox.setSpacing(0)

        self._queue_lbl = QLabel('Queue')
        self._queue_lbl.setStyleSheet(
            'color: #ddd; font-weight: bold; font-size: 16px; background: transparent; border: none;'
        )
        hbox.addWidget(self._queue_lbl)
        hbox.addSpacing(8)

        self._position_lbl = QLabel('') # e.g. "3/12"
        self._position_lbl.setStyleSheet(
            'color: #555; font-size: 13px; background: transparent; border: none;'
        )
        hbox.addWidget(self._position_lbl)
        hbox.addStretch()

        self._duration_lbl = QLabel('')
        self._duration_lbl.setStyleSheet(
            'color: #555; font-size: 13px; background: transparent; border: none;'
        )
        hbox.addWidget(self._duration_lbl)
        hbox.addSpacing(8)

        if not embedded:
            close_btn = QPushButton('×')
            close_btn.setFixedSize(22, 22)
            close_btn.setFlat(True)
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            close_btn.setStyleSheet(
                'QPushButton { color: #555; font-size: 17px; line-height: 1;'
                '              background: transparent; border: none; }'
                'QPushButton:hover { color: #bbb; }'
            )
            close_btn.clicked.connect(self.close_requested)
            hbox.addWidget(close_btn)

        col.addWidget(header)

        # ── Content stack (list / artist info) ───────────────────────────────
        from artist_info_panel import ArtistInfoPanel
        self._artist_info_panel = ArtistInfoPanel()
        self._artist_info_panel.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._artist_info_panel.hide()
        self._navidrome_client = None   # set from outside via set_client()

        # Track list
        self._list = QListWidget()
        self._list.setFrameShape(QListWidget.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setMouseTracking(True)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setStyleSheet('''
            QListWidget {
                background: transparent;
                outline: 0;
                border: none;
            }
            QListWidget::item {
                border-bottom: 1px solid rgba(255,255,255,0.04);
            }
            QListWidget::item:selected {
                background: rgba(255,255,255,0.06);
            }
        ''')

        self._is_playing = False
        self._playing_movie = QMovie("img/playing.gif")
        self._playing_movie.setScaledSize(QSize(30, 30))
        self._playing_movie.frameChanged.connect(lambda: self._list.viewport().update())

        self._delegate = _QueueDelegate(self)
        self._list.setItemDelegate(self._delegate)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.viewport().setMouseTracking(True)
        self._list.viewport().installEventFilter(self)

        self._list.setDragEnabled(True)
        self._list.setAcceptDrops(True)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setDropIndicatorShown(True)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        self._update_list_style()

        col.addWidget(self._list)
        col.addWidget(self._artist_info_panel)

        # ── Bottom tab bar ────────────────────────────────────────────────────
        bottom_bar = QWidget()
        bottom_bar.setObjectName('QueueBottomBar')
        bottom_bar.setFixedHeight(52)
        bottom_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom_bar.setStyleSheet(
            '#QueueBottomBar { background: transparent; border-top: 1px solid rgba(255,255,255,0.07); }'
        )
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(0, 0, 0, 0)
        bb_layout.setSpacing(0)

        from player import resource_path as _rp
        self.btn_queue  = _TabButton(_rp('img/queue.png'),  'Queue')
        self.btn_lyrics = _TabButton(_rp('img/lyrics.png'), 'Lyrics')
        self.btn_info   = _TabButton(_rp('img/info.png'),   'Info')
        self.btn_queue.setChecked(True)

        # Mutual exclusivity + tab switching
        _tab_btns = (self.btn_queue, self.btn_lyrics, self.btn_info)
        for _b in _tab_btns:
            _b.toggled.connect(lambda checked, src=_b: [
                other.setChecked(False) for other in _tab_btns if other is not src
            ] if checked else None)

        self.btn_queue.toggled.connect(lambda on: self._show_tab('queue') if on else None)
        self.btn_info.toggled.connect(lambda on: self._show_tab('info') if on else None)

        bb_layout.addWidget(self.btn_queue)
        bb_layout.addWidget(self.btn_lyrics)
        bb_layout.addWidget(self.btn_info)

        col.addWidget(bottom_bar)
        self._bottom_bar = bottom_bar

        root.addWidget(content, 1)

        # ── Right-edge resize handle (width) — not used in embedded mode ─────
        if not embedded:
            self._right_handle = _ResizeHandleRight(self)
            self._right_handle.resize_delta.connect(self._on_width_delta)
            root.addWidget(self._right_handle)
        else:
            self._right_handle = None

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _make_heart_pix(path: str, color: str):
        from PyQt6.QtGui import QPixmap, QPainter as _P
        base = QPixmap(path)
        if base.isNull():
            return QPixmap()
        base = base.scaled(QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = _P(pix)
        p.drawPixmap(0, 0, base)
        p.setCompositionMode(_P.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pix.rect(), QColor(color))
        p.end()
        return pix

    def set_client(self, client):
        self._navidrome_client = client

    def load_track(self, artist_id: str, artist_name: str):
        # Wipe stale content immediately if the artist has changed
        current_id = getattr(self._artist_info_panel, '_current_id', None)
        if artist_id != current_id:
            self._artist_info_panel._clear()
            self._artist_info_panel._build_empty("Loading…")
        self._pending_load = (artist_id, artist_name)
        if self.isVisible() and self.btn_info.isChecked():
            self._info_load_timer.start()

    def _do_load_artist_info(self):
        if self._pending_load is None:
            return
        args, self._pending_load = self._pending_load, None
        self._artist_info_panel.set_accent_color(self._accent_color)
        self._artist_info_panel.load_track(self._navidrome_client, *args)

    def _show_tab(self, tab: str):
        self._list.setVisible(tab == 'queue')
        self._artist_info_panel.setVisible(tab == 'info')
        if tab == 'info' and self._pending_load is not None:
            self._info_load_timer.start()

    def set_accent_color(self, color: str):
        self._accent_color = color
        for _btn in (self.btn_queue, self.btn_lyrics, self.btn_info):
            _btn.set_accent(color)
        self._artist_info_panel.set_accent_color(color)
        self._update_list_style()
        if not hasattr(self, '_scroll_reveal'):
            self._scroll_reveal = install_scroll_reveal(self._list.viewport(), self._list.verticalScrollBar())
        self._scroll_reveal.color = color
        self._list.viewport().update()
        self._duration_lbl.setStyleSheet(
            f'color: {color}; font-size: 11px; background: transparent; border: none;'
        )

    def apply_theme(self, theme):
        self._primary_px      = getattr(theme, 'font_size_primary',    14)
        self._primary_color   = getattr(theme, 'font_color_primary',   '#dddddd')
        self._secondary_px    = getattr(theme, 'font_size_secondary',  12)
        self._secondary_color = getattr(theme, 'font_color_secondary', '#777777')
        self._queue_lbl.setStyleSheet(
            f'color: {self._primary_color}; font-weight: bold; font-size: 16px; background: transparent; border: none;'
        )
        self._artist_info_panel.apply_theme(theme)
        self._list.viewport().update()

    def toggle_favorite_at(self, idx: int):
        item = self._list.item(idx)
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            raw = d.get('starred', False)
            current = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
            d['starred'] = not current
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._list.viewport().update()

    def _update_list_style(self):
        c = self._accent_color
        self._list.setStyleSheet(f'''
            QListWidget {{
                background: transparent;
                outline: 0;
                border: none;
            }}
            QListWidget::item {{
                border-bottom: 1px solid rgba(255,255,255,0.04);
            }}
            QListWidget::item:selected {{
                background: rgba(255,255,255,0.06);
            }}
            {scrollbar_css(c, hide_horizontal=True)}
        ''')

    def update_playing_state(self, is_playing: bool):
        self._is_playing = is_playing
        if is_playing:
            self._playing_movie.start()
        else:
            self._playing_movie.stop()
        self._list.viewport().update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_refresh is not None:
            args, self._pending_refresh = self._pending_refresh, None
            self.refresh(*args)
        if self.btn_info.isChecked() and self._pending_load is not None:
            self._info_load_timer.start()

    def refresh(self, playlist_data: list, current_index: int, is_playing: bool = False):
        if not self.isVisible():
            self._pending_refresh = (playlist_data, current_index, is_playing)
            return
        self._is_playing = is_playing
        if is_playing:
            self._playing_movie.start()
        else:
            self._playing_movie.stop()
        vscroll    = self._list.verticalScrollBar()
        saved_pos  = vscroll.value()

        self._list.blockSignals(True)
        self._list.clear()

        for i, track in enumerate(playlist_data):
            item = QListWidgetItem()
            item.setSizeHint(QSize(self._list.viewport().width(), ROW_H))
            d = dict(track)
            d['_num']        = i + 1
            d['_is_current'] = (i == current_index)
            d['_is_past']    = (current_index >= 0 and i < current_index)
            item.setData(Qt.ItemDataRole.UserRole, d)
            self._list.addItem(item)

        n = len(playlist_data)
        pos = (current_index + 1) if 0 <= current_index < n else 0
        self._position_lbl.setText(f'{pos}/{n}' if n else '0/0')

        total_sec = 0
        for t in playlist_data:
            dur = t.get('duration') or t.get('duration_ms')
            try:
                if isinstance(dur, (int, float)):
                    total_sec += int(dur / 1000) if dur > 9999 else int(dur)
                elif dur and ':' in str(dur):
                    parts = str(dur).split(':')
                    if len(parts) == 2:
                        total_sec += int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        total_sec += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except (ValueError, TypeError):
                pass
        h, rem = divmod(total_sec, 3600)
        m, s   = divmod(rem, 60)
        self._duration_lbl.setText(f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}')

        self._list.blockSignals(False)

        if 0 <= current_index < self._list.count():
            self._list.scrollToItem(
                self._list.item(current_index),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
        else:
            vscroll.setValue(saved_pos)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_rows_moved(self):
        new_tracks = []
        new_current = -1
        for i in range(self._list.count()):
            d = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if d:
                new_tracks.append(d)
                if d.get('_is_current'):
                    new_current = i
        self.reordered.emit(new_tracks, new_current)

    def _on_resize_delta(self, delta: int):
        new_h = max(self._MIN_H, min(self._MAX_H, self.height() + delta))
        actual_delta = new_h - self.height()
        # Move top edge up, keep bottom fixed
        self.setGeometry(self.x(), self.y() - actual_delta, self.width(), new_h)
        self._settings.setValue('queue_panel_height', new_h)

    def _on_width_delta(self, delta: int):
        new_w = max(260, min(800, self.width() + delta))
        # Left edge stays fixed, right edge moves
        self.resize(new_w, self.height())
        self._settings.setValue('queue_panel_width', new_w)

    def eventFilter(self, obj, event):
        if obj is self._list.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos  = event.position().toPoint()
                item = self._list.itemAt(pos)
                new_hover = None
                if item and self._artist_rect_at(item).contains(pos):
                    data   = item.data(Qt.ItemDataRole.UserRole)
                    artist = data.get('artist', '') if data else ''
                    part   = self._artist_part_at(item, pos.x(), artist)
                    if part:
                        new_hover = (self._list.row(item), part)
                if new_hover != self._hover_artist:
                    self._hover_artist = new_hover
                    self._list.viewport().update()
                self._list.viewport().setCursor(
                    Qt.CursorShape.PointingHandCursor if new_hover
                    else Qt.CursorShape.ArrowCursor
                )
            elif event.type() == QEvent.Type.Leave:
                if self._hover_artist is not None:
                    self._hover_artist = None
                    self._list.viewport().update()
            elif event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos  = event.position().toPoint()
                    item = self._list.itemAt(pos)
                    if item:
                        r = self._list.visualItemRect(item)
                        fav_x = r.right() - DUR_W - FAV_W - 8
                        if QRect(fav_x, r.top(), FAV_W, r.height()).contains(pos):
                            idx = self._list.row(item)
                            self.toggle_favorite_at(idx)
                            self.favorite_toggled.emit(idx)
                            return True
                    if item and self._artist_rect_at(item).contains(pos):
                        data   = item.data(Qt.ItemDataRole.UserRole)
                        artist = data.get('artist', '') if data else ''
                        name   = self._artist_part_at(item, pos.x(), artist)
                        if name:
                            self.artist_clicked.emit(name)
                            return True
        return super().eventFilter(obj, event)

    def _artist_rect_at(self, item: QListWidgetItem) -> QRect:
        r      = self._list.visualItemRect(item)
        text_x = NUM_W + 10
        text_w = self._list.viewport().width() - NUM_W - FAV_W - DUR_W - 28
        return QRect(text_x, r.top() + 28, text_w, 20)

    def _artist_part_at(self, item: QListWidgetItem, click_x: int, artist: str) -> str:
        f = QFont()
        f.setPixelSize(12)
        fm = QFontMetrics(f)
        ax = NUM_W + 10
        for part, is_sep in _split_artist(artist):
            pw = fm.horizontalAdvance(part)
            if not is_sep and ax <= click_x < ax + pw:
                return part.strip()
            ax += pw
        return ''

    def _on_double_clicked(self, item: QListWidgetItem):
        self.play_index.emit(self._list.row(item))

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        idx   = self._list.row(item)
        data  = item.data(Qt.ItemDataRole.UserRole) or {}
        track = {k: v for k, v in data.items() if not k.startswith('_')}

        _main = self.window()
        _theme = getattr(_main, 'theme', None)
        _bg  = getattr(_theme, 'main_panel_bg',       '14,14,14')
        _bc  = getattr(_theme, 'border_color',        '#2a2a2a')
        _fg  = getattr(_theme, 'font_color_primary',  '#dddddd')
        _fg2 = getattr(_theme, 'font_color_secondary','#555555')
        _px  = getattr(_theme, 'font_size_primary',   14)
        _acc = getattr(_theme, 'accent',               '#ffffff')
        MENU_CSS = (
            f"QMenu {{ background-color: rgb({_bg}); color: {_fg}; font-size: {_px}px; border: 1px solid {_bc};"
            "  border-radius: 12px; padding: 4px; }"
            f"QMenu::item {{ padding: 6px 25px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background-color: {menu_hover(_acc)}; color: {_fg}; }}"
            f"QMenu::item:disabled {{ color: {_fg2}; }}"
            f"QMenu::separator {{ height: 1px; background: {_bc}; margin: 4px 8px; }}"
        )
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(MENU_CSS)

        # Play / queue actions
        act_play = menu.addAction("Play Now")
        act_next = menu.addAction("Play Next")
        menu.addSeparator()

        # Favorite
        is_fav_raw = track.get('starred', False)
        is_fav = is_fav_raw.lower() in ('true', '1') if isinstance(is_fav_raw, str) else bool(is_fav_raw)
        act_fav = menu.addAction("Unlove (♥)" if is_fav else "Love (♡)")
        menu.addSeparator()

        # Add to Playlist submenu
        main = self.window()
        track_id = str(track.get('id', ''))
        if track_id and main:
            add_menu = QMenu("Add to Playlist", menu)
            add_menu.setStyleSheet(MENU_CSS)
            act_new_pl = QAction("+ New Playlist...", add_menu)
            act_new_pl.triggered.connect(lambda: self._add_to_new_playlist(main, [track_id]))
            add_menu.addAction(act_new_pl)
            playlists = []
            if hasattr(main, 'playlists_browser'):
                playlists = main.playlists_browser.all_playlists or []
            if playlists:
                add_menu.addSeparator()
                for pl in playlists:
                    pl_id = pl.get('id')
                    if not pl_id:
                        continue
                    label = f"{pl.get('name', 'Unnamed')}  ({pl.get('songCount', '')})" if pl.get('songCount', '') != '' else pl.get('name', 'Unnamed')
                    a = QAction(label, add_menu)
                    a.triggered.connect(lambda checked=False, pid=pl_id, pn=pl.get('name', ''): self._add_to_existing_playlist(main, pid, pn, [track_id]))
                    add_menu.addAction(a)
            menu.addMenu(add_menu)
            menu.addSeparator()

        # Go to submenu
        goto_menu = menu.addMenu("Go to")
        goto_menu.setStyleSheet(MENU_CSS)
        album_id = track.get('albumId') or track.get('parent')
        album_title = track.get('album', 'Unknown')
        if album_id and main:
            album_data = {'id': album_id, 'title': album_title, 'artist': track.get('artist'), 'coverArt': track.get('coverArt')}
            goto_menu.addAction(f"Album: {album_title}").triggered.connect(
                lambda: main.navigate_to_album(album_data))
        goto_menu.addSeparator()
        import re as _re
        artists = [p.strip() for p in _re.split(
            r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )',
            track.get('artist', '')) if p.strip()]
        for art in artists:
            goto_menu.addAction(f"Artist: {art}").triggered.connect(
                lambda checked=False, a=art: (self.artist_clicked.emit(a)))

        menu.addSeparator()
        act_info = menu.addAction("Get Info")
        menu.addSeparator()
        act_remove = menu.addAction("Remove from Queue")

        # Connect actions
        act_play.triggered.connect(lambda: self.play_index.emit(idx))
        act_next.triggered.connect(lambda: self.play_next_index.emit(idx))
        act_fav.triggered.connect(lambda: (self.toggle_favorite_at(idx), self.favorite_toggled.emit(idx)))
        tb = getattr(main, 'tracks_browser', None) if main else None
        act_info.triggered.connect(lambda: tb and tb._show_track_info(track))
        if not tb:
            act_info.setEnabled(False)
        act_remove.triggered.connect(lambda: self.remove_index.emit(idx))

        menu.exec(self._list.viewport().mapToGlobal(pos))

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
            is_public = dialog.is_public()
            import threading
            def _worker():
                try:
                    new_id = client.create_playlist(name, public=is_public)
                    if new_id:
                        client.add_tracks_to_playlist(new_id, track_ids)
                except Exception as e:
                    print(f"Queue: create playlist failed: {e}")
            threading.Thread(target=_worker, daemon=True).start()

    def _add_to_existing_playlist(self, main, pl_id, pl_name, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client:
            return
        import threading
        def _worker():
            try:
                client.add_tracks_to_playlist(pl_id, track_ids)
            except Exception as e:
                print(f"Queue: add to playlist failed: {e}")
        threading.Thread(target=_worker, daemon=True).start()
