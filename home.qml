import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: "transparent"
    focus: true

    // ── Theme (defaults match albums_grid.qml; overridden by homeBridge signals) ──
    property string accentColor:    "#888888"
    property string skeletonColor:  "#282828"
    property string hoverColor:     "#555555"
    property string textPrimary:    "#eeeeee"
    property string textSecondary:  "#aaaaaa"
    property int    fontSizePrimary:   13
    property int    fontSizeSecondary: 12

    // ── Refresh-spin state (driven by homeBridge signals) ──────────────────
    property bool recentSpinning: false
    property bool randomSpinning: false

    // ── Keyboard navigation state ─────────────────────────────────────────
    property string selectedRowId: ""
    property int    selectedCol:   -1

    // ── Drag-to-reorder state ──────────────────────────────────────────────
    property int  draggingIdx: -1   // index of row being dragged, -1 = none
    property real dragCurY:    0    // mouse Y in rowsColumn coordinate space

    Connections {
        target: homeBridge
        function onAccentColorChanged(c)       { root.accentColor       = c }
        function onSkeletonColorChanged(c)     { root.skeletonColor     = c }
        function onHoverColorChanged(c)        { root.hoverColor        = c }
        function onFontSizePrimaryChanged(s)   { root.fontSizePrimary   = s }
        function onFontSizeSecondaryChanged(s) { root.fontSizeSecondary = s }
        function onFontColorPrimaryChanged(c)  { root.textPrimary       = c }
        function onFontColorSecondaryChanged(c){ root.textSecondary     = c }
        function onRecentSpinChanged(s)        { root.recentSpinning    = s }
        function onRandomSpinChanged(s)        { root.randomSpinning    = s }
    }

    // ── Row order ──────────────────────────────────────────────────────────
    ListModel { id: rowOrderModel }

    Component.onCompleted: {
        var raw = (savedRowOrder || "recent,random,most_played").split(",")
        var all = ["recent", "random", "most_played"]
        var seen = {}
        for (var i = 0; i < raw.length; i++) {
            var r = raw[i].trim()
            if (all.indexOf(r) !== -1 && !seen[r]) {
                rowOrderModel.append({ rowId: r })
                seen[r] = true
            }
        }
        for (var j = 0; j < all.length; j++) {
            if (!seen[all[j]]) rowOrderModel.append({ rowId: all[j] })
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────
    function albumModelFor(rowId) {
        if (rowId === "recent")      return recentModel
        if (rowId === "random")      return randomModel
        if (rowId === "most_played") return mostPlayedModel
        return null
    }
    function rowTitleFor(rowId) {
        if (rowId === "recent")      return "Recently Added"
        if (rowId === "random")      return "Random Mix"
        if (rowId === "most_played") return "Most Played"
        return rowId
    }
    function rowHasRefresh(rowId)  { return rowId === "recent" || rowId === "random" }
    function isSpinning(rowId) {
        return (rowId === "recent" && root.recentSpinning) ||
               (rowId === "random" && root.randomSpinning)
    }

    // Calculates where a dragged row should be inserted based on mouse Y
    // (mouseY is in rowsColumn coordinate space)
    function calcDropIdx(mouseY) {
        for (var i = 0; i < rowRepeater.count; i++) {
            var item = rowRepeater.itemAt(i)
            if (!item) continue
            if (mouseY < item.y + item.height / 2) return i
        }
        return rowRepeater.count
    }

    function saveRowOrder() {
        var ids = []
        for (var i = 0; i < rowOrderModel.count; i++) ids.push(rowOrderModel.get(i).rowId)
        homeBridge.saveRowOrder(ids.join(","))
    }

    // ── Keyboard navigation helpers ────────────────────────────────────────
    function _initSelection() {
        if (rowOrderModel.count > 0) {
            selectedRowId = rowOrderModel.get(0).rowId
            selectedCol   = 0
        }
    }

    function _navigateCol(delta) {
        if (selectedRowId === "") { _initSelection(); return }
        var cnt = homeBridge.rowCount(selectedRowId)
        if (cnt === 0) return
        selectedCol = Math.max(0, Math.min(cnt - 1, selectedCol + delta))
    }

    function _navigateRow(delta) {
        if (selectedRowId === "") { _initSelection(); return }
        var curIdx = -1
        for (var i = 0; i < rowOrderModel.count; i++) {
            if (rowOrderModel.get(i).rowId === selectedRowId) { curIdx = i; break }
        }
        if (curIdx < 0) { _initSelection(); return }
        var ni = Math.max(0, Math.min(rowOrderModel.count - 1, curIdx + delta))
        if (ni === curIdx) return
        selectedRowId = rowOrderModel.get(ni).rowId
        var cnt = homeBridge.rowCount(selectedRowId)
        selectedCol = cnt > 0 ? Math.min(selectedCol, cnt - 1) : 0
    }

    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Left) {
            _navigateCol(-1); event.accepted = true
        } else if (event.key === Qt.Key_Right) {
            _navigateCol(1); event.accepted = true
        } else if (event.key === Qt.Key_Up) {
            _navigateRow(-1); event.accepted = true
        } else if (event.key === Qt.Key_Down) {
            _navigateRow(1); event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (selectedRowId !== "" && selectedCol >= 0)
                homeBridge.albumClicked(selectedRowId, selectedCol)
            event.accepted = true
        } else if (event.key === Qt.Key_Space) {
            if (selectedRowId !== "" && selectedCol >= 0)
                homeBridge.playClicked(selectedRowId, selectedCol)
            event.accepted = true
        }
    }

    // ── Vertical scroll ────────────────────────────────────────────────────
    Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: rowsColumn.y + rowsColumn.implicitHeight + 60
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        interactive: root.draggingIdx === -1  // freeze scroll while reordering

        // Prevent Flickable from consuming arrow keys — delegate to root nav
        Keys.forwardTo: [root]

        property bool isScrollActive: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: scroller.isScrollActive = false }
        onContentYChanged: {
            isScrollActive = true; scrollHideTimer.restart()
            if (!scroller._animating) scroller.targetY = scroller.contentY
        }

        property real targetY: 0
        property bool _animating: false

        // Wheel-scroll easing — SmoothedAnimation is driven by Qt's animation
        // system (tied to the render loop / real vsync), unlike a stepped
        // direct contentY assignment which jumps instantly.
        Behavior on contentY {
            enabled: scroller._animating
            SmoothedAnimation {
                velocity: 1800
                onRunningChanged: if (!running) scroller._animating = false
            }
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            onWheel: wheel => {
                var delta = -(wheel.angleDelta.y / 120) * 60
                var maxY  = Math.max(0, scroller.contentHeight - scroller.height)
                scroller._animating = true
                scroller.targetY = Math.max(0, Math.min(scroller.targetY + delta, maxY))
                scroller.contentY = scroller.targetY
                wheel.accepted = true
            }
        }

        Column {
            id: rowsColumn
            y: 20
            x: 4
            width: scroller.width - 8
            spacing: 16

            Repeater {
                id: rowRepeater
                model: rowOrderModel

                delegate: Item {
                    id: rowItem
                    width: parent.width

                    property string rowId:      model.rowId
                    property bool   hasRefresh: root.rowHasRefresh(rowId)
                    property bool   spinning:   root.isSpinning(rowId)
                    property bool   isDragging: root.draggingIdx === index

                    opacity: isDragging ? 0.30 : 1.0
                    Behavior on opacity { NumberAnimation { duration: 120 } }

                    // Scroll carousel + Flickable when keyboard selection moves to this row
                    Connections {
                        target: root
                        function onSelectedColChanged() {
                            if (root.selectedRowId === rowItem.rowId && root.selectedCol >= 0)
                                carousel.positionViewAtIndex(root.selectedCol, ListView.Contain)
                        }
                        function onSelectedRowIdChanged() {
                            if (root.selectedRowId !== rowItem.rowId) return
                            if (root.selectedCol >= 0)
                                carousel.positionViewAtIndex(root.selectedCol, ListView.Contain)
                            var rTop = rowsColumn.y + rowItem.y
                            var rBot = rTop + rowItem.height
                            if (rTop < scroller.contentY)
                                scroller.contentY = rTop
                            else if (rBot > scroller.contentY + scroller.height)
                                scroller.contentY = rBot - scroller.height
                        }
                    }

                    // Responsive column count (Feishin-style breakpoints)
                    readonly property int nCols: {
                        if (width >= 1440) return 8
                        if (width >= 1280) return 7
                        if (width >= 1152) return 6
                        if (width >= 960)  return 5
                        if (width >= 720)  return 4
                        if (width >= 520)  return 3
                        return 2
                    }
                    readonly property real cellW: Math.floor(width / nCols)
                    readonly property real cellH: cellW + 70

                    height: 36 + 10 + cellH

                    // ── Header ─────────────────────────────────────────────
                    Item {
                        id: rowHeader
                        width: parent.width
                        height: 36

                        // Grip handle — 3 horizontal lines, visible on hover
                        Item {
                            id: gripHandle
                            width: 22; height: parent.height
                            visible: headerHover.containsMouse

                            Column {
                                anchors.centerIn: parent
                                spacing: 4
                                Repeater {
                                    model: 3
                                    Rectangle {
                                        width: 14; height: 2
                                        color: root.accentColor
                                        radius: 1
                                    }
                                }
                            }

                            // Drag interaction
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor

                                onPressed: {
                                    root.draggingIdx = index
                                    root.dragCurY    = mapToItem(rowsColumn, mouseX, mouseY).y
                                }
                                onPositionChanged: {
                                    if (root.draggingIdx !== -1)
                                        root.dragCurY = mapToItem(rowsColumn, mouseX, mouseY).y
                                }
                                onReleased: {
                                    if (root.draggingIdx !== -1) {
                                        var from = root.draggingIdx
                                        var to   = root.calcDropIdx(root.dragCurY)
                                        root.draggingIdx = -1
                                        var adj = to > from ? to - 1 : to
                                        if (from !== adj && adj >= 0 && adj < rowOrderModel.count) {
                                            rowOrderModel.move(from, adj, 1)
                                            root.saveRowOrder()
                                        }
                                    }
                                }
                            }
                        }

                        // Hover tracker for grip visibility
                        MouseArea {
                            id: headerHover
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.NoButton
                        }

                        // Title
                        Text {
                            text: root.rowTitleFor(rowId)
                            color: root.textPrimary
                            font.pixelSize: 15
                            font.bold: true
                            anchors.left: gripHandle.right
                            anchors.leftMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            renderType: Text.NativeRendering
                        }

                        // Right controls: refresh + arrows
                        Row {
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            height: 31
                            spacing: 4

                            // Refresh button
                            Item {
                                visible: rowItem.hasRefresh
                                width: 31; height: 31

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: root.hoverColor
                                    opacity: refreshHover.containsMouse && !rowItem.spinning ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }

                                Image {
                                    anchors.centerIn: parent
                                    width: 19; height: 19
                                    source: "image://homeicons/sub_refresh_" + root.accentColor.replace("#", "")
                                    cache: false
                                    mipmap: true
                                    smooth: true
                                    transformOrigin: Item.Center

                                    NumberAnimation on rotation {
                                        running: rowItem.spinning
                                        loops:   Animation.Infinite
                                        from: 0; to: 360
                                        duration: 800
                                    }
                                }

                                MouseArea {
                                    id: refreshHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    enabled: !rowItem.spinning
                                    onClicked: {
                                        if (rowId === "recent")      homeBridge.refreshRecent()
                                        else if (rowId === "random") homeBridge.refreshRandom()
                                    }
                                }
                            }

                            // Left arrow
                            Item {
                                width: 29; height: 31
                                property bool canPage: carousel.contentX > 0

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: root.hoverColor
                                    opacity: leftArrowHover.containsMouse && parent.canPage ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }

                                Image {
                                    anchors.centerIn: parent
                                    width: 13; height: 13
                                    source: parent.canPage
                                        ? "image://homeicons/home_back_" + root.accentColor.replace("#", "")
                                        : "image://homeicons/home_back_444444"
                                    cache: false
                                    mipmap: true
                                    smooth: true
                                }
                                MouseArea {
                                    id: leftArrowHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: parent.canPage ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: {
                                        var pageW = rowItem.nCols * rowItem.cellW
                                        var cur   = Math.round(carousel.contentX / pageW)
                                        carousel.contentX = Math.max(0, (cur - 1) * pageW)
                                    }
                                }
                            }

                            // Right arrow
                            Item {
                                width: 29; height: 31
                                property bool canPage: carousel.contentX + carousel.width < carousel.contentWidth - 1

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: root.hoverColor
                                    opacity: rightArrowHover.containsMouse && parent.canPage ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }

                                Image {
                                    anchors.centerIn: parent
                                    width: 13; height: 13
                                    source: parent.canPage
                                        ? "image://homeicons/home_next_" + root.accentColor.replace("#", "")
                                        : "image://homeicons/home_next_444444"
                                    cache: false
                                    mipmap: true
                                    smooth: true
                                }
                                MouseArea {
                                    id: rightArrowHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: parent.canPage ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: {
                                        var pageW = rowItem.nCols * rowItem.cellW
                                        var cur   = Math.round(carousel.contentX / pageW)
                                        var maxX  = Math.max(0, carousel.contentWidth - carousel.width)
                                        carousel.contentX = Math.min(maxX, (cur + 1) * pageW)
                                    }
                                }
                            }
                        }
                    }

                    // ── Horizontal album carousel ───────────────────────────
                    Item {
                        anchors.top:       rowHeader.bottom
                        anchors.topMargin: 10
                        width:  parent.width
                        height: rowItem.cellH
                        clip:   true

                        ListView {
                            id: carousel
                            anchors.fill: parent
                            orientation:  ListView.Horizontal
                            interactive:  false   // arrows drive scrolling
                            boundsBehavior: Flickable.StopAtBounds
                            clip: true
                            spacing: 0

                            model: root.albumModelFor(rowId)

                            // Smooth page transitions
                            Behavior on contentX {
                                SmoothedAnimation { velocity: 3500; maximumEasingTime: 200 }
                            }

                            property string rowId: rowItem.rowId

                            // Trigger load-more when 80% through
                            onContentXChanged: {
                                if (count > 0 && !_loadingMore &&
                                    contentX + width >= contentWidth * 0.8) {
                                    _loadingMore = true
                                    homeBridge.loadMore(carousel.rowId, count)
                                }
                            }
                            property bool _loadingMore: false
                            onCountChanged: { _loadingMore = false }

                            // ── Album card delegate ─────────────────────────
                            delegate: Item {
                                width:  rowItem.cellW
                                height: rowItem.cellH

                                // ── Skeleton card ───────────────────────────
                                SkeletonCard {
                                    visible:    isLoading
                                    anchors.fill: parent
                                    anchors.margins: 6
                                    pillCount:  2
                                    baseColor:  root.skeletonColor
                                    cardIndex:  index
                                }

                                // ── Real card ───────────────────────────────
                                Item {
                                    id: card
                                    visible: !isLoading
                                    anchors.fill:        parent
                                    anchors.leftMargin:  6
                                    anchors.rightMargin: 6
                                    anchors.topMargin:   4
                                    anchors.bottomMargin:4

                                    property bool hov: mainArea.containsMouse ||
                                                       playArea.containsMouse
                                    property bool isSelected: !isLoading &&
                                        carousel.rowId === root.selectedRowId &&
                                        index === root.selectedCol

                                    // Cover area (square)
                                    Item {
                                        id: coverArea
                                        width:  parent.width
                                        height: parent.width
                                        anchors.top: parent.top

                                        // Placeholder when no cover yet
                                        Rectangle {
                                            anchors.fill: parent
                                            radius: 8
                                            color:  root.skeletonColor
                                            visible: coverId === ""
                                        }

                                        Image {
                                            visible:  coverId !== ""
                                            anchors.fill: parent
                                            source:   coverId !== "" ? "image://homecovers/" + coverId : ""
                                            fillMode: Image.PreserveAspectCrop
                                            cache:    false
                                            layer.enabled: true
                                            layer.effect: null
                                        }

                                        // Hover dim overlay
                                        Rectangle {
                                            anchors.fill: parent
                                            radius:  8
                                            color:   "#000"
                                            opacity: card.hov ? 0.40 : 0.0
                                            Behavior on opacity { NumberAnimation { duration: 150 } }
                                        }

                                        // Accent border on hover / keyboard selection
                                        Rectangle {
                                            anchors.fill: parent
                                            radius:       8
                                            color:        "transparent"
                                            border.color: (card.hov || card.isSelected) ? root.accentColor : "transparent"
                                            border.width: card.isSelected ? 2 : 1
                                        }

                                        // Play button circle
                                        Rectangle {
                                            id: playBtn
                                            width:  Math.min(52, parent.width / 2.2)
                                            height: width
                                            radius: width / 2
                                            color:  root.accentColor
                                            anchors.centerIn: parent

                                            opacity: playArea.containsMouse ? 1.0
                                                   : card.hov              ? 0.80
                                                   : 0.0
                                            scale:   playArea.containsMouse ? 1.0 : 0.82
                                            Behavior on opacity { NumberAnimation { duration: 150 } }
                                            Behavior on scale   { NumberAnimation { duration: 150 } }

                                            // Triangle play icon
                                            Canvas {
                                                anchors.fill: parent
                                                onPaint: {
                                                    var ctx = getContext("2d")
                                                    ctx.clearRect(0, 0, width, height)
                                                    ctx.fillStyle = "#111"
                                                    var s  = width / 3
                                                    var cx = width / 2
                                                    ctx.beginPath()
                                                    ctx.moveTo(cx - s / 3, cx - s / 2)
                                                    ctx.lineTo(cx - s / 3, cx + s / 2)
                                                    ctx.lineTo(cx + s / 2 + 2, cx)
                                                    ctx.fill()
                                                }
                                            }
                                        }
                                    }

                                    // Text info below cover
                                    Column {
                                        visible: albumTitle !== ""
                                        z: 2
                                        anchors.top:       coverArea.bottom
                                        anchors.topMargin: 8
                                        anchors.left:  parent.left
                                        anchors.right: parent.right
                                        spacing: 2

                                        Text {
                                            width: parent.width
                                            text:  albumTitle
                                            color: (card.hov || card.isSelected) ? root.accentColor : root.textPrimary
                                            font.pixelSize: root.fontSizePrimary
                                            font.bold:      true
                                            elide: Text.ElideRight
                                            renderType: Text.NativeRendering
                                        }

                                        Text {
                                            id: artistText
                                            width: parent.width
                                            property bool hov: false
                                            text:  albumArtist
                                            color: hov ? root.accentColor : root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            elide: Text.ElideRight
                                            Rectangle {
                                                visible: parent.hov
                                                y: parent.baselineOffset + 2
                                                width: parent.paintedWidth; height: 1
                                                color: parent.color
                                            }
                                            renderType: Text.NativeRendering

                                            MouseArea {
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape:  Qt.PointingHandCursor
                                                z: 4
                                                onEntered: parent.hov = true
                                                onExited:  parent.hov = false
                                                onClicked: mouse => {
                                                    homeBridge.artistNameClicked(albumArtist, albumArtistId)
                                                    mouse.accepted = true
                                                }
                                            }
                                        }

                                        Text {
                                            width: parent.width
                                            text:  (albumSongCount ? albumSongCount : "") +
                                                   (albumSongCount && albumYear ? " · " : "") +
                                                   (albumYear ? albumYear : "")
                                            color: root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            elide: Text.ElideRight
                                            renderType: Text.NativeRendering
                                        }
                                    }

                                    // Main click area (whole card, z:1)
                                    MouseArea {
                                        id: mainArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape:  Qt.PointingHandCursor
                                        z: 1
                                        onClicked: {
                                            root.selectedRowId = carousel.rowId
                                            root.selectedCol   = index
                                            root.forceActiveFocus()
                                            homeBridge.albumClicked(carousel.rowId, index)
                                        }
                                    }

                                    // Play-button click area (z:3, above mainArea)
                                    MouseArea {
                                        id: playArea
                                        x:      coverArea.x + playBtn.x
                                        y:      coverArea.y + playBtn.y
                                        width:  playBtn.width
                                        height: playBtn.height
                                        hoverEnabled: true
                                        cursorShape:  Qt.PointingHandCursor
                                        z: 3
                                        onClicked: mouse => {
                                            homeBridge.playClicked(carousel.rowId, index)
                                            mouse.accepted = true
                                        }
                                    }
                                }
                            } // delegate
                        } // ListView
                    } // clip Item
                } // row Item delegate
            } // Repeater
        } // Column
    } // Flickable

    ScrollBar {
        id: vbar
        anchors.right:  parent.right
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        width: 10

        opacity: scroller.contentHeight > scroller.height ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 250 } }

        property real fixedLength: 50
        size:     scroller.height > 0 ? (fixedLength / scroller.height) : 0
        position: (scroller.contentHeight > scroller.height)
                  ? (scroller.contentY / (scroller.contentHeight - scroller.height)) * (1.0 - size)
                  : 0

        onPositionChanged: {
            if (pressed) {
                var pct = position / (1.0 - size)
                scroller.contentY = pct * (scroller.contentHeight - scroller.height)
            }
        }

        contentItem: Rectangle {
            radius: 3
            color:  root.accentColor
            opacity: vbar.pressed || vbar.hovered || scroller.isScrollActive ? 0.9 : 0.0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }
        background: Rectangle { color: "transparent" }
    }

    // ── Drop-position indicator shown while dragging ────────────────────────
    Rectangle {
        id: dropIndicator
        visible:  root.draggingIdx !== -1
        color:    root.accentColor
        height:   3
        radius:   1
        x:        10
        width:    parent.width - 20
        z:        200

        y: {
            if (root.draggingIdx === -1) return 0
            var di = root.calcDropIdx(root.dragCurY)
            var yInCol
            if (di <= 0) {
                var first = rowRepeater.itemAt(0)
                yInCol = first ? first.y - 3 : 0
            } else if (di >= rowRepeater.count) {
                var last = rowRepeater.itemAt(rowRepeater.count - 1)
                yInCol = last ? last.y + last.height + 1 : 0
            } else {
                var prev = rowRepeater.itemAt(di - 1)
                var next = rowRepeater.itemAt(di)
                yInCol = prev && next
                    ? (prev.y + prev.height + next.y) / 2
                    : 0
            }
            // Convert from rowsColumn coords to root coords
            return rowsColumn.y + yInCol - scroller.contentY
        }
    }

}

