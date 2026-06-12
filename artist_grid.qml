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
    property bool isScrollActive: false
    property int    fontSizePrimary:    13
    property int    fontSizeSecondary:  12
    property string fontColorPrimary:   "#eeeeee"
    property string fontColorSecondary: "#aaaaaa"
    property string skeletonBaseColor:  "#282828"
    property int    infoLineCount:      3
    property string hoverColor:    "#333333"
    property string panelBgColor:  "#181818"
    property string cardBorderColor: "#2a2a2a"
    property string fontFamily:    ""
    property string statusText:    "Loading artists..."
    property string burgerIconName: "sort-latest-a"

    Timer { id: scrollHideTimer; interval: 600; onTriggered: root.isScrollActive = false }

    Connections {
        target: artistBridge
        function onAccentColorChanged(color) { root.accentColor = color }
        function onBgAlphaChanged(alpha) { root.bgAlpha = alpha }
        function onFontSizePrimaryChanged(size)    { root.fontSizePrimary = size }
        function onFontSizeSecondaryChanged(size)  { root.fontSizeSecondary = size }
        function onFontColorPrimaryChanged(color)  { root.fontColorPrimary = color }
        function onFontColorSecondaryChanged(color){ root.fontColorSecondary = color }
        function onSkeletonBaseColorChanged(color) { root.skeletonBaseColor = color }
        function onHoverColorChanged(color)        { root.hoverColor = color }
        function onPanelBgChanged(color)           { root.panelBgColor = color }
        function onCardBorderChanged(color)        { root.cardBorderColor = color }
        function onFontFamilyChanged(family)       { root.fontFamily = family }
        function onStatusTextChanged(text)         { root.statusText = text }
        function onBurgerIconChanged(name)         { root.burgerIconName = name }

        // 👇 🟢 CATCH THE KILL SIGNAL FROM PYTHON
        function onCancelScroll() {
            if (typeof omniScroller !== "undefined" && omniScroller.isScrolling) {
                omniScroller.isScrolling = false;
                omniScroller.cursorShape = Qt.ArrowCursor;
            }
        }

        // 🟢 OMNI-SCROLLER FIX: Python pushes a pixel delta; we apply it to contentY.
        function onScrollBy(delta) {
            var minY = -grid.topMargin;
            var maxY = Math.max(minY, grid.contentHeight + grid.bottomMargin - grid.height);
            grid.contentY = Math.max(minY, Math.min(grid.contentY + delta, maxY));
        }
    }

    Connections {
        target: artistBridge.searchCtl
        function onSearchReset()         { gridSearchBar.reset() }
        function onSearchOpen()          { gridSearchBar.open() }
        function onSearchTextAppend(ch)  { gridSearchBar.appendChar(ch) }
        function onSearchTextBackspace() { gridSearchBar.backspace() }
        function onSearchClose()         { gridSearchBar.close() }
    }

    // ── TOOLBAR ──────────────────────────────────────────────────────────────
    Item {
        id: toolbarRow
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 15
        anchors.rightMargin: 10
        height: 50

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.statusText
            color: root.fontColorSecondary
            font.bold: true
            font.pixelSize: root.fontSizeSecondary
            font.family: root.fontFamily
            renderType: Text.NativeRendering
        }

        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            height: 32
            spacing: 4

            SearchBar {
                id: gridSearchBar
                anchors.verticalCenter: parent.verticalCenter
                height: 32

                accentColor:       root.accentColor
                textPrimary:       root.fontColorPrimary
                textSecondary:     root.fontColorSecondary
                panelBgColor:      root.panelBgColor
                borderColor:       root.cardBorderColor
                hoverColor:        root.hoverColor
                fontFamily:        root.fontFamily
                fontSizeSecondary: root.fontSizeSecondary
                placeholderText:   "Search artists..."

                onOpened: artistBridge.searchCtl.setSearchActive(true)
                onClosed: artistBridge.searchCtl.setSearchActive(false)
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                iconSource: "image://albumicons/" + root.burgerIconName + "_" + root.accentColor.replace("#", "")
                hoverColor: root.hoverColor
                onTriggered: (gx, gy) => artistBridge.showSortMenu(gx, gy)
            }
        }
    }

    GridView {
        id: grid
        anchors.top: toolbarRow.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        leftMargin: 4
        rightMargin: 4
        topMargin: 4
        bottomMargin: 4

        focus: true
        currentIndex: count > 0 ? 0 : -1

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton

            onWheel: (wheel) => {
                var scrollSpeed = 2.0
                var pixelScroll = (wheel.angleDelta.y / 120) * 60 * scrollSpeed
                var minY = -grid.topMargin
                var maxY = Math.max(minY, grid.contentHeight + grid.bottomMargin - grid.height)
                grid._animating = true
                grid.targetY = Math.max(minY, Math.min(grid.targetY - pixelScroll, maxY))
                grid.contentY = grid.targetY
                wheel.accepted = true
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
                        artistBridge.emitPlayClicked(Number(grid.currentIndex))
                    } else {
                        artistBridge.emitItemClicked(Number(grid.currentIndex))
                    }
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Space) {
                if (grid.currentIndex >= 0) {
                    artistBridge.emitPlayClicked(Number(grid.currentIndex))
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
        property real widthPerItem: Math.floor(availableWidth / itemsPerRow)

        cellWidth: widthPerItem
        cellHeight: widthPerItem + 80

        model: artistModel
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        property real targetY: 0
        property bool _animating: false

        // Wheel-scroll easing — SmoothedAnimation is driven by Qt's animation
        // system (tied to the render loop / real vsync), unlike a fixed-interval
        // Timer which caps motion updates at its own rate regardless of refresh rate.
        Behavior on contentY {
            enabled: grid._animating
            SmoothedAnimation {
                velocity: 1800
                onRunningChanged: if (!running) grid._animating = false
            }
        }

        Timer { interval: 200; running: true; repeat: false; onTriggered: grid.forceLayout() }

        onContentYChanged: {
            reportScroll(); root.isScrollActive = true; scrollHideTimer.restart()
            if (!grid._animating) grid.targetY = grid.contentY
        }
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
                artistBridge.reportVisibleRange(startIdx, endIdx)
            }
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

                property bool isMouseHovered: mouseArea.containsMouse || playArea.containsMouse
                property bool isKeyboardFocused: grid.activeFocus && grid.currentIndex === index
                property bool isHovered: isMouseHovered || isKeyboardFocused

                SkeletonCard {
                    visible: artistName === ""
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    pillCount: root.infoLineCount
                    baseColor: root.skeletonBaseColor
                    cardIndex: index
                }

                Rectangle {
                    visible: isLoading && artistName !== ""
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: width
                    radius: 8
                    color: root.skeletonBaseColor
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
                        color: coverId ? "transparent" : root.skeletonBaseColor
                    }

                    Image {
                        anchors.fill: parent
                        source: coverId ? "image://artistcovers/" + coverId : ""
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
                    visible: artistName !== ""
                    z: 2
                    anchors.top: coverContainer.bottom
                    anchors.topMargin: 8
                    anchors.left: parent.left
                    anchors.right: parent.right
                    spacing: 2

                    Text {
                        width: parent.width
                        text: artistName
                        color: cardRoot.isHovered ? root.accentColor : root.fontColorPrimary
                        font.pixelSize: root.fontSizePrimary
                        font.bold: true
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
                    }

                    Text {
                        width: parent.width
                        text: albumCount + " albums"
                        color: root.fontColorSecondary
                        font.pixelSize: root.fontSizeSecondary
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
                    }

                    Text {
                        width: parent.width
                        text: songCount > 0 ? (songCount + " tracks") : ""
                        color: root.fontColorSecondary
                        font.pixelSize: root.fontSizeSecondary
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
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
                        artistBridge.emitItemClicked(Number(index))
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
                        artistBridge.emitPlayClicked(Number(index))
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

        position: (grid.contentHeight > grid.height) ? (grid.contentY / (grid.contentHeight - grid.height)) * (1.0 - size) : 0

        onPositionChanged: {
            if (pressed) {
                var scrollPercentage = position / (1.0 - size)
                grid.contentY = scrollPercentage * (grid.contentHeight - grid.height)
            }
        }

        contentItem: Rectangle {
            radius: 3
            color: root.accentColor
            opacity: (vbar.pressed || vbar.hovered || root.isScrollActive) ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }

        background: Rectangle { color: "transparent" }
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