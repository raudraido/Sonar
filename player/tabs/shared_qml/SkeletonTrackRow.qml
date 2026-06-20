import QtQuick

// Loading placeholder for one track row in TrackListView — same shimmer
// language as SkeletonCard.qml (ShimmerSweep.qml light-band sweep), just
// laid out horizontally to mimic a row's thumbnail+title+artist and
// trailing column pills instead of a vertical grid card.
Item {
    id: skeletonRoot
    height: 58

    property string baseColor:       "#2a2a2a"
    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"

    // Card body continuation — same side fill/borders as a real track row
    Rectangle { x: 12; y: 0; width: parent.width - 24; height: parent.height; color: skeletonRoot.cardBgColor }
    Rectangle { x: 12; y: 0; width: 1; height: parent.height; color: skeletonRoot.cardBorderColor }
    Rectangle { x: parent.width - 13; y: 0; width: 1; height: parent.height; color: skeletonRoot.cardBorderColor }

    // Thumbnail + title/artist pills (mirrors the "track" column's layout)
    Row {
        x: 48; anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Rectangle {
            width: 52; height: 52; radius: 3; color: skeletonRoot.baseColor
            clip: true
            ShimmerSweep {}
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6

            Rectangle {
                width: 170; height: 11; radius: 5; color: skeletonRoot.baseColor
                clip: true
                ShimmerSweep {}
            }

            Rectangle {
                width: 110; height: 9; radius: 4; color: Qt.darker(skeletonRoot.baseColor, 1.15)
                clip: true
                ShimmerSweep {}
            }
        }
    }

    // Trailing column pills (genre/duration-ish)
    Row {
        anchors.right: parent.right; anchors.rightMargin: 36
        anchors.verticalCenter: parent.verticalCenter
        spacing: 40

        Repeater {
            model: [70, 44]
            delegate: Rectangle {
                required property int modelData
                width: modelData; height: 9; radius: 4; color: skeletonRoot.baseColor
                clip: true
                ShimmerSweep {}
            }
        }
    }
}
