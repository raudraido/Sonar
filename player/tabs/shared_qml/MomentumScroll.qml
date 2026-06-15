import QtQuick

// Reusable momentum wheel-scroll behavior for any Flickable-derived view
// (GridView, ListView, Flickable). Each wheel notch adds an impulse to a
// velocity (px/sec) that decays exponentially (friction), like Chromium/
// macOS wheel scrolling — a single notch gives a short glide, rapid notches
// stack velocity for a faster, longer glide that eases out smoothly, unlike
// a constant-velocity-to-target model where speed runs at full speed then
// stops abruptly. Tuning (impulsePerNotch, maxVelocity, decayHalfLife) comes
// from the shared `scrollTuning` context property (player/scroll_tuning.py)
// — tune it there, not here.
//
// Usage: add as a child of the Flickable it should scroll.
//
//   GridView {
//       id: grid
//       ...
//       MomentumScroll {
//           target: grid
//           minContentY: -grid.topMargin
//           maxContentY: Math.max(minContentY, grid.contentHeight + grid.bottomMargin - grid.height)
//       }
//   }
//
// For a plain Flickable with no extra margins, minContent/maxContent can
// be left at their defaults (0 / contentHeight - height). For a ListView
// with a header (originY != 0), bind both to target.originY-based bounds.
//
// Set horizontal: true to drive target.contentX instead of contentY (e.g.
// a horizontal ListView) — minContent/maxContent then refer to the X axis.
Item {
    id: root

    required property Flickable target
    property bool horizontal: false
    property real minContent: 0
    property real maxContent: Math.max(minContent, (horizontal ? target.contentWidth - target.width : target.contentHeight - target.height))

    // Deprecated aliases kept for existing vertical call sites.
    property alias minContentY: root.minContent
    property alias maxContentY: root.maxContent

    property real wheelVelocity: 0   // px/sec, +down/-up (or +right/-left when horizontal)

    anchors.fill: parent
    // Sit behind the Flickable's content (header/delegates): this Item is
    // added after the Flickable's own contentItem, so without an explicit z
    // it wins cursor/hover hit-testing everywhere it overlaps, swallowing
    // every cursorShape set on content MouseAreas (buttons, resize handles).
    z: -1

    // FrameAnimation ticks on the render loop's actual vsync (>60Hz on a
    // 143.8Hz monitor), unlike a Timer which is capped around 60Hz.
    FrameAnimation {
        running: Math.abs(root.wheelVelocity) > 1
        onTriggered: {
            var dt = frameTime
            if (dt <= 0) return
            var current = root.horizontal ? root.target.contentX : root.target.contentY
            var newPos = current + root.wheelVelocity * dt
            if (newPos <= root.minContent) {
                newPos = root.minContent
                root.wheelVelocity = 0
            } else if (newPos >= root.maxContent) {
                newPos = root.maxContent
                root.wheelVelocity = 0
            } else {
                root.wheelVelocity *= Math.pow(0.5, dt / scrollTuning.decayHalfLife)
                if (Math.abs(root.wheelVelocity) <= 1) {
                    // Last frame of this glide: snap to a whole pixel so
                    // Text content (Text.QtRendering) lands on the same
                    // pixel as the sub-pixel-positioned images/rects,
                    // instead of settling ~0.5px apart and popping by 1px
                    // once the glide stops.
                    newPos = Math.round(newPos)
                }
            }
            if (root.horizontal) {
                root.target.contentX = newPos
            } else {
                root.target.contentY = newPos
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        onWheel: (wheel) => {
            var impulse = -(wheel.angleDelta.y / 120) * scrollTuning.impulsePerNotch
            root.wheelVelocity = Math.max(-scrollTuning.maxVelocity, Math.min(root.wheelVelocity + impulse, scrollTuning.maxVelocity))
            wheel.accepted = true
        }
    }
}
