"""favorites_view.py — Favorites tab: starred artists, albums and top artists."""
from collections import Counter

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QTreeWidget, QTreeWidgetItem,
                              QHeaderView, QStyle, QPushButton, QCheckBox,
                              QListWidget, QListWidgetItem, QLineEdit, QFrame,
                              QApplication)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QPoint, QRect
from PyQt6.QtGui import QColor, QMovie, QPixmap, QPainter as _QPainter, QCursor

from home import HomeAlbumRowWidget
from tracks_browser import _checkmark_svg_path


class _ShadowContextMenu(QFrame):
    """Frameless popup — shadow matches psysonic: 0 12px 32px rgba(0,0,0,0.6)."""
    _PAD = 36   # generous enough for blur+offset without being too heavy

    def __init__(self, parent=None, is_submenu: bool = False):
        super().__init__(parent,
                         Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._is_sub = is_submenu
        self._bg = QColor(20, 20, 20)
        self._bc = QColor(50, 50, 50)
        self._fg = '#dddddd'; self._fg2 = '#666666'
        self._hov = '#333333'; self._px = 14; self._accent = '#cccccc'
        self._callbacks: list = []
        self._open_sub = None
        self._sub_trigger = None
        self._sub_trigger_base = ''
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._poll_mouse)

        # Submenu: no left shadow so it doesn't overlap the parent menu rows
        pl = 4 if is_submenu else self._PAD
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pl, self._PAD, self._PAD, self._PAD)
        outer.setSpacing(0)
        self._lo = QVBoxLayout()
        self._lo.setContentsMargins(4, 4, 4, 4)
        self._lo.setSpacing(1)
        outer.addLayout(self._lo)

    def configure(self, bg_rgb: str, bc: str, fg: str, fg2: str, hov: str, px: int,
                  accent: str = '#cccccc'):
        try:
            r, g, b = [int(x) for x in bg_rgb.split(',')]
            self._bg = QColor(r, g, b)
        except Exception:
            self._bg = QColor(20, 20, 20)
        self._bc = QColor(bc)
        self._fg = fg; self._fg2 = fg2; self._hov = hov; self._px = px
        self._accent = accent

    def _close_open_sub(self):
        if self._open_sub and self._open_sub.isVisible():
            self._open_sub.hide()
        if self._sub_trigger and self._sub_trigger_base:
            self._sub_trigger.setStyleSheet(self._sub_trigger_base)
        self._open_sub = None
        self._sub_trigger = None
        self._sub_trigger_base = ''
        self._poll.stop()

    def _poll_mouse(self):
        """Close submenu when cursor is over neither submenu nor its trigger row."""
        if not (self._open_sub and self._open_sub.isVisible()):
            self._poll.stop()
            return
        pos = QCursor.pos()
        if self._open_sub.geometry().contains(pos):
            return  # cursor inside submenu — keep open
        if self._sub_trigger:
            tg = self._sub_trigger.mapToGlobal(QPoint(0, 0))
            if QRect(tg, self._sub_trigger.size()).contains(pos):
                return  # cursor still on trigger row — keep open
        self._close_open_sub()

    def _row(self, text: str, enabled: bool = True, color: str = '',
             icon_path: str = '') -> QWidget:
        from PyQt6.QtWidgets import QHBoxLayout as _HBL
        from player.widgets import tint_icon as _ti
        c = color or self._fg2
        base_ss = (f'color: {c}; font-size: {self._px}px; '
                   f'background: transparent; border-radius: 4px;')
        hov_ss  = (f'color: {c}; font-size: {self._px}px; '
                   f'background: {self._hov}; border-radius: 4px;')

        row = QWidget()
        row.setStyleSheet(base_ss)
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lo = _HBL(row)
        lo.setContentsMargins(12, 5, 20, 5)
        lo.setSpacing(8)

        if icon_path:
            ico = QLabel()
            icon_color = color if color else self._accent
            pix = _ti(icon_path, icon_color).pixmap(QSize(14, 14))
            ico.setPixmap(pix)
            ico.setFixedSize(14, 14)
            ico.setStyleSheet('background: transparent;')
            lo.addWidget(ico)
        else:
            lo.addSpacing(22)   # align text with icon rows

        txt = QLabel(text)
        txt.setStyleSheet(f'color: {c}; font-size: {self._px}px; background: transparent;')
        lo.addWidget(txt)
        lo.addStretch()

        if enabled:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            def _enter(_, _r=row, _h=hov_ss):
                self._close_open_sub()
                _r.setStyleSheet(_h)
            def _leave(_, _r=row, _b=base_ss):
                _r.setStyleSheet(_b)
            row.enterEvent = _enter
            row.leaveEvent = _leave
        return row

    def add_header(self, text: str):
        lbl = self._row(text, enabled=False)
        lbl.setStyleSheet(
            f'color: {self._fg}; font-size: {self._px}px; font-weight: bold; '
            f'padding: 6px 12px; background: transparent;')
        self._lo.addWidget(lbl)

    def add_separator(self):
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f'background: {self._bc.name()}; margin: 3px 8px;')
        sep.setFixedHeight(1)
        self._lo.addWidget(sep)

    def add_action(self, text: str, callback=None, enabled: bool = True,
                   color: str = '', icon_path: str = ''):
        row = self._row(text, enabled, color, icon_path)
        if enabled and callback:
            cb = callback
            self._callbacks.append(cb)
            def _press(_e, f=cb):
                f(); self.close()
            row.mousePressEvent = _press
        self._lo.addWidget(row)
        return row

    def add_submenu(self, text: str, items: list, icon_path: str = ''):
        """items = [(label, callback), ...]"""
        trigger = self._row(f'{text}  ›', icon_path=icon_path)
        self._lo.addWidget(trigger)
        sub = _ShadowContextMenu(self, is_submenu=True)
        sub.configure(
            f'{self._bg.red()},{self._bg.green()},{self._bg.blue()}',
            self._bc.name(), self._fg, self._fg2, self._hov, self._px)
        for lbl, cb in items:
            sub.add_action(lbl, cb)

        def _show():
            sub.adjustSize()
            tr = trigger.mapToGlobal(QPoint(trigger.width(), 0))
            gp = QPoint(tr.x(),
                        trigger.mapToGlobal(QPoint(0, -sub._PAD)).y())
            sub.move(gp)
            sub.show()
            self._poll.start()
        _hov_style = (f'color: {self._fg2}; font-size: {self._px}px; '
                      f'background: {self._hov}; border-radius: 4px;')
        _base_style = trigger.styleSheet()

        def _on_enter(_):
            self._close_open_sub()
            self._open_sub = sub
            self._sub_trigger = trigger
            self._sub_trigger_base = _base_style
            _show()
            trigger.setStyleSheet(_hov_style)
        trigger.enterEvent = _on_enter
        return trigger

    def hideEvent(self, event):
        self._close_open_sub()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._close_open_sub()
        super().closeEvent(event)

    def exec_at(self, pos: QPoint):
        self.adjustSize()
        self.move(pos)
        self.show()

    def paintEvent(self, _):
        from PyQt6.QtCore import QRectF
        p = _QPainter(self)
        p.setRenderHint(_QPainter.RenderHint.Antialiasing)
        pad = self._PAD
        # Shadow: psysonic 0 12px 32px rgba(0,0,0,0.6)
        # Offset mostly downward — minimal spread on sides/top
        BLUR = 35; OY = 10; MAX_A = 45
        pl = 4 if self._is_sub else pad
        content = QRectF(self.rect()).adjusted(pl, pad, -pad, -pad)
        p.setPen(Qt.PenStyle.NoPen)
        steps = 16
        for i in range(steps, 0, -1):
            t = i / steps
            alpha = int(MAX_A * (1 - t) ** 2)
            ex = BLUR * t
            left_ex = 0 if self._is_sub else -ex * .7
            rect = content.adjusted(left_ex,
                                    -ex * .4 + OY * (1 - t),
                                     ex * .7,
                                     ex + OY * t)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(rect, 10 + ex * .25, 10 + ex * .25)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(content, 10, 10)
        p.end()


class _GenrePopup(QFrame):
    """Genre filter popup styled like the tracks browser ColumnFilterPopup."""
    selection_changed = pyqtSignal(set)

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.WindowType.Popup |
                         Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedWidth(240)
        self._genres: list[str] = []
        self._selected: set[str] = set()
        self._paint_bg = QColor(20, 20, 20)
        self._paint_bc = QColor(42, 42, 42)

        lo = QVBoxLayout(self)
        lo.setContentsMargins(8, 8, 8, 8)
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
        fg  = getattr(theme, 'font_color_primary',  '#dddddd') if theme else '#dddddd'
        fg2 = getattr(theme, 'font_color_secondary', '#888888') if theme else '#888888'
        try:
            self._paint_bg = QColor(*[int(x) for x in bg.split(',')])
        except Exception:
            self._paint_bg = QColor(20, 20, 20)
        if theme and not getattr(theme, 'auto_border_from_accent', True):
            self._paint_bc = QColor(getattr(theme, 'manual_border_color', '#2a2a2a'))
        else:
            self._paint_bc = QColor(bc)
        from player.mixins.visuals import scrollbar_css
        self.setStyleSheet(f"""
            QLineEdit {{
                background: rgb({bg}); color: {fg}; border: 1px solid {bc};
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
            }}
            QListWidget {{
                background: transparent; border: none; color: {fg}; font-size: 13px;
            }}
            QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
            QListWidget::item:hover {{ background: {hov}; }}
            QListWidget::item:selected {{ background: transparent; color: {fg}; }}
            QListWidget::indicator {{
                width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid {bc}; background: rgb({bg});
            }}
            QListWidget::indicator:checked {{
                background: rgb({bg});
                image: url("{_checkmark_svg_path(accent)}");
            }}
            QPushButton {{
                background: transparent; color: {fg}; border: 1px solid {bc};
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
        p.setPen(self._paint_bc)
        p.setBrush(self._paint_bg)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
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
        # Toggle checkbox when clicking anywhere on the row
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
from albums_browser import GridCoverWorker, _TrackListDelegate, _TrackHeader, resource_path
from now_playing_info import _Card
from player.mixins.visuals import scrollbar_css, install_scroll_reveal


class _SortableTrackHeader(_TrackHeader):
    """_TrackHeader extended with 3-state sort cycling on sortable columns."""

    sort_changed = pyqtSignal(int, str)   # col, 'asc' | 'desc' | ''

    # Columns that support sorting (indices into favorites' 7-col layout).
    # Genre (col 4) is excluded.
    SORT_COLS = {0, 1, 2, 3, 5, 6}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sort_col   = -1    # currently sorted column (-1 = none)
        self._sort_state = ''    # 'asc' | 'desc' | ''
        self._up_pix   = self._load_icon('img/filter_up.png')
        self._down_pix = self._load_icon('img/filter_down.png')

    @staticmethod
    def _load_icon(path):
        p = QPixmap(resource_path(path))
        if p.isNull():
            return QPixmap()
        return p.scaled(QSize(10, 10), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)

    def _tinted_pix(self, pix):
        if pix.isNull():
            return pix
        color = self._accent
        out = QPixmap(pix.size()); out.fill(Qt.GlobalColor.transparent)
        p = _QPainter(out)
        p.drawPixmap(0, 0, pix)
        p.setCompositionMode(_QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(out.rect(), color)
        p.end()
        return out

    def _in_resize_zone(self, x: int) -> bool:
        grip = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderGripMargin) + 2
        for i in range(self.count()):
            boundary = self.sectionViewportPosition(i) + self.sectionSize(i)
            if abs(x - boundary) <= grip:
                return True
        return False

    # ── Override click to cycle sort state ───────────────────────────────
    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and not self._in_resize_zone(event.pos().x())
                and not self._near_flex_boundary(event.pos().x())):
            col = self.logicalIndexAt(event.pos().x())
            if col in self.SORT_COLS:
                if self._sort_col == col:
                    # Cycle: desc → asc → none
                    self._sort_state = {'desc': 'asc', 'asc': '', '': 'desc'}[self._sort_state]
                else:
                    self._sort_col   = col
                    self._sort_state = 'desc'
                if not self._sort_state:
                    self._sort_col = -1
                self.viewport().update()
                self.sort_changed.emit(col, self._sort_state)
                event.accept()
                return
        super().mousePressEvent(event)

    # Centered columns in the 7-col favorites layout: #(0), DURATION(5), PLAYS(6)
    _CENTER_COLS = {0, 5, 6}

    _ICON_SZ = 14   # matches tracks browser FILTER_ICON_SIZE

    # ── Override paintSection: correct alignment + sort arrow ─────────────
    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        from PyQt6.QtGui import QFont, QPen, QPainter, QFontMetrics
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, Qt.GlobalColor.transparent)

        text   = self.model().headerData(logical_index, Qt.Orientation.Horizontal) or ''
        f      = QFont(); f.setPixelSize(self._secondary_px()); f.setBold(True)
        fm     = QFontMetrics(f)
        painter.setFont(f)
        painter.setPen(QColor(self._secondary_color()))

        has_sort = (logical_index == self._sort_col and bool(self._sort_state))
        sz       = self._ICON_SZ
        fy       = rect.bottom() - sz - 8   # same baseline as text

        centered = logical_index in self._CENTER_COLS
        if centered and has_sort:
            # Text + icon grouped and centred together
            text_w    = fm.horizontalAdvance(text)
            content_w = text_w + 4 + sz
            gx        = rect.left() + 4 + max(0, (rect.width() - 8 - content_w) // 2)
            from PyQt6.QtCore import QRect as _QR
            painter.drawText(_QR(gx, rect.top(), text_w, rect.height() - 8),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
            fx = gx + text_w + 4
        else:
            h_align = Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft
            painter.drawText(rect.adjusted(4, 0, -4, -8),
                             h_align | Qt.AlignmentFlag.AlignBottom, text)
            fx = rect.left() + 4 + fm.horizontalAdvance(text) + 4

        # Bottom border
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Column separator
        if logical_index > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border_qcolor(), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(rect.right(), rect.top() - 5, rect.right(), rect.bottom() - 8)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Sort arrow — same position/tint as tracks browser
        if has_sort:
            src = self._up_pix if self._sort_state == 'asc' else self._down_pix
            pix = self._tinted_pix(src.scaled(
                QSize(sz, sz), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            if not pix.isNull():
                painter.drawPixmap(fx, int(fy), pix)

        painter.restore()


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


class FavoritesView(QWidget):
    album_clicked  = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)
    play_album     = pyqtSignal(dict)
    play_track     = pyqtSignal(dict)
    play_all       = pyqtSignal(object)   # emits list[dict] → play_whole_album
    shuffle_all    = pyqtSignal(object)   # emits list[dict] → play_whole_album shuffled

    def __init__(self, client=None, parent=None):
        super().__init__(parent)
        self._client       = client
        self._accent       = '#888888'
        self._worker       = None
        self._cover_worker = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('FavoritesPanel')

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName('FavScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_reveal = None

        content = QWidget()
        content.setObjectName('FavContent')
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(5, 20, 4, 50)
        self._layout.setSpacing(10)
        self.scroll.setWidget(content)
        main.addWidget(self.scroll)

        # ── Artists row ───────────────────────────────────────────────────
        self._artists_row = HomeAlbumRowWidget('Artists')
        self._artists_row.album_clicked.connect(self._on_artist_card_clicked)
        self._artists_row.delegate.clickable_artist = False
        self._artists_row.delegate.show_play_btn    = False
        self._layout.addWidget(self._artists_row)

        # ── Albums row ────────────────────────────────────────────────────
        self._albums_row = HomeAlbumRowWidget('Albums')
        self._albums_row.album_clicked.connect(self.album_clicked)
        self._albums_row.play_album.connect(self.play_album)
        self._albums_row.artist_clicked.connect(self.artist_clicked)
        self._layout.addWidget(self._albums_row)

        # ── Top Artists by Favorites ──────────────────────────────────────
        self._top_row = HomeAlbumRowWidget('Top Artists by Favorites')
        self._top_row.album_clicked.connect(self._on_top_artist_clicked)
        self._top_row.delegate.clickable_artist = False
        self._top_row.delegate.show_play_btn    = False
        self._layout.addWidget(self._top_row)

        # ── Favorite Songs header ─────────────────────────────────────────
        self._selected_genres: set = set()
        self._selected_artist: str = ''

        _hdr = QWidget(); _hdr.setObjectName('FavSongsHdr')
        _hdr.setStyleSheet('QWidget#FavSongsHdr { background: transparent; }')
        _hdr_lo = QVBoxLayout(_hdr)
        _hdr_lo.setContentsMargins(6, 12, 6, 4)
        _hdr_lo.setSpacing(8)

        # Row 1: "Songs" + status
        _title_row = QHBoxLayout()
        _title_row.setSpacing(8)
        self._songs_lbl = QLabel('Favorite Songs')
        self._songs_status_lbl = QLabel('')
        self._songs_status_lbl.setStyleSheet('color: #666; font-size: 12px; background: transparent;')
        _title_row.addWidget(self._songs_lbl)
        _title_row.addWidget(self._songs_status_lbl)
        _title_row.addStretch()
        _hdr_lo.addLayout(_title_row)

        # Row 2: action buttons
        _btn_row = QHBoxLayout()
        _btn_row.setSpacing(8)

        _icon_btn_style = (
            'QPushButton { background: transparent; border: none; border-radius: 4px; }'
            ' QPushButton:hover { background: rgba(255,255,255,0.1); }'
            ' QPushButton:checked { background: rgba(255,255,255,0.15); }'
        )

        from player.widgets import PlayButton as _PB
        self._play_all_btn = _PB()
        self._play_all_btn.setFixedSize(58, 58)
        self._play_all_btn.setIconSize(QSize(18, 18))
        self._play_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_all_btn.ensure_glow()
        self._play_all_btn.clicked.connect(self._on_play_all)
        _btn_row.addWidget(self._play_all_btn)

        self._shuffle_btn = QPushButton()
        self._shuffle_btn.setFlat(True)
        self._shuffle_btn.setFixedSize(36, 36)
        self._shuffle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shuffle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._shuffle_btn.setStyleSheet(_icon_btn_style)
        self._shuffle_btn.setIconSize(QSize(20, 20))
        self._shuffle_btn.clicked.connect(self._on_shuffle_all)
        _btn_row.addWidget(self._shuffle_btn)

        self._genre_btn = QPushButton()
        self._genre_btn.setFlat(True)
        self._genre_btn.setCheckable(True)
        self._genre_btn.setFixedSize(36, 36)
        self._genre_btn.setIconSize(QSize(20, 20))
        self._genre_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._genre_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._genre_btn.setStyleSheet(_icon_btn_style)
        self._genre_btn.clicked.connect(self._toggle_genre_popup)
        _btn_row.addWidget(self._genre_btn)

        self._clear_artist_btn = QPushButton('✕  Clear filters')
        self._clear_artist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_artist_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_artist_btn.clicked.connect(self._clear_all_filters)
        self._clear_artist_btn.hide()
        _btn_row.addWidget(self._clear_artist_btn)

        _btn_row.addStretch()
        _hdr_lo.addLayout(_btn_row)

        self._layout.addWidget(_hdr)

        self._genre_popup = _GenrePopup(self)
        self._genre_popup.hide()
        self._genre_popup.selection_changed.connect(self._on_genres_changed)

        # Sync button checked state when popup closes
        orig_hide = self._genre_popup.hideEvent
        def _on_popup_hide(e):
            orig_hide(e)
            self._genre_btn.setChecked(bool(self._selected_genres))
        self._genre_popup.hideEvent = _on_popup_hide

        self._track_tree = QTreeWidget()
        self._track_tree.setColumnCount(7)
        self._track_tree.setHeaderLabels(['#', 'TITLE', 'ARTIST', 'ALBUM', 'GENRE', 'DURATION', 'PLAYS'])
        self._track_tree.setRootIsDecorated(False)
        self._track_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._track_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._track_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._track_tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._track_tree.setMouseTracking(True)
        self._track_tree.viewport().setMouseTracking(True)
        self._track_tree.setStyleSheet("""
            QTreeWidget { background: transparent; border: none; outline: none; }
            QTreeWidget::item { height: 38px; border: none; padding-left: 4px; }
            QTreeWidget::item:hover { background: transparent; }
            QTreeWidget::item:selected { background: transparent; }
            QHeaderView { background: transparent; border: none; }
        """)

        self._track_delegate = _TrackListDelegate(self._track_tree, heart_col=-1)
        self._playing_movie = QMovie(resource_path('img/playing.gif'))
        self._playing_movie.setScaledSize(QSize(30, 30))
        self._playing_movie.frameChanged.connect(
            lambda: self._track_tree.viewport().update())
        self._track_delegate.set_movie(self._playing_movie)

        def _heart_pix(path, color):
            from PyQt6.QtGui import QPixmap, QPainter as _P
            base = QPixmap(resource_path(path)).scaled(
                QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            out = QPixmap(base.size()); out.fill(Qt.GlobalColor.transparent)
            p = _P(out); p.drawPixmap(0, 0, base)
            p.setCompositionMode(_P.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(color)); p.end()
            return out
        self._track_delegate.set_heart_pixmaps(
            _heart_pix('img/heart_filled.png', '#E91E63'),
            _heart_pix('img/heart.png', '#555555'),
        )
        self._track_tree.setItemDelegate(self._track_delegate)

        self._track_header = _SortableTrackHeader(self._track_tree)
        self._track_header.sort_changed.connect(self._on_sort)
        self._track_tree.setHeader(self._track_header)
        hdr = self._track_header
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 7):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 50)
        hdr.resizeSection(2, 160); hdr.resizeSection(3, 160)
        hdr.resizeSection(4, 120); hdr.resizeSection(5, 76)
        hdr.resizeSection(6, 60)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(30)
        self._restore_col_widths()

        self._col_save_timer = QTimer(self)
        self._col_save_timer.setSingleShot(True)
        self._col_save_timer.setInterval(400)
        self._col_save_timer.timeout.connect(self._save_col_widths)
        hdr.sectionResized.connect(lambda *_: self._col_save_timer.start())

        self._track_tree.viewport().setAutoFillBackground(False)
        self._track_tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._track_tree.itemDoubleClicked.connect(self._on_track_double_clicked)
        self._track_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._track_tree.customContextMenuRequested.connect(self._show_context_menu)

        self._track_card = _Card()
        _tcl = QVBoxLayout(self._track_card)
        _tcl.setContentsMargins(0, 16, 0, 0)
        _tcl.setSpacing(0)
        _tcl.addWidget(self._track_tree)
        _card_wrap = QWidget(); _card_wrap.setStyleSheet('background: transparent;')
        _cw_lo = QVBoxLayout(_card_wrap)
        _cw_lo.setContentsMargins(7, 0, 8, 0)
        _cw_lo.setSpacing(0)
        _cw_lo.addWidget(self._track_card)
        self._layout.addWidget(_card_wrap)

        self._layout.addStretch()

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
        theme = getattr(self.window(), 'theme', None)
        bg    = getattr(theme, 'main_panel_bg', '14,14,14') if theme else '14,14,14'
        fc1   = getattr(theme, 'font_color_primary',  '#dddddd') if theme else '#dddddd'
        fsize = getattr(theme, 'font_size_primary',   14)        if theme else 14
        self.scroll.setStyleSheet(
            f'QScrollArea {{ background: rgb({bg}); border: none; }}'
            + scrollbar_css(color)
        )
        self._songs_lbl.setStyleSheet(
            f'color: {fc1}; font-size: {fsize + 1}px; font-weight: bold;'
            ' background: transparent; padding: 8px 6px 4px 6px;'
        )
        for row in (self._artists_row, self._albums_row, self._top_row):
            row.set_accent_color(color)
        if hasattr(self, '_track_header'):
            self._track_header.set_accent(color)
        if hasattr(self, '_track_card'):
            border  = getattr(theme, 'border_color',        '#2a2a2a') if theme else '#2a2a2a'
            card_bg = getattr(theme, 'now_playing_card_bg', '#1e1e1e') if theme else '#1e1e1e'
            self._track_card.set_border(border)
            self._track_card.set_bg(card_bg)
        if self._scroll_reveal:
            self._scroll_reveal.color = color
        from player.mixins.visuals import resolve_menu_hover
        hov = resolve_menu_hover(theme)
        from player.widgets import tint_icon
        self._play_all_btn.apply_accent(color, theme)
        _icon_style = (
            f'QPushButton {{ background: transparent; border: none; border-radius: 4px; }}'
            f' QPushButton:hover {{ background: {hov}; }}'
        )
        from player.mixins.visuals import resolve_active_hover
        _ah = resolve_active_hover(theme)
        _ah_css = f'rgba({_ah.red()},{_ah.green()},{_ah.blue()},{_ah.alpha()})'
        _filter_style = _icon_style + f' QPushButton:checked {{ background: {_ah_css}; }}'
        sec = getattr(theme, 'font_color_secondary', '#888888') if theme else '#888888'
        self._shuffle_btn.setIcon(tint_icon('img/shuffle.png', sec))
        self._shuffle_btn.setStyleSheet(_icon_style)
        self._genre_btn.setIcon(tint_icon('img/filter.png', sec))
        self._genre_btn.setStyleSheet(_filter_style)
        self._clear_artist_btn.setStyleSheet(
            f'QPushButton {{ background: transparent; border: none; '
            f'color: {color}; font-size: 11px; padding: 4px 8px; }}'
            f'QPushButton:hover {{ text-decoration: underline; }}'
        )
        self._genre_popup.apply_theme(theme, color, hov)

    def refresh(self):
        if not self._client:
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = _StarredWorker(self._client)
        self._worker.done.connect(self._on_data)
        self._worker.start()

    # ── Internal ──────────────────────────────────────────────────────────

    def _on_data(self, data: dict):
        songs   = data.get('songs',   [])
        albums  = data.get('albums',  [])
        artists = data.get('artists', [])

        # Build artist-id lookup first — used by both sections
        # _parse_song_data stores artist ID as 'artist_id' (not 'artistId')
        song_cover_lookup: dict = {}
        for s in songs:
            name = s.get('artist', '')
            aid  = s.get('artist_id') or s.get('artistId', '')
            if name and name not in song_cover_lookup and aid:
                # Ensure ar- prefix so Navidrome serves the artist photo
                song_cover_lookup[name] = aid if aid.startswith('ar-') else f'ar-{aid}'

        # Artists row — use artist_id from songs for reliable artist photo
        artist_items = []
        for a in artists:
            card = self._artist_to_card(a)
            name = a.get('name', '')
            if name in song_cover_lookup:
                card['coverArt'] = song_cover_lookup[name]
            artist_items.append(card)
        self._artists_row.populate(artist_items)

        # Albums row
        album_items = [self._album_to_card(a) for a in albums]
        self._albums_row.populate(album_items)

        # Top Artists by Favorites — count songs per artist, cap at 16
        counts = Counter(s.get('artist', '') for s in songs if s.get('artist'))
        artist_lookup = {a.get('name', ''): a for a in artists}

        top = sorted(counts.items(), key=lambda x: -x[1])[:16]
        top_items = []
        for name, count in top:
            a = artist_lookup.get(name, {})
            # Always prefer artistId from songs — it reliably maps to the artist
            # photo via getCoverArt. Starred-artist coverArt can be stale/wrong.
            cid = song_cover_lookup.get(name) or a.get('id', '')
            card = {
                'id':           a.get('id', ''),
                'title':        name,
                'artist':       f'{count} song{"s" if count != 1 else ""}',
                'coverArt':     cid,
                '_is_artist':   True,
                '_artist_name': name,
            }
            top_items.append(card)
        self._top_row.populate(top_items)

        self._populate_tracks(songs)

        # Populate genre filter with genres from all songs
        all_genres: set = set()
        for s in songs:
            for g in (s.get('genre', '') or '').split('•'):
                g = g.strip()
                if g:
                    all_genres.add(g)
        self._genre_popup.set_genres(sorted(all_genres), self._selected_genres)

        # Queue covers through the persistent worker
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
        for row in (self._artists_row, self._albums_row, self._top_row):
            row.apply_cover(cover_id, image_data)

    # col → sort key function
    _SORT_KEYS = {
        0: lambda t, i: i,
        1: lambda t, i: t.get('title', '').lower(),
        2: lambda t, i: t.get('artist', '').lower(),
        3: lambda t, i: t.get('album', '').lower(),
        5: lambda t, i: t.get('duration_ms', 0) or 0,
        6: lambda t, i: int(t.get('play_count', 0) or 0),
    }

    # ── Genre filter ──────────────────────────────────────────────────────

    def _toggle_genre_popup(self):
        if self._genre_popup.isVisible():
            self._genre_popup.hide()
            self._genre_btn.setChecked(False)
            return
        btn_pos = self._genre_btn.mapToGlobal(QPoint(0, self._genre_btn.height() + 4))
        self._genre_popup.move(btn_pos)
        self._genre_popup.show()
        self._genre_popup.raise_()
        self._genre_btn.setChecked(True)

    def _on_genres_changed(self, genres: set):
        self._selected_genres = genres
        self._genre_btn.setChecked(bool(genres))
        self._apply_filters()

    # ── Artist filter (Top Artists row) ──────────────────────────────────

    def _on_top_artist_clicked(self, card: dict):
        name = card.get('_artist_name', card.get('title', ''))
        self._selected_artist = '' if self._selected_artist == name else name
        # Reset genre filter when switching artist
        if self._selected_artist and self._selected_genres:
            self._selected_genres = set()
            self._genre_btn.setChecked(False)
            self._genre_popup.set_genres(self._genre_popup._genres, set())
        self._apply_filters()

    def _clear_all_filters(self):
        self._selected_artist = ''
        self._selected_genres = set()
        self._genre_btn.setChecked(False)
        self._genre_popup.set_genres(self._genre_popup._genres, set())
        self._apply_filters()

    def _on_play_all(self):
        songs = getattr(self, '_songs', [])
        if songs:
            self.play_all.emit(songs)

    def _on_shuffle_all(self):
        import random
        songs = list(getattr(self, '_songs', []))
        if songs:
            random.shuffle(songs)
            self.shuffle_all.emit(songs)

    # ── Combined filter application ───────────────────────────────────────

    def _apply_filters(self):
        all_songs = list(getattr(self, '_songs_original', []))
        songs = all_songs
        if self._selected_artist:
            songs = [s for s in songs if s.get('artist', '') == self._selected_artist]
        if self._selected_genres:
            def _genre_match(s):
                g = s.get('genre', '') or ''
                return any(sel in g for sel in self._selected_genres)
            songs = [s for s in songs if _genre_match(s)]
        self._populate_tracks(songs, update_original=False)

        # Update status label
        total = len(all_songs)
        shown = len(songs)
        parts = []
        if self._selected_artist:
            parts.append(self._selected_artist)
        if self._selected_genres:
            parts.append(', '.join(sorted(self._selected_genres)))
        if parts or shown != total:
            self._songs_status_lbl.setText(
                f'Showing {shown} of {total}' + (f'  ({" · ".join(parts)})' if parts else ''))
        else:
            self._songs_status_lbl.setText('')

        self._clear_artist_btn.setVisible(bool(self._selected_artist or self._selected_genres))

    def _on_sort(self, col: int, state: str):
        songs = list(getattr(self, '_songs_original', self._songs))
        if state and col in self._SORT_KEYS:
            key = self._SORT_KEYS[col]
            songs = sorted(enumerate(songs), key=lambda x: key(x[1], x[0]),
                           reverse=(state == 'desc'))
            songs = [s for _, s in songs]
        self._populate_tracks(songs, update_original=False)

    def _populate_tracks(self, songs: list, update_original: bool = True):
        if update_original:
            self._songs_original = list(songs)
        tree = self._track_tree
        tree.setUpdatesEnabled(False)
        tree.clear()
        theme = getattr(self.window(), 'theme', None)
        pri = QColor(getattr(theme, 'font_color_primary',   '#dddddd') if theme else '#dddddd')
        sec = QColor(getattr(theme, 'font_color_secondary', '#aaaaaa') if theme else '#aaaaaa')
        for idx, t in enumerate(songs):
            dur_ms = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
            secs   = dur_ms // 1000
            dur    = f'{secs // 60}:{secs % 60:02d}'
            item = QTreeWidgetItem([
                str(idx + 1),
                t.get('title', ''),
                t.get('artist', ''),
                t.get('album', '') or '',
                t.get('genre', '') or '',
                dur,
                str(t.get('play_count') or 0) if t.get('play_count') else '-',
            ])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {'_track_idx': idx, 'id': str(t.get('id', ''))})
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            for col in range(7):
                item.setForeground(col, pri if col == 1 else sec)
            tree.addTopLevelItem(item)
        tree.setUpdatesEnabled(True)
        row_h = tree.sizeHintForRow(0) if songs else 38
        hdr_h = tree.header().height()
        tree.setFixedHeight(hdr_h + row_h * len(songs) + 4)
        self._songs = songs   # store for double-click lookup

    _COL_SETTINGS_KEY = 'favorites/track_col_widths'
    _COL_MIN = {0: 30, 2: 80, 3: 80, 4: 60, 5: 50, 6: 40}

    def _save_col_widths(self):
        from PyQt6.QtCore import QSettings
        hdr = self._track_header
        widths = {str(i): hdr.sectionSize(i) for i in range(hdr.count()) if i != 1}
        QSettings().setValue(self._COL_SETTINGS_KEY, widths)

    def _restore_col_widths(self):
        from PyQt6.QtCore import QSettings
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

    def _show_context_menu(self, pos):
        item = self._track_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        idx  = data.get('_track_idx', -1)
        songs = getattr(self, '_songs', [])
        if not (0 <= idx < len(songs)):
            return
        track = songs[idx]
        main  = self.window()

        theme = getattr(main, 'theme', None)
        bg  = getattr(theme, 'main_panel_bg',       '14,14,14') if theme else '14,14,14'
        if theme and not getattr(theme, 'auto_border_from_accent', True):
            bc = getattr(theme, 'manual_border_color', '#2a2a2a')
        else:
            bc = getattr(theme, 'border_color', '#2a2a2a') if theme else '#2a2a2a'
        fg  = getattr(theme, 'font_color_primary',  '#dddddd')  if theme else '#dddddd'
        fg2 = getattr(theme, 'font_color_secondary','#555555')  if theme else '#555555'
        px  = getattr(theme, 'font_size_primary',   14)         if theme else 14
        from player.mixins.visuals import resolve_menu_hover
        hov = resolve_menu_hover(theme)

        menu = _ShadowContextMenu(self)
        acc = getattr(theme, 'accent', '#cccccc') if theme else '#cccccc'
        menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)

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

        def _toggle_fav():
            client = getattr(main, 'navidrome_client', None)
            if not client: return
            new_state = not is_fav
            __import__('threading').Thread(
                target=lambda: client.set_favorite(track_id, new_state), daemon=True).start()
            if not new_state:
                if hasattr(self, '_songs_original'):
                    self._songs_original = [s for s in self._songs_original
                                            if str(s.get('id', '')) != track_id]
            else:
                track['starred'] = True
                for s in getattr(self, '_songs_original', []):
                    if str(s.get('id', '')) == track_id:
                        s['starred'] = True
            self._apply_filters()

        playlists = getattr(getattr(main, 'playlists_browser', None), 'all_playlists', None) or []
        if playlists and track_id:
            pl_items = []
            for pl in playlists:
                pid = pl.get('id')
                if not pid: continue
                cnt = pl.get('songCount', '')
                lbl = f"{pl.get('name','Unnamed')}  ({cnt})" if cnt != '' else pl.get('name','Unnamed')
                def _add(_, _pid=pid):
                    c = getattr(main, 'navidrome_client', None)
                    if c: __import__('threading').Thread(
                        target=lambda: c.add_tracks_to_playlist(_pid, [track_id]), daemon=True).start()
                pl_items.append((lbl, _add))
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')

        tb = getattr(main, 'tracks_browser', None)
        menu.add_action('Get Info',
                        callback=(lambda: tb._show_track_info(track)) if tb else None,
                        enabled=bool(tb), icon_path='img/info.png')

        _HEART_COLOR = '#E91E63'
        fav_label = 'Remove from Favorites' if is_fav else 'Add to Favorites'
        fav_icon  = 'img/heart_filled.png'  if is_fav else 'img/heart.png'
        menu.add_action(fav_label, _toggle_fav,
                        color=_HEART_COLOR, icon_path=fav_icon)

        gp = self._track_tree.viewport().mapToGlobal(pos)
        menu.exec_at(QPoint(gp.x() - menu._PAD, gp.y() - menu._PAD))

    def _on_track_double_clicked(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        idx  = data.get('_track_idx', -1)
        if 0 <= idx < len(getattr(self, '_songs', [])):
            self.play_track.emit(self._songs[idx])

    def _on_artist_card_clicked(self, card: dict):
        """Artist cards emit album_clicked with _is_artist=True — route to artist view."""
        if card.get('_is_artist') and card.get('_artist_name'):
            self.artist_clicked.emit(card['_artist_name'])
        else:
            self.album_clicked.emit(card)

    @staticmethod
    def _artist_to_card(a: dict) -> dict:
        n = a.get('albumCount', a.get('album_count', ''))
        subtitle = f"{n} Album{'s' if n != 1 else ''}" if n else ''
        # Navidrome serves artist photos at getCoverArt?id=ar-{artist_id}.
        # Prefer explicit artistImageUrl, then build the ar- prefixed cover ID.
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
        }

    def showEvent(self, event):
        super().showEvent(event)
        if self._scroll_reveal is None:
            self._scroll_reveal = install_scroll_reveal(
                self.scroll.viewport(),
                self.scroll.verticalScrollBar()
            )
            self._scroll_reveal.color = self._accent
        # Refresh data every time the tab is shown so new favorites appear
        self.refresh()
