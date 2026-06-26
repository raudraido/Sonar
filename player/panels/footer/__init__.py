"""
FooterPanel — transport bar: playback controls, seek bar (3 waveform display
modes), now-playing info, volume/cast/settings.

QML-hosted (see UI_MANIFEST.md: QMLGridWrapper + QQuickView for real-
refresh-rate rendering, instead of the old QWidget/QPainter WaveformScrubber +
NowPlayingFooterWidget + PlayButton/StatusButton/ClickableSlider). FooterPanel
is the public API surface other code talks to — callers use
`self._footer_panel.set_position_ms(...)` etc., never reach into QML widgets
directly (mirrors player/panels/right/queue_panel.py's QueueBridge pattern).
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtQuickWidgets import QQuickWidget

from player import resource_path
from player.widgets import QMLGridWrapper, AlbumIconProvider, AlbumDetailCoverProvider, _round_pixmap
from player.panels.footer.footer_bridge import FooterBridge, FooterArtProvider


class FooterPanel(QWidget):
    """Transport bar: playback controls, seek bar, now-playing info, volume/cast/settings."""

    # ── Signals: forwarded from FooterBridge slots, window.py connects here ──
    play_clicked          = pyqtSignal()
    prev_clicked           = pyqtSignal()
    next_clicked           = pyqtSignal()
    stop_clicked           = pyqtSignal()
    shuffle_toggled        = pyqtSignal(bool)
    repeat_toggled          = pyqtSignal(bool)
    volume_changed         = pyqtSignal(int)
    mute_clicked            = pyqtSignal()
    seek_requested          = pyqtSignal(int)
    scratch_mode_changed   = pyqtSignal(bool)
    velocity_changed        = pyqtSignal(float)
    position_updated        = pyqtSignal(int)
    mode_toggled             = pyqtSignal(int)
    artist_clicked          = pyqtSignal(str)
    album_clicked            = pyqtSignal()
    title_clicked            = pyqtSignal()
    track_right_clicked     = pyqtSignal(object)
    bpm_adjusted             = pyqtSignal(float)
    expand_art_clicked      = pyqtSignal()
    cast_clicked             = pyqtSignal()
    settings_clicked         = pyqtSignal()

    def __init__(self, window):
        super().__init__()
        self.setObjectName("FooterPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#FooterPanel { background-color: rgba(14, 14, 14, 0.75); "
            "border-top: 1px solid rgba(255, 255, 255, 0.1); }"
        )
        self.setFixedHeight(132)

        self._window = window

        # ── Now-playing / track state ───────────────────────────────────────
        self._current_track = None
        self._current_bpm = None
        self._file_type = None
        self._cover_version = 0

        # ── Waveform state (ported from WaveformScrubber) ───────────────────
        # Scratch-mode rendering itself happens entirely in QML now (see
        # footer_bar.qml's paintScratch) — _samples is just pulled on demand
        # via the bridge's getSamples(), no numpy mirror/buffer needed here.
        self._samples = [0.0] * 5000
        self._has_real_data = False
        self._display_mode = 2
        try:
            saved_mode = int(window.settings.value('waveform_mode', 2))
        except (TypeError, ValueError):
            saved_mode = 2
        if saved_mode in (1, 2):
            # Mirrors the old WaveformScrubber restore behavior: minimal/bars
            # modes are restored, scratch mode is not (never resume straight
            # into the DJ-scratch view on launch).
            self._display_mode = saved_mode
        self._show_remaining = bool(int(window.settings.value('show_remaining_time', 0) or 0))
        self._position_ms = 0
        self._duration_ms = 1
        self._is_playing = False
        self._is_scratching = False

        # ── Volume/mute state ────────────────────────────────────────────────
        self._volume = int(getattr(window, 'last_volume', 100))
        self._muted = False

        # ── Bridge + image providers ─────────────────────────────────────────
        self._bridge = FooterBridge(self)
        # Strong Python refs — addImageProvider doesn't keep one, and a GC'd
        # provider makes the engine fall back to a no-op requestImage()
        # (UI_MANIFEST.md "Image Provider Strong Reference" gotcha).
        self._icon_provider = AlbumIconProvider()
        self._art_provider = FooterArtProvider()
        # Reuses album_detail.qml's blurred-shadow play-button glow ("btn/<hex>")
        # so the footer's play button matches that page's halo exactly.
        self._btn_glow_provider = AlbumDetailCoverProvider()

        # Computed up front and handed to QML as context properties (read by
        # the root item's property *initializers*, not pushed via signal)
        # so the very first frame already shows real theme colors — relying
        # on apply_theme()'s signals alone left a window where QML's
        # hardcoded property defaults (dark grey) could paint first,
        # flashing a near-black box before the real theme color arrives.
        initial = self._compute_theme_values(window.theme)

        self._qml = QMLGridWrapper()
        try:
            r, g, b = (int(x) for x in initial['bg'].split(','))
            self._qml.setClearColor(QColor(r, g, b))
        except Exception:
            self._qml.setClearColor(QColor(14, 14, 14))
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._qml.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        engine = self._qml.engine()
        engine.addImageProvider("footericons", self._icon_provider)
        engine.addImageProvider("footerart", self._art_provider)
        engine.addImageProvider("footerbtnglow", self._btn_glow_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("footerBridge", self._bridge)
        ctx.setContextProperty("initialAccentColor", initial['accent'])
        ctx.setContextProperty("initialPanelBg", initial['bg'])
        ctx.setContextProperty("initialHoverColor", initial['hov'])
        ctx.setContextProperty("initialBorderColor", initial['bc'])
        ctx.setContextProperty("initialBorderWidth", initial['bw'])
        ctx.setContextProperty("initialFontColorPrimary", initial['fc1'])
        ctx.setContextProperty("initialFontColorSecondary", initial['fc2'])
        ctx.setContextProperty("initialFontSizePrimary", initial['fs1'])
        ctx.setContextProperty("initialFontSizeSecondary", initial['fs2'])
        ctx.setContextProperty("initialFontFamily", initial['font_family'])
        self._qml.setSource(QUrl.fromLocalFile(resource_path("player/panels/footer/footer_bar.qml")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qml)

        # Invisible anchor positioned roughly under the QML cast icon (right
        # edge of the bar), so cast_manager.py's device-picker popup
        # (CastDevicePopup.show_near(button)) still has a QWidget to anchor
        # to now that the cast button itself is QML, not a QPushButton.
        self.cast_anchor = QWidget(self)
        self.cast_anchor.setFixedSize(1, 1)

        # Push initial state once QML has loaded.
        self._bridge.volumeChanged.emit(self._volume)
        self._bridge.displayModeChanged.emit(self._display_mode)
        self._bridge.showRemainingChanged.emit(self._show_remaining)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.cast_anchor.move(self.width() - 40, self.height() // 2)

    # ── Playback state ──────────────────────────────────────────────────────
    def set_playing(self, playing: bool):
        self._is_playing = bool(playing)
        self._bridge.isPlayingChanged.emit(self._is_playing)

    @property
    def is_playing(self):
        return self._is_playing

    def set_position_ms(self, ms, hard=False):
        if self._is_scratching:
            return
        self._position_ms = int(ms)
        self._bridge.positionMsChanged.emit(self._position_ms, bool(hard))

    @property
    def position_ms(self):
        return self._position_ms

    def set_duration_ms(self, ms):
        self._duration_ms = int(ms) if ms and ms > 0 else 1
        self._bridge.durationMsChanged.emit(self._duration_ms)

    @property
    def duration_ms(self):
        return self._duration_ms

    @property
    def is_dragging(self):
        return self._is_scratching

    is_spinning_freely = is_dragging  # callers only ever check "is the user actively scrubbing"

    def set_shuffle(self, on: bool):
        self._bridge.shuffleChanged.emit(bool(on))

    def set_repeat(self, on: bool):
        self._bridge.repeatChanged.emit(bool(on))

    def set_cast_connected(self, connected: bool):
        self._bridge.castConnectedChanged.emit(bool(connected))

    def set_sidebar_art_expanded(self, expanded: bool):
        self._bridge.sidebarArtExpandedChanged.emit(bool(expanded))

    # ── Volume / mute ────────────────────────────────────────────────────────
    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        self._volume = max(0, min(100, int(value)))
        self._bridge.volumeChanged.emit(self._volume)

    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        self._bridge.mutedChanged.emit(self._muted)

    # ── Waveform ─────────────────────────────────────────────────────────────
    @property
    def display_mode(self):
        return self._display_mode

    @display_mode.setter
    def display_mode(self, mode):
        self._display_mode = int(mode)
        self._bridge.displayModeChanged.emit(self._display_mode)

    @property
    def has_real_data(self):
        return self._has_real_data

    def reset_waveform(self):
        self._has_real_data = False
        self._samples = [0.0] * 5000
        self._bridge.hasRealDataChanged.emit(False)
        self._bridge.samplesChanged.emit()

    def set_real_samples(self, new_samples):
        if not new_samples:
            return
        self._has_real_data = True
        self._samples = new_samples
        self._bridge.hasRealDataChanged.emit(True)
        self._bridge.samplesChanged.emit()

    def _on_mode_toggled(self, mode):
        self._display_mode = int(mode)
        self._bridge.displayModeChanged.emit(self._display_mode)
        self.mode_toggled.emit(self._display_mode)

    @property
    def show_remaining(self):
        return self._show_remaining

    def _on_remaining_toggled(self, on):
        self._show_remaining = bool(on)
        self._bridge.showRemainingChanged.emit(self._show_remaining)

    # ── Now-playing info ─────────────────────────────────────────────────────
    def set_track(self, track):
        self._current_track = track

    def set_track_info(self, title, artist, album):
        self._bridge.trackInfoChanged.emit(title or "", artist or "", album or "")

    def set_cover(self, pixmap):
        if pixmap and not pixmap.isNull():
            self._art_provider.set_pixmap(_round_pixmap(pixmap, radius=6))
        else:
            self._art_provider.set_pixmap(None)
        self._cover_version += 1
        self._bridge.coverVersionChanged.emit(self._cover_version)

    def set_file_type(self, file_type):
        self._file_type = file_type

    def set_bpm(self, bpm):
        self._current_bpm = bpm
        ft = f" ᛫ {self._file_type}" if self._file_type else ""
        text = f"***.* BPM{ft}" if bpm is None else f"{bpm:.1f} BPM{ft}"
        self._bridge.bpmTextChanged.emit(text)

    def _show_track_context_menu(self):
        if self._current_track:
            self.track_right_clicked.emit(self._current_track)

    def _show_bpm_menu(self):
        if not self._current_bpm or self._current_bpm <= 0:
            return
        from PyQt6.QtGui import QCursor
        from player.widgets import ShadowContextMenu
        from player.mixins.visuals import resolve_menu_hover

        def _fmt(v):
            s = f"{v:.2f}".rstrip('0').rstrip('.')
            return f"{s} BPM"

        theme = getattr(self._window, 'theme', None)
        bg   = getattr(theme, 'main_panel_bg',      '14,14,14')
        bc   = getattr(theme, 'border_color',        '#444444')
        fg   = getattr(theme, 'font_color_primary',  '#dddddd')
        fg2  = getattr(theme, 'font_color_secondary', '#555555')
        hov  = resolve_menu_hover(theme)
        px   = getattr(theme, 'font_size_secondary', 12)
        acc  = getattr(theme, 'accent',              '#cccccc')

        menu = ShadowContextMenu()
        menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)
        for label, mult in [("Half", 0.5), ("2/3", 2/3), ("3/4", 3/4),
                             ("4/3", 4/3), ("3/2", 3/2), ("Double", 2.0)]:
            new_val = self._current_bpm * mult
            menu.add_action(f"{label}  |  {_fmt(new_val)}",
                            callback=lambda v=new_val: self.bpm_adjusted.emit(v))
        menu.exec_at(QCursor.pos(), window=self._window)

    # ── Theming ──────────────────────────────────────────────────────────────
    def set_accent_color(self, color):
        self._bridge.accentColorChanged.emit(color)

    @staticmethod
    def _compute_theme_values(theme):
        """Shared by __init__ (initial QML context properties — avoids a
        startup flash of QML's hardcoded property defaults) and apply_theme
        (live signal-driven updates), so both stay in sync."""
        mc = theme.accent
        if mc.startswith('#') and len(mc) > 7:
            mc = mc[:7]

        from player.mixins.visuals import resolve_menu_hover
        fc1 = getattr(theme, 'font_color_primary',   '#dddddd')
        fc2 = getattr(theme, 'font_color_secondary',  '#777777')
        fs1 = getattr(theme, 'font_size_primary',     15)
        fs2 = getattr(theme, 'font_size_secondary',   12)
        hov = resolve_menu_hover(theme)
        bw  = getattr(theme, 'border_width', 1)
        bg  = getattr(theme, 'footer_panel_bg', '14,14,14')

        # theme.border_color can be a CSS "rgba(r,g,b,a)" string (when "Auto
        # border from accent" is off) — that syntax is only valid inside Qt
        # stylesheets (QSS); QColor(...) and QML's `color` type both reject
        # it and silently fall back to black. Recompute directly from the
        # underlying accent/manual-color fields instead, into a format
        # (#AARRGGBB) both QSS and QML actually parse.
        if getattr(theme, 'auto_border_from_accent', True):
            bc_color = QColor(mc).darker(250)
        else:
            bc_color = QColor(getattr(theme, 'manual_border_color', '#2a2a2a'))
        bc = bc_color.name(QColor.NameFormat.HexArgb)

        return {
            'accent': mc, 'fc1': fc1, 'fc2': fc2, 'fs1': fs1, 'fs2': fs2,
            'hov': hov, 'bw': bw, 'bg': bg, 'bc': bc,
            'font_family': getattr(theme, 'app_font', ''),
        }

    def apply_theme(self, theme):
        v = self._compute_theme_values(theme)

        self.setStyleSheet(
            f"QWidget#FooterPanel {{ background-color: rgb({v['bg']}); border-top: {v['bw']}px solid {v['bc']}; }}"
        )
        try:
            r, g, b = (int(x) for x in v['bg'].split(','))
            self._qml.setClearColor(QColor(r, g, b))
        except Exception:
            pass

        self._bridge.accentColorChanged.emit(v['accent'])
        self._bridge.panelBgChanged.emit(v['bg'])
        self._bridge.hoverColorChanged.emit(v['hov'])
        self._bridge.borderColorChanged.emit(v['bc'])
        self._bridge.borderWidthChanged.emit(v['bw'])
        self._bridge.fontColorPrimaryChanged.emit(v['fc1'])
        self._bridge.fontColorSecondaryChanged.emit(v['fc2'])
        self._bridge.fontSizePrimaryChanged.emit(v['fs1'])
        self._bridge.fontSizeSecondaryChanged.emit(v['fs2'])
        self._bridge.fontFamilyChanged.emit(v['font_family'])
