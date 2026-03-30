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
        target: bridge
        function onAccentColorChanged(color) { root.accentColor = color }
        
        function onBgAlphaChanged(alpha) { root.bgAlpha = alpha }

        
        function onCancelScroll() {
            if (typeof omniScroller !== "undefined" && omniScroller.isScrolling) {
                omniScroller.isScrolling = false;
                omniScroller.cursorShape = Qt.ArrowCursor;
            }
        }

        
        function onScrollBy(delta) {
            var minY = -grid.topMargin;
            var maxY = Math.max(minY, grid.contentHeight + grid.bottomMargin - grid.height);
            grid.contentY = Math.max(minY, Math.min(grid.contentY + delta, maxY));
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
            
            onWheel: (wheel) => {
                // Change this to 2.0, 3.0, 4.0 etc. to dial in your perfect speed!
                var scrollSpeed = 3.0; 
                
                // Calculate the exact pixel jump
                var pixelScroll = (wheel.angleDelta.y / 120) * 60 * scrollSpeed;
                var newY = grid.contentY - pixelScroll;
                
                var minY = -grid.topMargin;
                var maxY = Math.max(minY, grid.contentHeight + grid.bottomMargin - grid.height);
                
                grid.contentY = Math.max(minY, Math.min(newY, maxY));
                
                // CRITICAL: This tells Qt "I handled the scroll, stop using the default speed!"
                wheel.accepted = true; 
            }
        }
        
        onCountChanged: {
            if (count > 0 && currentIndex === -1) {
                currentIndex = 0
            }
        }

        onVisibleChanged: {
            if (visible) {
                forceActiveFocus()
            }
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
            } else if (event.key === Qt.Key_PageDown) {
                var pgDownTarget = Math.min(grid.currentIndex + (grid.itemsPerRow * 5), grid.count - 1)
                grid.currentIndex = pgDownTarget
                grid.positionViewAtIndex(pgDownTarget, GridView.Contain)
                event.accepted = true
            } else if (event.key === Qt.Key_PageUp) {
                var pgUpTarget = Math.max(grid.currentIndex - (grid.itemsPerRow * 5), 0)
                grid.currentIndex = pgUpTarget
                grid.positionViewAtIndex(pgUpTarget, GridView.Contain)
                event.accepted = true
            }
        }
        
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

        Timer { interval: 200; running: true; repeat: false; onTriggered: grid.forceLayout() }

        onContentYChanged: reportScroll()
        onHeightChanged: reportScroll()

        function reportScroll() {
            var adjustedY = Math.max(0, grid.contentY + grid.topMargin)
            var startRow = Math.max(0, Math.floor(adjustedY / grid.cellHeight))
            
            var visibleRows = Math.ceil(grid.height / grid.cellHeight) + 4 
            
            var startIdx = startRow * grid.itemsPerRow
            var endIdx = startIdx + (visibleRows * grid.itemsPerRow)
            
            if (endIdx >= grid.count) {
                endIdx = grid.count - 1
            }
            
            if (startIdx >= 0 && endIdx >= startIdx) {
                bridge.reportVisibleRange(startIdx, endIdx)
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
                                        var albumIdx = parent.parent.albumIndex
                                        grid.forceActiveFocus()
                                        grid.currentIndex = albumIdx
                                        bridge.emitArtistNameClicked(parent.text)
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
    } // <-- End of ScrollBar

   
    MouseArea {
        id: omniScroller
        anchors.fill: grid
        acceptedButtons: Qt.MiddleButton 
        hoverEnabled: omniScroller.isScrolling 
        
        property bool isScrolling: false
        property real originY: 0
        
        Connections {
            target: grid
            function onActiveFocusChanged() {
                if (!grid.activeFocus && omniScroller.isScrolling) {
                    omniScroller.isScrolling = false;
                    omniScroller.cursorShape = Qt.ArrowCursor;
                }
            }
        }
        
        onVisibleChanged: {
            if (!omniScroller.visible && omniScroller.isScrolling) {
                omniScroller.isScrolling = false;
                omniScroller.cursorShape = Qt.ArrowCursor;
            }
        }
        
        Timer {
            interval: 7 
            running: omniScroller.isScrolling
            repeat: true
            onTriggered: {
                var delta = omniScroller.mouseY - omniScroller.originY;
                var deadzone = 15;
                if (Math.abs(delta) > deadzone) {
                    var speed = (Math.abs(delta) - deadzone) * 0.03;
                    var direction = delta > 0 ? 1 : -1;
                    
                    var newY = grid.contentY + (speed * direction);
                    var maxY = Math.max(0, grid.contentHeight - grid.height);
                    grid.contentY = Math.max(0, Math.min(newY, maxY));
                }
            }
        }

        
        onPressed: (mouse) => {
            if (mouse.button === Qt.MiddleButton) {
                if (omniScroller.isScrolling) {
                    omniScroller.isScrolling = false;
                    omniScroller.cursorShape = Qt.ArrowCursor;
                } else {
                    omniScroller.isScrolling = true;
                    omniScroller.originY = mouse.y;
                    omniScroller.cursorShape = Qt.SizeVerCursor;
                }
                mouse.accepted = true; 
            }
        }
    } // <-- End of MouseArea

}