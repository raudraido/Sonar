"""
player/window.py — SonarPlayer main window.

SonarPlayer composes all behaviour from five focused mixins.
Only __init__ and init_ui live here; everything else is in
player/mixins/*.py.
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidgetItem, QSlider, QPushButton, QFileDialog, QHeaderView,
    QAbstractItemView, QStyledItemDelegate, QColorDialog, QMenu,
    QStyle, QCheckBox, QToolTip, QGraphicsColorizeEffect, QLineEdit,
    QGraphicsOpacityEffect, QTabWidget, QListWidgetItem, QSizePolicy,
    QProgressBar, QDialog, QMessageBox, QComboBox, QApplication, QSplitter
)
from PyQt6.QtCore import (
    Qt, QTimer, QSize, QThread, pyqtSignal, QPropertyAnimation,
    QUrl, QPoint, QPointF, QItemSelectionModel, QRect, QEvent,
    QRectF, QSettings
)
from PyQt6.QtGui import (
    QPixmap, QImage, QColor, QMouseEvent, QAction, QIcon,
    QFontMetrics, QCursor,
    QPolygon, QFont, QPen, QBrush, QPainterPath, QPixmapCache,
    QMovie
)

import os
import sys
import json
from visualizer import AudioVisualizer
from audio_engine import AudioEngine
from subsonic_client import SubsonicClient
from albums_browser import LibraryGridBrowser
from artists_browser import ArtistGridBrowser
from now_playing import PlaylistTree, NowPlayingPanel, COL_LENGTH, COL_TITLE, COL_ALBUM
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
    TriangleTooltip, ClickableSlider, ClickableLabel,
    SettingsWindow, StatusButton, SquareArtContainer,
)
from player import resource_path
from player.mixins.playback    import PlaybackMixin
from player.mixins.navigation  import NavigationMixin
from player.mixins.visuals     import VisualsMixin
from player.mixins.keyboard    import KeyboardMixin
from player.mixins.persistence import PersistenceMixin
from queue_panel import QueuePanel
from PyQt6.QtCore import QObject as _QObject, QEvent as _QEvent2
from PyQt6.QtWidgets import QFrame as _QFrame, QLabel as _QLabelTT, QSplitterHandle as _QSplitterHandle
from PyQt6.QtCore import Qt as _Qt2
from PyQt6.QtGui import QPainter as _QPainter, QColor as _QColor


class _SectionWidget(QWidget):
    """Left-panel section — hide/unhide button fades in on hover, exact scrubber pattern."""

    def __init__(self, content, key, main_window, parent=None):
        super().__init__(parent)
        self._content = content
        self._key = key
        self._main = main_window

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lo.addWidget(content)

        self._btn = QPushButton(self)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn.setIconSize(QSize(16, 16))
        self._btn.setFixedSize(28, 28)
        self._btn.clicked.connect(self._toggle)
        self._btn.installEventFilter(self)

        # Build dim (#515151) and bright (#ffffff) variants for both icons
        def _tint(path, color):
            raw = QPixmap(resource_path(path))
            if raw.isNull():
                return QIcon()
            out = QPixmap(raw.size())
            out.fill(Qt.GlobalColor.transparent)
            p = _QPainter(out)
            p.setRenderHint(_QPainter.RenderHint.Antialiasing)
            p.drawPixmap(0, 0, raw)
            p.setCompositionMode(_QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(color))
            p.end()
            return QIcon(out)

        self._hide_dim    = _tint("img/hide.png",   "#515151")
        self._hide_bright = _tint("img/hide.png",   "#ffffff")
        self._show_dim    = _tint("img/unhide.png", "#515151")
        self._show_bright = _tint("img/unhide.png", "#ffffff")

        self._btn.setIcon(self._hide_dim)
        self._btn.setToolTip("Hide")
        self._toggle_opacity = None
        self._hover_anim = None
        self._init_opacity_effect()
        self._btn.hide()

    def _current_dim(self):
        return self._hide_dim if self._content.isVisible() else self._show_dim

    def _current_bright(self):
        return self._hide_bright if self._content.isVisible() else self._show_bright

    def set_master_color(self, mc):
        r, g, b = QColor(mc).red(), QColor(mc).green(), QColor(mc).blue()
        dr, dg, db = int(r * .3), int(g * .3), int(b * .3)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba({r},{g},{b},0.1);
                border: 2px solid rgb({dr},{dg},{db});
                border-radius: 14px; outline: none;
            }}
            QPushButton:hover {{
                background-color: rgba({r},{g},{b},0.4);
                border: 2px solid rgb({r},{g},{b});
            }}
            QPushButton:pressed {{ background-color: rgba({r},{g},{b},0.2); }}
        """)

    def _init_opacity_effect(self):
        self._btn.show()
        self._toggle_opacity = QGraphicsOpacityEffect(self._btn)
        self._toggle_opacity.setOpacity(0.0)
        self._btn.setGraphicsEffect(self._toggle_opacity)
        self._hover_anim = QPropertyAnimation(self._toggle_opacity, b"opacity")
        self._hover_anim.setDuration(250)
        self._hover_anim.finished.connect(self._on_anim_finished)

    def _on_anim_finished(self):
        if self._toggle_opacity and self._toggle_opacity.opacity() == 0.0:
            self._btn.setGraphicsEffect(None)
            self._btn.hide()
            self._toggle_opacity = None
            self._hover_anim = None

    def eventFilter(self, obj, event):
        if obj is self._btn:
            if event.type() == QEvent.Type.Enter:
                self._btn.setIcon(self._current_bright())
            elif event.type() == QEvent.Type.Leave:
                self._btn.setIcon(self._current_dim())
        return super().eventFilter(obj, event)

    def enterEvent(self, event):
        if self._toggle_opacity is None:
            self._init_opacity_effect()
        self._hover_anim.stop()
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._toggle_opacity is None:
            super().leaveEvent(event)
            return
        self._hover_anim.stop()
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._btn.move(self.width() - self._btn.width() - 10, 5)

    def _toggle(self):
        vis = not self._content.isVisible()
        self._content.setVisible(vis)
        self._btn.setIcon(self._current_dim())
        self._btn.setToolTip("Hide" if vis else "Unhide")
        self._main.settings.setValue(f'section_{self._key}_visible', int(vis))


class _GripSplitter(QSplitter):
    """QSplitter with a subtle line grab handle."""
    def createHandle(self):
        return _GripHandle(self.orientation(), self)


class _GripHandle(_QSplitterHandle):
    _LINE_H = 40

    def paintEvent(self, event):
        p = _QPainter(self)
        p.setRenderHint(_QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        alpha = 140 if self.underMouse() else 0
        p.setPen(_Qt2.PenStyle.NoPen)
        p.setBrush(_QColor(255, 255, 255, alpha))
        p.drawRoundedRect(cx - 1, cy - self._LINE_H // 2, 2, self._LINE_H, 1, 1)
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class _TooltipLabel(_QFrame):
    """Custom tooltip popup with column-header matching style."""
    def __init__(self):
        super().__init__(None, _Qt2.WindowType.ToolTip | _Qt2.WindowType.FramelessWindowHint | _Qt2.WindowType.WindowStaysOnTopHint)
        self.setAttribute(_Qt2.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 0px;
            }
        """)
        from PyQt6.QtWidgets import QVBoxLayout, QLabel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        self._lbl = QLabel(self)
        self._lbl.setStyleSheet("color: #888888; background: transparent; border: none; font-weight: bold;")
        f = self._lbl.font()
        f.setBold(True)
        f.setPixelSize(11)
        self._lbl.setFont(f)
        lay.addWidget(self._lbl)

    def show_at(self, pos, text):
        self._lbl.setText(text)
        self._lbl.adjustSize()
        self.adjustSize()
        self.move(pos)
        self.show()
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


class SonarPlayer(
    PlaybackMixin,
    NavigationMixin,
    VisualsMixin,
    KeyboardMixin,
    PersistenceMixin,
    QMainWindow,
):
    """
    Main application window.

    Behaviour is split across five mixins (in player/mixins/).
    This class owns only construction (__init__) and UI layout (init_ui).
    """

    def __init__(self, client):
        super().__init__()
        # Install app-level event filters (keep refs to prevent GC)
        self._tooltip_filter = _TooltipFilter(QApplication.instance())
        QApplication.instance().installEventFilter(self._tooltip_filter)
        self.navidrome_client = client
        self.bpm_cache = self.load_bpm_cache()
        self.setWindowTitle("Sonar")
        self.resize(1625, 1070)  #Screen Size Pixels
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
        self.temp_files = []

        self.settings = QSettings()
        
        self.search_context = {}  
        self.last_tab_index = 0    

        self.last_engine_pos = 0       
        self.last_engine_update_time = 0 
        self.ignore_updates_until = 0    
        
        self.smooth_timer = QTimer(self)
        self.smooth_timer.setInterval(33)
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

        # --- DEFAULT VISUAL STYLE / COLOR ---
        self.master_color = "#fafafada"
        self.dynamic_color = True
        self.static_bg_path = self.settings.value('static_bg_path') or None
        
        default_vis = {'blur': 15, 'overlay': 0.3, 'bg_alpha': 0.55, 'footer_alpha': 0.85, 'queue_alpha': 0.96}
        saved_vis = self.settings.value('visual_settings')
        if saved_vis:
            try:
                self.visual_settings = json.loads(saved_vis)
                # Ensure keys exist for backward compatibility with old saves
                if 'footer_alpha' not in self.visual_settings:
                    self.visual_settings['footer_alpha'] = 0.85
                if 'queue_alpha' not in self.visual_settings:
                    self.visual_settings['queue_alpha'] = 0.96
            except:
                self.visual_settings = default_vis
        else:
            self.visual_settings = default_vis        
        
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
        self.opacity_effect = QGraphicsOpacityEffect(self.bg_label)
        self.bg_label.setGraphicsEffect(self.opacity_effect)
        
        # --- The Crossfade Animation Engine
        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(400) # 400ms for a beautiful smooth blend
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.valueChanged.connect(self._on_fade_step)
        self.fade_anim.finished.connect(self._on_fade_finished)
        self.crossfade_progress = 1.0
        
        self.blur_thread = None 
        self.generic_tooltip = TriangleTooltip(self, show_triangle=False)

        
        try:
            # 1. Instantly apply the last used theme color before the UI builds!
            
            saved_color = self.settings.value('last_master_color')
            if saved_color:
                self.master_color = saved_color

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

            # 4. Ultimate Fallback if settings are empty — stay dark until real art loads
            if not started_bg:
                self.bg_label.setStyleSheet("background-color: #080808;")

        except Exception as e:
            print(f"Startup restore error: {e}")

        # Continue with standard init
        self.enable_dark_title_bar()
        self.refresh_ui_styles()
        self.audio_engine.set_volume(self.last_volume)
        self.load_playlist()
        self.refresh_ui_styles()

        QTimer.singleShot(100, self.test_navidrome_fetch)
        QTimer.singleShot(0, self.reposition_nav_buttons)
        if self.static_bg_path:
            QTimer.singleShot(50, self.apply_static_background)

        # --- Background downloader for playlist covers ---
        self.playlist_cover_worker = PlaylistCoverWorker(None)
        self.playlist_cover_worker.cover_downloaded.connect(self.tree.viewport().update)
        
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
        self._resize_debounce.timeout.connect(self._apply_bg_scale)

        # Pre-load the QMovie once — the GIF decoder is the expensive part.
        # QLabel and QGraphicsColorizeEffect are recreated per-call in update_indicator()
        # because Qt takes ownership of any widget passed to setItemWidget() and deletes
        # it (along with its child effect) when the item is removed or the tree is cleared,
        # which would leave _pi_effect as a dangling C++ pointer on the next track change.
        from PyQt6.QtGui import QMovie as _QMovie
        self._pi_movie = _QMovie(resource_path("img/playing.gif"))
        self._pi_movie.setScaledSize(QSize(40, 40))

        # --- END of INIT ---

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)

            if hasattr(self, 'audio_engine'):
                self.audio_engine.set_visualizer_active(not minimized)
            if hasattr(self, 'visualizer'):
                self.visualizer.visualizer_enabled = not minimized

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
        
        self.bg_label_old = QLabel(self)
        self.bg_label_old.setScaledContents(True)
        self.bg_label_old.resize(self.size())
        self.bg_label_old.setStyleSheet("background-color: #080808;")

        self.bg_label = QLabel(self)
        self.bg_label.setScaledContents(True)
        self.bg_label.resize(self.size())
        self.bg_label.setStyleSheet("background-color: transparent;")
        
        self.bg_label.lower()
        self.bg_label_old.lower()
        
        central_widget = QWidget()
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 30, 0, 0)
        main_layout.setSpacing(0)

        content = QHBoxLayout()
        content.setContentsMargins(40, 0, 8, 0)
        content.setSpacing(0)

        self._splitter = _GripSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(16)
        self._splitter.setChildrenCollapsible(False)
        # Queue panel (index 2) will be shown/hidden via setVisible, not collapsed

        # --- LEFT PANEL (Art / Visualizer / Track Info — three equal sections) ---
        _left_widget = QWidget()
        left_panel = QVBoxLayout(_left_widget)
        left_panel.setContentsMargins(0, 0, 6, 0)
        left_panel.setSpacing(0)
        left_panel.addSpacing(36)

        # Section 1: Album art (50%)
        self.art_container = SquareArtContainer(self)
        self._art_section = _SectionWidget(self.art_container, 'art', self)
        left_panel.addWidget(self._art_section, 2)

        # Section 2: Visualizer (25%)
        self.visualizer = AudioVisualizer(self.audio_engine)
        self.visualizer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._vis_section = _SectionWidget(self.visualizer, 'vis', self)
        left_panel.addWidget(self._vis_section, 1)

        # Section 3: Track info (25%)
        text_info_container = QWidget()
        text_info_layout = QVBoxLayout(text_info_container)
        text_info_layout.setContentsMargins(0, 15, 0, 0)
        text_info_layout.setSpacing(6)

        self.track_title = QLabel("")
        self.track_title.setWordWrap(True)
        self.track_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.track_title.setStyleSheet("font-size: 28px; font-weight: bold; color: white;")
        text_info_layout.addWidget(self.track_title)

        self.track_artist = QLabel("")
        self.track_artist.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.track_artist.setStyleSheet("font-size: 18px; color: #888;")
        text_info_layout.addWidget(self.track_artist)

        self.file_type_label = QLabel("")
        self.file_type_label.setStyleSheet("font-size: 11px; color: #888; font-weight: 600;")
        text_info_layout.addWidget(self.file_type_label)

        self.heart_btn = QPushButton()
        self.heart_btn.setFixedSize(24, 24)
        self.heart_btn.setFlat(True)
        self.heart_btn.setIconSize(QSize(18, 18))
        self.heart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.heart_btn.setStyleSheet("background: transparent; border: none;")
        self.heart_btn.clicked.connect(self._toggle_now_playing_favorite)
        text_info_layout.addWidget(self.heart_btn)
        text_info_layout.addStretch(1)

        self._info_section = _SectionWidget(text_info_container, 'info', self)
        left_panel.addWidget(self._info_section, 1)

        # Restore section visibility (deferred so layout has computed heights first)
        for sec, key in [(self._art_section, 'art'), (self._vis_section, 'vis'), (self._info_section, 'info')]:
            if not int(self.settings.value(f'section_{key}_visible', 1)):
                QTimer.singleShot(0, sec._toggle)
        
        # --- RIGHT PANEL (Tabs & Search) ---

        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)

        
        self.nav_container = QWidget()
        nav_layout = QHBoxLayout(self.nav_container)
        
        
        nav_layout.setContentsMargins(5, 0, 15, 9) 
        nav_layout.setSpacing(4)
        
        
        nav_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.btn_back.setFixedSize(36, 28)
        self.btn_fwd.setFixedSize(36, 28)
        self.btn_back.setToolTip("Go Back")
        self.btn_fwd.setToolTip("Go Forward")
        
        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_fwd)
        
        
        self.tabs.setCornerWidget(self.nav_container, Qt.Corner.TopLeftCorner)
        
        # 1. Home
        self.home_tab = HomeView(None) 
        self.home_tab.play_album.connect(self.play_whole_album)
        self.tabs.addTab(self.home_tab, "Home")
        self.home_tab.artist_clicked.connect(self.navigate_to_artist)

        # 2. Now Playing
        self._now_playing_panel = NowPlayingPanel(self)
        self.tree = self._now_playing_panel.tree  # all self.tree.* refs keep working
        self.tree.sig_drag_started.connect(self.show_ghost_drag)
        self.tree.sig_drag_moved.connect(self.move_ghost_drag)
        self.tree.sig_drag_ended.connect(self.ghost_label.hide)
        self.tree.itemPressed.connect(lambda: self.setFocus())
        self.tree.orderChanged.connect(self.sync_data_after_drag)
        self.tree.header().sectionResized.connect(self.enforce_artist_min_width)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.tree.itemClicked.connect(self.on_queue_item_clicked)
        self._now_playing_panel.load_column_state()
        self.tabs.addTab(self._now_playing_panel, "Now Playing")

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
        if hasattr(self, 'master_color'): self.album_browser.set_accent_color(self.master_color)
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
        if hasattr(self, 'master_color'): self.artist_browser.set_accent_color(self.master_color)
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
        if hasattr(self, 'master_color'): self.playlists_browser.set_accent_color(self.master_color)
        self.tabs.addTab(self.playlists_browser, "Playlists")


        # 7. THE HIDDEN GLOBAL ALBUM TAB!
        from albums_browser import AlbumDetailView
        self.global_album_view = AlbumDetailView(None)
        
        self.global_album_view.track_list.play_track.connect(self.add_and_play_from_browser)
        self.global_album_view.track_list.play_multiple_tracks.connect(self.play_whole_album)
        self.global_album_view.track_list.queue_track.connect(self.add_track_to_queue)
        self.global_album_view.track_list.play_next.connect(self.play_track_next)
        self.global_album_view.track_list.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        
        self.global_album_view.play_clicked.connect(self.play_global_album)
        self.global_album_view.shuffle_clicked.connect(self.shuffle_global_album)
        self.global_album_view.album_favorite_toggled.connect(self.toggle_global_fav)
        self.global_album_view.artist_clicked.connect(lambda name: self.navigate_to_artist(name))
        
        self.tabs.addTab(self.global_album_view, "")
        self.global_album_tab_idx = self.tabs.count() - 1
        self.tabs.tabBar().setTabVisible(self.global_album_tab_idx, False) 
        self.tabs.tabBarClicked.connect(self.on_tab_bar_clicked)

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
        
        # --- SIGNAL CONNECTIONS ---
        self.home_tab.album_clicked.connect(lambda data: self.navigate_to_album(data))
        self.album_browser.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        self.artist_browser.switch_to_album_tab.connect(lambda data: self.navigate_to_album(data))
        self._now_playing_panel.artist_clicked.connect(lambda name: self.navigate_to_artist(name))
        self._now_playing_panel.album_clicked.connect(lambda data: self.navigate_to_album(data))
        self.tabs.currentChanged.connect(self.on_tab_changed_global)
        

        # Enable drag reordering
        self.tabs.tabBar().setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self._tab_move_in_progress = False
        self._restore_tab_order()

        # Initialize History with Home
        self.add_global_nav(self.tabs.indexOf(self.home_tab), 'home')

        # --- RIGHT PANEL (Tabs) ---
        _right_widget = QWidget()
        right_panel = QVBoxLayout(_right_widget)
        right_panel.setContentsMargins(6, 0, 0, 0)
        right_panel.setSpacing(0)
        right_panel.addWidget(self.tabs)

        self._splitter.addWidget(_left_widget)
        self._splitter.addWidget(_right_widget)
        self._splitter.setStretchFactor(0, 35)
        self._splitter.setStretchFactor(1, 65)

        content.addWidget(self._splitter)
        main_layout.addLayout(content, 1)
        main_layout.addSpacing(8)

        # =========================================================
        # PLAYER CONTROLS & FOOTER
        # =========================================================

        self.import_btn = QPushButton("")
        self.import_btn.setFixedSize(40, 40)
        self.import_btn.setIconSize(QSize(20, 20))
        self.import_btn.clicked.connect(self.import_music)
        self.import_btn.setToolTip("Add Music")

        self.settings_btn = QPushButton("")
        self.settings_btn.setFixedSize(40, 40)
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.clicked.connect(self.open_settings)
        self.settings_btn.setToolTip("Settings")

        self.btn_shuffle = StatusButton("")
        self.btn_shuffle.setCheckable(True)
        self.btn_shuffle.setFixedSize(40, 40)
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        self.btn_shuffle.setToolTip("Shuffle")

        self.btn_prev = QPushButton("")
        self.btn_prev.setFixedSize(50, 50)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_prev.setToolTip("Previous Track")

        self.btn_play = QPushButton("")
        self.btn_play.setFixedSize(65, 65)
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
        self.vol_slider.setFixedWidth(130)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.last_volume)
        self.vol_slider.valueChanged.connect(self.update_volume)
        self.vol_slider.sliderMoved.connect(self.vol_slider.update_tooltip_pos)

        self.vol_icon_label = ClickableLabel(main_window=self)
        self.vol_icon_label.setFixedSize(24, 24)
        self.vol_icon_label.setToolTip("Mute/Unmute")
        self.vol_icon_label.setCursor(Qt.CursorShape.PointingHandCursor)

        self.current_time_label = QLabel("0:00")
        
        # SWAP THE SLIDER FOR THE WAVEFORM
        self.seek_bar = WaveformScrubber(master_color=self.master_color, parent=self)
        self.seek_bar.seek_requested.connect(self.on_waveform_seek)
        self.seek_bar.mode_toggled.connect(self.on_waveform_toggled)
        
        # THE SCRATCH CONNECTION (Wire physics straight to C++)
        self.seek_bar.scratch_mode_changed.connect(self.audio_engine.set_scratch_mode)
        self.seek_bar.velocity_changed.connect(self.audio_engine.set_scratch_velocity)
        
        # THE UI CONNECTION (Update the time text while scrubbing)
        self.seek_bar.position_updated.connect(
            lambda ms: self.current_time_label.setText(self.format_time(ms)) if hasattr(self, 'current_time_label') else None
        )

        saved_mode = int(self.settings.value('waveform_mode', 0))
        if saved_mode in (1, 2):
            self.seek_bar.display_mode = saved_mode
            self.seek_bar.render_timer.stop()
        
        self.total_time_label = QLabel("0:00")


        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(20)
        self.controls_layout.addStretch()
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

        self.footer_container = QWidget()
        self.footer_container.setObjectName("FooterBar")
        self.footer_container.setStyleSheet("QWidget#FooterBar { background-color: rgba(11, 11, 11, 0.75); border-top: 1px solid rgba(255, 255, 255, 0.1); }")

        main_footer_layout = QHBoxLayout(self.footer_container)
        main_footer_layout.setContentsMargins(20, 0, 20, 0)
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
        self.now_playing_widget.art_clicked.connect(self._toggle_queue_panel)
        left_layout.addWidget(self.now_playing_widget)
        
        footer_center = QWidget()
        center_layout = QVBoxLayout(footer_center)
        center_layout.setContentsMargins(10, 10, 10, 10)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addLayout(self.controls_layout)
        center_layout.addLayout(self.slider_layout)
        
        footer_right = QWidget()
        right_layout = QHBoxLayout(footer_right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        right_layout.addWidget(self.import_btn)
        right_layout.addWidget(self.settings_btn)
        right_layout.addSpacing(10)
        right_layout.addWidget(self.vol_icon_label)
        right_layout.addWidget(self.vol_slider)
        
        main_footer_layout.addWidget(footer_left, 1)
        main_footer_layout.addWidget(footer_center, 2)
        main_footer_layout.addWidget(footer_right, 1)

        main_layout.addWidget(self.footer_container)

        # --- FLOATING QUEUE PANEL (overlay, anchored above footer) ---
        self._queue_panel = QueuePanel(self)
        self._queue_panel.play_index.connect(self._queue_play_at)
        self._queue_panel.play_next_index.connect(self._queue_play_next_at)
        self._queue_panel.remove_index.connect(self._queue_remove_at)
        self._queue_panel.close_requested.connect(self._toggle_queue_panel)
        self._queue_panel.artist_clicked.connect(self.navigate_to_artist)
        self._queue_panel.favorite_toggled.connect(self._queue_toggle_favorite)
        self._queue_panel.hide()
        if self.settings.value('queue_panel_visible', 'false') == 'true':
            QTimer.singleShot(0, lambda: (self._queue_panel.show(),
                                          self._reposition_queue_panel(),
                                          self._queue_panel.raise_()))

        # --- FINAL SETUPS ---
        # Context menu is handled by NowPlayingPanel._show_track_context_menu
        for w in [self.import_btn, self.settings_btn, self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat, self.vol_slider, self.seek_bar, self.vol_icon_label, self.btn_back, self.btn_fwd]:
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
        self.tracks_browser.switch_to_artist_tab.connect(lambda name: self.navigate_to_artist(name))
        self.tracks_browser.switch_to_album_tab.connect(lambda data: self.navigate_to_album(data))
        self.audio_engine.waveform_generated.connect(self.seek_bar.set_real_samples)

    # ── Queue panel helpers ───────────────────────────────────────────────────

    def _toggle_queue_panel(self):
        visible = not self._queue_panel.isVisible()
        if visible:
            self._queue_panel.show()
            self._reposition_queue_panel()
            self._queue_panel.raise_()
            self._refresh_queue_panel()
        else:
            self.settings.setValue('queue_panel_height',
                                   self._queue_panel.height())
            self._queue_panel.hide()
        self.settings.setValue('queue_panel_visible', 'true' if visible else 'false')

    def _reposition_queue_panel(self):
        if not hasattr(self, '_queue_panel') or not hasattr(self, 'footer_container'):
            return
        footer_h = self.footer_container.height()
        panel_w  = int(self.settings.value('queue_panel_width', 420))
        panel_h  = int(self.settings.value('queue_panel_height', 420))
        margin_l = 8   # align with left content margin
        margin_b = 8    # gap above footer
        margin_t = 8    # gap below top edge
        max_h = self.height() - footer_h - margin_b - margin_t
        self._queue_panel._MAX_H = max_h
        panel_h  = min(panel_h, max_h)
        panel_h  = max(panel_h, 180)
        x = margin_l
        y = self.height() - footer_h - margin_b - panel_h
        self._queue_panel.setGeometry(x, y, panel_w, panel_h)

    def _refresh_queue_panel(self):
        if not hasattr(self, '_queue_panel'):
            return
        color = getattr(self, 'master_color', '#cccccc')
        self._queue_panel.set_accent_color(color)
        self._queue_panel.refresh(self.playlist_data, self.current_index)

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

    def _queue_toggle_favorite(self, idx: int):
        if not (0 <= idx < len(self.playlist_data)):
            return
        track = self.playlist_data[idx]
        raw = track.get('starred', False)
        current = raw.lower() in ('true', '1') if isinstance(raw, str) else bool(raw)
        new_state = not current
        track['starred'] = new_state
        if idx == self.current_index and hasattr(self, 'heart_btn'):
            accent = getattr(self, 'master_color', '#ffffff')
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
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()
        self._refresh_queue_panel()

