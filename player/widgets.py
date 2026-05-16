"""
player/widgets.py — Reusable custom Qt widgets.

These are self-contained UI components with no dependency on
the main IcosahedronPlayer window (except SettingsWindow which takes a
parent reference so it can write back to visual_settings).
"""
import os
import re
from version import __version__

from PyQt6.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QSlider, QPushButton, QColorDialog, QCheckBox, QApplication,
    QMessageBox, QScrollArea, QFrame, QGridLayout, QFileDialog, QGroupBox
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize, pyqtSignal, QSettings, QProcess, QEvent, QPropertyAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from PyQt6.QtGui import (
    QFont, QFontMetrics, QColor, QMouseEvent, QPainter, QPen,
    QBrush, QPainterPath, QPolygon, QPixmap, QKeySequence, QIcon
)

from audio_engine import AudioEngine


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
        self.art_label.setStyleSheet("background-color: #222; border-radius: 4px; border: 1px solid #333;")
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
            self.art_label.setPixmap(_round_pixmap(pixmap))
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
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor

        def _fmt(v):
            s = f"{v:.2f}".rstrip('0').rstrip('.')
            return f"{s} BPM"

        _theme = getattr(self.window(), 'theme', None)
        _bc = getattr(_theme, 'border_color',       '#444444')
        _fg = getattr(_theme, 'font_color_primary', '#dddddd')
        _px = getattr(_theme, 'font_size_primary',  14)
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: #222; color: {_fg}; font-size: {_px}px; border: 1px solid {_bc}; }}"
            f"QMenu::item {{ padding: 6px 25px; }}"
            f"QMenu::item:selected {{ background-color: #333; color: {_fg}; }}"
        )
        for label, mult in [("Half", 0.5), ("2/3", 2/3), ("3/4", 3/4),
                             ("4/3", 4/3), ("3/2", 3/2), ("Double", 2.0)]:
            new_val = self._current_bpm * mult
            menu.addAction(f"{label}  |  {_fmt(new_val)}").triggered.connect(
                lambda checked=False, v=new_val: self.bpm_adjusted.emit(v)
            )
        menu.exec(QCursor.pos())



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
            f"font-size: {self._sec_px}px; color: #fff; background: transparent; text-decoration: underline;")

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



class TriangleTooltip(QLabel):
    def __init__(self, parent=None, show_triangle=True):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.show_triangle = show_triangle
        self.hide()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(10, 5, 10, 10 if show_triangle else 5) 
        self.setFont(QFont('Sans Serif', 12, QFont.Weight.Bold))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bottom_margin = -6 if self.show_triangle else 0
        rect = self.rect().adjusted(1, 1, -1, bottom_margin)
        painter.setBrush(QColor("#0d0d0d"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 10, 10)
        if self.show_triangle:
            triangle = QPolygon([QPoint(self.width()//2-6, rect.bottom()), QPoint(self.width()//2+6, rect.bottom()), QPoint(self.width()//2, self.height()-1)])
            painter.setBrush(QColor("#0d0d0d")); painter.drawPolygon(triangle)
        painter.setPen(QColor("#ffffff")); painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())



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


class SettingsWindow(QWidget):
    def __init__(self, parent):
        super().__init__(None)
        self.parent = parent
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(900)
        self.setStyleSheet("color: #ddd; font-family: sans-serif;")
        self._drag_pos = None

        # Themed frame — same approach as TrackInfoDialog
        theme = getattr(parent, 'theme', None)
        bg = getattr(theme, 'main_panel_bg', '24,24,24') if theme else '24,24,24'
        bc = getattr(theme, 'border_color', '#2a2a2a') if theme else '#2a2a2a'

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
        _sep_line = QFrame()
        _sep_line.setFrameShape(QFrame.Shape.VLine)
        _sep_line.setStyleSheet("color: #2a2a2a;")
        outer.addWidget(_sep_line)

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
        self._settings_logo_tint.raise_()
        self._settings_logo_size = _logo_size
        self._update_settings_logo_tint(self.parent.theme.accent)

        header.addWidget(_logo_ctr)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        name_lbl = QLabel("Icosahedron")
        name_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; background: transparent;")
        ver_lbl = QLabel(f"v{__version__}")
        ver_lbl.setStyleSheet("font-size: 11px; color: #555; background: transparent;")
        title_col.addWidget(name_lbl)
        title_col.addWidget(ver_lbl)
        header.addLayout(title_col)
        header.addStretch()
        layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a2a2a;")
        layout.addWidget(sep)

        layout.addWidget(QLabel("Theme:"))
        self.dynamic_check = QCheckBox("Auto-Match Color from Album Art")
        self.dynamic_check.setChecked(self.parent.theme.dynamic_accent)
        self.dynamic_check.stateChanged.connect(self.toggle_dynamic_color)
        layout.addWidget(self.dynamic_check)

        self.color_btn = QPushButton("Pick Static Color")
        self.color_btn.clicked.connect(self.pick_global_color)
        self.color_btn.setStyleSheet(f"background: {self.parent.theme.accent}; color: black; padding: 10px; border-radius: 6px; font-weight: bold;")
        layout.addWidget(self.color_btn)

        self._sliders = ()
        self._apply_slider_color()

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #2a2a2a;")
        layout.addWidget(sep2)

        bg_label = QLabel("Background Colors")
        bg_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(bg_label)

        self.auto_bg_check = QCheckBox("Auto-tint from accent color")
        self.auto_bg_check.setChecked(self.parent.theme.auto_bg_from_accent)
        self.auto_bg_check.stateChanged.connect(self._toggle_auto_bg)
        layout.addWidget(self.auto_bg_check)

        _BG_FIELDS = [
            ("left_panel_bg",   "Left Panel"),
            ("queue_panel_bg",  "Queue Panel"),
            ("footer_panel_bg", "Footer Panel"),
            ("main_panel_bg",   "Main Panel"),
            ("header_panel_bg", "Header Panel"),
        ]

        _bg_grid = QWidget()
        _bg_grid.setStyleSheet("background: transparent;")
        _bg_gl = QGridLayout(_bg_grid)
        _bg_gl.setContentsMargins(0, 0, 0, 0)
        _bg_gl.setHorizontalSpacing(10)
        _bg_gl.setVerticalSpacing(6)
        _bg_gl.setColumnStretch(1, 1)

        self._bg_btns = {}
        self._BG_FIELDS = _BG_FIELDS

        def _bg_btn_style(hex_color, disabled=False):
            if disabled:
                return ("background: #1e1e1e; border-radius: 6px; border: 1px solid #2a2a2a;"
                        " min-height: 28px; font-size: 11px; color: #444;")
            return (f"background: {hex_color}; border-radius: 6px; border: 1px solid #333;"
                    f" min-height: 28px; font-size: 11px; color: {'#000' if QColor(hex_color).lightness() > 128 else '#fff'};")

        self._bg_btn_style = _bg_btn_style

        def _make_bg_picker(f, btn):
            def _pick():
                if self.parent.theme.auto_bg_from_accent:
                    return
                r, g, b = (int(x) for x in getattr(self.parent.theme, f).split(','))
                color = QColorDialog.getColor(QColor(r, g, b), self, "Pick Background Color")
                if color.isValid():
                    setattr(self.parent.theme, f, f"{color.red()},{color.green()},{color.blue()}")
                    btn.setText(color.name())
                    btn.setStyleSheet(_bg_btn_style(color.name()))
                    self.parent._last_theme_key = None
                    self.parent.refresh_ui_styles()
            return _pick

        auto = self.parent.theme.auto_bg_from_accent
        for row, (field, label_text) in enumerate(_BG_FIELDS):
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #bbb; font-size: 12px; background: transparent;")

            r, g, b = (int(x) for x in getattr(self.parent.theme, field).split(','))
            hex_color = QColor(r, g, b).name()
            btn = QPushButton("auto" if auto else hex_color)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(not auto)
            btn.setStyleSheet(_bg_btn_style(hex_color, disabled=auto))
            btn.clicked.connect(_make_bg_picker(field, btn))

            _bg_gl.addWidget(lbl, row, 0)
            _bg_gl.addWidget(btn, row, 1)
            self._bg_btns[field] = btn

        layout.addWidget(_bg_grid)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("color: #2a2a2a;")
        layout.addWidget(sep3)

        border_section_label = QLabel("Border Color")
        border_section_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(border_section_label)

        self.auto_border_check = QCheckBox("Auto-derive from accent color")
        self.auto_border_check.setChecked(self.parent.theme.auto_border_from_accent)
        self.auto_border_check.stateChanged.connect(self._toggle_auto_border)
        layout.addWidget(self.auto_border_check)

        _border_auto = self.parent.theme.auto_border_from_accent
        _border_hex = self.parent.theme.manual_border_color
        self.border_color_btn = QPushButton("auto" if _border_auto else _border_hex)
        self.border_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.border_color_btn.setEnabled(not _border_auto)
        self.border_color_btn.setStyleSheet(
            _bg_btn_style(_border_hex, disabled=_border_auto)
        )
        self.border_color_btn.clicked.connect(self._pick_border_color)
        layout.addWidget(self.border_color_btn)

        layout.addStretch()

        # ── Right column: hotkeys + logout ────────────────────────────────
        if hasattr(self.parent, 'hotkey_manager'):
            from hotkeys import DEFAULT_HOTKEYS

            hotkeys_label = QLabel("HOTKEYS")
            hotkeys_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
            rlayout.addWidget(hotkeys_label)

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
                lbl.setStyleSheet("color: #bbb; font-size: 12px; background: transparent;")

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

    def _apply_slider_color(self):
        mc = getattr(self.parent, 'master_color', '#ffffff')
        self._update_settings_logo_tint(mc)
        _sb_ss = (
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:vertical {{ border: none; background: #1a1a1a; width: 6px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {mc}; min-height: 20px; border-radius: 3px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self._scroll.setStyleSheet(_sb_ss)
        if hasattr(self, '_scroll_right'):
            self._scroll_right.setStyleSheet(_sb_ss)

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

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
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

    def _update_settings_logo_tint(self, color: str):
        if not hasattr(self, '_settings_logo_tint'):
            return
        from player import resource_path
        from PyQt6.QtGui import QPainter as _P
        sz = self._settings_logo_size
        raw = QPixmap(resource_path("img/shahedron1.png"))
        if raw.isNull():
            return
        raw = raw.scaled(sz, sz, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(raw.size())
        out.fill(Qt.GlobalColor.transparent)
        p = _P(out)
        p.setRenderHint(_P.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, raw)
        p.setCompositionMode(_P.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(out.rect(), QColor(color))
        p.end()
        self._settings_logo_tint.setPixmap(out)

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

    def toggle_dynamic_color(self):
        self.parent.theme.dynamic_accent = self.dynamic_check.isChecked()

    def _toggle_auto_bg(self):
        auto = self.auto_bg_check.isChecked()
        self.parent.theme.auto_bg_from_accent = auto
        for field, btn in self._bg_btns.items():
            if auto:
                btn.setEnabled(False)
                btn.setText("auto")
                btn.setStyleSheet(self._bg_btn_style("#000000", disabled=True))
            else:
                r, g, b = (int(x) for x in getattr(self.parent.theme, field).split(','))
                hex_color = QColor(r, g, b).name()
                btn.setEnabled(True)
                btn.setText(hex_color)
                btn.setStyleSheet(self._bg_btn_style(hex_color))
        if auto:
            self.parent._auto_tint_bg_colors()
            self.parent.refresh_ui_styles()

    def _sync_bg_btns(self):
        """Refresh button labels/colors after auto-tint has updated the theme fields."""
        if not hasattr(self, '_bg_btns'):
            return
        auto = self.parent.theme.auto_bg_from_accent
        for field, btn in self._bg_btns.items():
            r, g, b = (int(x) for x in getattr(self.parent.theme, field).split(','))
            hex_color = QColor(r, g, b).name()
            btn.setText("auto" if auto else hex_color)
            btn.setStyleSheet(self._bg_btn_style(hex_color, disabled=auto))

    def _toggle_auto_border(self):
        auto = self.auto_border_check.isChecked()
        self.parent.theme.auto_border_from_accent = auto
        hex_color = self.parent.theme.manual_border_color
        if auto:
            self.border_color_btn.setEnabled(False)
            self.border_color_btn.setText("auto")
            self.border_color_btn.setStyleSheet(self._bg_btn_style(hex_color, disabled=True))
        else:
            self.border_color_btn.setEnabled(True)
            self.border_color_btn.setText(hex_color)
            self.border_color_btn.setStyleSheet(self._bg_btn_style(hex_color))
        self.parent._last_theme_key = None
        self.parent.refresh_ui_styles()

    def _pick_border_color(self):
        color = QColorDialog.getColor(
            QColor(self.parent.theme.manual_border_color), self, "Pick Border Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if color.isValid():
            hex_argb = color.name(QColor.NameFormat.HexArgb)
            self.parent.theme.manual_border_color = hex_argb
            self.border_color_btn.setText(hex_argb)
            self.border_color_btn.setStyleSheet(self._bg_btn_style(hex_argb))
            self.parent._last_theme_key = None
            self.parent.refresh_ui_styles()
     
    def update_vis_settings(self):
        self.vis_speed_label.setText(f"Responsiveness: {self.vis_speed_slider.value()}%")
        self.vis_gain_label.setText(f"Bar Height: {self.vis_gain_slider.value()}")
        if hasattr(self.parent, 'visualizer'):
            self.parent.visualizer.speed = self.vis_speed_slider.value() / 100.0
            self.parent.visualizer.gain = float(self.vis_gain_slider.value())


    def pick_global_color(self):
        self.dynamic_check.setChecked(False) 
        self.parent.theme.dynamic_accent = False
        color = QColorDialog.getColor(QColor(self.parent.theme.accent), self, "Choose Master Theme Color")
        if color.isValid():
            self.parent.theme.accent = color.name()
            self.color_btn.setStyleSheet(f"background: {self.parent.theme.accent}; color: black; padding: 10px; border-radius: 6px; font-weight: bold;")
            self._apply_slider_color()
            self.parent._auto_tint_bg_colors()
            self._sync_bg_btns()
            self.parent.refresh_ui_styles()
            if hasattr(self.parent, 'visualizer'):
                self.parent.visualizer.bar_color = QColor(self.parent.theme.accent)
            if hasattr(self.parent, '_queue_panel'):
                self.parent._queue_panel.set_accent_color(self.parent.theme.accent)
            if hasattr(self.parent, 'seek_bar'):
                self.parent.seek_bar._user_picked = True
                self.parent.seek_bar.update()
    

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



class SquareArtContainer(QWidget):
    """Paints the album art perfectly 1:1 without triggering any layout loops."""
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(100, 100) # Prevents collapsing to 0

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = 0  # top-aligned — no empty space above the art
        
        rect = QRect(x, y, side, side)
        
        painter.setBrush(QColor("#121212"))
        painter.setPen(QPen(QColor("#222222"), 1))
        painter.drawRoundedRect(rect, 5, 5)
        
        progress = getattr(self.main_window, 'crossfade_progress', 1.0)
        if not hasattr(self, 'scaled_cache'): self.scaled_cache = {}
        
        # 🟢 ULTRA-FAST CACHING PAINTER
        def draw_art(pix_attr, alpha):
            pix = getattr(self.main_window, pix_attr, None)
            if not pix or pix.isNull(): return
            
            # Only do the heavy scaling math if the window changed size!
            if self.scaled_cache.get(pix_attr, {}).get('size') != side:
                self.scaled_cache[pix_attr] = {
                    'size': side, 
                    'pix': pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                }
            scaled_pix = self.scaled_cache[pix_attr]['pix']
            
            painter.save()
            painter.setOpacity(alpha)
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), 5, 5)
            painter.setClipPath(path)
            px_x = x + (side - scaled_pix.width()) // 2
            px_y = y + (side - scaled_pix.height()) // 2
            painter.drawPixmap(px_x, px_y, scaled_pix)
            painter.restore()

        has_drawn_old = False
        
        # 1. Draw old art (Uses the pre-decoded 'old_cover_pixmap')
        if progress < 1.0 and hasattr(self.main_window, 'old_cover_pixmap') and self.main_window.old_cover_pixmap:
            draw_art('old_cover_pixmap', 1.0)
            has_drawn_old = True
            
        # 2. Draw new art (Uses the pre-decoded 'current_cover_pixmap')
        if hasattr(self.main_window, 'current_cover_pixmap') and self.main_window.current_cover_pixmap:
            draw_art('current_cover_pixmap', progress)
        elif not has_drawn_old:
            painter.setPen(QColor("#333333"))
            font = painter.font()
            font.setPixelSize(max(20, int(side * 0.3)))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "💿")

