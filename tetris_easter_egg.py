"""tetris_easter_egg.py — Hidden Tetris easter egg, triggered by 7 Home-tab clicks."""
import random
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QColor, QFont

COLS, ROWS = 10, 26
HUD_H = 36   # fixed px for the bottom score/button bar

SHAPES = {
    'I': [[1,1,1,1]],
    'O': [[1,1],[1,1]],
    'T': [[0,1,0],[1,1,1]],
    'S': [[0,1,1],[1,1,0]],
    'Z': [[1,1,0],[0,1,1]],
    'J': [[1,0,0],[1,1,1]],
    'L': [[0,0,1],[1,1,1]],
}
COLORS = {
    'I': '#00f0f0', 'O': '#f0f000', 'T': '#a000f0',
    'S': '#00f000', 'Z': '#f00000', 'J': '#0000f0', 'L': '#f0a000',
}


def _rotate(shape):
    return [list(row) for row in zip(*shape[::-1])]


class _Piece:
    def __init__(self):
        self.name = random.choice(list(SHAPES))
        self.shape = [row[:] for row in SHAPES[self.name]]
        self.color = COLORS[self.name]
        self.x = COLS // 2 - len(self.shape[0]) // 2
        self.y = 0

    def cells(self, dx=0, dy=0, shape=None):
        s = shape or self.shape
        return [(self.x + c + dx, self.y + r + dy)
                for r, row in enumerate(s) for c, v in enumerate(row) if v]


class _Canvas(QWidget):
    """The game board — fills all available space above the HUD."""
    def __init__(self, game):
        super().__init__(game)
        self._g = game
        self._cell = 16
        self._bg    = QColor('#0d0d0d')
        self._grid  = QColor('#1a1a1a')

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Uniform cell — maintain 10:ROWS aspect ratio, centre in canvas
        cell = max(4, min(self.width() // COLS, self.height() // ROWS))
        self._cell = cell
        self._cw   = cell
        self._ch   = cell
        self.update()

    def paintEvent(self, _):
        g = self._g
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cell = self._cell
        cw = ch = cell
        bw, bh = COLS * cell, ROWS * cell
        ox = (W - bw) // 2   # centre horizontally
        oy = (H - bh) // 2   # centre vertically

        p.fillRect(0, 0, W, H, self._bg)
        p.setPen(self._grid)
        for c in range(COLS + 1):
            p.drawLine(ox + c * cw, oy, ox + c * cw, oy + bh)
        for r in range(ROWS + 1):
            p.drawLine(ox, oy + r * ch, ox + bw, oy + r * ch)

        # Locked cells
        for r in range(ROWS):
            for c in range(COLS):
                if g._board[r][c]:
                    self._cell_rect(p, ox, oy, c, r, g._board[r][c])

        # Ghost + active piece
        if g._piece and not g._game_over:
            dy = 0
            while g._valid(g._piece, dy=dy + 1): dy += 1
            if dy > 0:
                for gx, gy in g._piece.cells(dy=dy):
                    self._cell_rect(p, ox, oy, gx, gy, g._piece.color, ghost=True)
            for cx, cy in g._piece.cells():
                self._cell_rect(p, ox, oy, cx, cy, g._piece.color)

        # Pause / Game Over overlay
        if g._paused or g._game_over:
            p.fillRect(ox, oy, bw, bh, QColor(0, 0, 0, 160))
            f = QFont(); f.setPixelSize(max(14, ch)); f.setBold(True); p.setFont(f)
            p.setPen(QColor('#ffffff'))
            msg = 'PAUSED' if g._paused else 'GAME OVER'
            p.drawText(QRect(ox, oy + bh // 2 - ch - 4, bw, ch + 8),
                       Qt.AlignmentFlag.AlignCenter, msg)
            if g._game_over:
                f2 = QFont(); f2.setPixelSize(max(9, ch // 2)); p.setFont(f2)
                p.setPen(QColor('#aaa'))
                p.drawText(QRect(ox, oy + bh // 2 + 8, bw, ch),
                           Qt.AlignmentFlag.AlignCenter, 'Press Restart or Enter')
        p.end()

    def _cell_rect(self, p, ox, oy, cx, cy, color, ghost=False):
        if cy < 0: return
        cell = self._cell
        x = ox + cx * cell + 1
        y = oy + cy * cell + 1
        w = h = cell - 2
        c = QColor(color)
        if ghost:
            c.setAlpha(35)
            p.fillRect(x, y, w, h, c)
        else:
            p.fillRect(x, y, w, h, c)
            p.fillRect(x, y, w, 3, QColor(255, 255, 255, 60))
            p.fillRect(x, y + h - 3, w, 3, QColor(0, 0, 0, 80))


class TetrisWidget(QWidget):
    closed = pyqtSignal()

    _BTN = ("QPushButton { background: rgba(255,255,255,0.08); border: none; "
            "border-radius: 4px; color: #aaa; font-size: 11px; padding: 4px 10px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.18); color: #fff; }")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName('TetrisWidget')
        self.setStyleSheet('#TetrisWidget { background: #0d0d0d; }')
        self._bg_css = '#0d0d0d'

        self._board: list[list] = [[None] * COLS for _ in range(ROWS)]
        self._piece: _Piece | None = None
        self._next: _Piece = _Piece()
        self._score = self._lines = 0
        self._level = 1
        self._game_over = self._paused = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._canvas = _Canvas(self)
        root.addWidget(self._canvas, 1)

        # HUD row
        hud = QWidget(); hud.setFixedHeight(HUD_H)
        hud.setStyleSheet('background: #111; border-top: 1px solid #222;')
        hlo = QHBoxLayout(hud)
        hlo.setContentsMargins(6, 0, 6, 0)
        hlo.setSpacing(4)

        self._score_lbl = QLabel('Score: 0   Lines: 0   Lv 1')
        self._score_lbl.setStyleSheet('color: #666; font-size: 11px; background: transparent;')
        hlo.addWidget(self._score_lbl)
        hlo.addStretch()

        for text, slot in [('⏸ Pause', self._toggle_pause),
                           ('↺ Restart', self._restart),
                           ('✕ Close',  lambda: self.closed.emit())]:
            btn = QPushButton(text)
            btn.setStyleSheet(self._BTN)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(slot)
            hlo.addWidget(btn)
            if '⏸' in text:
                self._pause_btn = btn

        root.addWidget(hud)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._new_piece()
        self._timer.start(self._speed())

    def set_theme(self, bg_rgb: str, border_color: str):
        """Apply theme colors — bg_rgb like '20,20,20', border_color hex."""
        try:
            r, g, b = [int(x) for x in bg_rgb.split(',')]
            bg = QColor(r, g, b)
        except Exception:
            bg = QColor('#0d0d0d')
        self._bg_css = bg.name()
        self.setStyleSheet(f'#TetrisWidget {{ background: {bg.name()}; }}')
        self._canvas._bg   = bg
        self._canvas._grid = QColor(border_color)
        self._canvas.update()

    # ── game logic ────────────────────────────────────────────────────────

    def _speed(self):
        return max(80, 500 - (self._level - 1) * 40)

    def _new_piece(self):
        self._piece = self._next
        self._next = _Piece()
        if not self._valid(self._piece):
            self._game_over = True
            self._timer.stop()

    def _valid(self, piece, dx=0, dy=0, shape=None):
        for x, y in piece.cells(dx, dy, shape):
            if x < 0 or x >= COLS or y >= ROWS: return False
            if y >= 0 and self._board[y][x]: return False
        return True

    def _lock(self):
        for x, y in self._piece.cells():
            if 0 <= y < ROWS: self._board[y][x] = self._piece.color
        full = [r for r in range(ROWS) if all(self._board[r])]
        for r in full:
            del self._board[r]; self._board.insert(0, [None] * COLS)
        if full:
            pts = [0, 100, 300, 500, 800][min(len(full), 4)] * self._level
            self._score += pts; self._lines += len(full)
            self._level = self._lines // 10 + 1
            self._timer.setInterval(self._speed())
        self._update_hud()
        self._new_piece()

    def _tick(self):
        if self._game_over or self._paused: return
        if self._valid(self._piece, dy=1):
            self._piece.y += 1
        else:
            self._lock()
        self._canvas.update()

    def _update_hud(self):
        self._score_lbl.setText(
            f'Score: {self._score}   Lines: {self._lines}   Lv {self._level}')

    # ── controls ──────────────────────────────────────────────────────────

    def _toggle_pause(self):
        self._paused = not self._paused
        self._pause_btn.setText('▶ Resume' if self._paused else '⏸ Pause')
        self._canvas.update()

    def _restart(self):
        self._board = [[None] * COLS for _ in range(ROWS)]
        self._score = self._lines = 0; self._level = 1
        self._game_over = self._paused = False
        self._pause_btn.setText('⏸ Pause')
        self._next = _Piece(); self._new_piece()
        self._timer.start(self._speed())
        self._update_hud(); self._canvas.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape: self.closed.emit(); return
        if self._game_over:
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Space): self._restart()
            return
        if k == Qt.Key.Key_P: self._toggle_pause(); return
        if self._paused: return
        if k == Qt.Key.Key_Left  and self._valid(self._piece, dx=-1): self._piece.x -= 1
        if k == Qt.Key.Key_Right and self._valid(self._piece, dx=+1): self._piece.x += 1
        if k == Qt.Key.Key_Down  and self._valid(self._piece, dy=+1): self._piece.y += 1
        if k == Qt.Key.Key_Up:
            rot = _rotate(self._piece.shape)
            if self._valid(self._piece, shape=rot): self._piece.shape = rot
        if k == Qt.Key.Key_Space:
            while self._valid(self._piece, dy=1): self._piece.y += 1
            self._lock()
        self._canvas.update()
