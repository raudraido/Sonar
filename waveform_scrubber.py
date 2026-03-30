import random
import time
import os
import sys

from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget, QPushButton
from PyQt6.QtGui import QPainter, QColor, QPen, QIcon, QPixmap, QImage
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRectF, QPropertyAnimation, QSize

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class WaveformScrubber(QWidget):
    seek_requested = pyqtSignal(int)
    scratch_mode_changed = pyqtSignal(bool)
    velocity_changed = pyqtSignal(float)
    position_updated = pyqtSignal(int)
    mode_toggled = pyqtSignal(int)

    def __init__(self, master_color="#1db954", parent=None):
        super().__init__(parent)
        self.setFixedHeight(60)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.master_color = QColor(master_color)
        
        self.base_pixels_per_sample = 1.5
        self.pixels_per_sample = self.base_pixels_per_sample
        
        self.samples = [0.0] * 5000
        self.total_samples = len(self.samples)
        self.has_real_data = False

        self.current_index = 0.0
        self.is_dragging = False

        self.is_spinning_freely = False
        self.is_playing = False

        self.last_mouse_x = 0
        self.duration_ms = 1
        self.position_ms = 0
        self.visual_offset_ms = 0

        self.last_move_time = 0
        self.current_velocity = 0.0
        self.decay_timer = QTimer(self)
        self.decay_timer.setInterval(20)
        self.decay_timer.timeout.connect(self._decay_velocity)

        self.render_timer = QTimer(self)
        self.render_timer.setInterval(16)
        self.render_timer.timeout.connect(self._auto_scroll)
        self.render_timer.start()
        self.last_render_time = time.time()

        # THE DISPLAY MODE (0=Scratch, 1=Minimal, 2=SoundCloud)
        self.display_mode = 0

        self._color_cache = {}

        self.btn_toggle_wave = QPushButton(self)
        self.btn_toggle_wave.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_wave.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        raw_pixmap = QPixmap(resource_path("img/switch.png"))
        
        if not raw_pixmap.isNull():
            bright_pix = QPixmap(raw_pixmap.size())
            bright_pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(bright_pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, raw_pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(bright_pix.rect(), QColor("#ffffff")) 
            painter.end()
            self.bright_icon = QIcon(bright_pix)
            
            dim_pix = QPixmap(raw_pixmap.size())
            dim_pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(dim_pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, raw_pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(dim_pix.rect(), QColor("#515151")) 
            painter.end()
            self.dim_icon = QIcon(dim_pix)
            
            self.btn_toggle_wave.setIcon(self.dim_icon)
        
        self.btn_toggle_wave.setIconSize(QSize(16, 16))
        self.btn_toggle_wave.setFixedSize(28, 28)
        self.btn_toggle_wave.clicked.connect(self.toggle_waveform_mode)
        self.btn_toggle_wave.installEventFilter(self)
        
        self.set_master_color(master_color)

        self.toggle_opacity = None
        self.hover_anim = None
        self._init_opacity_effect()
        self.btn_toggle_wave.hide()

    def _init_opacity_effect(self):
        self.btn_toggle_wave.show()
        self.toggle_opacity = QGraphicsOpacityEffect(self.btn_toggle_wave)
        self.toggle_opacity.setOpacity(0.0)
        self.btn_toggle_wave.setGraphicsEffect(self.toggle_opacity)
        self.hover_anim = QPropertyAnimation(self.toggle_opacity, b"opacity")
        self.hover_anim.setDuration(250)
        self.hover_anim.finished.connect(self._on_hover_anim_finished)

    def _on_hover_anim_finished(self):
        if self.toggle_opacity and self.toggle_opacity.opacity() == 0.0:
            self.btn_toggle_wave.setGraphicsEffect(None)
            self.btn_toggle_wave.hide()
            self.toggle_opacity = None
            self.hover_anim = None

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj == self.btn_toggle_wave:
            if event.type() == QEvent.Type.Enter:
                if hasattr(self, 'bright_icon'):
                    self.btn_toggle_wave.setIcon(self.bright_icon)
            elif event.type() == QEvent.Type.Leave:
                if hasattr(self, 'dim_icon'):
                    self.btn_toggle_wave.setIcon(self.dim_icon)
        return super().eventFilter(obj, event)
    
    def toggle_waveform_mode(self):
        # Cycles 0 -> 1 -> 2 -> 0
        self.display_mode = (self.display_mode + 1) % 3

        if self.display_mode == 0:
            self.render_timer.start()
        else:
            self.render_timer.stop()
            
        self.mode_toggled.emit(self.display_mode)
        self.update()

    def set_master_color(self, color_hex):
        self.master_color = QColor(color_hex)
        self._color_cache.clear()
        
        r = self.master_color.red()
        g = self.master_color.green()
        b = self.master_color.blue()
        
        dim_r = int(r * 0.3)
        dim_g = int(g * 0.3)
        dim_b = int(b * 0.3)
        
        self.btn_toggle_wave.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba({r}, {g}, {b}, 0.1); 
                border: 2px solid rgb({dim_r}, {dim_g}, {dim_b}); 
                border-radius: 14px;
                outline: none; 
            }}
            QPushButton:hover {{
                background-color: rgba({r}, {g}, {b}, 0.4); 
                border: 2px solid rgb({r}, {g}, {b}); 
            }}
            QPushButton:pressed {{
                background-color: rgba({r}, {g}, {b}, 0.2);
            }}
        """)
        self.update()
    
    def resizeEvent(self, event):
        if event:
            super().resizeEvent(event)
        padding_right = 10
        padding_top = 5
        self.btn_toggle_wave.move(self.width() - self.btn_toggle_wave.width() - padding_right, padding_top)
        self._rebuild_fade_lookup()

    def _rebuild_fade_lookup(self):
        w = self.width()
        if w <= 0:
            self._fade_lookup = []
            return
        cx = w / 2.0
        self._fade_lookup = [
            max(0.0, 1.0 - (abs(x - cx) / (w / 2.0)) ** 1.6)
            for x in range(w)
        ]

    def enterEvent(self, event):
        if self.toggle_opacity is None:
            self._init_opacity_effect()
        self.hover_anim.stop()
        self.hover_anim.setEndValue(1.0)
        self.hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.toggle_opacity is None:
            super().leaveEvent(event)
            return
        self.hover_anim.stop()
        self.hover_anim.setEndValue(0.0)
        self.hover_anim.start()
        super().leaveEvent(event)
    
    def update_duration(self, duration_ms):
        self.duration_ms = duration_ms if duration_ms > 0 else 1

    def _auto_scroll(self):
        now = time.time()
        dt = now - self.last_render_time
        self.last_render_time = now

        if self.is_playing and not self.is_dragging and not self.is_spinning_freely:
            self.position_ms += (dt * 1000.0)

            if self.duration_ms > 0:
                self.position_ms = min(self.position_ms, self.duration_ms)
                ratio = self.position_ms / self.duration_ms
                self.current_index = ratio * (self.total_samples - 1)
                self.update()

    def update_position(self, engine_pos_ms):
        if not self.is_dragging and not self.is_spinning_freely:
            target_ms = engine_pos_ms + self.visual_offset_ms

            if abs(self.position_ms - target_ms) > 150:
                self.position_ms = target_ms
            else:
                self.position_ms = (self.position_ms * 0.85) + (target_ms * 0.15)

           
            if self.display_mode != 0:
                self.update()

    def set_real_samples(self, new_samples):
        if not new_samples: return
        
        self.has_real_data = True 
        
        self.samples = new_samples
        self.total_samples = len(self.samples)
        
        if self.duration_ms > 0:
            ratio = self.position_ms / self.duration_ms
            self.current_index = ratio * (self.total_samples - 1)
            
        self.update()

    def reset_waveform(self):
        self.has_real_data = False
        self.samples = [0.0] * 5000
        self.total_samples = len(self.samples)
        self.current_index = 0.0
        self.update()

    def _decay_velocity(self):
        if self.is_spinning_freely:
            target_vel = 1.0 if self.is_playing else 0.0
            self.current_velocity = self.current_velocity + (target_vel - self.current_velocity) * 0.15

            ms_shifted = self.current_velocity * 20.0
            self.position_ms += ms_shifted
            self.position_ms = max(0, min(self.position_ms, self.duration_ms))

            ratio = self.position_ms / self.duration_ms if self.duration_ms > 0 else 0
            self.current_index = ratio * (self.total_samples - 1)

            self.velocity_changed.emit(self.current_velocity)
            self.position_updated.emit(int(self.position_ms))
            self.update()

            if abs(self.current_velocity - target_vel) < 0.05:
                self.current_velocity = target_vel
                self.velocity_changed.emit(self.current_velocity)

                self.is_spinning_freely = False
                self.decay_timer.stop()

                target = max(0, int(self.position_ms - self.visual_offset_ms))
                self.seek_requested.emit(target)
                self.scratch_mode_changed.emit(False)
        else:
            if time.time() - self.last_move_time > 0.08:
                self.current_velocity *= 0.5
                if abs(self.current_velocity) < 0.05:
                    self.current_velocity = 0.0
                    self.decay_timer.stop()
                self.velocity_changed.emit(self.current_velocity)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = True
            
            if self.display_mode != 0:
                self.scratch_mode_changed.emit(True)
                click_x = event.position().x()
                self.position_ms = max(0, min((click_x / self.width()) * self.duration_ms, self.duration_ms))
                self.position_updated.emit(int(self.position_ms))
                self.update()
                event.accept()
                return

            self.is_spinning_freely = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.last_mouse_x = event.position().x()
            self.last_move_time = time.time()
            self.current_velocity = 0.0
            self.scratch_mode_changed.emit(True)
            self.velocity_changed.emit(0.0) 
            self.decay_timer.start()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            current_x = event.position().x()
            
            if self.display_mode != 0:
                self.position_ms = max(0, min((current_x / self.width()) * self.duration_ms, self.duration_ms))
                self.position_updated.emit(int(self.position_ms))
                self.update()
                return

            now = time.time()
            dt = now - self.last_move_time
            if dt < 0.001: dt = 0.001 

            delta_x = current_x - self.last_mouse_x
            total_pixels = self.total_samples * self.pixels_per_sample
            ratio = delta_x / total_pixels
            delta_ms = ratio * self.duration_ms

            self.current_velocity = -(delta_ms / (dt * 1000.0))
            self.velocity_changed.emit(self.current_velocity)

            samples_shifted = delta_x / self.pixels_per_sample
            self.current_index -= samples_shifted
            self.current_index = max(0, min(self.current_index, self.total_samples - 1))

            ratio_idx = self.current_index / (self.total_samples - 1)
            self.position_ms = int(ratio_idx * self.duration_ms)
            self.position_updated.emit(self.position_ms)

            self.last_mouse_x = current_x
            self.last_move_time = now
            self.decay_timer.start()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_dragging:
            
            if self.display_mode != 0:
                self.is_dragging = False
                target = max(0, int(self.position_ms - self.visual_offset_ms))
                self.seek_requested.emit(target)
                self.scratch_mode_changed.emit(False)
                return

            self.is_dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

            if abs(self.current_velocity) < 0.5:
                self.is_spinning_freely = False
                self.decay_timer.stop()
                target = max(0, int(self.position_ms - self.visual_offset_ms))
                self.seek_requested.emit(target)
                self.scratch_mode_changed.emit(False)
            else:
                self.is_spinning_freely = True

    def wheelEvent(self, event):
        if self.display_mode != 0:
            return 
        if not hasattr(self, 'zoom_level'):
            self.zoom_level = 1.0

        delta = event.angleDelta().y() + event.angleDelta().x()
        if delta == 0: return

        zoom_factor = 1.0 + (0.15 * (abs(delta) / 120.0))
        
        if delta > 0:
            self.zoom_level *= zoom_factor
        elif delta < 0:
            self.zoom_level /= zoom_factor
            
        self.zoom_level = max(0.1, min(self.zoom_level, 5.0))
        self.pixels_per_sample = self.base_pixels_per_sample * self.zoom_level
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False) 
        
        width, height = self.width(), self.height()
        center_x, center_y = width / 2.0, height / 2.0
        
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)

        max_bar_height = (height / 2.0) * 0.90 
        base_hue = self.master_color.hue() if self.master_color.hue() >= 0 else 150 

        # --- MODE 0: HEAVY DJ SCRATCH WAVEFORM ---
        if self.display_mode == 0:
            if getattr(self, 'has_real_data', False):
                fade_lookup = getattr(self, '_fade_lookup', None)
                if not fade_lookup or len(fade_lookup) != width:
                    self._rebuild_fade_lookup()
                    fade_lookup = self._fade_lookup

                img = getattr(self, '_waveform_image', None)
                if img is None or img.width() != width or img.height() != height:
                    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
                    self._waveform_image = img
                
                img.fill(0) 
                
                img_painter = QPainter(img)
                img_painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

                for x in range(width):
                    distance_from_center_px = x - center_x
                    sample_offset = distance_from_center_px / self.pixels_per_sample
                    exact_index = self.current_index + sample_offset

                    if 0 <= exact_index < self.total_samples - 1:
                        idx1 = int(exact_index)
                        frac = exact_index - idx1
                        raw_val = self.samples[idx1] + (self.samples[idx1 + 1] - self.samples[idx1]) * frac
                        val = raw_val * raw_val * 0.5 + raw_val * 0.5
                        bar_h = val * max_bar_height

                        fade_factor = fade_lookup[x]

                        if x < center_x:
                            brightness = int(120 + (135 * fade_factor))
                            alpha = int(255 * fade_factor)
                        else:
                            brightness = int(70 + (130 * fade_factor))
                            alpha = int(180 * fade_factor)

                        hue_shift = int(20 * raw_val)
                        final_hue = (base_hue + hue_shift) % 360
                        saturation = int(255 * max(0.3, 1.0 - (raw_val * 0.6)))

                        key = (final_hue, saturation, brightness, alpha)
                        rgba = self._color_cache.get(key)
                        if rgba is None:
                            c = QColor.fromHsv(final_hue, saturation, brightness)
                            c.setAlpha(alpha)
                            rgba = c.rgba()
                            self._color_cache[key] = rgba

                        top    = max(0, int(center_y - bar_h))
                        bottom = min(height, int(center_y + bar_h))
                        
                        img_painter.setPen(QColor.fromRgba(rgba))
                        img_painter.drawLine(x, top, x, bottom)

                img_painter.end()
                painter.drawImage(0, 0, img)
            else:
                if self.duration_ms > 1:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.setPen(QColor(100, 100, 100))
                    font = painter.font()
                    font.setPixelSize(11)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ANALYZING WAVEFORM...")

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            glow_pen = QPen(QColor(255, 255, 255, 120))
            glow_pen.setWidth(3)
            painter.setPen(glow_pen)
            painter.drawLine(int(center_x), int(center_y - max_bar_height - 4), 
                             int(center_x), int(center_y + max_bar_height + 4))
            
            core_pen = QPen(QColor(255, 255, 255, 255))
            core_pen.setWidth(1)
            painter.setPen(core_pen)
            painter.drawLine(int(center_x), int(center_y - max_bar_height - 4), 
                             int(center_x), int(center_y + max_bar_height + 4))

        # --- MODE 1: MINIMAL SEEKBAR ---
        elif self.display_mode == 1:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            from PyQt6.QtCore import QRectF
            
            track_h = 6
            track_y = center_y - (track_h / 2.0)
            
            progress = self.position_ms / max(1, self.duration_ms)
            playhead_x = progress * width
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(60, 60, 60, 150))
            painter.drawRoundedRect(QRectF(0, track_y, width, track_h), track_h/2, track_h/2)
            
            painter.setBrush(self.master_color)
            painter.drawRoundedRect(QRectF(0, track_y, playhead_x, track_h), track_h/2, track_h/2)
            
            handle_radius = 6
            painter.drawEllipse(QRectF(playhead_x - handle_radius, center_y - handle_radius, handle_radius*2, handle_radius*2))

        # --- MODE 2: BAR WAVEFORM ---
        elif self.display_mode == 2:
            if getattr(self, 'has_real_data', False):
                from waveform_renderer import render_waveform_bars
                render_waveform_bars(
                    painter, width, height,
                    self.samples, self.total_samples,
                    self.position_ms, self.duration_ms,
                    self.master_color
                )
            else:
                if self.duration_ms > 1:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.setPen(QColor(100, 100, 100))
                    font = painter.font()
                    font.setPixelSize(11)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ANALYZING WAVEFORM...")