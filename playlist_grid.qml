import QtQuick
import QtQuick.Controls

Rectangle {
    id: root

    property real bgAlpha: 0.3
    color: "transparent"
    radius: 5

    onActiveFocusChanged: {
        if (activeFocus) {
            grid.forceActiveFocus()
        }
    }

    Rectangle {
        width: parent.width
        height: 10
        color: root.color
        anchors.top: parent.top
    }

    property string accentColor: "#1db954"

    Connections {
        target: playlistBridge
        function onAccentColorChanged(color) { root.accentColor = color }
        function onBgAlphaChanged(alpha)     { root.bgAlpha = alpha }
    }

    
    
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: {
            playlistBridge.emitBackgroundRightClicked()
        }
    }
    
    GridView {
        id: grid
        anchors.fill: parent

        leftMargin: 20
        rightMargin: 20
        topMargin: 20
        bottomMargin: 20

        focus: true
        currentIndex: count > 0 ? 0 : -1

        MouseArea {
            anchors.fill: parent
            
            
            acceptedButtons: Qt.NoButton

            // Keep your scrolling math exactly the same!
            onWheel: (wheel) => {
                var scrollSpeed = 3.0
                var pixelScroll = (wheel.angleDelta.y / 120) * 60 * scrollSpeed
                var newY = grid.contentY - pixelScroll
                var minY = -grid.topMargin
                var maxY = Math.max(minY, grid.contentHeight + grid.bottomMargin - grid.height)
                grid.contentY = Math.max(minY, Math.min(newY, maxY))
                wheel.accepted = true
            }
        }

        onCountChanged: {
            if (count > 0 && currentIndex === -1) {
                currentIndex = 0
            }
        }

        onVisibleChanged: {
            if (visible) forceActiveFocus()
        }

        Keys.onPressed: (event) => {
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (grid.currentIndex >= 0) {
                    if (event.modifiers & Qt.ShiftModifier) {
                        playlistBridge.emitPlayClicked(Number(grid.currentIndex))
                    } else {
                        playlistBridge.emitItemClicked(Number(grid.currentIndex))
                    }
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Space) {
                if (grid.currentIndex >= 0) {
                    playlistBridge.emitPlayClicked(Number(grid.currentIndex))
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_PageDown) {
                var pgDn = Math.min(grid.currentIndex + (grid.itemsPerRow * 5), grid.count - 1)
                grid.currentIndex = pgDn
                grid.positionViewAtIndex(pgDn, GridView.Contain)
                event.accepted = true
            } else if (event.key === Qt.Key_PageUp) {
                var pgUp = Math.max(grid.currentIndex - (grid.itemsPerRow * 5), 0)
                grid.currentIndex = pgUp
                grid.positionViewAtIndex(pgUp, GridView.Contain)
                event.accepted = true
            } else if (event.text.length === 1 && event.text.trim().length === 1 &&
                       !(event.modifiers & Qt.ControlModifier) &&
                       !(event.modifiers & Qt.AltModifier)) {
                playlistBridge.forwardKeyText(event.text)
                event.accepted = true
            } else if (event.key === 47) { // "/"
                playlistBridge.forwardSlash()
                event.accepted = true
            }
        }

        property real itemGap: 10
        property real baseItemSize: 180

        property real availableWidth: width - leftMargin - rightMargin
        property int  itemsPerRow: Math.max(1, Math.floor(availableWidth / (baseItemSize + (itemGap * 2))))
        property real widthPerItem: availableWidth / itemsPerRow

        cellWidth:  widthPerItem
        cellHeight: widthPerItem + 70

        model: playlistModel
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        delegate: Item {
            width: grid.cellWidth
            height: grid.cellHeight

            Rectangle {
                id: cardRoot
                anchors.fill: parent
                anchors.margins: grid.itemGap
                color: "transparent"

                property bool isMouseHovered:    mouseArea.containsMouse || playArea.containsMouse
                property bool isKeyboardFocused: grid.activeFocus && grid.currentIndex === index
                property bool isHovered:         isMouseHovered || isKeyboardFocused

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
                        source: coverId ? "image://plcovers/" + coverId : ""
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
                        scale:   playArea.containsMouse ? 1.0 : 0.8
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                        Behavior on scale   { NumberAnimation { duration: 150 } }

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
                        text: playlistTitle
                        color: cardRoot.isHovered ? root.accentColor : "#eee"
                        font.pixelSize: 13
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    Text {
                        width: parent.width
                        text: playlistSubtitle
                        color: "#999"
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                }

                MouseArea {
                    id: mouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    z: 1
                    
                    
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    
                    onClicked: (mouse) => {
                        grid.forceActiveFocus()
                        grid.currentIndex = index
                        
                        if (mouse.button === Qt.RightButton) {
                            playlistBridge.emitItemRightClicked(Number(index))
                        } else {
                            playlistBridge.emitItemClicked(Number(index))
                        }
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
                        playlistBridge.emitPlayClicked(Number(index))
                    }
                }
            }
        }
    }

    ScrollBar {
        id: vbar
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        active: true
        width: 10

        property real fixedLength: 50
        size: height > 0 ? (fixedLength / height) : 0
        opacity: grid.contentHeight > grid.height ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 250 } }

        position: (grid.contentHeight > grid.height)
                  ? (grid.contentY / (grid.contentHeight - grid.height)) * (1.0 - size)
                  : 0

        onPositionChanged: {
            if (pressed) {
                var pct = position / (1.0 - size)
                grid.contentY = pct * (grid.contentHeight - grid.height)
            }
        }

        contentItem: Rectangle {
            radius: 5
            color: vbar.pressed ? root.accentColor : (vbar.hovered ? root.accentColor : "#333333")
        }

        background: Rectangle { color: Qt.rgba(0, 0, 0, 0.05) }
    }
}
