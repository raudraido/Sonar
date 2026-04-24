"""
player/widgets.py — Reusable custom Qt widgets.

These are self-contained UI components with no dependency on
the main SonarPlayer window (except SettingsWindow which takes a
parent reference so it can write back to visual_settings).
"""
import os
import re

from PyQt6.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy,
    QSlider, QPushButton, QColorDialog, QCheckBox, QApplication,
    QMessageBox, QScrollArea, QFrame, QGridLayout, QFileDialog
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, pyqtSignal, QSettings, QProcess
from PyQt6.QtGui import (
    QFont, QFontMetrics, QColor, QMouseEvent, QPainter, QPen,
    QBrush, QPainterPath, QPolygon, QPixmap, QKeySequence
)

from audio_engine import AudioEngine

class ElidedLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full_text = text
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
            self.setStyleSheet("color: white; background: transparent; text-decoration: underline;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet("color: white; background: transparent; text-decoration: none;")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if event.pos().x() <= self._text_w():
            self.setStyleSheet("color: white; background: transparent; text-decoration: underline;")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setStyleSheet("color: white; background: transparent; text-decoration: none;")
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.pos().x() <= self._text_w():
            self.clicked.emit()
        super().mousePressEvent(event)



class _ArtLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._hovered:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # dim overlay
        p.fillRect(self.rect(), QColor(0, 0, 0, 80))
        # upward arrow centred near the top
        cx = self.width() / 2
        cy = self.height() / 2 - 4
        aw, ah = 10, 7
        path = QPainterPath()
        path.moveTo(cx,        cy - ah)
        path.lineTo(cx + aw,   cy + ah)
        path.lineTo(cx - aw,   cy + ah)
        path.closeSubpath()
        p.setBrush(QColor(255, 255, 255, 220))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class NowPlayingFooterWidget(QWidget):
    artist_clicked = pyqtSignal(str)
    album_clicked = pyqtSignal()
    title_clicked = pyqtSignal()
    art_clicked = pyqtSignal()
    track_right_clicked = pyqtSignal(object)  # emits the current track dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_track = None

        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setFixedHeight(74)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(12)
        
        # 1. Tiny Cover Art
        self.art_label = _ArtLabel()
        self.art_label.setFixedSize(64, 64)
        self.art_label.setStyleSheet("background-color: #222; border-radius: 4px; border: 1px solid #333;")
        self.art_label.setScaledContents(True)
        self.art_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.art_label.hide()
        self.art_label.clicked.connect(self.art_clicked)
        
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
        f.setPixelSize(15)
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

        text_layout.addWidget(self.title_lbl)
        text_layout.addWidget(self.artist_widget)
        text_layout.addWidget(self.album_lbl)
        
        layout.addWidget(self.art_label)
        layout.addWidget(text_container, 1)
                
    def update_info(self, title, artist, album):
        self.title_lbl.setText(title)
        
        # Update Artists
        while self.artist_layout.count():
            child = self.artist_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

        parts = re.split(r'( /// | • | / | feat\. | Feat\. | vs\. )', artist)
        
        for part in parts:
            if not part: continue
            #if re.match(r'( • | / | feat\. | vs\. )', part):
            if re.match(r'( /// | • | / | feat\. | Feat\. | vs\. )', part):
                sep_lbl = QLabel(part)
                sep_lbl.setStyleSheet("font-size: 13px; color: #777; background: transparent;")
                self.artist_layout.addWidget(sep_lbl)
            else:
                lbl = FooterClickableLabel(part)
                lbl.clicked.connect(self.artist_clicked.emit)
                self.artist_layout.addWidget(lbl)
        
        self.artist_layout.addStretch()

        if album:
            self.album_lbl.setText(album)
            self.album_lbl.show()
        else:
            self.album_lbl.hide()
            
    def set_track(self, track):
        self._current_track = track

    def set_cover(self, pixmap):
        if pixmap and not pixmap.isNull():
            self.art_label.setPixmap(pixmap)
            self.art_label.show()
        else:
            self.art_label.clear()
            self.art_label.hide()
            


class FooterClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.full_text = text
        self.setMouseTracking(True)
        self.setStyleSheet("font-size: 13px; color: #bbb; background: transparent; text-decoration: none;")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _text_w(self):
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    def enterEvent(self, event):
        from PyQt6.QtGui import QCursor
        if self.mapFromGlobal(QCursor.pos()).x() <= self._text_w():
            self.setStyleSheet("font-size: 13px; color: #fff; background: transparent; text-decoration: underline;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet("font-size: 13px; color: #bbb; background: transparent; text-decoration: none;")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if event.pos().x() <= self._text_w():
            self.setStyleSheet("font-size: 13px; color: #fff; background: transparent; text-decoration: underline;")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setStyleSheet("font-size: 13px; color: #bbb; background: transparent; text-decoration: none;")
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
        super().__init__()
        self.parent = parent
        self.setWindowTitle("Settings")
        self.setFixedWidth(420)
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setStyleSheet("background-color: #181818; color: #ddd; font-family: sans-serif;")
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

                
        layout.addWidget(QLabel("Theme:"))
        self.dynamic_check = QCheckBox("Auto-Match Color from Album Art")
        self.dynamic_check.setChecked(self.parent.dynamic_color)
        self.dynamic_check.stateChanged.connect(self.toggle_dynamic_color)
        layout.addWidget(self.dynamic_check)

        self.color_btn = QPushButton("Pick Static Color")
        self.color_btn.clicked.connect(self.pick_global_color)
        self.color_btn.setStyleSheet(f"background: {self.parent.master_color}; color: black; padding: 10px; border-radius: 6px; font-weight: bold;")
        layout.addWidget(self.color_btn)

        # --- STATIC BACKGROUND IMAGE ---
        bg_img_label = QLabel("Static Background Image:")
        bg_img_label.setStyleSheet("color: #aaa; margin-top: 4px;")
        layout.addWidget(bg_img_label)

        bg_row = QHBoxLayout()
        self.bg_img_btn = QPushButton("Choose Image…")
        self.bg_img_btn.clicked.connect(self.pick_static_bg)
        self.bg_img_btn.setStyleSheet("padding: 8px 14px; border-radius: 6px; background: #2a2a2a; color: #ddd;")
        bg_row.addWidget(self.bg_img_btn)

        self.bg_img_clear = QPushButton("Clear")
        self.bg_img_clear.clicked.connect(self.clear_static_bg)
        self.bg_img_clear.setStyleSheet("padding: 8px 14px; border-radius: 6px; background: #2a2a2a; color: #888;")
        self.bg_img_clear.setVisible(bool(getattr(self.parent, 'static_bg_path', None)))
        bg_row.addWidget(self.bg_img_clear)
        layout.addLayout(bg_row)

        current_path = getattr(self.parent, 'static_bg_path', None)
        self.bg_img_name = QLabel(os.path.basename(current_path) if current_path else "None")
        self.bg_img_name.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.bg_img_name)

        layout.addWidget(QLabel("Background:"))
        _blur_pct = int(min(self.parent.visual_settings['blur'], 5) / 5 * 100)
        self.blur_label = QLabel(f"Blur Radius: {_blur_pct}%")

        layout.addWidget(self.blur_label)
        self.blur_slider = QSlider(Qt.Orientation.Horizontal)
        self.blur_slider.setRange(0, 100)
        self.blur_slider.setValue(_blur_pct)
        self.blur_slider.valueChanged.connect(self.update_labels_only)

        layout.addWidget(self.blur_slider)
        self.dark_label = QLabel(f"Darkness Blend: {int(self.parent.visual_settings['overlay'] * 100)}%")

        layout.addWidget(self.dark_label)
        self.dark_slider = QSlider(Qt.Orientation.Horizontal)
        self.dark_slider.setRange(0, 100)
        self.dark_slider.setValue(int(self.parent.visual_settings['overlay'] * 100))
        self.dark_slider.valueChanged.connect(self.update_labels_only)

        layout.addWidget(self.dark_slider)
        self.alpha_label = QLabel(f"Playlist Opacity: {int(self.parent.visual_settings['bg_alpha'] * 100)}%")

        layout.addWidget(self.alpha_label)
        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(int(self.parent.visual_settings['bg_alpha'] * 100))
        self.alpha_slider.valueChanged.connect(self.update_labels_only)
        layout.addWidget(self.alpha_slider)

        # --- THE NEW FOOTER SLIDER HERE
        self.footer_alpha_label = QLabel(f"Footer Opacity: {int(self.parent.visual_settings.get('footer_alpha', 0.85) * 100)}%")
        layout.addWidget(self.footer_alpha_label)

        self.footer_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.footer_alpha_slider.setRange(0, 100)
        self.footer_alpha_slider.setValue(int(self.parent.visual_settings.get('footer_alpha', 0.85) * 100))
        self.footer_alpha_slider.valueChanged.connect(self.update_labels_only)
        layout.addWidget(self.footer_alpha_slider)

        self.queue_alpha_label = QLabel(f"Queue Opacity: {int(self.parent.visual_settings.get('queue_alpha', 0.96) * 100)}%")
        layout.addWidget(self.queue_alpha_label)

        self.queue_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.queue_alpha_slider.setRange(0, 100)
        self.queue_alpha_slider.setValue(int(self.parent.visual_settings.get('queue_alpha', 0.96) * 100))
        self.queue_alpha_slider.valueChanged.connect(self.update_labels_only)
        layout.addWidget(self.queue_alpha_slider)

        # Single debounce timer — restarted on every valueChanged, fires apply_heavy_changes
        # 150ms after the user stops interacting (covers drag, arrow keys, and bar clicks).
        from PyQt6.QtCore import QTimer as _QTimer
        self._apply_debounce = _QTimer(self)
        self._apply_debounce.setSingleShot(True)
        self._apply_debounce.setInterval(150)
        self._apply_debounce.timeout.connect(self.apply_heavy_changes)
        for _s in (self.blur_slider, self.dark_slider, self.alpha_slider,
                   self.footer_alpha_slider, self.queue_alpha_slider):
            _s.valueChanged.connect(self._apply_debounce.start)

        layout.addSpacing(15)

        # --- HOTKEYS SECTION ---
        if hasattr(self.parent, 'hotkey_manager'):
            from hotkeys import DEFAULT_HOTKEYS

            hotkeys_label = QLabel("HOTKEYS")
            hotkeys_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold; letter-spacing: 2px;")
            layout.addWidget(hotkeys_label)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setFixedHeight(300)
            scroll.setStyleSheet("""
                QScrollArea { background: transparent; border: none; }
                QScrollBar:vertical { border: none; background: rgba(0,0,0,0.05); width: 6px; margin: 0; }
                QScrollBar::handle:vertical { background: #444; min-height: 20px; border-radius: 3px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            """)

            content = QWidget()
            content.setStyleSheet("background: transparent;")
            grid = QGridLayout(content)
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

            scroll.setWidget(content)
            layout.addWidget(scroll)
            layout.addSpacing(10)

        # --- LOGOUT BUTTON ---
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
        layout.addWidget(self.logout_btn)
        
        layout.addStretch()

    def toggle_dynamic_color(self):
        self.parent.dynamic_color = self.dynamic_check.isChecked()
     
    def update_vis_settings(self):
        self.vis_speed_label.setText(f"Responsiveness: {self.vis_speed_slider.value()}%")
        self.vis_gain_label.setText(f"Bar Height: {self.vis_gain_slider.value()}")
        if hasattr(self.parent, 'visualizer'):
            self.parent.visualizer.speed = self.vis_speed_slider.value() / 100.0
            self.parent.visualizer.gain = float(self.vis_gain_slider.value())

    def pick_static_bg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Background Image", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp)"
        )
        if not path:
            return
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > 2:
            QMessageBox.warning(self, "File Too Large",
                f"Image must be under 2 MB (selected file is {size_mb:.1f} MB).")
            return
        self.parent.static_bg_path = path
        self.bg_img_name.setText(os.path.basename(path))
        self.bg_img_clear.setVisible(True)
        if hasattr(self.parent, 'apply_static_background'):
            self.parent.apply_static_background()

    def clear_static_bg(self):
        self.parent.static_bg_path = None
        self.bg_img_name.setText("None")
        self.bg_img_clear.setVisible(False)
        # Trigger a normal background refresh from current track
        if hasattr(self.parent, 'visual_update_timer'):
            self.parent._last_rendered_cid = None
            self.parent.visual_update_timer.start(100)

    def pick_global_color(self):
        self.dynamic_check.setChecked(False) 
        self.parent.dynamic_color = False
        color = QColorDialog.getColor(QColor(self.parent.master_color), self, "Choose Master Theme Color")
        if color.isValid():
            self.parent.master_color = color.name(); self.color_btn.setStyleSheet(f"background: {self.parent.master_color}; color: black; padding: 10px; border-radius: 6px; font-weight: bold;")
            self.parent.refresh_ui_styles()
            if hasattr(self.parent, 'visualizer'): self.parent.visualizer.bar_color = QColor(self.parent.master_color)
    
    def update_labels_only(self):
        self.blur_label.setText(f"Blur Radius: {self.blur_slider.value()}%")
        self.dark_label.setText(f"Darkness Blend: {self.dark_slider.value()}%")
        self.alpha_label.setText(f"Playlist Opacity: {self.alpha_slider.value()}%")
        
        self.footer_alpha_label.setText(f"Footer Opacity: {self.footer_alpha_slider.value()}%")
        self.queue_alpha_label.setText(f"Queue Opacity: {self.queue_alpha_slider.value()}%")
    
    def apply_heavy_changes(self):
        self.parent.visual_settings['blur'] = round(self.blur_slider.value() / 100 * 5, 2)
        self.parent.visual_settings['overlay'] = self.dark_slider.value() / 100.0
        self.parent.visual_settings['bg_alpha'] = self.alpha_slider.value() / 100.0
        
        self.parent.visual_settings['footer_alpha'] = self.footer_alpha_slider.value() / 100.0
        self.parent.visual_settings['queue_alpha']  = self.queue_alpha_slider.value() / 100.0

        if hasattr(self.parent, 'refresh_visuals'): self.parent.refresh_visuals()

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
                try: keyring.delete_password("Sonar", user)
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
        # 🟢 THE FIX: Strictly lock the visualizer's width to match the square
        if hasattr(self.main_window, 'visualizer'):
            side = min(self.width(), self.height())
            self.main_window.visualizer.setMaximumWidth(side)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        side = min(self.width(), self.height())
        x = self.width() - side
        y = (self.height() - side) // 2
        rect = QRect(x, y, side, side)
        
        painter.setBrush(QColor("#121212"))
        painter.setPen(QPen(QColor("#222222"), 1))
        painter.drawRoundedRect(rect, 15, 15)
        
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
            path.addRoundedRect(QRectF(rect), 15, 15)
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

