import QtQuick

// Reusable icon button with a hover background. Emits global screen
// coordinates on click so Python can pop up a native ShadowContextMenu
// (or any other native widget) anchored to the button.
//
// Usage:
//   IconButton {
//       iconSource: "image://albumicons/burger_" + root.fontColorSecondary.replace("#", "")
//       hoverColor: root.hoverColor
//       onTriggered: (gx, gy) => bridge.sortMenuRequested(gx, gy)
//   }
Item {
    id: btn

    width: 32
    height: parent ? parent.height : 32

    property string iconSource: ""
    property string hoverColor: "#333333"
    property int    iconSize:   18
    property int    radius:     4

    signal triggered(real globalX, real globalY)

    Rectangle {
        anchors.fill: parent; radius: btn.radius
        color: hoverArea.containsMouse ? btn.hoverColor : "transparent"
    }
    Image {
        anchors.centerIn: parent
        width: btn.iconSize; height: btn.iconSize
        sourceSize: Qt.size(btn.iconSize, btn.iconSize)
        source: btn.iconSource
        cache: false; mipmap: true; smooth: true
    }
    MouseArea {
        id: hoverArea
        anchors.fill: parent; hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: (mouse) => {
            var gp = mapToGlobal(mouse.x, mouse.y)
            btn.triggered(gp.x, gp.y)
        }
    }
}
