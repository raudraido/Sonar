import QtQuick
import QtQuick.Controls

// Shared horizontal album/artist carousel: title row with arrow-paging,
// a momentum-free ListView of skeleton/real cover cards. Extracted from
// home.qml so both home.qml and favorites.qml share one implementation
// (see UI_MANIFEST.md). Host sets `width` (height is content-driven).
Item {
    id: root

    // ── Public API ────────────────────────────────────────────────────────
    property string title: ""
    property var    model: null

    // Theme passthroughs (host binds these to its own bridge-driven props)
    property string accentColor:    "#888888"
    property string skeletonColor:  "#282828"
    property string hoverColor:     "#555555"
    property string textPrimary:    "#eeeeee"
    property string textSecondary:  "#aaaaaa"
    property int    fontSizePrimary:   13
    property int    fontSizeSecondary: 12

    property bool showPlayButton:   true
    property bool subtextClickable: true   // artist-name link under each card
    property bool showRefresh:      false
    property bool spinning:         false
    property int  selectedIndex:    -1     // for keyboard-nav highlight; -1 = none
    property Component leadingItem: null   // optional header content (e.g. drag grip)

    signal cardClicked(int index)
    signal playClicked(int index)
    signal refreshClicked()
    signal artistSubtextClicked(string name, string artistId)
    signal loadMoreRequested(int count)

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

    // Programmatic paging — used by hosts that drive selection externally
    // (e.g. keyboard navigation across multiple carousels in home.qml).
    function scrollToIndex(idx) {
        var pageW = nCols * cellW
        var page  = Math.floor(idx / nCols)
        var maxX  = Math.max(0, carousel.contentWidth - carousel.width)
        carousel.contentX = Math.min(maxX, page * pageW)
    }

    height: 36 + 10 + cellH

    // Whether the mouse is anywhere over the title row — hosts can bind a
    // `leadingItem`'s own visibility to this (e.g. home.qml's drag grip,
    // which should reveal on hover over the whole row, not just itself).
    property alias headerHovered: headerHoverArea.containsMouse

    // ── Header ───────────────────────────────────────────────────────────
    Item {
        id: rowHeader
        width: parent.width
        height: 36

        MouseArea {
            id: headerHoverArea
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.NoButton
        }

        Loader {
            id: leadingLoader
            anchors.verticalCenter: parent.verticalCenter
            sourceComponent: root.leadingItem
        }

        Text {
            text: root.title
            color: root.textPrimary
            font.pixelSize: 15
            font.bold: true
            anchors.left: leadingLoader.status === Loader.Ready ? leadingLoader.right : parent.left
            anchors.leftMargin: leadingLoader.status === Loader.Ready ? 4 : 0
            anchors.verticalCenter: parent.verticalCenter
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
                visible: root.showRefresh
                width: 31; height: 31

                Rectangle {
                    anchors.fill: parent
                    radius: 4
                    color: root.hoverColor
                    opacity: refreshHover.containsMouse && !root.spinning ? 1.0 : 0.0
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
                        running: root.spinning
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
                    enabled: !root.spinning
                    onClicked: root.refreshClicked()
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
                        var pageW = root.nCols * root.cellW
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
                        var pageW = root.nCols * root.cellW
                        var cur   = Math.round(carousel.contentX / pageW)
                        var maxX  = Math.max(0, carousel.contentWidth - carousel.width)
                        carousel.contentX = Math.min(maxX, (cur + 1) * pageW)
                    }
                }
            }
        }
    }

    // ── Horizontal card carousel ─────────────────────────────────────────
    Item {
        anchors.top:       rowHeader.bottom
        anchors.topMargin: 10
        width:  parent.width
        height: root.cellH
        clip:   true

        ListView {
            id: carousel
            anchors.fill: parent
            orientation:  ListView.Horizontal
            interactive:  false   // arrows drive scrolling
            boundsBehavior: Flickable.StopAtBounds
            pixelAligned: true
            clip: true
            spacing: 0

            model: root.model

            // Smooth page transitions
            Behavior on contentX {
                SmoothedAnimation { velocity: 3500; maximumEasingTime: 200 }
            }

            // Trigger load-more when 80% through
            onContentXChanged: {
                if (count > 0 && !_loadingMore &&
                    contentX + width >= contentWidth * 0.8) {
                    _loadingMore = true
                    root.loadMoreRequested(count)
                }
            }
            property bool _loadingMore: false
            onCountChanged: { _loadingMore = false }

            // ── Card delegate ────────────────────────────────────────────
            delegate: Item {
                width:  root.cellW
                height: root.cellH

                // ── Skeleton card ───────────────────────────────────────
                SkeletonCard {
                    visible:    isLoading
                    anchors.fill: parent
                    anchors.margins: 6
                    pillCount:  2
                    baseColor:  root.skeletonColor
                    cardIndex:  index
                }

                // ── Real card ───────────────────────────────────────────
                Item {
                    id: card
                    visible: !isLoading
                    anchors.fill:        parent
                    anchors.leftMargin:  6
                    anchors.rightMargin: 6
                    anchors.topMargin:   4
                    anchors.bottomMargin:4

                    property bool hov: mainArea.containsMouse ||
                                       (root.showPlayButton && playArea.containsMouse)
                    property bool isSelected: !isLoading && index === root.selectedIndex

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
                            visible: root.showPlayButton
                            width:  Math.min(52, parent.width / 2.2)
                            height: width
                            radius: width / 2
                            color:  root.accentColor
                            anchors.centerIn: parent

                            opacity: playArea.containsMouse ? 1.0
                                   : card.hov               ? 0.80
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
                        }

                        Text {
                            id: artistText
                            width: parent.width
                            property bool hov: false
                            text:  albumArtist
                            color: (root.subtextClickable && hov) ? root.accentColor : root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            elide: Text.ElideRight
                            Rectangle {
                                visible: root.subtextClickable && parent.hov
                                y: parent.baselineOffset + 2
                                width: parent.paintedWidth; height: 1
                                color: parent.color
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: root.subtextClickable
                                enabled:      root.subtextClickable
                                cursorShape:  Qt.PointingHandCursor
                                z: 4
                                onEntered: parent.hov = true
                                onExited:  parent.hov = false
                                onClicked: mouse => {
                                    root.artistSubtextClicked(albumArtist, albumArtistId)
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
                        }
                    }

                    // Main click area (whole card, z:1)
                    MouseArea {
                        id: mainArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape:  Qt.PointingHandCursor
                        z: 1
                        onClicked: root.cardClicked(index)
                    }

                    // Play-button click area (z:3, above mainArea)
                    MouseArea {
                        id: playArea
                        visible: root.showPlayButton
                        enabled: root.showPlayButton
                        x:      coverArea.x + playBtn.x
                        y:      coverArea.y + playBtn.y
                        width:  playBtn.width
                        height: playBtn.height
                        hoverEnabled: true
                        cursorShape:  Qt.PointingHandCursor
                        z: 3
                        onClicked: mouse => {
                            root.playClicked(index)
                            mouse.accepted = true
                        }
                    }
                }
            } // delegate
        } // ListView
    } // clip Item
}
