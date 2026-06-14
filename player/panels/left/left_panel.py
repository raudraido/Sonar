"""
left_panel.py — LeftPanel widget for Icoshahedron.

Hosts left_panel.qml (header w/ logo + collapsible album-art section) inside
a single QQuickWidget, driven by LeftPanelBridge. Native back/forward nav
buttons are overlaid as small widgets in the header's top-right corner.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtSlot, QTimer, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtQuick import QQuickView

from player.widgets import AlbumIconProvider, LeftPanelCoverProvider
from player import resource_path


class LeftPanelBridge(QObject):
    """Bridge for left_panel.qml: pushes theme/art state to QML and reports
    the art-section close-button click back to LeftPanel."""

    # → QML
    panelBgChanged             = pyqtSignal(str)
    borderColorChanged         = pyqtSignal(str)
    borderWidthChanged         = pyqtSignal(int)
    accentColorChanged         = pyqtSignal(str)
    logoTintColorChanged       = pyqtSignal(str)
    closeBtnBgChanged          = pyqtSignal(str)
    closeBtnBgHoverChanged     = pyqtSignal(str)
    closeBtnBorderChanged      = pyqtSignal(str)
    closeBtnBorderHoverChanged = pyqtSignal(str)
    artVisibleChanged          = pyqtSignal(bool)
    artTargetSizeChanged       = pyqtSignal(int)
    crossfadeProgressChanged   = pyqtSignal(float)
    currentArtIdChanged        = pyqtSignal(str)
    oldArtIdChanged            = pyqtSignal(str)

    # → Python
    closeArtClicked = pyqtSignal()

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    @pyqtSlot()
    def logoClicked(self):
        self._panel._on_logo_clicked()


class LeftPanel(QWidget):
    """Left sidebar: QML header (logo) + collapsible album-art section."""

    HEADER_HEIGHT = 62

    def __init__(self, main_window, audio_engine, settings, parent=None):
        super().__init__(parent)
        self._main = main_window
        self._settings = settings

        self.setObjectName('LeftPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            '#LeftPanel { background: rgba(14,14,14,0.96); border: none; border-radius: 0px; }'
        )

        self._art_token = 0
        self._art_visible = False
        self._header_widgets = []
        self._panel_bg_hex = '#0e0e0e'

        self._bridge = LeftPanelBridge(self)
        self._cover_provider = LeftPanelCoverProvider()
        self._icon_provider = AlbumIconProvider()

        # QQuickView in a window container renders at the monitor's real
        # refresh rate, unlike QQuickWidget which caps at ~60Hz regardless
        # of display Hz.
        self._qml_view = QQuickView()
        self._qml_view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        # QQuickView defaults to a white clear color — match the panel
        # background to avoid a white flash while the QML loads.
        self._qml_view.setColor(QColor(14, 14, 14))
        self._qml = QWidget.createWindowContainer(self._qml_view, self)
        self._qml.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        engine = self._qml_view.engine()
        engine.addImageProvider("leftpanelcover", self._cover_provider)
        engine.addImageProvider("albumicons", self._icon_provider)

        ctx = self._qml_view.rootContext()
        ctx.setContextProperty("leftPanelBridge", self._bridge)
        ctx.setContextProperty(
            "leftPanelLogoBase",
            QUrl.fromLocalFile(resource_path("img/shahedron2.png")).toString(),
        )

        self._qml_view.setSource(QUrl.fromLocalFile(resource_path("player/panels/left/left_panel.qml")))

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lo.addWidget(self._qml)

        # Triple-click logo (within 500ms) → Theme Builder
        self._click_count = 0
        self._click_timer = QTimer()
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(500)
        self._click_timer.timeout.connect(lambda: setattr(self, '_click_count', 0))

    # ── Header overlay (back/forward nav buttons) ───────────────────────
    def add_header_widget(self, widget):
        widget.setParent(self)
        # createWindowContainer's native child window always paints above
        # regular (non-native) sibling widgets. Promote this overlay widget
        # to a native window too so normal raise_()/z-order applies between
        # it and the QML view's container. Native child windows don't
        # composite translucency, so paint an opaque background matching
        # the QML header color instead.
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        if hasattr(widget, 'set_bg_color'):
            widget.set_bg_color(self._panel_bg_hex)
        widget.show()
        self._header_widgets.append(widget)
        self._reposition_header_widgets()

    def _reposition_header_widgets(self):
        x = self.width() - 8
        for w in reversed(self._header_widgets):
            x -= w.width()
            w.setGeometry(x, (self.HEADER_HEIGHT - w.height()) // 2, w.width(), w.height())
            w.raise_()
            x -= 4

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_header_widgets()

    # ── Logo / theme builder ─────────────────────────────────────────────
    def _on_logo_clicked(self):
        self._click_count += 1
        self._click_timer.start()
        if self._click_count >= 3:
            self._click_count = 0
            self._click_timer.stop()
            self._open_theme_builder()

    def _open_theme_builder(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from player.components.theme_builder import ThemeBuilderDialog
        if getattr(self, '_theme_builder', None) is not None:
            try:
                self._theme_builder.raise_()
                self._theme_builder.activateWindow()
                return
            except RuntimeError:
                self._theme_builder = None
        self._theme_builder = ThemeBuilderDialog(self._main)
        self._main._theme_builder_open = True
        def _on_destroyed():
            self._theme_builder = None
            self._main._theme_builder_open = False
        self._theme_builder.destroyed.connect(_on_destroyed)
        self._theme_builder.show()

    # ── Album art section ────────────────────────────────────────────────
    @property
    def art_visible(self):
        return self._art_visible

    def set_art_visible(self, visible: bool):
        self._art_visible = bool(visible)
        self._bridge.artVisibleChanged.emit(self._art_visible)

    def set_art_target_size(self, px: int):
        self._bridge.artTargetSizeChanged.emit(int(px))

    def set_cover_art(self, pixmap):
        self._cover_provider.set_current_art(pixmap)
        if pixmap is None or pixmap.isNull():
            self._bridge.currentArtIdChanged.emit("")
            return
        self._art_token += 1
        self._bridge.currentArtIdChanged.emit(str(self._art_token))

    def set_old_art(self, pixmap):
        self._cover_provider.set_old_art(pixmap)
        if pixmap is None or pixmap.isNull():
            self._bridge.oldArtIdChanged.emit("")
            return
        self._art_token += 1
        self._bridge.oldArtIdChanged.emit(str(self._art_token))

    def clear_old_art(self):
        self._cover_provider.set_old_art(None)
        self._bridge.oldArtIdChanged.emit("")

    def set_crossfade_progress(self, value: float):
        self._bridge.crossfadeProgressChanged.emit(float(value))

    # ── Theme ─────────────────────────────────────────────────────────────
    def set_master_color(self, color: str):
        self._bridge.logoTintColorChanged.emit(color)
        self._bridge.accentColorChanged.emit(color)

        c = QColor(color)
        r, g, b = c.red(), c.green(), c.blue()
        dr, dg, db = int(r * .3), int(g * .3), int(b * .3)

        self._bridge.closeBtnBgChanged.emit(f"#1a{r:02x}{g:02x}{b:02x}")
        self._bridge.closeBtnBgHoverChanged.emit(f"#66{r:02x}{g:02x}{b:02x}")
        self._bridge.closeBtnBorderChanged.emit(f"#{dr:02x}{dg:02x}{db:02x}")
        self._bridge.closeBtnBorderHoverChanged.emit(f"#{r:02x}{g:02x}{b:02x}")

    def apply_theme(self, theme):
        try:
            r, g, b = (int(x) for x in theme.left_panel_bg.split(','))
            panel_hex = '#{:02x}{:02x}{:02x}'.format(r, g, b)
        except Exception:
            panel_hex = '#0e0e0e'
        self._panel_bg_hex = panel_hex
        self._bridge.panelBgChanged.emit(panel_hex)
        self._bridge.borderColorChanged.emit(self._border_color_to_hex(theme.border_color))
        self._bridge.borderWidthChanged.emit(theme.border_width)
        for w in self._header_widgets:
            if hasattr(w, 'set_bg_color'):
                w.set_bg_color(panel_hex)

    @staticmethod
    def _border_color_to_hex(color_str: str) -> str:
        # QML's color property doesn't parse Qt-stylesheet-style
        # "rgba(r,g,b,a)" with a 0-255 alpha — convert to #AARRGGBB.
        if color_str.startswith('rgba'):
            r, g, b, a = (int(float(x)) for x in color_str[color_str.index('(') + 1:-1].split(','))
            return '#{:02x}{:02x}{:02x}{:02x}'.format(a, r, g, b)
        return color_str
