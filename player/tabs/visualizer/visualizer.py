import math

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt6.QtCore import Qt, QPointF, QSettings, QTimer
from player import resource_path


class AudioVisualizer(QWidget):
    def __init__(self, audio_engine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine

        self.visualizer_enabled = True # The Master Switch
        self.vis_mode = 2              # only the dual VU meter exists; kept for settings compat
        self.master_color = QColor("#1db954") # Default fallback color

        # Dual Douk-style VU meter assets
        self._douk_frame   = QPixmap(resource_path("img/new_vu_frame.png"))
        self._douk_left    = QPixmap(resource_path("img/new_vu_left.png"))
        self._douk_right   = QPixmap(resource_path("img/new_vu_right.png"))
        self._douk_knob    = QPixmap(resource_path("img/vu_gain_knob_inner.png"))
        self._douk_sens_knob = QPixmap(resource_path("img/vu_sens_knob_inner.png"))
        self._douk_scaled_frame = QPixmap()
        self._douk_scaled_left  = QPixmap()
        self._douk_scaled_right = QPixmap()
        self._douk_scaled_w = 0
        self._douk_scaled_h = 0
        self._knob_drag_y   = None   # mouse y at drag start
        self._knob_drag_ref = None   # ref level at drag start
        self._sens_drag_y   = None   # mouse y at drag start
        self._sens_drag_val = None   # sens value at drag start

        _s = QSettings("Icosahedron", "Visualizer")
        self._vu_ref_level = int(_s.value("vu_ref_level", -10))
        self._vu_sens_step = int(_s.value("vu_sens_step", 9))   # 0..18 detents, like GAIN

        self._bg_color: QColor | None = None

        self.setMinimumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self.audio_engine.vuDataReady.connect(self.update_vu_data)

        # Vsync-locked render timer — drives all repaints at the screen refresh rate
        screen = QApplication.primaryScreen()
        hz = screen.refreshRate() if screen else 60.0
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(max(1, round(1000.0 / hz)))
        self._render_timer.timeout.connect(self._render_tick)
        self._render_timer.start()

    # ── Color bridge ─────────────────────────────────────────────────────────

    @property
    def bar_color(self):
        """Kept for backward-compat with main.py which sets visualizer.bar_color."""
        return self.master_color

    @bar_color.setter
    def bar_color(self, color: QColor):
        self.master_color = QColor(color.name())

    def set_bg_color(self, rgb_str: str):
        """Accept 'r,g,b' string from theme and repaint background."""
        try:
            r, g, b = (int(x) for x in rgb_str.split(','))
            self._bg_color = QColor(r, g, b)
        except Exception:
            self._bg_color = None
        self.update()

    # ── GAIN / SENS knob interaction ─────────────────────────────────────────

    def _knob_rect(self):
        """Returns the GAIN knob hit area in widget coords."""
        if self._douk_scaled_w == 0:
            return None
        scale = self._douk_scaled_w / 1540.0
        ox = (self.width()  - self._douk_scaled_w) // 2
        oy = (self.height() - self._douk_scaled_h) // 2
        kx = ox + 660.0 * scale
        ky = oy + 168.0 * scale
        kr = 43.0 * scale
        return (kx, ky, kr)

    def _sens_knob_rect(self):
        """Returns the SENS knob hit area in widget coords."""
        if self._douk_scaled_w == 0:
            return None
        scale = self._douk_scaled_w / 1540.0
        ox = (self.width()  - self._douk_scaled_w) // 2
        oy = (self.height() - self._douk_scaled_h) // 2
        kx = ox + 873.0 * scale
        ky = oy + 168.0 * scale
        kr = 43.0 * scale
        return (kx, ky, kr)

    def mousePressEvent(self, event):
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
        hit = self._sens_knob_rect()
        if hit:
            kx, ky, kr = hit
            dx = event.position().x() - kx
            dy = event.position().y() - ky
            if dx*dx + dy*dy <= (kr*1.4)**2:
                self._sens_drag_y   = event.position().y()
                self._sens_drag_val = self._vu_sens_step
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._knob_drag_y is not None:
            dy = self._knob_drag_y - event.position().y()  # drag up = increase
            delta = int(dy / 6)
            new_val = max(-18, min(0, self._knob_drag_ref + delta))
            self._vu_ref_level = new_val
            event.accept()
            return
        if self._sens_drag_y is not None:
            dy = self._sens_drag_y - event.position().y()  # drag up = increase
            delta = int(dy / 6)
            new_val = max(0, min(18, self._sens_drag_val + delta))
            self._vu_sens_step = new_val
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
        if self._sens_drag_y is not None:
            QSettings("Icosahedron", "Visualizer").setValue("vu_sens_step", self._vu_sens_step)
            self._sens_drag_y   = None
            self._sens_drag_val = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        hit = self._sens_knob_rect()
        if hit:
            kx, ky, kr = hit
            dx = event.position().x() - kx
            dy = event.position().y() - ky
            if dx*dx + dy*dy <= (kr*1.4)**2:
                step = 1 if event.angleDelta().y() > 0 else -1
                new_val = max(0, min(18, self._vu_sens_step + step))
                if new_val != self._vu_sens_step:
                    self._vu_sens_step = new_val
                    QSettings("Icosahedron", "Visualizer").setValue("vu_sens_step", self._vu_sens_step)
                event.accept()
                return

        hit = self._knob_rect()
        if hit:
            kx, ky, kr = hit
            dx = event.position().x() - kx
            dy = event.position().y() - ky
            if dx*dx + dy*dy <= (kr*1.4)**2:
                delta = event.angleDelta().x() or event.angleDelta().y()
                step = 1 if delta > 0 else -1
                new_val = max(-18, min(0, self._vu_ref_level + step))
                if new_val != self._vu_ref_level:
                    self._vu_ref_level = new_val
                    QSettings("Icosahedron", "Visualizer").setValue("vu_ref_level", self._vu_ref_level)
                event.accept()
                return

        super().wheelEvent(event)

    # ── Data processing ───────────────────────────────────────────────────────

    def _needle_k(self):
        """Rate constant for needle rise/fall — also used to decay the raw RMS
        toward zero on silence, so the pause/stop fallback moves at exactly
        the same speed as the needle's normal tracking, scaled by SENS."""
        STEP_MIN, STEP_MAX = 0.12, 0.70
        step = STEP_MIN + (self._vu_sens_step / 18.0) * (STEP_MAX - STEP_MIN)
        return -math.log(1.0 - step) * 60.0 * 2.0

    def _render_tick(self):
        import time as _t
        now = _t.monotonic()
        last = getattr(self, '_last_vu_t', now)
        if now - last > 0.05:  # no audio data for >50ms → decay toward zero
            dt = now - getattr(self, '_render_last_t', now - 0.016)
            dt = min(dt, 0.1)
            decay = math.exp(-self._needle_k() * dt)
            self._raw_vu_rms_l = getattr(self, '_raw_vu_rms_l', 0.0) * decay
            self._raw_vu_rms_r = getattr(self, '_raw_vu_rms_r', 0.0) * decay
        self._render_last_t = now
        self.update()

    def update_vu_data(self, true_rms_l: float, true_rms_r: float):
        if not getattr(self, 'visualizer_enabled', True):
            return
        import time as _t
        self._raw_vu_rms_l = true_rms_l
        self._raw_vu_rms_r = true_rms_r
        self._last_vu_t = _t.monotonic()

    def reset(self):
        """Let both VU needles decay naturally to zero instead of snapping."""
        self._last_vu_t = 0.0   # triggers _render_tick decay immediately
        self.update()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if not getattr(self, 'visualizer_enabled', True):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._bg_color is not None:
            painter.fillRect(self.rect(), self._bg_color)

        self._paint_dual_vu(painter, self.width(), self.height())
        painter.end()

    # ── Dual Douk-style VU meter (image-based) ────────────────────────────────

    def _paint_dual_vu(self, painter, W, H):
        if self._douk_frame.isNull():
            return

        iw, ih = self._douk_frame.width(), self._douk_frame.height()
        scale  = min(W / iw, H / ih) * 0.84
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

        # Composite: left face → right face (frame is drawn last, on top of the needles)
        painter.drawPixmap(ox, oy, self._douk_scaled_left)
        painter.drawPixmap(ox, oy, self._douk_scaled_right)

        # ── Needle physics ────────────────────────────────────────────────────
        import time as _time
        now    = _time.monotonic()
        rms_l  = getattr(self, '_raw_vu_rms_l', 0.0)
        rms_r  = getattr(self, '_raw_vu_rms_r', 0.0)

        ALPHA_K = -math.log(0.9) * 60.0
        dt_e    = min(now - getattr(self, '_elec2_t', now - 0.016), 0.1)
        self._elec2_t = now
        alpha = math.exp(-ALPHA_K * dt_e)
        if not hasattr(self, '_vu_rms2_l'):
            self._vu_rms2_l = 0.0
            self._vu_rms2_r = 0.0
        self._vu_rms2_l = alpha * self._vu_rms2_l + (1.0 - alpha) * rms_l
        self._vu_rms2_r = alpha * self._vu_rms2_r + (1.0 - alpha) * rms_r

        # Mirror the knob's relative position around its center (-9) before
        # applying it to the calibration math — knob visual/drag/scroll and
        # its stored value are untouched, only this calculation is flipped.
        _ref_mirrored = -(self._vu_ref_level + 9) - 9
        db_l = 20.0 * math.log10(max(self._vu_rms2_l, 1e-9)) + (-_ref_mirrored)
        db_r = 20.0 * math.log10(max(self._vu_rms2_r, 1e-9)) + (-_ref_mirrored)

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

        # Independent physics per channel
        target_left  = db2a(db_l, _LEFT_MARKS)
        target_right = db2a(db_r, _RIGHT_MARKS)
        # SENS knob: 0.0 = slow/sluggish needle, 1.0 = fast/snappy needle
        NEEDLE_K = self._needle_k()   # rise/fall speed — same both ways

        def _step_needle(prefix, target):
            base_pos    = getattr(self, prefix + '_pos', target)
            base_target = getattr(self, prefix + '_target', target)
            base_t      = getattr(self, prefix + '_t', now)
            if target != base_target:
                e   = now - base_t
                base_pos    = base_target + (base_pos - base_target) * math.exp(-NEEDLE_K * e)
                base_target = target
                base_t      = now
            setattr(self, prefix + '_pos', base_pos)
            setattr(self, prefix + '_target', base_target)
            setattr(self, prefix + '_t', base_t)
            e  = now - base_t
            return base_target + (base_pos - base_target) * math.exp(-NEEDLE_K * e)

        needle_left  = _step_needle('_ndl2L', target_left)
        needle_right = _step_needle('_ndl2R', target_right)

        s   = sw / 1540.0
        R   = 230.0 * s
        cpy = oy + 380.0 * s

        # The warped diffuser plastic occupies a fixed-height band right above
        # the pivot, in screen space — not a fraction of the needle's length.
        # So the distorted segment's length naturally varies with swing angle:
        # shortest when the needle is near vertical (steepest exit out of the
        # band), longest when it leans toward horizontal (grazes through it).
        BAND_H = 63.0 * s

        for cpx_img, ndl in ((309.0, needle_left), (1209.0, needle_right)):
            a     = min(ndl, db2a(6.0, (_LEFT_MARKS if cpx_img == 309.0 else _RIGHT_MARKS)))
            cpx   = ox + cpx_img * s
            tip_x = cpx + R * math.sin(a)
            tip_y = cpy - R * math.cos(a)
            r_band  = min(R, BAND_H / max(math.cos(a), 0.05))
            split_x = cpx + r_band * math.sin(a)
            split_y = cpy - r_band * math.cos(a)

            # Wavy refraction through the ribbed diffuser plastic — the needle's
            # path itself bends in a small zigzag while inside the band, fading
            # to zero at both the pivot and the split point (so it joins the
            # straight, undistorted upper segment with no seam).
            WAVE_AMP    = 1.2 * s
            WAVE_LEN    = 16.0 * s
            START_FRAC  = 0.40   # closest part to the pivot stays undistorted
            r_start = r_band * START_FRAC
            perp_x, perp_y = math.cos(a), math.sin(a)
            N_STEPS = 16

            NEEDLE_DARK = (0x2e, 0x1e, 0x1a)
            THIN_MULT   = 0.55   # how much thinner the needle gets, mid-band

            THIN_LEN = r_band * 1.3   # how far up from the pivot the thinning extends

            points  = [(cpx, cpy)]
            factors = [0.0]   # 0 = normal width, 1 = thinnest (mid-band)
            for i in range(1, N_STEPS + 1):
                ri = r_band * i / N_STEPS
                bx = cpx + ri * math.sin(a)
                by = cpy - ri * math.cos(a)
                # Thinning starts right at the pivot, fading out over THIN_LEN
                factor = math.sin(math.pi * ri / THIN_LEN) if ri < THIN_LEN else 0.0
                if ri <= r_start or r_band <= r_start:
                    offset = 0.0
                else:
                    t = (ri - r_start) / (r_band - r_start)
                    offset = WAVE_AMP * math.sin(math.pi * t) * math.sin(2.0 * math.pi * ri / WAVE_LEN)
                points.append((bx + offset * perp_x, by + offset * perp_y))
                factors.append(factor)
            points.append((split_x, split_y))
            factors.append(0.0)

            # Shadow — uniform, no width taper needed
            painter.save()
            painter.translate(s, s)
            painter.setPen(QPen(QColor(0, 0, 0, 60), max(1.0, s * 2.5),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(len(points) - 1):
                painter.drawLine(QPointF(*points[i]), QPointF(*points[i + 1]))
            painter.drawLine(QPointF(split_x, split_y), QPointF(tip_x, tip_y))
            painter.restore()

            # Needle — wavy through the band, same dark color throughout but
            # tapering thinner, peaking in the middle of the diffuser band
            base_w = max(1.0, s * 2.0)
            pen = QPen(QColor(*NEEDLE_DARK), base_w,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            for i in range(len(points) - 1):
                f = (factors[i] + factors[i + 1]) * 0.5
                pen.setWidthF(base_w * (1.0 - THIN_MULT * f))
                painter.setPen(pen)
                painter.drawLine(QPointF(*points[i]), QPointF(*points[i + 1]))
            painter.setPen(QPen(QColor(*NEEDLE_DARK), base_w,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(split_x, split_y), QPointF(tip_x, tip_y))

            # Pivot dot
            painter.setBrush(QColor(0x2e, 0x1e, 0x1a))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cpx, cpy), 5.0 * s, 5.0 * s)

        # ── Frame overlay — drawn on top so it covers the needles outside the glass
        painter.drawPixmap(ox, oy, self._douk_scaled_frame)

        # ── Rotatable GAIN knob ───────────────────────────────────────────────
        # Knob center in 1540×516 image: (660, 168), inner radius 43px
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

        # ── Rotatable SENS knob — controls needle ballistic response speed ────
        if not self._douk_sens_knob.isNull():
            sens_angle = (self._vu_sens_step - 9) * 15.0
            sens_r     = 43.0 * scale
            sens_size  = max(2, int(sens_r * 2))
            scaled_sens_knob = self._douk_sens_knob.scaled(
                sens_size, sens_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            skx = ox + 873.0 * scale
            sky = oy + 168.0 * scale
            painter.save()
            painter.translate(skx, sky)
            painter.rotate(sens_angle)
            painter.drawPixmap(int(-sens_r), int(-sens_r), scaled_sens_knob)
            painter.restore()
