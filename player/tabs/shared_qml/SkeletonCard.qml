import QtQuick

Item {
    id: skeletonRoot

    property int    pillCount: 2
    property string baseColor: "#2a2a2a"
    property int    cardIndex: 0

    // Widths cycle per pill so they look naturally varied
    readonly property var pillWidths: [0.72, 0.50, 0.60, 0.40, 0.65]

    Rectangle {
        id: cover
        anchors.left:  parent.left
        anchors.right: parent.right
        anchors.top:   parent.top
        height: width
        radius: 8
        color:  baseColor

        Rectangle {
            anchors.fill: parent
            radius:       parent.radius
            color:        "white"
            opacity:      0
            SequentialAnimation on opacity {
                running: skeletonRoot.visible; loops: -1
                NumberAnimation { to: 0.2; duration: 900; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.0; duration: 900; easing.type: Easing.InOutSine }
            }
        }
    }

    Column {
        anchors.top:       cover.bottom
        anchors.topMargin: 10
        anchors.left:      parent.left
        anchors.right:     parent.right
        spacing: 6

        Repeater {
            model: pillCount

            Rectangle {
                width:  parent.width * pillWidths[index % pillWidths.length]
                height: index === 0 ? 11 : 9
                radius: index === 0 ? 5 : 4
                color:  index === 0 ? baseColor : Qt.darker(baseColor, 1.15)

                Rectangle {
                    anchors.fill: parent
                    radius:       parent.radius
                    color:        "white"
                    opacity:      0
                    SequentialAnimation on opacity {
                        running: skeletonRoot.visible; loops: -1
                        NumberAnimation { to: 0.2; duration: 900; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 0.0; duration: 900; easing.type: Easing.InOutSine }
                    }
                }
            }
        }
    }
}
