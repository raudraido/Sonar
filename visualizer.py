import math

from PyQt6.QtWidgets import QWidget, QPushButton, QGraphicsOpacityEffect, QLabel, QHBoxLayout, QApplication
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QLinearGradient, QRadialGradient,
    QPen, QGradient, QPainterPath,
    QIcon, QPixmap
)
from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QPropertyAnimation, QEvent, QSettings, QTimer
from player import resource_path

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
        self.vis_mode = 1              # 0 = bars, 1 = VU meter
        self.master_color = QColor("#1db954") # Default fallback color

        # VU meter ballistic state
        self._vu_rms       = 0.0   # smoothed RMS in linear domain
        self._vu_bg        = QPixmap(resource_path("img/vuM.png"))
        self._vu_frame     = QPixmap(resource_path("img/vuM_frame.png"))
        self._vu_bg_scaled    = QPixmap()
        self._vu_frame_scaled = QPixmap()
        self._douk_frame   = QPixmap(resource_path("img/new_vu_frame.png"))
        self._douk_left    = QPixmap(resource_path("img/new_vu_left.png"))
        self._douk_right   = QPixmap(resource_path("img/new_vu_right.png"))
        self._douk_knob    = QPixmap(resource_path("img/vu_gain_knob_inner.png"))
        self._douk_scaled_frame = QPixmap()
        self._douk_scaled_left  = QPixmap()
        self._douk_scaled_right = QPixmap()
        self._douk_scaled_w = 0
        self._douk_scaled_h = 0
        self._knob_drag_y   = None   # mouse y at drag start
        self._knob_drag_ref = None   # ref level at drag start
        self._raw_vis_data = []
        self._vu_debug_frame = 0
        _s = QSettings("Icosahedron", "Visualizer")
        self._vu_ref_level = int(_s.value("vu_ref_level", -10))

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

        self._bg_color: QColor | None = None

        self.setMinimumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        # Pre-cache floor line pen — rebuilt only on resizeEvent, not every frame
        self._floor_pen = None
        self._floor_pen_width = -1

        self.audio_engine.visualizerDataReady.connect(self.update_data)
        self.audio_engine.vuDataReady.connect(self.update_vu_data)

        # Vsync-locked render timer — drives all repaints at the screen refresh rate
        screen = QApplication.primaryScreen()
        hz = screen.refreshRate() if screen else 60.0
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(max(1, round(1000.0 / hz)))
        self._render_timer.timeout.connect(self._render_tick)
        self._render_timer.start()

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

        # 5. REF LEVEL PILL CONTROL — always visible in VU mode
        self._ref_container = QWidget(self)
        self._ref_container.setFixedSize(110, 22)
        self._ref_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        _ref_lay = QHBoxLayout(self._ref_container)
        _ref_lay.setContentsMargins(0, 0, 0, 0)
        _ref_lay.setSpacing(0)

        self.btn_ref_minus = QPushButton("−", self._ref_container)
        self.btn_ref_minus.setFixedSize(22, 22)
        self.btn_ref_minus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_ref_minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ref_minus.clicked.connect(self._dec_ref_level)

        self.lbl_ref_level = QLabel(f"{self._vu_ref_level} dBFS", self._ref_container)
        self.lbl_ref_level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_ref_level.setFixedHeight(22)

        self.btn_ref_plus = QPushButton("+", self._ref_container)
        self.btn_ref_plus.setFixedSize(22, 22)
        self.btn_ref_plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_ref_plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ref_plus.clicked.connect(self._inc_ref_level)

        _ref_lay.addWidget(self.btn_ref_minus)
        _ref_lay.addWidget(self.lbl_ref_level, 1)
        _ref_lay.addWidget(self.btn_ref_plus)

        self._ref_container.setVisible(self.vis_mode in (1, 2))

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

        self.btn_ref_minus.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(0, 0, 0, 80);
                border: 1px solid rgb({dim_r}, {dim_g}, {dim_b});
                border-right: none;
                border-top-left-radius: 11px;
                border-bottom-left-radius: 11px;
                color: rgba(220, 220, 220, 180);
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ color: rgb(255, 255, 255); }}
            QPushButton:pressed {{ background-color: rgba({r}, {g}, {b}, 20); }}
        """)
        self.btn_ref_plus.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(0, 0, 0, 80);
                border: 1px solid rgb({dim_r}, {dim_g}, {dim_b});
                border-left: none;
                border-top-right-radius: 11px;
                border-bottom-right-radius: 11px;
                color: rgba(220, 220, 220, 180);
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ color: rgb(255, 255, 255); }}
            QPushButton:pressed {{ background-color: rgba({r}, {g}, {b}, 20); }}
        """)
        self.lbl_ref_level.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 0, 0, 80);
                border-top: 1px solid rgb({dim_r}, {dim_g}, {dim_b});
                border-bottom: 1px solid rgb({dim_r}, {dim_g}, {dim_b});
                color: rgba(220, 220, 220, 180);
                font-size: 10px;
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
        self.vis_mode = (self.vis_mode + 1) % 3
        self._ref_container.setVisible(self.vis_mode in (1, 2))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._floor_pen = None
        W, H = self.width(), self.height()
        cw = int(W * 2 / 3)
        cx = (W - cw) // 2
        if not self._vu_bg.isNull():
            iw, ih = self._vu_bg.width(), self._vu_bg.height()
            scale = min(cw / iw, H / ih)
            sw, sh = int(iw * scale), int(ih * scale)
            self._vu_bg_scaled = self._vu_bg.scaled(
                sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            if not self._vu_frame.isNull():
                self._vu_frame_scaled = self._vu_frame.scaled(
                    sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            ch = sh
        else:
            ch = H

        cy = (H - ch) // 2
        # Only position toggle button when it hasn't been re-parented to _SectionWidget
        if self.btn_toggle_vis.parent() is self:
            self.btn_toggle_vis.move(cx + cw - self.btn_toggle_vis.width() - 8, cy + 8)
        btn_bottom = cy + ch - 4
        self._ref_container.move(cx + (cw - self._ref_container.width()) // 2,
                                 btn_bottom - self._ref_container.height())

    def enterEvent(self, event):
        # Toggle button — only when it hasn't been re-parented to _SectionWidget
        if self.btn_toggle_vis.parent() is self:
            if self.toggle_opacity is None:
                self._init_opacity_effect()
            self.hover_anim.stop()
            self.hover_anim.setEndValue(1.0)
            self.hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.btn_toggle_vis.parent() is self and self.toggle_opacity is not None:
            self.hover_anim.stop()
            self.hover_anim.setEndValue(0.0)
            self.hover_anim.start()
        super().leaveEvent(event)


    def _knob_rect(self):
        """Returns the knob hit area in widget coords (used for mode 2)."""
        if self.vis_mode != 2 or self._douk_scaled_w == 0:
            return None
        scale = self._douk_scaled_w / 1540.0
        ox = (self.width()  - self._douk_scaled_w) // 2
        oy = (self.height() - self._douk_scaled_h) // 2
        kx = ox + 660.0 * scale
        ky = oy + 168.0 * scale
        kr = 43.0 * scale
        return (kx, ky, kr)

    def mousePressEvent(self, event):
        if self.vis_mode == 2:
            hit = self._knob_rect()
            if hit:
                kx, ky, kr = hit
                dx = event.position().x() - kx
                dy = event.position().y() - ky
                if dx*dx + dy*dy <= (kr*1.4)**2:
                    self._knob_drag_y   = event.position().y()
                    self._knob_drag_ref = self._vu_ref_level
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._knob_drag_y is not None:
            dy = self._knob_drag_y - event.position().y()  # drag up = increase
            delta = int(dy / 6)
            new_val = max(-18, min(0, self._knob_drag_ref + delta))
            if new_val != self._vu_ref_level:
                self._vu_ref_level = new_val
                self._update_ref_btn_text()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._knob_drag_y is not None:
            QSettings("Icosahedron", "Visualizer").setValue("vu_ref_level", self._vu_ref_level)
            self._knob_drag_y   = None
            self._knob_drag_ref = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.vis_mode not in (1, 2):
            super().wheelEvent(event)
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        new_val = max(-18, min(0, self._vu_ref_level + step))
        if new_val != self._vu_ref_level:
            self._vu_ref_level = new_val
            self._update_ref_btn_text()
            QSettings("Icosahedron", "Visualizer").setValue("vu_ref_level", self._vu_ref_level)
        event.accept()

    def _update_ref_btn_text(self):
        self.lbl_ref_level.setText(f"{self._vu_ref_level} dBFS")

    def _inc_ref_level(self):
        new_val = min(0, self._vu_ref_level + 1)
        if new_val != self._vu_ref_level:
            self._vu_ref_level = new_val
            self._update_ref_btn_text()
            QSettings("Icosahedron", "Visualizer").setValue("vu_ref_level", self._vu_ref_level)

    def _dec_ref_level(self):
        new_val = max(-18, self._vu_ref_level - 1)
        if new_val != self._vu_ref_level:
            self._vu_ref_level = new_val
            self._update_ref_btn_text()
            QSettings("Icosahedron", "Visualizer").setValue("vu_ref_level", self._vu_ref_level)

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

    def _render_tick(self):
        import time as _t
        now = _t.monotonic()
        last = getattr(self, '_last_vu_t', now)
        if now - last > 0.05:  # no audio data for >50ms → decay toward zero
            dt = now - getattr(self, '_render_last_t', now - 0.016)
            dt = min(dt, 0.1)
            # half-life of 400ms — needle falls naturally over ~1s
            decay = 0.5 ** (dt / 0.4)
            self._raw_vu_rms = getattr(self, '_raw_vu_rms', 0.0) * decay
        self._render_last_t = now
        self.update()

    def update_vu_data(self, true_rms: float):
        if not getattr(self, 'visualizer_enabled', True):
            return
        import time as _t
        self._raw_vu_rms = true_rms
        self._last_vu_t = _t.monotonic()

    def reset(self):
        """Clear bars instantly; let VU needle decay naturally to zero."""
        self.vis_data = [0.0] * self.num_bars
        self._last_vu_t   = 0.0   # triggers _render_tick decay immediately
        self._ndl_base_pos    = getattr(self, '_ndl_base_pos', -math.radians(30.5))
        self._ndl_base_target = -math.radians(30.5)  # park target at minimum
        self._ndl_base_t      = __import__('time').monotonic()
        self.update()
    # -----------------------------
    
    # ── Painting ──────────────────────────────────────────────────────────────

    def _rebuild_floor_pen(self, w, x_offset=0):
        """Build the floor-line pen once per resize instead of every frame."""
        fl_grad = QLinearGradient(x_offset, 0, x_offset + w, 0)
        for pos, (fr, fg, fb) in SPECTRUM:
            fl_grad.setColorAt(pos, QColor(fr, fg, fb, 130))
        self._floor_pen = QPen(QBrush(fl_grad), 1.0)
        self._floor_pen_width = w
        self._floor_pen_x = x_offset

    def set_bg_color(self, rgb_str: str):
        """Accept 'r,g,b' string from theme and repaint background."""
        try:
            r, g, b = (int(x) for x in rgb_str.split(','))
            self._bg_color = QColor(r, g, b)
        except Exception:
            self._bg_color = None
        self.update()

    def paintEvent(self, event):
        if not getattr(self, 'visualizer_enabled', True):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        full_w, full_h = self.width(), self.height()

        if self._bg_color is not None:
            painter.fillRect(self.rect(), self._bg_color)

        cw = int(full_w * 2 / 3)
        cx = (full_w - cw) // 2
        if not self._vu_bg.isNull():
            iw, ih = self._vu_bg.width(), self._vu_bg.height()
            scale = min(cw / iw, full_h / ih)
            ch = int(ih * scale)
        else:
            ch = full_h
        cy = (full_h - ch) // 2

        painter.save()
        painter.translate(cx, cy)

        if self.vis_mode == 0:
            self._paint_bars(painter, cw, ch)
        elif self.vis_mode == 1:
            self._paint_vu_meter(painter, cw, ch)
        else:
            painter.restore()               # undo the (cx,cy) translate
            painter.save()
            self._paint_dual_vu(painter, full_w, full_h)

        painter.restore()
        painter.end()

    def _paint_bars(self, painter, W, H):
        _bg = self._vu_bg_scaled if not self._vu_bg_scaled.isNull() else self._vu_bg
        if not _bg.isNull():
            dw, dh = _bg.width(), _bg.height()
            dx, dy = (W - dw) // 2, 0
        else:
            dx, dy, dw, dh = 0, 0, W, H

        BASE_Y = float(dy + dh)
        painter.setPen(Qt.PenStyle.NoPen)

        slot_w = dw / self.num_bars
        bar_w  = slot_w * 0.72
        gap    = (slot_w - bar_w) / 2
        radius = min(bar_w * 0.5, 4.5)

        for i in range(self.num_bars):
            val   = self.vis_data[i]
            bar_h = val * dh * HEIGHT_SCALE
            if bar_h < 1.0:
                continue
            x     = dx + i * slot_w + gap
            y_top = BASE_Y - bar_h
            painter.setBrush(self.bar_brushes[i])
            painter.drawRoundedRect(QRectF(x, y_top, bar_w, bar_h), radius, radius)


    def _paint_vu_meter(self, painter, W, H):

        # ── Layer 1: full background — use pre-scaled copy for crisp rendering ─
        _bg = self._vu_bg_scaled if not self._vu_bg_scaled.isNull() else self._vu_bg
        if not _bg.isNull():
            dw, dh = _bg.width(), _bg.height()
            dx, dy = (W - dw) // 2, 0
            radius = 8.0
            path = QPainterPath()
            path.addRoundedRect(QRectF(dx, dy, dw, dh), radius, radius)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(dx, dy, _bg)
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
            (-20, 0.000), (-10, 0.140), (-7, 0.248), (-5, 0.341),
            (-3,  0.458), (-2,  0.529), (-1, 0.612), ( 0, 0.709),
            ( 1,  0.799), ( 2,  0.898), ( 3, 0.990),
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

        import time as _time
        now = _time.monotonic()

        # ── 1. Electrical smoothing — runs at audio callback rate in update_vu_data.
        #    Here we just read the smoothed value.
        rms_instant = getattr(self, '_raw_vu_rms', 0.0)
        ALPHA_K = -math.log(0.948) * 60.0   # decay constant ≈ 3.22 /s (tuned at 60 fps)
        dt_elec = min(now - getattr(self, '_elec_last_t', now - 0.016), 0.1)
        self._elec_last_t = now
        alpha = math.exp(-ALPHA_K * dt_elec)
        if not hasattr(self, '_vu_rms'):
            self._vu_rms = 0.0
        self._vu_rms = alpha * self._vu_rms + (1.0 - alpha) * rms_instant

        db_level = 20.0 * math.log10(max(self._vu_rms, 1e-9)) + (-self._vu_ref_level)
        target_a  = db2a(max(-20.0, min(3.0, db_level)))

        # ── 2. Analytical needle position — continuous exponential decay.
        #    K derived from NEEDLE_SPEED_60=0.25 at 60 fps:
        #      per-frame decay = (1-0.25) = 0.75  →  K = -ln(0.75)*60 ≈ 17.3 /s
        #    pos(t) = target + (base_pos - base_target) * e^(-K * elapsed)
        #    Re-anchor base whenever target changes to preserve continuity.
        NEEDLE_K = -math.log(1.0 - 0.25) * 60.0   # ≈ 17.3 /s

        if not hasattr(self, '_ndl_base_pos'):
            self._ndl_base_pos    = target_a
            self._ndl_base_target = target_a
            self._ndl_base_t      = now

        # Re-anchor when target moves (keeps previous velocity)
        if target_a != getattr(self, '_ndl_base_target', target_a):
            elapsed = now - self._ndl_base_t
            cur = self._ndl_base_target + (self._ndl_base_pos - self._ndl_base_target) * math.exp(-NEEDLE_K * elapsed)
            self._ndl_base_pos    = cur
            self._ndl_base_target = target_a
            self._ndl_base_t      = now

        elapsed = now - self._ndl_base_t
        needle_a = self._ndl_base_target + (self._ndl_base_pos - self._ndl_base_target) * math.exp(-NEEDLE_K * elapsed)

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
        painter.setPen(QPen(QColor(0x2e, 0x1e, 0x1a), 1.5,
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
        _frame = self._vu_frame_scaled if not self._vu_frame_scaled.isNull() else self._vu_frame
        if not _frame.isNull():
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(dx, dy, _frame)
            painter.restore()

    # ── Dual Douk-style VU meter (image-based) ────────────────────────────────

    def _paint_dual_vu(self, painter, W, H):
        if self._douk_frame.isNull():
            return

        iw, ih = self._douk_frame.width(), self._douk_frame.height()
        scale  = min(W / iw, H / ih) * 0.70
        sw, sh = int(iw * scale), int(ih * scale)

        # Re-scale all three layers only when size changes
        if sw != self._douk_scaled_w or sh != self._douk_scaled_h:
            self._douk_scaled_frame = self._douk_frame.scaled(
                sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._douk_scaled_left  = self._douk_left.scaled(
                sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._douk_scaled_right = self._douk_right.scaled(
                sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._douk_scaled_w = sw
            self._douk_scaled_h = sh

        ox = (W - sw) // 2
        oy = (H - sh) // 2

        # Composite: frame → left face → right face
        painter.drawPixmap(ox, oy, self._douk_scaled_frame)
        painter.drawPixmap(ox, oy, self._douk_scaled_left)
        painter.drawPixmap(ox, oy, self._douk_scaled_right)

        # ── Rotatable GAIN knob ───────────────────────────────────────────────
        # Knob center in 1540×516 image: (719, 169), inner radius 43px
        # Rotation: -135° at -18 dBFS, 0° at -9 dBFS, +135° at 0 dBFS
        if not self._douk_knob.isNull():
            knob_angle = (self._vu_ref_level + 9) * 15.0
            knob_r     = 43.0 * scale
            knob_size  = max(2, int(knob_r * 2))
            scaled_knob = self._douk_knob.scaled(
                knob_size, knob_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            kx = ox + 660.0 * scale
            ky = oy + 168.0 * scale
            painter.save()
            painter.translate(kx, ky)
            painter.rotate(knob_angle)
            painter.drawPixmap(int(-knob_r), int(-knob_r), scaled_knob)
            painter.restore()

        # ── Needle physics ────────────────────────────────────────────────────
        import time as _time
        now  = _time.monotonic()
        rms  = getattr(self, '_raw_vu_rms', 0.0)

        ALPHA_K = -math.log(0.9) * 60.0
        dt_e    = min(now - getattr(self, '_elec2_t', now - 0.016), 0.1)
        self._elec2_t = now
        alpha = math.exp(-ALPHA_K * dt_e)
        if not hasattr(self, '_vu_rms2'):
            self._vu_rms2 = 0.0
        self._vu_rms2 = alpha * self._vu_rms2 + (1.0 - alpha) * rms

        db = 20.0 * math.log10(max(self._vu_rms2, 1e-9)) + (-self._vu_ref_level)

        # Calibrated from image pixel analysis:
        # pivot_left=(311,340), R=273px, left_angle=-45.2°, right_angle=+36.1°
        # t-values derived from acoustic VU law + measured tick spacing
        _BASE = [(-60, 0.000), (-50, 0.075), (-40, 0.170), (-30, 0.290), (-20, 0.435), (-10, 0.595)]
        # Per-needle 0, +4, +6 — tune independently
        _LEFT_MARKS  = _BASE + [(0, 0.755), (4, 0.865), (6, 0.950)]
        _RIGHT_MARKS = _BASE + [(0, 0.760), (4, 0.870), (6, 0.955)]

        LEFT_A = math.radians(-41.4)
        RIGHT_A = math.radians(45.0)
        SPAN_A  = RIGHT_A - LEFT_A

        def db2a(d, marks):
            d = max(-60.0, min(6.0, d))
            for i in range(len(marks) - 1):
                d0, t0 = marks[i]; d1, t1 = marks[i + 1]
                if d0 <= d <= d1:
                    f = (d - d0) / (d1 - d0)
                    return LEFT_A + (t0 + f * (t1 - t0)) * SPAN_A
            return LEFT_A + marks[-1][1] * SPAN_A

        # Shared physics driven by left marks; right needle gets its own target angle
        target_left  = db2a(db, _LEFT_MARKS)
        target_right = db2a(db, _RIGHT_MARKS)
        NEEDLE_K_ATTACK  = -math.log(1.0 - 0.45) * 60.0   # rise speed
        NEEDLE_K_RELEASE = -math.log(1.0 - 0.45) * 60.0   # fallback speed — tune this

        if not hasattr(self, '_ndl2_base_pos'):
            self._ndl2_base_pos    = target_left
            self._ndl2_base_target = target_left
            self._ndl2_base_t      = now
        if target_left != self._ndl2_base_target:
            _k  = NEEDLE_K_ATTACK if target_left > self._ndl2_base_target else NEEDLE_K_RELEASE
            e   = now - self._ndl2_base_t
            cur = self._ndl2_base_target + (self._ndl2_base_pos - self._ndl2_base_target) * math.exp(-_k * e)
            self._ndl2_base_pos    = cur
            self._ndl2_base_target = target_left
            self._ndl2_base_t      = now
        _k        = NEEDLE_K_ATTACK if self._ndl2_base_target >= self._ndl2_base_pos else NEEDLE_K_RELEASE
        e         = now - self._ndl2_base_t
        needle_left  = self._ndl2_base_target + (self._ndl2_base_pos - self._ndl2_base_target) * math.exp(-_k * e)
        needle_right = needle_left + (target_right - target_left)

        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPen

        s   = sw / 1540.0
        R   = 230.0 * s
        cpy = oy + 380.0 * s

        for cpx_img, ndl in ((309.0, needle_left), (1209.0, needle_right)):
            a     = min(ndl, db2a(6.0, (_LEFT_MARKS if cpx_img == 309.0 else _RIGHT_MARKS)))
            cpx   = ox + cpx_img * s
            tip_x = cpx + R * math.sin(a)
            tip_y = cpy - R * math.cos(a)
            # Shadow
            painter.setPen(QPen(QColor(0, 0, 0, 60), max(1.0, s * 2.5),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(cpx + s, cpy + s), QPointF(tip_x + s, tip_y + s))
            # Needle
            painter.setPen(QPen(QColor(0x2e, 0x1e, 0x1a), max(1.0, s * 2.0),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(cpx, cpy), QPointF(tip_x, tip_y))

            # Pivot dot
            painter.setBrush(QColor(0x2e, 0x1e, 0x1a))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cpx, cpy), 5.0 * s, 5.0 * s)

