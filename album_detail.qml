import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: "transparent"
    focus: true

    // ── Theme ──────────────────────────────────────────────────────────────────
    property string accentColor:     "#888888"
    property string hoverColor:      "#555555"
    property string textPrimary:     "#eeeeee"
    property string textSecondary:   "#aaaaaa"
    property int    fontSizePrimary:    13
    property int    fontSizeSecondary:  12
    property int    _twoLineH: _lhRef.implicitHeight * 2 + 2
    property string fontFamily:      ""
    property string skeletonColor:   "#282828"
    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"

    // ── Album state ────────────────────────────────────────────────────────────
    property string albumTitle:    ""
    property string albumArtist:   ""
    property string albumMeta:     ""
    property string albumType:     ""
    property string coverId:       ""
    property bool   albumFavorite: false
    property string playingTrackId:      ""
    property bool   isCurrentlyPlaying:  false
    // trackSearchBar lives inside the ListView's `header` item, which is its
    // own id scope — stash a reference here once it's created so root-level
    // bindings/handlers (search text, search controller signals) can reach it.
    property var    _searchBar: null
    readonly property string searchText: root._searchBar ? root._searchBar.searchText : ""
    property string panelBgColor:  "#0e0e0e"
    property int    selectedTrkIdx: -1

    // ── Column widths ──────────────────────────────────────────────────────────
    readonly property int colNum:  44
    property int colFav:  68
    property int colArtist: 160
    property int colDur:    72
    property int colPlays:  60
    property int colGenre:  140

    function colTitleW(avail) {
        return Math.max(80, avail - colNum - colArtist - colFav - colDur - colPlays - colGenre - 8)
    }

    // ── Column width persistence ───────────────────────────────────────────────
    Text { id: _lhRef; visible: false; text: "X"; font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily; renderType: Text.NativeRendering }

    Timer {
        id: colSaveTimer
        interval: 400; repeat: false
        onTriggered: albumBridge.saveColWidths(root.colArtist, root.colFav, root.colDur, root.colPlays, root.colGenre)
    }

    Component.onCompleted: {
        var w = albumBridge.getColWidths()
        root.colArtist = w[0]
        root.colFav    = w[1]
        root.colDur    = w[2]
        root.colPlays  = w[3]
        root.colGenre  = w[4]
    }

    // ── Bridge connections ─────────────────────────────────────────────────────
    Connections {
        target: albumBridge
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
        function onAlbumDataChanged(title, artist, meta, type, covId, isFav) {
            root.albumTitle    = title
            root.albumArtist   = artist
            root.albumMeta     = meta
            root.albumType     = type
            root.coverId       = covId
            root.albumFavorite = isFav
        }
        function onCoverIdChanged(covId)        { root.coverId       = covId }
        function onAlbumFavoriteChanged(isFav)  { root.albumFavorite = isFav }
        function onPlayingStatusChanged(tid, playing) {
            root.playingTrackId     = tid
            root.isCurrentlyPlaying = playing
        }
        function onSelectedTrackChanged(idx) { root.selectedTrkIdx = idx }
        function onScrollToModelRow(row) {
            trackList.positionViewAtIndex(row, ListView.Contain)
        }
        function onScrollToTopOfView() {
            trackList.contentY = trackList.originY
        }
        function onScrollToBottomOfView() {
            trackList.contentY = trackList.originY + Math.max(0, trackList.contentHeight - trackList.height)
        }
    }

    Connections {
        target: albumBridge.searchCtl
        function onSearchReset()         { if (root._searchBar) root._searchBar.reset() }
        function onSearchOpen()          { if (root._searchBar) root._searchBar.open() }
        function onSearchTextAppend(ch)  { if (root._searchBar) root._searchBar.appendChar(ch) }
        function onSearchTextBackspace() { if (root._searchBar) root._searchBar.backspace() }
        function onSearchClose()         { if (root._searchBar) root._searchBar.close() }
    }

    // ── Freestanding scrollbar ─────────────────────────────────────────────────
    ScrollBar {
        id: vbar
        anchors.right:  parent.right
        anchors.top:    parent.top
        anchors.bottom: parent.bottom
        width: 10
        z: 10

        opacity: trackList.contentHeight > trackList.height ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 250 } }

        property real fixedLength: 50
        size:     trackList.height > 0 ? (fixedLength / trackList.height) : 0
        position: (trackList.contentHeight > trackList.height)
                  ? ((trackList.contentY - trackList.originY) / (trackList.contentHeight - trackList.height)) * (1.0 - size)
                  : 0
        onPositionChanged: {
            if (pressed) {
                var pct = position / (1.0 - size)
                trackList.contentY = trackList.originY + pct * (trackList.contentHeight - trackList.height)
            }
        }
        contentItem: Rectangle {
            radius: 3; color: root.accentColor
            opacity: vbar.pressed || vbar.hovered || trackList.isScrollActive ? 0.9 : 0.0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }
        background: Rectangle { color: "transparent" }
    }

    // ── Main scrolling view ───────────────────────────────────────────────────
    // A single virtualized ListView is the whole page: the album header card and
    // tracklist toolbar/column headers live in `header`, the bottom card-rounding
    // strip lives in `footer`, and track rows are normal delegates. This keeps
    // the "everything scrolls together" feel while only realizing on-screen rows
    // (plus cacheBuffer) regardless of track count.
    ListView {
        id: trackList
        objectName: "trackList"
        anchors.fill: parent
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: false  // wheel handled below via momentum; no touch/drag flicking on this view
        clip: true
        spacing: 0
        cacheBuffer: 600
        model: trackModel

        property bool isScrollActive: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: trackList.isScrollActive = false }
        onContentYChanged: {
            isScrollActive = true; scrollHideTimer.restart()
        }

        // Momentum wheel-scroll: see MomentumScroll.qml for the model.
        MomentumScroll {
            target: trackList
            minContentY: trackList.originY
            maxContentY: trackList.originY + Math.max(0, trackList.contentHeight - trackList.height)
        }

        // ── HEADER: album info card + tracklist toolbar/column headers ─────────
        header: Item {
            id: pageHeader
            width: trackList.width
            height: headerArea.height + 10 + cardLid.height

            Component.onCompleted: root._searchBar = trackSearchBar

            // ── HEADER CARD ──────────────────────────────────────────────────
            Item {
                id: headerArea
                x: 12; y: 0
                width: parent.width - 24
                height: Math.max(coverItem.artSize, metaCol.implicitHeight) + 56

                Rectangle {
                    anchors.fill: parent
                    radius: 10
                    color: root.cardBgColor
                    border.color: root.cardBorderColor
                    border.width: 1
                }

                // Use Item instead of Row so we can anchor siblings freely
                Item {
                    id: headerContent
                    x: 28; y: 28
                    width: parent.width - 56
                    height: Math.max(coverItem.artSize, metaCol.implicitHeight)

                    // ── Cover art ─────────────────────────────────────────
                    Item {
                        id: coverItem
                        // Layout uses artSize (260). Shadow image bleeds outside via
                        // negative offset — so the art face sits at the card padding
                        // position and the shadow bleeds naturally around it.
                        readonly property int artSize:    264
                        readonly property int shadowPad:  30
                        readonly property int providerSize: artSize + shadowPad * 2  // 324
                        width:  artSize
                        height: artSize
                        anchors.left:           parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        property bool coverHov: false

                        // Skeleton
                        Rectangle {
                            anchors.fill: parent; radius: 10
                            color: root.skeletonColor
                            visible: root.coverId === ""
                        }

                        // Static shadow image — positioned at -shadowPad so it bleeds
                        // around the art face without affecting layout size
                        Image {
                            x: -coverItem.shadowPad; y: -coverItem.shadowPad
                            width: coverItem.providerSize; height: coverItem.providerSize
                            source: root.coverId !== "" ? "image://albumdetailcover/" + root.coverId : ""
                            mipmap: true; cache: false; smooth: true
                            visible: root.coverId !== ""
                        }

                        // Art zoom canvas — same algorithm as _RoundedPixmapLabel.paintEvent:
                        // loadImage(url) → clip rounded rect → drawImage scaled + center-offset.
                        Canvas {
                            id: artCanvas
                            anchors.fill: parent

                            property real artZoom: coverItem.coverHov ? 1.08 : 1.0
                            property string artUrl: root.coverId !== ""
                                ? "image://albumdetailcover/art/" + root.coverId : ""

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
                                var r = 10
                                ctx.beginPath()
                                ctx.moveTo(r, 0)
                                ctx.lineTo(width - r, 0)
                                ctx.arcTo(width, 0, width, r, r)
                                ctx.lineTo(width, height - r)
                                ctx.arcTo(width, height, width - r, height, r)
                                ctx.lineTo(r, height)
                                ctx.arcTo(0, height, 0, height - r, r)
                                ctx.lineTo(0, r)
                                ctx.arcTo(0, 0, r, 0, r)
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
                            onEntered: coverItem.coverHov = true
                            onExited:  coverItem.coverHov = false
                            onClicked: albumBridge.coverClicked()
                        }
                    }

                    // ── Metadata ───────────────────────────────────────────
                    Column {
                        id: metaCol
                        anchors.left:      coverItem.right
                        anchors.leftMargin: 28
                        anchors.right:     parent.right
                        anchors.top:       parent.top
                        anchors.topMargin: 16
                        spacing: 6

                        Text {
                            text: root.albumType.toUpperCase()
                            color: root.textSecondary
                            font.pixelSize: 11; font.bold: true; font.letterSpacing: 1.5
                            font.family: root.fontFamily
                            // QtRendering (default), not NativeRendering: native
                            // rendering snaps glyphs to integer pixels
                            // independently of contentY, causing a 1px pop
                            // relative to the (sub-pixel) cover image during the
                            // slow tail of a momentum scroll.
                            visible: root.albumType !== ""
                        }

                        Text {
                            width: parent.width
                            text: root.albumTitle
                            color: root.textPrimary
                            font.pixelSize: 28; font.bold: true
                            wrapMode: Text.WordWrap
                            font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                        }

                        // Artist — accent color, underline + click per part
                        Flow {
                            width: parent.width
                            spacing: 0

                            Repeater {
                                model: root.albumArtist.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })

                                delegate: Text {
                                    property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                    property bool hov: false
                                    text: modelData
                                    color: isSep ? root.textSecondary : root.accentColor
                                    font.pixelSize: root.fontSizePrimary + 1
                                    font.family: root.fontFamily
                                    // QtRendering (default) — see note above.
                                    Rectangle {
                                        visible: !parent.isSep && parent.hov
                                        y: parent.baselineOffset + 2
                                        width: parent.paintedWidth; height: 1
                                        color: parent.color
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        enabled: !parent.isSep
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true
                                        onExited:  parent.hov = false
                                        onClicked: albumBridge.albumArtistClicked(parent.text, "")
                                    }
                                }
                            }
                        }

                        Text {
                            text: root.albumMeta
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.bold: true
                            font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                            visible: root.albumMeta !== "" && root.albumMeta !== "Loading..."
                        }
                        Text {
                            text: "Loading…"
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                            visible: root.albumMeta === "Loading..."
                        }

                        // ── Action buttons ─────────────────────────────────
                        Row {
                            spacing: 10
                            topPadding: 16

                            // Play — ring button matching footer PlayButton
                            Item {
                                id: playCircle
                                width: 58; height: 58

                                // Gaussian halo — fades in on hover
                                Image {
                                    id: playHalo
                                    readonly property int sp: 20
                                    x: -sp; y: -sp
                                    width: parent.width + sp * 2; height: parent.height + sp * 2
                                    source: "image://albumdetailcover/btn/" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                    opacity: playHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                }

                                // Solid background fill — blocks halo from showing through ring centre
                                Rectangle {
                                    anchors.fill: parent; anchors.margins: 2.5
                                    radius: width / 2
                                    color: root.cardBgColor
                                }

                                // Ring — Canvas matches QPainter ellipse exactly:
                                // pen centered on path at m=2.5, width=1.8 → outer edge at 1.6px
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

                                // Play icon (accent tinted) — 16px matches footer PlayButton.iconSize()
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
                                    onClicked: albumBridge.playClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); albumBridge.showTooltip("Play Album (Ctrl+Enter)", a.x, a.y, b.y) }
                                    onExited:  albumBridge.hideTooltip()
                                }
                            }

                            // Shuffle
                            Item {
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.hoverColor
                                    opacity: shuffleHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: "image://albumicons/shuffle_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: shuffleHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: albumBridge.shuffleClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); albumBridge.showTooltip("Shuffle", a.x, a.y, b.y) }
                                    onExited:  albumBridge.hideTooltip()
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
                                    source: root.albumFavorite
                                        ? "image://albumicons/heart_filled_E91E63"
                                        : "image://albumicons/heart_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: likeHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: albumBridge.albumFavoriteClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); albumBridge.showTooltip("Add to Favorite Albums", a.x, a.y, b.y) }
                                    onExited:  albumBridge.hideTooltip()
                                }
                            }
                        }
                    }
                }
            }

            // ── TRACKLIST CARD LID (toolbar + column headers, rounded top) ────
            Rectangle {
                id: cardLid
                x: 12
                y: headerArea.height + 10
                width: parent.width - 24
                height: 12 + toolbarRow.height + colHeader.height
                color: root.cardBgColor
                border.color: root.cardBorderColor
                border.width: 1
                topLeftRadius: 10
                topRightRadius: 10
                bottomLeftRadius: 0
                bottomRightRadius: 0
            }
            // Hide cardLid's bottom border so it blends into the first track row
            Rectangle {
                x: 13
                y: cardLid.y + cardLid.height - 1
                width: parent.width - 26
                height: 1
                color: root.cardBgColor
            }

            // ── TOOLBAR ROW ──────────────────────────────────────────────
            Item {
                id: toolbarRow
                x: 20; y: cardLid.y + 12
                width: parent.width - 40; height: 36

                SearchBar {
                    id: trackSearchBar
                    anchors.right: parent.right
                    anchors.top:   parent.top
                    anchors.bottom: parent.bottom

                    accentColor:       root.accentColor
                    textPrimary:       root.textPrimary
                    textSecondary:     root.textSecondary
                    panelBgColor:      root.panelBgColor
                    borderColor:       root.cardBorderColor
                    hoverColor:        root.hoverColor
                    fontFamily:        root.fontFamily
                    fontSizeSecondary: root.fontSizeSecondary
                    placeholderText:   "Search tracks..."

                    onOpened: albumBridge.searchCtl.setSearchActive(true)
                    onClosed: albumBridge.searchCtl.setSearchActive(false)
                }
            }

            // ── TRACK LIST HEADER ────────────────────────────────────────
            Item {
                id: colHeader
                x: 20; y: toolbarRow.y + toolbarRow.height
                width: parent.width - 40; height: 36

                Row {
                    x: 4; height: parent.height; width: parent.width - 8
                    property string colStyle: root.textSecondary
                    property int    fSize:    root.fontSizeSecondary - 1

                    Text { width: root.colNum; height: parent.height; text: "#"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily }  // QtRendering (default)
                    Text { width: root.colTitleW(colHeader.width - 8); height: parent.height; text: "TITLE"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily; leftPadding: 4 }  // QtRendering (default)
                    Text { width: root.colArtist; height: parent.height; text: "ARTIST"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily; leftPadding: 4 }  // QtRendering (default)
                    Item {
                        width: root.colFav; height: parent.height
                        Text { anchors.fill: parent; text: "FAVORITE"; color: parent.parent.colStyle; font.pixelSize: parent.parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily }  // QtRendering (default)
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: albumBridge.favHeaderClicked() }
                    }
                    Text { width: root.colGenre; height: parent.height; text: "GENRE"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily; leftPadding: 4 }  // QtRendering (default)
                    Text { width: root.colDur; height: parent.height; text: "DURATION"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily }  // QtRendering (default)
                    Text { width: root.colPlays; height: parent.height; text: "PLAYS"; color: parent.colStyle; font.pixelSize: parent.fSize; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily }  // QtRendering (default)
                }
                // ── Column resize handles — visible 2px line + 12px drag zone ──
                // Order from right: PLAYS, DURATION, GENRE, FAVORITE, ARTIST
                MouseArea {
                    x: parent.width - root.colFav - root.colGenre - root.colDur - root.colPlays - 18
                    y: 0; width: 12; height: parent.height; z: 10
                    cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                    property real _pressX: 0; property int _pressW: 0
                    onPressed: { _pressX = mapToItem(null, mouseX, 0).x; _pressW = root.colArtist }
                    onPositionChanged: if (pressed) {
                        root.colArtist = Math.max(60, _pressW + (mapToItem(null, mouseX, 0).x - _pressX))
                        colSaveTimer.restart()
                    }
                    Rectangle { anchors.centerIn: parent; width: 2; height: parent.height - 22; color: root.textSecondary; opacity: parent.containsMouse ? 0.55 : 0.25 }
                }

                MouseArea {
                    x: parent.width - root.colGenre - root.colDur - root.colPlays - 18
                    y: 0; width: 12; height: parent.height; z: 10
                    cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                    property real _pressX: 0; property int _pressW: 0
                    onPressed: { _pressX = mapToItem(null, mouseX, 0).x; _pressW = root.colFav }
                    onPositionChanged: if (pressed) {
                        root.colFav = Math.max(40, _pressW + (mapToItem(null, mouseX, 0).x - _pressX))
                        colSaveTimer.restart()
                    }
                    Rectangle { anchors.centerIn: parent; width: 2; height: parent.height - 22; color: root.textSecondary; opacity: parent.containsMouse ? 0.55 : 0.25 }
                }

                MouseArea {
                    x: parent.width - root.colDur - root.colPlays - 18
                    y: 0; width: 12; height: parent.height; z: 10
                    cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                    property real _pressX: 0; property int _pressW: 0
                    onPressed: { _pressX = mapToItem(null, mouseX, 0).x; _pressW = root.colGenre }
                    onPositionChanged: if (pressed) {
                        root.colGenre = Math.max(60, _pressW + (mapToItem(null, mouseX, 0).x - _pressX))
                        colSaveTimer.restart()
                    }
                    Rectangle { anchors.centerIn: parent; width: 2; height: parent.height - 22; color: root.textSecondary; opacity: parent.containsMouse ? 0.55 : 0.25 }
                }

                MouseArea {
                    x: parent.width - root.colPlays - 18
                    y: 0; width: 12; height: parent.height; z: 10
                    cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                    property real _pressX: 0; property int _pressW: 0
                    onPressed: { _pressX = mapToItem(null, mouseX, 0).x; _pressW = root.colDur }
                    onPositionChanged: if (pressed) {
                        root.colDur = Math.max(44, _pressW + (mapToItem(null, mouseX, 0).x - _pressX))
                        colSaveTimer.restart()
                    }
                    Rectangle { anchors.centerIn: parent; width: 2; height: parent.height - 22; color: root.textSecondary; opacity: parent.containsMouse ? 0.55 : 0.25 }
                }

                MouseArea {
                    x: parent.width - 18
                    y: 0; width: 12; height: parent.height; z: 10
                    cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                    property real _pressX: 0; property int _pressW: 0
                    onPressed: { _pressX = mapToItem(null, mouseX, 0).x; _pressW = root.colPlays }
                    onPositionChanged: if (pressed) {
                        root.colPlays = Math.max(40, _pressW + (mapToItem(null, mouseX, 0).x - _pressX))
                        colSaveTimer.restart()
                    }
                }
            }
        }

        // ── FOOTER: bottom rounded corner of the tracklist card + page padding ──
        footer: Item {
            width: trackList.width
            height: 12 + 32

            Rectangle {
                x: 12; y: 0
                width: parent.width - 24; height: 12
                color: root.cardBgColor
                border.color: root.cardBorderColor
                border.width: 1
                topLeftRadius: 0
                topRightRadius: 0
                bottomLeftRadius: 10
                bottomRightRadius: 10
            }
            // Hide this rectangle's top border so it blends into the last track row
            Rectangle {
                x: 13; y: 0
                width: parent.width - 26; height: 1
                color: root.cardBgColor
            }
        }

        // ── TRACK ROWS ───────────────────────────────────────────────────────
        delegate: Item {
            id: trackRow
            width: trackList.width

            // Cache all model roles as local properties BEFORE any Repeater
            // can shadow the 'model' context object.
            property bool   isDisc:    model.isDiscHeader
            property string discLbl:   model.discLabel    || ""
            property int    trkIdx:    model.trackIdx
            property string trkId:     model.trackId      || ""
            property string trkNum:    model.trackNumber  || ""
            property string trkTitle:  model.trackTitle   || ""
            property string artName:   model.artistName   || ""
            property bool   isFav:     model.isFavorite
            property string durStr:    model.durationStr  || ""
            property string playsStr:  model.playCountStr || ""
            property string genreStr:  model.trackGenre   || ""

            property bool rowHov:      false
            property bool isSelected:  !isDisc && trkIdx === root.selectedTrkIdx
            property bool isPlaying:   root.isCurrentlyPlaying && trkId === root.playingTrackId
            property bool matchSearch: root.searchText === ""
                || trkTitle.toLowerCase().indexOf(root.searchText.toLowerCase()) >= 0
                || artName.toLowerCase().indexOf(root.searchText.toLowerCase()) >= 0
                || genreStr.toLowerCase().indexOf(root.searchText.toLowerCase()) >= 0

            height: isDisc ? (root.searchText === "" ? 36 : 0) : (matchSearch ? 40 : 0)
            visible: height > 0
            clip: false

            // ── Card body continuation (fill + side borders) ────────────────
            Rectangle {
                x: 12; y: 0
                width: parent.width - 24; height: parent.height
                color: root.cardBgColor
            }
            Rectangle { x: 12; y: 0; width: 1; height: parent.height; color: root.cardBorderColor }
            Rectangle { x: parent.width - 13; y: 0; width: 1; height: parent.height; color: root.cardBorderColor }

            // ── Disc header ─────────────────────────────────────────
            Item {
                visible: isDisc; anchors.fill: parent
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    x: root.colNum + 28
                    text: trackRow.discLbl
                    color: root.textSecondary
                    font.pixelSize: root.fontSizeSecondary; font.bold: true
                    font.family: root.fontFamily
                    // QtRendering (default) — see note above.
                }
            }

            // ── Track row ────────────────────────────────────────────
            Item {
                visible: !isDisc; anchors.fill: parent

                // Hover / playing / keyboard-selection background
                Rectangle {
                    x: 13; y: 1
                    width: parent.width - 26; height: parent.height - 2
                    radius: 4
                    color: isPlaying
                        ? Qt.rgba(Qt.color(root.accentColor).r, Qt.color(root.accentColor).g, Qt.color(root.accentColor).b, 0.15)
                        : root.hoverColor
                    opacity: isPlaying ? 1.0 : (rowHov || isSelected ? 1.0 : 0.0)
                    Behavior on opacity { NumberAnimation { duration: 120 } }
                }

                Row {
                    x: 24; y: 0
                    width: parent.width - 48; height: parent.height

                    // # / playing bars
                    Item {
                        width: root.colNum; height: parent.height
                        Text {
                            visible: !isPlaying; anchors.centerIn: parent
                            text: trackRow.trkNum; color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                        }
                        Row {
                            visible: isPlaying; anchors.centerIn: parent; spacing: 3
                            Repeater {
                                model: [300, 420, 340]
                                delegate: Rectangle {
                                    required property int modelData
                                    required property int index
                                    width: 3; radius: 1.5; color: root.accentColor; height: 4
                                    SequentialAnimation on height {
                                        loops: Animation.Infinite
                                        running: isPlaying && root.isCurrentlyPlaying
                                        NumberAnimation { from: 4; to: 4 + (index + 1) * 4; duration: modelData; easing.type: Easing.InOutSine }
                                        NumberAnimation { from: 4 + (index + 1) * 4; to: 4; duration: modelData; easing.type: Easing.InOutSine }
                                    }
                                }
                            }
                        }
                    }

                    // Title
                    Text {
                        width: root.colTitleW(trackList.width - 48); height: parent.height
                        verticalAlignment: Text.AlignVCenter; leftPadding: 4
                        text: trackRow.trkTitle
                        color: isPlaying ? root.accentColor : root.textPrimary
                        font.pixelSize: root.fontSizePrimary; font.bold: true
                        elide: Text.ElideRight; font.family: root.fontFamily
                        // QtRendering (default) — see note above.
                    }

                    // Artist — split into clickable parts
                    // NOTE: uses trackRow.artName (NOT model.artistName) to avoid
                    // the Repeater's own 'model' property shadowing the delegate context.
                    Item {
                        width: root.colArtist; height: parent.height; clip: true

                        Flow {
                            id: flowArt
                            x: 4; width: parent.width - 4; anchors.verticalCenter: parent.verticalCenter; spacing: 0
                            height: Math.min(implicitHeight, root._twoLineH)
                            clip: true

                            Repeater {
                                model: trackRow.artName.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })

                                delegate: Text {
                                    property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                    property bool hov: false
                                    text: modelData
                                    opacity: isSep ? 0.4 : 1.0
                                    color: !isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary
                                    font.family: root.fontFamily
                                    // QtRendering (default) — see note above.
                                    Rectangle {
                                        visible: !parent.isSep && parent.hov
                                        y: parent.baselineOffset + 2
                                        width: parent.paintedWidth; height: 1
                                        color: parent.color
                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        enabled: !parent.isSep; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true
                                        onExited:  parent.hov = false
                                        onClicked: mouse => { albumBridge.trackArtistClicked(parent.text); mouse.accepted = true }
                                    }
                                }
                            }
                        }

                        Text {
                            visible: flowArt.implicitHeight > flowArt.height
                            text: "…"
                            anchors.right: parent.right; anchors.rightMargin: 4
                            y: flowArt.y + flowArt.height - implicitHeight
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                        }
                    }

                    // Favorite
                    Item {
                        width: root.colFav; height: parent.height
                        Image {
                            anchors.centerIn: parent; width: 16; height: 16
                            source: trackRow.isFav
                                ? "image://albumicons/heart_filled_E91E63"
                                : "image://albumicons/heart_" + (favHov.containsMouse ? root.accentColor.replace("#","") : root.textSecondary.replace("#",""))
                            cache: false; mipmap: true; smooth: true
                            scale: favHov.containsMouse ? 1.2 : 1.0
                            Behavior on scale { NumberAnimation { duration: 100 } }
                        }
                        MouseArea {
                            id: favHov; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor; z: 5
                            onClicked: mouse => { albumBridge.trackFavoriteClicked(trackRow.trkIdx); mouse.accepted = true }
                        }
                    }

                    // Genre — clickable parts separated by " • "
                    Item {
                        width: root.colGenre; height: parent.height; clip: true

                        Flow {
                            id: flowGen
                            x: 4; width: parent.width - 4; anchors.verticalCenter: parent.verticalCenter; spacing: 0
                            height: Math.min(implicitHeight, root._twoLineH)
                            clip: true

                            Repeater {
                                model: trackRow.genreStr.split(/( • )/).filter(function(p) { return p !== "" })

                                delegate: Text {
                                    property bool isSep: modelData === " • "
                                    property bool hov: false
                                    text: modelData
                                    opacity: isSep ? 0.4 : 1.0
                                    color: !isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary
                                    font.family: root.fontFamily
                                    // QtRendering (default) — see note above.
                                    Rectangle {
                                        visible: !parent.isSep && parent.hov
                                        y: parent.baselineOffset + 2
                                        width: parent.paintedWidth; height: 1
                                        color: parent.color
                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        enabled: !parent.isSep; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true
                                        onExited:  parent.hov = false
                                        onClicked: mouse => { albumBridge.trackGenreClicked(parent.text); mouse.accepted = true }
                                    }
                                }
                            }
                        }

                        Text {
                            visible: flowGen.implicitHeight > flowGen.height
                            text: "…"
                            anchors.right: parent.right; anchors.rightMargin: 4
                            y: flowGen.y + flowGen.height - implicitHeight
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                            // QtRendering (default) — see note above.
                        }
                    }

                    // Duration
                    Text {
                        width: root.colDur; height: parent.height
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: trackRow.durStr
                        color: isPlaying ? root.accentColor : root.textSecondary
                        font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                        // QtRendering (default) — see note above.
                    }

                    // Plays
                    Text {
                        width: root.colPlays; height: parent.height
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: trackRow.playsStr; color: root.textSecondary
                        font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                        // QtRendering (default) — see note above.
                    }
                }

                // Row mouse handler
                HoverHandler { onHoveredChanged: trackRow.rowHov = hovered }
                MouseArea {
                    anchors.fill: parent; hoverEnabled: false
                    acceptedButtons: Qt.LeftButton | Qt.RightButton; z: 2
                    propagateComposedEvents: true
                    onClicked: mouse => {
                        if (mouse.button === Qt.RightButton) {
                            var gp = mapToGlobal(mouse.x, mouse.y)
                            albumBridge.trackContextMenuRequested(trackRow.trkIdx, gp.x, gp.y)
                            mouse.accepted = true
                        } else {
                            mouse.accepted = false
                        }
                    }
                    onDoubleClicked: albumBridge.trackPlayClicked(trackRow.trkIdx)
                }
            }
        }
    }
}
