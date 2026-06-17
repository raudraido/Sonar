import QtQuick
import QtQuick.Controls
import "../shared_qml"

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

    property string accentColor:        "#1db954"
    property string fontColorPrimary:   "#eeeeee"
    property string fontColorSecondary: "#999999"
    property int    fontSizePrimary:    13
    property int    fontSizeSecondary:  12
    property bool isScrollActive: false
    property bool dimmed: false

    Timer { id: scrollHideTimer; interval: 600; onTriggered: root.isScrollActive = false }

    Connections {
        target: playlistBridge
        function onAccentColorChanged(color)         { root.accentColor = color }
        function onBgAlphaChanged(alpha)             { root.bgAlpha = alpha }
        function onFontColorPrimaryChanged(color)    { root.fontColorPrimary = color }
        function onFontColorSecondaryChanged(color)  { root.fontColorSecondary = color }
        function onFontSizePrimaryChanged(size)      { root.fontSizePrimary = size }
        function onFontSizeSecondaryChanged(size)    { root.fontSizeSecondary = size }
        function onDimChanged(value)                 { root.dimmed = value }
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
        pixelAligned: true
        leftMargin: 4
        rightMargin: 4
        topMargin: 4
        bottomMargin: 4

        focus: true
        currentIndex: count > 0 ? 0 : -1

        // Momentum wheel-scroll: see MomentumScroll.qml for the model.
        MomentumScroll {
            target: grid
            minContentY: -grid.topMargin
            maxContentY: Math.max(minContentY, grid.contentHeight + grid.bottomMargin - grid.height)
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
            } else if (event.key === Qt.Key_Right) {
                var nextR = Math.min(grid.currentIndex + 1, grid.count - 1)
                grid.currentIndex = nextR
                grid.positionViewAtIndex(nextR, GridView.Contain)
                event.accepted = true
            } else if (event.key === Qt.Key_Left) {
                var nextL = Math.max(grid.currentIndex - 1, 0)
                grid.currentIndex = nextL
                grid.positionViewAtIndex(nextL, GridView.Contain)
                event.accepted = true
            } else if (event.key === Qt.Key_Down) {
                var nextD = Math.min(grid.currentIndex + grid.itemsPerRow, grid.count - 1)
                grid.currentIndex = nextD
                grid.positionViewAtIndex(nextD, GridView.Contain)
                event.accepted = true
            } else if (event.key === Qt.Key_Up) {
                var nextU = Math.max(grid.currentIndex - grid.itemsPerRow, 0)
                grid.currentIndex = nextU
                grid.positionViewAtIndex(nextU, GridView.Contain)
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
        property real widthPerItem: Math.floor(availableWidth / itemsPerRow)

        cellWidth:  widthPerItem
        cellHeight: widthPerItem + 70

        model: playlistModel
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: false  // wheel handled above via momentum; no touch/drag flicking on this grid
        onContentYChanged: {
            root.isScrollActive = true; scrollHideTimer.restart()
        }

        delegate: Item {
            width: grid.cellWidth
            height: grid.cellHeight

            Rectangle {
                id: cardRoot
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 6
                anchors.topMargin: 4
                anchors.bottomMargin: 4
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
                        color: cardRoot.isHovered ? root.accentColor : root.fontColorPrimary
                        font.pixelSize: root.fontSizePrimary
                        font.bold: true
                        elide: Text.ElideRight
                        // QtRendering (default), not NativeRendering: native
                        // rendering snaps glyphs to integer pixels
                        // independently of contentY, causing a 1px pop
                        // relative to the (sub-pixel) cover image during the
                        // slow tail of a momentum scroll.
                    }

                    Text {
                        width: parent.width
                        text: playlistSubtitle
                        color: root.fontColorSecondary
                        font.pixelSize: root.fontSizeSecondary
                        elide: Text.ElideRight
                        // QtRendering (default) — see note above.
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
        width: 6

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
            radius: 3
            color: root.accentColor
            opacity: (vbar.pressed || vbar.hovered || root.isScrollActive) ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }

        background: Rectangle { color: "transparent" }
    }
}
