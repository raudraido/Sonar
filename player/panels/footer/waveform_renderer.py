import math
import numpy as np
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient, QBrush
from PyQt6.QtCore import Qt, QRectF

BAR_WIDTH = 3
BAR_GAP = 2

def build_waveform_path(width: int, height: int, samples: list, total_samples: int) -> QPainterPath:
    path = QPainterPath()

    total_bar = BAR_WIDTH + BAR_GAP
    num_bars = int(width / total_bar)

    if num_bars <= 0 or total_samples <= 0:
        return path

    samples_per_bar = total_samples / num_bars
    center_y = height / 2.0

    for i in range(num_bars):
        start_idx = int(i * samples_per_bar)
        end_idx = min(int((i + 1) * samples_per_bar), total_samples)

        chunk = samples[start_idx:end_idx]

        if chunk:
            avg_val = sum(chunk) / len(chunk)
            expanded = math.pow(avg_val, 1.5)
            val = min(1.0, expanded * 1.8)
        else:
            val = 0.0

        bar_height = max(4.0, val * (height * 0.85))
        x = i * total_bar + (BAR_WIDTH / 2.0)

        path.moveTo(x, center_y - bar_height / 2)
        path.lineTo(x, center_y + bar_height / 2)

    return path

def draw_waveform_bars(painter: QPainter, path: QPainterPath, width: int, height: int, position_ms: int, duration_ms: int, master_color: QColor):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if path.isEmpty():
        return

    progress = position_ms / max(1, duration_ms)
    playhead_x = progress * width

    # 1. Draw the ENTIRE background waveform (Unplayed Gray)
    unplayed_pen = QPen(QColor(80, 80, 80, 150))
    unplayed_pen.setWidth(BAR_WIDTH)
    unplayed_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(unplayed_pen)
    painter.drawPath(path)

    # 2. Create the smooth vertical gradient for the played portion
    gradient = QLinearGradient(0, 0, 0, height)
    # Top is bright and vibrant
    gradient.setColorAt(0.0, master_color.lighter(130))
    # Center is the true master color
    gradient.setColorAt(0.5, master_color)
    # Bottom fades into a darker, richer hue
    gradient.setColorAt(1.0, master_color.darker(150))

    played_pen = QPen(QBrush(gradient), BAR_WIDTH)
    played_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(played_pen)

    # 3.
    # This draws the gradient ONLY up to the current millisecond, allowing it
    # to smoothly slice through the middle of the bars instead of snapping.
    painter.save()
    painter.setClipRect(QRectF(0, 0, playhead_x, height))
    painter.drawPath(path)
    painter.restore()

def _hsv_to_rgb_np(h, s, v):
    # h: 0-359, s/v: 0-255 (int arrays), matches QColor.fromHsv ranges
    h = h.astype(np.float64)
    s = s.astype(np.float64) / 255.0
    v = v.astype(np.float64) / 255.0

    hi = h / 60.0
    i = np.floor(hi).astype(np.int64) % 6
    f = hi - np.floor(hi)

    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)

    cases = [i == 0, i == 1, i == 2, i == 3, i == 4, i == 5]
    r = np.select(cases, [v, q, p, p, t, v])
    g = np.select(cases, [t, v, v, q, p, p])
    b = np.select(cases, [p, p, t, v, v, q])

    to_u8 = lambda c: np.clip(c * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return to_u8(r), to_u8(g), to_u8(b)

def render_scratch_waveform(buf, width: int, height: int, samples_np, total_samples: int,
                             current_index: float, pixels_per_sample: float, fade_lookup_np,
                             base_hue: int, max_bar_height: float, user_picked: bool, master_color: QColor):
    """Fills `buf` (a (height, width, 4) uint8 RGBA array) with the scratch-mode waveform.

    Vectorized with numpy so a full-width redraw is cheap enough for 144Hz —
    the previous per-pixel QPainter.drawLine() loop couldn't keep up.
    """
    buf[:] = 0

    if total_samples < 2:
        return

    center_y = height / 2.0
    center_x = width / 2.0

    x = np.arange(width, dtype=np.float64)
    exact_index = current_index + (x - center_x) / pixels_per_sample

    valid = (exact_index >= 0) & (exact_index < total_samples - 1)

    idx1 = np.clip(exact_index.astype(np.int64), 0, total_samples - 2)
    frac = exact_index - idx1

    raw_val = samples_np[idx1] + (samples_np[idx1 + 1] - samples_np[idx1]) * frac
    val = raw_val * raw_val * 0.5 + raw_val * 0.5
    bar_h = val * max_bar_height

    left_mask = x < center_x
    brightness = np.where(left_mask, 120.0 + 135.0 * fade_lookup_np, 70.0 + 130.0 * fade_lookup_np)
    alpha = np.where(left_mask, 255.0 * fade_lookup_np, 180.0 * fade_lookup_np)

    hue_shift = (20.0 * raw_val).astype(np.int64)
    final_hue = (base_hue + hue_shift) % 360

    sat_base = master_color.saturation() if user_picked else 255
    sat_min = 0.0 if user_picked else 0.3
    saturation = sat_base * np.maximum(sat_min, 1.0 - raw_val * 0.6)

    r, g, b = _hsv_to_rgb_np(
        np.clip(final_hue, 0, 359),
        np.clip(saturation, 0, 255).astype(np.int64),
        np.clip(brightness, 0, 255).astype(np.int64),
    )
    a = np.clip(alpha, 0, 255).astype(np.uint8)

    ya = (center_y - bar_h).astype(np.int64)
    yb = (center_y + bar_h).astype(np.int64)
    top = np.clip(np.minimum(ya, yb), 0, height - 1)
    bottom = np.clip(np.maximum(ya, yb), 0, height - 1)

    rows = np.arange(height).reshape(-1, 1)
    mask_2d = valid & (rows >= top) & (rows <= bottom)

    buf[..., 0][mask_2d] = np.broadcast_to(r, (height, width))[mask_2d]
    buf[..., 1][mask_2d] = np.broadcast_to(g, (height, width))[mask_2d]
    buf[..., 2][mask_2d] = np.broadcast_to(b, (height, width))[mask_2d]
    buf[..., 3][mask_2d] = np.broadcast_to(a, (height, width))[mask_2d]
