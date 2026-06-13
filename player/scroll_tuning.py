"""Single source of truth for the app's wheel-scroll "feel".

Both the QML grids/lists (via the `scrollTuning` context property, see
QMLGridWrapper, AlbumDetailView, HomeView) and the QWidget-side
SmoothScroller (player/mixins/visuals.py) implement the same momentum
model: each wheel notch adds an impulse to a velocity that decays
exponentially (friction), like Chromium/macOS wheel scrolling — a single
notch gives a short glide, rapid notches stack velocity for a faster, longer
glide that eases out smoothly. Exposed as a QObject with NOTIFY signals so a
future theme-builder setting can adjust it live across every open view.
"""

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal


class ScrollTuning(QObject):
    impulsePerNotchChanged = pyqtSignal()
    maxVelocityChanged = pyqtSignal()
    decayHalfLifeChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._impulse_per_notch = 1600.0  # px/sec added to velocity per wheel notch
        self._max_velocity = 8000.0       # px/sec cap on accumulated velocity
        self._decay_half_life = 0.045     # seconds for velocity to halve (friction)

    def _get_impulse_per_notch(self):
        return self._impulse_per_notch

    def _set_impulse_per_notch(self, value):
        value = float(value)
        if value != self._impulse_per_notch:
            self._impulse_per_notch = value
            self.impulsePerNotchChanged.emit()

    impulsePerNotch = pyqtProperty(float, _get_impulse_per_notch, _set_impulse_per_notch, notify=impulsePerNotchChanged)

    def _get_max_velocity(self):
        return self._max_velocity

    def _set_max_velocity(self, value):
        value = float(value)
        if value != self._max_velocity:
            self._max_velocity = value
            self.maxVelocityChanged.emit()

    maxVelocity = pyqtProperty(float, _get_max_velocity, _set_max_velocity, notify=maxVelocityChanged)

    def _get_decay_half_life(self):
        return self._decay_half_life

    def _set_decay_half_life(self, value):
        value = float(value)
        if value != self._decay_half_life:
            self._decay_half_life = value
            self.decayHalfLifeChanged.emit()

    decayHalfLife = pyqtProperty(float, _get_decay_half_life, _set_decay_half_life, notify=decayHalfLifeChanged)


# Shared singleton — import this, don't construct your own.
scroll_tuning = ScrollTuning()
