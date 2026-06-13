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
    property string fontColorSecondary: "#cccccc"
    property string skeletonBaseColor:  "#282828"
    property int    infoLineCount:      3
    property string hoverColor:    "#333333"
    property string panelBgColor:  "#181818"
    property string cardBorderColor: "#2a2a2a"
    property string fontFamily:    ""
    property string statusText:    "Loading albums..."
    property string burgerIconName: "sort-latest-a"

    Timer { id: scrollHideTimer; interval: 600; onTriggered: root.isScrollActive = false }

    Connections {
        target: bridge
        function onAccentColorChanged(color) { root.accentColor = color }
        function onBgAlphaChanged(alpha) { root.bgAlpha = alpha }
        function onFontSizePrimaryChanged(size)    { root.fontSizePrimary = size }
        function onFontSizeSecondaryChanged(size)  { root.fontSizeSecondary = size }
        function onFontColorPrimaryChanged(color)  { root.fontColorPrimary = color }
        function onFontColorSecondaryChanged(color){ root.fontColorSecondary = color }
        function onSkeletonBaseColorChanged(color) { root.skeletonBaseColor = color }
        function onInfoLineCountChanged(count)     { root.infoLineCount = count }
        function onHoverColorChanged(color)        { root.hoverColor = color }
        function onPanelBgChanged(color)           { root.panelBgColor = color }
        function onCardBorderChanged(color)        { root.cardBorderColor = color }
        function onFontFamilyChanged(family)       { root.fontFamily = family }
        function onStatusTextChanged(text)         { root.statusText = text }
        function onBurgerIconChanged(name)         { root.burgerIconName = name }


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

    Connections {
        target: bridge.searchCtl
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
                placeholderText:   "Search albums..."

                onOpened: bridge.searchCtl.setSearchActive(true)
                onClosed: bridge.searchCtl.setSearchActive(false)
            }

            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                iconSource: "image://albumicons/" + root.burgerIconName + "_" + root.accentColor.replace("#", "")
                hoverColor: root.hoverColor
                onTriggered: (gx, gy) => bridge.showSortMenu(gx, gy)
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
        property real widthPerItem: Math.floor(availableWidth / itemsPerRow)
        
        cellWidth: widthPerItem
        cellHeight: widthPerItem + 70 
        
        model: albumModel
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: false  // wheel handled below via momentum; no touch/drag flicking on this grid

        // Momentum wheel-scroll: see MomentumScroll.qml for the model.
        MomentumScroll {
            target: grid
            minContentY: -grid.topMargin
            maxContentY: Math.max(minContentY, grid.contentHeight + grid.bottomMargin - grid.height)
        }

        Timer { interval: 200; running: true; repeat: false; onTriggered: grid.forceLayout() }

        onContentYChanged: {
            reportScroll(); root.isScrollActive = true; scrollHideTimer.restart()
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
                bridge.reportVisibleRange(startIdx, endIdx)
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

                // Full skeleton: only when slot has no data at all
                SkeletonCard {
                    visible: albumTitle === ""
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    pillCount: root.infoLineCount
                    baseColor: root.skeletonBaseColor
                    cardIndex: index
                }

                // Gray image placeholder: data known but cover not yet loaded
                Rectangle {
                    visible: isLoading && albumTitle !== ""
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
                        source: coverId ? "image://covers/" + coverId : ""
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
                    visible: albumTitle !== ""
                    z: 2

                    anchors.top: coverContainer.bottom
                    anchors.topMargin: 8
                    anchors.left: parent.left
                    anchors.right: parent.right
                    spacing: 2

                    Text {
                        width: parent.width
                        text: albumTitle
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

                    Flow {
                        width: parent.width
                        spacing: 0
                        property int albumIndex: index
                        property string primaryArtistId: {
                            var hasSep = albumArtist.indexOf(" /// ") >= 0
                                      || albumArtist.indexOf(" • ") >= 0
                                      || albumArtist.indexOf(" / ") >= 0
                            return hasSep ? "" : albumArtistId
                        }

                        Repeater {
                            model: albumArtist.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })

                            delegate: Text {
                                property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                property bool hov: false

                                text: modelData
                                color: isSep ? "#777" : (hov ? root.accentColor : root.fontColorSecondary)
                                font.pixelSize: root.fontSizeSecondary
                                // QtRendering (default) — see note on the
                                // album title Text above.
                                Rectangle {
                                    visible: !parent.isSep && parent.hov
                                    y: parent.baselineOffset + 2
                                    width: parent.paintedWidth; height: 1
                                    color: parent.color
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: !parent.isSep
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onEntered: parent.hov = true
                                    onExited:  parent.hov = false
                                    onClicked: (mouse) => {
                                        var albumIdx = parent.parent.albumIndex
                                        var aid = parent.parent.primaryArtistId
                                        grid.forceActiveFocus()
                                        grid.currentIndex = albumIdx
                                        bridge.emitArtistNameClicked(parent.text, aid)
                                        mouse.accepted = true
                                    }
                                }
                            }
                        }
                    }
                    
                    Text {
                        width: parent.width
                        text: (albumSongCount ? albumSongCount : "") + (albumSongCount && albumYear ? " · " : "") + (albumYear ? albumYear : "")
                        color: root.fontColorSecondary
                        font.pixelSize: root.fontSizeSecondary
                        elide: Text.ElideRight
                        // QtRendering (default) — see note on the album
                        // title Text above.
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
        anchors.top: toolbarRow.bottom
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
                var scrollPercentage = position / (1.0 - size);
                grid.contentY = scrollPercentage * (grid.contentHeight - grid.height);
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