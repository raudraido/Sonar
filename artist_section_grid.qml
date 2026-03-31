import QtQuick
import QtQuick.Controls

// Non-scrolling album grid for artist detail sections.
// Height is driven by content: Python sets setFixedHeight via reportContentHeight.
Item {
    id: root

    property string accentColor: "#1db954"

    Connections {
        target: sectionBridge
        function onAccentColorChanged(color) { root.accentColor = color }
        function onSelectIndex(idx) {
            if (idx >= 0 && idx < grid.count) {
                grid.currentIndex = idx
            }
            grid.forceActiveFocus()
        }
    }

    GridView {
        id: grid
        anchors.fill: parent

        leftMargin: 20
        rightMargin: 20
        topMargin: 10
        bottomMargin: 10

        focus: true
        currentIndex: count > 0 ? 0 : -1

        interactive: false
        boundsBehavior: Flickable.StopAtBounds
        clip: false

        Keys.onPressed: (event) => {
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (grid.currentIndex >= 0) {
                    if (event.modifiers & Qt.ShiftModifier) {
                        sectionBridge.emitPlayClicked(Number(grid.currentIndex))
                    } else {
                        sectionBridge.emitItemClicked(Number(grid.currentIndex))
                    }
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Space) {
                if (grid.currentIndex >= 0) {
                    sectionBridge.emitPlayClicked(Number(grid.currentIndex))
                    event.accepted = true
                }
            }
        }

        property real itemGap: 10
        property real baseItemSize: 180

        property real availableWidth: Math.max(1, width - leftMargin - rightMargin)
        property int itemsPerRow: Math.max(1, Math.floor(availableWidth / (baseItemSize + itemGap * 2)))
        property real widthPerItem: availableWidth / itemsPerRow

        cellWidth: widthPerItem
        cellHeight: widthPerItem + 70

        model: sectionAlbumModel

        Timer { interval: 200; running: true; repeat: false; onTriggered: grid.forceLayout() }

        onContentHeightChanged: {
            if (sectionBridge) sectionBridge.reportContentHeight(grid.contentHeight + grid.topMargin + grid.bottomMargin)
        }

        onHeightChanged: {
            if (sectionBridge) sectionBridge.reportContentHeight(grid.contentHeight + grid.topMargin + grid.bottomMargin)
        }

        delegate: Item {
            width: grid.cellWidth
            height: grid.cellHeight

            Rectangle {
                id: cardRoot
                anchors.fill: parent
                anchors.margins: grid.itemGap
                color: "transparent"

                property bool isMouseHovered: mouseArea.containsMouse || playArea.containsMouse
                property bool isKeyboardFocused: grid.activeFocus && grid.currentIndex === index
                property bool isHovered: isMouseHovered || isKeyboardFocused

                Item {
                    id: coverContainer
                    width: parent.width
                    height: parent.width
                    anchors.top: parent.top

                    Rectangle {
                        anchors.fill: parent
                        radius: 8
                        color: coverId ? "transparent" : "#1a1a1a"
                    }

                    Image {
                        anchors.fill: parent
                        source: coverId ? "image://sectioncovers/" + coverId : ""
                        fillMode: Image.PreserveAspectCrop
                        mipmap: true
                        cache: false
                    }

                    Rectangle {
                        anchors.fill: parent
                        radius: 8
                        color: "#000"
                        opacity: cardRoot.isHovered ? 0.4 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }

                    Rectangle {
                        anchors.fill: parent
                        radius: 8
                        color: "transparent"
                        border.color: cardRoot.isHovered ? root.accentColor : "transparent"
                        border.width: cardRoot.isHovered ? 1 : 0
                    }

                    Rectangle {
                        id: playBtnObj
                        width: Math.min(60, parent.width / 2)
                        height: width
                        radius: width / 2
                        color: root.accentColor
                        anchors.centerIn: parent

                        opacity: playArea.containsMouse ? 1.0 : (cardRoot.isHovered ? 0.8 : 0.0)
                        scale: playArea.containsMouse ? 1.0 : 0.8
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                        Behavior on scale { NumberAnimation { duration: 150 } }

                        Canvas {
                            anchors.fill: parent
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.fillStyle = "#111"
                                ctx.beginPath()
                                var triSize = parent.width / 3
                                var cx = parent.width / 2
                                ctx.moveTo(cx - triSize/3, cx - triSize/2)
                                ctx.lineTo(cx - triSize/3, cx + triSize/2)
                                ctx.lineTo(cx + triSize/2 + 2, cx)
                                ctx.fill()
                            }
                        }
                    }
                }

                Column {
                    z: 2
                    anchors.top: coverContainer.bottom
                    anchors.topMargin: 8
                    anchors.left: parent.left
                    anchors.right: parent.right
                    spacing: 2

                    Text {
                        width: parent.width
                        text: albumTitle
                        color: cardRoot.isHovered ? root.accentColor : "#eee"
                        font.pixelSize: 13
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    Flow {
                        width: parent.width
                        spacing: 0
                        property int albumIndex: index

                        Repeater {
                            model: albumArtist.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })
                            delegate: Text {
                                property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                property bool hov: false
                                text: modelData
                                color: isSep ? "#777" : (hov ? root.accentColor : "#ccc")
                                font.underline: !isSep && hov
                                font.pixelSize: 12
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: !parent.isSep
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onEntered: parent.hov = true
                                    onExited:  parent.hov = false
                                    onClicked: (mouse) => {
                                        grid.forceActiveFocus()
                                        grid.currentIndex = parent.parent.albumIndex
                                        sectionBridge.emitArtistNameClicked(parent.text)
                                        mouse.accepted = true
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        text: albumYear
                        color: "#777"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }

                MouseArea {
                    id: mouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    z: 1
                    onClicked: {
                        grid.forceActiveFocus()
                        grid.currentIndex = index
                        sectionBridge.emitItemClicked(Number(index))
                    }
                }

                MouseArea {
                    id: playArea
                    x: coverContainer.x + playBtnObj.x
                    y: coverContainer.y + playBtnObj.y
                    width: playBtnObj.width
                    height: playBtnObj.height
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    z: 2
                    onClicked: {
                        grid.forceActiveFocus()
                        grid.currentIndex = index
                        sectionBridge.emitPlayClicked(Number(index))
                    }
                }
            }
        }
    }
}
