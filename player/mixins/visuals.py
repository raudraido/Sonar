"""
player/mixins/visuals.py — Background blur, cover art crossfade,
dynamic theming, row highlighting, and volume icon updates.
"""
import os
import sys
import time
from player.components.version import __version__

from PyQt6.QtWidgets import QAbstractItemView, QAbstractButton, QApplication, QLabel, QListWidget
from PyQt6.QtCore import Qt, QTimer, QElapsedTimer, QPropertyAnimation, QObject, QEvent, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPixmap, QPainter, QPalette, QImage

from player import resource_path
from player.scroll_tuning import scroll_tuning
from player.workers import BlurWorker, BPMWorker, CoverLoaderWorker

class _ScrollRevealFilter(QObject):
    """Shows the scrollbar handle in master color while scrolling, hides it after idle."""

    def __init__(self, scrollbar, parent=None):
        super().__init__(parent)
        self._sb = scrollbar
        self.color = '#cccccc'
        self._saved_style = ""
        self._active = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._hide)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            self._show()
            self._timer.start()
        return False

    def _show(self):
        if not self._active:
            self._saved_style = self._sb.styleSheet()
            self._active = True
        self._sb.setStyleSheet(
            f"QScrollBar:vertical {{ border: none; background: transparent; width: 6px; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ background: {self.color}; border-radius: 3px; min-height: 30px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
        )

    def _hide(self):
        self._active = False
        self._sb.setStyleSheet(self._saved_style)


class CoverDecodeWorker(QThread):
    """
    Decodes raw cover bytes to a cropped square QImage off the main thread.
    Emit enqueue(cover_id, bytes) to queue work; connect decoded signal for results.
    """
    decoded = pyqtSignal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        import threading
        self._queue = []
        self._lock  = threading.Lock()
        self._wake  = threading.Event()
        self._running = True

    def enqueue(self, cover_id: str, data: bytes, side: int = 300):
        with self._lock:
            self._queue.append((cover_id, data, side))
        self._wake.set()

    def stop(self):
        self._running = False
        self._wake.set()

    def run(self):
        while self._running:
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    cover_id, data, side = self._queue.pop(0)
                img = QImage()
                img.loadFromData(data)
                if img.isNull():
                    continue
                img = img.scaled(side, side,
                                 Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
                x = (img.width()  - side) // 2
                y = (img.height() - side) // 2
                self.decoded.emit(cover_id, img.copy(x, y, side, side))


class SpinRefreshButton(QAbstractButton):
    """
    A refresh button that spins its icon while loading.
    Usage:
        btn = SpinRefreshButton(icon_path, icon_size=18, btn_size=32, color='#ffffff')
        btn.start_spin()   # start animation
        btn._do_stop()     # stop after 600ms minimum
    """
    def __init__(self, icon_path: str, icon_size: int = 14, btn_size: int = 30,
                 color: str = '#ffffff', parent=None):
        super().__init__(parent)
        self._icon_path = icon_path
        self._icon_size = icon_size
        self._color     = QColor(color)
        self._icon_pix  = QPixmap()
        self._angle     = 0.0
        self.loading    = False
        self.setFixedSize(btn_size, btn_size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_color(self, color: str):
        self._color    = QColor(color)
        self._icon_pix = QPixmap()
        self.update()

    def _build_icon(self):
        base = QPixmap(self._icon_path)
        if base.isNull():
            return QPixmap()
        s = self._icon_size
        scaled = base.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap(scaled.size())
        pix.fill(Qt.GlobalColor.transparent)
        from PyQt6.QtGui import QPainter as _P
        p = _P(pix)
        p.drawPixmap(0, 0, scaled)
        p.setCompositionMode(_P.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pix.rect(), self._color)
        p.end()
        return pix

    def _spin_tick(self):
        if not self.loading:
            return
        self._angle = (self._angle + 4.5) % 360
        self.repaint()
        QTimer.singleShot(16, self._spin_tick)

    def start_spin(self):
        import time as _t
        self._t0    = _t.monotonic()
        self.loading = True
        self.repaint()
        QTimer.singleShot(16, self._spin_tick)

    def _do_stop(self):
        import time as _t
        elapsed_ms = (_t.monotonic() - self._t0) * 1000
        remaining  = int(600 - elapsed_ms)
        if remaining > 0:
            QTimer.singleShot(remaining, self._finish_stop)
        else:
            self._finish_stop()

    def _finish_stop(self):
        self.loading = False
        self._angle  = 0.0
        self.repaint()

    def paintEvent(self, _):
        from PyQt6.QtGui import QPainter as _P
        p = _P(self)
        p.setRenderHint(_P.RenderHint.Antialiasing)
        if self.underMouse():
            _theme = getattr(self.window(), 'theme', None)
            p.setBrush(QColor(resolve_menu_hover(_theme)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(self.rect(), 6, 6)
        if self._icon_pix.isNull():
            self._icon_pix = self._build_icon()
        if not self._icon_pix.isNull():
            cx = self.width()  // 2
            cy = self.height() // 2
            p.translate(cx, cy)
            p.rotate(self._angle)
            p.drawPixmap(-self._icon_pix.width() // 2,
                         -self._icon_pix.height() // 2, self._icon_pix)
        p.end()


def install_scroll_reveal(viewport, scrollbar):
    """Attach a _ScrollRevealFilter to a scrollable widget's viewport. Returns the filter."""
    f = _ScrollRevealFilter(scrollbar, scrollbar)
    viewport.installEventFilter(f)
    return f


class SmoothScroller(QObject):
    """
    Momentum wheel-scroll for any QAbstractScrollArea (QScrollArea,
    QListWidget, QTreeWidget, QTreeView, etc.).

    Usage:
        SmoothScroller(my_widget)
        SmoothScroller(my_widget, vsync_source=some_qquickwindow)

    Parents itself to the widget so it is cleaned up automatically.

    Implements the same momentum model as the QML grids/lists (see
    album_grid.qml etc.): each wheel notch adds an impulse to a velocity
    (px/sec) that decays exponentially (friction), like Chromium/macOS wheel
    scrolling — a single notch gives a short glide, rapid notches stack
    velocity for a faster, longer glide that eases out smoothly. Tuning
    (`impulsePerNotch`, `maxVelocity`, `decayHalfLife`) comes from the shared
    `scroll_tuning` singleton (player/scroll_tuning.py) — tune it there, not
    here.

    If the scrollbar moves for any reason other than this scroller's own tick
    (drag, keyboard navigation, programmatic setValue), the velocity is reset
    to zero so the glide never fights the user.

    With no vsync_source, the animation ticks on a 16ms (~60Hz) QTimer. Pass
    a QQuickWindow/QQuickView (e.g. via QMLGridWrapper.quickWindow()) as
    vsync_source to also drive the animation off that window's frameSwapped
    signal, tracking the monitor's real refresh rate (>60Hz).
    """

    def __init__(self, widget, vsync_source=None):
        super().__init__(widget)
        if isinstance(widget, QAbstractItemView):
            widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._w = widget
        self._wheel_velocity = 0.0  # px/sec, +down/-up
        self._position = float(widget.verticalScrollBar().value())  # sub-pixel accumulator
        self._last_wheel_ts = None
        self._vsync_source = vsync_source
        self._animating = False
        self._applying = False
        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        widget.viewport().installEventFilter(self)
        widget.verticalScrollBar().valueChanged.connect(self._on_external_move)

        # SCROLL_DEBUG=1 prints the actual _tick rate once per second while a
        # glide is running, so the effect of vsync_source can be measured
        # (should approach the monitor's refresh rate, not be capped at ~60Hz).
        self._fps_debug = bool(os.environ.get("SCROLL_DEBUG"))
        self._fps_tick_count = 0
        self._fps_t0 = 0.0

    def _is_animating(self):
        return self._timer.isActive()

    def _start_animation(self):
        # The 16ms timer is the guaranteed baseline driver: a vsync_source's
        # frameSwapped can stop firing entirely once its QQuickWindow is
        # scrolled out of view, which would silently stall _tick. The timer
        # keeps _tick alive regardless; frameSwapped just adds bonus ticks
        # for >60Hz smoothness when its window is actually rendering.
        if not self._timer.isActive():
            self._elapsed.restart()
            self._timer.start()
            if self._fps_debug:
                self._fps_tick_count = 0
                self._fps_t0 = time.monotonic()
        if self._vsync_source is not None and not self._animating:
            self._animating = True
            self._vsync_source.frameSwapped.connect(self._tick)
            self._vsync_source.update()

    def _stop_animation(self):
        self._timer.stop()
        if self._vsync_source is not None and self._animating:
            self._animating = False
            try:
                self._vsync_source.frameSwapped.disconnect(self._tick)
            except TypeError:
                pass

    def _tick(self):
        dt = self._elapsed.restart() / 1000.0
        if dt <= 0:
            return
        sb = self._w.verticalScrollBar()
        new_position = self._position + self._wheel_velocity * dt
        minimum, maximum = sb.minimum(), sb.maximum()
        if new_position <= minimum:
            new_position = minimum
            self._wheel_velocity = 0.0
        elif new_position >= maximum:
            new_position = maximum
            self._wheel_velocity = 0.0
        else:
            self._wheel_velocity *= 0.5 ** (dt / scroll_tuning.decayHalfLife)
        self._position = new_position
        self._applying = True
        sb.setValue(int(round(new_position)))
        self._applying = False
        if self._fps_debug:
            self._fps_tick_count += 1
            now = time.monotonic()
            elapsed = now - self._fps_t0
            if elapsed >= 1.0:
                print(f"[scroll fps] {self._fps_tick_count / elapsed:.1f}Hz "
                      f"vsync_source={'yes' if self._vsync_source else 'no'}")
                self._fps_tick_count = 0
                self._fps_t0 = now
        if abs(self._wheel_velocity) <= 1:
            self._stop_animation()
            return
        if self._vsync_source is not None:
            self._vsync_source.update()

    def _on_external_move(self, value: int):
        if not self._applying:
            if self._fps_debug:
                print(f"[scroll settle] external scrollbar move: "
                      f"{self._position:.2f} -> {value} "
                      f"(delta {value - self._position:.2f}, animating={self._is_animating()})")
            self._wheel_velocity = 0.0
            self._position = float(value)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            self._apply_wheel(event)
            return True
        return super().eventFilter(obj, event)

    def _apply_wheel(self, event):
        # QMLGridWrapper.installEventFilter() registers a filter on three
        # underlying QObjects (the wrapper, its container, and the embedded
        # QQuickWindow), since Qt can deliver a Wheel event to any of them
        # depending on the event. That means a WheelForwarder installed on a
        # QMLGridWrapper sees the SAME physical wheel notch 2-3x. Dedup by
        # timestamp so the velocity only gets one impulse per notch.
        ts = event.timestamp()
        if ts and ts == self._last_wheel_ts:
            return
        self._last_wheel_ts = ts

        delta = event.angleDelta().y()
        impulse = -(delta / 120.0) * scroll_tuning.impulsePerNotch
        self._wheel_velocity = max(-scroll_tuning.maxVelocity,
                                    min(self._wheel_velocity + impulse, scroll_tuning.maxVelocity))
        self._start_animation()

    def forward_wheel(self, event):
        """Apply a wheel event captured by an external event filter.

        createWindowContainer's native surface doesn't propagate Wheel
        events to parent widgets the way regular child widgets do, so views
        embedding a QQuickView inside this scroller's widget must catch
        Wheel events themselves (e.g. via WheelForwarder) and hand them here.
        """
        self._apply_wheel(event)


class WheelForwarder(QObject):
    """Redirects QEvent.Wheel from a createWindowContainer's native surface
    to a SmoothScroller, since native child windows don't propagate wheel
    events to parent widgets like regular widgets do.

    Usage:
        forwarder = WheelForwarder(smooth_scroller, parent)
        qml_grid_wrapper.installEventFilter(forwarder)
    """

    def __init__(self, scroller, parent=None):
        super().__init__(parent)
        self._scroller = scroller

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            self._scroller.forward_wheel(event)
            return True
        return False


def resolve_active_hover(theme) -> 'QColor':
    """Return the active-hover halo colour (tab bar + tracklist keyboard halo)."""
    if getattr(theme, 'active_hover_auto', True):
        c = QColor(getattr(theme, 'accent', '#ffffff'))
    else:
        c = QColor(getattr(theme, 'active_hover_color', '#ffffff'))
    c.setAlpha(45)
    return c


def resolve_menu_hover(theme) -> str:
    """Return the effective menu selection highlight colour from the theme."""
    if getattr(theme, 'auto_menu_hover', True):
        return QColor(getattr(theme, 'accent', '#ffffff')).lighter(200).name()
    return getattr(theme, 'menu_hover_color', '#555555')


def menu_hover(accent: str) -> str:
    """CSS fallback for QMenu::item:selected (palette is authoritative)."""
    c = QColor(accent)
    return f"rgba({c.red()},{c.green()},{c.blue()},60)"


def apply_menu_palette(menu, hover_color: str) -> None:
    """Set QPalette Highlight/HighlightedText on a QMenu.

    With WA_TranslucentBackground, Qt6 ignores QSS item:selected background and
    uses the palette instead — this is the only reliable way to set hover colour.
    """
    from PyQt6.QtGui import QPalette
    hover_c = QColor(hover_color)
    text_c = QColor('#111111') if hover_c.lightness() > 140 else QColor('#eeeeee')
    pal = menu.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Normal):
        pal.setColor(group, QPalette.ColorRole.Highlight,       hover_c)
        pal.setColor(group, QPalette.ColorRole.HighlightedText, text_c)
    menu.setPalette(pal)


def scrollbar_css(color: str, hide_horizontal: bool = False) -> str:
    v = (
        f"QScrollBar:vertical {{ border: none; background: transparent; width: 6px; margin: 0; }}"
        f"QScrollBar::handle:vertical {{ background: transparent; min-height: 30px; border-radius: 3px; }}"
        f"QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {color}; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
    )
    if hide_horizontal:
        h = "QScrollBar:horizontal { height: 0px; }"
    else:
        h = (
            f"QScrollBar:horizontal {{ border: none; background: transparent; height: 6px; margin: 0; }}"
            f"QScrollBar::handle:horizontal {{ background: transparent; min-width: 30px; border-radius: 3px; }}"
            f"QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {color}; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}"
            f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}"
        )
    return v + h


def _dim_accent(hex_color: str, target_max: int = 15) -> str:
    """Scale accent RGB down so the brightest channel equals target_max, preserving hue."""
    c = QColor(hex_color)
    r, g, b = c.red(), c.green(), c.blue()
    mx = max(r, g, b)
    if mx == 0:
        return "0,0,0"
    f = target_max / mx
    return f"{int(r * f)},{int(g * f)},{int(b * f)}"


class VisualsMixin:
    def update_background_threaded(self, path, calc_color=True, raw_data_override=None):
        if self.blur_thread and self.blur_thread.isRunning():
            try: self.blur_thread.finished.disconnect()
            except: pass
            self.blur_thread.quit()

        
        # Fixed target art size — large enough to stay sharp for both the
        # 84px footer thumbnail and the expanded left-panel sidebar art
        # (which is scaled down from this, never up).
        art_size = 500

        self.blur_thread = BlurWorker(
            path,
            self.theme.accent,
            calc_color,
            raw_data_override=raw_data_override,
            art_size=art_size
        )
        
        self.blur_thread.finished.connect(self.apply_threaded_art)
        self.blur_thread.start()

    def _apply_dominant_color(self, dominant_color: str):
        """Apply a dominant accent color to the UI (theme + all dependent widgets)."""
        if dominant_color.startswith('#') and len(dominant_color) > 7:
            dominant_color = dominant_color[:7]
        self.theme.accent = dominant_color
        self._auto_tint_bg_colors()
        if getattr(self, 'visualizer', None): self.visualizer.bar_color = QColor(self.theme.accent)
        if hasattr(self, '_queue_panel'): self._queue_panel.set_accent_color(self.theme.accent)
        self._footer_panel.set_accent_color(self.theme.accent)
        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            raw_artist = track.get('artist', 'Unknown')
            formatted_artist = raw_artist.replace(" /// ", f" <span style='color:{self.theme.accent}; font-size:24px'>•</span> ")
            year = str(track.get('year', '') or '').strip()
            if year and year != '0':
                formatted_artist += f"  •  {year}"
            self.track_artist.setText(formatted_artist)
            if hasattr(self, 'heart_btn'):
                raw_state = track.get('starred')
                is_fav = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)
                self.heart_btn.setIcon(self._make_heart_icon(is_fav, self.theme.accent))
        self.refresh_ui_styles(scroll_to_current=False)

    def apply_threaded_art(self, cover_qimg, raw_art, dominant_color):
        self.old_cover_pixmap = getattr(self, 'current_cover_pixmap', None)
        self.current_cover_pixmap = QPixmap()
        if not cover_qimg.isNull():
            self.current_cover_pixmap = QPixmap.fromImage(cover_qimg)

        # Store cover_id instead of raw bytes — CoverCache already has the data on disk
        self.current_raw_art = raw_art

        if self.theme.dynamic_accent:
            if dominant_color not in ('#cccccc', self.theme.accent):
                # Cache the freshly computed color so future plays are instant
                cid = getattr(self, '_last_rendered_cid', None)
                if cid:
                    if not hasattr(self, '_color_cache'):
                        self._color_cache = {}
                    self._color_cache[str(cid)] = dominant_color
            self._apply_dominant_color(dominant_color)

        if not self.current_cover_pixmap.isNull():
            self._footer_panel.set_cover(self.current_cover_pixmap)
        else:
            self._footer_panel.set_cover(None)

        if hasattr(self, '_left_panel'):
            self._left_panel.set_old_art(self.old_cover_pixmap)
            self._left_panel.set_cover_art(
                self.current_cover_pixmap if not self.current_cover_pixmap.isNull() else None
            )

        import gc; gc.collect()

    def _auto_tint_bg_colors(self):
        if not self.theme.auto_bg_from_accent:
            return
        dim = _dim_accent(self.theme.accent)
        for field in ('left_panel_bg', 'queue_panel_bg', 'footer_panel_bg', 'main_panel_bg', 'header_panel_bg'):
            setattr(self.theme, field, dim)
        self._last_theme_key = None
        if hasattr(self, 'swin') and self.swin and self.swin.isVisible():
            self.swin.refresh_theme()

    def apply_cover_art(self, data):
        calc = not getattr(self, '_skip_color_calc', False)
        self._skip_color_calc = False
        self.update_background_threaded(None, raw_data_override=data, calc_color=calc)

    def _perform_heavy_visual_update(self):
        """Called by timer when user has stopped skipping tracks."""
        if not (0 <= self.current_index < len(self.playlist_data)): return

        track    = self.playlist_data[self.current_index]
        track_id = str(track.get('id') or track.get('path', 'unknown'))

        # BPM analysis — runs here so rapid skipping never piles up workers.
        # Also re-runs once per track if bpm_cache has it but beatgrid_cache
        # doesn't — true for every track played before the beat-grid feature
        # existed, since bpm_cache persists across restarts on its own
        # (see load_bpm_cache) and was never going to retroactively gain an
        # anchor otherwise.
        if not getattr(self, 'bpm_detection_disabled', False) and not (
                hasattr(self, 'bpm_cache') and track_id in self.bpm_cache
                and hasattr(self, 'beatgrid_cache') and track_id in self.beatgrid_cache):
            if hasattr(self, 'bpm_worker') and self.bpm_worker.isRunning():
                try: self.bpm_worker.bpm_ready.disconnect()
                except Exception: pass
                try: self.bpm_worker.beatgrid_ready.disconnect()
                except Exception: pass
                self._safe_discard_worker(self.bpm_worker)
            self.bpm_worker = BPMWorker(self.audio_engine, track)
            self.bpm_worker.bpm_ready.connect(self._on_bpm_calculated)
            self.bpm_worker.beatgrid_ready.connect(self._on_beatgrid_calculated)
            self.bpm_worker.start()

        cid = track.get('cover_id') or track.get('coverArt') or track.get('albumId')

        # Skip if this exact cover was just rendered
        if cid and cid == getattr(self, '_last_rendered_cid', None):
            return
        self._last_rendered_cid = cid

        # Apply cached dominant color immediately so the UI updates without waiting for BlurWorker
        if not hasattr(self, '_color_cache'):
            self._color_cache = {}
        cached_color = self._color_cache.get(str(cid)) if cid else None
        if cached_color and getattr(self.theme, 'dynamic_accent', False):
            self._apply_dominant_color(cached_color)
            self._skip_color_calc = True
        else:
            self._skip_color_calc = False

        # 1. Trigger the heavy Blur/Color calculation
        if cid and hasattr(self, 'navidrome_client'):
             cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')

             if getattr(self, 'cover_loader', None) and self.cover_loader.isRunning():
                 self._safe_discard_worker(self.cover_loader)

             self.cover_loader = CoverLoaderWorker(self.navidrome_client, cover_id)
             self.cover_loader.finished.connect(self.apply_cover_art)
             self.cover_loader.start()
        elif track.get('path'):
            self.update_background_threaded(track['path'])
        else:
            self.update_background_threaded(None)        
   
    def _on_fade_step(self, value):
        self.crossfade_progress = value
        if hasattr(self, '_left_panel'):
            self._left_panel.set_crossfade_progress(value)

    def _on_fade_finished(self):
        if hasattr(self, '_left_panel'):
            self._left_panel.clear_old_art()
        self.old_cover_pixmap = None
        import gc; gc.collect()

    def refresh_ui_styles(self, scroll_to_current=True):
        _font_name = getattr(self.theme, 'app_font', '')
        if getattr(self, '_last_app_font', None) != _font_name:
            self._last_app_font = _font_name
            _app = QApplication.instance()
            if _app:
                _f = QFont(_font_name if _font_name else 'Segoe UI')
                _f.setPointSize(10)
                _f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
                _f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
                _app.setFont(_f)

        mc = self.theme.accent
        if mc.startswith('#') and len(mc) > 7:
            mc = mc[:7]
        alpha = 1.0
        rgb = QColor(mc)

        if not hasattr(self, 'icon_cache'):
            self.icon_cache = {}
            
        if not hasattr(self, 'base_pixmap_cache'):
            self.base_pixmap_cache = {}

        def get_cached_icon(icon_name, color):
            key = (icon_name, color)
            if key in self.icon_cache: return self.icon_cache[key]
            
            if icon_name not in self.base_pixmap_cache:
                path = resource_path(icon_name)
                if not os.path.exists(path): return QIcon()
                self.base_pixmap_cache[icon_name] = QPixmap(path)
                
            pixmap = self.base_pixmap_cache[icon_name]
            colored = QPixmap(pixmap.size()); colored.fill(QColor(0,0,0,0))
            painter = QPainter(colored); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(colored.rect(), QColor(color))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawPixmap(0, 0, pixmap); painter.end()
            
            icon = QIcon(colored)
            if len(self.icon_cache) >= 200:
                # Evict oldest quarter when full
                for old_key in list(self.icon_cache.keys())[:50]:
                    del self.icon_cache[old_key]
            self.icon_cache[key] = icon
            return icon

        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            raw_artist = track.get('artist', 'Unknown')
            formatted_artist = raw_artist.replace(" /// ", f" <span style='color:{mc}; font-size:24px'>•</span> ")
            year = str(track.get('year', '') or '').strip()
            if year and year != '0':
                formatted_artist += f"  •  {year}"
            self.track_artist.setText(formatted_artist)
            if hasattr(self, 'heart_btn'):
                raw_state = track.get('starred')
                is_fav = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)
                self.heart_btn.setIcon(self._make_heart_icon(is_fav, mc))

        # Only walk the tree and repaint the indicator row when the playing track or
        # accent color actually changed — skips the expensive tree scan on volume/tab events.
        is_playing = self.audio_engine.is_playing if hasattr(self, 'audio_engine') else False
        indicator_key = (self.current_index, mc, is_playing)
        
        if getattr(self, '_last_indicator_key', None) != indicator_key:
            self._last_indicator_key = indicator_key
            self.update_indicator(scroll_to_current=scroll_to_current)

        self._footer_panel.set_cast_connected(getattr(self, '_cast_connected', False))

        try:
            _r, _g, _b = (int(x) for x in getattr(self.theme, 'main_panel_bg', '14,14,14').split(','))
            _is_dark_bg = (_r * 299 + _g * 587 + _b * 114) / 1000 < 128
        except Exception:
            _is_dark_bg = True
        if getattr(self, '_last_title_bar_dark', None) != _is_dark_bg:
            self._last_title_bar_dark = _is_dark_bg
            self.enable_dark_title_bar(_is_dark_bg)

        theme_key = (
            mc, resolve_menu_hover(self.theme),
            self.theme.auto_border_from_accent, self.theme.manual_border_color,
            self.theme.border_width,
        )

        if getattr(self, '_last_theme_key', None) == theme_key:
            return
            
        self._last_theme_key = theme_key

        if getattr(self, 'visualizer', None): self.visualizer.bar_color = QColor(mc)
        if hasattr(self, '_queue_panel'): self._queue_panel.set_accent_color(mc)

        if hasattr(self, '_queue_panel'): self._queue_panel.apply_theme(self.theme)

        if hasattr(self, '_left_panel'): self._left_panel.set_master_color(mc)
        if hasattr(self, '_left_handle'):  self._left_handle.update_color(mc)
        if hasattr(self, '_queue_handle'): self._queue_handle.update_color(mc)

        active_tab = self.tabs.currentWidget()
        if hasattr(active_tab, 'set_accent_color'):
            active_tab.set_accent_color(mc)

        _active_hover = resolve_active_hover(self.theme)
        for _w in [
            getattr(self, 'global_album_view', None),
            getattr(getattr(self, 'album_browser', None), 'detail_view', None),
            getattr(self, 'tracks_browser', None),
        ]:
            if _w and hasattr(_w, 'set_active_hover'):
                _w.set_active_hover(_active_hover)

        if not getattr(self, '_tab_hook_set', False):
            def _on_tab_changed():
                w = self.tabs.currentWidget()
                if w and hasattr(w, 'set_accent_color'):
                    w.set_accent_color(self.theme.accent)
            self.tabs.currentChanged.connect(_on_tab_changed)
            self._tab_hook_set = True

        _hov = resolve_menu_hover(self.theme)
        _fc1 = getattr(self.theme, 'font_color_primary', '#dddddd')

        # THE MAGICAL CSS TRICK (Inside refresh_ui_styles)
        tabs_css = f"""
            QTabBar {{ border: none; background: transparent; }}
            QTabBar::tab {{
                background: transparent;
                color: {_fc1};
                padding: 10px 5px;
                border: none;
                font-family: 'sans-serif', sans-serif;
                font-weight: bold;
                font-size: {getattr(self.theme, 'font_size_primary', 13)}px;
                border-radius: 5px;
                margin-right: 4px;
            }}
            QTabBar::tab:selected {{ color: {mc}; background: transparent; border: none; }}
            QTabBar::tab:hover {{ color: {_fc1}; background: {_hov}; border-radius: 5px; }}
        """
        # 2. Define the Buttons CSS
        modern_dark_style = f"""
            QPushButton {{ 
                background: #111; 
                color: #888; 
                border: none; 
                border-radius: 5px; 
                font-family: 'sans-serif', sans-serif; 
                font-weight: 900; 
                font-size: 16px; 
                min-width: 30px;   
                min-height: 28px;  
            }} 
            QPushButton:hover {{ 
                background: #222; 
                color: {mc}; 
            }}
            QPushButton:disabled {{
                background: #0a0a0a; 
                color: #333;         
            }}
        """

        # 3. DEFER BOTH STYLES! This prevents the 30ms layout freeze!
        def apply_deferred_styles():
            from PyQt6.QtWidgets import QListWidget
            current_tab = self.tabs.currentWidget()
            grids_to_save = []
            areas_to_save = []

            # Only save/restore scroll on the VISIBLE tab — hidden tabs rebuild lazily
            widget = current_tab
            if widget and hasattr(widget, 'grid_view'):
                grid = widget.grid_view
                grids_to_save.append({
                    'grid': grid,
                    'scroll': grid.verticalScrollBar().value()
                })
                grid.setLayoutMode(QListWidget.LayoutMode.SinglePass)

            if widget and hasattr(widget, 'scroll_area'):
                area = widget.scroll_area
                areas_to_save.append({
                    'area': area,
                    'scroll': area.verticalScrollBar().value()
                })

            # Apply the CSS (triggers style recalc across all tabs)
            self.tabs.setStyleSheet(tabs_css)

            # Rebuild and restore scroll only for the current tab
            for state in grids_to_save:
                grid = state['grid']
                grid.doItemsLayout()
                grid.verticalScrollBar().setValue(state['scroll'])
                grid.setLayoutMode(QListWidget.LayoutMode.Batched)

            for state in areas_to_save:
                area = state['area']
                area.verticalScrollBar().setValue(state['scroll'])
            # ------------------------------------------------

            if hasattr(self, 'btn_back'):
                self.btn_back.set_color(mc)
                self.btn_fwd.set_color(mc)

            if hasattr(self, 'tab_bar'):
                from PyQt6.QtCore import QSize
                self.tab_bar.setIconSize(QSize(16, 16))
                self.tab_bar.set_master_color(mc)
                self.tab_bar.set_active_hover(resolve_active_hover(self.theme))
                try:
                    _r, _g, _b = (int(x) for x in self.theme.header_panel_bg.split(','))
                    self.tab_bar.set_bg_color(QColor(_r, _g, _b))
                except Exception:
                    pass
                icon_map = {
                    'home_tab':           'img/home.png',
                    '_now_playing_panel': 'img/now_playing.png',
                    'album_browser':      'img/albums.png',
                    'artist_browser':     'img/artists.png',
                    'tracks_browser':     'img/tracks.png',
                    'playlists_browser':  'img/playlists.png',
                    '_favorites_tab':     'img/heart.png',
                    '_mix_builder_tab':   'img/mix.png',
                    '_vis_container':     'img/visualizer.png',
                }
                for attr, img in icon_map.items():
                    widget = getattr(self, attr, None)
                    if widget:
                        idx = self.tabs.indexOf(widget)
                        if idx >= 0:
                            icon = get_cached_icon(img, mc)
                            # Always keep _stored_icons current with the tinted icon
                            self.tab_bar._stored_icons[idx] = icon
                            if self.tab_bar.tabText(idx):
                                # Full mode — set icon on tab normally
                                self.tab_bar.setTabIcon(idx, icon)
                            else:
                                # Icon-only mode — overlay draws from _stored_icons;
                                # don't also set it on the tab or it renders twice
                                self.tab_bar.update()

            if hasattr(self, '_now_playing_panel') and hasattr(self._now_playing_panel, 'apply_theme'):
                self._now_playing_panel.apply_theme(self.theme)

        bw = self.theme.border_width
        if self.theme.auto_border_from_accent:
            self.theme.border_color = QColor(mc).darker(250).name()
        else:
            c = QColor(self.theme.manual_border_color)
            self.theme.border_color = f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"
        bc = self.theme.border_color

        # Needs border_color computed above (apply_theme reads it for the
        # footer's top divider line — moving this earlier would read the
        # previous frame's stale value).
        self._footer_panel.apply_theme(self.theme)

        if hasattr(self, '_queue_panel'):
            self._queue_panel.setStyleSheet(
                f'#QueuePanel {{'
                f'  background: rgb({self.theme.queue_panel_bg});'
                f'  border: none;'
                f'  border-left: {bw}px solid {bc};'
                f'  border-radius: 0px;'
                f'}}'
            )
        if hasattr(self, 'main_header'):
            self.main_header.setStyleSheet(
                f'#MainHeader {{ background: rgb({self.theme.header_panel_bg}); border-bottom: {bw}px solid {bc}; }}'
            )
        if hasattr(self, '_left_panel'):
            # apply_theme() sets the panel's full stylesheet itself
            # (background + right-edge border), so no separate setStyleSheet
            # call is needed here.
            self._left_panel.apply_theme(self.theme)
        bg = self.theme.main_panel_bg
        for _i in range(self.tabs.count()):
            _w = self.tabs.widget(_i)
            if hasattr(_w, 'set_bg_color'):
                _w.set_bg_color(bg)
            elif _w is not None and _w.objectName() == 'VisContainer':
                _w.setStyleSheet(f'#VisContainer {{ background: rgb({bg}); }}')
                if getattr(self, 'visualizer', None):
                    self.visualizer.set_bg_color(bg)
                if hasattr(self, '_coming_soon_lbl'):
                    self._coming_soon_lbl.setStyleSheet(
                        f"color: {self.theme.font_color_primary}; background: transparent;"
                        f" border: none; font-size: {self.theme.font_size_primary}px;"
                        f" letter-spacing: 1px; padding: 10px 0 0 0;"
                    )
        if hasattr(self, '_queue_tree_panel') and hasattr(self._queue_tree_panel, 'set_bg_color'):
            self._queue_tree_panel.set_bg_color(bg)
        if hasattr(self, '_queue_panel') and hasattr(self._queue_panel, '_panel_header'):
            from PyQt6.QtWidgets import QWidget as _QW
            _bb = self._queue_panel.findChild(_QW, 'QueueBottomBar')
            if _bb:
                _bb.setStyleSheet(
                    f'#QueueBottomBar {{ background: transparent; border-top: {bw}px solid {bc}; }}'
                )
        if hasattr(self, '_main_panel') and not getattr(self, '_main_panel_ss_set', False):
            self._main_panel.setStyleSheet('#MainPanel { background: transparent; border: none; }')
            self._main_panel_ss_set = True
        if hasattr(self, '_favorites_tab') and hasattr(self._favorites_tab, 'set_accent_color'):
            self._favorites_tab.set_accent_color(mc)
        _tab_bg = f'rgb({self.theme.main_panel_bg})'
        if hasattr(self, '_mix_builder_tab') and self._mix_builder_tab:
            _fc1   = getattr(self.theme, 'font_color_primary', '#dddddd')
            _fsize = getattr(self.theme, 'font_size_primary',  14)
            self._mix_builder_tab.setStyleSheet(
                f'#MixBuilderTab {{ background: {_tab_bg}; }}'
                f' QLabel {{ color: {_fc1}; background: transparent; border: none;'
                f' font-size: {_fsize}px; letter-spacing: 1px; }}'
            )

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, apply_deferred_styles)

        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.set_accent_color(mc)

        if hasattr(self, 'swin') and self.swin and self.swin.isVisible():
            _swin = self.swin
            QTimer.singleShot(0, _swin.refresh_theme)
        pass  # Tooltip styling handled by _TooltipFilter in window.py

    def refresh_visuals(self):
        self.refresh_ui_styles()

    def update_indicator(self, scroll_to_current=True):
        """Updates the dancing GIF, row highlight, and broadcasts the playing state to all tabs."""
        if not hasattr(self, 'tree') or not self.tree:
            return
            
                    
        mc = self.theme.accent
        rgb = QColor(mc)

        # Define styles for the active and inactive rows
        highlight_bg = QColor(rgb.red(), rgb.green(), rgb.blue(), 40)
        default_color = QColor("#ddd")
        transparent = QColor(0, 0, 0, 0)
        
        normal_font = QFont("sans-serif", 10)
        bold_font = QFont("sans-serif", 10)
        bold_font.setBold(True)
        
        target_item = None
        is_playing = hasattr(self, 'audio_engine') and self.audio_engine.is_playing

        # --- 1. UPDATE THE MAIN "NOW PLAYING" QUEUE ---
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            
            if i == getattr(self, 'current_index', -1):
                target_item = item
                
                # Apply the dancing GIF or numbers
                if is_playing and hasattr(self, 'playing_movie'):
                    item.setText(0, "")          # ← clear track number first
                    pi_label = QLabel()
                    pi_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    pi_label.setStyleSheet("background: transparent;")
                    pi_label.setMovie(self.playing_movie)
                    # no QGraphicsColorizeEffect
                    self.tree.setItemWidget(item, 0, pi_label)
                    self.playing_movie.start()
                else:
                    if self.tree.itemWidget(item, 0):
                        self.tree.removeItemWidget(item, 0)
                        if hasattr(self, 'playing_movie'):
                            self.playing_movie.stop()
                    item.setText(0, str(i + 1))
                
                # Apply the row highlight and bold text
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, highlight_bg)
                    if col != 7:  # Skip the Heart/Favorite column
                        item.setForeground(col, rgb)
                        item.setFont(col, bold_font)
            else:
                # Clear styles for non-playing rows
                if self.tree.itemWidget(item, 0):
                    self.tree.removeItemWidget(item, 0)
                item.setText(0, str(i + 1))
                
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, transparent)
                    if col != 7:  # Skip the Heart/Favorite column
                        item.setForeground(col, default_color)
                        item.setFont(col, normal_font)
                        
        # Auto-scroll the Now Playing queue if requested
        if target_item and scroll_to_current: 
            self.tree.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtCenter)
            
        self.last_index = getattr(self, 'current_index', -1)


        # --- 2. GLOBAL GIF SYNC (Broadcast to Albums, Tracks, and Playlists) ---
        playing_id = None
        if hasattr(self, 'playlist_data') and getattr(self, 'current_index', -1) >= 0:
            if self.current_index < len(self.playlist_data):
                playing_id = self.playlist_data[self.current_index].get('id')
            
        # Compile a list of all grids that need to know about the playing track
        browsers_to_update = [
            getattr(self, 'tracks_browser', None),
            getattr(getattr(self, 'global_album_view', None), 'track_list', None),
            getattr(getattr(self, 'global_playlist_view', None), 'track_list', None),
            getattr(self, 'global_album_view', None),
            getattr(getattr(self, 'album_browser', None), 'detail_view', None),
        ]
        
        # Broadcast the ID, state, and exact color downward
        for tb in browsers_to_update:
            if tb and hasattr(tb, 'update_playing_status'):
                tb.update_playing_status(playing_id, is_playing, mc)

    def update_window_title(self):
        if 0 <= self.current_index < len(self.playlist_data):
            status = "Playing" if self.audio_engine.is_playing else "Paused"
            track = self.playlist_data[self.current_index]
            title = track.get('title', 'Unknown')
            artist = track.get('artist', '')
            self.setWindowTitle(f"({status}) [{self.current_index + 1}/{len(self.playlist_data)}] {title} — {artist}")
        else:
            self.setWindowTitle(f"Icosahedron {__version__}")

    def update_volume(self, value):
        """Optimized: Only updates audio engine and icon, skips full UI refresh."""
        self.audio_engine.set_volume(value)
        self._footer_panel.volume = value

        should_be_muted = (value == 0)

        if should_be_muted != self.is_muted:
            self.is_muted = should_be_muted
            self.update_volume_icon()

        if not self.is_muted:
            self.last_volume = value

    def update_volume_icon(self):
        """Lightweight update just for the speaker icon."""
        self._footer_panel.set_muted(self.is_muted)

    def toggle_mute(self):
        if not self.is_muted:
            self.last_volume = self._footer_panel.volume
            self.update_volume(0)
        else:
            self.update_volume(self.last_volume)

    def adjust_volume_by(self, delta):
        """Changes volume via keyboard."""
        new_vol = max(0, min(100, self._footer_panel.volume + delta))
        self.update_volume(new_vol)

    def get_colored_pixmap(self, path, color, size):
        """Helper to tint an image without reloading the whole interface."""
        if not os.path.exists(path): return QPixmap()
        
        # 1. Load the original image
        src = QPixmap(path)
        
        # 2. Scale it to fit within the box (preserving aspect ratio)
        src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        
        # 3. Create the final transparent canvas
        colored = QPixmap(size, size)
        colored.fill(QColor(0, 0, 0, 0)) # Transparent background
        
        painter = QPainter(colored)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 4. Draw the Icon FIRST (centered)
        x = (size - src.width()) // 2
        y = (size - src.height()) // 2
        painter.drawPixmap(x, y, src)
        
        # 5. Tint it using SourceIn 
        # (Meaning: "Replace the color of what I just drew with this new color")
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(colored.rect(), QColor(color))
        
        painter.end()
        
        return colored
    
    def load_current_track_metadata(self):
        """
        Wrapper to fix AttributeError.
        1. Updates text immediately (Fast).
        2. Schedules heavy graphics/color update (Debounced).
        """
        # 1. Update Labels Instantly
        self.load_current_track_metadata_text_only()
        
        # 2. Stop any pending heavy updates from previous rapid clicks
        self.visual_update_timer.stop()
        
        # 3. Schedule the heavy update (Blur/Color) to run in 350ms
        # This prevents lag if this method is called rapidly (e.g. gapless transitions)
        self.visual_update_timer.start(350)
    
    def load_current_track_metadata_text_only(self):
        """Updates only the labels. Fast and safe to run instantly."""
        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            
            # 1. Update Title and Artist
            title = track.get('title', 'Unknown').strip()
            artist = track.get('artist', 'Unknown').strip()
            year = str(track.get('year', '') or '').strip()
            artist_year = f"{artist}  •  {year}" if (year and year != '0') else artist
            self.track_title.setText(title)
            self.track_artist.setText(artist_year)

            # Update heart button state
            if hasattr(self, 'heart_btn'):
                raw_state = track.get('starred')
                if isinstance(raw_state, str):
                    is_fav = raw_state.lower() in ('true', '1')
                else:
                    is_fav = bool(raw_state)
                accent = getattr(self, 'master_color', '#ffffff')
                self.heart_btn.setIcon(self._make_heart_icon(is_fav, accent))
            
            # 2. Determine Base File Type (STREAM vs MP3/FLAC)
            target_path = track.get('path', '')
            stream_url = track.get('stream_url', '')
            
            if stream_url and not target_path:
                self.current_file_type_text = "STREAM"
            else:
                self.current_file_type_text = os.path.splitext(target_path)[1].upper().replace('.', '') if target_path else 'UNKNOWN'

            # 3. Update the Bottom "Now Playing" Widget
            if hasattr(self, '_footer_panel'):
                self._footer_panel.set_file_type(self.current_file_type_text)
                self._footer_panel.set_track_info(title, artist, track.get('album', ''))
                self._footer_panel.set_track(track)
                # Eagerly show cached cover art in the footer without waiting for BlurWorker
                _cid = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
                if _cid:
                    try:
                        from player.components.cover_cache import CoverCache
                        _data = CoverCache.instance().get_full(_cid) or CoverCache.instance().get_thumb(_cid)
                        if _data:
                            _pix = QPixmap()
                            _pix.loadFromData(_data)
                            if not _pix.isNull():
                                self._footer_panel.set_cover(_pix)
                    except Exception:
                        pass

            # Update the rich Now Playing info tab
            if hasattr(self, '_now_playing_panel') and hasattr(self._now_playing_panel, 'load_track'):
                self._now_playing_panel.load_track(track)

            # 4. BPM Cache Check — worker is started in _perform_heavy_visual_update
            track_id = str(track.get('id') or track.get('path', 'unknown'))
            if hasattr(self, 'bpm_cache') and track_id in self.bpm_cache:
                bpm = self.bpm_cache[track_id]
                self.file_type_label.setText(f"{self.current_file_type_text}   •   {bpm:.1f} BPM")
                self._footer_panel.set_bpm(bpm)
                if hasattr(self, 'beatgrid_cache') and track_id in self.beatgrid_cache:
                    positions = self.beatgrid_cache[track_id]
                    if self._beatgrid_matches_bpm(positions, bpm):
                        self._footer_panel.set_beatgrid(bpm, positions)
                    else:
                        # Stale grid left over from a BPM correction made
                        # before this consistency check existed — silently
                        # regenerate it to match the cached (correct) BPM.
                        self._regenerate_beatgrid_for_bpm(track_id, bpm)
            elif getattr(self, 'bpm_detection_disabled', False) and track.get('bpm'):
                # Detection is off and nothing's cached — fall back to the
                # raw ID3/tag BPM instead of leaving "BPM..." forever, since
                # no worker will ever run to fill bpm_cache in.
                bpm = float(track['bpm'])
                self.file_type_label.setText(f"{self.current_file_type_text}   •   {bpm:.1f} BPM")
                self._footer_panel.set_bpm(bpm)
            else:
                self.file_type_label.setText(f"{self.current_file_type_text}   •   BPM...")
                self._footer_panel.set_bpm(None)

            # Lyrics — load for the new track
            if hasattr(self, '_queue_panel'):
                self._queue_panel.queue_lyrics_load(track)

    def _make_heart_icon(self, active, color_str):
        path = resource_path("img/heart_filled.png" if active else "img/heart.png")
        base = QPixmap(path)
        if base.isNull():
            return QIcon()
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.drawPixmap(0, 0, base)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pix.rect(), QColor("#E91E63" if active else "#555555"))
        painter.end()
        return QIcon(pix)

    def _toggle_now_playing_favorite(self):
        if not (0 <= self.current_index < len(self.playlist_data)):
            return
        track = self.playlist_data[self.current_index]
        raw_state = track.get('starred')
        if isinstance(raw_state, str):
            current_state = raw_state.lower() in ('true', '1')
        else:
            current_state = bool(raw_state)
        new_state = not current_state
        track['starred'] = new_state
        accent = getattr(self, 'master_color', '#ffffff')
        self.heart_btn.setIcon(self._make_heart_icon(new_state, accent))
        if hasattr(self, 'navidrome_client') and self.navidrome_client:
            import threading
            threading.Thread(
                target=lambda: self.navidrome_client.set_favorite(track.get('id'), new_state),
                daemon=True
            ).start()

    def set_elided_text(self, label, text):
        metrics = QFontMetrics(label.font())
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, label.width())
        label.setText(elided)
    
    def enforce_artist_min_width(self, index, old_size, new_size):
        MIN_WIDTH = 100 
        if index == 1 and new_size < MIN_WIDTH:
            self.tree.header().resizeSection(1, MIN_WIDTH)
    
    def enable_dark_title_bar(self, is_dark=True):
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                val  = ctypes.c_int(1 if is_dark else 0)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), 4)
                # Force non-client area repaint so Windows applies the change immediately
                _SWP = 0x0027  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)
            except Exception:
                pass
       
