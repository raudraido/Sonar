"""
player/window.py — IcosahedronPlayer main window.

IcosahedronPlayer composes all behaviour from five focused mixins.
Only __init__ and init_ui live here; everything else is in
player/mixins/*.py.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidgetItem, QSlider, QPushButton, QFileDialog, QHeaderView,
    QAbstractItemView, QStyledItemDelegate, QColorDialog, QMenu,
    QStyle, QCheckBox, QToolTip, QGraphicsColorizeEffect, QLineEdit,
    QGraphicsOpacityEffect, QTabWidget, QTabBar, QStackedWidget,
    QStylePainter, QStyleOptionTab, QProxyStyle, QListWidgetItem, QSizePolicy,
    QProgressBar, QDialog, QMessageBox, QComboBox, QApplication, QSplitter
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QThread, pyqtSignal, QPropertyAnimation,
    QUrl, QPoint, QPointF, QItemSelectionModel, QRect, QEvent,
    QRectF, QSettings, QEasingCurve
)
from PyQt6.QtGui import (
    QPixmap, QImage, QColor, QMouseEvent, QAction, QIcon,
    QFontMetrics, QCursor, QPainter,
    QPolygon, QFont, QPen, QBrush, QPainterPath, QPixmapCache,
    QMovie
)

import os
import sys
import json
import threading
from version import __version__
from visualizer import AudioVisualizer
from audio_engine import AudioEngine
from subsonic_client import SubsonicClient
from albums_browser import LibraryGridBrowser
from artists_browser import ArtistGridBrowser
from now_playing import PlaylistTree, NowPlayingPanel, COL_LENGTH, COL_TITLE, COL_ALBUM
from now_playing_info import NowPlayingInfoTab
from home import HomeView
from tracks_browser import TracksBrowser
from spotlight_search import SpotlightSearch
from login_dialog import LoginDialog
from playlists_browser import PlaylistsBrowser
from waveform_scrubber import WaveformScrubber

from player.workers import (
    BPMWorker, SyncCheckWorker, PlaybackManager, BlurWorker,
    MetadataWorker, PlaylistCoverWorker, PlaylistLoaderWorker,
    CoverLoaderWorker, CrossPlatformMediaKeyListener,
)
from player.widgets import (
    ElidedLabel, NowPlayingFooterWidget, FooterClickableLabel,
    TriangleTooltip, ClickableSlider,
    SettingsWindow, StatusButton, SquareArtContainer, PlayButton,
)
from player import resource_path
from player.theme import Theme
from player.mixins.playback    import PlaybackMixin
from player.mixins.navigation  import NavigationMixin
from player.mixins.visuals     import VisualsMixin
from player.mixins.keyboard    import KeyboardMixin
from player.mixins.persistence import PersistenceMixin
from queue_panel import QueuePanel
from left_panel import LeftPanel
from PyQt6.QtCore import QObject as _QObject, QEvent as _QEvent2


class _NoBaseStyle(QProxyStyle):
    """Proxy style that suppresses the PE_FrameTabBarBase baseline."""
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_FrameTabBarBase:
            return
        super().drawPrimitive(element, option, painter, widget)


class _TabBar(QTabBar):
    """QTabBar that skips CE_TabBarBase so no gray baseline is drawn."""

    _accent: str = '#888888'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlays:     dict  = {}   # {tab_index: QLabel}
        self._stored_icons: dict  = {}   # {tab_index: QIcon} — cleared from bar in icon-only mode
        self._active_hover: QColor = QColor(204, 204, 204, 45)
        self._bg_color:     QColor = QColor(14, 14, 14)

    def set_master_color(self, color: str):
        self._accent = color
        self.update()

    def set_active_hover(self, color: QColor):
        self._active_hover = color
        self.update()

    def set_bg_color(self, color: QColor):
        self._bg_color = color
        self.update()

    def _on_icons_tab_moved(self, from_idx: int, to_idx: int):
        """Keep _stored_icons indices in sync when tabs are reordered."""
        if not self._stored_icons:
            return
        icons = [self._stored_icons.get(i) for i in range(self.count())]
        icon = icons.pop(from_idx)
        icons.insert(to_idx, icon)
        self._stored_icons = {i: ic for i, ic in enumerate(icons) if ic is not None}

    def tabSizeHint(self, index: int):
        hint = super().tabSizeHint(index)
        if not self.tabText(index):
            s = hint.height()
            return QSize(s, s)
        return hint

    def _sync_overlays(self):
        from PyQt6.QtWidgets import QLabel as _QL
        active: set = set()
        sz = self.iconSize()
        for i, icon in self._stored_icons.items():
            if not self.tabText(i):
                active.add(i)
                if i not in self._overlays:
                    lbl = _QL(self)
                    lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    lbl.setStyleSheet('background: transparent;')
                    self._overlays[i] = lbl
                lbl = self._overlays[i]
                r        = self.tabRect(i)
                visual_w = r.width() - 4
                x = r.x() + (visual_w - sz.width())  // 2
                y = r.y() + (r.height() - sz.height()) // 2
                lbl.setGeometry(x, y, sz.width(), sz.height())
                lbl.setPixmap(icon.pixmap(sz))
                lbl.show()
                lbl.raise_()
        for i, lbl in self._overlays.items():
            if i not in active:
                lbl.hide()

    def paintEvent(self, event):
        # Let Qt handle all tabs including drag — no duplication
        super().paintEvent(event)

        sel = self.currentIndex()
        if sel >= 0 and self.isTabVisible(sel):
            # Redraw selected tab WITHOUT State_Selected so Fusion skips its
            # selection indicator (underline), then add our own accent halo.
            sp = QStylePainter(self)
            opt = QStyleOptionTab()
            self.initStyleOption(opt, sel)
            opt.state &= ~(QStyle.StateFlag.State_Selected |
                           QStyle.StateFlag.State_MouseOver)
            if not opt.text and not opt.icon.isNull():
                opt.icon = QIcon()
                sp.drawControl(QStyle.ControlElement.CE_TabBarTabShape, opt)
            else:
                sp.drawControl(QStyle.ControlElement.CE_TabBarTabShape, opt)
                self.initStyleOption(opt, sel)
                sp.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, opt)

            # Accent halo
            r = self.tabRect(sel)
            sp.setRenderHint(QPainter.RenderHint.Antialiasing)
            sp.setPen(Qt.PenStyle.NoPen)
            sp.setBrush(self._active_hover)
            sp.drawRoundedRect(r.x(), r.y(), r.width() - 4, r.height(), 6, 6)

        self._sync_overlays()


class _TabsCompat(_QObject):
    """QTabBar + QStackedWidget drop-in for the QTabWidget API we use.
    tab_bar lives inside main_header; tab_stack sits below it in right_panel."""
    currentChanged = pyqtSignal(int)
    tabBarClicked  = pyqtSignal(int)

    def __init__(self, tab_bar: QTabBar, tab_stack: QStackedWidget):
        super().__init__()
        self._bar   = tab_bar
        self._stack = tab_stack
        tab_bar.currentChanged.connect(self._on_current_changed)
        tab_bar.tabBarClicked.connect(self.tabBarClicked)
        tab_bar.tabMoved.connect(self._sync_stack_move)

    def _on_current_changed(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self.currentChanged.emit(idx)

    def _sync_stack_move(self, from_idx: int, to_idx: int):
        widget = self._stack.widget(from_idx)
        self._stack.removeWidget(widget)
        self._stack.insertWidget(to_idx, widget)
        self._stack.setCurrentIndex(self._bar.currentIndex())

    # ── QTabWidget-compatible API ─────────────────────────────────────────────

    def addTab(self, widget, label: str, icon=None) -> int:
        if icon is not None:
            idx = self._bar.addTab(icon, label)
        else:
            idx = self._bar.addTab(label)
        self._stack.addWidget(widget)
        return idx

    def tabBar(self):              return self._bar
    def currentWidget(self):       return self._stack.currentWidget()
    def currentIndex(self) -> int: return self._bar.currentIndex()
    def count(self) -> int:        return self._bar.count()

    def setCurrentIndex(self, idx: int):
        self._bar.setCurrentIndex(idx)

    def indexOf(self, widget) -> int:
        for i in range(self._stack.count()):
            if self._stack.widget(i) is widget:
                return i
        return -1

    def widget(self, idx: int):
        return self._stack.widget(idx)

    def setFocusPolicy(self, policy): self._bar.setFocusPolicy(policy)
    def setElideMode(self, mode):     self._bar.setElideMode(mode)
    def setObjectName(self, name):    self._bar.setObjectName(name)
    def setStyleSheet(self, css):     self._bar.setStyleSheet(css)
    def setCornerWidget(self, *_):    pass  # nav buttons are in main_header layout
from PyQt6.QtWidgets import QFrame as _QFrame, QLabel as _QLabelTT
from PyQt6.QtCore import Qt as _Qt2
from PyQt6.QtGui import QPainter as _QPainter, QColor as _QColor


class _TooltipLabel(_QFrame):
    """Custom tooltip popup with painted shadow + text."""
    _PAD = 12

    def __init__(self):
        super().__init__(None, _Qt2.WindowType.ToolTip | _Qt2.WindowType.FramelessWindowHint |
                         _Qt2.WindowType.WindowStaysOnTopHint | _Qt2.WindowType.NoDropShadowWindowHint)
        self.setAttribute(_Qt2.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(_Qt2.WidgetAttribute.WA_TranslucentBackground)
        self._text  = ''
        self._fg    = '#999999'
        self._px    = 14
        self._bg    = (20, 20, 20)
        self._bc    = '#2a2a2a'

    @staticmethod
    def _get_theme():
        from PyQt6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            t = getattr(w, 'theme', None)
            if t: return t
        return None

    def paintEvent(self, _):
        from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
        from PyQt6.QtCore import QRectF, Qt as _Qt3
        r, g, b = self._bg
        bc = QColor(self._bc)
        pad = self._PAD
        content = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(_Qt3.PenStyle.NoPen)
        for i in range(10, 0, -1):
            t2 = i / 10; ex = 10 * t2
            p.setBrush(QColor(0, 0, 0, int(80 * (1 - t2) ** 2)))
            p.drawRoundedRect(content.adjusted(-ex*.6, -ex*.3+5*(1-t2), ex*.6, ex+5*t2), 6, 6)
        p.setBrush(QColor(r, g, b))
        p.setPen(QPen(bc, 1))
        p.drawRoundedRect(content, 6, 6)
        f = QFont(); f.setPixelSize(self._px)
        p.setFont(f)
        p.setPen(QColor(self._fg))
        p.drawText(content.adjusted(10, 5, -10, -5),
                   _Qt3.AlignmentFlag.AlignCenter, self._text)
        p.end()

    def show_at(self, pos, text):
        t = self._get_theme()
        self._fg   = getattr(t, 'font_color_secondary', '#999999') if t else '#999999'
        self._px   = getattr(t, 'font_size_primary',    14)        if t else 14
        bg_str = getattr(t, 'main_panel_bg', '20,20,20') if t else '20,20,20'
        try: self._bg = tuple(int(x) for x in bg_str.split(','))
        except: self._bg = (20, 20, 20)
        if t and getattr(t, 'auto_border_from_accent', True):
            from PyQt6.QtGui import QColor as _QC
            self._bc = _QC(getattr(t, 'accent', '#cccccc')).darker(250).name()
        else:
            self._bc = getattr(t, 'manual_border_color', '#2a2a2a') if t else '#2a2a2a'
        self._text = text
        # Measure text to set fixed size
        from PyQt6.QtGui import QFont, QFontMetrics
        f = QFont(); f.setPixelSize(self._px)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        pad = self._PAD
        self.setFixedSize(tw + pad*2 + 20, th + pad*2 + 10)
        self.move(pos.x(), pos.y())
        super(_TooltipLabel, self).show()
        self.raise_()


class _TooltipFilter(_QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tip = None

    def _ensure_tip(self):
        if self._tip is None:
            self._tip = _TooltipLabel()
        return self._tip

    def eventFilter(self, obj, event):
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtCore import QPoint
        if event.type() == _QEvent2.Type.ToolTip:
            widget = obj if isinstance(obj, QWidget) else None
            text = widget.toolTip() if widget else ''
            if text:
                tip = self._ensure_tip()
                # Position below the widget
                gpos = widget.mapToGlobal(QPoint(0, widget.height() + 4))
                tip.show_at(gpos, text)
                return True
            else:
                if self._tip and self._tip.isVisible():
                    self._tip.hide()
                return False
        if event.type() in (_QEvent2.Type.Leave, _QEvent2.Type.MouseButtonPress,
                            _QEvent2.Type.KeyPress, _QEvent2.Type.WindowDeactivate):
            if self._tip and self._tip.isVisible():
                self._tip.hide()
        return False


class _ResizeHandle(QWidget):
    """Transparent overlay at a panel border — only the drag icon is visible."""

    _DEFAULT_OPACITY = 0.4

    def __init__(self, target: QWidget, default_w: int, sign: int,
                 settings_key: str, settings, reposition_cb, parent=None):
        super().__init__(parent)
        self._target         = target
        self._min_w          = int(default_w * 0.9)
        self._max_w          = int(default_w * 1.1)
        self._sign           = sign
        self._key            = settings_key
        self._settings       = settings
        self._reposition_cb  = reposition_cb
        self._drag_x         = None
        self._drag_w         = None

        self.setFixedWidth(12)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        self._hovered = False
        self._eff  = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(self._DEFAULT_OPACITY)
        self.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b'opacity')
        self._anim.setDuration(180)

    def update_color(self, color: str):
        self._dot_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = getattr(self, '_dot_color', QColor('#ffffff' if self._hovered else '#888888'))
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        cx = self.width() // 2
        mid = self.height() // 2
        for dy in (-5, 0, 5):
            p.drawEllipse(cx - 2, mid + dy - 2, 4, 4)
        p.end()

    def enterEvent(self, event):
        self._hovered = True
        self._anim.stop(); self._anim.setEndValue(1.0); self._anim.start()
        local_y = int(event.position().y())
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._near_dots(local_y) else Qt.CursorShape.ArrowCursor)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._anim.stop(); self._anim.setEndValue(self._DEFAULT_OPACITY); self._anim.start()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def _near_dots(self, y: int) -> bool:
        """True when y is within 20px of the dots centre."""
        return abs(y - self.height() // 2) <= 20

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._near_dots(int(event.position().y())):
            self._drag_x = event.globalPosition().x()
            self._drag_w = self._target.width()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Update cursor based on position
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._near_dots(int(event.position().y()))
                       else Qt.CursorShape.ArrowCursor)
        if self._drag_x is None:
            return
        dx = (event.globalPosition().x() - self._drag_x) * self._sign
        new_w = max(self._min_w, min(self._max_w, int(self._drag_w + dx)))
        self._target.setFixedWidth(new_w)
        QTimer.singleShot(0, self._reposition_cb)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_x is not None:
            self._settings.setValue(self._key, self._target.width())
        self._drag_x = None
        self._drag_w = None
        super().mouseReleaseEvent(event)


class SonarPlayer(
    PlaybackMixin,
    NavigationMixin,
    VisualsMixin,
    KeyboardMixin,
    PersistenceMixin,
    QMainWindow,
):
    _VOL_ICON_SIZE = 30  # px — single source of truth for volume icon size
    """
    Main application window.

    Behaviour is split across five mixins (in player/mixins/).
    This class owns only construction (__init__) and UI layout (init_ui).
    """

    def __init__(self, client):
        super().__init__()
        self.settings = QSettings()
        self.setWindowTitle(f"Icosahedron {__version__}")

        # Restore saved geometry and dark title bar BEFORE show so the window
        # appears in the right place and with the right chrome on the first frame
        _screen = QApplication.primaryScreen()
        _available = _screen.availableGeometry() if _screen else None
        _saved_w = int(self.settings.value('window_width',  0) or 0)
        _saved_h = int(self.settings.value('window_height', 0) or 0)
        if _available and _saved_w > 0 and _saved_h > 0:
            w = min(_saved_w, _available.width())
            h = min(_saved_h, _available.height())
            self.resize(w, h)
            self.move(_available.x() + (_available.width()  - w) // 2,
                      _available.y() + (_available.height() - h) // 2)
        else:
            self.resize(1750, 1070)

        # Install app-level event filters (keep refs to prevent GC)
        self._tooltip_filter = _TooltipFilter(QApplication.instance())
        QApplication.instance().installEventFilter(self._tooltip_filter)
        self.navidrome_client = client
        self.bpm_cache = self.load_bpm_cache()
        self.setAcceptDrops(True)
        self.last_gapless_time = 0
      
        self.preload_timer = QTimer(self)
        self.preload_timer.setSingleShot(True)
        self.preload_timer.setInterval(4000)  # Wait 4 seconds before preloading
        self.preload_timer.timeout.connect(self._execute_preload_now)

        self.visual_update_timer = QTimer(self)
        self.visual_update_timer.setSingleShot(True)
        self.visual_update_timer.setInterval(350)
        self.visual_update_timer.timeout.connect(self._perform_heavy_visual_update)
        
        self.is_slider_moving = False; self.transition_triggered = False
        self.queued_next_index = -1
        self.programmatic_tab_change = False  # Flag to track tab changes
        self._logging_out = False

        self.history = []
        self._shuffle_queue = []
        self.temp_files = []

        # Debounce timers for rapid track clicking
        self._indicator_debounce = QTimer(self)
        self._indicator_debounce.setSingleShot(True)
        self._indicator_debounce.setInterval(60)
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(200)

        self.search_context = {}  
        self.last_tab_index = 0    

        self.last_engine_pos = 0       
        self.last_engine_update_time = 0 
        self.ignore_updates_until = 0    
        
        self.smooth_timer = QTimer(self)
        _screen = QApplication.primaryScreen()
        _hz = _screen.refreshRate() if _screen else 60.0
        self.smooth_timer.setInterval(max(1, round(1000 / _hz)))
        self.smooth_timer.timeout.connect(self.run_smooth_interpolator)
        
        self.audio_engine = AudioEngine()
        self.audio_engine.positionChanged.connect(self.update_ui_state)
        self.audio_engine.durationChanged.connect(self.handle_duration_change)
        self.audio_engine.endOfMedia.connect(self.on_track_finished)
        self.audio_engine.mediaSwitched.connect(self.on_gapless_transition)

        # --- MANAGER SETUP ---
        self.playback_manager = PlaybackManager(self.audio_engine)
        self.playback_manager.track_started.connect(self.on_play_started) 
        self.playback_manager.start()

        # --- THEME (single source of truth for all visual settings) ---
        _saved_theme = self.settings.value('theme')
        if _saved_theme:
            self.theme = Theme.from_json(_saved_theme)
        else:
            # Try to migrate from legacy separate keys; fall back to Cream preset
            try:
                _vis = json.loads(self.settings.value('visual_settings') or '{}')
            except Exception:
                _vis = {}
            _color   = self.settings.value('last_master_color')
            _dynamic = bool(int(self.settings.value('dynamic_color', 1) or 1))
            if _color:
                self.theme = Theme.from_legacy(_vis, _color, _dynamic)
            else:
                from player.theme import load_presets as _lp
                self.theme = _lp().get('Cream', Theme())
        
        self.is_shuffle = False
        self.is_repeat = False
        self.is_muted = False
        self.current_raw_art = None
        self.playlist_data = []
        self.current_index = -1
        self.last_index = -1
        self.next_index = -1
        self.last_volume = 100

        self.init_ui()

        # Show after UI is built so the window appears fully formed in one animation
        try:
            from player.theme import Theme as _Theme
            _saved = self.settings.value('theme')
            _bg = _Theme.from_json(_saved).main_panel_bg if _saved else '14,14,14'
            _r2, _g2, _b2 = (int(x) for x in _bg.split(','))
            _is_dark = (_r2 * 299 + _g2 * 587 + _b2 * 114) / 1000 < 128
        except Exception:
            _is_dark = True
        self.enable_dark_title_bar(_is_dark)
        self._last_title_bar_dark = _is_dark
        self.show()

        self.crossfade_progress = 1.0
        self.blur_thread = None
        self.generic_tooltip = TriangleTooltip(self, show_triangle=False)

        
        try:
            # theme.accent already restored from saved settings above

            # 2. Check if we have a saved track
            
            saved_idx = int(self.settings.value('saved_current_index', -1))
            saved_json = self.settings.value('current_playlist')

            track_for_bg = None
            if saved_json and saved_idx != -1:
                saved_data = json.loads(saved_json)
                if 0 <= saved_idx < len(saved_data):
                    track_for_bg = saved_data[saved_idx]

            started_bg = False
            if track_for_bg:
                # 3a. Try getting the image from the local cache first
                cid = str(track_for_bg.get('cover_id') or track_for_bg.get('coverArt') or track_for_bg.get('albumId') or '')
                if cid:
                    from cover_cache import CoverCache
                    cache = CoverCache.instance()
                    cached_data = cache.get_full(cid) or cache.get_thumb(cid)
                    if cached_data:
                        self.update_background_threaded(None, raw_data_override=cached_data)
                        started_bg = True
                
                # 3b. If no cache, but it's a local file, read the file
                if not started_bg and track_for_bg.get('path'):
                    self.update_background_threaded(track_for_bg['path'])
                    started_bg = True

            # 4. No fallback needed — window is already dark from default styles

        except Exception as e:
            print(f"Startup restore error: {e}")

        # Continue with standard init
        self.refresh_ui_styles()
        self.audio_engine.set_volume(self.last_volume)
        QTimer.singleShot(0, self.load_playlist)

        # Start home tab immediately — don't wait for ping
        if self.navidrome_client:
            QTimer.singleShot(0, self._early_home_init)
        QTimer.singleShot(100, self.test_navidrome_fetch)
        QTimer.singleShot(0, self.reposition_nav_buttons)
        self._indicator_debounce.timeout.connect(lambda: self.update_indicator(scroll_to_current=True))
        self._refresh_debounce.timeout.connect(self._deferred_song_refresh)
        # --- Background downloader for playlist covers ---
        self.playlist_cover_worker = PlaylistCoverWorker(None)
        self.playlist_cover_worker.cover_downloaded.connect(self.tree.viewport().update)

        # Cast manager initialized lazily on first cast button click
        
        # --- Initialize the Spotlight Search Overlay (No DB passed)
        self.spotlight = SpotlightSearch(self, None)
        self.spotlight.view_requested.connect(self.handle_spotlight_view)
        
        
        self.spotlight.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        self.spotlight.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        
        # 1. Connect single tracks
        self.spotlight.play_requested.connect(self.play_track_from_data)

        # 2. Connect entire artists and albums
        self.spotlight.play_multiple_requested.connect(self.play_whole_album)


        # --- evdev Media Key Listener (Linux, direct kernel input) ---
        self.media_key_listener = CrossPlatformMediaKeyListener()
        self.media_key_listener.sig_play_pause.connect(self.toggle_playback)
        self.media_key_listener.sig_stop.connect(self._media_stop)
        self.media_key_listener.sig_next.connect(self.play_next)
        self.media_key_listener.sig_prev.connect(self.play_prev)
        self.media_key_listener.start()

        # --- Install a global application filter to intercept shortcuts
        QApplication.instance().installEventFilter(self)

        
        QPixmapCache.setCacheLimit(20 * 1024)

        self.ram_timer = QTimer()
        self.ram_timer.timeout.connect(self.print_ram_usage)
        self.ram_timer.start(30_000)  # 30s — was 5s; gc.collect on a big heap every 5s is expensive

        # Debounce timer for resizeEvent — avoids SmoothTransformation on every resize pixel
        self._resize_debounce = QTimer(self)
        self._resize_debounce.setSingleShot(True)
        self._resize_debounce.setInterval(120)

        # Pre-load the QMovie once — the GIF decoder is the expensive part.
        # QLabel and QGraphicsColorizeEffect are recreated per-call in update_indicator()
        # because Qt takes ownership of any widget passed to setItemWidget() and deletes
        # it (along with its child effect) when the item is removed or the tree is cleared,
        # which would leave _pi_effect as a dangling C++ pointer on the next track change.
        from PyQt6.QtGui import QMovie as _QMovie
        self._pi_movie = _QMovie(resource_path("img/playing.gif"))
        self._pi_movie.setScaledSize(QSize(40, 40))

        # --- END of INIT ---

    def _reposition_resize_handles(self):
        cw = self.centralWidget()
        if cw is None:
            return
        for handle, panel, edge in (
            (self._left_handle,  self._left_panel,             'right'),
            (self._queue_handle, self._queue_panel_container,  'left'),
        ):
            if handle is None or panel is None:
                continue
            panel_pos = panel.mapTo(cw, QPoint(panel.width() if edge == 'right' else 0, 0))
            x = panel_pos.x() - handle.width() // 2
            y = panel.mapTo(cw, QPoint(0, 0)).y()
            handle.setGeometry(x, y, handle.width(), panel.height())
            handle.raise_()

        # Keep sidebar art square as left panel is resized
        if getattr(self, '_sidebar_art_visible', False):
            new_h = max(0, self._left_panel.width() - 16)
            self._art_section.setMaximumHeight(new_h)
            self._art_section.setMinimumHeight(new_h)
            if hasattr(self, '_art_close_btn') and self._art_close_btn.isVisible():
                self._art_close_btn.move(self._art_section.width() - 28, 4)

    def _update_tab_mode(self):
        if not hasattr(self, 'main_header'):
            return
        bar = self.tab_bar

        # Measure natural width with all labels + icons restored
        bar.setIconSize(QSize(16, 16))
        for i in range(bar.count()):
            label = bar.tabData(i)
            if label:
                bar.setTabText(i, label)
                if i in bar._stored_icons:
                    bar.setTabIcon(i, bar._stored_icons[i])

        needed = bar.sizeHint().width()
        icon_only = needed > self.main_header.width()

        if icon_only:
            bar.setIconSize(QSize(20, 20))
            for i in range(bar.count()):
                label = bar.tabData(i)
                if label:
                    # Store icon and clear it — overlay will draw it centred
                    icon = bar.tabIcon(i)
                    if not icon.isNull():
                        bar._stored_icons[i] = icon
                    bar.setTabIcon(i, QIcon())
                    bar.setTabText(i, '')
        else:
            bar._stored_icons.clear()
            bar.setIconSize(QSize(16, 16))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reposition_resize_handles)
        QTimer.singleShot(0, self._update_tab_mode)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)

            on_vis_tab = (hasattr(self, '_vis_container') and
                          self.tabs.currentWidget() is self._vis_container)
            vis_active = not minimized and on_vis_tab
            if hasattr(self, 'audio_engine'):
                self.audio_engine.set_visualizer_active(vis_active)
            if getattr(self, 'visualizer', None):
                self.visualizer.visualizer_enabled = vis_active

            if hasattr(self, 'smooth_timer'):
                if minimized:
                    self.smooth_timer.stop()
                elif getattr(self.audio_engine, 'is_playing', False):
                    self.smooth_timer.start()

            if hasattr(self, 'seek_bar'):
                sb = self.seek_bar
                if minimized:
                    sb.render_timer.stop()
                elif getattr(sb, 'display_mode', 1) == 0:
                    sb.render_timer.start()

            if hasattr(self, 'playing_movie'):
                if minimized:
                    self.playing_movie.stop()
                    if hasattr(self, '_pi_movie'):
                        self._pi_movie.stop()
                elif getattr(self.audio_engine, 'is_playing', False):
                    self.playing_movie.start()
                    if hasattr(self, '_pi_movie'):
                        self._pi_movie.start()

        super().changeEvent(event)

    def init_ui(self):
                
        # Initialize the animated playing indicator!
        self.playing_indicator_label = QLabel()
        self.playing_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playing_indicator_label.setStyleSheet("background: transparent;")
        
        self.playing_movie = QMovie(resource_path("img/playing.gif"))
        self.playing_movie.setScaledSize(QSize(30, 30)) # Perfect size for the row
        self.playing_indicator_label.setMovie(self.playing_movie)
        
        self.nav_history = []
        self.nav_index = -1
        self.programmatic_nav = False
        
        self.setup_global_navigation()
        
        # --- Background Setup ---
        self.ghost_label = QLabel(self)
        self.ghost_label.hide()
        self.ghost_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ghost_label.setStyleSheet("background: transparent;")
        
        
        central_widget = QWidget()
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0) #TAB window top margin
        main_layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0) # Space between Queue and main panel

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0) # Space between left panel and main panel

        self._splitter = None

        # --- LEFT PANEL ---
        self._left_panel = LeftPanel(self, self.audio_engine, self.settings)
        self.art_container = self._left_panel.art_container
        self._art_section  = self._left_panel.art_section
        self._sidebar_art_anim = self._left_panel.sidebar_art_anim
        self._sidebar_art_visible = False
        self._info_section = None  # removed from left panel

        # track_title / track_artist / file_type_label / heart_btn exist as detached
        # widgets so all mixin code that references them keeps working — they just
        # don't appear in the left panel anymore.
        self.track_title = QLabel("")
        self.track_artist = QLabel("")
        self.file_type_label = QLabel("")
        self.heart_btn = QPushButton()
        self.heart_btn.setFlat(True)
        self.heart_btn.setIconSize(QSize(18, 18))
        self.heart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.heart_btn.setStyleSheet("background: transparent; border: none;")
        self.heart_btn.clicked.connect(self._toggle_now_playing_favorite)
        
        # --- RIGHT PANEL (Tabs & Search) ---

        self.tab_bar = _TabBar()
        self.tab_bar.setStyle(_NoBaseStyle(self.tab_bar.style()))
        self.tab_bar.tabMoved.connect(self.tab_bar._on_icons_tab_moved)
        self.tab_bar.setObjectName('TabsPanel')
        self.tab_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_bar.setExpanding(False)
        self.tab_bar.currentChanged.connect(self.tab_bar.update)

        self.tab_stack = QStackedWidget()
        self.tab_stack.setObjectName('TabStack')

        self.tabs = _TabsCompat(self.tab_bar, self.tab_stack)

        self.btn_back.setToolTip("Go Back")
        self.btn_fwd.setToolTip("Go Forward")
        
        # 1. Home
        self.home_tab = HomeView(None) 
        self.home_tab.play_album.connect(self.play_whole_album)
        self.tabs.addTab(self.home_tab, "Home")
        self.home_tab.artist_clicked.connect(self.navigate_to_artist)

        # 2. Now Playing queue tree — kept hidden; self.tree.* refs still work
        self._queue_tree_panel = NowPlayingPanel(self)
        self.tree = self._queue_tree_panel.tree
        self.tree.sig_drag_started.connect(self.show_ghost_drag)
        self.tree.sig_drag_moved.connect(self.move_ghost_drag)
        self.tree.sig_drag_ended.connect(self.ghost_label.hide)
        self.tree.itemPressed.connect(lambda: self.setFocus())
        self.tree.orderChanged.connect(self.sync_data_after_drag)
        self.tree.header().sectionResized.connect(self.enforce_artist_min_width)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.tree.itemClicked.connect(self.on_queue_item_clicked)
        self._queue_tree_panel.load_column_state()

        # Rich info panel goes in the Now Playing tab
        self._now_playing_panel = NowPlayingInfoTab(self)
        self._now_playing_panel.set_client(self.navidrome_client)
        self._now_playing_panel.artist_clicked.connect(self.navigate_to_artist)
        self._now_playing_panel.album_clicked.connect(self.navigate_to_album)
        self._now_playing_panel.genre_clicked.connect(self.navigate_to_genre)
        self._now_playing_panel.play_requested.connect(self._play_track_from_info)
        self._now_playing_panel.favorite_toggled.connect(
            lambda tid, state: (
                [t.__setitem__('starred', state) for t in self.playlist_data if t.get('id') == tid],
                threading.Thread(target=lambda: self.navidrome_client and self.navidrome_client.set_favorite(tid, state), daemon=True).start()
            )
        )
        self._now_playing_panel.lyrics_requested.connect(
            lambda: (self._queue_panel.btn_lyrics.setChecked(True),)
        )
        self.tabs.addTab(self._now_playing_panel, "Now Playing")
        if hasattr(self, 'theme'):
            self._now_playing_panel.apply_theme(self.theme)

        # 3. Albums
        self.album_browser = LibraryGridBrowser(None)
        
        saved_alb = self.settings.value('album_state')
        if saved_alb:
            try: self.album_browser.restore_state(json.loads(saved_alb))
            except Exception: pass
            
        self.album_browser.play_track_signal.connect(self.add_and_play_from_browser)
        self.album_browser.play_album_signal.connect(self.play_whole_album)
        self.album_browser.queue_track_signal.connect(self.add_track_to_queue)
        self.album_browser.play_next_signal.connect(self.play_track_next)
        if hasattr(self, 'theme'): self.album_browser.set_accent_color(self.theme.accent)
        self.tabs.addTab(self.album_browser, "Albums")
        self.album_browser.album_clicked.connect(self.navigate_to_album)

        # 4. Artists
        self.artist_browser = ArtistGridBrowser(None)
        
        saved_art = self.settings.value('artist_state')
        if saved_art:
            try: self.artist_browser.restore_state(json.loads(saved_art))
            except Exception: pass
            
        self.artist_browser.play_track_signal.connect(self.add_and_play_from_browser)
        self.artist_browser.play_album_signal.connect(self.play_whole_album)
        self.artist_browser.queue_track_signal.connect(self.add_track_to_queue)
        self.artist_browser.play_next_signal.connect(self.play_track_next)
        if hasattr(self, 'theme'): self.artist_browser.set_accent_color(self.theme.accent)
        self.tabs.addTab(self.artist_browser, "Artists")
        self.artist_browser.artist_clicked.connect(self.navigate_to_artist)

        # 5. Tracks
        self.tracks_browser = TracksBrowser(None)
        self.tabs.addTab(self.tracks_browser, "Tracks")

        # 6. Playlists (NEW)
        self.playlists_browser = PlaylistsBrowser(None)
        self.playlists_browser.play_track_signal.connect(self.add_and_play_from_browser)
        self.playlists_browser.play_album_signal.connect(self.play_whole_album)
        self.playlists_browser.queue_track_signal.connect(self.add_track_to_queue)
        self.playlists_browser.play_next_signal.connect(self.play_track_next)
        self.playlists_browser.switch_to_artist_tab.connect(self.navigate_to_artist)
        self.playlists_browser.playlist_clicked.connect(self.navigate_to_playlist)
        if hasattr(self, 'theme'): self.playlists_browser.set_accent_color(self.theme.accent)
        self.tabs.addTab(self.playlists_browser, "Playlists")

        # 7. Favorites
        from favorites_view import FavoritesView
        self._favorites_tab = FavoritesView(self.navidrome_client)
        self._favorites_tab.setObjectName('FavoritesTab')
        self._favorites_tab.album_clicked.connect(self.navigate_to_album)
        self._favorites_tab.artist_clicked.connect(self.navigate_to_artist)
        self._favorites_tab.play_album.connect(self.play_whole_album)
        self._favorites_tab.play_all.connect(self.play_whole_album)
        self._favorites_tab.shuffle_all.connect(self.play_whole_album)
        self._favorites_tab.play_track.connect(self.add_and_play_from_browser)
        self.tabs.addTab(self._favorites_tab, "Favorites")

        # 8. Mix Builder
        self._mix_builder_tab = QWidget()
        self._mix_builder_tab.setObjectName('MixBuilderTab')
        self._mix_builder_tab.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _mb_lo = QVBoxLayout(self._mix_builder_tab)
        _mb_lo.setContentsMargins(0, 0, 0, 0)
        _mb_lo.setSpacing(0)
        _mb_lbl = QLabel("Coming Soon™")
        _mb_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _mb_lbl.setStyleSheet("padding: 10px 0 0 0;")
        _mb_lo.addWidget(_mb_lbl)
        _mb_lo.addStretch()
        self.tabs.addTab(self._mix_builder_tab, "Mix Builder")

        # 9. Visualizer — created lazily on first visit to save ~50–80 MB at startup
        self.visualizer = None
        _vis_container = QWidget()
        _vis_container.setObjectName('VisContainer')
        _vis_lo = QVBoxLayout(_vis_container)
        _vis_lo.setContentsMargins(0, 0, 0, 0)
        _vis_lo.setSpacing(0)
        self._coming_soon_lbl = QLabel("Coming Soon™")
        self._coming_soon_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._coming_soon_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.18); background: transparent; border: none;"
            "font-size: 11px; letter-spacing: 1px; padding: 10px 0 0 0;"
        )
        _vis_lo.addWidget(self._coming_soon_lbl)
        self._vis_container = _vis_container
        self._vis_lo = _vis_lo
        self.tabs.addTab(_vis_container, "Visualizer")
        self._vis_tab_idx = self.tabs.count() - 1

        def _on_vis_tab_changed(idx):
            is_vis = (self.tabs.widget(idx) is self._vis_container)
            if is_vis and self.visualizer is None:
                # First visit — create the visualizer now
                self.visualizer = AudioVisualizer(self.audio_engine)
                self._vis_lo.addWidget(self.visualizer, 1)
                saved_vis = self.settings.value('vis_mode')
                if saved_vis is not None:
                    self.visualizer.vis_mode = int(saved_vis)
                if hasattr(self, 'theme'):
                    self.visualizer.bar_color = QColor(self.theme.accent)
                    self.visualizer.set_bg_color(self.theme.main_panel_bg)
            if self.visualizer:
                self.audio_engine.set_visualizer_active(is_vis)
                self.visualizer.visualizer_enabled = is_vis
        self.tabs.currentChanged.connect(_on_vis_tab_changed)

        # 8. THE HIDDEN GLOBAL ALBUM TAB!
        from albums_browser import AlbumDetailView
        self.global_album_view = AlbumDetailView(None)
        
        
        self.global_album_view.play_clicked.connect(self.play_global_album)
        self.global_album_view.shuffle_clicked.connect(self.shuffle_global_album)
        self.global_album_view.album_favorite_toggled.connect(self.toggle_global_fav)
        self.global_album_view.artist_clicked.connect(self.navigate_to_artist)
        self.global_album_view.track_artist_clicked.connect(self.navigate_to_artist)
        self.global_album_view.genre_clicked.connect(self.navigate_to_genre)
        self.global_album_view.track_play_signal.connect(lambda tracks, idx: self.play_whole_album([tracks[idx]]))
        
        self.tabs.addTab(self.global_album_view, "")
        self.global_album_tab_idx = self.tabs.count() - 1
        self.tabs.tabBar().setTabVisible(self.global_album_tab_idx, False) 
        self.tabs.tabBarClicked.connect(self.on_tab_bar_clicked)

        # Easter egg: 7 rapid clicks on the Home tab launches Tetris
        self._home_click_count = 0
        self._home_click_timer = QTimer(self)
        self._home_click_timer.setSingleShot(True)
        self._home_click_timer.setInterval(600)
        self._home_click_timer.timeout.connect(lambda: setattr(self, '_home_click_count', 0))

        def _on_tab_clicked_egg(idx):
            home_idx = self.tabs.indexOf(self.home_tab)
            if idx == home_idx:
                self._home_click_count += 1
                self._home_click_timer.start()
                if self._home_click_count >= 7:
                    self._home_click_count = 0
                    self._home_click_timer.stop()
                    self._toggle_tetris()
        self.tabs.tabBarClicked.connect(_on_tab_clicked_egg)

        # 8. THE HIDDEN GLOBAL ARTIST TAB!
        from artists_browser import ArtistRichDetailView
        self.global_artist_view = ArtistRichDetailView()
        
        # Route clicks to your main navigation engine
        self.global_artist_view.album_clicked.connect(self.navigate_to_album)
        self.global_artist_view.play_album.connect(self.play_whole_album)
        self.global_artist_view.play_multiple_tracks.connect(self.play_whole_album)
        self.global_artist_view.play_track.connect(self.add_and_play_from_browser)
        self.global_artist_view.artist_clicked.connect(self.navigate_to_artist)
        
        self.tabs.addTab(self.global_artist_view, "")
        self.global_artist_tab_idx = self.tabs.count() - 1
        
        # Hide it from the UI!
        self.tabs.tabBar().setTabVisible(self.global_artist_tab_idx, False)

        # 9. THE HIDDEN GLOBAL PLAYLIST TAB!
        from playlists_browser import PlaylistDetailView
        self.global_playlist_view = PlaylistDetailView(None)
        
        self.global_playlist_view.track_list.play_track.connect(self.add_and_play_from_browser)
        self.global_playlist_view.track_list.play_multiple_tracks.connect(self.play_whole_album)
        self.global_playlist_view.track_list.queue_track.connect(self.add_track_to_queue)
        self.global_playlist_view.track_list.play_next.connect(self.play_track_next)
        self.global_playlist_view.track_list.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        self.global_playlist_view.track_list.switch_to_album_tab.connect(lambda data: self.navigate_to_album(data))
        
        self.global_playlist_view.play_clicked.connect(self.play_global_playlist)
        self.global_playlist_view.shuffle_clicked.connect(self.shuffle_global_playlist)
        
        self.tabs.addTab(self.global_playlist_view, "")
        self.global_playlist_tab_idx = self.tabs.count() - 1
        self.tabs.tabBar().setTabVisible(self.global_playlist_tab_idx, False)

        # ── Tab icons + narrow-mode label map ────────────────────────────────
        _TAB_ICON_MAP = {
            'Home': 'home', 'Now Playing': 'now_playing', 'Albums': 'albums',
            'Artists': 'artists', 'Tracks': 'tracks', 'Playlists': 'playlists',
            'Favorites': 'heart', 'Mix Builder': 'mix',
            'Visualizer': 'visualizer',
        }
        _bar = self.tab_bar
        _bar.setIconSize(QSize(16, 16))
        for _i in range(_bar.count()):
            _lbl = _bar.tabText(_i)
            if _lbl and _bar.isTabVisible(_i):
                # Store label as tab data so it survives reordering
                _bar.setTabData(_i, _lbl)
                _img = _TAB_ICON_MAP.get(_lbl)
                if _img:
                    _pix = QPixmap(resource_path(f'img/{_img}.png'))
                    if not _pix.isNull():
                        _pix = _pix.scaled(QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                        _bar.setTabIcon(_i, QIcon(_pix))
        QTimer.singleShot(0, self._update_tab_mode)

        # --- SIGNAL CONNECTIONS ---
        self.home_tab.album_clicked.connect(lambda data: self.navigate_to_album(data))
        self.album_browser.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        self.album_browser.genre_filter_requested.connect(self.navigate_to_genre)
        self.artist_browser.switch_to_album_tab.connect(lambda data: self.navigate_to_album(data))
        # artist_clicked / album_clicked already connected above during NowPlayingInfoTab init
        self.tabs.currentChanged.connect(self.on_tab_changed_global)
        

        # Enable drag reordering
        self.tabs.tabBar().setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self._tab_move_in_progress = False
        self._restore_tab_order()

        # Initialize History with Home
        self.add_global_nav(self.tabs.indexOf(self.home_tab), 'home')

        # --- Main Panel (Tabs) ---
        self._main_panel = QWidget()
        self._main_panel.setObjectName('MainPanel')
        self._main_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_panel = QVBoxLayout(self._main_panel)
        _bw = self.theme.border_width
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(0)

        self.main_header = QWidget()
        self.main_header.setObjectName('MainHeader')
        self.main_header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.main_header.setFixedHeight(62)
        self.main_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _mh_layout = QHBoxLayout(self.main_header)
        _mh_layout.setContentsMargins(0, 0, 0, 0)
        _mh_layout.setSpacing(0)

        _mh_layout.addStretch()
        _mh_layout.addWidget(self.tab_bar)
        _mh_layout.addStretch()

        # Nav buttons go in the left panel header's right corner
        self._left_panel.header_layout.addWidget(self.btn_back)
        self._left_panel.header_layout.addWidget(self.btn_fwd)

        # Trigger tab mode check whenever the header is resized (e.g. panels dragged)
        class _HRF(_QObject):
            def eventFilter(self_, obj, ev):
                if ev.type() == QEvent.Type.Resize:
                    QTimer.singleShot(0, self._update_tab_mode)
                return False
        self._header_resize_filter = _HRF(self.main_header)
        self.main_header.installEventFilter(self._header_resize_filter)

        right_panel.addWidget(self.main_header)       # tab bar only — own background
        right_panel.addWidget(self.tab_stack, 1)      # content — browser backgrounds only
        _right_widget = self._main_panel

        _left_w = max(297, min(363, int(self.settings.value('left_panel_width', 330))))
        self._left_panel.setFixedWidth(_left_w)
        content.addWidget(self._left_panel)
        content.addWidget(_right_widget, 1)
        body.addLayout(content, 1)

        # ── Permanent queue sidebar ──────────────────────────────────────────
        self._queue_panel_container = QWidget()
        _queue_w = max(360, min(440, int(self.settings.value('queue_panel_width', 400))))
        self._queue_panel_container.setFixedWidth(_queue_w)
        _qc_layout = QVBoxLayout(self._queue_panel_container)
        _qc_layout.setContentsMargins(0, 0, 0, 0) #QUEUE margins (bottom)
        _qc_layout.setSpacing(0)
        self._queue_panel = QueuePanel(self._queue_panel_container, embedded=True)
        self._queue_panel.play_index.connect(self._queue_play_at)
        self._queue_panel.play_next_index.connect(self._queue_play_next_at)
        self._queue_panel.remove_index.connect(self._queue_remove_at)
        self._queue_panel.artist_clicked.connect(self.navigate_to_artist)
        self._queue_panel.favorite_toggled.connect(self._queue_toggle_favorite)
        self._queue_panel.reordered.connect(self._queue_reordered)
        self._queue_panel.start_radio.connect(self.start_radio)
        self._queue_panel.clear_queue.connect(self._clear_queue)
        _qc_layout.addWidget(self._queue_panel)
        body.addWidget(self._queue_panel_container)

        main_layout.addLayout(body, 1)
        main_layout.addSpacing(0)

        # =========================================================
        # PLAYER CONTROLS & FOOTER
        # =========================================================


        self.cast_btn = QPushButton("")
        self.cast_btn.setFixedSize(40, 40)
        self.cast_btn.setIconSize(QSize(22, 22))
        self.cast_btn.setFlat(True)
        self.cast_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cast_btn.setStyleSheet("background: transparent; border: none;")
        self.cast_btn.setToolTip("Cast to device")
        self.cast_btn.clicked.connect(self._on_cast_clicked)

        self.settings_btn = QPushButton("")
        self.settings_btn.setFixedSize(40, 40)
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.clicked.connect(self.open_settings)
        self.settings_btn.setToolTip("Settings")

        self.btn_stop = QPushButton("")
        self.btn_stop.setFixedSize(40, 40)
        self.btn_stop.clicked.connect(self._media_stop)
        self.btn_stop.setToolTip("Stop")

        self.btn_shuffle = StatusButton("")
        self.btn_shuffle.setCheckable(True)
        self.btn_shuffle.setFixedSize(40, 40)
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_shuffle.setToolTip("Shuffle")

        self.btn_prev = QPushButton("")
        self.btn_prev.setFixedSize(50, 50)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_prev.setToolTip("Previous Track")

        self.btn_play = PlayButton()
        self.btn_play.setFixedSize(58, 58)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_play.setToolTip("Play/Pause")

        self.btn_next = QPushButton("")
        self.btn_next.setFixedSize(50, 50)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_next.setToolTip("Next Track")

        self.btn_repeat = StatusButton("")
        self.btn_repeat.setCheckable(True)
        self.btn_repeat.setFixedSize(40, 40)
        self.btn_repeat.clicked.connect(self.toggle_repeat)
        self.btn_repeat.setToolTip("Repeat")

        self.vol_slider = ClickableSlider(Qt.Orientation.Horizontal, self, is_volume=True)
        self.vol_slider.setFixedWidth(100)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.last_volume)
        self.vol_slider.valueChanged.connect(self.update_volume)
        self.vol_slider.sliderMoved.connect(self.vol_slider.update_tooltip_pos)

        self.vol_icon_label = QPushButton()
        self.vol_icon_label.setFixedSize(40, 40)
        self.vol_icon_label.setIconSize(QSize(self._VOL_ICON_SIZE, self._VOL_ICON_SIZE))
        self.vol_icon_label.setToolTip("Mute/Unmute")
        self.vol_icon_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol_icon_label.setFlat(True)
        self.vol_icon_label.clicked.connect(self.toggle_mute)

        self.current_time_label = QLabel("0:00")
        
        # SWAP THE SLIDER FOR THE WAVEFORM
        self.seek_bar = WaveformScrubber(master_color=self.theme.accent, parent=self)
        self.seek_bar.seek_requested.connect(self.on_waveform_seek)
        self.seek_bar.mode_toggled.connect(self.on_waveform_toggled)
        
        # THE SCRATCH CONNECTION (Wire physics straight to C++)
        self.seek_bar.scratch_mode_changed.connect(self.audio_engine.set_scratch_mode)
        self.seek_bar.velocity_changed.connect(self.audio_engine.set_scratch_velocity)
        
        # THE UI CONNECTION (Update the time text while scrubbing)
        self.seek_bar.position_updated.connect(
            lambda ms: self.current_time_label.setText(self.format_time(ms)) if hasattr(self, 'current_time_label') else None
        )

        saved_mode = int(self.settings.value('waveform_mode', 2))
        if saved_mode in (1, 2):
            self.seek_bar.display_mode = saved_mode
            self.seek_bar.render_timer.stop()

        saved_vis = int(self.settings.value('vis_mode', 0))
        if saved_vis and getattr(self, 'visualizer', None):
            self.visualizer.vis_mode = saved_vis
        
        self.total_time_label = QLabel("0:00")


        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(20)
        self.controls_layout.addStretch()
        self.controls_layout.addWidget(self.btn_stop)
        self.controls_layout.addWidget(self.btn_shuffle)
        self.controls_layout.addWidget(self.btn_prev)
        self.controls_layout.addWidget(self.btn_play)
        self.controls_layout.addWidget(self.btn_next)
        self.controls_layout.addWidget(self.btn_repeat)
        self.controls_layout.addStretch()

        self.slider_layout = QHBoxLayout()
        self.slider_layout.setContentsMargins(0, 0, 0, 0)
        self.slider_layout.setSpacing(15)
        self.slider_layout.addWidget(self.current_time_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.slider_layout.addWidget(self.seek_bar, 1)
        self.slider_layout.addWidget(self.total_time_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._footer_panel = QWidget()
        self._footer_panel.setObjectName("FooterPanel")
        self._footer_panel.setStyleSheet("QWidget#FooterPanel { background-color: rgba(14, 14, 14, 0.75); border-top: 1px solid rgba(255, 255, 255, 0.1); }")

        main_footer_layout = QHBoxLayout(self._footer_panel)
        main_footer_layout.setContentsMargins(8, 0, 20, 0)
        main_footer_layout.setSpacing(0)

        footer_left = QWidget()
        left_layout = QHBoxLayout(footer_left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.now_playing_widget = NowPlayingFooterWidget()
        self.now_playing_widget.artist_clicked.connect(self.on_footer_artist_click)
        self.now_playing_widget.album_clicked.connect(self.on_footer_album_click)
        self.now_playing_widget.title_clicked.connect(self.on_footer_title_click)
        self.now_playing_widget.track_right_clicked.connect(self._show_footer_track_context_menu)
        # art left-click intentionally unbound
        self.now_playing_widget.bpm_adjusted.connect(self._on_footer_bpm_adjusted)
        self.now_playing_widget.expand_art_clicked.connect(self._toggle_sidebar_art)

        # Footer art slide animation (width) — needs now_playing_widget to exist
        self._footer_art_anim = QPropertyAnimation(
            self.now_playing_widget.art_label, b"maximumWidth"
        )
        self._footer_art_anim.setDuration(250)
        self._footer_art_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._footer_art_anim.valueChanged.connect(
            lambda v: self.now_playing_widget.art_label.setMinimumWidth(int(v))
        )

        # Close button overlaid on the left-panel art (top-right corner, hover-only)
        self._art_close_btn = QPushButton(self._art_section)
        self._art_close_btn.setFixedSize(24, 24)
        self._art_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._art_close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._art_close_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._art_close_btn.setIconSize(QSize(16, 16))
        self._art_close_btn.hide()
        self._art_close_btn.clicked.connect(self._toggle_sidebar_art)
        self._art_close_btn.raise_()

        _raw_exp = QPixmap(resource_path("img/expand.png"))
        if not _raw_exp.isNull():
            def _mk_icon(pix, color):
                out = QPixmap(pix.size()); out.fill(Qt.GlobalColor.transparent)
                p = QPainter(out); p.drawPixmap(0, 0, pix)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                p.fillRect(out.rect(), QColor(color)); p.end()
                return QIcon(out)
            self._close_icon_dim    = _mk_icon(_raw_exp, "#515151")
            self._close_icon_bright = _mk_icon(_raw_exp, "#ffffff")
            self._art_close_btn.setIcon(self._close_icon_dim)

        self._art_close_opacity = QGraphicsOpacityEffect(self._art_close_btn)
        self._art_close_opacity.setOpacity(0.0)
        self._art_close_btn.setGraphicsEffect(self._art_close_opacity)
        self._art_close_hover_anim = QPropertyAnimation(self._art_close_opacity, b"opacity")
        self._art_close_hover_anim.setDuration(180)

        # Dedicated hover filter — not blocked by the keyboard mixin's eventFilter
        class _ArtHoverFilter(_QObject):
            def __init__(self_, btn, opacity, anim):
                super().__init__()
                self_._btn = btn
                self_._opacity = opacity
                self_._anim = anim
            def eventFilter(self_, _obj, event):
                if event.type() == _QEvent2.Type.Enter:
                    self_._anim.stop(); self_._anim.setEndValue(1.0); self_._anim.start()
                    if hasattr(self, '_close_icon_bright'):
                        self._art_close_btn.setIcon(self._close_icon_bright)
                elif event.type() == _QEvent2.Type.Leave:
                    self_._anim.stop(); self_._anim.setEndValue(0.0); self_._anim.start()
                    if hasattr(self, '_close_icon_dim'):
                        self._art_close_btn.setIcon(self._close_icon_dim)
                return False
        self._art_hover_filter = _ArtHoverFilter(
            self._art_close_btn, self._art_close_opacity, self._art_close_hover_anim
        )
        self._art_section.installEventFilter(self._art_hover_filter)

        left_layout.addWidget(self.now_playing_widget)
        
        footer_center = QWidget()
        center_layout = QVBoxLayout(footer_center)
        center_layout.setContentsMargins(10, 10, 10, 0)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addLayout(self.controls_layout)
        center_layout.addLayout(self.slider_layout)
        
        footer_right = QWidget()
        right_layout = QHBoxLayout(footer_right)
        right_layout.setContentsMargins(8, 0, 8, 0)
        right_layout.setSpacing(8)

        right_layout.addStretch()
        right_layout.addWidget(self.settings_btn)
        right_layout.addWidget(self.vol_icon_label)
        right_layout.addSpacing(4)
        right_layout.addWidget(self.vol_slider)
        right_layout.addWidget(self.cast_btn)
        
        main_footer_layout.addWidget(footer_left, 2)
        main_footer_layout.addWidget(footer_center, 3)
        main_footer_layout.addWidget(footer_right, 2)

        main_layout.addWidget(self._footer_panel)

        # Queue panel is now a permanent sidebar (see body layout above)

        # --- FINAL SETUPS ---
        # Context menu is handled by NowPlayingPanel._show_track_context_menu
        for w in [self.settings_btn, self.cast_btn, self.btn_stop, self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat, self.vol_slider, self.seek_bar, self.vol_icon_label, self.btn_back, self.btn_fwd]:
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            w.installEventFilter(self)

        self.sync_overlay = QWidget(self)
        self.sync_overlay.setFixedSize(300, 85)
        self.sync_overlay.setStyleSheet("background-color: rgba(20, 20, 20, 0.95); border: 1px solid #1DB954; border-radius: 8px;")
        self.sync_overlay.hide()
        
        overlay_layout = QVBoxLayout(self.sync_overlay)
        overlay_layout.setContentsMargins(15, 10, 15, 10)
        overlay_layout.setSpacing(5)
        
        self.sync_overlay_label = QLabel("Building Local Library...")
        self.sync_overlay_label.setStyleSheet("color: white; font-size: 13px; font-weight: bold; border: none; background: transparent;")
        
        self.sync_progress_bar = QProgressBar()
        self.sync_progress_bar.setFixedHeight(4)
        self.sync_progress_bar.setTextVisible(False)
        self.sync_progress_bar.setStyleSheet("QProgressBar { background-color: #333; border: none; border-radius: 2px; } QProgressBar::chunk { background-color: #1DB954; border-radius: 2px; }")
        
        self.sync_progress_label = QLabel("Connecting to server...")
        self.sync_progress_label.setStyleSheet("color: #aaa; font-size: 11px; border: none; background: transparent;")
        
        overlay_layout.addWidget(self.sync_overlay_label)
        overlay_layout.addWidget(self.sync_progress_bar)
        overlay_layout.addWidget(self.sync_progress_label)
        
        from hotkeys import HotkeyManager
        self.hotkey_manager = HotkeyManager(self.settings)

        self.sc_space         = self.hotkey_manager.register("play_pause",       self, self.handle_space_shortcut)
        self.sc_left          = self.hotkey_manager.register("seek_back",        self, lambda: self.handle_arrow_shortcut(-5000))
        self.sc_right         = self.hotkey_manager.register("seek_fwd",         self, lambda: self.handle_arrow_shortcut(5000))
        self.sc_next_tab      = self.hotkey_manager.register("next_tab",         self, self.cycle_tab_forward)
        self.sc_prev_tab      = self.hotkey_manager.register("prev_tab",         self, self.cycle_tab_backward)
        self.sc_vol_up        = self.hotkey_manager.register("vol_up",           self, lambda: self.adjust_volume_by(5))
        self.sc_vol_down      = self.hotkey_manager.register("vol_down",         self, lambda: self.adjust_volume_by(-5))
        self.sc_mute          = self.hotkey_manager.register("mute",             self, self.toggle_mute)
        self.sc_search        = self.hotkey_manager.register("spotlight",        self, self.focus_spotlight)
        self.sc_back          = self.hotkey_manager.register("nav_back",         self, self.go_back)
        self.sc_fwd           = self.hotkey_manager.register("nav_fwd",          self, self.go_forward)
        self.sc_shuffle       = self.hotkey_manager.register("shuffle",          self, lambda: self.btn_shuffle.setChecked(not self.btn_shuffle.isChecked()) or self.toggle_shuffle())
        self.sc_repeat        = self.hotkey_manager.register("repeat",           self, lambda: self.btn_repeat.setChecked(not self.btn_repeat.isChecked()) or self.toggle_repeat())
        self.sc_next_track    = self.hotkey_manager.register("next_track",       self, self.play_next)
        self.sc_prev_track    = self.hotkey_manager.register("prev_track",       self, self.play_prev)
        self.sc_local_search  = self.hotkey_manager.register("local_search",     self, self.focus_local_search)
        self.sc_local_search2 = self.hotkey_manager.register("local_search_alt", self, self.focus_local_search)

        
        self.tracks_browser.play_track.connect(self.add_and_play_from_browser)
        self.tracks_browser.play_multiple_tracks.connect(self.play_whole_album)
        self.tracks_browser.queue_track.connect(self.add_track_to_queue)
        self.tracks_browser.play_next.connect(self.play_track_next)
        self.tracks_browser.start_radio.connect(self.start_radio)
        self.tracks_browser.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        self.tracks_browser.switch_to_album_tab.connect(lambda data: self.navigate_to_album(data))
        self.audio_engine.waveform_generated.connect(self.seek_bar.set_real_samples)

        # Overlay drag handles — transparent, sit at panel borders
        self._left_handle = _ResizeHandle(
            self._left_panel, 330, +1, 'left_panel_width', self.settings,
            self._reposition_resize_handles, central_widget)
        self._queue_handle = _ResizeHandle(
            self._queue_panel_container, 400, -1, 'queue_panel_width', self.settings,
            self._reposition_resize_handles, central_widget)
        QTimer.singleShot(0, self._reposition_resize_handles)

    # ── Cast ──────────────────────────────────────────────────────────────────

    def _init_cast_manager(self):
        """Called at startup (2 s delay) to kick off background device discovery."""
        from cast_manager import CastManager
        if not hasattr(self, '_cast_manager'):
            self._cast_manager = CastManager(self)

    def _on_cast_clicked(self):
        self._init_cast_manager()
        self._cast_manager.show_picker()

    # ── Queue panel helpers ───────────────────────────────────────────────────

    def _refresh_queue_panel(self):
        if not hasattr(self, '_queue_panel'):
            return
        color = self.theme.accent
        self._queue_panel.set_client(getattr(self, 'navidrome_client', None))
        self._queue_panel.set_accent_color(color)
        self._queue_panel.refresh(self.playlist_data, self.current_index,
                                  is_playing=getattr(self.audio_engine, 'is_playing', False))
        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            self._queue_panel.load_track(
                track.get('artistId') or track.get('artist_id') or '',
                track.get('artist') or '',
            )

    def _toggle_sidebar_art(self):
        # Block expand when Tetris is active
        tetris_active = (getattr(self, '_tetris_widget', None) and
                         self._tetris_widget.isVisible())
        if tetris_active and not self._sidebar_art_visible:
            return  # refuse to expand while playing
        self._sidebar_art_visible = not self._sidebar_art_visible
        target = self._left_panel.width() - 16
        art_lbl = self.now_playing_widget.art_label

        self._sidebar_art_anim.stop()
        self._footer_art_anim.stop()

        if self._sidebar_art_visible:
            # Left panel: slide open
            self._sidebar_art_anim.setStartValue(0)
            self._sidebar_art_anim.setEndValue(target)
            # Footer art: slide out to the left
            self._footer_art_anim.setStartValue(art_lbl.maximumWidth())
            self._footer_art_anim.setEndValue(0)
            self.now_playing_widget.set_expand_btn_direction(False)
            # Show close button once animation finishes
            self._sidebar_art_anim.finished.connect(self._on_sidebar_art_opened)
        else:
            # Hide close button immediately
            self._art_close_btn.hide()
            try: self._sidebar_art_anim.finished.disconnect(self._on_sidebar_art_opened)
            except: pass
            # Left panel: slide closed
            self._sidebar_art_anim.setStartValue(self._art_section.maximumHeight())
            self._sidebar_art_anim.setEndValue(0)
            # Footer art: slide back in from the left
            self._footer_art_anim.setStartValue(art_lbl.maximumWidth())
            self._footer_art_anim.setEndValue(84)
            self.now_playing_widget.set_expand_btn_direction(True)

        self._sidebar_art_anim.start()
        self._footer_art_anim.start()

    def _on_sidebar_art_opened(self):
        try: self._sidebar_art_anim.finished.disconnect(self._on_sidebar_art_opened)
        except: pass
        self._update_art_close_btn_style()
        self._art_close_btn.move(self._art_section.width() - 28, 4)
        self._art_close_btn.show()
        self._art_close_btn.raise_()

    def _update_art_close_btn_style(self):
        c = QColor(self.theme.accent)
        r, g, b = c.red(), c.green(), c.blue()
        dr, dg, db = int(r * .3), int(g * .3), int(b * .3)
        self._art_close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba({r},{g},{b},0.1);
                border: 2px solid rgb({dr},{dg},{db});
                border-radius: 12px; outline: none;
            }}
            QPushButton:hover {{
                background-color: rgba({r},{g},{b},0.4);
                border: 2px solid rgb({r},{g},{b});
            }}
            QPushButton:pressed {{ background-color: rgba({r},{g},{b},0.2); }}
            QPushButton::menu-indicator {{ width: 0; image: none; }}
        """)

    def _queue_play_at(self, idx: int):
        if 0 <= idx < len(self.playlist_data):
            self.play_song(idx)

    def _queue_play_next_at(self, idx: int):
        if not (0 <= idx < len(self.playlist_data)):
            return
        current_track = (self.playlist_data[self.current_index]
                         if 0 <= self.current_index < len(self.playlist_data) else None)
        track = self.playlist_data.pop(idx)
        item  = self.tree.takeTopLevelItem(idx)
        if current_track:
            try:
                self.current_index = self.playlist_data.index(current_track)
            except ValueError:
                self.current_index = -1
        insert_pos = self.current_index + 1
        self.playlist_data.insert(insert_pos, track)
        self.tree.insertTopLevelItem(insert_pos, item)
        if current_track:
            try:
                self.current_index = self.playlist_data.index(current_track)
            except ValueError:
                pass
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setText(0, str(i + 1))
        self._refresh_queue_panel()

    def _on_footer_bpm_adjusted(self, new_bpm: float):
        rounded = round(new_bpm, 1)
        if not (0 <= self.current_index < len(self.playlist_data)):
            return
        track_id = str(self.playlist_data[self.current_index].get('id') or
                       self.playlist_data[self.current_index].get('path', ''))
        # Update every matching entry in playlist_data
        for t in self.playlist_data:
            if str(t.get('id') or t.get('path', '')) == track_id:
                t['bpm'] = rounded
        if track_id and hasattr(self, 'bpm_cache'):
            self.bpm_cache[track_id] = rounded
            self.save_bpm_cache()
        self.now_playing_widget.set_bpm(rounded)
        self.file_type_label.setText(
            f"{getattr(self, 'current_file_type_text', '')}   •   {rounded:.1f} BPM"
        )
        if hasattr(self, 'tracks_browser') and track_id:
            self.tracks_browser.refresh_track_bpm(track_id, rounded)

    def _toggle_tetris(self):
        from tetris_easter_egg import TetrisWidget
        if not hasattr(self, '_tetris_widget'):
            self._tetris_widget = None
        if self._tetris_widget and self._tetris_widget.isVisible():
            self._tetris_widget.hide()
            return
        if not self._tetris_widget:
            self._tetris_widget = TetrisWidget(self._left_panel)
            self._tetris_widget.closed.connect(self._tetris_widget.hide)

            # Keep tetris filling the panel whenever it resizes
            class _PanelFilter(_QObject):
                def eventFilter(self_, obj, ev):
                    if ev.type() == QEvent.Type.Resize and self._tetris_widget.isVisible():
                        hh = self._left_panel.header.height()
                        self._tetris_widget.setGeometry(
                            0, hh, self._left_panel.width() - 1, self._left_panel.height() - hh)
                    return False
            self._tetris_panel_filter = _PanelFilter(self._left_panel)
            self._left_panel.installEventFilter(self._tetris_panel_filter)
        lp = self._left_panel
        # Collapse album art if open
        if getattr(self, '_sidebar_art_visible', False):
            self._toggle_sidebar_art()
        # Apply current theme colors
        _t = getattr(self, 'theme', None)
        _bg     = getattr(_t, 'left_panel_bg',        '14,14,14') if _t else '14,14,14'
        _bc     = getattr(_t, 'border_color',         '#2a2a2a')  if _t else '#2a2a2a'
        _accent = getattr(_t, 'accent',               '#cccccc')  if _t else '#cccccc'
        _fg2    = getattr(_t, 'font_color_secondary', '#999999')  if _t else '#999999'
        _px2    = getattr(_t, 'font_size_secondary',  12)         if _t else 12
        if _t and not getattr(_t, 'auto_border_from_accent', True):
            _bc = getattr(_t, 'manual_border_color', '#2a2a2a')
        self._tetris_widget.set_theme(_bg, _bc, _accent, _fg2, _px2)

        # Fill left panel below the header (y=62 = header height)
        hdr_h = lp.header.height()
        self._tetris_widget.setGeometry(0, hdr_h, lp.width() - 1, lp.height() - hdr_h)
        self._tetris_widget.raise_()
        self._tetris_widget.show()
        self._tetris_widget.setFocus()

    def _queue_reordered(self, new_tracks: list, new_current: int):
        clean = [{k: v for k, v in t.items() if not k.startswith('_')} for t in new_tracks]
        self.playlist_data = clean
        self.current_index = new_current
        self.history.clear()
        self._shuffle_queue.clear()
        self.tree.blockSignals(True)
        self.tree.clear()
        for i, track in enumerate(clean):
            item = self._build_tree_item(track)
            item.setText(0, str(i + 1))
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self.refresh_ui_styles()
        self.update_indicator()
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()  # recalculate _is_past after reorder

    def _clear_queue(self):
        self.audio_engine.stop()
        self.playlist_data.clear()
        self.current_index = -1
        self.history.clear()
        self._shuffle_queue.clear()
        self.tree.clear()
        self._refresh_queue_panel()
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()

    def _play_track_from_info(self, track: dict):
        """Play a single track clicked in the NowPlayingInfoTab."""
        self.play_track_from_data(track)

    def _queue_toggle_favorite(self, idx: int):
        if not (0 <= idx < len(self.playlist_data)):
            return
        track = self.playlist_data[idx]
        raw = track.get('starred', False)
        current = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new_state = not current
        track['starred'] = new_state
        if idx == self.current_index and hasattr(self, 'heart_btn'):
            accent = self.theme.accent
            self.heart_btn.setIcon(self._make_heart_icon(new_state, accent))
        if hasattr(self, 'navidrome_client') and self.navidrome_client:
            import threading
            threading.Thread(
                target=lambda: self.navidrome_client.set_favorite(track.get('id'), new_state),
                daemon=True,
            ).start()

    def _queue_remove_at(self, idx: int):
        if not (0 <= idx < len(self.playlist_data)):
            return
        self.history.clear()
        self._shuffle_queue.clear()
        playing_track = (self.playlist_data[self.current_index]
                         if 0 <= self.current_index < len(self.playlist_data) else None)
        self.playlist_data.pop(idx)
        self.tree.takeTopLevelItem(idx)
        self.current_index = -1
        if playing_track:
            try:
                self.current_index = self.playlist_data.index(playing_track)
            except ValueError:
                self.current_index = -1
        if self.current_index == -1 and playing_track:
            self.audio_engine.stop()
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setText(0, str(i + 1))
        self.refresh_ui_styles()
        self.update_indicator()
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()

