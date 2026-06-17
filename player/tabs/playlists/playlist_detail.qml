import QtQuick
import QtQuick.Controls
import "../shared_qml"

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
    property string panelBgColor:    "#0e0e0e"

    // ── Playlist state ─────────────────────────────────────────────────────────
    property string playlistTitle:      ""
    property string playlistOwner:      ""
    property string playlistMeta:       ""
    property string coverId:            ""
    property bool   isPublic:           false
    property string playingTrackId:     ""
    property bool   isCurrentlyPlaying: false
    property var    _searchBar:         null
    readonly property string searchText: root._searchBar ? root._searchBar.searchText : ""
    property int    selectedTrkIdx: -1

    // ── Drag-reorder state ─────────────────────────────────────────────────────
    property int    _dragFromIdx:    -1
    property int    _dragToIdx:      -1
    property string _dragGhostTitle: ""
    property string _dragGhostArt:   ""
    property real   _dragGhostY:     0
    property bool   _isDragging:     _dragFromIdx >= 0

    // ── Column widths ──────────────────────────────────────────────────────────
    readonly property int colNum:   44
    property int colTrack:  240
    property int colTitle:  200
    property int colArtist: 160
    property int colFav:     68
    property int colDur:     72
    property int colPlays:   60
    property int colGenre:  140
    property int colAlbum:  160

    // Column visibility (burger menu)
    property bool showTrack:  true
    property bool showTitle:  true
    property bool showArtist: true
    property bool showFav:    true
    property bool showGenre:  true
    property bool showDur:    true
    property bool showPlays:  true
    property bool showAlbum:  true


    // ── Column layout — uniform sequential model ───────────────────────────────
    // All columns have fixed user-adjustable widths. Total always equals the
    // available row width — nothing escapes the left/right walls. _clampCols
    // enforces this on every layout change.
    property var colOrder: ["track", "title", "artist", "album", "fav", "genre", "dur", "plays"]

    readonly property int _rowAvail: Math.max(0, trackList.width - 48 - colNum - 8)

    function _colVis(id) {
        if (id === "track")  return showTrack
        if (id === "title")  return showTitle
        if (id === "artist") return showArtist
        if (id === "fav")    return showFav
        if (id === "genre")  return showGenre
        if (id === "dur")    return showDur
        if (id === "plays")  return showPlays
        if (id === "album")  return showAlbum
        return false
    }
    // Stored (saved) width — always returns the raw property value
    function _colWFixed(id) {
        if (id === "track")  return colTrack
        if (id === "title")  return colTitle
        if (id === "artist") return colArtist
        if (id === "fav")    return colFav
        if (id === "genre")  return colGenre
        if (id === "dur")    return colDur
        if (id === "plays")  return colPlays
        if (id === "album")  return colAlbum
        return 0
    }
    // TRACK is elastic: fills all space not taken by other visible columns.
    // Every other column returns its stored value.
    function _colW(id) {
        if (id === "track") {
            var other = 0
            for (var i = 0; i < colOrder.length; i++) {
                var cid = colOrder[i]
                if (cid !== "track" && _colVis(cid)) other += _colWFixed(cid)
            }
            return Math.max(minColTrack, _rowAvail - other)
        }
        return _colWFixed(id)
    }
    // Rendered width: stored width when visible, 0 when hidden
    function _colRenderW(id) { return _colVis(id) ? _colW(id) : 0 }

    function _colMinW(id) {
        if (id === "track")  return minColTrack
        if (id === "title")  return minColTitle
        if (id === "artist") return minColArtist
        if (id === "fav")    return minColFav
        if (id === "genre")  return minColGenre
        if (id === "dur")    return minColDur
        if (id === "plays")  return minColPlays
        if (id === "album")  return minColAlbum
        return 40
    }
    // Next visible column after id in colOrder, or "" if none
    function _nextVisCol(id) {
        var idx = colOrder.indexOf(id)
        for (var i = idx + 1; i < colOrder.length; i++)
            if (_colVis(colOrder[i])) return colOrder[i]
        return ""
    }
    function _setColW(id, w) {
        if      (id === "track")  colTrack  = w
        else if (id === "title")  colTitle  = w
        else if (id === "artist") colArtist = w
        else if (id === "fav")    colFav    = w
        else if (id === "genre")  colGenre  = w
        else if (id === "dur")    colDur    = w
        else if (id === "plays")  colPlays  = w
        else if (id === "album")  colAlbum  = w
    }
    function _colLabel(id) {
        if (id === "track")  return "TRACK"
        if (id === "title")  return "TITLE"
        if (id === "artist") return "ARTIST"
        if (id === "fav")    return "FAVORITE"
        if (id === "genre")  return "GENRE"
        if (id === "dur")    return "DURATION"
        if (id === "plays")  return "PLAYS"
        if (id === "album")  return "ALBUM"
        return ""
    }
    // X position: sum of rendered widths of all visible columns before id in colOrder
    function _colX(id) {
        var x = 0
        for (var i = 0; i < colOrder.length; i++) {
            if (colOrder[i] === id) return x
            x += _colRenderW(colOrder[i])
        }
        return x
    }

    // Keep all columns within the walls: total always equals available width.
    // TRACK is elastic when visible — only the fixed columns need scaling.
    function _clampCols(listW) {
        if (listW < 100) return
        var avail = Math.max(0, listW - 48 - colNum - 8)
        if (showTrack) {
            // Elastic TRACK absorbs surplus/deficit automatically.
            // Only act if the fixed columns have grown so large that TRACK
            // would be forced below its minimum.
            var fixedTotal = 0
            for (var i = 0; i < colOrder.length; i++) {
                var cid = colOrder[i]
                if (cid !== "track" && _colVis(cid)) fixedTotal += _colWFixed(cid)
            }
            if (avail - fixedTotal >= minColTrack) return
            var target = Math.max(0, avail - minColTrack)
            if (fixedTotal <= 0) return
            var scale = target / fixedTotal
            if (showTitle)  colTitle  = Math.max(20, Math.round(colTitle  * scale))
            if (showArtist) colArtist = Math.max(20, Math.round(colArtist * scale))
            if (showFav)    colFav    = Math.max(20, Math.round(colFav    * scale))
            if (showGenre)  colGenre  = Math.max(20, Math.round(colGenre  * scale))
            if (showDur)    colDur    = Math.max(20, Math.round(colDur    * scale))
            if (showPlays)  colPlays  = Math.max(20, Math.round(colPlays  * scale))
            if (showAlbum)  colAlbum  = Math.max(20, Math.round(colAlbum  * scale))
        } else {
            // No elastic column — all visible columns must sum to avail.
            var total = 0
            for (var j = 0; j < colOrder.length; j++)
                if (_colVis(colOrder[j])) total += _colWFixed(colOrder[j])
            if (total === avail) return
            if (total > avail) {
                var s = avail / total
                if (showTitle)  colTitle  = Math.max(20, Math.round(colTitle  * s))
                if (showArtist) colArtist = Math.max(20, Math.round(colArtist * s))
                if (showFav)    colFav    = Math.max(20, Math.round(colFav    * s))
                if (showGenre)  colGenre  = Math.max(20, Math.round(colGenre  * s))
                if (showDur)    colDur    = Math.max(20, Math.round(colDur    * s))
                if (showPlays)  colPlays  = Math.max(20, Math.round(colPlays  * s))
                if (showAlbum)  colAlbum  = Math.max(20, Math.round(colAlbum  * s))
            } else {
                for (var k = colOrder.length - 1; k >= 0; k--)
                    if (_colVis(colOrder[k])) { _setColW(colOrder[k], _colWFixed(colOrder[k]) + (avail - total)); break }
            }
        }
    }

    onShowTrackChanged:  Qt.callLater(function() { _clampCols(trackList.width) })
    onShowTitleChanged:  Qt.callLater(function() { _clampCols(trackList.width) })
    onShowArtistChanged: Qt.callLater(function() { _clampCols(trackList.width) })
    onShowFavChanged:    Qt.callLater(function() { _clampCols(trackList.width) })
    onShowGenreChanged:  Qt.callLater(function() { _clampCols(trackList.width) })
    onShowDurChanged:    Qt.callLater(function() { _clampCols(trackList.width) })
    onShowPlaysChanged:  Qt.callLater(function() { _clampCols(trackList.width) })
    onShowAlbumChanged:  Qt.callLater(function() { _clampCols(trackList.width) })

    Text { id: _lhRef;     visible: false; text: "X";        font.pixelSize: root.fontSizeSecondary;     font.family: root.fontFamily; renderType: Text.NativeRendering }
    Text { id: _hdrTrack;  visible: false; text: "TRACK";    font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrTitle;  visible: false; text: "TITLE";    font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrArtist; visible: false; text: "ARTIST";   font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrFav;    visible: false; text: "FAVORITE"; font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrGenre;  visible: false; text: "GENRE";    font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrDur;    visible: false; text: "DURATION"; font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrPlays;  visible: false; text: "PLAYS";    font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }
    Text { id: _hdrAlbum;  visible: false; text: "ALBUM";    font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily }

    // Minimum for drag resize = header text width (column can't be dragged narrower than its label)
    readonly property int minColTrack:  Math.ceil(_hdrTrack.implicitWidth)  + 16
    readonly property int minColTitle:  Math.ceil(_hdrTitle.implicitWidth)  + 16
    readonly property int minColArtist: Math.ceil(_hdrArtist.implicitWidth) + 16
    readonly property int minColFav:    Math.ceil(_hdrFav.implicitWidth)    + 16
    readonly property int minColGenre:  Math.ceil(_hdrGenre.implicitWidth)  + 16
    readonly property int minColDur:    Math.ceil(_hdrDur.implicitWidth)    + 16
    readonly property int minColPlays:  Math.ceil(_hdrPlays.implicitWidth)  + 16
    readonly property int minColAlbum:  Math.ceil(_hdrAlbum.implicitWidth)  + 16

    Timer {
        id: colSaveTimer
        interval: 400; repeat: false
        onTriggered: playlistDetailBridge.saveColWidths(root.colTrack, root.colTitle, root.colArtist, root.colFav, root.colDur, root.colPlays, root.colGenre, root.colAlbum)
    }

    Component.onCompleted: {
        var w = playlistDetailBridge.getColWidths()
        root.colTrack  = w[0]
        root.colTitle  = w[1]
        root.colArtist = w[2]
        root.colFav    = w[3]
        root.colDur    = w[4]
        root.colPlays  = w[5]
        root.colGenre  = w[6]
        root.colAlbum  = w[7]
        var v = playlistDetailBridge.getColVisibility()
        root.showTrack  = v[0]
        root.showTitle  = v[1]
        root.showArtist = v[2]
        root.showFav    = v[3]
        root.showGenre  = v[4]
        root.showDur    = v[5]
        root.showPlays  = v[6]
        root.showAlbum  = v[7]
        root.colOrder = playlistDetailBridge.getColOrder()
        // _clampCols is NOT called here — trackList.width is 0 at this point and would
        // destroy saved widths. The onWidthChanged handler below runs it once on first layout.
    }

    // ── Bridge connections ─────────────────────────────────────────────────────
    Connections {
        target: playlistDetailBridge
        function onAccentColorChanged(c)        { root.accentColor       = c }
        function onHoverColorChanged(c)         { root.hoverColor        = c }
        function onSkeletonColorChanged(c)      { root.skeletonColor     = c }
        function onCardBgChanged(c)             { root.cardBgColor       = c }
        function onCardBorderChanged(c)         { root.cardBorderColor   = c }
        function onPanelBgChanged(c)            { root.panelBgColor      = c }
        function onFontSizePrimaryChanged(s)    { root.fontSizePrimary   = s }
        function onFontSizeSecondaryChanged(s)  { root.fontSizeSecondary = s }
        function onFontColorPrimaryChanged(c)   { root.textPrimary       = c }
        function onFontColorSecondaryChanged(c) { root.textSecondary     = c }
        function onFontFamilyChanged(f)         { root.fontFamily        = f }
        function onPlaylistDataChanged(title, owner, meta, covId) {
            root.playlistTitle = title
            root.playlistOwner = owner
            root.playlistMeta  = meta
            root.coverId       = covId
        }
        function onCoverIdChanged(covId)        { root.coverId      = covId }
        function onPublicStateChanged(pub)      { root.isPublic     = pub }
        function onPlayingStatusChanged(tid, playing) {
            root.playingTrackId     = tid
            root.isCurrentlyPlaying = playing
        }
        function onSelectedTrackChanged(idx)    { root.selectedTrkIdx = idx }
        function onScrollToModelRow(row)        { trackList.positionViewAtIndex(row, ListView.Contain) }
        function onScrollToTopOfView()          { trackList.contentY = trackList.originY }
        function onScrollToBottomOfView()       { trackList.contentY = trackList.originY + Math.max(0, trackList.contentHeight - trackList.height) }
        function onShowTrackChanged(v)   { root.showTrack  = v; if (v) root._clampCols(trackList.width) }
        function onShowTitleChanged(v)   { root.showTitle  = v; root._clampCols(trackList.width) }
        function onShowArtistChanged(v)  { root.showArtist = v; if (v) root._clampCols(trackList.width) }
        function onShowFavChanged(v)     { root.showFav    = v; if (v) root._clampCols(trackList.width) }
        function onShowGenreChanged(v)   { root.showGenre  = v; if (v) root._clampCols(trackList.width) }
        function onShowDurChanged(v)     { root.showDur    = v; if (v) root._clampCols(trackList.width) }
        function onShowPlaysChanged(v)   { root.showPlays  = v; if (v) root._clampCols(trackList.width) }
        function onShowAlbumChanged(v)   { root.showAlbum  = v; if (v) root._clampCols(trackList.width) }
    }

    Connections {
        target: playlistDetailBridge.searchCtl
        function onSearchReset()         { if (root._searchBar) root._searchBar.reset() }
        function onSearchOpen()          { if (root._searchBar) root._searchBar.open() }
        function onSearchTextAppend(ch)  { if (root._searchBar) root._searchBar.appendChar(ch) }
        function onSearchTextBackspace() { if (root._searchBar) root._searchBar.backspace() }
        function onSearchClose()         { if (root._searchBar) root._searchBar.close() }
    }

    Connections {
        target: trackList
        function onWidthChanged() { root._clampCols(trackList.width) }
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

    // ── Main scrolling view ────────────────────────────────────────────────────
    ListView {
        id: trackList
        objectName: "trackList"
        anchors.fill: parent
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: false
        clip: true
        pixelAligned: true
        spacing: 0
        cacheBuffer: 1500
        model: playlistTrackModel

        property bool isScrollActive: false
        property bool _initialClampDone: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: trackList.isScrollActive = false }
        onContentYChanged: { isScrollActive = true; scrollHideTimer.restart() }
        onWidthChanged: {
            if (!_initialClampDone && width >= 100) { _initialClampDone = true; root._clampCols(width) }
        }

        MomentumScroll {
            target: trackList
            minContentY: trackList.originY
            maxContentY: trackList.originY + Math.max(0, trackList.contentHeight - trackList.height)
        }

        // ── HEADER: playlist info card + tracklist toolbar/column headers ──────
        header: Item {
            id: pageHeader
            width: trackList.width
            height: headerArea.y + headerArea.height + 10 + cardLid.height

            Component.onCompleted: root._searchBar = trackSearchBar

            // ── HEADER CARD ───────────────────────────────────────────────────
            Item {
                id: headerArea
                x: 12; y: 12
                width: parent.width - 24
                height: Math.max(coverItem.artSize, metaCol.implicitHeight) + 56

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
                    height: Math.max(coverItem.artSize, metaCol.implicitHeight)

                    // ── Cover art ─────────────────────────────────────────────
                    Item {
                        id: coverItem
                        readonly property int artSize:      264
                        readonly property int shadowPad:     30
                        readonly property int providerSize: artSize + shadowPad * 2
                        width:  artSize
                        height: artSize
                        anchors.left:           parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        property bool coverHov: false

                        Rectangle {
                            anchors.fill: parent; radius: 10
                            color: root.skeletonColor
                            visible: root.coverId === ""
                        }

                        Image {
                            x: -coverItem.shadowPad; y: -coverItem.shadowPad
                            width: coverItem.providerSize; height: coverItem.providerSize
                            source: root.coverId !== "" ? "image://playlistdetailcover/" + root.coverId : ""
                            mipmap: true; cache: false; smooth: true
                            visible: root.coverId !== ""
                        }

                        Canvas {
                            id: artCanvas
                            anchors.fill: parent

                            property real artZoom: coverItem.coverHov ? 1.08 : 1.0
                            property string artUrl: root.coverId !== ""
                                ? "image://playlistdetailcover/art/" + root.coverId : ""

                            Behavior on artZoom { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
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
                                ctx.moveTo(r, 0); ctx.lineTo(width - r, 0)
                                ctx.arcTo(width, 0, width, r, r)
                                ctx.lineTo(width, height - r)
                                ctx.arcTo(width, height, width - r, height, r)
                                ctx.lineTo(r, height)
                                ctx.arcTo(0, height, 0, height - r, r)
                                ctx.lineTo(0, r)
                                ctx.arcTo(0, 0, r, 0, r)
                                ctx.closePath(); ctx.clip()
                                var zw = width  * artZoom
                                var zh = height * artZoom
                                ctx.drawImage(artUrl, -(zw - width) / 2, -(zh - height) / 2, zw, zh)
                                ctx.restore()
                            }
                        }

                        MouseArea {
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onEntered: coverItem.coverHov = true
                            onExited:  coverItem.coverHov = false
                        }
                    }

                    // ── Metadata ───────────────────────────────────────────────
                    Column {
                        id: metaCol
                        anchors.left:       coverItem.right
                        anchors.leftMargin: 28
                        anchors.right:      parent.right
                        anchors.top:        parent.top
                        anchors.topMargin:  16
                        spacing: 6

                        Text {
                            text: "PLAYLIST"
                            color: root.textSecondary
                            font.pixelSize: 11; font.bold: true; font.letterSpacing: 1.5
                            font.family: root.fontFamily
                        }

                        Text {
                            width: parent.width
                            text: root.playlistTitle
                            color: root.textPrimary
                            font.pixelSize: 28; font.bold: true
                            wrapMode: Text.WordWrap
                            font.family: root.fontFamily
                        }

                        Text {
                            visible: root.playlistOwner !== ""
                            text: "By " + root.playlistOwner
                            color: root.accentColor
                            font.pixelSize: root.fontSizePrimary + 1
                            font.family: root.fontFamily
                        }

                        Text {
                            text: root.playlistMeta
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.bold: true
                            font.family: root.fontFamily
                            visible: root.playlistMeta !== "" && root.playlistMeta !== "Loading..."
                        }
                        Text {
                            text: "Loading…"
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            font.family: root.fontFamily
                            visible: root.playlistMeta === "Loading..."
                        }

                        // ── Action buttons ─────────────────────────────────────
                        Row {
                            spacing: 10
                            topPadding: 16

                            // Play — ring button matching album detail style
                            Item {
                                id: playCircle
                                width: 58; height: 58

                                Image {
                                    readonly property int sp: 20
                                    x: -sp; y: -sp
                                    width: parent.width + sp * 2; height: parent.height + sp * 2
                                    source: "image://playlistdetailcover/btn/" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                    opacity: playHover.containsMouse ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                }

                                Rectangle {
                                    anchors.fill: parent; anchors.margins: 2.5
                                    radius: width / 2; color: root.cardBgColor
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
                                    Connections { target: root; function onAccentColorChanged() { ringCanvas.requestPaint() } }
                                }

                                Image {
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: 1
                                    width: 16; height: 16
                                    source: "image://playlisticons/play_" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }

                                MouseArea {
                                    id: playHover
                                    anchors.fill: parent; hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: playlistDetailBridge.playClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); playlistDetailBridge.showTooltip("Play All (Ctrl+Enter)", a.x, a.y, b.y) }
                                    onExited:  playlistDetailBridge.hideTooltip()
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
                                    source: "image://playlisticons/shuffle_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: shuffleHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: playlistDetailBridge.shuffleClicked()
                                    onEntered: { var a = mapToGlobal(width/2, -4); var b = mapToGlobal(width/2, height+4); playlistDetailBridge.showTooltip("Shuffle", a.x, a.y, b.y) }
                                    onExited:  playlistDetailBridge.hideTooltip()
                                }
                            }

                            // Public / private toggle pill
                            Item {
                                id: publicToggleBtn
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                property bool hov: false

                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.hoverColor
                                    opacity: parent.hov ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }

                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 28; height: 16; radius: 8
                                    color: root.isPublic ? root.accentColor : root.textSecondary
                                    opacity: 0.85
                                    Behavior on color { ColorAnimation { duration: 150 } }

                                    Rectangle {
                                        width: 10; height: 10; radius: 5; color: "white"
                                        anchors.verticalCenter: parent.verticalCenter
                                        x: root.isPublic ? parent.width - width - 3 : 3
                                        Behavior on x { NumberAnimation { duration: 150 } }
                                    }
                                }

                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onEntered: {
                                        publicToggleBtn.hov = true
                                        var a = mapToGlobal(width/2, -4)
                                        var b = mapToGlobal(width/2, height+4)
                                        playlistDetailBridge.showTooltip(
                                            root.isPublic ? "Public — click to make private"
                                                          : "Private — click to make public",
                                            a.x, a.y, b.y)
                                    }
                                    onExited:  { publicToggleBtn.hov = false; playlistDetailBridge.hideTooltip() }
                                    onClicked: playlistDetailBridge.togglePublic()
                                }
                            }
                        }
                    }
                }
            }

            // ── TRACKLIST CARD LID (toolbar + column headers, rounded top) ─────
            Rectangle {
                id: cardLid
                x: 12
                y: headerArea.y + headerArea.height + 10
                width: parent.width - 24
                height: 12 + toolbarRow.height + colHeader.height
                color: root.cardBgColor
                border.color: root.cardBorderColor
                border.width: 1
                topLeftRadius: 10;    topRightRadius: 10
                bottomLeftRadius: 0;  bottomRightRadius: 0
            }
            Rectangle {
                x: 13; y: cardLid.y + cardLid.height - 1
                width: parent.width - 26; height: 1
                color: root.cardBgColor
            }

            // ── TOOLBAR ROW ───────────────────────────────────────────────────
            Item {
                id: toolbarRow
                x: 20; y: cardLid.y + 12
                width: parent.width - 40; height: 36

                // Burger button — column picker (right of search)
                Item {
                    id: burgerBtn
                    anchors.right:          parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 32; height: 32

                    Rectangle {
                        anchors.fill: parent; radius: 4
                        color: burgerHov.containsMouse ? root.hoverColor : "transparent"
                    }
                    Image {
                        anchors.centerIn: parent; width: 18; height: 18
                        source: "image://albumicons/burger_" + root.accentColor.replace("#","")
                        cache: false; mipmap: true; smooth: true
                    }
                    MouseArea {
                        id: burgerHov
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            var gp = mapToGlobal(0, height)
                            playlistDetailBridge.burgerClicked(gp.x, gp.y)
                        }
                    }
                }

                SearchBar {
                    id: trackSearchBar
                    anchors.right:  burgerBtn.left
                    anchors.top:    parent.top
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
                    onOpened: playlistDetailBridge.searchCtl.setSearchActive(true)
                    onClosed: playlistDetailBridge.searchCtl.setSearchActive(false)
                }
            }

            // ── COLUMN HEADERS ────────────────────────────────────────────────
            Item {
                id: colHeader
                x: 20; y: toolbarRow.y + toolbarRow.height
                width: parent.width - 40; height: 36

                // ── column drag-to-reorder state ──────────────────────────────
                property int  _dragFrom: -1
                property int  _dragTo:   -1
                property real _ghostX:   0
                readonly property bool _dragging: _dragFrom >= 0

                Item {
                    id: hdrRow
                    x: 4; height: parent.height; width: parent.width - 8

                    // # — always first, never reorderable
                    Text {
                        x: 0; width: root.colNum; height: parent.height; text: "#"
                        color: root.textSecondary; font.pixelSize: root.fontSizeSecondary - 1
                        font.bold: true; font.letterSpacing: 0.8
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        font.family: root.fontFamily
                    }

                    Repeater {
                        model: root.colOrder
                        delegate: Item {
                            id: hdrCell
                            required property string modelData
                            required property int index

                            visible: root._colVis(modelData)
                            x:       root.colNum + root._colX(modelData)
                            width:   root._colRenderW(modelData)
                            height:  hdrRow.height
                            // fade out original while dragging it
                            opacity: colHeader._dragFrom === index ? 0 : 1

                            // ── label ─────────────────────────────────────────
                            Text {
                                visible: modelData !== "fav"
                                anchors.fill: parent; leftPadding: 4
                                text: root._colLabel(modelData); color: root.textSecondary
                                font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8
                                horizontalAlignment: (modelData === "dur" || modelData === "plays") ? Text.AlignHCenter : Text.AlignLeft
                                verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily
                            }
                            Item {
                                visible: modelData === "fav"; anchors.fill: parent
                                Text { anchors.fill: parent; text: "FAVORITE"; color: root.textSecondary; font.pixelSize: root.fontSizeSecondary - 1; font.bold: true; font.letterSpacing: 0.8; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.family: root.fontFamily }
                            }

                            // ── resize handle ─────────────────────────────────
                            // TRACK is elastic and absorbs all width changes —
                            // each handle just changes its own column freely.
                            // TRACK has no handle (it fills remaining space).
                            MouseArea {
                                visible: hdrCell.modelData !== "track" &&
                                         root._nextVisCol(hdrCell.modelData) !== ""
                                x: parent.width - 6
                                y: 0; width: 12; height: parent.height; z: 10
                                cursorShape: Qt.SizeHorCursor; hoverEnabled: true
                                property real _pressX:          0
                                property int  _pressW:          0
                                property int  _pressTrackSlack: 0
                                onPressed: {
                                    _pressX          = mapToItem(null, mouseX, 0).x
                                    _pressW          = root._colWFixed(hdrCell.modelData)
                                    // How much TRACK can give before hitting its minimum
                                    _pressTrackSlack = root.showTrack
                                        ? Math.max(0, root._colW("track") - root.minColTrack)
                                        : 0
                                }
                                onPositionChanged: if (pressed) {
                                    var delta = mapToItem(null, mouseX, 0).x - _pressX
                                    var minW  = root._colMinW(hdrCell.modelData)
                                    root._setColW(hdrCell.modelData,
                                        Math.max(minW, Math.min(_pressW + _pressTrackSlack, _pressW + delta)))
                                    colSaveTimer.restart()
                                }
                                Rectangle {
                                    anchors.centerIn: parent; width: 2; height: parent.height - 22; color: root.textSecondary
                                    opacity: parent.containsMouse ? 0.55 : 0.15
                                }
                            }

                            // ── drag-to-reorder (also handles FAV header click) ──
                            // No propagateComposedEvents so this MA holds exclusive grab on press,
                            // ensuring onReleased always fires and the ghost never gets stuck.
                            MouseArea {
                                anchors.fill: parent
                                anchors.rightMargin: 6
                                anchors.leftMargin:  0
                                z: 5
                                cursorShape: _active ? Qt.ClosedHandCursor
                                           : (modelData === "fav" || modelData === "album" ? Qt.PointingHandCursor : Qt.OpenHandCursor)
                                hoverEnabled: true

                                property bool _active: false
                                property real _startX: 0

                                onPressed: mouse => { _startX = mapToItem(colHeader, mouseX, 0).x; _active = false; mouse.accepted = true }

                                onPositionChanged: mouse => {
                                    if (mouse.buttons === Qt.NoButton) return
                                    var gx = mapToItem(colHeader, mouseX, 0).x
                                    if (!_active && Math.abs(gx - _startX) > 5) {
                                        _active = true
                                        colHeader._dragFrom = hdrCell.index
                                        colHeader._dragTo   = hdrCell.index
                                    }
                                    if (_active) {
                                        colHeader._ghostX = gx - root._colRenderW(hdrCell.modelData) / 2
                                        var cx = 4 + root.colNum; var best = root.colOrder.length
                                        for (var i = 0; i < root.colOrder.length; i++) {
                                            var cid = root.colOrder[i]
                                            if (!root._colVis(cid)) continue
                                            var w = root._colRenderW(cid)
                                            if (gx < cx + w * 0.5) { best = i; break }
                                            cx += w; best = i + 1
                                        }
                                        colHeader._dragTo = best
                                    }
                                }

                                onReleased: {
                                    var from = colHeader._dragFrom
                                    var to   = colHeader._dragTo
                                    var wasDrag = _active
                                    _active = false; colHeader._dragFrom = -1; colHeader._dragTo = -1
                                    if (wasDrag && from >= 0 && to !== from && to !== from + 1) {
                                        var newOrder = root.colOrder.slice()
                                        var moved = newOrder.splice(from, 1)[0]
                                        newOrder.splice(to > from ? to - 1 : to, 0, moved)
                                        root.colOrder = newOrder
                                        playlistDetailBridge.saveColOrder(root.colOrder)
                                    } else if (!wasDrag && hdrCell.modelData === "fav") {
                                        playlistDetailBridge.favHeaderClicked()
                                    } else if (!wasDrag && hdrCell.modelData === "album") {
                                        playlistDetailBridge.albumHeaderClicked()
                                    }
                                }

                                onCanceled: { _active = false; colHeader._dragFrom = -1; colHeader._dragTo = -1 }
                            }
                        }
                    }
                }

                // ── drag ghost ────────────────────────────────────────────────
                Rectangle {
                    visible: colHeader._dragging && colHeader._dragFrom >= 0 && colHeader._dragFrom < root.colOrder.length
                    x: Math.max(4 + root.colNum, Math.min(colHeader._ghostX, parent.width - width - 4))
                    y: 2; height: parent.height - 4
                    width: colHeader._dragFrom >= 0 && colHeader._dragFrom < root.colOrder.length
                           ? root._colRenderW(root.colOrder[colHeader._dragFrom]) : 0
                    color: root.panelBgColor; opacity: 0.93
                    border.color: root.accentColor; border.width: 1; radius: 3; z: 20
                    Text {
                        anchors.centerIn: parent
                        text: colHeader._dragFrom >= 0 && colHeader._dragFrom < root.colOrder.length
                              ? root._colLabel(root.colOrder[colHeader._dragFrom]) : ""
                        color: root.accentColor; font.pixelSize: root.fontSizeSecondary - 1
                        font.bold: true; font.letterSpacing: 0.8; font.family: root.fontFamily
                    }
                }

                // ── drop indicator ────────────────────────────────────────────
                Rectangle {
                    visible: colHeader._dragging && colHeader._dragTo !== colHeader._dragFrom && colHeader._dragTo !== colHeader._dragFrom + 1
                    x: {
                        var cx = 4 + root.colNum
                        var to = colHeader._dragTo
                        for (var i = 0; i < to && i < root.colOrder.length; i++)
                            if (root._colVis(root.colOrder[i])) cx += root._colRenderW(root.colOrder[i])
                        return cx - 1
                    }
                    y: 2; width: 2; height: parent.height - 4; color: root.accentColor; z: 21
                }
            }
        }

        // ── FOOTER: bottom rounded corner ─────────────────────────────────────
        footer: Item {
            width: trackList.width
            height: 12 + 32

            Rectangle {
                x: 12; y: 0
                width: parent.width - 24; height: 12
                color: root.cardBgColor
                border.color: root.cardBorderColor; border.width: 1
                topLeftRadius: 0;     topRightRadius: 0
                bottomLeftRadius: 10; bottomRightRadius: 10
            }
            Rectangle { x: 13; y: 0; width: parent.width - 26; height: 1; color: root.cardBgColor }
        }

        // ── TRACK ROWS ────────────────────────────────────────────────────────
        delegate: Item {
            id: trackRow
            width: trackList.width

            property int    trkIdx:     model.trackIdx
            property int    rowIdx:     index
            property string trkId:      model.trackId      || ""
            property string trkNum:     model.trackNumber  || ""
            property string trkTitle:   model.trackTitle   || ""
            property string artName:    model.artistName   || ""
            property bool   isFav:      model.isFavorite
            property string durStr:     model.durationStr  || ""
            property string playsStr:   model.playCountStr || ""
            property string genreStr:   model.trackGenre   || ""
            property string coverArtId: model.coverArtId   || ""
            property string albName:    model.albumName    || ""
            property string albId:     model.albumId      || ""

            property bool rowHov:     false
            property bool isSelected: trkIdx === root.selectedTrkIdx
            property bool isPlaying:  root.isCurrentlyPlaying && trkId === root.playingTrackId
            property bool matchSearch: root.searchText === ""
                || trkTitle.toLowerCase().indexOf(root.searchText.toLowerCase()) >= 0
                || artName.toLowerCase().indexOf(root.searchText.toLowerCase())  >= 0
                || genreStr.toLowerCase().indexOf(root.searchText.toLowerCase()) >= 0
                || albName.toLowerCase().indexOf(root.searchText.toLowerCase())  >= 0

            height: matchSearch ? 58 : 0
            visible: height > 0
            clip: false
            opacity: root._isDragging && root._dragFromIdx === rowIdx ? 0.3 : 1.0
            Behavior on opacity { NumberAnimation { duration: 100 } }

            // Card body continuation
            Rectangle { x: 12; y: 0; width: parent.width - 24; height: parent.height; color: root.cardBgColor }
            Rectangle { x: 12; y: 0; width: 1; height: parent.height; color: root.cardBorderColor }
            Rectangle { x: parent.width - 13; y: 0; width: 1; height: parent.height; color: root.cardBorderColor }

            Item {
                anchors.fill: parent

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

                Item {
                    x: 24; y: 0
                    width: parent.width - 48; height: parent.height

                    // # / playing bars / drag grip — always at x:0
                    Item {
                        id: numCol
                        x: 0; width: root.colNum; height: parent.height

                        Text {
                            visible: isPlaying ? false : (!rowHov || (root._isDragging && root._dragFromIdx !== rowIdx))
                            anchors.centerIn: parent
                            text: trackRow.trkNum; color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                        }
                        Row {
                            visible: !isPlaying && (rowHov || (root._isDragging && root._dragFromIdx === rowIdx))
                            anchors.centerIn: parent; spacing: 3
                            Repeater {
                                model: 2
                                delegate: Column {
                                    spacing: 3
                                    Repeater {
                                        model: 3
                                        delegate: Rectangle {
                                            width: 2.5; height: 2.5; radius: 1.25
                                            color: root._isDragging && root._dragFromIdx === rowIdx ? root.accentColor : root.textSecondary
                                            opacity: 0.7
                                        }
                                    }
                                }
                            }
                        }
                        DragHandler {
                            target: null; enabled: !isPlaying; dragThreshold: 6
                            property int  _startIdx:    -1
                            property real _pressSceneY: 0
                            onActiveChanged: {
                                if (active) {
                                    _startIdx = trackRow.rowIdx; _pressSceneY = centroid.scenePosition.y
                                    root._dragFromIdx = _startIdx; root._dragToIdx = _startIdx
                                    root._dragGhostTitle = trackRow.trkTitle; root._dragGhostArt = trackRow.artName
                                    root._dragGhostY = centroid.scenePosition.y
                                } else if (root._dragFromIdx === _startIdx) {
                                    if (root._dragFromIdx !== root._dragToIdx)
                                        playlistDetailBridge.reorderTrack(root._dragFromIdx, root._dragToIdx)
                                    root._dragFromIdx = -1; root._dragToIdx = -1; _startIdx = -1
                                }
                            }
                            onCentroidChanged: {
                                if (!active || root._dragFromIdx !== _startIdx) return
                                var sy = centroid.scenePosition.y
                                var idx = trackList.indexAt(0, sy + trackList.contentY)
                                if (idx >= 0) root._dragToIdx = idx
                                root._dragGhostY = sy
                            }
                        }
                        HoverHandler { cursorShape: isPlaying ? Qt.ArrowCursor : (root._isDragging ? Qt.ClosedHandCursor : Qt.OpenHandCursor) }
                        Row {
                            visible: isPlaying; anchors.centerIn: parent; spacing: 3
                            Repeater {
                                model: [300, 420, 340]
                                delegate: Rectangle {
                                    required property int modelData
                                    required property int index
                                    width: 3; radius: 1.5; color: root.accentColor; height: 4
                                    SequentialAnimation on height {
                                        loops: Animation.Infinite; running: isPlaying && root.isCurrentlyPlaying
                                        NumberAnimation { from: 4; to: 4 + (index + 1) * 4; duration: modelData; easing.type: Easing.InOutSine }
                                        NumberAnimation { from: 4 + (index + 1) * 4; to: 4; duration: modelData; easing.type: Easing.InOutSine }
                                    }
                                }
                            }
                        }
                    }

                    // Track (cover art + title + artist combined) — x driven by colOrder
                    Item {
                        visible: root.showTrack
                        x: root.colNum + root._colX("track"); width: root._colRenderW("track"); height: parent.height
                        Row {
                            x: 4; height: parent.height; width: parent.width - 4; spacing: 8
                            Rectangle {
                                width: 52; height: 52; radius: 3
                                anchors.verticalCenter: parent.verticalCenter; color: root.cardBorderColor
                                Image {
                                    anchors.fill: parent
                                    source: trackRow.coverArtId ? "image://playlisttrackcovers/" + trackRow.coverArtId : ""
                                    fillMode: Image.PreserveAspectCrop; cache: true; smooth: true; asynchronous: true
                                }
                            }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 52 - 8 - 4; spacing: 1
                                Text { width: parent.width; text: trackRow.trkTitle; color: isPlaying ? root.accentColor : root.textPrimary; font.pixelSize: root.fontSizePrimary; font.bold: true; elide: Text.ElideRight; font.family: root.fontFamily }
                                Item {
                                    width: parent.width; height: artistLbl.implicitHeight
                                    property bool hov: false
                                    Text {
                                        id: artistLbl
                                        width: parent.width
                                        text: trackRow.artName
                                        color: parent.hov ? root.accentColor : root.textSecondary
                                        font.pixelSize: root.fontSizeSecondary; elide: Text.ElideRight; font.family: root.fontFamily
                                        Rectangle { visible: parent.parent.hov; y: parent.baselineOffset + 2; width: parent.paintedWidth; height: 1; color: parent.color }
                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true; onExited: parent.hov = false
                                        onClicked: mouse => { playlistDetailBridge.trackArtistClicked(trackRow.artName); mouse.accepted = true }
                                    }
                                }
                            }
                        }
                    }

                    // Title — x driven by colOrder
                    Text {
                        visible: root.showTitle
                        x: root.colNum + root._colX("title"); width: root._colRenderW("title"); height: parent.height
                        verticalAlignment: Text.AlignVCenter; leftPadding: 4
                        text: trackRow.trkTitle; color: isPlaying ? root.accentColor : root.textPrimary
                        font.pixelSize: root.fontSizePrimary; font.bold: true; elide: Text.ElideRight; font.family: root.fontFamily
                    }

                    // Artist — x driven by colOrder
                    Item {
                        visible: root.showArtist
                        x: root.colNum + root._colX("artist"); width: root.colArtist; height: parent.height; clip: true
                        Flow {
                            id: flowArt; x: 4; width: parent.width - 4
                            anchors.verticalCenter: parent.verticalCenter; spacing: 0
                            height: Math.min(implicitHeight, root._twoLineH); clip: true
                            Repeater {
                                model: trackRow.artName.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/).filter(function(p) { return p !== "" })
                                delegate: Text {
                                    property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                                    property bool hov: false
                                    text: modelData; opacity: isSep ? 0.4 : 1.0
                                    color: !isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                                    Rectangle { visible: !parent.isSep && parent.hov; y: parent.baselineOffset + 2; width: parent.paintedWidth; height: 1; color: parent.color }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true; enabled: !parent.isSep; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true; onExited: parent.hov = false
                                        onClicked: mouse => { playlistDetailBridge.trackArtistClicked(parent.text); mouse.accepted = true }
                                    }
                                }
                            }
                        }
                        Text { visible: flowArt.implicitHeight > flowArt.height; text: "…"; anchors.right: parent.right; anchors.rightMargin: 4; y: flowArt.y + flowArt.height - implicitHeight; color: root.textSecondary; font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily }
                    }

                    // Album — x driven by colOrder
                    Item {
                        visible: root.showAlbum
                        x: root.colNum + root._colX("album"); width: root._colRenderW("album"); height: parent.height; clip: true
                        property bool hov: false
                        Text {
                            x: 4; width: parent.width - 4
                            anchors.verticalCenter: parent.verticalCenter
                            text: trackRow.albName
                            color: parent.hov ? root.accentColor : root.textSecondary
                            font.pixelSize: root.fontSizeSecondary; elide: Text.ElideRight; font.family: root.fontFamily
                            Rectangle { visible: parent.parent.hov; y: parent.baselineOffset + 2; width: parent.paintedWidth; height: 1; color: parent.color }
                        }
                        MouseArea {
                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onEntered: parent.hov = true; onExited: parent.hov = false
                            onClicked: mouse => { playlistDetailBridge.trackAlbumClicked(trackRow.albId, trackRow.albName); mouse.accepted = true }
                        }
                    }

                    // Favorite — x driven by colOrder
                    Item {
                        visible: root.showFav
                        x: root.colNum + root._colX("fav"); width: root.colFav; height: parent.height
                        Image {
                            anchors.centerIn: parent; width: 16; height: 16
                            source: trackRow.isFav ? "image://playlisticons/heart_filled_E91E63" : "image://playlisticons/heart_" + (favHov.containsMouse ? root.accentColor.replace("#","") : root.textSecondary.replace("#",""))
                            cache: false; mipmap: true; smooth: true
                            scale: favHov.containsMouse ? 1.2 : 1.0
                            Behavior on scale { NumberAnimation { duration: 100 } }
                        }
                        MouseArea { id: favHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; z: 5; onClicked: mouse => { playlistDetailBridge.trackFavoriteClicked(trackRow.trkIdx); mouse.accepted = true } }
                    }

                    // Genre — x driven by colOrder
                    Item {
                        visible: root.showGenre
                        x: root.colNum + root._colX("genre"); width: root.colGenre; height: parent.height; clip: true
                        Flow {
                            id: flowGen; x: 4; width: parent.width - 4
                            anchors.verticalCenter: parent.verticalCenter; spacing: 0
                            height: Math.min(implicitHeight, root._twoLineH); clip: true
                            Repeater {
                                model: trackRow.genreStr.split(/( • )/).filter(function(p) { return p !== "" })
                                delegate: Text {
                                    property bool isSep: modelData === " • "
                                    property bool hov: false
                                    text: modelData; opacity: isSep ? 0.4 : 1.0
                                    color: !isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                                    Rectangle { visible: !parent.isSep && parent.hov; y: parent.baselineOffset + 2; width: parent.paintedWidth; height: 1; color: parent.color }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true; enabled: !parent.isSep; cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true; onExited: parent.hov = false
                                        onClicked: mouse => { playlistDetailBridge.trackGenreClicked(parent.text); mouse.accepted = true }
                                    }
                                }
                            }
                        }
                        Text { visible: flowGen.implicitHeight > flowGen.height; text: "…"; anchors.right: parent.right; anchors.rightMargin: 4; y: flowGen.y + flowGen.height - implicitHeight; color: root.textSecondary; font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily }
                    }

                    // Duration — x driven by colOrder
                    Text {
                        visible: root.showDur
                        x: root.colNum + root._colX("dur"); width: root.colDur; height: parent.height
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: trackRow.durStr; color: isPlaying ? root.accentColor : root.textSecondary
                        font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                    }

                    // Plays — x driven by colOrder
                    Text {
                        visible: root.showPlays
                        x: root.colNum + root._colX("plays"); width: root.colPlays; height: parent.height
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                        text: trackRow.playsStr; color: root.textSecondary
                        font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily
                    }
                }


                HoverHandler { onHoveredChanged: trackRow.rowHov = hovered }
                MouseArea {
                    anchors.fill: parent; hoverEnabled: false
                    acceptedButtons: Qt.LeftButton | Qt.RightButton; z: 2
                    propagateComposedEvents: true
                    onClicked: mouse => {
                        if (mouse.button === Qt.RightButton) {
                            var gp = mapToGlobal(mouse.x, mouse.y)
                            playlistDetailBridge.trackContextMenuRequested(trackRow.trkIdx, gp.x, gp.y)
                            mouse.accepted = true
                        } else {
                            mouse.accepted = false
                        }
                    }
                    onDoubleClicked: playlistDetailBridge.trackPlayClicked(trackRow.trkIdx)
                }
            }
        }
    }

    // ── Drag-reorder overlay (visual-only — no MouseArea; grab stays in the
    //    grip's MouseArea so it is never released mid-drag) ─────────────────────
    Item {
        id: dragOverlay
        anchors.fill: trackList
        z: 200
        visible: root._isDragging
        // root coords == dragOverlay coords (both fill the same parent)

        // Ghost row
        Rectangle {
            id: ghostRow
            x: 12
            y: Math.max(0, Math.min(dragOverlay.height - 52, root._dragGhostY - 26))
            width: parent.width - 24; height: 52
            color: root.cardBgColor
            border.color: root.accentColor; border.width: 1
            radius: 4; opacity: 0.93

            Row {
                x: 24; y: 0; height: parent.height; width: ghostRow.width - 48

                Item {
                    width: root.colNum; height: parent.height
                    Row {
                        anchors.centerIn: parent; spacing: 3
                        Repeater {
                            model: 2
                            delegate: Column {
                                spacing: 3
                                Repeater {
                                    model: 3
                                    delegate: Rectangle { width: 2.5; height: 2.5; radius: 1.25; color: root.accentColor; opacity: 0.7 }
                                }
                            }
                        }
                    }
                }
                Text {
                    width: root._colRenderW("title") > 0 ? root._colRenderW("title") : root._colRenderW("track")
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter; leftPadding: 4
                    text: root._dragGhostTitle; color: root.accentColor
                    font.pixelSize: root.fontSizePrimary; font.bold: true
                    elide: Text.ElideRight; font.family: root.fontFamily
                }
                Text {
                    width: root.colArtist; height: parent.height
                    verticalAlignment: Text.AlignVCenter; leftPadding: 4
                    text: root._dragGhostArt; color: root.textSecondary
                    font.pixelSize: root.fontSizeSecondary
                    elide: Text.ElideRight; font.family: root.fontFamily
                }
            }
        }

        // Insertion line with leading dot (matches queue panel drop indicator style)
        Item {
            visible: root._dragToIdx !== root._dragFromIdx
            x: 20; width: parent.width - 40; height: 8
            y: {
                var hdrH = trackList.headerItem ? trackList.headerItem.height : 0
                var rowY  = trackList.originY + hdrH + root._dragToIdx * 52 - trackList.contentY
                return (root._dragToIdx > root._dragFromIdx ? rowY + 52 : rowY) - 4
            }
            // dot
            Rectangle {
                width: 8; height: 8; radius: 4; color: root.accentColor
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
            }
            // line
            Rectangle {
                anchors.left: parent.left; anchors.leftMargin: 8
                anchors.right: parent.right
                height: 2; color: root.accentColor
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
