import QtQuick

// Horizontal "light sweep" shimmer overlay — anchors.fill onto the skeleton
// bar/card it decorates. A soft translucent gradient band slides
// left-to-right on a loop, pauses briefly, then repeats — the classic
// skeleton-loader shimmer, much more noticeable than a uniform opacity
// pulse. Used by SkeletonCard.qml and SkeletonTrackRow.qml.
Item {
    id: sweepRoot
    anchors.fill: parent
    clip: true

    Rectangle {
        id: band
        width: parent.width * 0.6
        height: parent.height
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.22) }
            GradientStop { position: 1.0; color: "transparent" }
        }

        SequentialAnimation on x {
            running: sweepRoot.visible
            loops: Animation.Infinite
            NumberAnimation {
                from: -band.width; to: sweepRoot.width
                duration: 1100; easing.type: Easing.InOutSine
            }
            PauseAnimation { duration: 450 }
        }
    }
}
