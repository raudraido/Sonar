"""
footer_bridge.py — Python <-> footer_bar.qml bridge + image providers.

Mirrors the QueueBridge pattern (player/panels/right/queue_panel.py):
pyqtSignals push theme/playback state Python -> QML, pyqtSlots receive
QML -> Python callbacks. See UI_MANIFEST.md.
"""

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtQuick import QQuickImageProvider


class FooterArtProvider(QQuickImageProvider):
    """Serves the current cover art as image://footerart/cover."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._pixmap = QPixmap()

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if (pixmap and not pixmap.isNull()) else QPixmap()

    def requestImage(self, id, requestedSize):
        if self._pixmap.isNull():
            empty = QImage(1, 1, QImage.Format.Format_ARGB32)
            empty.fill(Qt.GlobalColor.transparent)
            return empty, empty.size()
        img = self._pixmap.toImage()
        return img, img.size()


class WaveformImageProvider(QQuickImageProvider):
    """Serves the scratch-mode (display mode 0) RGBA buffer as
    image://waveformbuf/frame. The buffer itself is still computed by
    render_scratch_waveform() (waveform_renderer.py, numpy-vectorized) —
    only the blit target changed, from a QWidget paintEvent to a QML
    Canvas's drawImage()."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._image = QImage()

    def set_buffer(self, buf, width, height):
        # .copy() is required: `buf` is reused/mutated next frame by the
        # caller, and QImage(buf.data, ...) only wraps the existing memory.
        self._image = QImage(
            buf.data, width, height, width * 4, QImage.Format.Format_RGBA8888
        ).copy()

    def requestImage(self, id, requestedSize):
        if self._image.isNull():
            empty = QImage(1, 1, QImage.Format.Format_ARGB32)
            empty.fill(Qt.GlobalColor.transparent)
            return empty, empty.size()
        return self._image, self._image.size()


class FooterBridge(QObject):
    # ── Signals: Python -> QML ──────────────────────────────────────────
    accentColorChanged        = pyqtSignal(str)
    panelBgChanged             = pyqtSignal(str)
    hoverColorChanged          = pyqtSignal(str)
    fontColorPrimaryChanged    = pyqtSignal(str)
    fontColorSecondaryChanged  = pyqtSignal(str)
    fontSizePrimaryChanged     = pyqtSignal(int)
    fontSizeSecondaryChanged   = pyqtSignal(int)
    fontFamilyChanged          = pyqtSignal(str)
    borderColorChanged         = pyqtSignal(str)
    borderWidthChanged         = pyqtSignal(int)

    isPlayingChanged           = pyqtSignal(bool)
    positionMsChanged          = pyqtSignal(int, bool)   # ms, hard (snap vs. monotonic-forward)
    durationMsChanged          = pyqtSignal(int)
    shuffleChanged             = pyqtSignal(bool)
    repeatChanged               = pyqtSignal(bool)
    castConnectedChanged       = pyqtSignal(bool)

    mutedChanged               = pyqtSignal(bool)
    volumeChanged              = pyqtSignal(int)

    displayModeChanged         = pyqtSignal(int)
    showRemainingChanged       = pyqtSignal(bool)
    samplesChanged             = pyqtSignal()
    hasRealDataChanged         = pyqtSignal(bool)
    waveformBufVersionChanged  = pyqtSignal(int)

    coverVersionChanged        = pyqtSignal(int)
    trackInfoChanged           = pyqtSignal(str, str, str)   # title, artist, album
    bpmTextChanged              = pyqtSignal(str)
    sidebarArtExpandedChanged  = pyqtSignal(bool)

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    # ── Pull-style getters (QML calls these as functions on demand,
    #    rather than mirroring large arrays through a property/signal) ───
    @pyqtSlot(result=list)
    def getSamples(self):
        return list(self._panel._samples)

    @pyqtSlot(float, float, int, int)
    def computeScratchFrame(self, current_index, pixels_per_sample, width, height):
        self._panel._compute_scratch_frame(current_index, pixels_per_sample, width, height)

    # ── Slots: QML -> Python ─────────────────────────────────────────────
    @pyqtSlot()
    def playClicked(self):
        self._panel.play_clicked.emit()

    @pyqtSlot()
    def prevClicked(self):
        self._panel.prev_clicked.emit()

    @pyqtSlot()
    def nextClicked(self):
        self._panel.next_clicked.emit()

    @pyqtSlot()
    def stopClicked(self):
        self._panel.stop_clicked.emit()

    @pyqtSlot(bool)
    def shuffleToggled(self, on):
        self._panel.shuffle_toggled.emit(on)

    @pyqtSlot(bool)
    def repeatToggled(self, on):
        self._panel.repeat_toggled.emit(on)

    @pyqtSlot(int)
    def volumeChangedByUser(self, value):
        self._panel.volume_changed.emit(value)

    @pyqtSlot()
    def muteClicked(self):
        self._panel.mute_clicked.emit()

    @pyqtSlot(int)
    def seekRequested(self, target_ms):
        self._panel.seek_requested.emit(target_ms)

    @pyqtSlot(bool)
    def scratchModeChanged(self, active):
        self._panel._is_scratching = active
        self._panel.scratch_mode_changed.emit(active)

    @pyqtSlot(float)
    def velocityChanged(self, velocity):
        self._panel.velocity_changed.emit(velocity)

    @pyqtSlot(int)
    def positionUpdated(self, ms):
        self._panel.position_updated.emit(ms)

    @pyqtSlot(int)
    def modeToggled(self, mode):
        self._panel._on_mode_toggled(mode)

    @pyqtSlot(bool)
    def remainingToggled(self, on):
        self._panel._on_remaining_toggled(on)

    @pyqtSlot(str)
    def artistClicked(self, name):
        if name:
            self._panel.artist_clicked.emit(name)

    @pyqtSlot()
    def albumClicked(self):
        self._panel.album_clicked.emit()

    @pyqtSlot()
    def titleClicked(self):
        self._panel.title_clicked.emit()

    @pyqtSlot()
    def trackContextMenuRequested(self):
        self._panel._show_track_context_menu()

    @pyqtSlot()
    def bpmContextMenuRequested(self):
        self._panel._show_bpm_menu()

    @pyqtSlot()
    def expandArtClicked(self):
        self._panel.expand_art_clicked.emit()

    @pyqtSlot()
    def castClicked(self):
        self._panel.cast_clicked.emit()

    @pyqtSlot()
    def settingsClicked(self):
        self._panel.settings_clicked.emit()

    # ── QML-driven native tooltips — mirrors albums_browser.py/
    #    playlists_browser.py/artists_browser.py's showTooltip/hideTooltip,
    #    backed by the shared _TooltipFilter installed in window.py ──────────
    @pyqtSlot(str, int, int, int)
    def showTooltip(self, text: str, cx: int, above_y: int, below_y: int):
        from PyQt6.QtWidgets import QApplication
        t = getattr(self, '_tip_hide_timer', None)
        if t and t.isActive():
            t.stop()
        for w in QApplication.topLevelWidgets():
            tf = getattr(w, '_tooltip_filter', None)
            if tf:
                tf._qml_mode = True
                tf._ensure_tip().show_at(cx, above_y, below_y, text)
                break

    @pyqtSlot()
    def hideTooltip(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        def _do_hide():
            for w in QApplication.topLevelWidgets():
                tf = getattr(w, '_tooltip_filter', None)
                if tf:
                    tf._qml_mode = False
                    if tf._tip and tf._tip.isVisible():
                        tf._tip.hide()
                    break
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(_do_hide)
        t.start(120)
        self._tip_hide_timer = t
