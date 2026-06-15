import QtQuick
import QtQuick.Controls
import "../shared_qml"

// Single-scene artist detail page: header card (photo/name/actions), bio
// card, popular tracks, chunked album-section grids, and a related-artists
// strip — all inside ONE ListView with QML-driven momentum scrolling
// (MomentumScroll.qml), mirroring album_detail.qml. Replaces the old
// QScrollArea + per-section QQuickView (QMLGridWrapper) architecture.
Rectangle {
    id: root
    color: root.panelBgColor
    focus: true

    // ── Theme (sourced from artistBridge — shared by every section) ────────────
    property string accentColor:     "#888888"
    property string hoverColor:      "#555555"
    property string textPrimary:     "#eeeeee"
    property string textSecondary:   "#aaaaaa"
    property int    fontSizePrimary:    13
    property int    fontSizeSecondary:  12
    property string fontFamily:      ""
    property string skeletonColor:   "#282828"
    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"
    property string panelBgColor:    "#0e0e0e"

    // ── Artist state ─────────────────────────────────────────────────────────
    property string artistName:    "Artist Name"
    property string artistStats:   "Loading..."
    property bool   artistFavorite: false
    property string photoId:       ""
    property string bioText:       ""
    property bool   bioCollapsed:  true

    // ── Related-artist card geometry (shared by delegate + arrow scroll step) ──
    readonly property int relCellWidth:  220
    readonly property int relCellHeight: 270

    // ── Cross-scope bridge: header/footer are separate id-scopes (ListView
    // header/footer are implicitly wrapped in their own Component), so
    // root-level functions can't reach ids like popularItem/popularList
    // directly. The header keeps this in sync via a Binding.
    property real popularItemY: 0

    // ── Bridge connections ──────────────────────────────────────────────────────
    Connections {
        target: artistBridge
        function onAccentColorChanged(c)        { root.accentColor       = c }
        function onHoverColorChanged(c)         { root.hoverColor        = c }
        function onSkeletonColorChanged(c)      { root.skeletonColor     = c }
        function onCardBgChanged(c)             { root.cardBgColor       = c }
        function onCardBorderChanged(c)         { root.cardBorderColor   = c }
        function onFontSizePrimaryChanged(s)    { root.fontSizePrimary   = s }
        function onFontSizeSecondaryChanged(s)  { root.fontSizeSecondary = s }
        function onFontColorPrimaryChanged(c)   { root.textPrimary       = c }
        function onFontColorSecondaryChanged(c) { root.textSecondary     = c }
        function onFontFamilyChanged(f)         { root.fontFamily        = f }
        function onPanelBgChanged(c)            { root.panelBgColor      = c }
        function onArtistDataChanged(name, stats, isFav) {
            root.artistName     = name
            root.artistStats    = stats
            root.artistFavorite = isFav
        }
        function onPhotoIdChanged(pid) { root.photoId = pid }
        function onBioChanged(text) {
            root.bioText      = text
            root.bioCollapsed = true
        }
    }

    // ── Keyboard-navigation helpers (called from Python via QMetaObject.invokeMethod) ──
    function scrollToTop() {
        pageScroll.wheelVelocity = 0
        pageList.contentY = pageList.originY
    }

    function scrollToBottom() {
        pageScroll.wheelVelocity = 0
        pageList.contentY = pageList.originY + Math.max(0, pageList.contentHeight - pageList.height)
    }

    function scrollToPopular() {
        pageScroll.wheelVelocity = 0
        var maxY = pageList.originY + Math.max(0, pageList.contentHeight - pageList.height)
        pageList.contentY = Math.max(pageList.originY, Math.min(root.popularItemY, maxY))
    }

    // ── Main scrolling view ───────────────────────────────────────────────────
    ListView {
        id: pageList
        anchors.fill: parent
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: false  // wheel handled via MomentumScroll below
        clip: true
        spacing: 0
        cacheBuffer: 600
        model: sectionsModel

        MomentumScroll {
            id: pageScroll
            target: pageList
            minContentY: pageList.originY
            maxContentY: pageList.originY + Math.max(0, pageList.contentHeight - pageList.height)
        }

        // ── HEADER: artist card + bio card + popular tracks ─────────────────
        header: Column {
            id: headerCol
            width: pageList.width
            topPadding: 12
            spacing: 10

            // ── ARTIST HEADER CARD (photo, name, stats, action buttons) ─────
            Item {
                id: headerCardItem
                x: 12
                width: parent.width - 12 - 6
                height: Math.max(photoItem.artSize, metaCol.implicitHeight) + 56

                Rectangle {
                    anchors.fill: parent
                    radius: 10
                    color: root.cardBgColor
                    border.color: root.cardBorderColor
                    border.width: 1
                }

                Item {
                    id: headerContent
                    x: 28; y: 28
                    width: parent.width - 56
                    height: Math.max(photoItem.artSize, metaCol.implicitHeight)

                    // ── Artist photo (circular) ─────────────────────────────
                    Item {
                        id: photoItem
                        readonly property int artSize:    264
                        readonly property int shadowPad:  30
                        readonly property int providerSize: artSize + shadowPad * 2  // 324
                        width:  artSize
                        height: artSize
                        anchors.left:           parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        property bool photoHov: false

                        // Skeleton
                        Rectangle {
                            anchors.fill: parent; radius: width / 2
                            color: root.skeletonColor
                            visible: root.photoId === ""
                        }

                        // Static shadow image — bleeds around the circular photo
                        Image {
                            x: -photoItem.shadowPad; y: -photoItem.shadowPad
                            width: photoItem.providerSize; height: photoItem.providerSize
                            source: root.photoId !== "" ? "image://artistdetailcover/" + root.photoId : ""
                            mipmap: true; cache: false; smooth: true
                            visible: root.photoId !== ""
                        }

                        // Photo zoom canvas — circular clip, zoom on hover
                        Canvas {
                            id: photoCanvas
                            anchors.fill: parent

                            property real artZoom: photoItem.photoHov ? 1.08 : 1.0
                            property string artUrl: root.photoId !== ""
                                ? "image://artistdetailcover/art/" + root.photoId : ""

                            Behavior on artZoom {
                                NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                            }
                            onArtZoomChanged: requestPaint()
                            onArtUrlChanged: {
                                if (artUrl !== "") loadImage(artUrl)
                                else requestPaint()
                            }
                            onImageLoaded: requestPaint()

                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                if (artUrl === "" || !isImageLoaded(artUrl)) return
                                ctx.save()
                                ctx.imageSmoothingEnabled = true
                                ctx.imageSmoothingQuality = "high"
                                ctx.beginPath()
                                ctx.arc(width / 2, height / 2, width / 2, 0, Math.PI * 2)
                                ctx.closePath()
                                ctx.clip()
                                var zw = width  * artZoom
                                var zh = height * artZoom
                                ctx.drawImage(artUrl, -(zw - width) / 2, -(zh - height) / 2, zw, zh)
                                ctx.restore()
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onEntered: photoItem.photoHov = true
                            onExited:  photoItem.photoHov = false
                            onClicked: artistBridge.photoClicked()
                        }
                    }

                    // ── Metadata ───────────────────────────────────────────
                    Column {
                        id: metaCol
                        anchors.left:      photoItem.right
                        anchors.leftMargin: 28
                        anchors.right:     parent.right
                        anchors.top:       parent.top
                        anchors.topMargin: 16
                        spacing: 6

                        Text {
                            width: parent.width
                            text: root.artistName
                            color: root.textPrimary
                            font.pixelSize: 28; font.bold: true
                            wrapMode: Text.WordWrap
                            font.family: root.fontFamily; renderType: Text.NativeRendering
                        }

                        Text {
                            text: root.artistStats
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.bold: true
                            font.family: root.fontFamily; renderType: Text.NativeRendering
                        }

                        // ── Action buttons ─────────────────────────────────
                        Row {
                            spacing: 10
                            topPadding: 16

                            // Play — ring button matching album_detail.qml
                            Item {
                                id: playCircle
                                width: 58; height: 58

                                Image {
                                    id: playHalo
                                    readonly property int sp: 20
                                    x: -sp; y: -sp
                                    width: parent.width + sp * 2; height: parent.height + sp * 2
                                    source: "image://artistdetailcover/btn/" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                    opacity: playHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                }

                                Rectangle {
                                    anchors.fill: parent; anchors.margins: 2.5
                                    radius: width / 2
                                    color: root.cardBgColor
                                }

                                Canvas {
                                    id: ringCanvas
                                    anchors.fill: parent
                                    onPaint: {
                                        var ctx = getContext("2d")
                                        ctx.clearRect(0, 0, width, height)
                                        ctx.strokeStyle = root.accentColor
                                        ctx.lineWidth = 1.8
                                        ctx.beginPath()
                                        ctx.arc(width / 2, height / 2, width / 2 - 2.5, 0, Math.PI * 2)
                                        ctx.stroke()
                                    }
                                    Connections {
                                        target: root
                                        function onAccentColorChanged() { ringCanvas.requestPaint() }
                                    }
                                }

                                Image {
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: 1
                                    width: 16; height: 16
                                    source: "image://albumicons/play_" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }

                                MouseArea {
                                    id: playHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: artistBridge.playClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); artistBridge.showTooltip("Play all tracks (Ctrl+Enter)", a.x, a.y, b.y) }
                                    onExited:  artistBridge.hideTooltip()
                                }
                            }

                            // Like / favorite
                            Item {
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.hoverColor
                                    opacity: likeHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }
                                Image {
                                    anchors.centerIn: parent; width: 22; height: 22
                                    source: root.artistFavorite
                                        ? "image://albumicons/heart_filled_E91E63"
                                        : "image://albumicons/heart_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: likeHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: artistBridge.likeClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); artistBridge.showTooltip("Add to Favorites", a.x, a.y, b.y) }
                                    onExited:  artistBridge.hideTooltip()
                                }
                            }

                            // Last.fm
                            Item {
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.hoverColor
                                    opacity: lastfmHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }
                                Image {
                                    anchors.centerIn: parent; width: 22; height: 22
                                    source: "image://albumicons/lastfm_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: lastfmHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: artistBridge.lastfmClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); artistBridge.showTooltip("Open on Last.fm", a.x, a.y, b.y) }
                                    onExited:  artistBridge.hideTooltip()
                                }
                            }

                            // Wikipedia
                            Item {
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.hoverColor
                                    opacity: wikiHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }
                                Image {
                                    anchors.centerIn: parent; width: 22; height: 22
                                    source: "image://albumicons/wikipedia_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: wikiHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: artistBridge.wikipediaClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); artistBridge.showTooltip("Open on Wikipedia", a.x, a.y, b.y) }
                                    onExited:  artistBridge.hideTooltip()
                                }
                            }
                        }
                    }
                }
            }

            // ── ABOUT / BIO CARD ─────────────────────────────────────────────
            Item {
                id: aboutCardItem
                x: 12
                width: parent.width - 12 - 6
                visible: root.bioText !== ""
                height: visible ? (24 + aboutCol.implicitHeight + 24) : 0

                Rectangle {
                    anchors.fill: parent
                    radius: 10
                    color: root.cardBgColor
                    border.color: root.cardBorderColor
                    border.width: 1
                    visible: aboutCardItem.visible
                }

                Column {
                    id: aboutCol
                    x: 24; y: 24
                    width: parent.width - 48
                    spacing: 8

                    Text {
                        text: "About " + root.artistName
                        color: root.textPrimary
                        font.pixelSize: 20; font.bold: true
                        font.family: root.fontFamily; renderType: Text.NativeRendering
                    }

                    Text {
                        id: bioBody
                        width: parent.width
                        text: root.bioText
                        color: root.textSecondary
                        font.pixelSize: root.fontSizeSecondary
                        font.family: root.fontFamily; renderType: Text.NativeRendering
                        wrapMode: Text.WordWrap
                        lineHeight: 1.4
                        maximumLineCount: root.bioCollapsed ? 10 : 100000
                        elide: Text.ElideRight
                    }

                    // Hidden measurement copy — used only to detect whether the
                    // full bio would overflow 10 lines, independent of bioCollapsed
                    Text {
                        id: bioMeasure
                        visible: false
                        width: bioBody.width
                        text: root.bioText
                        font.pixelSize: bioBody.font.pixelSize
                        font.family: bioBody.font.family
                        wrapMode: Text.WordWrap
                        maximumLineCount: 10
                        elide: Text.ElideRight
                    }

                    Text {
                        text: root.bioCollapsed ? "Show more" : "Show less"
                        color: root.textSecondary
                        font.pixelSize: root.fontSizeSecondary
                        font.family: root.fontFamily; renderType: Text.NativeRendering
                        visible: bioMeasure.truncated

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.bioCollapsed = !root.bioCollapsed
                        }
                    }
                }
            }

            // ── POPULAR TRACKS ───────────────────────────────────────────────
            Item {
                id: popularItem
                x: 12
                width: parent.width - 12 - 6
                visible: popularList.count > 0
                height: visible ? (popularTitle.height + popularTitle.anchors.topMargin + popularList.height) : 0

                Text {
                    id: popularTitle
                    anchors.top: parent.top
                    anchors.topMargin: 10
                    anchors.left: parent.left
                    text: "Popular"
                    color: root.textPrimary
                    font.pixelSize: 20; font.bold: true
                    font.family: root.fontFamily; renderType: Text.NativeRendering
                }

                ListView {
                    id: popularList
                    anchors.top: popularTitle.bottom
                    anchors.topMargin: 10
                    width: parent.width
                    height: contentHeight

                    interactive: false
                    boundsBehavior: Flickable.StopAtBounds
                    clip: false

                    focus: true
                    currentIndex: count > 0 ? 0 : -1
                    bottomMargin: 10

                    model: trackListModel

                    readonly property int rowHeight: 44

                    delegate: Item {
                        id: rowRoot
                        width: popularList.width
                        height: popularList.rowHeight

                        property bool isCurrent: ListView.isCurrentItem && popularList.activeFocus
                        property bool isHighlighted: rowArea.containsMouse || albumArea.containsMouse || isCurrent

                        Rectangle {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            radius: 6
                            color: root.hoverColor
                            opacity: rowRoot.isHighlighted ? 1.0 : 0.0
                        }

                        Text {
                            id: numberText
                            width: 45
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            horizontalAlignment: Text.AlignHCenter
                            text: trackNumber
                            color: root.textSecondary
                            font.pixelSize: root.fontSizePrimary
                            renderType: Text.NativeRendering
                        }

                        Rectangle {
                            id: coverRect
                            width: 40
                            height: 40
                            radius: 4
                            anchors.left: numberText.right
                            anchors.leftMargin: 5
                            anchors.verticalCenter: parent.verticalCenter
                            color: coverId ? "transparent" : "#222222"
                            clip: true

                            Image {
                                anchors.fill: parent
                                source: coverId ? "image://populartrackcovers/" + coverId : ""
                                fillMode: Image.PreserveAspectCrop
                                cache: false
                                smooth: true
                            }
                        }

                        Text {
                            id: titleText
                            anchors.left: coverRect.right
                            anchors.leftMargin: 15
                            anchors.right: albumText.left
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: trackTitle
                            color: rowRoot.isHighlighted ? root.accentColor : root.textPrimary
                            font.pixelSize: root.fontSizePrimary
                            elide: Text.ElideRight
                            renderType: Text.NativeRendering
                        }

                        Text {
                            id: albumText
                            width: Math.max(80, parent.width * 0.3)
                            anchors.right: durationText.left
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: trackAlbum
                            color: albumArea.containsMouse ? root.accentColor : root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            font.underline: albumArea.containsMouse
                            elide: Text.ElideRight
                            renderType: Text.NativeRendering

                            MouseArea {
                                id: albumArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                z: 1
                                onClicked: trackListBridge.emitAlbumClicked(Number(index))
                            }
                        }

                        Text {
                            id: durationText
                            width: 70
                            anchors.right: parent.right
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            horizontalAlignment: Text.AlignRight
                            text: trackDuration
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            renderType: Text.NativeRendering
                        }

                        MouseArea {
                            id: rowArea
                            anchors.fill: parent
                            anchors.rightMargin: albumText.width + durationText.width + 20
                            hoverEnabled: true
                            onClicked: {
                                popularList.forceActiveFocus()
                                popularList.currentIndex = index
                            }
                            onDoubleClicked: trackListBridge.emitTrackClicked(Number(index))
                        }
                    }
                }
            }

            // popularList/popularItem live in this header Component's id
            // scope, invisible to root — mirror what root needs out via a
            // property, and handle popular-track selection here.
            Binding { target: root; property: "popularItemY"; value: popularItem.y }

            Connections {
                target: trackListBridge
                function onSelectIndex(idx) {
                    if (idx >= 0 && idx < popularList.count) popularList.currentIndex = idx
                    popularList.forceActiveFocus()
                    root.scrollToPopular()
                }
            }
        }

        // ── DELEGATE: one album-section chunk per row ───────────────────────
        delegate: Item {
            id: sectionItem
            width: pageList.width
            property int sectionRow: index
            height: titleRow.height + grid.contentHeight

            Item {
                id: titleRow
                x: 12
                width: parent.width - 12 - 6
                height: sectionTitle !== "" ? 50 : 0
                visible: sectionTitle !== ""

                Row {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10

                    Text {
                        text: sectionTitle
                        color: root.textPrimary
                        font.pixelSize: 20; font.bold: true
                        font.family: root.fontFamily; renderType: Text.NativeRendering
                    }

                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        radius: 4
                        color: "transparent"
                        border.color: root.cardBorderColor
                        border.width: 1
                        height: 22
                        width: sectionCountText.implicitWidth + 16

                        Text {
                            id: sectionCountText
                            anchors.centerIn: parent
                            text: sectionCount
                            color: root.textPrimary
                            font.pixelSize: 12; font.bold: true
                            font.family: root.fontFamily; renderType: Text.NativeRendering
                        }
                    }
                }
            }

            GridView {
                id: grid
                anchors.top: titleRow.bottom
                width: parent.width
                height: contentHeight

                interactive: false
                boundsBehavior: Flickable.StopAtBounds
                clip: false

                currentIndex: count > 0 ? 0 : -1
                model: albumModel

                leftMargin: 4
                rightMargin: 4
                topMargin: 4
                bottomMargin: 4

                property real itemGap: 10
                property real baseItemSize: 180
                property real availableWidth: Math.max(1, width - leftMargin - rightMargin)
                property int  itemsPerRow: Math.max(1, Math.floor(availableWidth / (baseItemSize + itemGap * 2)))
                property real widthPerItem: Math.floor(availableWidth / itemsPerRow)

                cellWidth: widthPerItem
                cellHeight: widthPerItem + 70

                Connections {
                    target: sectionBridge
                    function onSelectIndex(row, idx) {
                        if (row === sectionItem.sectionRow) {
                            if (idx >= 0 && idx < grid.count) grid.currentIndex = idx
                            grid.forceActiveFocus()
                            pageList.positionViewAtIndex(row, ListView.Contain)
                        }
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
                            visible: albumTitle === ""
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            pillCount: 3
                            baseColor: root.skeletonColor
                            cardIndex: index
                        }

                        Item {
                            id: coverContainer
                            visible: albumTitle !== ""
                            width: parent.width
                            height: parent.width
                            anchors.top: parent.top

                            Rectangle {
                                anchors.fill: parent
                                radius: 8
                                color: coverId ? "transparent" : root.skeletonColor
                            }

                            Image {
                                anchors.fill: parent
                                source: coverId ? "image://sectioncovers/" + coverId : ""
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
                                color: cardRoot.isHovered ? root.accentColor : root.textPrimary
                                font.pixelSize: root.fontSizePrimary
                                font.bold: true
                                elide: Text.ElideRight
                                renderType: Text.NativeRendering
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
                                        color: isSep ? "#777" : (hov ? root.accentColor : root.textSecondary)
                                        font.pixelSize: root.fontSizeSecondary
                                        renderType: Text.NativeRendering
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
                                                var aid = parent.parent.primaryArtistId
                                                grid.forceActiveFocus()
                                                grid.currentIndex = parent.parent.albumIndex
                                                sectionBridge.emitArtistNameClicked(parent.text, aid)
                                                mouse.accepted = true
                                            }
                                        }
                                    }
                                }
                            }

                            Text {
                                width: parent.width
                                text: albumYear
                                color: root.textSecondary
                                font.pixelSize: root.fontSizeSecondary
                                elide: Text.ElideRight
                                renderType: Text.NativeRendering
                            }
                        }

                        MouseArea {
                            id: mouseArea
                            enabled: albumTitle !== ""
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            z: 1
                            onClicked: {
                                grid.forceActiveFocus()
                                grid.currentIndex = index
                                sectionBridge.emitItemClicked(sectionItem.sectionRow, Number(index))
                            }
                        }

                        MouseArea {
                            id: playArea
                            enabled: albumTitle !== ""
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
                                sectionBridge.emitPlayClicked(sectionItem.sectionRow, Number(index))
                            }
                        }
                    }
                }
            }
        }

        // ── FOOTER: related artists strip ────────────────────────────────────
        footer: Item {
            id: footerItem
            width: pageList.width
            visible: relatedList.count > 0
            height: visible ? (50 + relatedList.height + 50) : 0

            Item {
                id: relatedTitleRow
                x: 12
                anchors.top: parent.top
                width: parent.width - 12 - 6
                height: 50

                Row {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10

                    Text {
                        text: "Related Artists"
                        color: root.textPrimary
                        font.pixelSize: 20; font.bold: true
                        font.family: root.fontFamily; renderType: Text.NativeRendering
                    }

                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        radius: 4
                        color: "transparent"
                        border.color: root.cardBorderColor
                        border.width: 1
                        height: 22
                        width: relatedCountText.implicitWidth + 16

                        Text {
                            id: relatedCountText
                            anchors.centerIn: parent
                            text: relatedList.count
                            color: root.textPrimary
                            font.pixelSize: 12; font.bold: true
                            font.family: root.fontFamily; renderType: Text.NativeRendering
                        }
                    }
                }

                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4

                    Rectangle {
                        id: leftArrowBtn
                        width: 30; height: 30
                        radius: 12
                        color: leftArrowArea.containsMouse ? root.hoverColor : "transparent"

                        Canvas {
                            id: leftArrowCanvas
                            anchors.fill: parent
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                ctx.strokeStyle = root.accentColor
                                ctx.lineWidth = 2
                                ctx.lineCap = "round"
                                ctx.lineJoin = "round"
                                var cx = width / 2, cy = height / 2
                                var s = 6, o = 3
                                ctx.beginPath()
                                ctx.moveTo(cx + o, cy - s)
                                ctx.lineTo(cx - o, cy)
                                ctx.lineTo(cx + o, cy + s)
                                ctx.stroke()
                            }
                            Connections {
                                target: root
                                function onAccentColorChanged() { leftArrowCanvas.requestPaint() }
                            }
                        }

                        MouseArea {
                            id: leftArrowArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                var target = relatedList.contentX - root.relCellWidth
                                relatedList.contentX = Math.max(0, Math.min(target, Math.max(0, relatedList.contentWidth - relatedList.width)))
                            }
                        }
                    }

                    Rectangle {
                        id: rightArrowBtn
                        width: 30; height: 30
                        radius: 12
                        color: rightArrowArea.containsMouse ? root.hoverColor : "transparent"

                        Canvas {
                            id: rightArrowCanvas
                            anchors.fill: parent
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                ctx.strokeStyle = root.accentColor
                                ctx.lineWidth = 2
                                ctx.lineCap = "round"
                                ctx.lineJoin = "round"
                                var cx = width / 2, cy = height / 2
                                var s = 6, o = 3
                                ctx.beginPath()
                                ctx.moveTo(cx - o, cy - s)
                                ctx.lineTo(cx + o, cy)
                                ctx.lineTo(cx - o, cy + s)
                                ctx.stroke()
                            }
                            Connections {
                                target: root
                                function onAccentColorChanged() { rightArrowCanvas.requestPaint() }
                            }
                        }

                        MouseArea {
                            id: rightArrowArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                var target = relatedList.contentX + root.relCellWidth
                                relatedList.contentX = Math.max(0, Math.min(target, Math.max(0, relatedList.contentWidth - relatedList.width)))
                            }
                        }
                    }
                }
            }

            ListView {
                id: relatedList
                anchors.top: relatedTitleRow.bottom
                width: parent.width
                height: root.relCellHeight + 12
                orientation: ListView.Horizontal

                interactive: false
                boundsBehavior: Flickable.StopAtBounds
                clip: false

                currentIndex: count > 0 ? 0 : -1
                model: relatedArtistModel

                MomentumScroll {
                    target: relatedList
                    horizontal: true
                    minContent: 0
                    maxContent: Math.max(0, relatedList.contentWidth - relatedList.width)
                }

                delegate: Item {
                    id: cardRoot
                    width: root.relCellWidth
                    height: root.relCellHeight

                    property bool isMouseHovered: mouseArea.containsMouse || playArea.containsMouse
                    property bool isKeyboardFocused: relatedList.activeFocus && relatedList.currentIndex === index
                    property bool isHovered: isMouseHovered || isKeyboardFocused

                    Item {
                        id: photoContainer
                        width: parent.width - 20
                        height: width
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.top
                        anchors.topMargin: 10

                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: root.skeletonColor
                            visible: coverId === ""
                        }

                        Image {
                            anchors.fill: parent
                            source: coverId ? "image://relatedartistcovers/" + coverId : ""
                            fillMode: Image.PreserveAspectCrop
                            cache: false
                            smooth: true
                        }

                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: "#000"
                            opacity: cardRoot.isHovered ? 0.4 : 0.0
                            Behavior on opacity { NumberAnimation { duration: 150 } }
                        }

                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: "transparent"
                            border.color: cardRoot.isHovered ? root.accentColor : "transparent"
                            border.width: cardRoot.isHovered ? 2 : 0
                        }

                        Rectangle {
                            id: playBtnObj
                            width: Math.min(40, parent.width / 3)
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
                                    ctx.moveTo(cx - triSize / 3, cx - triSize / 2)
                                    ctx.lineTo(cx - triSize / 3, cx + triSize / 2)
                                    ctx.lineTo(cx + triSize / 2 + 2, cx)
                                    ctx.fill()
                                }
                            }
                        }
                    }

                    Text {
                        width: parent.width - 10
                        anchors.top: photoContainer.bottom
                        anchors.topMargin: 10
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: artistName
                        color: cardRoot.isHovered ? root.accentColor : root.textPrimary
                        font.pixelSize: root.fontSizePrimary
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
                    }

                    MouseArea {
                        id: mouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        z: 1
                        onClicked: {
                            relatedList.forceActiveFocus()
                            relatedList.currentIndex = index
                            relatedArtistsBridge.emitItemClicked(Number(index))
                        }
                    }

                    MouseArea {
                        id: playArea
                        x: photoContainer.x + playBtnObj.x
                        y: photoContainer.y + playBtnObj.y
                        width: playBtnObj.width
                        height: playBtnObj.height
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        z: 2
                        onClicked: {
                            relatedList.forceActiveFocus()
                            relatedList.currentIndex = index
                            relatedArtistsBridge.emitPlayClicked(Number(index))
                        }
                    }
                }
            }

            // relatedList lives in this footer Component's id scope,
            // invisible to root — handle related-artist selection here.
            Connections {
                target: relatedArtistsBridge
                function onSelectIndex(idx) {
                    if (idx >= 0 && idx < relatedList.count) relatedList.currentIndex = idx
                    relatedList.forceActiveFocus()
                    root.scrollToBottom()
                }
            }
        }
    }
}
