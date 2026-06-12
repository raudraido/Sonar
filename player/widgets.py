"""
player/widgets.py — Reusable custom Qt widgets.

These are self-contained UI components with no dependency on
the main IcosahedronPlayer window (except SettingsWindow which takes a
parent reference so it can write back to visual_settings).
"""
import os
import re
import time
from version import __version__

from PyQt6.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QSlider, QPushButton, QCheckBox, QApplication, QAbstractButton,
    QMessageBox, QScrollArea, QFrame, QGridLayout, QFileDialog, QGroupBox, QDialog,
    QStyledItemDelegate, QStyle
)
from PyQt6.QtCore import (Qt, QPoint, QRect, QRectF, QSize, pyqtSignal, QSettings, QProcess, QEvent,
                          QPropertyAnimation, QTimer, QVariantAnimation, QEasingCurve,
                          QAbstractListModel, QModelIndex, QObject)
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider
from PyQt6.QtGui import (
    QFont, QFontMetrics, QColor, QMouseEvent, QPainter, QPen,
    QBrush, QPainterPath, QPolygon, QPixmap, QKeySequence, QIcon, QCursor, QImage
)

from audio_engine import AudioEngine
from PyQt6.QtWidgets import QGraphicsDropShadowEffect as _QDSE


class PlayButton(QPushButton):
    """Antialiased ring play/pause button shared by the footer and album detail view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ring_color = QColor('#cccccc')
        self._hover_fill = QColor(255, 255, 255, 30)
        self._glow_color = QColor(0, 0, 0, 0)
        self._hovered    = False
        self._glow_eff   = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_ring_color(self, color: str):
        self._ring_color = QColor(color)
        self.update()

    def set_hover_fill(self, color: 'QColor'):
        self._hover_fill = color
        self.update()

    def set_glow_color(self, color: 'QColor'):
        self._glow_color = color

    def apply_accent(self, color: str, theme=None):
        """Apply master colour to ring, icon, hover fill and glow in one call."""
        from player.mixins.visuals import resolve_active_hover
        self.set_ring_color(color)
        self.set_hover_fill(resolve_active_hover(theme))
        gc = QColor(color); gc.setAlpha(160)
        self.set_glow_color(gc)
        self.setIcon(tint_icon('img/play.png', color))

    def ensure_glow(self):
        if self._glow_eff is None:
            eff = _QDSE(self)
            eff.setOffset(0, 0)
            eff.setBlurRadius(28)
            eff.setColor(QColor(0, 0, 0, 0))
            self.setGraphicsEffect(eff)
            self._glow_eff = eff

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        if self._glow_eff:
            self._glow_eff.setColor(self._glow_color)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        if self._glow_eff:
            self._glow_eff.setColor(QColor(0, 0, 0, 0))
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        m = 2.5
        ellipse = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)

        if self._hovered or self.isDown():
            fill = QColor(self._hover_fill)
            if self.isDown():
                fill.setAlpha(min(255, fill.alpha() + 40))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawEllipse(ellipse)

        pen = QPen(self._ring_color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(ellipse)

        icon = self.icon()
        if not icon.isNull():
            sz  = self.iconSize()
            pix = icon.pixmap(sz)
            x   = (self.width()  - sz.width())  // 2
            y   = (self.height() - sz.height()) // 2
            p.drawPixmap(x, y, pix)
        p.end()

def tint_icon(path: str, color: str) -> 'QIcon':
    """Return a QIcon with the PNG at *path* tinted to *color*."""
    from player import resource_path as _rp
    pix = QPixmap(_rp(path))
    if pix.isNull():
        return QIcon()
    out = QPixmap(pix.size())
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    p.fillRect(out.rect(), QColor(color))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, pix)
    p.end()
    return QIcon(out)

def _round_pixmap(pix: QPixmap, radius: int = 12) -> QPixmap:
    if pix.isNull():
        return pix
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pix.width(), pix.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out

class ElidedLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self._base_color = "white"
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

    def setText(self, text):
        self._full_text = text
        self.update_text()

    def resizeEvent(self, event):
        self.update_text()
        super().resizeEvent(event)

    def update_text(self):
        width = self.width()
        if width <= 0: return
        
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        super().setText(elided)

    def _text_w(self):
        return QFontMetrics(self.font()).horizontalAdvance(super().text())

    def enterEvent(self, event):
        from PyQt6.QtGui import QCursor
        if self.mapFromGlobal(QCursor.pos()).x() <= self._text_w():
            self.setStyleSheet(f"color: {self._base_color}; background: transparent; text-decoration: underline;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(f"color: {self._base_color}; background: transparent; text-decoration: none;")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if event.pos().x() <= self._text_w():
            self.setStyleSheet(f"color: {self._base_color}; background: transparent; text-decoration: underline;")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setStyleSheet(f"color: {self._base_color}; background: transparent; text-decoration: none;")
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.pos().x() <= self._text_w():
            self.clicked.emit()
        super().mousePressEvent(event)

class _ArtLabel(QLabel):
    clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = QColor(255, 255, 255)

    def set_accent_color(self, color_str):
        self._accent = QColor(color_str)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 6, 6)
        p.setClipPath(clip)

        pix = self.pixmap()
        if pix and not pix.isNull():
            scaled = pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        else:
            p.fillRect(self.rect(), QColor("#222"))

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        super().mousePressEvent(event)

class NowPlayingFooterWidget(QWidget):
    artist_clicked = pyqtSignal(str)
    album_clicked = pyqtSignal()
    title_clicked = pyqtSignal()
    art_clicked = pyqtSignal()
    track_right_clicked = pyqtSignal(object)  # emits the current track dict
    expand_art_clicked = pyqtSignal()         # upward arrow on footer art clicked
    bpm_adjusted = pyqtSignal(float)           # emits the new BPM value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_track = None
        self._primary_px = 15
        self._secondary_px = 12
        self._secondary_color = "#777777"

        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setFixedHeight(84)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 1. Tiny Cover Art
        self.art_label = _ArtLabel()
        self.art_label.setFixedHeight(84)
        self.art_label.setMinimumWidth(84)
        self.art_label.setMaximumWidth(84)
        self.art_label.setStyleSheet("background: transparent; border: none;")
        self.art_label.setScaledContents(True)
        self.art_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.art_label.hide()
        self.art_label.clicked.connect(self.art_clicked)
        self.art_label.right_clicked.connect(
            lambda: self.track_right_clicked.emit(self._current_track) if self._current_track else None
        )

        # Expand-to-sidebar button — floats over the art, fades in on hover
        self._expand_btn = QPushButton(self.art_label)
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._expand_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
        )
        self._expand_btn.setIconSize(QSize(16, 16))
        self._expand_btn.move(84 - 24 - 2, 2)
        self._expand_btn.clicked.connect(self.expand_art_clicked)

        from player import resource_path as _rp
        _raw = QPixmap(_rp("img/expand.png"))
        if not _raw.isNull():
            def _tint_pix(pix, color):
                out = QPixmap(pix.size()); out.fill(Qt.GlobalColor.transparent)
                p = QPainter(out); p.drawPixmap(0, 0, pix)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                p.fillRect(out.rect(), QColor(color)); p.end()
                return QIcon(out)
            self._expand_icon_dim    = _tint_pix(_raw, "#515151")
            self._expand_icon_bright = _tint_pix(_raw, "#ffffff")
            self._expand_btn.setIcon(self._expand_icon_dim)
        self._expand_btn.installEventFilter(self)

        self._expand_btn_opacity = QGraphicsOpacityEffect(self._expand_btn)
        self._expand_btn_opacity.setOpacity(0.0)
        self._expand_btn.setGraphicsEffect(self._expand_btn_opacity)
        self._expand_btn_anim = QPropertyAnimation(self._expand_btn_opacity, b"opacity")
        self._expand_btn_anim.setDuration(180)
        self.art_label.installEventFilter(self)
        
        # 2. Text Info Container
        text_container = QWidget()
        text_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        # --- TITLE CHANGE ---
        self.title_lbl = ElidedLabel("")
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title_lbl.clicked.connect(self.title_clicked.emit)
        self.title_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.title_lbl.customContextMenuRequested.connect(
            lambda _: self.track_right_clicked.emit(self._current_track) if self._current_track else None
        )
        f = self.title_lbl.font()
        f.setPixelSize(self._primary_px)
        f.setBold(True)
        self.title_lbl.setFont(f)
        self.title_lbl.setStyleSheet("color: white; background: transparent;")
  
        
        # Artist Container
        self.artist_widget = QWidget()
        self.artist_layout = QHBoxLayout(self.artist_widget)
        self.artist_layout.setContentsMargins(0, 0, 0, 0)
        self.artist_layout.setSpacing(0)
        self.artist_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        # Album
        self.album_lbl = FooterClickableLabel("")
        self.album_lbl.clicked.connect(lambda _: self.album_clicked.emit())

        # BPM
        self._current_bpm = None
        self.bpm_lbl = QLabel("")
        self.bpm_lbl.setStyleSheet(
            f"font-size: {self._secondary_px}px; color: {self._secondary_color}; background: transparent;")
        self.bpm_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bpm_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bpm_lbl.customContextMenuRequested.connect(self._show_bpm_menu)
        self.bpm_lbl.hide()

        text_layout.addWidget(self.title_lbl)
        text_layout.addWidget(self.artist_widget)
        text_layout.addWidget(self.album_lbl)
        text_layout.addWidget(self.bpm_lbl)
        
        layout.addWidget(self.art_label)
        layout.addWidget(text_container, 1)
                
    def update_info(self, title, artist, album):
        self.title_lbl.setText(title)
        
        # Update Artists
        while self.artist_layout.count():
            child = self.artist_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

        parts = re.split(r'( /// | • | / |, | feat\. | Feat\. | vs\. )', artist)

        for part in parts:
            if not part: continue
            if re.match(r'( /// | • | / |, | feat\. | Feat\. | vs\. )', part):
                sep_lbl = QLabel(part)
                sep_lbl.setStyleSheet(
                    f"font-size: {self._secondary_px}px; color: {self._secondary_color}; background: transparent;")
                self.artist_layout.addWidget(sep_lbl)
            else:
                lbl = FooterClickableLabel(part)
                lbl.set_secondary_style(self._secondary_px, self._secondary_color)
                lbl.clicked.connect(self.artist_clicked.emit)
                self.artist_layout.addWidget(lbl)
        
        self.artist_layout.addStretch()

        if album:
            self.album_lbl.setText(album)
            self.album_lbl.show()
        else:
            self.album_lbl.hide()
            
    def set_accent_color(self, color: str):
        self.art_label.set_accent_color(color)
        self.title_lbl._base_color = color
        self.title_lbl.setStyleSheet(
            f"color: {color}; background: transparent;"
        )

    def set_track(self, track):
        self._current_track = track

    def set_cover(self, pixmap):
        if pixmap and not pixmap.isNull():
            self.art_label.setPixmap(pixmap)
            self.art_label.show()
        else:
            self.art_label.clear()
            self.art_label.hide()

    def eventFilter(self, obj, event):
        if obj is self.art_label:
            if event.type() == QEvent.Type.Enter:
                self._expand_btn_anim.stop()
                self._expand_btn_anim.setEndValue(1.0)
                self._expand_btn_anim.start()
            elif event.type() == QEvent.Type.Leave:
                self._expand_btn_anim.stop()
                self._expand_btn_anim.setEndValue(0.0)
                self._expand_btn_anim.start()
        elif obj is self._expand_btn:
            if event.type() == QEvent.Type.Enter:
                if hasattr(self, '_expand_icon_bright'):
                    self._expand_btn.setIcon(self._expand_icon_bright)
            elif event.type() == QEvent.Type.Leave:
                if hasattr(self, '_expand_icon_dim'):
                    self._expand_btn.setIcon(self._expand_icon_dim)
        return super().eventFilter(obj, event)

    def set_expand_btn_direction(self, _up: bool):
        pass  # direction now conveyed by context; icon is expand.png regardless

    def set_expand_btn_style(self, accent: str):
        c = QColor(accent)
        r, g, b = c.red(), c.green(), c.blue()
        dr, dg, db = int(r * .3), int(g * .3), int(b * .3)
        self._expand_btn.setStyleSheet(f"""
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

    def apply_theme(self, theme):
        self._primary_px      = getattr(theme, 'font_size_primary',   15)
        self._secondary_px    = getattr(theme, 'font_size_secondary', 12)
        self._secondary_color = getattr(theme, 'font_color_secondary', '#777777')
        # Title font size
        f = self.title_lbl.font()
        f.setPixelSize(self._primary_px)
        self.title_lbl.setFont(f)
        # Album
        self.album_lbl.set_secondary_style(self._secondary_px, self._secondary_color)
        # BPM row
        self.bpm_lbl.setStyleSheet(
            f"font-size: {self._secondary_px}px; color: {self._secondary_color}; background: transparent;")
        # Artist row — restyle existing children in place
        for child in self.artist_widget.findChildren(QLabel):
            if isinstance(child, FooterClickableLabel):
                child.set_secondary_style(self._secondary_px, self._secondary_color)
            else:
                child.setStyleSheet(
                    f"font-size: {self._secondary_px}px; color: {self._secondary_color}; background: transparent;")

    def set_file_type(self, file_type):
        self._file_type = file_type

    def set_bpm(self, bpm):
        self._current_bpm = bpm
        ft = f" ᛫ {self._file_type}" if getattr(self, '_file_type', None) else ""
        if bpm is None:
            self.bpm_lbl.setText(f"***.* BPM{ft}")
        else:
            self.bpm_lbl.setText(f"{bpm:.1f} BPM{ft}")
        self.bpm_lbl.show()

    def _show_bpm_menu(self):
        if not self._current_bpm or self._current_bpm <= 0:
            return
        from PyQt6.QtGui import QCursor

        def _fmt(v):
            s = f"{v:.2f}".rstrip('0').rstrip('.')
            return f"{s} BPM"

        win    = self.window()
        _theme = getattr(win, 'theme', None)
        from player.mixins.visuals import resolve_menu_hover
        _bg  = getattr(_theme, 'main_panel_bg',      '14,14,14')
        _bc  = getattr(_theme, 'border_color',        '#444444')
        _fg  = getattr(_theme, 'font_color_primary',  '#dddddd')
        _fg2 = getattr(_theme, 'font_color_secondary','#555555')
        _hov = resolve_menu_hover(_theme)
        _px  = getattr(_theme, 'font_size_secondary', 12)
        _acc = getattr(_theme, 'accent',              '#cccccc')

        menu = ShadowContextMenu()
        menu.configure(_bg, _bc, _fg, _fg2, _hov, _px, accent=_acc)
        for label, mult in [("Half", 0.5), ("2/3", 2/3), ("3/4", 3/4),
                             ("4/3", 4/3), ("3/2", 3/2), ("Double", 2.0)]:
            new_val = self._current_bpm * mult
            menu.add_action(f"{label}  |  {_fmt(new_val)}",
                            callback=lambda v=new_val: self.bpm_adjusted.emit(v))
        menu.exec_at(QCursor.pos(), window=win)

class FooterClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.setMouseTracking(True)
        self._sec_px = 13
        self._sec_color = "#bbbbbb"
        self._apply_normal()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_secondary_style(self, px: int, color: str):
        self._sec_px = px
        self._sec_color = color
        self._apply_normal()

    def _apply_normal(self):
        self.setStyleSheet(
            f"font-size: {self._sec_px}px; color: {self._sec_color}; background: transparent; text-decoration: none;")

    def _apply_hover(self):
        self.setStyleSheet(
            f"font-size: {self._sec_px}px; color: {self._sec_color}; background: transparent; text-decoration: underline;")

    def _text_w(self):
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    def enterEvent(self, event):
        from PyQt6.QtGui import QCursor
        if self.mapFromGlobal(QCursor.pos()).x() <= self._text_w():
            self._apply_hover()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_normal()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if event.pos().x() <= self._text_w():
            self._apply_hover()
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self._apply_normal()
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.pos().x() <= self._text_w():
            self.clicked.emit(self.text())

class TriangleTooltip(QWidget):
    """Tooltip — transparent outer window + styled inner label + drop shadow."""

    # shadow margin so drop shadow fits inside the transparent window
    _SH = 16

    def __init__(self, parent=None, show_triangle=True):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.show_triangle = show_triangle
        self.hide()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        from PyQt6.QtWidgets import QHBoxLayout, QLabel, QGraphicsDropShadowEffect
        lay = QHBoxLayout(self)
        sh = self._SH
        lay.setContentsMargins(sh, sh, sh, sh)
        lay.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self._lbl)

        # Shadow on the inner label — stays within the transparent outer window
        eff = QGraphicsDropShadowEffect(self._lbl)
        eff.setBlurRadius(12)
        eff.setOffset(0, 4)
        eff.setColor(QColor(0, 0, 0, 153))   # rgba(0,0,0,0.6) — psysonic exact
        self._lbl.setGraphicsEffect(eff)

    @staticmethod
    def _get_theme():
        from PyQt6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            t = getattr(w, 'theme', None)
            if t: return t
        return None

    def text(self):
        return self._lbl.text()

    def setText(self, text):
        self._lbl.setText(text)

    def _apply_theme(self):
        t = self._get_theme()
        fg = getattr(t, 'font_color_secondary', '#999999') if t else '#999999'
        px = getattr(t, 'font_size_primary',    14)        if t else 14
        bg = getattr(t, 'main_panel_bg',  '20,20,20')  if t else '20,20,20'
        if t and getattr(t, 'auto_border_from_accent', True):
            bc = QColor(getattr(t, 'accent', '#cccccc')).darker(250).name()
        else:
            bc = getattr(t, 'manual_border_color', '#2a2a2a') if t else '#2a2a2a'
        self._lbl.setStyleSheet(
            f'color: {fg}; font-size: {px}px; background: rgb({bg});'
            f' border: 1px solid {bc}; border-radius: 6px;'
        )

    def show(self):
        self._apply_theme()
        super().show()

    def show_at(self, pos, text):
        self._lbl.setText(text)
        self._apply_theme()
        self.adjustSize()
        self.move(pos)
        super().show()
        self.raise_()

class ClickableSlider(QSlider):
    def __init__(self, orientation, parent=None, is_volume=False):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.is_volume = is_volume
        self.custom_tip = TriangleTooltip(self.window() if self.window() else parent)
        self.custom_tip.hide()

    def get_value_from_mouse_event(self, event):
        val_range = self.maximum() - self.minimum()
        if val_range <= 0: return self.minimum()
        handle_width = 14
        padding = handle_width // 2  
        groove_width = self.width() - handle_width
        if groove_width <= 0: return self.minimum()
        mouse_x = event.position().x()
        x = mouse_x - padding
        x = max(0, min(x, groove_width))
        ratio = x / groove_width
        new_val = self.minimum() + (val_range * ratio)
        return int(new_val)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            new_val = self.get_value_from_mouse_event(event)
            self.setValue(new_val)
            self.update_tooltip_pos()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton:
            new_val = self.get_value_from_mouse_event(event)
            self.setValue(new_val)
            self.update_tooltip_pos()
        super().mouseMoveEvent(event)

    def update_tooltip_pos(self):
        if self.maximum() <= 0: return
        val_range = self.maximum() - self.minimum()
        if val_range == 0: ratio = 0
        else: ratio = (self.value() - self.minimum()) / val_range
        handle_width = 14
        padding = handle_width // 2
        groove_width = self.width() - handle_width
        handle_center_x = padding + int(ratio * groove_width)
        time_str = f"{self.value()}%" if self.is_volume else (self.window().format_time(self.value()) if hasattr(self.window(), 'format_time') else "0:00")
        self.custom_tip.setText(time_str)
        self.custom_tip.adjustSize() 
        global_pos = self.mapToGlobal(QPoint(handle_center_x, 0))
        centered_x = global_pos.x() - (self.custom_tip.width() // 2)
        self.custom_tip.move(centered_x, global_pos.y() - 45)
        if self.underMouse() or (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            if not self.custom_tip.isVisible(): self.custom_tip.show()
        else: self.custom_tip.hide()

    def enterEvent(self, event): 
        self.update_tooltip_pos()
        super().enterEvent(event)
    
    def leaveEvent(self, event): 
        self.custom_tip.hide()
        super().leaveEvent(event)

class ClickableLabel(QLabel):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent); self.main_window = main_window; self.setMouseTracking(True) 

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.main_window: self.main_window.toggle_mute()

class KeyCaptureButton(QPushButton):
    """A button that captures the next keypress and emits it as a string."""
    key_captured = pyqtSignal(str)

    def __init__(self, key_str, parent=None):
        super().__init__(parent)
        self._current_key = key_str
        self._capturing = False
        self.setText(key_str or "—")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start_capture)
        self._apply_style(False)

    def set_key(self, key_str):
        self._current_key = key_str
        self.setText(key_str or "—")

    def _apply_style(self, capturing):
        if capturing:
            self.setStyleSheet(
                "QPushButton { background: #0d1a33; color: #7eb8ff; border: 1px solid #7eb8ff; "
                "border-radius: 4px; padding: 2px 8px; font-size: 11px; min-width: 120px; font-family: monospace; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: #252525; color: #ddd; border: 1px solid #3a3a3a; "
                "border-radius: 4px; padding: 2px 8px; font-size: 11px; min-width: 120px; font-family: monospace; } "
                "QPushButton:hover { border-color: #777; color: white; }"
            )

    def _start_capture(self):
        self._capturing = True
        self.setText("▶  Press a key…")
        self._apply_style(True)
        self.setFocus()

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_unknown):
            return
        if key == Qt.Key.Key_Escape:
            self._capturing = False
            self.setText(self._current_key or "—")
            self._apply_style(False)
            event.accept()
            return
        key_str = QKeySequence(event.keyCombination()).toString()
        if key_str:
            self._current_key = key_str
            self.setText(key_str)
            self.key_captured.emit(key_str)
        self._capturing = False
        self._apply_style(False)
        event.accept()

    def focusOutEvent(self, event):
        if self._capturing:
            self._capturing = False
            self.setText(self._current_key or "—")
            self._apply_style(False)
        super().focusOutEvent(event)

class _DimOverlay(QWidget):
    """Full-window semi-transparent tint overlay, same animation as spotlight search."""
    _TARGET_ALPHA = 200
    _STEPS        = 10
    _INTERVAL_MS  = 16

    def __init__(self, parent):
        super().__init__(parent,
                         Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self._alpha = 0.0
        self._step  = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._alpha += self._step
        if self._step > 0 and self._alpha >= self._TARGET_ALPHA:
            self._alpha = self._TARGET_ALPHA
            self._timer.stop()
        elif self._step < 0 and self._alpha <= 0:
            self._alpha = 0
            self._timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, int(self._alpha)))
        p.end()

    def fade_in(self):
        self._refit()
        self.raise_()
        self.show()
        self._step = self._TARGET_ALPHA / self._STEPS
        self._timer.start()

    def fade_out(self):
        self._step = -(self._TARGET_ALPHA / self._STEPS)
        self._timer.start()

    def _refit(self):
        p = self.parent()
        if p:
            win = p if p.isWindow() else p.window()
            self.setGeometry(win.geometry())

class SettingsWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(900)
        self._seps = []
        self._hotkey_desc_lbls = []
        self.setStyleSheet("color: #ddd; font-family: sans-serif;")
        self._drag_pos = None

        # Themed frame — same approach as TrackInfoDialog
        theme = getattr(parent, 'theme', None)
        bg  = getattr(theme, 'main_panel_bg',       '24,24,24') if theme else '24,24,24'
        bc  = getattr(theme, 'border_color',         '#2a2a2a') if theme else '#2a2a2a'
        fc1 = getattr(theme, 'font_color_primary',   '#dddddd') if theme else '#dddddd'
        fc2 = getattr(theme, 'font_color_secondary', '#777777') if theme else '#777777'
        acc = getattr(theme, 'accent',               '#ffffff') if theme else '#ffffff'

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.bg = QFrame()
        self.bg.setObjectName("settingsBg")
        self.bg.setStyleSheet(f"""
            QFrame#settingsBg {{
                background-color: rgb({bg});
                border: 1px solid {bc};
                border-radius: 10px;
            }}
        """)
        root_layout.addWidget(self.bg)

        _scroll_ss = (
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        outer = QHBoxLayout(self.bg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(1)

        # ── Left column ───────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(_scroll_ss)
        outer.addWidget(self._scroll, 1)

        _left = QWidget()
        _left.setStyleSheet("background: transparent;")
        self._scroll.setWidget(_left)
        layout = QVBoxLayout(_left)
        layout.setSpacing(15)
        layout.setContentsMargins(16, 16, 12, 16)

        # ── Right column ──────────────────────────────────────────────────
        self._sep_line_v = QFrame()
        self._sep_line_v.setFrameShape(QFrame.Shape.VLine)
        self._sep_line_v.setStyleSheet(f"color: {bc};")
        self._seps.append(self._sep_line_v)
        outer.addWidget(self._sep_line_v)

        self._scroll_right = QScrollArea()
        self._scroll_right.setWidgetResizable(True)
        self._scroll_right.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_right.setStyleSheet(_scroll_ss)
        outer.addWidget(self._scroll_right, 1)

        _right = QWidget()
        _right.setStyleSheet("background: transparent;")
        self._scroll_right.setWidget(_right)
        rlayout = QVBoxLayout(_right)
        rlayout.setSpacing(15)
        rlayout.setContentsMargins(12, 16, 16, 16)

        # ── Logo + version header ─────────────────────────────────────────
        from player import resource_path
        header = QHBoxLayout()
        header.setSpacing(10)
        header.setContentsMargins(0, 4, 0, 4)

        _logo_size = 62
        _logo_ctr = QWidget()
        _logo_ctr.setFixedSize(_logo_size, _logo_size)
        _logo_ctr.setStyleSheet("QWidget { border: none; background: transparent; }")

        self._settings_logo_base = QLabel(_logo_ctr)
        self._settings_logo_base.setGeometry(0, 0, _logo_size, _logo_size)
        _pix_base = QPixmap(resource_path("img/shahedron2.png"))
        if not _pix_base.isNull():
            _pix_base = _pix_base.scaled(_logo_size, _logo_size,
                                          Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
        self._settings_logo_base.setPixmap(_pix_base)
        self._settings_logo_base.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._settings_logo_tint = QLabel(_logo_ctr)
        self._settings_logo_tint.setGeometry(0, 0, _logo_size, _logo_size)
        self._settings_logo_tint.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._settings_logo_tint.setPixmap(self._tint_logo(acc, _logo_size))
        self._settings_logo_tint.raise_()

        header.addWidget(_logo_ctr)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._name_lbl = QLabel("Icosahedron")
        self._name_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {fc1}; background: transparent;")
        self._ver_lbl = QLabel(f"v{__version__}")
        self._ver_lbl.setStyleSheet(f"font-size: 11px; color: {fc2}; background: transparent;")
        title_col.addWidget(self._name_lbl)
        title_col.addWidget(self._ver_lbl)
        header.addLayout(title_col)
        header.addStretch()
        layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {bc};")
        self._seps.append(sep)
        layout.addWidget(sep)

        from player.theme import load_presets
        PRESETS = load_presets()
        self._preset_label = QLabel("Preset")
        self._preset_label.setStyleSheet(f"color: {fc2}; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(self._preset_label)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self._preset_btns = {}
        active_name = getattr(self.parent.theme, 'name', '')
        for preset_name in PRESETS:
            pb = QPushButton(preset_name)
            pb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._preset_btns[preset_name] = pb
            pb.clicked.connect(lambda _=False, n=preset_name: self._apply_preset(n))
            preset_row.addWidget(pb)
        self._acc = acc
        self._bc = bc
        self._fc1 = fc1
        self._fc2 = fc2
        self._refresh_preset_buttons(active_name)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        sep_preset = QFrame()
        sep_preset.setFrameShape(QFrame.Shape.HLine)
        sep_preset.setStyleSheet(f"color: {bc};")
        self._seps.append(sep_preset)
        layout.addWidget(sep_preset)

        self._lyrics_label = QLabel("Lyrics Sources")
        self._lyrics_label.setStyleSheet(f"color: {fc2}; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(self._lyrics_label)

        from lyrics_panel import SOURCES as _LYRIC_SOURCES, SETTINGS_KEY as _LYRIC_KEY
        _s = QSettings('Icosahedron', 'Icosahedron')
        _enabled = list(_s.value(_LYRIC_KEY, _LYRIC_SOURCES) or _LYRIC_SOURCES)
        self._lyrics_source_checks = {}
        for src in _LYRIC_SOURCES:
            cb = QCheckBox(src)
            cb.setStyleSheet(f"color: {fc1}; background: transparent;")
            cb.setChecked(src in _enabled)
            cb.stateChanged.connect(self._save_lyrics_sources)
            layout.addWidget(cb)
            self._lyrics_source_checks[src] = cb

        layout.addStretch()

        # ── Right column: hotkeys + logout ────────────────────────────────
        if hasattr(self.parent, 'hotkey_manager'):
            from hotkeys import DEFAULT_HOTKEYS

            self._hotkeys_label = QLabel("HOTKEYS")
            self._hotkeys_label.setStyleSheet(f"color: {fc2}; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
            rlayout.addWidget(self._hotkeys_label)

            _hk_widget = QWidget()
            _hk_widget.setStyleSheet("background: transparent;")
            grid = QGridLayout(_hk_widget)
            grid.setContentsMargins(0, 0, 6, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(0, 1)

            self._key_buttons = {}

            for row, (hid, desc, _default) in enumerate(DEFAULT_HOTKEYS):
                lbl = QLabel(desc)
                lbl.setStyleSheet(f"color: {fc1}; font-size: 12px; background: transparent;")
                self._hotkey_desc_lbls.append(lbl)

                btn = KeyCaptureButton(self.parent.hotkey_manager.get(hid))
                btn.key_captured.connect(lambda key, h=hid: self._on_key_captured(h, key))

                reset_btn = QPushButton("↺")
                reset_btn.setFixedSize(26, 26)
                reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                reset_btn.setToolTip("Reset to default")
                reset_btn.setStyleSheet(
                    "QPushButton { background: #2a2a2a; color: #666; border: 1px solid #3a3a3a; "
                    "border-radius: 4px; font-size: 13px; } "
                    "QPushButton:hover { color: #fff; border-color: #666; }"
                )
                reset_btn.clicked.connect(lambda _, h=hid: self._on_reset(h))

                grid.addWidget(lbl,       row, 0)
                grid.addWidget(btn,       row, 1)
                grid.addWidget(reset_btn, row, 2)
                self._key_buttons[hid] = btn

            reset_all = QPushButton("Reset All Hotkeys")
            reset_all.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_all.setStyleSheet(
                "QPushButton { background: #2a2a2a; color: #888; border: 1px solid #3a3a3a; "
                "border-radius: 4px; padding: 5px; font-size: 11px; } "
                "QPushButton:hover { color: #fff; border-color: #666; }"
            )
            reset_all.clicked.connect(self._reset_all_hotkeys)
            grid.addWidget(reset_all, len(DEFAULT_HOTKEYS), 0, 1, 3)

            rlayout.addWidget(_hk_widget)

        rlayout.addStretch()

        self.logout_btn = QPushButton("Logout / Switch Server")
        self.logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logout_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #f44336; }
            QPushButton:pressed { background-color: #b71c1c; }
        """)
        self.logout_btn.clicked.connect(self.perform_logout)
        rlayout.addWidget(self.logout_btn)

    def showEvent(self, event):
        super().showEvent(event)
        p = self.parent
        if p and p.isVisible():
            pg = p.geometry()
            h = min(900, pg.height() - 60)
            self.setFixedHeight(h)
            x = pg.x() + (pg.width()  - self.width())  // 2
            y = pg.y() + (pg.height() - h) // 2
            self.move(x, y)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, lambda: QApplication.instance().installEventFilter(self))

    def _refresh_preset_buttons(self, active_name: str):
        for name, btn in self._preset_btns.items():
            if name == active_name:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {self._acc}; color: #111; border: 1px solid {self._acc}; "
                    f"border-radius: 5px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}"
                    f"QPushButton:hover {{ background: {self._acc}; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {self._fc2}; border: 1px solid {self._bc}; "
                    f"border-radius: 5px; padding: 6px 14px; font-size: 12px; }}"
                    f"QPushButton:hover {{ color: {self._fc1}; border-color: {self._fc1}; }}"
                )

    def _open_theme_builder(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from theme_builder import ThemeBuilderDialog
        dlg = ThemeBuilderDialog(self.parent, self)
        dlg.exec()

    def _tint_logo(self, color: str, size: int) -> QPixmap:
        from player import resource_path as _rp
        raw = QPixmap(_rp("img/shahedron1.png"))
        if raw.isNull():
            return QPixmap()
        raw = raw.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(raw.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, raw)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(out.rect(), QColor(color))
        p.end()
        return out

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        if hasattr(self.parent, 'hide_dim'):
            self.parent.hide_dim()
        # Re-apply title bar mode: Windows may reset DWM state when a child window closes
        if hasattr(self.parent, 'enable_dark_title_bar') and hasattr(self.parent, '_last_title_bar_dark') \
                and self.parent._last_title_bar_dark is not None:
            self.parent.enable_dark_title_bar(self.parent._last_title_bar_dark)
        super().hideEvent(event)

    def eventFilter(self, _, event):
        from PyQt6.QtCore import QEvent, QRect
        if event.type() == QEvent.Type.MouseButtonPress:
            pos = event.globalPosition().toPoint()
            tl  = self.mapToGlobal(self.rect().topLeft())
            br  = self.mapToGlobal(self.rect().bottomRight())
            if not QRect(tl, br).contains(pos):
                self.hide()
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        super().closeEvent(event)

    def refresh_theme(self):
        t  = self.parent.theme
        fc1 = t.font_color_primary
        fc2 = t.font_color_secondary
        bc  = t.border_color
        bg  = t.main_panel_bg
        mc  = getattr(self.parent, 'master_color', '#ffffff')

        self.bg.setStyleSheet(
            f"QFrame#settingsBg {{ background-color: rgb({bg}); border: 1px solid {bc}; border-radius: 10px; }}"
        )
        for sep in self._seps:
            sep.setStyleSheet(f"color: {bc};")
        _sec = f"color: {fc2}; font-size: 10px; font-weight: bold; letter-spacing: 2px;"
        self._preset_label.setStyleSheet(_sec)
        self._lyrics_label.setStyleSheet(_sec)
        if hasattr(self, '_hotkeys_label'):
            self._hotkeys_label.setStyleSheet(_sec)
        self._name_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {fc1}; background: transparent;")
        self._ver_lbl.setStyleSheet(f"font-size: 11px; color: {fc2}; background: transparent;")
        for lbl in self._hotkey_desc_lbls:
            lbl.setStyleSheet(f"color: {fc1}; font-size: 12px; background: transparent;")
        _sb_ss = (
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ border: none; background: #1a1a1a; width: 6px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {mc}; min-height: 20px; border-radius: 3px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self._scroll.setStyleSheet(_sb_ss)
        if hasattr(self, '_scroll_right'):
            self._scroll_right.setStyleSheet(_sb_ss)

    def _apply_preset(self, name: str):
        from player.theme import load_presets
        import dataclasses
        preset = load_presets().get(name)
        if not preset:
            return
        t = self.parent.theme
        for f in dataclasses.fields(preset):
            if f.name != 'border_color':
                setattr(t, f.name, getattr(preset, f.name))
        self.parent._last_theme_key = None
        if t.auto_bg_from_accent:
            self.parent._auto_tint_bg_colors()
        self.parent.refresh_ui_styles()
        self._apply_menu_hover_palette()
        if getattr(self.parent, 'visualizer', None):
            self.parent.visualizer.bar_color = QColor(t.accent)
        if hasattr(self.parent, '_queue_panel'):
            self.parent._queue_panel.set_accent_color(t.accent)
            self.parent._queue_panel.apply_theme(t)
        self.parent.now_playing_widget.apply_theme(t)
        self.refresh_theme()
        self._refresh_preset_buttons(name)

    def _save_lyrics_sources(self):
        from lyrics_panel import SOURCES as _LYRIC_SOURCES, SETTINGS_KEY as _LYRIC_KEY
        enabled = [src for src, cb in self._lyrics_source_checks.items() if cb.isChecked()]
        QSettings('Icosahedron', 'Icosahedron').setValue(_LYRIC_KEY, enabled)

    def _apply_menu_hover_palette(self):
        from player.mixins.visuals import resolve_menu_hover
        from PyQt6.QtGui import QPalette, QColor as _QC
        from PyQt6.QtWidgets import QApplication
        hover_c = _QC(resolve_menu_hover(self.parent.theme))
        text_c  = _QC('#111111') if hover_c.lightness() > 140 else _QC('#eeeeee')
        pal = QApplication.instance().palette()
        pal.setColor(QPalette.ColorRole.Highlight,       hover_c)
        pal.setColor(QPalette.ColorRole.HighlightedText, text_c)
        QApplication.instance().setPalette(pal)
        self.parent._last_theme_key = None
        self.parent._last_title_bar_dark = None  # force re-apply after palette change
        self.parent.refresh_ui_styles()
        for i in range(self.parent.tabs.count()):
            tab = self.parent.tabs.widget(i)
            if hasattr(tab, 'footer'):
                tab.footer.set_accent_color(tab.footer.current_accent)
            if hasattr(tab, 'search_container'):
                tab.search_container.set_accent_color(getattr(tab, 'current_accent', '#ffffff'))

    def _on_key_captured(self, hid, key):
        self.parent.hotkey_manager.rebind(hid, key)

    def _on_reset(self, hid):
        self.parent.hotkey_manager.reset(hid)
        self._key_buttons[hid].set_key(self.parent.hotkey_manager.get(hid))

    def _reset_all_hotkeys(self):
        self.parent.hotkey_manager.reset_all()
        for hid, btn in self._key_buttons.items():
            btn.set_key(self.parent.hotkey_manager.get(hid))

    def perform_logout(self):
        from PyQt6.QtCore import QSettings, QProcess
        from PyQt6.QtWidgets import QMessageBox, QApplication
        import sys
        import os
        import keyring

        reply = QMessageBox.question(
            self, 'Confirm Logout',
            'Are you sure you want to log out?\n\nThis will clear your saved credentials, wipe the cache, and restart the player.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            settings = QSettings()
            user = settings.value("navidrome/username", "")

            # Wipe keyring password
            if user:
                try: keyring.delete_password("Icosahedron", user)
                except keyring.errors.PasswordDeleteError: pass

            # Wipe all user-specific settings so the next user starts clean
            for key in (
                "navidrome/url",
                "navidrome/username",
                "current_playlist",
                "saved_current_index",
                "saved_position",
                "last_master_color",
                "album_state",
                "artist_state",
                "waveform_mode",
                "tab_order",
                "tracks_sort_state",
                "tracks_columns_hidden",
                "now_playing_columns_hidden",
            ):
                settings.remove(key)
            settings.sync()

            # Clear in-memory API cache
            if hasattr(self.parent, 'navidrome_client') and self.parent.navidrome_client:
                try:
                    if hasattr(self.parent.navidrome_client, '_api_cache'):
                        self.parent.navidrome_client._api_cache.cache.clear()
                        if hasattr(self.parent.navidrome_client._api_cache, 'save'):
                            self.parent.navidrome_client._api_cache.save()
                except Exception: pass

            # Clear JSON data cache on disk
            import shutil
            if getattr(sys, 'frozen', False): base_dir = os.path.dirname(sys.executable)
            else: base_dir = os.path.dirname(os.path.abspath(__file__))
            cache_dir = os.path.join(base_dir, "app_data", "json_data")
            if os.path.exists(cache_dir):
                try: shutil.rmtree(cache_dir)
                except Exception as e: print(f"Failed to clear disk cache: {e}")

            # Flag the main window so closeEvent skips re-saving user data
            if hasattr(self.parent, '_logging_out'):
                self.parent._logging_out = True

            QProcess.startDetached(sys.executable, sys.argv)
            QApplication.quit()

class StatusButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = "#ffffff" 

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isChecked():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(self.master_color))
            painter.setPen(Qt.PenStyle.NoPen)
            dot_size = 5; x = (self.width() - dot_size) // 2; y = 2 
            painter.drawEllipse(x, y, dot_size, dot_size); painter.end()

class ShadowContextMenu(QFrame):
    """Universal shadow context menu — psysonic style: 0 12px 32px rgba(0,0,0,0.6)."""
    _PAD = 20

    def __init__(self, parent=None, is_submenu: bool = False):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._is_sub  = is_submenu
        self._bg      = QColor(20, 20, 20)
        self._bc      = QColor(50, 50, 50)
        self._fg      = '#dddddd'
        self._fg2     = '#666666'
        self._hov     = '#333333'
        self._px      = 14
        self._accent  = '#cccccc'
        self._callbacks:      list = []
        self._open_sub        = None
        self._sub_trigger     = None
        self._sub_trigger_base = ''
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._poll_mouse)

        pl = 4 if is_submenu else self._PAD
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pl, self._PAD, self._PAD, self._PAD)
        outer.setSpacing(0)
        self._lo = QVBoxLayout()
        self._lo.setContentsMargins(4, 4, 4, 4)
        self._lo.setSpacing(1)
        outer.addLayout(self._lo)

    def configure(self, bg_rgb: str, bc: str, fg: str, fg2: str, hov: str,
                  px: int, accent: str = '#cccccc'):
        try:
            r, g, b = [int(x) for x in bg_rgb.split(',')]
            self._bg = QColor(r, g, b)
        except Exception:
            self._bg = QColor(20, 20, 20)
        self._bc = QColor(bc)
        self._fg = fg; self._fg2 = fg2; self._hov = hov
        self._px = px; self._accent = accent

    # ── helpers ───────────────────────────────────────────────────────────────

    def _close_open_sub(self):
        if self._open_sub and self._open_sub.isVisible():
            self._open_sub.hide()
        if self._sub_trigger and self._sub_trigger_base:
            self._sub_trigger.setStyleSheet(self._sub_trigger_base)
        self._open_sub = None; self._sub_trigger = None
        self._sub_trigger_base = ''; self._poll.stop()

    def _poll_mouse(self):
        from PyQt6.QtGui import QCursor as _QC
        if not (self._open_sub and self._open_sub.isVisible()):
            self._poll.stop(); return
        pos = _QC.pos()
        if self._open_sub.geometry().contains(pos): return
        if self._sub_trigger:
            tg = self._sub_trigger.mapToGlobal(QPoint(0, 0))
            if QRect(tg, self._sub_trigger.size()).contains(pos): return
        self._close_open_sub()

    def _row(self, text: str, enabled: bool = True, color: str = '',
             icon_path: str = '') -> QWidget:
        c = color or self._fg2
        base_ss = (f'color: {c}; font-size: {self._px}px; '
                   f'background: transparent; border-radius: 4px;')
        hov_ss  = (f'color: {c}; font-size: {self._px}px; '
                   f'background: {self._hov}; border-radius: 4px;')
        row = QWidget()
        row.setStyleSheet(base_ss)
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lo = QHBoxLayout(row)
        lo.setContentsMargins(12, 5, 20, 5)
        lo.setSpacing(8)
        if icon_path:
            _ti = tint_icon  # already in this module
            ico = QLabel()
            pix = _ti(icon_path, color if color else self._accent).pixmap(QSize(14, 14))
            ico.setPixmap(pix); ico.setFixedSize(14, 14)
            ico.setStyleSheet('background: transparent;')
            lo.addWidget(ico)
        else:
            lo.addSpacing(22)
        txt = QLabel(text)
        txt.setStyleSheet(f'color: {c}; font-size: {self._px}px; background: transparent;')
        lo.addWidget(txt); lo.addStretch()
        if enabled:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            def _enter(_, _r=row, _h=hov_ss):
                self._close_open_sub(); _r.setStyleSheet(_h)
            def _leave(_, _r=row, _b=base_ss): _r.setStyleSheet(_b)
            row.enterEvent = _enter; row.leaveEvent = _leave
        return row

    # ── public API ────────────────────────────────────────────────────────────

    def add_action(self, text: str, callback=None, enabled: bool = True,
                   color: str = '', icon_path: str = ''):
        row = self._row(text, enabled, color, icon_path)
        if enabled and callback:
            cb = callback; self._callbacks.append(cb)
            def _press(_e, f=cb): f(); self.close()
            row.mousePressEvent = _press
        self._lo.addWidget(row); return row

    def add_submenu(self, text: str, items: list, icon_path: str = ''):
        trigger = self._row(f'{text}  ›', icon_path=icon_path)
        self._lo.addWidget(trigger)
        sub = ShadowContextMenu(self, is_submenu=True)
        sub.configure(f'{self._bg.red()},{self._bg.green()},{self._bg.blue()}',
                      self._bc.name(), self._fg, self._fg2, self._hov, self._px,
                      accent=self._accent)
        for entry in items:
            lbl, cb = entry[0], entry[1]
            ico = entry[2] if len(entry) > 2 else ""
            sub.add_action(lbl, cb, icon_path=ico)
        def _show():
            sub.adjustSize()
            # Natural position: right of trigger, slightly overlapping (-4px like psysonic)
            tr_right = trigger.mapToGlobal(QPoint(trigger.width(), 0)).x()
            tr_left  = trigger.mapToGlobal(QPoint(0, 0)).x()
            x = tr_right                                        # default: right side
            y = trigger.mapToGlobal(QPoint(0, 0)).y() - 4 - sub._PAD  # -4px overlap

            win = getattr(self, '_win', None)  # main app window stored by exec_at
            if win:
                wr  = win.geometry()
                buf = self._PAD
                if x + sub.width() > wr.right() + buf:
                    x = tr_left - sub.width() + sub._PAD  # cancel right shadow gap
                if y + sub.height() > wr.bottom() + buf:
                    y = wr.bottom() + buf - sub.height()
                x = max(x, wr.left() - buf)
                y = max(y, wr.top()  - buf)
            sub.move(QPoint(x, y))
            sub.show(); self._poll.start()
        _hs = (f'color: {self._fg2}; font-size: {self._px}px; '
               f'background: {self._hov}; border-radius: 4px;')
        _bs = trigger.styleSheet()
        def _on_enter(_):
            self._close_open_sub(); self._open_sub = sub
            self._sub_trigger = trigger; self._sub_trigger_base = _bs
            _show(); trigger.setStyleSheet(_hs)
        trigger.enterEvent = _on_enter; return trigger

    def exec_at(self, pos: QPoint, window=None):
        self._win = window   # store for submenu bounds checking
        self.adjustSize()
        x, y = pos.x(), pos.y()
        if window:
            wr = window.geometry()
            x = min(x, wr.right()  - self.width()  + self._PAD)
            y = min(y, wr.bottom() - self.height() + self._PAD)
            x = max(x, wr.left()   - self._PAD)
            y = max(y, wr.top()    - self._PAD)
        self.move(QPoint(x, y)); self.show()

    def hideEvent(self, ev): self._close_open_sub(); super().hideEvent(ev)
    def closeEvent(self, ev): self._close_open_sub(); super().closeEvent(ev)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad = self._PAD
        BLUR = 16; OY = 6; MAX_A = 55
        pl = 4 if self._is_sub else pad
        content = QRectF(self.rect()).adjusted(pl, pad, -pad, -pad)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(16, 0, -1):
            t = i / 16; alpha = int(MAX_A * (1 - t) ** 2); ex = BLUR * t
            lx = 0 if self._is_sub else -ex * .7
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(content.adjusted(lx, -ex*.4+OY*(1-t), ex*.7, ex+OY*t),
                              10+ex*.25, 10+ex*.25)
        p.setPen(self._bc); p.setBrush(self._bg)
        p.drawRoundedRect(content, 10, 10); p.end()

def themed_shadow_menu(parent, bg: str = None) -> ShadowContextMenu:
    """Create a ShadowContextMenu pre-configured with the window's current theme.

    `bg` overrides the panel background (e.g. a view's own _bg_color) when the
    theme's main_panel_bg shouldn't be used directly.
    """
    from player.mixins.visuals import resolve_menu_hover
    theme = getattr(parent.window(), 'theme', None)
    if bg is None:
        bg = getattr(theme, 'main_panel_bg', '14,14,14') if theme else '14,14,14'
    bc = getattr(theme, 'border_color', '#2a2a2a') if theme else '#2a2a2a'
    if theme and not getattr(theme, 'auto_border_from_accent', True):
        bc = getattr(theme, 'manual_border_color', '#2a2a2a')
    fg  = getattr(theme, 'font_color_primary',   '#dddddd') if theme else '#dddddd'
    fg2 = getattr(theme, 'font_color_secondary', '#555555') if theme else '#555555'
    px  = getattr(theme, 'font_size_primary',    14)        if theme else 14
    acc = getattr(theme, 'accent',               '#cccccc') if theme else '#cccccc'
    hov = resolve_menu_hover(theme)
    menu = ShadowContextMenu(parent)
    menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)
    return menu

def popup_menu_at_global(menu: ShadowContextMenu, global_x: float, global_y: float, window=None):
    """Show `menu` so its padded edge aligns with the given global (x, y)."""
    gp = QPoint(int(global_x), int(global_y))
    menu.exec_at(QPoint(gp.x() - menu._PAD, gp.y() - menu._PAD), window=window)

class CoverImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.image_cache = {}

    def requestImage(self, id, requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import Qt, QRectF

        real_id = id.split("?t=")[0]
        data = self.image_cache.get(real_id)


        size = 250
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)

        if data:
            source = QImage()
            source.loadFromData(data)
            if not source.isNull():
                source = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)

                # Slice the corners off the QImage perfectly!
                painter = QPainter(img)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)

                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()

        return img, img.size()

class AlbumModel(QAbstractListModel):
    TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
    ARTIST_ROLE = Qt.ItemDataRole.UserRole + 2
    YEAR_ROLE = Qt.ItemDataRole.UserRole + 3
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 4
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 5
    IS_LOADING_ROLE = Qt.ItemDataRole.UserRole + 6
    SONG_COUNT_ROLE = Qt.ItemDataRole.UserRole + 7
    ARTIST_ID_ROLE = Qt.ItemDataRole.UserRole + 8

    def __init__(self):
        super().__init__()
        self.albums = []

    def rowCount(self, parent=QModelIndex()): return len(self.albums)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.albums[index.row()]
        if role == self.TITLE_ROLE:
            if a.get('type') == 'placeholder': return ''
            return a.get('title') or a.get('name') or 'Unknown'
        if role == self.ARTIST_ROLE: return a.get('artist') or a.get('albumArtist') or ''
        if role == self.YEAR_ROLE: return str(a.get('year') or a.get('minYear') or a.get('maxYear') or '').replace('None', '')
        if role == self.COVER_ID_ROLE: return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return a
        if role == self.IS_LOADING_ROLE: return a.get('type') == 'placeholder'
        if role == self.SONG_COUNT_ROLE:
            n = a.get('songCount') or a.get('trackCount') or ''
            return f"{n} tracks" if n else ''
        if role == self.ARTIST_ID_ROLE:
            return a.get('artistId') or a.get('albumArtistId') or ''
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE: b"albumTitle", self.ARTIST_ROLE: b"albumArtist",
            self.YEAR_ROLE: b"albumYear", self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData", self.IS_LOADING_ROLE: b"isLoading",
            self.SONG_COUNT_ROLE: b"albumSongCount",
            self.ARTIST_ID_ROLE: b"albumArtistId",
        }

    def append_albums(self, new_albums):
        start = len(self.albums)
        self.beginInsertRows(QModelIndex(), start, start + len(new_albums) - 1)
        self.albums.extend(new_albums)
        self.endInsertRows()

    def set_albums(self, albums):
        self.beginResetModel()
        self.albums = list(albums)
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self.albums = []
        self.endResetModel()

    def update_cover(self, cover_id):
        forced_id = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self.albums):
            raw = str(a.get('cover_id') or a.get('coverArt') or a.get('id') or '')
            if raw == cover_id:
                a['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class AlbumIconProvider(QQuickImageProvider):
    """QML image provider serving `img/{name}.png`, optionally tinted with a
    `_rrggbb` suffix (e.g. `image://albumicons/sort-random-a_ffffff`)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = {}

    def requestImage(self, icon_id, requestedSize):
        from player import resource_path
        from PyQt6.QtGui import QImage
        parts     = icon_id.rsplit('_', 1)
        name      = parts[0]
        color_hex = ('#' + parts[1]) if len(parts) > 1 else '#ffffff'
        cache_key = f"{name}_{color_hex}"
        if cache_key in self._cache:
            img = self._cache[cache_key]
            return img, img.size()
        path = resource_path(f"img/{name}.png")
        base = QImage(path)
        if base.isNull():
            empty = QImage(1, 1, QImage.Format.Format_ARGB32)
            empty.fill(Qt.GlobalColor.transparent)
            return empty, empty.size()
        result = QImage(base.size(), QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(0, 0, base)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(result.rect(), QColor(color_hex))
        p.end()
        self._cache[cache_key] = result
        return result, result.size()

class LeftPanelCoverProvider(QQuickImageProvider):
    """Serves the left-panel album-art square via
    `image://leftpanelcover/<current|old>/<token>`.

    Reproduces the old SquareArtContainer paint: a rounded square (radius 5)
    on a #121212 background, with the source pixmap scaled
    (KeepAspectRatioByExpanding) and center-cropped to fill it. The actual
    pixmaps are pushed imperatively via set_current_art/set_old_art — the
    requested id only selects which of the two to render.
    """

    SIZE = 400

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._current = None
        self._old = None

    def set_current_art(self, pixmap):
        self._current = pixmap

    def set_old_art(self, pixmap):
        self._old = pixmap

    def requestImage(self, image_id, requestedSize):
        which = image_id.split("/")[0]
        src = self._current if which == "current" else self._old

        size = self.SIZE
        out = QImage(size, size, QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), 5, 5)
        p.setClipPath(path)
        p.fillRect(out.rect(), QColor("#121212"))
        if src is not None and not src.isNull():
            scaled = src.scaled(size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            ox = (scaled.width()  - size) // 2
            oy = (scaled.height() - size) // 2
            p.drawPixmap(-ox, -oy, scaled)
        p.end()
        return out, out.size()

class AlbumDetailCoverProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.cache = {}

    ART   = 264  # visible art pixels — matches _RoundedPixmapLabel(264, 264)
    PAD   = 30   # transparent shadow bleed — enough for blurRadius=38 + offset=10
    TOTAL = ART + PAD * 2  # 324

    BTN_D   = 58   # play button diameter — matches footer PlayButton
    BTN_PAD = 20   # shadow bleed around button
    BTN_TOT = BTN_D + BTN_PAD * 2   # 98

    def _btn_shadow(self, hex_color: str):
        import numpy as _np
        from scipy.ndimage import gaussian_filter as _gf
        d, pad, total = self.BTN_D, self.BTN_PAD, self.BTN_TOT
        SIGMA = 7.0   # fades to <1% at BTN_PAD boundary
        SA    = 180
        OY    = 3     # subtle downward offset

        base = QColor("#" + hex_color) if QColor("#" + hex_color).isValid() else QColor(136, 136, 136)
        sr, sg, sb = base.red(), base.green(), base.blue()

        # Rasterise circle at offset position
        mask_img = QImage(total, total, QImage.Format.Format_ARGB32)
        mask_img.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask_img)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad + OY, d, d))
        mp.fillPath(path, QColor(255, 255, 255, 255))
        mp.end()

        ptr = mask_img.bits(); ptr.setsize(total * total * 4)
        alpha_f = _np.frombuffer(ptr, dtype=_np.uint8).reshape((total, total, 4))[:, :, 3].astype(_np.float32) / 255.0
        blurred = _gf(alpha_f, sigma=SIGMA)
        shad_a  = (blurred * SA).clip(0, 255).astype(_np.uint8)

        shad_arr = _np.zeros((total, total, 4), dtype=_np.uint8)
        shad_arr[:, :, 0] = sb
        shad_arr[:, :, 1] = sg
        shad_arr[:, :, 2] = sr
        shad_arr[:, :, 3] = shad_a
        shad_bytes = bytes(shad_arr)
        shad_qimg  = QImage(shad_bytes, total, total, total * 4, QImage.Format.Format_ARGB32)

        img = QImage(total, total, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); p.drawImage(0, 0, shad_qimg); p.end()
        return img, img.size()

    def requestImage(self, cov_id, requestedSize):
        # "btn/<hexcolor>" → circular Gaussian shadow for play button
        if cov_id.startswith("btn/"):
            return self._btn_shadow(cov_id[4:])

        # "art/<id>" prefix → return just the 260×260 rounded art (no shadow)
        art_only = cov_id.startswith("art/")
        if art_only:
            cov_id = cov_id[4:]
        real_id = cov_id.split("?t=")[0]
        data    = self.cache.get(real_id)
        art, pad, total, r = self.ART, self.PAD, self.TOTAL, 10

        # Decode + crop source once regardless of mode
        source = None
        if data:
            src = QImage()
            src.loadFromData(data)
            if not src.isNull():
                src = src.scaled(art, art,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                ox = (src.width()  - art) // 2
                oy = (src.height() - art) // 2
                source = src.copy(ox, oy, art, art)

        if art_only:
            # Return 2× resolution so the Canvas always downscales (crisp at zoom 1.08×)
            art2 = art * 2   # 528
            img  = QImage(art2, art2, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            if not data:
                return img, img.size()
            src2 = QImage(); src2.loadFromData(data)
            if src2.isNull():
                return img, img.size()
            src2 = src2.scaled(art2, art2,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            ox2 = (src2.width()  - art2) // 2
            oy2 = (src2.height() - art2) // 2
            src2 = src2.copy(ox2, oy2, art2, art2)
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawImage(0, 0, src2)
            painter.end()
            return img, img.size()

        # Full shadow + art image
        img = QImage(total, total, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        if source is None:
            return img, img.size()

        # Extract vibrant shadow colour — same logic as _extract_vibrant_color()
        # in now_playing_info.py: most-saturated pixel of an 8×8 sample, HSL L in (0.1,0.9)
        small  = source.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        best_sat = -1.0
        best_col = QColor(60, 60, 60)
        for sy in range(8):
            for sx in range(8):
                c = QColor(small.pixel(sx, sy))
                _, s, lv, _ = c.getHslF()
                if s > best_sat and 0.1 < lv < 0.9:
                    best_sat = s
                    best_col = c
        sr = best_col.red()   // 3
        sg = best_col.green() // 3
        sb = best_col.blue()  // 3
        SA = 210   # same as now-playing QGraphicsDropShadowEffect alpha
        OY = 10    # same as QGraphicsDropShadowEffect offset(0, 10)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Real Gaussian blur shadow — matches QGraphicsDropShadowEffect(blurRadius=38, offset=(0,10))
        # Step 1: rasterise shadow shape (rounded rect shifted down by OY) into an alpha mask
        import numpy as _np
        from scipy.ndimage import gaussian_filter as _gf
        SIGMA = 10.0   # tighter shadow: fades to ~1% at PAD=30px boundary

        mask_img = QImage(total, total, QImage.Format.Format_ARGB32)
        mask_img.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask_img)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        mp.fillPath(self._shadow_shape(pad, OY, art, r), QColor(255, 255, 255, 255))
        mp.end()

        # Step 2: extract alpha, apply Gaussian blur
        ptr = mask_img.bits(); ptr.setsize(total * total * 4)
        mask_arr = _np.frombuffer(ptr, dtype=_np.uint8).reshape((total, total, 4))
        alpha_f  = mask_arr[:, :, 3].astype(_np.float32) / 255.0   # copy → safe to blur
        blurred  = _gf(alpha_f, sigma=SIGMA)
        shad_a   = (blurred * SA).clip(0, 255).astype(_np.uint8)

        # Step 3: build BGRA shadow image (Format_ARGB32 on LE = B,G,R,A in bytes)
        shad_arr = _np.zeros((total, total, 4), dtype=_np.uint8)
        shad_arr[:, :, 0] = sb
        shad_arr[:, :, 1] = sg
        shad_arr[:, :, 2] = sr
        shad_arr[:, :, 3] = shad_a
        shad_bytes = bytes(shad_arr)          # keep alive through drawImage
        shad_qimg  = QImage(shad_bytes, total, total, total * 4,
                            QImage.Format.Format_ARGB32)

        # Step 4: composite shadow then art
        painter.drawImage(0, 0, shad_qimg)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setClipPath(self._art_clip(pad, art, r))
        painter.drawImage(pad, pad, source)
        painter.end()

        return img, img.size()

    def _shadow_shape(self, pad, oy, art, r):
        path = QPainterPath()
        path.addRoundedRect(QRectF(pad, pad + oy, art, art), r, r)
        return path

    def _art_clip(self, pad, art, r):
        path = QPainterPath()
        path.addRoundedRect(QRectF(pad, pad, art, art), r, r)
        return path

class DummyScrollBar:
    def value(self): return 0
    def setValue(self, val): pass
    def setStyleSheet(self, style): pass
    def setSingleStep(self, step): pass

class QMLGridWrapper(QQuickWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dummy_scroll = DummyScrollBar()

    def verticalScrollBar(self): return self._dummy_scroll

    def viewport(self): return self

    # Silently absorb all legacy QListWidget commands from main.py!
    def setLayoutMode(self, *args): pass

    def setUniformItemSizes(self, *args): pass

    def setBatchSize(self, *args): pass

    def setSpacing(self, *args): pass

    def setGridSize(self, *args): pass

    def setIconSize(self, *args): pass

    def setMovement(self, *args): pass

    def setVerticalScrollMode(self, *args): pass

    def setViewMode(self, *args): pass

    def doItemsLayout(self, *args): pass

    def clear(self): pass

    def count(self): return 0

    def currentItem(self): return None

    def currentRow(self): return -1

    def setCurrentRow(self, *args): pass

    def setCurrentItem(self, *args): pass

    def item(self, *args): return None

    def setResizeMode(self, mode):
        from PyQt6.QtQuickWidgets import QQuickWidget
        if isinstance(mode, QQuickWidget.ResizeMode):
            super().setResizeMode(mode)

class QMLMiddleClickScroller(QObject):
    """
    Middle-click omni-scroller for QMLGridWrapper.
    Mirrors MiddleClickScroller but pushes pixel deltas via GridBridge.scrollBy
    instead of writing to a QScrollBar (which is a no-op stub on QMLGridWrapper).
    """
    def __init__(self, qml_widget, bridge):
        super().__init__(qml_widget)
        self.target = qml_widget
        self.bridge = bridge
        self.is_scrolling = False
        self.origin_y = 0
        self.click_time = 0

        self.timer = QTimer(self)
        self.timer.start(7)
        self.timer.timeout.connect(self._process_scroll)

        # Monitor the widget itself (QQuickWidget has no separate viewport)
        self.target.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.target and event.type() == QEvent.Type.Hide:
            if self.is_scrolling:
                self._stop()
            return False

        if obj == self.target:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    if self.is_scrolling:
                        self._stop()
                    else:
                        self._start(event.globalPosition().toPoint().y())
                    return True          # swallow — QML native handler not needed
                elif self.is_scrolling:
                    self._stop()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.MiddleButton and self.is_scrolling:
                    if time.time() - self.click_time > 0.2:
                        self._stop()
                    return True

        return super().eventFilter(obj, event)

    def _start(self, start_y):
        self.is_scrolling = True
        self.origin_y = start_y
        self.click_time = time.time()
        self.target.setCursor(Qt.CursorShape.SizeVerCursor)

    def _stop(self):
        self.is_scrolling = False
        self.target.unsetCursor()
        self.bridge.cancelScroll.emit()     # also kills the QML-side cursor if active

    def _process_scroll(self):
        if not self.is_scrolling:
            return

        buttons = QApplication.mouseButtons()
        if (not self.target.isVisible()
                or not QApplication.activeWindow()
                or (buttons & Qt.MouseButton.LeftButton)
                or (buttons & Qt.MouseButton.RightButton)):
            self._stop()
            return

        delta = QCursor.pos().y() - self.origin_y
        deadzone = 15
        if abs(delta) < deadzone:
            return

        speed = (abs(delta) - deadzone) * 0.03
        direction = 1 if delta > 0 else -1
        self.bridge.scrollBy.emit(speed * direction)

class ArrowButton(QAbstractButton):
    """Small left/right chevron button with a themed hover highlight."""

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
        from player.mixins.visuals import resolve_menu_hover
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            theme = getattr(self.window(), 'theme', None)
            p.setBrush(QColor(resolve_menu_hover(theme)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 12, 12)
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

class GridItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#1db954")
        self.hovered_artist_row = -1
        self.clickable_artist = True   # set False to disable subtitle hover/underline
        self.show_play_btn   = True    # set False to hide the hover play button

        # Animation state
        self._hovered_row   = -1
        self._hover_progress = 0.0   # 0.0 = not hovered, 1.0 = fully hovered
        self._play_progress  = 0.0   # 0.0 = play btn not hovered, 1.0 = fully hovered

        self._hover_anim = QVariantAnimation()
        self._hover_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._hover_anim.valueChanged.connect(self._on_hover_value)

        self._play_anim = QVariantAnimation()
        self._play_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._play_anim.valueChanged.connect(self._on_play_value)

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent()
        w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)

    def _primary_px(self):
        t = self._theme()
        return getattr(t, 'font_size_primary', 14) if t else 14

    def _secondary_px(self):
        t = self._theme()
        return getattr(t, 'font_size_secondary', 12) if t else 12

    def _primary_color(self):
        t = self._theme()
        return getattr(t, 'font_color_primary', '#eeeeee') if t else '#eeeeee'

    def _secondary_color(self):
        t = self._theme()
        return getattr(t, 'font_color_secondary', '#cccccc') if t else '#cccccc'

    def set_hovered_artist_row(self, row):
        self.hovered_artist_row = row

    # ── Animation helpers ─────────────────────────────────────────────────

    def _on_hover_value(self, value):
        self._hover_progress = value
        self._request_repaint()

    def _on_play_value(self, value):
        self._play_progress = value
        self._request_repaint()

    def _request_repaint(self):
        lw = self.parent()
        if lw and hasattr(lw, 'viewport'):
            lw.viewport().update()

    def _animate(self, anim, current, target, full_duration=150):
        anim.stop()
        distance = abs(target - current)
        if distance < 0.001:
            return
        anim.setDuration(max(1, int(full_duration * distance)))
        anim.setStartValue(float(current))
        anim.setEndValue(float(target))
        anim.start()

    def set_hovered_row(self, row):
        if row == self._hovered_row:
            return
        self._hovered_row = row
        if row >= 0:
            # Snap any leftover progress from a previous item to 0 instantly,
            # then animate the new item in from 0.
            self._hover_progress = 0.0
            self._play_progress  = 0.0
            self._play_anim.stop()
            self._animate(self._hover_anim, 0.0, 1.0)
        else:
            self._animate(self._hover_anim, self._hover_progress, 0.0)
            self._animate(self._play_anim,  self._play_progress,  0.0)

    def set_play_hovered(self, is_hovered):
        target = 1.0 if is_hovered else 0.0
        self._animate(self._play_anim, self._play_progress, target)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        icon_width  = rect.width() - 12
        icon_height = icon_width
        icon_x      = rect.x() + 6
        icon_rect   = QRect(icon_x, rect.y() + 4, icon_width, icon_height)

        path = QPainterPath()
        path.addRoundedRect(icon_rect.x(), icon_rect.y(), icon_rect.width(), icon_rect.height(), 10, 10)

        # ── Cover image ───────────────────────────────────────────────────
        painter.save()
        painter.setClipPath(path)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pix = icon.pixmap(icon_width, icon_height)
            px = icon_rect.x() + (icon_width - pix.width()) // 2
            py = icon_rect.y() + (icon_height - pix.height()) // 2
            painter.drawPixmap(px, py, pix)

        # Determine animation progress for this item
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_this_hovered = (index.row() == self._hovered_row)
        hover_p = self._hover_progress if is_this_hovered else (1.0 if is_selected else 0.0)
        play_p  = self._play_progress  if is_this_hovered else 0.0

        # Dark overlay: opacity 0 → 0.4  (QML: color "#000", opacity 0→0.4)
        if hover_p > 0:
            painter.setBrush(QColor(0, 0, 0, int(hover_p * 102)))
            painter.setPen(QPen(self.master_color, 2))
            painter.drawPath(path)
        painter.restore()  # remove clip

        # ── Play button: scale 0.8→1.0, opacity 0→0.8 (+0.2 when on btn) ─
        if hover_p > 0 and self.show_play_btn:
            center    = icon_rect.center()
            play_size = min(60, icon_width // 2)
            scale     = 0.8 + play_p * 0.2          # matches QML scale behaviour
            scaled_sz = max(4, int(play_size * scale))
            play_rect = QRect(0, 0, scaled_sz, scaled_sz)
            play_rect.moveCenter(center)

            play_opacity = hover_p * 0.8 + play_p * 0.2   # 0→0.8, then 0.8→1.0
            painter.setOpacity(play_opacity)
            painter.setBrush(self.master_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(play_rect)

            tri_size = scaled_sz // 3
            cx, cy = center.x(), center.y()
            p1 = QPoint(cx - tri_size // 3, cy - tri_size // 2)
            p2 = QPoint(cx - tri_size // 3, cy + tri_size // 2)
            p3 = QPoint(cx + tri_size // 2 + 2, cy)
            painter.setBrush(QColor("#111111"))
            painter.drawPolygon(QPolygon([p1, p2, p3]))
            painter.setOpacity(1.0)

        # ── Text ──────────────────────────────────────────────────────────
        data = index.data(Qt.ItemDataRole.UserRole)
        if data:
            title  = data.get('title') or data.get('name') or "Unknown"
            artist = data.get('artist', '')
            year   = str(data.get('year', ''))

            text_width = rect.width() - 20
            text_x     = rect.x() + 10
            current_y  = icon_rect.bottom() + 10

            # Title: interpolate primary color → accent on hover
            mc  = self.master_color
            pc  = QColor(self._primary_color())
            r   = int(pc.red()   + (mc.red()   - pc.red())   * hover_p)
            g   = int(pc.green() + (mc.green() - pc.green()) * hover_p)
            b   = int(pc.blue()  + (mc.blue()  - pc.blue())  * hover_p)
            painter.setPen(QColor(r, g, b))
            font = painter.font(); font.setBold(True); font.setPixelSize(self._primary_px()); painter.setFont(font)
            fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()),
                             Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(title, Qt.TextElideMode.ElideRight, text_width))

            current_y += fm.height() + 2
            font.setBold(False); font.setPixelSize(self._secondary_px())
            artist_hovered = self.clickable_artist and (index.row() == self.hovered_artist_row)
            if artist_hovered:
                font.setUnderline(True)
                painter.setPen(QColor(self.master_color))
            else:
                font.setUnderline(False)
                painter.setPen(QColor(self._secondary_color()))
            painter.setFont(font); fm = QFontMetrics(font)
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()),
                             Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(artist, Qt.TextElideMode.ElideRight, text_width))
            font.setUnderline(False); painter.setFont(font)

            current_y += fm.height() + 2
            painter.setPen(QColor(self._secondary_color()))
            painter.drawText(QRect(text_x, current_y, text_width, fm.height()), Qt.AlignmentFlag.AlignLeft, fm.elidedText(year, Qt.TextElideMode.ElideRight, text_width))

        painter.restore()