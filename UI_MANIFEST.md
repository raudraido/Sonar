# UI Manifest

Rules for QML hosting, scrolling, theming, and overlay z-order, established
during the QQuickWidget → QQuickView (144Hz) refactor. Any new view or change
to an existing one should follow these patterns so the app behaves
consistently across pages.

## 1. Hosting QML: always QQuickView + createWindowContainer

**Never use `QQuickWidget`.** `QQuickWidget` has no real native surface, so
its animation driver is hardcoded to ~16ms (~60Hz) regardless of the
monitor's actual refresh rate. `QQuickView` embedded via
`QWidget.createWindowContainer()` gets a real native surface and tracks the
monitor's actual vsync.

### Standard widget: `QMLGridWrapper` (`player/widgets.py:1920`)

For any new QML-hosting widget, reuse `QMLGridWrapper` rather than writing a
new `QQuickView`/container pair. It's a composite `QWidget` that already
provides:

- `engine()`, `rootContext()`, `rootObject()`, `setSource(url)`,
  `quickWindow()` (returns the underlying `QQuickView`)
- `setClearColor(color)` → `_view.setColor(color)`
- `setResizeMode(mode)` — accepts `QQuickWidget.ResizeMode` values (the int
  values match `QQuickView.ResizeMode` 1:1, so existing call sites using
  `QQuickWidget.ResizeMode.SizeRootObjectToView` / `SizeViewToRootObject`
  keep working unchanged)
- Focus forwarding (`setFocus`, `hasFocus`, `setFocusPolicy`) to the native
  container
- `_search_active`/`_capturing` mirrored onto the container for
  `player/mixins/keyboard.py`'s type-to-search interceptor
- Event filter / cursor forwarding via `installEventFilter`,
  `removeEventFilter`, `setCursor`, `unsetCursor`, and `_owns(obj)` (checks
  `self`, `_container`, and `_view` — events can land on either the
  container widget or the embedded `QQuickWindow` depending on event type)
- Legacy `QListWidget`-compatible no-op shims (`verticalScrollBar`,
  `viewport`, `clear`, `setSpacing`, etc.) for old call sites

### Resize mode rules

- **`SizeRootObjectToView`** — imperatively calls `item->setSize()`, which
  *clears* any QML binding on the root item's `width`/`height`. Only safe
  when the root item has **no** such bindings (plain `anchors.fill`-style
  content, e.g. `artist_section_grid.qml`).
- **`SizeViewToRootObject`** — only calls `window->resize()`, never touches
  QML item properties. Safe to keep for content-driven-height views where the
  QML root has explicit `width:`/`height:` bindings (e.g.
  `height: mainCol.implicitHeight + 24`) and Python is told the height via a
  `reportHeight`/`heightReportTimer` bridge signal, then calls
  `setFixedHeight(h)` on the wrapper.

### Avoid white-flash

`QQuickView` defaults to a white clear color. Always call
`wrapper.setClearColor(QColor(r, g, b))` with the panel/section background
color immediately after construction, and again whenever the theme changes.

### Recycled delegates: no `mipmap: true`

Don't set `mipmap: true` on `Image` delegates fed by a
`QQuickImageProvider` with `cache: false` in a recycled `ListView`/`GridView`
— this combination spams `QSGPlainTexture: Mipmap settings changed...`
warnings during fast scrolling. Use `cache: false; smooth: true` without
`mipmap`.

## 2. Scrolling

One model, used everywhere: **momentum/friction wheel-scroll**. Every wheel
notch adds an impulse to a velocity (px/sec); each frame, the scroll position
moves by `velocity * dt`, then velocity decays exponentially
(`velocity *= 0.5^(dt / decayHalfLife)`) — like Chromium/macOS wheel
scrolling. A single notch gives a short glide (~120px); rapid notches stack
velocity for a faster, longer glide that eases out smoothly (continuously
decreasing speed), unlike a constant-velocity-to-target model (constant speed
then an abrupt stop). Velocity is clamped at the content bounds (zeroed on
hit) and at `maxVelocity`.

### Single source of truth: `scroll_tuning` (`player/scroll_tuning.py`)

The "feel" of every scroll — QML grid/list wheel-scroll and the QWidget
`SmoothScroller` alike — comes from one shared `ScrollTuning` QObject
singleton (`scroll_tuning`), with three properties:

- `impulsePerNotch` (default `1600.0`, px/sec) — velocity added per wheel
  notch.
- `maxVelocity` (default `8000.0`, px/sec) — cap on accumulated velocity.
- `decayHalfLife` (default `0.045`, seconds) — time for velocity to halve
  (friction). Lower = snappier stop, higher = longer glide.

To change the app-wide scroll feel, edit the defaults in
`player/scroll_tuning.py` — **don't** hardcode these numbers anywhere else.
All three properties have `NOTIFY` signals, so this is ready to become a live
theme-builder setting later (just call the setters; QML bindings and Python
reads both pick up the new value immediately).

Every `QQuickView`/engine must expose it as a context property:
```python
self._view.rootContext().setContextProperty("scrollTuning", scroll_tuning)
```
`QMLGridWrapper` does this in its constructor (covers album/artist/playlist
grids and the artist section grids); `AlbumDetailView`
(`player/tabs/albums/albums_browser.py`) and `HomeView`
(`player/tabs/home/home.py`) do it alongside their other
`setContextProperty` calls. Any new QQuickView must do the same.

### QML views (Flickable/ListView/GridView content scroll): `MomentumScroll.qml`

`MomentumScroll.qml` (`player/tabs/shared_qml/`, alongside
`IconButton.qml`/`SearchBar.qml`/`SkeletonCard.qml` — each consuming tab's
QML imports this directory via `import "../shared_qml"`) implements the
model above as a drop-in child of the view it scrolls. Used by
`album_grid.qml`'s `grid`, `artist_grid.qml`, `playlist_grid.qml`,
`album_detail.qml`'s `trackList`, and `home.qml`'s `scroller`. Any new
scrolling QML view should use it too:

```qml
import QtQuick      // needed for FrameAnimation (used inside MomentumScroll)
import "../shared_qml"  // for MomentumScroll (adjust relative path as needed)

// interactive: false — momentum model has sole control; no native
// touch/drag flicking on this view.
interactive: false

MomentumScroll {
    target: view
    // GridView (topMargin/bottomMargin):
    minContentY: -view.topMargin
    maxContentY: Math.max(minContentY, view.contentHeight + view.bottomMargin - view.height)
    // ListView with a header (originY != 0):
    //   minContentY: view.originY
    //   maxContentY: view.originY + Math.max(0, view.contentHeight - view.height)
    // Plain Flickable: omit minContentY/maxContentY — defaults to
    // 0 / (contentHeight - height).
}
```

`MomentumScroll` reads `scrollTuning` from the engine's root context
(set by `QMLGridWrapper`/`AlbumDetailView`/`HomeView`, see above) — no extra
wiring needed. `onContentYChanged` no longer needs to sync a separate
`targetY`; scrollbar drag / keyboard nav / programmatic `contentY` writes
just work since there's no separate target to fight.

`FrameAnimation` (Qt 6.3+, from `import QtQuick`) is required instead of a
`Timer` — a `Timer` is capped around 60Hz, which is visibly choppy on a
143.8Hz monitor.

**Gotcha — `z: -1` is required**: `MomentumScroll`'s root `Item` is added as
a QML child of the `Flickable`/`ListView`/`GridView` it scrolls, which means
it's inserted *after* that view's own `contentItem` in the children list —
without an explicit `z`, it paints on top of and wins cursor/hover
hit-testing over everything the view contains (header buttons, column-resize
handles, delegates), silently breaking every `cursorShape` set on those
items even though they still receive clicks. `MomentumScroll.qml` sets
`z: -1` for exactly this reason — don't remove it, and give any other
full-`anchors.fill` overlay child the same treatment.

### QWidget scroll areas: `SmoothScroller` (`player/mixins/visuals.py:198`)

`SmoothScroller(widget)` adds the same momentum wheel-scroll to any
`QAbstractScrollArea` / `QAbstractItemView` — matching the QML pattern above,
so QML-grid pages and QWidget-list pages feel identical.

- Each wheel notch adds `scroll_tuning.impulsePerNotch` to
  `self._wheel_velocity`, clamped to `±scroll_tuning.maxVelocity`.
- The 16ms `QTimer` is **always** the baseline driver for `_tick` (so a
  `vsync_source` whose `QQuickWindow` stops rendering once scrolled out of
  view can never silently stall the glide).
- Optional `vsync_source` (a `QQuickWindow`/`QQuickView`, e.g.
  `wrapper.quickWindow()`): when given, its `frameSwapped` signal *also*
  drives `_tick` (bonus ticks at the real monitor refresh rate, on top of
  the 16ms baseline) for extra smoothness while that window is actively
  rendering. Use this whenever the page hosts at least one `QMLGridWrapper`
  (pass any one of them — e.g. the header).
- `_tick` moves the scrollbar by `wheel_velocity * dt`, clamps at
  `sb.minimum()`/`sb.maximum()` (zeroing velocity on hit), then decays
  `wheel_velocity *= 0.5 ** (dt / scroll_tuning.decayHalfLife)`. Stops the
  timer once `abs(wheel_velocity) <= 1`.
- If the scrollbar moves for any reason other than this scroller's own
  `_tick` (drag, keyboard nav, programmatic `setValue`), `_on_external_move`
  zeroes `_wheel_velocity` so the glide never fights the user.
- To reset/cancel an in-flight glide programmatically, call
  `scroller._stop_animation()` and set `scroller._wheel_velocity = 0.0`.

### Forwarding wheel events out of native QML surfaces: `WheelForwarder` (`player/mixins/visuals.py:360`)

`createWindowContainer`'s native child window does **not** propagate
unhandled `QEvent.Wheel` to parent widgets the way a regular child widget
does. Any time a `QMLGridWrapper` sits *inside* a `QScrollArea` (rather than
*being* the scroll area itself), install a `WheelForwarder` on it:

```python
self._smooth_scroller = SmoothScroller(self.scroll, vsync_source=self._qml.quickWindow())
self._wheel_forwarder = WheelForwarder(self._smooth_scroller, self)
self._qml.installEventFilter(self._wheel_forwarder)
```

This is **not** needed when the `QMLGridWrapper` *is* the whole scrollable
surface (album/artist/playlist grids, `ArtistRichDetailView`'s single-page
`artist_detail_page.qml`) — those handle their own wheel via the QML pattern
in §2 above.

**Gotcha — duplicate wheel events**: `QMLGridWrapper.installEventFilter()`
registers the filter on *three* underlying QObjects (the wrapper itself,
`_container`, and `_view`/`QQuickWindow`), since Qt can deliver a given event
to any of them depending on its type. For `QEvent.Wheel` this means a
`WheelForwarder` sees the same physical notch 2-3x. `SmoothScroller._apply_wheel`
dedups this by `event.timestamp()` — don't remove that check, and don't add
a second `WheelForwarder`/`SmoothScroller` pair reading the same wheel
stream without the same dedup, or each notch will add 2-3x the intended
impulse to the velocity.

## 3. Overlay z-order

**Rule**: a `createWindowContainer`'s native child window always paints
*above* regular (non-native) sibling `QWidget`s, regardless of normal widget
stacking order or `raise_()`. Any overlay that must visually sit on top of,
or be covered by, a `QMLGridWrapper` needs one of these two fixes:

### Pattern A — top-level `Tool` window (for overlays that must appear ABOVE QML)

Used by `_CoverOverlay` (`player/tabs/now_playing/now_playing_info.py:405`),
`_ArtistPhotoOverlay` and `_ArtistLoadingOverlay`
(`player/tabs/artists/artists_browser.py`). Convert the overlay from a child
`QWidget` to a top-level frameless window:

```python
super().__init__(None,
    Qt.WindowType.Tool |
    Qt.WindowType.FramelessWindowHint |
    Qt.WindowType.NoDropShadowWindowHint)
self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
self.setGeometry(parent.geometry())
parent.installEventFilter(self)   # sync geometry on Move/Resize
parent._some_overlay = self        # strong Python ref — see below
self.show(); self.raise_(); self.activateWindow(); self.setFocus()
```

- **Must** keep a strong Python reference on the parent
  (`parent._some_overlay = self`, cleared in the close handler). A
  parentless `QWidget` with refcount 0 is garbage-collected immediately after
  `__init__` returns — fatal if it owns a running `QThread`
  (`QThread: Destroyed while thread is still running` → abort).
- Sync geometry via an `eventFilter` on the parent window for
  `QEvent.Move`/`QEvent.Resize`.

### Pattern B — `WA_NativeWindow` (for small overlays that must sit ON TOP of QML within the same window)

Used by `LeftPanel`'s nav `ArrowButton`s (`player/widgets.py:2113`). Promote
the overlay widget to a native window so normal `raise_()`/z-order works
against the `QMLGridWrapper`'s container:

```python
widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
```

- Native child windows **do not composite `WA_TranslucentBackground`** —
  they render as opaque black if you try. If the overlay needs to look
  transparent, give it a `set_bg_color(color)` that paints an **opaque
  fill** matching the QML surface behind it (see `ArrowButton.set_bg_color`,
  `_SpinnerRing.set_bg_color` in `player/panels/right/queue_panel.py`), and
  call it whenever the theme/panel background changes.
- Keep `raise_()` ordering correct: anything that must sit on top (e.g. a
  spinner on top of its loading overlay) must call `raise_()` *after* the
  layer below it.

## 4. Theming QML views

- Each QML-hosting widget has a `*Bridge(QObject)` with `pyqtSignal`s for
  every themeable property (colors, font sizes, etc.), consumed in QML via a
  `Connections { target: someBridge; function onXChanged(v) { root.x = v } }`
  block.
- On `apply_theme`/`set_accent_color`, emit the bridge signals **and** call
  `wrapper.setClearColor(QColor(r, g, b))` for the panel background, **and**
  update any `WA_NativeWindow` overlay's `set_bg_color(...)` (Pattern B
  above) so all three stay in sync.
- `_bg_qcolor()`-style helpers (see `ArtistRichDetailView`) that parse the
  stored `"r,g,b"` theme string into a `QColor` are the standard way to share
  one color across QML clear-color and native-overlay backgrounds.

## 5. Scrollbars

Use `scrollbar_css(color)` (`player/mixins/visuals.py:420`) for any
`QScrollBar` stylesheet — hidden by default, shown in the accent/master color
on hover or while scrolling via `install_scroll_reveal(viewport, scrollbar)`
(`player/mixins/visuals.py:191`). QML `ScrollBar` items follow the same
hidden-until-active visual language (`opacity` bound to
`root.isScrollActive`/`pressed`/`hovered`).
