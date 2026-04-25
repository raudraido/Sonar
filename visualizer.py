import os
import sys
import math

from PyQt6.QtWidgets import QWidget, QPushButton, QGraphicsOpacityEffect
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QLinearGradient, QPen, QGradient,
    QIcon, QPixmap
)
from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QPropertyAnimation, QEvent


import os
import sys

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline constants
# ─────────────────────────────────────────────────────────────────────────────

NUM_BARS      = 64      # Optimal balance of resolution and CPU efficiency
SAMPLE_RATE   = 44100
VIS_FFT_N     = 8192     # High resolution for true log scaling

# Frequency bounds
MIN_FREQ      = 20.0     # Deepest sub-bass
MAX_FREQ      = 15000.0  # Upper limit of human hearing priority

# ─────────────────────────────────────────────────────────────────────────────
#  Smoothing / animation
# ─────────────────────────────────────────────────────────────────────────────
ATTACK        = 0.20   # Ultra-stable attack (paired with Flat-Top window)
DECAY         = 0.92   # Soft, graceful decay
HEIGHT_SCALE  = 0.78   # Maximum bar height as fraction of widget height

# ─────────────────────────────────────────────────────────────────────────────
#  Spectral colour palette  (position 0–1  →  RGB)
# ─────────────────────────────────────────────────────────────────────────────
SPECTRUM = [
    (0.00, (90,   0, 255)),   # deep violet  — sub-bass
    (0.22, (0,  160, 255)),   # electric cyan — bass
    (0.45, (0,  230, 120)),   # mint green   — low-mid
    (0.68, (255, 190,   0)),  # amber        — mid
    (1.00, (255,  30, 140)),  # hot pink     — hi-mid / presence
]

def lerp_color(t: float):
    t = max(0.0, min(1.0, t))
    for i in range(len(SPECTRUM) - 1):
        p0, c0 = SPECTRUM[i]
        p1, c1 = SPECTRUM[i + 1]
        if t <= p1:
            f = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
            return (
                int(c0[0] + f * (c1[0] - c0[0])),
                int(c0[1] + f * (c1[1] - c0[1])),
                int(c0[2] + f * (c1[2] - c0[2])),
            )
    return SPECTRUM[-1][1]

class AudioVisualizer(QWidget):
    def __init__(self, audio_engine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine

        self.visualizer_enabled = True # The Master Switch
        self.master_color = QColor("#1db954") # Default fallback color

        self.num_bars = NUM_BARS
        self.vis_data = [0.0] * self.num_bars

        # 1. PRE-CALCULATE COLORS
        self.bar_colors = []
        for i in range(self.num_bars):
            t = i / max(self.num_bars - 1, 1)
            self.bar_colors.append(lerp_color(t))

        # 2. PRE-CALCULATE GRADIENT BRUSHES (CPU Saver!)
        self.bar_brushes = []
        for i in range(self.num_bars):
            r, g, b = self.bar_colors[i]
            
            grad = QLinearGradient(0.0, 0.0, 0.0, 1.0)
            grad.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
            
            grad.setColorAt(0.0, QColor(r,       g,       b,       255))
            grad.setColorAt(0.5, QColor(r // 2,  g // 2,  b // 2,  210))
            grad.setColorAt(1.0, QColor(r // 5,  g // 5,  b // 5,   70))
            
            self.bar_brushes.append(QBrush(grad))

        # 3. PRE-CALCULATE TRUE LOGARITHMIC BINS
        self.bar_bins = []
        hz_per_bin = SAMPLE_RATE / VIS_FFT_N  
        
        for i in range(self.num_bars):
            f_start = MIN_FREQ * ((MAX_FREQ / MIN_FREQ) ** (i / self.num_bars))
            f_end   = MIN_FREQ * ((MAX_FREQ / MIN_FREQ) ** ((i + 1) / self.num_bars))
            
            bin_start = int(f_start / hz_per_bin)
            bin_end   = int(f_end / hz_per_bin)
            
            if bin_end <= bin_start:
                bin_end = bin_start + 1
                
            self.bar_bins.append((bin_start, bin_end))

        self.setMinimumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        # Pre-cache floor line pen — rebuilt only on resizeEvent, not every frame
        self._floor_pen = None
        self._floor_pen_width = -1

        self.audio_engine.visualizerDataReady.connect(self.update_data)

        # 4. GHOST TOGGLE BUTTON SETUP
        self.btn_toggle_vis = QPushButton(self)
        self.btn_toggle_vis.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_vis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        #self.btn_toggle_vis.setToolTip("Switch Visualizer Style")
        
        raw_pixmap = QPixmap(resource_path("img/switch.png"))
        if not raw_pixmap.isNull():
            # BRIGHT ICON
            bright_pix = QPixmap(raw_pixmap.size())
            bright_pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(bright_pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, raw_pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(bright_pix.rect(), QColor("#ffffff")) 
            painter.end()
            self.bright_icon = QIcon(bright_pix) 
            
            # DIM ICON 
            dim_pix = QPixmap(raw_pixmap.size())
            dim_pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(dim_pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawPixmap(0, 0, raw_pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(dim_pix.rect(), QColor("#515151")) 
            painter.end()
            self.dim_icon = QIcon(dim_pix) 
            
            self.btn_toggle_vis.setIcon(self.dim_icon)
        
        self.btn_toggle_vis.setIconSize(QSize(16, 16))
        self.btn_toggle_vis.setFixedSize(28, 28)
        self.btn_toggle_vis.clicked.connect(self.toggle_mode)
        
        # Event filter for instant icon swaps
        self.btn_toggle_vis.installEventFilter(self)
        
        # --- HOVER FADE: We manage the effect lifecycle manually so the GPU
        # offscreen framebuffer is DESTROYED when the button is invisible and
        # only CREATED on hover. QGraphicsOpacityEffect allocated permanently
        # kept a composited GPU layer alive 100% of the time even at opacity 0.
        self._btn_opacity_value = 0.0
        self.toggle_opacity = None   # Created on first hover, destroyed on fade-out
        self.hover_anim = None
        self._init_opacity_effect()
        self.btn_toggle_vis.hide()  # Hidden until mouse enters the widget

        # Apply initial button style with default color
        self.set_master_color(self.master_color.name())

    def _init_opacity_effect(self):
        """Creates the opacity effect + animation. Called once, and again after teardown."""
        self.btn_toggle_vis.show()
        self.toggle_opacity = QGraphicsOpacityEffect(self.btn_toggle_vis)
        self.toggle_opacity.setOpacity(0.0)
        self.btn_toggle_vis.setGraphicsEffect(self.toggle_opacity)

        self.hover_anim = QPropertyAnimation(self.toggle_opacity, b"opacity")
        self.hover_anim.setDuration(250)
        self.hover_anim.finished.connect(self._on_hover_anim_finished)

    def _on_hover_anim_finished(self):
        """When a fade-out completes, hide the button and destroy the GPU framebuffer."""
        if self.toggle_opacity and self.toggle_opacity.opacity() == 0.0:
            self.btn_toggle_vis.setGraphicsEffect(None)
            self.btn_toggle_vis.hide()
            self.toggle_opacity = None
            self.hover_anim = None

    # ── Color bridge ─────────────────────────────────────────────────────────

    @property
    def bar_color(self):
        """Kept for backward-compat with main.py which sets visualizer.bar_color."""
        return self.master_color

    @bar_color.setter
    def bar_color(self, color: QColor):
        self.set_master_color(color.name())

    def set_master_color(self, color_hex: str):
        """Dynamically updates the button colors when the album changes.
        Identical signature and logic to WaveformScrubber.set_master_color."""
        self.master_color = QColor(color_hex)

        r = self.master_color.red()
        g = self.master_color.green()
        b = self.master_color.blue()

        dim_r = int(r * 0.3)
        dim_g = int(g * 0.3)
        dim_b = int(b * 0.3)

        self.btn_toggle_vis.setStyleSheet(f"""
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

    def eventFilter(self, obj, event):
        if obj == self.btn_toggle_vis:
            if event.type() == QEvent.Type.Enter:
                if hasattr(self, 'bright_icon'):
                    self.btn_toggle_vis.setIcon(self.bright_icon)
            elif event.type() == QEvent.Type.Leave:
                if hasattr(self, 'dim_icon'):
                    self.btn_toggle_vis.setIcon(self.dim_icon)
        return super().eventFilter(obj, event)

    def toggle_mode(self):
        self.visualizer_enabled = not self.visualizer_enabled
        self.audio_engine.set_visualizer_active(self.visualizer_enabled)
        if not self.visualizer_enabled:
            self.vis_data = [0.0] * self.num_bars  # Clear bars so widget paints blank once, then stops receiving updates
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Rebuild floor line pen now that width has changed
        self._rebuild_floor_pen(self.width())

    def enterEvent(self, event):
        if self.btn_toggle_vis.parent() is not self:
            super().enterEvent(event)
            return
        if self.toggle_opacity is None:
            self._init_opacity_effect()
        self.hover_anim.stop()
        self.hover_anim.setEndValue(1.0)
        self.hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.btn_toggle_vis.parent() is not self:
            super().leaveEvent(event)
            return
        if self.toggle_opacity is None:
            super().leaveEvent(event)
            return
        self.hover_anim.stop()
        self.hover_anim.setEndValue(0.0)
        self.hover_anim.start()
        super().leaveEvent(event)


    # ── Data processing ───────────────────────────────────────────────────────

    def update_data(self, raw: list):
        # KILL SWITCH: Instantly skips all expensive audio math if disabled
        if not getattr(self, 'visualizer_enabled', True) or not raw:
            return

        n = len(raw)
        EXP = 0.55

        for i in range(self.num_bars):
            lo = int((i       / self.num_bars) ** (1.0 / EXP) * n)
            hi = int(((i + 1) / self.num_bars) ** (1.0 / EXP) * n)
            hi = max(hi, lo + 1)

            chunk = raw[lo:min(hi, n)]
            peak = max(chunk) if chunk else 0.0  

            tilt_boost = 1.0 + (i / self.num_bars) * 1.5 
            peak *= tilt_boost

            target = min(math.log1p(abs(peak) * 450.0) / 3.5, 1.0)

            cur = self.vis_data[i]
            if target > cur:
                self.vis_data[i] = cur * (1.0 - ATTACK) + target * ATTACK
            else:
                self.vis_data[i] = cur * DECAY + target * (1.0 - DECAY)

        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def _rebuild_floor_pen(self, w):
        """Build the floor-line pen once per resize instead of every frame."""
        fl_grad = QLinearGradient(0, 0, w, 0)
        for pos, (fr, fg, fb) in SPECTRUM:
            fl_grad.setColorAt(pos, QColor(fr, fg, fb, 130))
        self._floor_pen = QPen(QBrush(fl_grad), 1.0)
        self._floor_pen_width = w

    def paintEvent(self, event):
        # KILL SWITCH: Prevents Qt from rendering anything if disabled
        if not getattr(self, 'visualizer_enabled', True):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        W, H   = self.width(), self.height()
        BASE_Y = float(H)

        slot_w = W / self.num_bars
        bar_w  = slot_w * 0.72
        gap    = (slot_w - bar_w) / 2
        radius = min(bar_w * 0.5, 4.5)

        # ── Bars ──────────────────────────────────────────────────────────────
        for i in range(self.num_bars):
            val     = self.vis_data[i]
            bar_h   = max(2.0, val * H * HEIGHT_SCALE)
            x       = i * slot_w + gap
            y_top   = BASE_Y - bar_h

            painter.setBrush(self.bar_brushes[i])
            painter.drawRoundedRect(QRectF(x, y_top, bar_w, bar_h), radius, radius)

        # ── Baseline floor line — use cached pen, rebuild only if width changed ──
        if self._floor_pen is None or self._floor_pen_width != W:
            self._rebuild_floor_pen(W)

        painter.setPen(self._floor_pen)
        painter.drawLine(QPointF(0, BASE_Y), QPointF(W, BASE_Y))

        painter.end()