"""
queue_panel.py — Floating queue overlay panel, anchored above the footer.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy,
)
from PyQt6.QtCore import (Qt, pyqtSignal, pyqtSlot, QRectF, QPoint, QSettings, QEvent,
                          QTimer, QObject, QAbstractListModel, QModelIndex, QUrl)
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon
from PyQt6.QtQuickWidgets import QQuickWidget
import os
from player.mixins.visuals import resolve_menu_hover
from player import resource_path
from player.widgets import QMLGridWrapper, AlbumIconProvider


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


# ── Simple icon-only header button (same color/hover as _TabButton) ───────────

class _HeaderIconButton(QPushButton):
    def __init__(self, icon_path, tooltip='', parent=None):
        super().__init__(parent)
        self._icon_path = icon_path
        self._hovered   = False
        self.setFixedSize(28, 28)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setStyleSheet('QPushButton { background: transparent; border: none; border-radius: 4px; }')
        self._icon_lbl = QLabel(self)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._icon_lbl.setGeometry(0, 0, 28, 28)
        self._refresh()

    def enterEvent(self, event):
        self._hovered = True;  self._refresh(); super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False; self._refresh(); super().leaveEvent(event)

    def _refresh(self):
        color = '#aaaaaa' if self._hovered else '#555555'
        pix = QPixmap(self._icon_path)
        if not pix.isNull():
            pix = pix.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            out = QPixmap(pix.size()); out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.drawPixmap(0, 0, pix)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(color))
            p.end()
            self._icon_lbl.setPixmap(out)


# ── Track model (backs queue_list.qml) ───────────────────────────────────────

class QueueTrackModel(QAbstractListModel):
    TRACK_ID     = Qt.ItemDataRole.UserRole + 1
    TRACK_TITLE  = Qt.ItemDataRole.UserRole + 2
    ARTIST_NAME  = Qt.ItemDataRole.UserRole + 3
    DURATION_STR = Qt.ItemDataRole.UserRole + 4
    IS_FAVORITE  = Qt.ItemDataRole.UserRole + 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        if role == self.TRACK_ID:     return r['id']
        if role == self.TRACK_TITLE:  return r['title']
        if role == self.ARTIST_NAME:  return r['artist']
        if role == self.DURATION_STR: return r['duration']
        if role == self.IS_FAVORITE:  return r['fav']
        return None

    def roleNames(self):
        return {
            self.TRACK_ID:     b"trackId",
            self.TRACK_TITLE:  b"trackTitle",
            self.ARTIST_NAME:  b"artistName",
            self.DURATION_STR: b"durationStr",
            self.IS_FAVORITE:  b"isFavorite",
        }

    def set_rows(self, rows: list):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def set_favorite(self, idx: int, value: bool):
        if 0 <= idx < len(self._rows):
            self._rows[idx]['fav'] = value
            mi = self.index(idx)
            self.dataChanged.emit(mi, mi, [self.IS_FAVORITE])


# ── Bridge (Python <-> queue_list.qml) ───────────────────────────────────────

class QueueBridge(QObject):
    accentColorChanged       = pyqtSignal(str)
    hoverColorChanged        = pyqtSignal(str)
    panelBgChanged           = pyqtSignal(str)
    fontColorPrimaryChanged  = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    fontSizePrimaryChanged   = pyqtSignal(int)
    fontSizeSecondaryChanged = pyqtSignal(int)
    currentIndexChanged      = pyqtSignal(int)
    isPlayingChanged         = pyqtSignal(bool)
    scrollToIndexRequested   = pyqtSignal(int)

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    @pyqtSlot(int)
    def trackPlayClicked(self, idx):
        self._panel.play_index.emit(idx)

    @pyqtSlot(int)
    def trackFavoriteClicked(self, idx):
        self._panel.toggle_favorite_at(idx)
        self._panel.favorite_toggled.emit(idx)

    @pyqtSlot(str)
    def trackArtistClicked(self, name):
        if name:
            self._panel.artist_clicked.emit(name)

    @pyqtSlot(int, int, int)
    def trackContextMenuRequested(self, idx, gx, gy):
        self._panel._show_context_menu_at(idx, QPoint(gx, gy))

    @pyqtSlot(int, int)
    def reorderTrack(self, from_idx, to_idx):
        self._panel._on_reorder_track(from_idx, to_idx)


class _SpinnerRing(QWidget):
    _SIZE = 52

    def __init__(self, parent_view):
        # Top-level frameless window (UI_MANIFEST.md Pattern A), same as
        # _ArtistLoadingOverlay: the queue track list is now a
        # createWindowContainer-backed QML view, whose native window always
        # paints above regular sibling QWidgets regardless of z-order/
        # raise_() — and, it turns out, can also still win against a sibling
        # promoted to WA_NativeWindow (Pattern B) depending on platform/
        # timing. A genuine top-level WA_AlwaysStackOnTop window sits above
        # it unconditionally, and (unlike a native *child* window) supports
        # real translucency, so the queue list stays visible all around the
        # small spinner instead of needing an opaque fill.
        super().__init__(None,
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint |
            Qt.WindowType.WindowDoesNotAcceptFocus)
        self._parent_view = parent_view
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._angle = 0
        self._color = QColor('#cccccc')
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.hide()

    def set_color(self, color: str):
        self._color = QColor(color)
        if self.isVisible():
            self.update()

    def sync_geometry(self):
        # Centered over the host panel, in global coordinates since this is
        # a top-level window.
        view = self._parent_view
        center = view.mapToGlobal(QPoint(view.width() // 2, view.height() // 2))
        s = self._SIZE
        self.setGeometry(center.x() - s // 2, center.y() - s // 2, s, s)

    def start(self):
        self._angle = 0
        self.sync_geometry()
        self._timer.start()
        self.show()
        self.raise_()
        self._parent_view.window().installEventFilter(self)

    def stop(self):
        self._timer.stop()
        self.hide()
        self._parent_view.window().removeEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._parent_view.window() and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.sync_geometry()
        return False

    def _tick(self):
        self._angle = (self._angle + 5) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = 5
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        pen = QPen(QColor(255, 255, 255, 35), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(rect)
        arc_color = QColor(self._color)
        arc_color.setAlpha(210)
        pen.setColor(arc_color)
        p.setPen(pen)
        p.drawArc(rect, int(-self._angle * 16), int(100 * 16))
        p.end()


# ── Main panel widget ─────────────────────────────────────────────────────────

class QueuePanel(QWidget):
    play_index       = pyqtSignal(int)
    play_next_index  = pyqtSignal(int)
    remove_index     = pyqtSignal(int)
    close_requested  = pyqtSignal()
    artist_clicked   = pyqtSignal(str)
    favorite_toggled = pyqtSignal(int)   # playlist index
    reordered        = pyqtSignal(list, int)  # new track list, new current index
    start_radio      = pyqtSignal(dict)
    clear_queue      = pyqtSignal()

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
        self._pending_refresh  = None  # (playlist_data, current_index, is_playing) deferred while hidden
        self._pending_load     = None  # (artist_id, artist_name) deferred while hidden or info tab inactive
        self._info_load_timer  = QTimer(self)
        self._info_load_timer.setSingleShot(True)
        self._info_load_timer.setInterval(350)
        self._info_load_timer.timeout.connect(self._do_load_artist_info)
        self._pending_lyrics   = None  # track dict deferred while hidden or lyrics tab inactive
        self._lyrics_load_timer = QTimer(self)
        self._lyrics_load_timer.setSingleShot(True)
        self._lyrics_load_timer.setInterval(400)
        self._lyrics_load_timer.timeout.connect(self._do_load_lyrics)
        self._is_playing = False
        self._tracks      = []   # full track dicts, parallel to self._model's trimmed rows

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
        self._root_layout = root

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
        header.setObjectName('QueueHeader')
        header.setFixedHeight(62)
        header.setStyleSheet('#QueueHeader { background: transparent; }')
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
        hbox.addSpacing(6)

        self._duration_lbl = QLabel('')
        self._duration_lbl.setStyleSheet(
            'color: #555; font-size: 12px; background: transparent; border: none;'
        )
        hbox.addWidget(self._duration_lbl)
        hbox.addStretch()

        self._clear_btn = _HeaderIconButton(resource_path('img/trash.png'), tooltip='Clear Queue')
        self._clear_btn.clicked.connect(self.clear_queue)
        hbox.addWidget(self._clear_btn)

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

        # ── Content stack (list / lyrics / artist info) ───────────────────────
        from player.components.artist_info_panel import ArtistInfoPanel
        from player.panels.right.lyrics_panel import LyricsPanel
        self._artist_info_panel = ArtistInfoPanel()
        self._artist_info_panel.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._artist_info_panel.hide()
        self._lyrics_panel = LyricsPanel()
        self._lyrics_panel.hide()
        self._lyrics_panel.seek_requested.connect(self._on_lyrics_seek)
        self._navidrome_client = None   # set from outside via set_client()

        # Track list — QML-hosted (see UI_MANIFEST.md: QMLGridWrapper + QQuickView
        # for real-refresh-rate scrolling, instead of the old QListWidget+delegate).
        self._model  = QueueTrackModel(self)
        self._bridge = QueueBridge(self)

        self._qml = QMLGridWrapper()
        self._qml.setClearColor(QColor(14, 14, 14, 245))
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._qml.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Keep a strong Python reference — addImageProvider doesn't, and a
        # GC'd provider makes the engine fall back to a no-op requestImage().
        self._icon_provider = AlbumIconProvider()
        engine = self._qml.engine()
        engine.addImageProvider("queueicons", self._icon_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("queueModel", self._model)
        ctx.setContextProperty("queueBridge", self._bridge)
        self._qml.setSource(QUrl.fromLocalFile(resource_path("player/panels/right/queue_list.qml")))

        col.addWidget(self._qml)
        col.addWidget(self._lyrics_panel)
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
        self.btn_lyrics.toggled.connect(lambda on: self._show_tab('lyrics') if on else None)
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

        # Centered overlay spinner for radio loading — top-level always-on-
        # top window (see _SpinnerRing), since the QML track list's native
        # window always paints above regular/promoted sibling QWidgets.
        self._spinner = _SpinnerRing(self)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_client(self, client):
        self._navidrome_client = client
        self._lyrics_panel.set_client(client)

    def set_radio_loading(self, loading: bool):
        if loading:
            self._spinner.start()
        else:
            self._spinner.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The spinner's own eventFilter only catches the *top-level* window
        # moving/resizing — this panel can also resize on its own (drag
        # handles) without that, so re-sync here too.
        if self._spinner.isVisible():
            self._spinner.sync_geometry()

    def load_track(self, artist_id: str, artist_name: str):
        # Wipe stale content immediately if the artist has changed
        current_str = getattr(self._artist_info_panel, '_raw_artist_str', None)
        if artist_name != current_str:
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
        self._qml.setVisible(tab == 'queue')
        self._lyrics_panel.setVisible(tab == 'lyrics')
        self._artist_info_panel.setVisible(tab == 'info')
        if tab == 'info' and self._pending_load is not None:
            self._info_load_timer.start()
        if tab == 'lyrics' and self._pending_lyrics is not None:
            self._lyrics_load_timer.start()

    def _on_lyrics_seek(self, seconds: float):
        w = self.window()
        if hasattr(w, 'audio_engine'):
            w.audio_engine.seek(int(seconds * 1000))

    def queue_lyrics_load(self, track: dict):
        """Called on every track change. Defers actual fetch until lyrics tab is open."""
        self._lyrics_load_timer.stop()
        self._pending_lyrics = track
        if self.isVisible() and self.btn_lyrics.isChecked():
            self._lyrics_load_timer.start()

    def _do_load_lyrics(self):
        if self._pending_lyrics is None:
            return
        track, self._pending_lyrics = self._pending_lyrics, None
        self._lyrics_panel.load_track(track)

    def update_lyrics_position(self, pos_ms: int):
        self._lyrics_panel.update_position(pos_ms)

    def set_accent_color(self, color: str):
        self._accent_color = color
        self._spinner.set_color(color)
        for _btn in (self.btn_queue, self.btn_lyrics, self.btn_info):
            _btn.set_accent(color)
        self._artist_info_panel.set_accent_color(color)
        self._bridge.accentColorChanged.emit(color)
        self._bridge.hoverColorChanged.emit(resolve_menu_hover(getattr(self.window(), 'theme', None)))
        _sec_px = getattr(self, '_secondary_px', 12)
        self._duration_lbl.setStyleSheet(
            f'color: {color}; font-size: {_sec_px}px; background: transparent; border: none;'
        )

    def apply_theme(self, theme):
        _offset               = getattr(theme, 'queue_font_size_offset', 0)
        self._primary_px      = getattr(theme, 'font_size_primary',    14) + _offset
        self._primary_color   = getattr(theme, 'font_color_primary',   '#dddddd')
        self._secondary_px    = getattr(theme, 'font_size_secondary',  12) + _offset
        self._secondary_color = getattr(theme, 'font_color_secondary', '#777777')
        self._queue_lbl.setStyleSheet(
            f'color: {self._primary_color}; font-weight: bold; font-size: {self._primary_px}px; background: transparent; border: none;'
        )
        self._position_lbl.setStyleSheet(
            f'color: {self._secondary_color}; font-size: {self._secondary_px}px; background: transparent; border: none;'
        )
        self._artist_info_panel.apply_theme(theme)
        self._lyrics_panel.apply_theme(theme)
        self._bridge.fontColorPrimaryChanged.emit(self._primary_color)
        self._bridge.fontColorSecondaryChanged.emit(self._secondary_color)
        self._bridge.fontSizePrimaryChanged.emit(self._primary_px)
        self._bridge.fontSizeSecondaryChanged.emit(self._secondary_px)
        self._bridge.hoverColorChanged.emit(resolve_menu_hover(theme))

        # The queue's own background comes from the theme's queue panel
        # color, same as #QueuePanel's own stylesheet (player/mixins/visuals.py,
        # refresh_ui_styles) — pushed to the QML root's panelBgColor property
        # (same pattern as TrackListView.qml) rather than re-touching the
        # native QQuickView's clear color after construction.
        bg_rgb = getattr(theme, 'queue_panel_bg', '14,14,14')
        try:
            r, g, b = (int(v) for v in bg_rgb.split(','))
        except (ValueError, AttributeError):
            r, g, b = 14, 14, 14
        bg_color = QColor(r, g, b)
        self._bridge.panelBgChanged.emit(bg_color.name())

        # #QueuePanel's own border-left (set on this widget's stylesheet by
        # player/mixins/visuals.py's refresh_ui_styles) gets silently covered
        # by the QML track list's native child window, which always wins the
        # z-order fight at the very edge it touches (see LeftPanel.apply_theme
        # for the same gotcha/fix). Reserve border_width px on the left so the
        # native surface doesn't extend over those border pixels.
        bw = getattr(theme, 'border_width', 0)
        self._root_layout.setContentsMargins(bw, 0, 0, 0)

    @staticmethod
    def _is_starred(raw) -> bool:
        """Subsonic returns 'starred' as a timestamp string (e.g.
        "2024-06-01T12:00:00.000Z") when favorited, '' when not — only a
        manual toggle here ever produces a literal 'true'/'1'/bool. Treat
        any non-empty, non-'false' string as favorited so tracks loaded
        into the queue (which carry the raw timestamp) show correctly."""
        if isinstance(raw, str):
            return raw.strip().lower() not in ('', 'false', '0', 'none')
        return bool(raw)

    def toggle_favorite_at(self, idx: int):
        if 0 <= idx < len(self._tracks):
            current = self._is_starred(self._tracks[idx].get('starred', False))
            self._tracks[idx]['starred'] = not current
            self._model.set_favorite(idx, not current)

    def update_playing_state(self, is_playing: bool):
        self._is_playing = is_playing
        self._bridge.isPlayingChanged.emit(is_playing)

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_refresh is not None:
            args, self._pending_refresh = self._pending_refresh, None
            self.refresh(*args)
        if self.btn_info.isChecked() and self._pending_load is not None:
            self._info_load_timer.start()
        if self.btn_lyrics.isChecked() and self._pending_lyrics is not None:
            self._lyrics_load_timer.start()

    @staticmethod
    def _format_duration(raw_dur) -> str:
        try:
            secs = int(float(raw_dur))
            return f"{secs // 60}:{secs % 60:02d}"
        except (ValueError, TypeError):
            return str(raw_dur or '')

    def refresh(self, playlist_data: list, current_index: int, is_playing: bool = False):
        if not self.isVisible():
            self._pending_refresh = (playlist_data, current_index, is_playing)
            return
        self._is_playing = is_playing
        # Shallow-copy each track dict — window.py's favorite/reorder handlers
        # mutate self.playlist_data[idx] independently of our own bookkeeping
        # (toggle_favorite_at, drag-reorder). Sharing dict identity with
        # playlist_data here caused a double-toggle: toggle_favorite_at would
        # flip 'starred' on the *same* dict window.py's _queue_toggle_favorite
        # then reads and flips again, sending the server the wrong state.
        self._tracks = [dict(t) for t in playlist_data]
        self._current_index = current_index

        rows = []
        for track in playlist_data:
            is_fav = self._is_starred(track.get('starred', False))
            rows.append({
                'id':       str(track.get('id', '')),
                'title':    track.get('title', track.get('name', 'Unknown')),
                'artist':   track.get('artist', ''),
                'duration': self._format_duration(track.get('duration', '')),
                'fav':      is_fav,
            })
        self._model.set_rows(rows)
        self._bridge.currentIndexChanged.emit(current_index)
        self._bridge.isPlayingChanged.emit(is_playing)
        if 0 <= current_index < len(rows):
            self._bridge.scrollToIndexRequested.emit(current_index)

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

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_reorder_track(self, from_idx: int, to_idx: int):
        if not (0 <= from_idx < len(self._tracks)):
            return
        current_track = (self._tracks[self._current_index]
                          if 0 <= getattr(self, '_current_index', -1) < len(self._tracks) else None)
        # Mirror window.py's reordered-signal expectation: dragging a row to
        # position `to_idx` (a ListView model-row target, i.e. the slot the
        # row lands in) means it ends up at index to_idx-1 once removed from
        # its old slot, when moving forward; unchanged when moving backward.
        target = to_idx - 1 if to_idx > from_idx else to_idx
        target = max(0, min(target, len(self._tracks) - 1))
        track = self._tracks.pop(from_idx)
        self._tracks.insert(target, track)
        new_current = -1
        if current_track is not None:
            new_current = next((i for i, t in enumerate(self._tracks) if t is current_track), -1)
        self.refresh(self._tracks, new_current, self._is_playing)
        self.reordered.emit(list(self._tracks), new_current)

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

    def _show_context_menu_at(self, idx: int, global_pos: QPoint):
        if not (0 <= idx < len(self._tracks)):
            return
        track = {k: v for k, v in self._tracks[idx].items() if not k.startswith('_')}
        main  = self.window()

        from player.widgets import ShadowContextMenu
        _theme = getattr(main, 'theme', None)
        bg  = getattr(_theme, 'main_panel_bg',        '14,14,14') if _theme else '14,14,14'
        bc  = getattr(_theme, 'border_color',          '#2a2a2a') if _theme else '#2a2a2a'
        fg  = getattr(_theme, 'font_color_primary',    '#dddddd') if _theme else '#dddddd'
        fg2 = getattr(_theme, 'font_color_secondary',  '#555555') if _theme else '#555555'
        px  = getattr(_theme, 'font_size_primary',     14)        if _theme else 14
        acc = getattr(_theme, 'accent',                '#cccccc') if _theme else '#cccccc'
        if _theme and not getattr(_theme, 'auto_border_from_accent', True):
            bc = getattr(_theme, 'manual_border_color', '#2a2a2a')
        hov = resolve_menu_hover(_theme)

        menu = ShadowContextMenu(self)
        menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)

        track_id   = str(track.get('id', ''))
        artist     = track.get('artist', '')
        album_id   = track.get('albumId') or track.get('parent')
        album_data = {'id': album_id, 'title': track.get('album', ''),
                      'artist': artist, 'coverArt': track.get('coverArt', '')}

        menu.add_action('Play Now',     lambda: self.play_index.emit(idx),      icon_path='img/sub_play.png')
        menu.add_action('Play Next',    lambda: self.play_next_index.emit(idx), icon_path='img/sub_next.png')
        menu.add_action('Go to Artist', lambda: self.artist_clicked.emit(artist) if artist else None,
                        enabled=bool(artist), icon_path='img/sub_artist.png')
        menu.add_action('Open Album',   lambda: main.navigate_to_album(album_data) if album_id and main else None,
                        enabled=bool(album_id and main), icon_path='img/album.png')
        menu.add_action('Start Radio',  lambda: self.start_radio.emit(track), icon_path='img/radio.png')

        playlists = (main.playlists_browser.all_playlists or []) if main and hasattr(main, 'playlists_browser') else []
        if track_id:
            pl_items = [('New Playlist…', lambda: self._add_to_new_playlist(main, [track_id]), 'img/add.png')]
            pl_items += [(f"{pl.get('name','Unnamed')}  ({pl.get('songCount','')})" if pl.get('songCount','') != '' else pl.get('name','Unnamed'),
                          lambda pid=pl.get('id'), pn=pl.get('name',''): self._add_to_existing_playlist(main, pid, pn, [track_id]),
                          'img/playlist.png')
                         for pl in playlists if pl.get('id')]
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')

        tb = getattr(main, 'tracks_browser', None) if main else None
        menu.add_action('Get Info', callback=(lambda: tb._show_track_info(track)) if tb else None,
                        enabled=bool(tb), icon_path='img/info.png')

        is_fav = self._is_starred(track.get('starred', False))
        menu.add_action('Remove from Favorites' if is_fav else 'Add to Favorites',
                        lambda: (self.toggle_favorite_at(idx), self.favorite_toggled.emit(idx)),
                        color='#E91E63',
                        icon_path='img/heart_filled.png' if is_fav else 'img/heart.png')

        menu.add_action('Remove from Queue', lambda: self.remove_index.emit(idx), icon_path='img/remove.png')

        menu.exec_at(QPoint(global_pos.x() - menu._PAD, global_pos.y() - menu._PAD), window=main)

    def _add_to_new_playlist(self, main, track_ids):
        client = getattr(main, 'navidrome_client', None)
        if not client:
            return
        from player.components.shared_widgets import NewPlaylistDialog
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
