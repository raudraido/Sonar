import os
import sys
import math

from PyQt6.QtWidgets import QWidget, QPushButton, QGraphicsOpacityEffect
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QLinearGradient, QRadialGradient,
    QPen, QGradient, QPainterPath,
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
        self.vis_mode = 0              # 0 = bars, 1 = VU meter
        self.master_color = QColor("#1db954") # Default fallback color

        # VU meter ballistic state
        self._vu_rms       = 0.0   # smoothed RMS in linear domain
        self._vu_bg        = QPixmap(resource_path("img/vuM.png"))
        self._vu_frame     = QPixmap(resource_path("img/vuM_frame.png"))
        self._raw_vis_data = []
        self._vu_debug_frame = 0

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
        self.audio_engine.vuDataReady.connect(self.update_vu_data)

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
        self.vis_mode = (self.vis_mode + 1) % 2
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

        # Store raw FFT magnitudes for VU meter (no tilt/boost/smoothing)
        self._raw_vis_data = raw

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

    
    def update_vu_data(self, true_rms: float):
        if not getattr(self, 'visualizer_enabled', True):
            return
            
        # Store the clean time-domain RMS
        self._raw_vu_rms = true_rms
        
        # Only trigger a repaint if we are actually looking at the VU meter
        if self.vis_mode == 1:
            self.update()
    # -----------------------------
    
    # ── Painting ──────────────────────────────────────────────────────────────

    def _rebuild_floor_pen(self, w):
        """Build the floor-line pen once per resize instead of every frame."""
        fl_grad = QLinearGradient(0, 0, w, 0)
        for pos, (fr, fg, fb) in SPECTRUM:
            fl_grad.setColorAt(pos, QColor(fr, fg, fb, 130))
        self._floor_pen = QPen(QBrush(fl_grad), 1.0)
        self._floor_pen_width = w

    def paintEvent(self, event):
        if not getattr(self, 'visualizer_enabled', True):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.vis_mode == 0:
            self._paint_bars(painter)
        else:
            self._paint_vu_meter(painter)

        painter.end()

    def _paint_bars(self, painter):
        painter.setPen(Qt.PenStyle.NoPen)

        W, H   = self.width(), self.height()
        BASE_Y = float(H)

        slot_w = W / self.num_bars
        bar_w  = slot_w * 0.72
        gap    = (slot_w - bar_w) / 2
        radius = min(bar_w * 0.5, 4.5)

        for i in range(self.num_bars):
            val   = self.vis_data[i]
            bar_h = max(2.0, val * H * HEIGHT_SCALE)
            x     = i * slot_w + gap
            y_top = BASE_Y - bar_h
            painter.setBrush(self.bar_brushes[i])
            painter.drawRoundedRect(QRectF(x, y_top, bar_w, bar_h), radius, radius)

        if self._floor_pen is None or self._floor_pen_width != W:
            self._rebuild_floor_pen(W)
        painter.setPen(self._floor_pen)
        painter.drawLine(QPointF(0, BASE_Y), QPointF(W, BASE_Y))

    def _paint_vu_meter(self, painter):
        W, H = self.width(), self.height()

        # ── Layer 1: full background (glass + frame) ──────────────────────────
        if not self._vu_bg.isNull():
            iw, ih = self._vu_bg.width(), self._vu_bg.height()
            scale  = min(W / iw, H / ih)
            dw, dh = int(iw * scale), int(ih * scale)
            dx, dy = (W - dw) // 2, (H - dh) // 2
            radius = 8.0
            path = QPainterPath()
            path.addRoundedRect(QRectF(dx, dy, dw, dh), radius, radius)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(dx, dy, dw, dh, self._vu_bg)
            painter.restore()
        else:
            dx, dy, dw, dh = 0, 0, W, H

        # ── Geometry calibrated to vuM.png (995×503) via pixel scan + circle fit ─
        # Arc clusters found at: (327,127),(390,112),(475,103),(500,103),(579,110),(654,119)
        # Circle fit (algebraic least-squares): cx=502.7, cy=774.9, R=671.8
        # Fractions: cx/995=0.5052, cy/503=1.5406 (pivot below image), R/995=0.6752
        # HALF_SPAN = asin((502.7-38)/671.8) ≈ 30.5°
        HALF_SPAN  = math.radians(30.5)
        EXTRA_SPAN = math.radians(3.0)   # total extra degrees at +3dB
        R   = dw * 0.675
        px  = dx + dw * 0.505
        py  = dy + dh * 0.205 + R     # pivot ~1.54× image height below top
        R_ndl = R * 0.970             # needle reaches near the scale arc

        def pt(angle, r):
            return QPointF(px + r * math.sin(angle), py - r * math.cos(angle))

        # t-values derived from measured cluster x-positions on image scale marks
        MARKS = [
            (-20, 0.000), (-10, 0.149), (-7, 0.252), (-5, 0.341),
            (-3,  0.438), (-2,  0.529), (-1, 0.617), ( 0, 0.713),
            ( 1,  0.799), ( 2,  0.885), ( 3, 1.000),
        ]

        def db2a(db):
            db = max(-20.0, min(3.0, db))
            for i in range(len(MARKS) - 1):
                d0, t0 = MARKS[i]; d1, t1 = MARKS[i + 1]
                if d0 <= db <= d1:
                    f = (db - d0) / (d1 - d0)
                    t = t0 + f * (t1 - t0)
                    angle = -HALF_SPAN + t * HALF_SPAN * 2.0
                    if db > 0.0:
                        angle += EXTRA_SPAN * (db / 3.0)
                    return angle
            return HALF_SPAN + EXTRA_SPAN

        # Grab the raw time-domain RMS from C++
        rms_instant = getattr(self, '_raw_vu_rms', 0.0)

        # ── 1. Electrical Smoothing (The Circuit) ───────────────────────────────
        ALPHA = 0.948
        if not hasattr(self, '_vu_rms'):
            self._vu_rms = 0.0
            
        self._vu_rms = ALPHA * self._vu_rms + (1.0 - ALPHA) * rms_instant
        
        # Calculate the Target Decibels (+10 offset for modern music)
        db_level = 20.0 * math.log10(max(self._vu_rms, 1e-9)) + 9.0

        # Calculate exactly where the audio wants the needle to point
        target_a  = db2a(max(-20.0, min(3.0, db_level)))

        # ── 2. Mechanical Smoothing (The Physical Needle) ───────────────────────
        # This determines how "heavy" the needle is. 
        # Lower number = heavier/smoother. Higher number = lighter/faster.
        NEEDLE_SPEED = 0.25 

        if not hasattr(self, '_current_a'):
            self._current_a = target_a
            
        # The needle glides toward the target instead of snapping to it instantly,
        # completely destroying any high-frequency micro-vibrations.
        self._current_a += (target_a - self._current_a) * NEEDLE_SPEED
        
        # Feed the smoothed angle to the drawing function
        needle_a = self._current_a

        # ── Needle + Pivot (clipped to image bounds) ──────────────────────────
        painter.save()
        painter.setClipPath(path)

        tip  = pt(needle_a, R_ndl)
        tail = pt(needle_a, 0)   # start from pivot — clip handles the cutoff
        base = QPointF(px, py)
        off  = QPointF(1.0, 0.8)
        painter.setPen(QPen(QColor(0, 0, 0, 45), 2.0,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(tail + off, tip + off)
        painter.setPen(QPen(QColor(18, 15, 10), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(tail, tip)

        pr = max(3.0, dh * 0.034)
        rg = QRadialGradient(px - pr * 0.38, py - pr * 0.38, pr * 2.0)
        rg.setColorAt(0.0, QColor(110, 100, 84))
        rg.setColorAt(1.0, QColor(24, 20, 14))
        painter.setBrush(QBrush(rg))
        painter.setPen(QPen(QColor(10, 8, 5), 1.0))
        painter.drawEllipse(base, pr, pr)

        painter.restore()

        # ── Layer 3: frame overlay (transparent glass, opaque bezel) ──────────
        # Drawn on top so the frame covers the needle tail naturally.
        if not self._vu_frame.isNull():
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(dx, dy, dw, dh, self._vu_frame)
            painter.restore()