import QtQuick

Item {
    property int pillCount: 2

    Rectangle {
        id: coverPlaceholder
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: width
        radius: 8
        color: "#2a2a2a"
    }

    Column {
        anchors.top: coverPlaceholder.bottom
        anchors.topMargin: 10
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: 6

        Rectangle {
            width: parent.width * 0.75
            height: 11
            radius: 5
            color: "#2a2a2a"
        }

        Rectangle {
            width: parent.width * 0.5
            height: 9
            radius: 4
            color: "#222"
            visible: pillCount >= 2
        }
    }
}
