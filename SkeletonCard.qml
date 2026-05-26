import QtQuick

Item {
    property int pillCount: 2
    property string baseColor: "#2a2a2a"

    Rectangle {
        id: coverPlaceholder
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: width
        radius: 8
        color: baseColor
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
            color: baseColor
        }

        Rectangle {
            width: parent.width * 0.5
            height: 9
            radius: 4
            color: Qt.darker(baseColor, 1.15)
            visible: pillCount >= 2
        }
    }
}
