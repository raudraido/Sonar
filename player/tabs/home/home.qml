import QtQuick
import QtQuick.Controls
import "../shared_qml"

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
        pixelAligned: true
        clip: true
        interactive: false  // wheel handled below via momentum; no touch/drag flicking on this view

        // Prevent Flickable from consuming arrow keys — delegate to root nav
        Keys.forwardTo: [root]

        property bool isScrollActive: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: scroller.isScrollActive = false }
        onContentYChanged: {
            isScrollActive = true; scrollHideTimer.restart()
        }

        // Momentum wheel-scroll: see MomentumScroll.qml for the model.
        MomentumScroll {
            target: scroller
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

                    // ── Drag grip handle — injected into Carousel's header ──
                    property Component gripComponent: Component {
                        Item {
                            id: gripHandle
                            width: 22; height: 36
                            visible: rowCarousel.headerHovered

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
                    }

                    Carousel {
                        id: rowCarousel
                        width: parent.width
                        title: root.rowTitleFor(rowId)

                        accentColor:       root.accentColor
                        skeletonColor:     root.skeletonColor
                        hoverColor:        root.hoverColor
                        textPrimary:       root.textPrimary
                        textSecondary:     root.textSecondary
                        fontSizePrimary:   root.fontSizePrimary
                        fontSizeSecondary: root.fontSizeSecondary

                        model:         root.albumModelFor(rowId)
                        showRefresh:   rowItem.hasRefresh
                        spinning:      rowItem.spinning
                        leadingItem:   rowItem.gripComponent
                        selectedIndex: (root.selectedRowId === rowId) ? root.selectedCol : -1

                        onRefreshClicked: {
                            if (rowId === "recent")      homeBridge.refreshRecent()
                            else if (rowId === "random") homeBridge.refreshRandom()
                        }
                        onCardClicked: (index) => {
                            root.selectedRowId = rowId
                            root.selectedCol   = index
                            root.forceActiveFocus()
                            homeBridge.albumClicked(rowId, index)
                        }
                        onPlayClicked: (index) => homeBridge.playClicked(rowId, index)
                        onArtistSubtextClicked: (name, artistId) => homeBridge.artistNameClicked(name, artistId)
                        onLoadMoreRequested: (count) => homeBridge.loadMore(rowId, count)
                    }

                    // Keep keyboard-nav carousel paging in sync (Carousel
                    // pages itself via arrows/contentX, but keyboard
                    // selection needs to scroll it programmatically too).
                    Connections {
                        target: root
                        function _snapToCol(col) {
                            rowCarousel.scrollToIndex(col)
                        }
                        function onSelectedColChanged() {
                            if (root.selectedRowId === rowItem.rowId && root.selectedCol >= 0)
                                _snapToCol(root.selectedCol)
                        }
                        function onSelectedRowIdChanged() {
                            if (root.selectedRowId !== rowItem.rowId) return
                            if (root.selectedCol >= 0)
                                _snapToCol(root.selectedCol)
                            var rTop = rowsColumn.y + rowItem.y
                            var rBot = rTop + rowItem.height
                            if (rTop < scroller.contentY)
                                scroller.contentY = rTop
                            else if (rBot > scroller.contentY + scroller.height)
                                scroller.contentY = rBot - scroller.height
                        }
                    }
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

