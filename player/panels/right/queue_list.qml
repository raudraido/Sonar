import QtQuick
import QtQuick.Controls
import "../../tabs/shared_qml"

// Queue panel track list — flat, single-column row (no album art/columns,
// unlike TrackListView.qml which backs album/playlist/track pages). Reuses
// the same momentum-scroll, drag-reorder, and artist-token patterns
// established there, just simplified to this panel's compact row shape.
Rectangle {
    id: root
    color: root.panelBgColor
    focus: true

    // ── Theme (pushed from queueBridge) ─────────────────────────────────────
    property string accentColor:    "#cccccc"
    property string hoverColor:     "#555555"
    property string textPrimary:    "#dddddd"
    property string textSecondary:  "#777777"
    property string panelBgColor:   "#0e0e0e"
    property int    fontSizePrimary:   14
    property int    fontSizeSecondary: 12

    // ── List state ───────────────────────────────────────────────────────────
    property int  currentIndex: -1
    property bool isPlaying:    false

    readonly property int rowH: 53
    readonly property int numW: 32
    readonly property int favW: 28
    readonly property int durW: 50

    // ── Drag-reorder state ───────────────────────────────────────────────────
    property int    _dragFromIdx:    -1
    property int    _dragToIdx:      -1
    property string _dragGhostTitle: ""
    property string _dragGhostArt:   ""
    property real   _dragGhostY:     0
    readonly property bool _isDragging: _dragFromIdx >= 0

    Connections {
        target: queueBridge
        function onAccentColorChanged(c)        { root.accentColor       = c }
        function onHoverColorChanged(c)         { root.hoverColor        = c }
        function onPanelBgChanged(c)            { root.panelBgColor      = c }
        function onFontColorPrimaryChanged(c)   { root.textPrimary       = c }
        function onFontColorSecondaryChanged(c) { root.textSecondary     = c }
        function onFontSizePrimaryChanged(s)    { root.fontSizePrimary   = s }
        function onFontSizeSecondaryChanged(s)  { root.fontSizeSecondary = s }
        function onCurrentIndexChanged(i)       { root.currentIndex      = i }
        function onIsPlayingChanged(p)          { root.isPlaying         = p }
        function onScrollToIndexRequested(i) {
            if (i >= 0) queueList.positionViewAtIndex(i, ListView.Center)
        }
    }

    // ── Freestanding scrollbar ───────────────────────────────────────────────
    ScrollBar {
        id: vbar
        anchors.right:  parent.right
        anchors.top:    queueList.top
        anchors.bottom: queueList.bottom
        width: 8
        z: 10

        opacity: queueList.contentHeight > queueList.height ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 250 } }

        property real fixedLength: 50
        size:     queueList.height > 0 ? (fixedLength / queueList.height) : 0
        position: (queueList.contentHeight > queueList.height)
                  ? ((queueList.contentY - queueList.originY) / (queueList.contentHeight - queueList.height)) * (1.0 - size)
                  : 0
        onPositionChanged: {
            if (pressed) {
                var pct = position / (1.0 - size)
                queueList.contentY = queueList.originY + pct * (queueList.contentHeight - queueList.height)
            }
        }
        contentItem: Rectangle {
            radius: 3; color: root.accentColor
            opacity: vbar.pressed || vbar.hovered || queueList.isScrollActive ? 0.9 : 0.0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }
        background: Rectangle { color: "transparent" }
    }

    // ── Main scrolling view ──────────────────────────────────────────────────
    ListView {
        id: queueList
        anchors.fill: parent
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: false
        clip: true
        pixelAligned: true
        spacing: 0
        cacheBuffer: 800
        model: queueModel

        property bool isScrollActive: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: queueList.isScrollActive = false }
        onContentYChanged: { isScrollActive = true; scrollHideTimer.restart() }

        MomentumScroll {
            target: queueList
            minContentY: queueList.originY
            maxContentY: queueList.originY + Math.max(0, queueList.contentHeight - queueList.height)
        }

        delegate: Item {
            id: trackRow
            width: queueList.width
            height: root.rowH

            property int    rowIdx:   index
            property string trkId:    model.trackId      || ""
            property string trkTitle: model.trackTitle   || ""
            property string artName:  model.artistName   || ""
            property string durStr:   model.durationStr  || ""
            property bool   isFav:    model.isFavorite

            readonly property bool isCurrent: rowIdx === root.currentIndex
            readonly property bool isPast:    root.currentIndex >= 0 && rowIdx < root.currentIndex
            readonly property bool isPlayingRow: isCurrent && root.isPlaying

            property bool rowHov: false
            opacity: root._isDragging && root._dragFromIdx === rowIdx ? 0.3 : 1.0
            Behavior on opacity { NumberAnimation { duration: 100 } }

            Rectangle {
                x: 8; y: 1
                width: parent.width - 16; height: parent.height - 2
                radius: 6
                color: trackRow.isCurrent
                    ? Qt.rgba(Qt.color(root.accentColor).r, Qt.color(root.accentColor).g, Qt.color(root.accentColor).b, 0.15)
                    : root.hoverColor
                opacity: trackRow.isCurrent ? 1.0 : (trackRow.rowHov ? 1.0 : 0.0)
                Behavior on opacity { NumberAnimation { duration: 120 } }
            }

            // # / playing bars / drag grip
            Item {
                id: numCol
                x: 6; width: root.numW; height: parent.height

                property bool _showGrip: !trackRow.isPlayingRow &&
                    (trackRow.rowHov || (root._isDragging && root._dragFromIdx === trackRow.rowIdx))

                Text {
                    visible: !trackRow.isPlayingRow && !numCol._showGrip
                    anchors.centerIn: parent
                    text: String(trackRow.rowIdx + 1)
                    color: trackRow.isCurrent ? root.accentColor : root.textSecondary
                    font.pixelSize: 11; font.bold: trackRow.isCurrent
                    opacity: trackRow.isCurrent ? 1.0 : (trackRow.isPast ? 0.5 : 1.0)
                }
                Row {
                    visible: numCol._showGrip
                    anchors.centerIn: parent; spacing: 3
                    Repeater {
                        model: 2
                        delegate: Column {
                            spacing: 3
                            Repeater {
                                model: 3
                                delegate: Rectangle {
                                    width: 2.5; height: 2.5; radius: 1.25
                                    color: root._isDragging && root._dragFromIdx === trackRow.rowIdx ? root.accentColor : root.textSecondary
                                    opacity: 0.7
                                }
                            }
                        }
                    }
                }
                DragHandler {
                    target: null; enabled: !trackRow.isPlayingRow; dragThreshold: 6
                    property int  _startIdx:    -1
                    onActiveChanged: {
                        if (active) {
                            _startIdx = trackRow.rowIdx
                            root._dragFromIdx = _startIdx; root._dragToIdx = _startIdx
                            root._dragGhostTitle = trackRow.trkTitle; root._dragGhostArt = trackRow.artName
                            root._dragGhostY = centroid.scenePosition.y
                        } else if (root._dragFromIdx === _startIdx) {
                            // Clear drag state BEFORE calling into Python — reorderTrack()
                            // resets the model synchronously, which destroys this very
                            // delegate (and its DragHandler) mid-handler. Any code after
                            // that call belongs to a being-destroyed object and may never
                            // run, which left the ghost overlay stuck visible forever.
                            var from = root._dragFromIdx, to = root._dragToIdx
                            root._dragFromIdx = -1; root._dragToIdx = -1; _startIdx = -1
                            if (from !== to)
                                queueBridge.reorderTrack(from, to)
                        }
                    }
                    onCentroidChanged: {
                        if (!active || root._dragFromIdx !== _startIdx) return
                        var sy = centroid.scenePosition.y
                        var idx = queueList.indexAt(0, sy + queueList.contentY)
                        if (idx >= 0) root._dragToIdx = idx
                        root._dragGhostY = sy
                    }
                }
                HoverHandler { cursorShape: trackRow.isPlayingRow ? Qt.ArrowCursor : (root._isDragging ? Qt.ClosedHandCursor : Qt.OpenHandCursor) }

                Row {
                    visible: trackRow.isPlayingRow; anchors.centerIn: parent; spacing: 3
                    Repeater {
                        model: [300, 420, 340]
                        delegate: Rectangle {
                            required property int modelData
                            required property int index
                            width: 3; radius: 1.5; color: root.accentColor; height: 4
                            SequentialAnimation on height {
                                loops: Animation.Infinite; running: trackRow.isPlayingRow
                                NumberAnimation { from: 4; to: 4 + (index + 1) * 4; duration: modelData; easing.type: Easing.InOutSine }
                                NumberAnimation { from: 4 + (index + 1) * 4; to: 4; duration: modelData; easing.type: Easing.InOutSine }
                            }
                        }
                    }
                }
            }

            // Title + artist
            Column {
                x: root.numW + 10
                width: parent.width - root.numW - root.favW - root.durW - 28
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1

                Text {
                    width: parent.width
                    text: trackRow.trkTitle
                    color: trackRow.isCurrent ? root.accentColor : root.textPrimary
                    opacity: trackRow.isCurrent ? 1.0 : (trackRow.isPast ? 0.45 : 1.0)
                    font.pixelSize: root.fontSizePrimary; font.bold: trackRow.isCurrent
                    elide: Text.ElideRight
                }
                Item {
                    width: parent.width; height: artRow.implicitHeight
                    clip: true
                    Row {
                        id: artRow
                        width: parent.width; spacing: 0
                        Repeater {
                            model: trackRow.artName.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })
                            delegate: Text {
                                property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                property bool hov: false
                                text: modelData
                                opacity: isSep ? 0.4 : (trackRow.isCurrent ? 1.0 : (trackRow.isPast ? 0.45 : 1.0))
                                color: trackRow.isCurrent ? root.accentColor : (!isSep && hov ? root.accentColor : root.textSecondary)
                                font.pixelSize: root.fontSizeSecondary
                                Rectangle { visible: !parent.isSep && parent.hov; y: parent.baselineOffset + 2; width: parent.paintedWidth; height: 1; color: parent.color }
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; enabled: !parent.isSep; cursorShape: Qt.PointingHandCursor
                                    onEntered: parent.hov = true; onExited: parent.hov = false
                                    onClicked: mouse => { queueBridge.trackArtistClicked(parent.text.trim()); mouse.accepted = true }
                                }
                            }
                        }
                    }
                }
            }

            // Favorite heart
            Item {
                x: parent.width - root.durW - root.favW - 8; y: 0
                width: root.favW; height: parent.height
                Image {
                    anchors.centerIn: parent; width: 16; height: 16
                    source: trackRow.isFav ? "image://queueicons/heart_filled_E91E63"
                                            : "image://queueicons/heart_" + root.textSecondary.replace("#", "")
                    cache: true; mipmap: true; smooth: true
                    scale: favHov.containsMouse ? 1.15 : 1.0
                    Behavior on scale { NumberAnimation { duration: 100 } }
                }
                MouseArea {
                    id: favHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; z: 5
                    onClicked: mouse => { queueBridge.trackFavoriteClicked(trackRow.rowIdx); mouse.accepted = true }
                }
            }

            // Duration
            Text {
                x: parent.width - root.durW - 8
                width: root.durW; height: parent.height
                horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter
                text: trackRow.durStr
                color: trackRow.isCurrent ? root.accentColor : root.textSecondary
                opacity: trackRow.isCurrent ? 1.0 : (trackRow.isPast ? 0.4 : 1.0)
                font.pixelSize: root.fontSizeSecondary
            }

            HoverHandler { onHoveredChanged: trackRow.rowHov = hovered }
            MouseArea {
                anchors.fill: parent; hoverEnabled: false
                acceptedButtons: Qt.LeftButton | Qt.RightButton; z: 2
                propagateComposedEvents: true
                onClicked: mouse => {
                    if (mouse.button === Qt.RightButton) {
                        var gp = mapToGlobal(mouse.x, mouse.y)
                        queueBridge.trackContextMenuRequested(trackRow.rowIdx, gp.x, gp.y)
                        mouse.accepted = true
                    } else {
                        mouse.accepted = false
                    }
                }
                onDoubleClicked: queueBridge.trackPlayClicked(trackRow.rowIdx)
            }
        }
    }

    // ── Drag-reorder overlay ─────────────────────────────────────────────────
    Item {
        id: dragOverlay
        anchors.fill: queueList
        z: 200
        visible: root._isDragging

        Rectangle {
            id: ghostRow
            x: 8
            y: Math.max(0, Math.min(dragOverlay.height - root.rowH, root._dragGhostY - root.rowH / 2))
            width: parent.width - 16; height: root.rowH
            color: Qt.lighter(root.panelBgColor, 1.05)
            border.color: root.accentColor; border.width: 1
            radius: 6; opacity: 0.80

            Row {
                x: root.numW + 10; y: 0; height: parent.height
                Text {
                    width: ghostRow.width - root.numW - 20; height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    text: root._dragGhostTitle; color: root.accentColor
                    font.pixelSize: root.fontSizePrimary; font.bold: true
                    elide: Text.ElideRight
                }
            }
        }

        Item {
            visible: root._dragToIdx !== root._dragFromIdx
            x: 8; width: parent.width - 16; height: 8
            y: {
                var rowY = root._dragToIdx * root.rowH - queueList.contentY
                return (root._dragToIdx > root._dragFromIdx ? rowY + root.rowH : rowY) - 4
            }
            Rectangle {
                width: 8; height: 8; radius: 4; color: root.accentColor
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
            }
            Rectangle {
                anchors.left: parent.left; anchors.leftMargin: 8
                anchors.right: parent.right
                height: 2; color: root.accentColor
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
