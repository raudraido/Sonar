"""
left_panel.py — LeftPanel widget for Icosahedron.

Contains the visualizer section, album-art section, and the top header that
mirrors the queue panel header for visual alignment.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSizePolicy, QGraphicsOpacityEffect, QLabel,
)
from PyQt6.QtCore import (
    Qt, QSize, QPropertyAnimation, QEasingCurve, QEvent, QTimer,
)
from PyQt6.QtGui import QPixmap, QColor, QIcon
from PyQt6.QtGui import QPainter as _QPainter

from visualizer import AudioVisualizer
from player.widgets import SquareArtContainer
from player import resource_path


class _SectionWidget(QWidget):
    """Left-panel section — hide/unhide button fades in on hover."""

    def __init__(self, content, key, main_window, parent=None, show_controls=False):
        super().__init__(parent)
        self._content = content
        self._key = key
        self._main = main_window
        self._companion_btn = None

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lo.addWidget(content, 1)

        self._toggle_opacity = None
        self._hover_anim = None

        if show_controls:
            self._btn = QPushButton(self)
            self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._btn.setIconSize(QSize(16, 16))
            self._btn.setFixedSize(28, 28)
            self._btn.clicked.connect(self._toggle)
            self._btn.installEventFilter(self)

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
            self._init_opacity_effect()
            self._btn.hide()
        else:
            self._btn = None

    def _current_dim(self):
        return self._hide_dim if self._content.isVisible() else self._show_dim

    def _current_bright(self):
        return self._hide_bright if self._content.isVisible() else self._show_bright

    def set_master_color(self, mc):
        if self._btn is None:
            return
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

        self._companion_opacity = None
        self._companion_anim = None
        if self._companion_btn is not None:
            self._companion_btn.show()
            self._companion_opacity = QGraphicsOpacityEffect(self._companion_btn)
            self._companion_opacity.setOpacity(0.0)
            self._companion_btn.setGraphicsEffect(self._companion_opacity)
            self._companion_anim = QPropertyAnimation(self._companion_opacity, b"opacity")
            self._companion_anim.setDuration(250)

    def _on_anim_finished(self):
        if self._toggle_opacity and self._toggle_opacity.opacity() == 0.0:
            self._btn.setGraphicsEffect(None)
            self._btn.hide()
            self._toggle_opacity = None
            self._hover_anim = None
            if self._companion_btn is not None:
                self._companion_btn.setGraphicsEffect(None)
                self._companion_btn.hide()
                self._companion_opacity = None
                self._companion_anim = None

    def eventFilter(self, obj, event):
        if obj is self._btn:
            if event.type() == QEvent.Type.Enter:
                self._btn.setIcon(self._current_bright())
            elif event.type() == QEvent.Type.Leave:
                self._btn.setIcon(self._current_dim())
        return super().eventFilter(obj, event)

    def enterEvent(self, event):
        if self._btn is None:
            super().enterEvent(event)
            return
        if self._toggle_opacity is None:
            self._init_opacity_effect()
        self._hover_anim.stop()
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        if self._companion_anim is not None:
            self._companion_anim.stop()
            self._companion_anim.setEndValue(1.0)
            self._companion_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._toggle_opacity is None:
            super().leaveEvent(event)
            return
        self._hover_anim.stop()
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        if self._companion_anim is not None:
            self._companion_anim.stop()
            self._companion_anim.setEndValue(0.0)
            self._companion_anim.start()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._btn is None:
            return
        self._btn.move(self.width() - self._btn.width() - 10, 5)
        if self._companion_btn is not None:
            self._companion_btn.move(self._btn.x() - self._companion_btn.width() - 8, 5)

    def _toggle(self):
        vis = not self._content.isVisible()
        self._content.setVisible(vis)
        self._btn.setIcon(self._current_dim())
        self._btn.setToolTip("Hide" if vis else "Unhide")
        self._main.settings.setValue(f'section_{self._key}_visible', int(vis))
        if self._key == 'vis':
            engine = getattr(self._main, 'audio_engine', None)
            visualizer = getattr(self._main, 'visualizer', None)
            if engine:
                engine.set_visualizer_active(vis)
            if visualizer:
                visualizer.visualizer_enabled = vis
                if not vis:
                    visualizer.vis_data = [0.0] * visualizer.num_bars
                    visualizer.update()


class LeftPanel(QWidget):
    """Left sidebar: fixed-height header + visualizer/art sections."""

    def __init__(self, main_window, audio_engine, settings, parent=None):
        super().__init__(parent)
        self._main = main_window
        self._settings = settings

        self.setObjectName('LeftPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            '#LeftPanel { background: rgba(14,14,14,0.96); border: none; border-radius: 0px; }'
        )

        # Outer layout: header full-width at top, then sections area with margins
        _left_outer = QVBoxLayout(self)
        _left_outer.setContentsMargins(0, 0, 0, 0)
        _left_outer.setSpacing(0)

        # Header — mirrors the queue panel header for visual alignment
        self.header = QWidget()
        self.header.setFixedHeight(62)
        self.header.setStyleSheet(
            'QWidget { background: transparent; border-bottom: 1px solid rgba(255,255,255,0.07); }'
        )
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(8, 0, 8, 0)
        self.header_layout.setSpacing(4)

        _logo_size = 46  # 62px header − 8px top − 8px bottom
        _logo_ctr = QWidget()
        _logo_ctr.setFixedSize(_logo_size, _logo_size)
        _logo_ctr.setStyleSheet("QWidget { border: none; }")
        _logo_ctr.setCursor(Qt.CursorShape.PointingHandCursor)

        self._logo_base = QLabel(_logo_ctr)
        self._logo_base.setGeometry(0, 0, _logo_size, _logo_size)
        _pix_base = QPixmap(resource_path("img/shahedron2.png"))
        if not _pix_base.isNull():
            _pix_base = _pix_base.scaled(
                _logo_size, _logo_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._logo_base.setPixmap(_pix_base)
        self._logo_base.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._logo_base.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._logo_tint = QLabel(_logo_ctr)
        self._logo_tint.setGeometry(0, 0, _logo_size, _logo_size)
        self._logo_tint.setPixmap(self._tint_logo("#fafafa", _logo_size))
        self._logo_tint.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._logo_tint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._logo_tint.raise_()

        self._logo_size = _logo_size
        self._click_count = 0
        self._click_timer = QTimer()
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(500)
        self._click_timer.timeout.connect(lambda: setattr(self, '_click_count', 0))

        def _logo_clicked(e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._click_count += 1
                self._click_timer.start()
                if self._click_count >= 3:
                    self._click_count = 0
                    self._click_timer.stop()
                    self._open_theme_builder()
        _logo_ctr.mousePressEvent = _logo_clicked

        self.header_layout.addWidget(_logo_ctr)
        self.header_layout.addStretch()
        _left_outer.addWidget(self.header)

        # Sections container — keeps 8px margins around art/visualizer
        _left_sections = QWidget()
        _left_sections.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        _left_outer.addWidget(_left_sections, 1)
        _layout = QVBoxLayout(_left_sections)
        _layout.setContentsMargins(8, 8, 8, 8)
        _layout.setSpacing(0)

        # Album art section — collapsed by default
        self.art_container = SquareArtContainer(main_window)
        self.art_section = _SectionWidget(self.art_container, 'art', main_window)
        self.art_section.setMaximumHeight(0)
        self.art_section.setMinimumHeight(0)

        # Art slide animation (height) — driven by window._toggle_sidebar_art
        self.sidebar_art_anim = QPropertyAnimation(self.art_section, b"maximumHeight")
        self.sidebar_art_anim.setDuration(250)
        self.sidebar_art_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.sidebar_art_anim.valueChanged.connect(
            lambda v: self.art_section.setMinimumHeight(int(v))
        )

        # Visualizer
        self.visualizer = AudioVisualizer(audio_engine)
        self.visualizer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.visualizer.setMaximumWidth(16777215)

        vis_container = QWidget()
        vis_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vis_layout = QHBoxLayout(vis_container)
        vis_layout.setContentsMargins(0, 0, 0, 0)
        vis_layout.setSpacing(0)
        vis_layout.addWidget(self.visualizer)

        self.vis_section = _SectionWidget(vis_container, 'vis', main_window, show_controls=True)

        # Wire the visualizer's switch button as the companion to the hide button.
        # Disable AudioVisualizer's own hover management so _SectionWidget owns it.
        btn_vis = self.visualizer.btn_toggle_vis
        btn_vis.setGraphicsEffect(None)
        btn_vis.hide()
        self.visualizer.toggle_opacity = None
        self.visualizer.hover_anim = None
        btn_vis.setParent(self.vis_section)
        btn_vis.raise_()
        self.vis_section._companion_btn = btn_vis
        # Tear down the opacity state created during __init__ so the first
        # enterEvent calls _init_opacity_effect() with the companion already set.
        self.vis_section._btn.setGraphicsEffect(None)
        self.vis_section._btn.hide()
        self.vis_section._toggle_opacity = None
        self.vis_section._hover_anim = None

        # Fixed order: visualizer fills, art slides in at bottom
        _layout.addWidget(self.vis_section, 1)
        _layout.addSpacing(8)
        _layout.addWidget(self.art_section, 0)

        # Restore vis section visibility
        if not int(settings.value('section_vis_visible', 1)):
            QTimer.singleShot(0, self.vis_section._toggle)

    def _tint_logo(self, color: str, size: int | None = None) -> QPixmap:
        sz = size if size is not None else self._logo_size
        raw = QPixmap(resource_path("img/shahedron1.png"))
        if raw.isNull():
            return QPixmap()
        raw = raw.scaled(sz, sz, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(raw.size())
        out.fill(Qt.GlobalColor.transparent)
        p = _QPainter(out)
        p.setRenderHint(_QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, raw)
        p.setCompositionMode(_QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(out.rect(), QColor(color))
        p.end()
        return out

    def _open_theme_builder(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from theme_builder import ThemeBuilderDialog
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

    def set_master_color(self, color: str):
        self._logo_tint.setPixmap(self._tint_logo(color))
