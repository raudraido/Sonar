import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: "transparent"
    property real bgAlpha: 0.3
    property string accentColor: "#1db954"
    property int currentIndex: grid.currentIndex
    property int gridCount: grid.count
    property int gridItemsPerRow: grid.itemsPerRow

    Connections {
        target: bridge
        function onAccentColorChanged(color) { root.accentColor = color }
        function onBgAlphaChanged(alpha)     { root.bgAlpha = alpha }
        function onTakeFocus() {
            console.log("[QML] onTakeFocus count=" + grid.count + " currentIndex=" + grid.currentIndex + " activeFocus=" + grid.activeFocus)
            if (grid.currentIndex < 0 && grid.count > 0) grid.currentIndex = 0
            grid.forceActiveFocus()
            console.log("[QML] onTakeFocus after force: activeFocus=" + grid.activeFocus)
        }
    }

    onActiveFocusChanged: {
        if (activeFocus) {
            if (grid.currentIndex < 0 && grid.count > 0) grid.currentIndex = 0
            grid.forceActiveFocus()
        }
    }

    GridView {
        id: grid
        anchors.fill: parent
        leftMargin: 20
        rightMargin: 20
        topMargin: 20
        bottomMargin: 20
        
        interactive: false 
        focus: true
        currentIndex: count > 0 ? 0 : -1

        property real itemGap: 10
        property real baseItemSize: 180
        property real availableWidth: width - leftMargin - rightMargin
        property int itemsPerRow: Math.max(1, Math.floor(availableWidth / (baseItemSize + (itemGap * 2))))
        property real widthPerItem: availableWidth / itemsPerRow
        
        cellWidth: widthPerItem
        cellHeight: widthPerItem + 70
        
        model: albumModel
        clip: true
        
        
        boundsBehavior: Flickable.StopAtBounds

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton 
            
            onWheel: (wheel) => {
                // By setting this to false, QML ignores the scroll wheel,
                // forcing the event to bubble up to your Python QScrollArea!
                wheel.accepted = false
            }
        }

        
        onCurrentIndexChanged: {
            if (bridge) bridge.emitIndexChanged(grid.currentIndex)
        }

        Keys.onPressed: (event) => {
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (grid.currentIndex >= 0) {
                    if (event.modifiers & Qt.ShiftModifier) {
                        bridge.emitPlayClicked(Number(grid.currentIndex))
                    } else {
                        bridge.emitItemClicked(Number(grid.currentIndex))
                    }
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Space) {
                if (grid.currentIndex >= 0) {
                    bridge.emitPlayClicked(Number(grid.currentIndex))
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Right) {
                grid.currentIndex = Math.min(grid.currentIndex + 1, grid.count - 1)
                event.accepted = true
            } else if (event.key === Qt.Key_Left) {
                grid.currentIndex = Math.max(grid.currentIndex - 1, 0)
                event.accepted = true
            } else if (event.key === Qt.Key_Down) {
                var nextDown = grid.currentIndex + grid.itemsPerRow
                if (nextDown < grid.count) {
                    grid.currentIndex = nextDown
                } else {
                    bridge.emitRequestFocusNext()
                }
                event.accepted = true
            } else if (event.key === Qt.Key_Up) {
                var nextUp = grid.currentIndex - grid.itemsPerRow
                if (nextUp >= 0) {
                    grid.currentIndex = nextUp
                } else {
                    bridge.emitRequestFocusPrev()
                }
                event.accepted = true
            }
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

                SkeletonCard {
                    visible: isLoading
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    pillCount: 2
                }

                Item {
                    id: coverContainer
                    visible: !isLoading
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
                        source: coverId ? "image://covers/" + coverId : ""
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
                                var ctx = getContext("2d");
                                ctx.fillStyle = "#111";
                                ctx.beginPath();
                                var triSize = parent.width / 3;
                                var cx = parent.width / 2;
                                ctx.moveTo(cx - triSize/3, cx - triSize/2);
                                ctx.lineTo(cx - triSize/3, cx + triSize/2);
                                ctx.lineTo(cx + triSize/2 + 2, cx);
                                ctx.fill();
                            }
                        }
                    }
                }

                Column {
                    visible: !isLoading
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

                    Text {
                        id: artistText
                        width: parent.width
                        text: albumArtist
                        color: artistMouseArea.containsMouse ? root.accentColor : "#ccc"
                        font.underline: artistMouseArea.containsMouse
                        font.pixelSize: 12
                        elide: Text.ElideRight

                        MouseArea {
                            id: artistMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor

                            onClicked: (mouse) => {
                                grid.forceActiveFocus()
                                grid.currentIndex = index
                                bridge.emitArtistClicked(Number(index))
                                mouse.accepted = true
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
                        bridge.emitItemClicked(Number(index))
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
                        bridge.emitPlayClicked(Number(index))
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
            width: 13
            
            property real fixedLength: 50 
            size: height > 0 ? (fixedLength / height) : 0
            
            opacity: grid.contentHeight > grid.height ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            position: (grid.contentHeight > grid.height) ? (grid.contentY / (grid.contentHeight - grid.height)) * (1.0 - size) : 0
            
            onPositionChanged: {
                if (pressed) {
                    var scrollPercentage = position / (1.0 - size);
                    grid.contentY = scrollPercentage * (grid.contentHeight - grid.height);
                }
            }
            
            contentItem: Rectangle {
                radius: 5
                color: vbar.pressed ? root.accentColor : (vbar.hovered ? root.accentColor : "#333333")
            }
            
            background: Rectangle {
                color: Qt.rgba(0, 0, 0, 0.05)
            }
        }
    }
}