import math
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient, QBrush
from PyQt6.QtCore import Qt, QRectF

def render_waveform_bars(painter: QPainter, width: int, height: int, samples: list, total_samples: int, position_ms: int, duration_ms: int, master_color: QColor):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    progress = position_ms / max(1, duration_ms)
    playhead_x = progress * width

    bar_width = 3      
    bar_gap = 2        
    total_bar = bar_width + bar_gap
    num_bars = int(width / total_bar)

    if num_bars <= 0 or total_samples <= 0:
        return

    samples_per_bar = total_samples / num_bars
    center_y = height / 2.0

    # 1. Build the skeleton (path) of all the bars at once
    path = QPainterPath()
    
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
        x = i * total_bar + (bar_width / 2.0)
        
        # Trace the line geometry
        path.moveTo(x, center_y - bar_height / 2)
        path.lineTo(x, center_y + bar_height / 2)

    # 2. Draw the ENTIRE background waveform (Unplayed Gray)
    unplayed_pen = QPen(QColor(80, 80, 80, 150))
    unplayed_pen.setWidth(bar_width)
    unplayed_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(unplayed_pen)
    painter.drawPath(path)

    # 3. Create the smooth vertical gradient for the played portion
    gradient = QLinearGradient(0, 0, 0, height)
    # Top is bright and vibrant
    gradient.setColorAt(0.0, master_color.lighter(130))
    # Center is the true master color
    gradient.setColorAt(0.5, master_color)
    # Bottom fades into a darker, richer hue
    gradient.setColorAt(1.0, master_color.darker(150))
    
    played_pen = QPen(QBrush(gradient), bar_width)
    played_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(played_pen)

    # 4.
    # This draws the gradient ONLY up to the current millisecond, allowing it 
    # to smoothly slice through the middle of the bars instead of snapping.
    painter.save()
    painter.setClipRect(QRectF(0, 0, playhead_x, height))
    painter.drawPath(path)
    painter.restore()