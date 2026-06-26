import QtQuick
import QtQuick.Controls
import "../shared_qml"

Rectangle {
    id: root
    color: "transparent"
    focus: true

    // ── Theme ────────────────────────────────────────────────────────────
    property string accentColor:      "#cccccc"
    property string hoverColor:       "#555555"
    property string textPrimary:      "#dddddd"
    property string textSecondary:    "#888888"
    property int    fontSizePrimary:    17
    property int    fontSizeSecondary:  12
    property string fontFamily:       ""
    property string skeletonColor:    "#282828"
    property string cardBgColor:      "#1e1e1e"
    property string cardBorderColor:  "#2a2a2a"
    property string panelBgColor:     "#0e0e0e"

    // ── Track-card state ─────────────────────────────────────────────────
    property bool   noTrack:        true
    property string trackTitle:     ""
    property string coverKey:       ""    // cache key for image://nowplayingpix/cover:<key>
    property string coverGlowColor: ""    // hex, for image://nowplayingglow/glow/<hex>
    property var    artistTokens:   []    // [{text, isSep}]
    property string albumName:      ""
    property string albumId:        ""
    property string yearText:       ""
    property var    genreTokens:    []    // [{text, isSep}]
    property string infoText:       ""
    property bool   isFavorite:     false
    property string lastfmUrl:      ""
    property string wikiUrl:        ""

    // ── Album-tracks card state ──────────────────────────────────────────
    property var    albumTracks:      []   // [{num, title, duration, isCurrent}]
    property int    albumWindowStart: 0
    property int    albumWindowEnd:   0
    property string albumMeta:        ""
    property bool   albumLoading:     true

    // ── Top-songs card state ─────────────────────────────────────────────
    property var    topSongs:        []    // [{title, album, duration, isCurrent}]
    property bool   topSongsLoading: true
    property string topSongsArtist:  ""
    property int    topSongsPageIdx: 0
    property int    topSongsPageCnt: 0

    // ── Artist card state ─────────────────────────────────────────────────
    property string artistPageName:  ""
    property int    artistPageIdx:   0
    property int    artistPageCnt:   0
    property string artistPhotoKey:  ""   // cache key for image://nowplayingpix/artist:<key>
    property string artistBio:       ""
    property var    similarArtists:  []    // [name, ...]

    // ── Tour card state ──────────────────────────────────────────────────
    property bool   bandsintownEnabled: false
    property var    tourEvents:         []   // [{month, day, venue, place, url}]
    readonly property int tourLimit: 5       // must match TOUR_LIMIT in now_playing_info.py

    // ── Bridge connections ───────────────────────────────────────────────
    Connections {
        target: nowPlayingBridge
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

        function onNoTrackChanged(v)            { root.noTrack           = v }
        function onTrackTitleChanged(t)         { root.trackTitle        = t }
        function onCoverKeyChanged(k)           { root.coverKey          = k }
        function onCoverGlowColorChanged(c)     { root.coverGlowColor    = c }
        function onArtistTokensChanged(v)       { root.artistTokens      = v }
        function onAlbumNameChanged(n)          { root.albumName         = n }
        function onAlbumIdChanged(i)            { root.albumId           = i }
        function onYearTextChanged(y)           { root.yearText          = y }
        function onGenreTokensChanged(v)        { root.genreTokens       = v }
        function onInfoTextChanged(t)           { root.infoText          = t }
        function onIsFavoriteChanged(v)         { root.isFavorite        = v }
        function onLastfmUrlChanged(u)          { root.lastfmUrl         = u }
        function onWikiUrlChanged(u)            { root.wikiUrl           = u }

        function onAlbumTracksChanged(rows, ws, we) {
            root.albumTracks      = rows
            root.albumWindowStart = ws
            root.albumWindowEnd   = we
            root.albumLoading     = false
            albumShowAll          = false
        }
        function onAlbumMetaChanged(m)          { root.albumMeta         = m }
        function onAlbumLoadingChanged(v)       { root.albumLoading      = v }

        function onTopSongsChanged(rows)        { root.topSongs = rows; root.topSongsLoading = false }
        function onTopSongsLoadingChanged(v)    { root.topSongsLoading   = v }
        function onTopSongsArtistChanged(n)     { root.topSongsArtist    = n }
        function onTopSongsPageChanged(idx, cnt){ root.topSongsPageIdx = idx; root.topSongsPageCnt = cnt }

        function onArtistPageChanged(idx, cnt)  { root.artistPageIdx = idx; root.artistPageCnt = cnt }
        function onArtistPageNameChanged(n)     { root.artistPageName    = n }
        function onArtistPhotoKeyChanged(k)     { root.artistPhotoKey    = k }
        function onArtistBioChanged(b)          { root.artistBio = b; bioExpanded = false }
        function onSimilarArtistsChanged(v)     { root.similarArtists    = v }

        function onBandsintownEnabledChanged(v) { root.bandsintownEnabled = v }
        function onTourEventsChanged(v)         { root.tourEvents = v; tourShowAll = false }
    }

    property bool albumShowAll: false
    property bool tourShowAll:  false
    property bool bioExpanded:  false

    // ── Vertical scroll ──────────────────────────────────────────────────
    Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: content.implicitHeight + 24
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        pixelAligned: true
        clip: true
        interactive: false

        property bool isScrollActive: false
        Timer { id: scrollHideTimer; interval: 600; onTriggered: scroller.isScrollActive = false }
        onContentYChanged: { isScrollActive = true; scrollHideTimer.restart() }

        MomentumScroll { target: scroller }

        Column {
            id: content
            x: 12; y: 12
            width: scroller.width - 24
            spacing: 10

            // ── EMPTY STATE ──────────────────────────────────────────────
            Card {
                visible: root.noTrack
                width: parent.width
                height: 80
                cardBgColor: root.cardBgColor
                cardBorderColor: root.cardBorderColor
                Text {
                    anchors.centerIn: parent
                    text: "No track playing"
                    color: root.textSecondary
                    font.pixelSize: 13
                    font.family: root.fontFamily
                }
            }

            // ── TRACK CARD ───────────────────────────────────────────────
            Card {
                visible: !root.noTrack
                width: parent.width
                height: trackCardRow.implicitHeight + 56
                cardBgColor: root.cardBgColor
                cardBorderColor: root.cardBorderColor

                Row {
                    id: trackCardRow
                    x: 28; y: 28
                    width: parent.width - 56
                    spacing: 28

                    // Cover + glow
                    Item {
                        id: coverItem
                        width: 264; height: 264
                        property bool hov: false

                        Image {
                            visible: root.coverGlowColor !== ""
                            readonly property int sp: 38
                            x: -sp; y: -sp
                            width: parent.width + sp * 2; height: parent.height + sp * 2
                            source: root.coverGlowColor !== "" ? "image://nowplayingglow/glow/" + root.coverGlowColor.replace("#", "") : ""
                            cache: false; mipmap: true; smooth: true
                        }

                        Rectangle {
                            anchors.fill: parent; radius: 10
                            color: root.skeletonColor
                            visible: root.coverKey === ""
                            clip: true
                            ShimmerSweep {}
                        }

                        // Rounded-corner cover, drawn via Canvas clip (same
                        // technique as playlist_detail.qml's artCanvas) since
                        // a plain Image can't clip itself to rounded corners.
                        Canvas {
                            id: coverCanvas
                            anchors.fill: parent
                            visible: root.coverKey !== ""

                            property real artZoom: coverItem.hov ? 1.08 : 1.0
                            property string artUrl: root.coverKey !== "" ? "image://nowplayingpix/cover:" + root.coverKey : ""

                            Behavior on artZoom { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                            onArtZoomChanged: requestPaint()
                            onArtUrlChanged: { if (artUrl !== "") loadImage(artUrl); else requestPaint() }
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
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onEntered: coverItem.hov = true
                            onExited:  coverItem.hov = false
                            onClicked: nowPlayingBridge.coverClicked()
                        }
                    }

                    // Right column: title, meta, genre, info, actions
                    Column {
                        width: parent.width - 264 - 28
                        spacing: 4
                        topPadding: 16

                        Text {
                            width: parent.width
                            text: root.trackTitle
                            color: root.accentColor
                            font.pixelSize: root.fontSizePrimary + 15
                            font.bold: true
                            wrapMode: Text.WordWrap
                            font.family: root.fontFamily
                        }

                        // Album • Year — own row, no leading separator before album
                        Row {
                            width: parent.width
                            visible: root.albumName !== "" || root.yearText !== ""
                            Text {
                                id: albumLinkText
                                visible: root.albumName !== ""
                                property bool hov: false
                                text: root.albumName
                                color: hov ? root.accentColor : root.textSecondary
                                font.pixelSize: root.fontSizePrimary; font.bold: true; font.family: root.fontFamily
                                Rectangle {
                                    visible: albumLinkText.hov
                                    y: albumLinkText.baselineOffset + 2
                                    width: albumLinkText.paintedWidth; height: 1
                                    color: albumLinkText.color
                                }
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onEntered: albumLinkText.hov = true; onExited: albumLinkText.hov = false
                                    onClicked: nowPlayingBridge.albumClicked(root.albumId, root.albumName)
                                }
                            }
                            Text {
                                visible: root.albumName !== "" && root.yearText !== ""
                                text: " • "
                                color: root.textSecondary
                                font.pixelSize: root.fontSizePrimary; font.bold: true; font.family: root.fontFamily
                            }
                            Text {
                                id: yearLinkText
                                visible: root.yearText !== ""
                                property bool hov: false
                                text: root.yearText
                                color: hov ? root.accentColor : root.textSecondary
                                font.pixelSize: root.fontSizePrimary; font.bold: true; font.family: root.fontFamily
                                Rectangle {
                                    visible: yearLinkText.hov
                                    y: yearLinkText.baselineOffset + 2
                                    width: yearLinkText.paintedWidth; height: 1
                                    color: yearLinkText.color
                                }
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onEntered: yearLinkText.hov = true; onExited: yearLinkText.hov = false
                                    onClicked: nowPlayingBridge.yearClicked(root.yearText)
                                }
                            }
                        }

                        // Artist(s)
                        Row {
                            width: parent.width
                            Repeater {
                                model: root.artistTokens
                                delegate: Text {
                                    property bool hov: false
                                    text: modelData.text
                                    opacity: modelData.isSep ? 0.5 : 1.0
                                    color: !modelData.isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizePrimary
                                    font.bold: true
                                    font.family: root.fontFamily
                                    Rectangle {
                                        visible: !modelData.isSep && parent.hov
                                        y: parent.baselineOffset + 2
                                        width: parent.paintedWidth; height: 1
                                        color: parent.color
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !modelData.isSep
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true
                                        onExited:  parent.hov = false
                                        onClicked: nowPlayingBridge.artistClicked(modelData.text)
                                    }
                                }
                            }
                        }

                        // Genre chips
                        Flow {
                            width: parent.width
                            visible: root.genreTokens.length > 0
                            Repeater {
                                model: root.genreTokens
                                delegate: Text {
                                    property bool hov: false
                                    text: modelData.text
                                    opacity: modelData.isSep ? 0.5 : 1.0
                                    color: !modelData.isSep && hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary
                                    font.family: root.fontFamily
                                    Rectangle {
                                        visible: !modelData.isSep && parent.hov
                                        y: parent.baselineOffset + 2
                                        width: parent.paintedWidth; height: 1
                                        color: parent.color
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !modelData.isSep
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: parent.hov = true
                                        onExited:  parent.hov = false
                                        onClicked: nowPlayingBridge.genreClicked(modelData.text)
                                    }
                                }
                            }
                        }

                        Text {
                            visible: root.infoText !== ""
                            text: root.infoText
                            color: root.textSecondary
                            font.pixelSize: root.fontSizeSecondary
                            font.family: root.fontFamily
                        }

                        // Action buttons
                        Row {
                            topPadding: 4
                            spacing: 8

                            Rectangle {
                                width: 28; height: 28; radius: 4
                                color: heartHov.containsMouse ? root.hoverColor : "transparent"
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: root.isFavorite ? "image://homeicons/heart_filled_E91E63" : "image://homeicons/heart_555555"
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: heartHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: nowPlayingBridge.heartClicked()
                                }
                            }

                            Rectangle {
                                width: 28; height: 28; radius: 4
                                color: lyricsHov.containsMouse ? root.hoverColor : "transparent"
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: "image://homeicons/lyrics_666666"
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: lyricsHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: nowPlayingBridge.lyricsRequested()
                                }
                            }

                            Rectangle {
                                visible: root.lastfmUrl !== ""
                                width: 28; height: 28; radius: 4
                                color: lastfmHov.containsMouse ? root.hoverColor : "transparent"
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: "image://homeicons/lastfm_666666"
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: lastfmHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: Qt.openUrlExternally(root.lastfmUrl)
                                }
                            }

                            Rectangle {
                                visible: root.wikiUrl !== ""
                                width: 28; height: 28; radius: 4
                                color: wikiHov.containsMouse ? root.hoverColor : "transparent"
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: "image://homeicons/wikipedia_666666"
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: wikiHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: Qt.openUrlExternally(root.wikiUrl)
                                }
                            }
                        }
                    }
                }
            }

            // ── TWO-COLUMN AREA ──────────────────────────────────────────
            Row {
                visible: !root.noTrack
                width: parent.width
                spacing: 10

                Column {
                    width: (parent.width - 10) / 2
                    spacing: 10

                    // ── ALBUM TRACKS CARD ────────────────────────────────
                    Card {
                        width: parent.width
                        height: albumCardCol.implicitHeight + 28
                        cardBgColor: root.cardBgColor
                        cardBorderColor: root.cardBorderColor

                        Column {
                            id: albumCardCol
                            x: 14; y: 14
                            width: parent.width - 28
                            spacing: 4

                            Row {
                                width: parent.width
                                Column {
                                    width: parent.width - goToAlbumText.implicitWidth
                                    spacing: 1
                                    Text {
                                        text: "FROM THIS ALBUM"
                                        color: root.accentColor
                                        font.pixelSize: root.fontSizeSecondary; font.bold: true; font.letterSpacing: 1.5
                                        font.family: root.fontFamily
                                    }
                                    Text {
                                        visible: root.albumMeta !== ""
                                        width: parent.width
                                        text: root.albumMeta
                                        color: root.textSecondary
                                        font.pixelSize: root.fontSizeSecondary
                                        font.family: root.fontFamily
                                        elide: Text.ElideRight
                                    }
                                }
                                Text {
                                    id: goToAlbumText
                                    visible: root.albumId !== "" || root.albumName !== ""
                                    property bool hov: false
                                    text: "Go to Album ↗"
                                    color: hov ? root.accentColor : root.textSecondary
                                    font.pixelSize: root.fontSizeSecondary
                                    font.family: root.fontFamily
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onEntered: goToAlbumText.hov = true; onExited: goToAlbumText.hov = false
                                        onClicked: nowPlayingBridge.albumClicked(root.albumId, root.albumName)
                                    }
                                }
                            }

                            Text {
                                visible: root.albumLoading
                                text: "Loading…"
                                color: root.textSecondary
                                font.pixelSize: 11
                                topPadding: 6
                            }

                            Rectangle {
                                visible: !root.albumLoading && root.albumTracks.length > 0
                                width: parent.width; height: 1
                                color: "#14ffffff"
                            }

                            Repeater {
                                model: root.albumLoading ? [] : root.albumTracks
                                delegate: Rectangle {
                                    width: albumCardCol.width
                                    height: 34
                                    visible: index >= root.albumWindowStart && index < root.albumWindowEnd || root.albumShowAll
                                    radius: 4
                                    color: rowHov.containsMouse ? root.hoverColor : "transparent"

                                    Row {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6; anchors.rightMargin: 8
                                        spacing: 6

                                        Text {
                                            width: 22
                                            horizontalAlignment: Text.AlignHCenter
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: modelData.num
                                            color: modelData.isCurrent ? root.accentColor : root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            font.bold: modelData.isCurrent
                                            font.family: root.fontFamily
                                        }
                                        Text {
                                            width: parent.width - 22 - 38 - 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: modelData.title
                                            color: (modelData.isCurrent || rowHov.containsMouse) ? root.accentColor : root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            font.bold: modelData.isCurrent
                                            font.family: root.fontFamily
                                            elide: Text.ElideRight
                                            Rectangle {
                                                visible: rowHov.containsMouse
                                                y: parent.baselineOffset + 2
                                                width: parent.paintedWidth; height: 1
                                                color: parent.color
                                            }
                                        }
                                        Text {
                                            width: 38
                                            horizontalAlignment: Text.AlignRight
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: modelData.duration
                                            color: root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            font.family: root.fontFamily
                                        }
                                    }
                                    MouseArea {
                                        id: rowHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: nowPlayingBridge.trackPlayClicked(index, "album")
                                    }
                                }
                            }

                            Text {
                                visible: !root.albumLoading && root.albumWindowEnd - root.albumWindowStart < root.albumTracks.length
                                text: root.albumShowAll ? "Show less" : "Show " + (root.albumTracks.length - (root.albumWindowEnd - root.albumWindowStart)) + " more"
                                color: root.accentColor
                                font.pixelSize: 11
                                topPadding: 4
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.albumShowAll = !root.albumShowAll }
                            }
                        }
                    }

                    // ── TOP SONGS CARD ───────────────────────────────────
                    Card {
                        width: parent.width
                        height: topCardCol.implicitHeight + 28
                        cardBgColor: root.cardBgColor
                        cardBorderColor: root.cardBorderColor

                        Column {
                            id: topCardCol
                            x: 14; y: 14
                            width: parent.width - 28
                            spacing: 4

                            Item {
                                width: parent.width
                                height: hdrTopText.implicitHeight

                                Text {
                                    id: hdrTopText
                                    anchors.left: parent.left
                                    anchors.right: hdrTopRight.left
                                    anchors.rightMargin: 6
                                    text: root.topSongsArtist !== "" ? "MOST PLAYED BY " + root.topSongsArtist.toUpperCase() : "MOST PLAYED BY THIS ARTIST"
                                    color: root.accentColor
                                    font.pixelSize: root.fontSizeSecondary; font.bold: true; font.letterSpacing: 1.5
                                    font.family: root.fontFamily
                                    elide: Text.ElideRight
                                }
                                Row {
                                    id: hdrTopRight
                                    anchors.right: parent.right
                                    spacing: 6

                                    Row {
                                        visible: root.topSongsPageCnt > 1
                                        spacing: 2
                                        anchors.verticalCenter: parent.verticalCenter
                                        Image {
                                            width: 12; height: 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            source: root.topSongsPageIdx > 0
                                                ? "image://homeicons/home_back_" + root.accentColor.replace("#", "")
                                                : "image://homeicons/home_back_444444"
                                            cache: false; mipmap: true; smooth: true
                                            MouseArea { anchors.fill: parent; enabled: root.topSongsPageIdx > 0; cursorShape: Qt.PointingHandCursor; onClicked: nowPlayingBridge.topSongsPageRequested(root.topSongsPageIdx - 1) }
                                        }
                                        Text {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: (root.topSongsPageIdx + 1) + "/" + root.topSongsPageCnt
                                            color: root.textSecondary; font.pixelSize: 11; font.bold: true
                                        }
                                        Image {
                                            width: 12; height: 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            source: root.topSongsPageIdx < root.topSongsPageCnt - 1
                                                ? "image://homeicons/home_next_" + root.accentColor.replace("#", "")
                                                : "image://homeicons/home_next_444444"
                                            cache: false; mipmap: true; smooth: true
                                            MouseArea { anchors.fill: parent; enabled: root.topSongsPageIdx < root.topSongsPageCnt - 1; cursorShape: Qt.PointingHandCursor; onClicked: nowPlayingBridge.topSongsPageRequested(root.topSongsPageIdx + 1) }
                                        }
                                    }
                                    Text {
                                        id: goToArtistTopText
                                        visible: root.topSongsArtist !== ""
                                        property bool hov: false
                                        text: "Go to Artist ↗"
                                        color: hov ? root.accentColor : root.textSecondary
                                        font.pixelSize: root.fontSizeSecondary
                                        font.family: root.fontFamily
                                        anchors.verticalCenter: parent.verticalCenter
                                        MouseArea {
                                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onEntered: goToArtistTopText.hov = true; onExited: goToArtistTopText.hov = false
                                            onClicked: nowPlayingBridge.artistClicked(root.topSongsArtist)
                                        }
                                    }
                                }
                            }

                            Text {
                                visible: root.topSongsLoading
                                text: "Loading…"
                                color: root.textSecondary
                                font.pixelSize: 11
                                topPadding: 6
                            }

                            Rectangle {
                                visible: !root.topSongsLoading && root.topSongs.length > 0
                                width: parent.width; height: 1
                                color: "#14ffffff"
                            }

                            Repeater {
                                model: root.topSongsLoading ? [] : root.topSongs
                                delegate: Rectangle {
                                    width: topCardCol.width
                                    height: 46
                                    radius: 4
                                    color: topRowHov.containsMouse ? root.hoverColor : "transparent"

                                    Row {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6; anchors.rightMargin: 8
                                        spacing: 6

                                        Text {
                                            width: 22
                                            horizontalAlignment: Text.AlignHCenter
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: index + 1
                                            color: modelData.isCurrent ? root.accentColor : root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            font.bold: modelData.isCurrent
                                            font.family: root.fontFamily
                                        }
                                        Column {
                                            width: parent.width - 22 - 38 - 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            Text {
                                                width: parent.width
                                                text: modelData.title
                                                color: (modelData.isCurrent || topRowHov.containsMouse) ? root.accentColor : root.textPrimary
                                                font.pixelSize: root.fontSizeSecondary
                                                font.bold: modelData.isCurrent
                                                font.family: root.fontFamily
                                                elide: Text.ElideRight
                                                Rectangle {
                                                    visible: topRowHov.containsMouse
                                                    y: parent.baselineOffset + 2
                                                    width: parent.paintedWidth; height: 1
                                                    color: parent.color
                                                }
                                            }
                                            Text {
                                                visible: modelData.album !== ""
                                                width: parent.width
                                                text: modelData.album
                                                color: root.textSecondary
                                                font.pixelSize: root.fontSizeSecondary - 2
                                                font.family: root.fontFamily
                                                elide: Text.ElideRight
                                            }
                                        }
                                        Text {
                                            width: 38
                                            horizontalAlignment: Text.AlignRight
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: modelData.duration
                                            color: root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary - 2
                                            font.family: root.fontFamily
                                        }
                                    }
                                    MouseArea {
                                        id: topRowHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: nowPlayingBridge.trackPlayClicked(index, "top")
                                    }
                                }
                            }

                            Text {
                                visible: !root.topSongsLoading && root.topSongsArtist !== ""
                                text: "Top tracks from " + root.topSongsArtist + " via Last.fm"
                                color: root.textSecondary
                                font.pixelSize: 10
                                topPadding: 4
                            }
                        }
                    }
                }

                Column {
                    width: (parent.width - 10) / 2
                    spacing: 10

                    // ── ARTIST CARD ───────────────────────────────────────
                    Card {
                        width: parent.width
                        height: artistCardCol.implicitHeight + 28
                        cardBgColor: root.cardBgColor
                        cardBorderColor: root.cardBorderColor

                        Column {
                            id: artistCardCol
                            x: 14; y: 14
                            width: parent.width - 28
                            spacing: 6

                            Item {
                                width: parent.width
                                height: hdrAboutText.implicitHeight

                                Text {
                                    id: hdrAboutText
                                    anchors.left: parent.left
                                    text: "ABOUT THE ARTIST"
                                    color: root.accentColor
                                    font.pixelSize: root.fontSizeSecondary; font.bold: true; font.letterSpacing: 1.5
                                    font.family: root.fontFamily
                                }
                                Row {
                                    anchors.right: parent.right
                                    spacing: 6

                                    Row {
                                        visible: root.artistPageCnt > 1
                                        spacing: 2
                                        anchors.verticalCenter: parent.verticalCenter
                                        Image {
                                            width: 12; height: 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            source: root.artistPageIdx > 0
                                                ? "image://homeicons/home_back_" + root.accentColor.replace("#", "")
                                                : "image://homeicons/home_back_444444"
                                            cache: false; mipmap: true; smooth: true
                                            MouseArea { anchors.fill: parent; enabled: root.artistPageIdx > 0; cursorShape: Qt.PointingHandCursor; onClicked: nowPlayingBridge.artistPageRequested(root.artistPageIdx - 1) }
                                        }
                                        Text {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: (root.artistPageIdx + 1) + "/" + root.artistPageCnt
                                            color: root.textSecondary; font.pixelSize: 11; font.bold: true
                                        }
                                        Image {
                                            width: 12; height: 12
                                            anchors.verticalCenter: parent.verticalCenter
                                            source: root.artistPageIdx < root.artistPageCnt - 1
                                                ? "image://homeicons/home_next_" + root.accentColor.replace("#", "")
                                                : "image://homeicons/home_next_444444"
                                            cache: false; mipmap: true; smooth: true
                                            MouseArea { anchors.fill: parent; enabled: root.artistPageIdx < root.artistPageCnt - 1; cursorShape: Qt.PointingHandCursor; onClicked: nowPlayingBridge.artistPageRequested(root.artistPageIdx + 1) }
                                        }
                                    }
                                    Text {
                                        id: goToArtistAboutText
                                        visible: root.artistPageName !== ""
                                        property bool hov: false
                                        text: "Go to Artist ↗"
                                        color: hov ? root.accentColor : root.textSecondary
                                        font.pixelSize: root.fontSizeSecondary
                                        font.family: root.fontFamily
                                        anchors.verticalCenter: parent.verticalCenter
                                        MouseArea {
                                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onEntered: goToArtistAboutText.hov = true; onExited: goToArtistAboutText.hov = false
                                            onClicked: nowPlayingBridge.artistClicked(root.artistPageName)
                                        }
                                    }
                                }
                            }

                            Row {
                                width: parent.width
                                spacing: 10
                                Item {
                                    width: 88; height: 88
                                    Rectangle {
                                        anchors.fill: parent; radius: 44
                                        color: root.skeletonColor
                                        visible: root.artistPhotoKey === ""
                                        clip: true
                                        ShimmerSweep {}
                                    }
                                    Canvas {
                                        id: artistPhotoCanvas
                                        anchors.fill: parent
                                        visible: root.artistPhotoKey !== ""
                                        property string artUrl: root.artistPhotoKey !== "" ? "image://nowplayingpix/artist:" + root.artistPhotoKey : ""
                                        onArtUrlChanged: { if (artUrl !== "") loadImage(artUrl); else requestPaint() }
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
                                            ctx.closePath(); ctx.clip()
                                            ctx.drawImage(artUrl, 0, 0, width, height)
                                            ctx.restore()
                                        }
                                    }
                                }
                                Text {
                                    width: parent.width - 88 - 10
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: root.artistPageName
                                    color: root.textPrimary
                                    font.pixelSize: 14; font.bold: true
                                    font.family: root.fontFamily
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Text {
                                id: bioText
                                visible: root.artistBio !== ""
                                width: parent.width
                                text: root.artistBio
                                wrapMode: Text.WordWrap
                                color: root.textSecondary
                                font.pixelSize: root.fontSizeSecondary
                                font.family: root.fontFamily
                                clip: true
                                readonly property real clampedH: fontSizeSecondary * 1.5 * 4
                                height: root.bioExpanded ? implicitHeight : Math.min(implicitHeight, clampedH)
                                Behavior on height { NumberAnimation { duration: 150 } }
                            }

                            Text {
                                visible: root.artistBio !== "" && bioText.implicitHeight > bioText.clampedH + 2
                                text: root.bioExpanded ? "Show less" : "Read more"
                                color: root.accentColor
                                font.pixelSize: 11
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.bioExpanded = !root.bioExpanded }
                            }

                            Flow {
                                width: parent.width
                                visible: root.similarArtists.length > 0
                                spacing: 5
                                topPadding: 4
                                Repeater {
                                    model: root.similarArtists
                                    delegate: Rectangle {
                                        width: chipText.implicitWidth + 16; height: 22
                                        radius: 4
                                        color: chipHov.containsMouse ? root.hoverColor : "#14ffffff"
                                        border.color: root.cardBorderColor; border.width: 1
                                        Text {
                                            id: chipText
                                            anchors.centerIn: parent
                                            text: modelData
                                            color: root.textSecondary
                                            font.pixelSize: root.fontSizeSecondary
                                            font.family: root.fontFamily
                                        }
                                        MouseArea {
                                            id: chipHov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onClicked: nowPlayingBridge.artistClicked(modelData)
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ── TOUR CARD ─────────────────────────────────────────
                    Card {
                        width: parent.width
                        height: tourCardCol.implicitHeight + 28
                        cardBgColor: root.cardBgColor
                        cardBorderColor: root.cardBorderColor

                        Column {
                            id: tourCardCol
                            x: 14; y: 14
                            width: parent.width - 28
                            spacing: 4

                            Text {
                                text: "ON TOUR"
                                color: root.accentColor
                                font.pixelSize: root.fontSizeSecondary; font.bold: true; font.letterSpacing: 1.5
                                font.family: root.fontFamily
                            }

                            // Opt-in card
                            Column {
                                visible: !root.bandsintownEnabled
                                width: parent.width
                                spacing: 6
                                topPadding: 4
                                Text {
                                    text: "See upcoming shows?"
                                    color: root.textPrimary
                                    font.pixelSize: 12; font.bold: true
                                    font.family: root.fontFamily
                                }
                                Text {
                                    width: parent.width
                                    text: "Loads tour dates from Bandsintown.\nOnly the artist name leaves your device."
                                    wrapMode: Text.WordWrap
                                    color: root.textSecondary
                                    font.pixelSize: 11
                                    font.family: root.fontFamily
                                }
                                Rectangle {
                                    width: enableText.implicitWidth + 32; height: 30
                                    radius: 6
                                    color: root.accentColor
                                    Text {
                                        id: enableText
                                        anchors.centerIn: parent
                                        text: "Enable tour dates"
                                        color: "#111111"
                                        font.pixelSize: 12; font.bold: true
                                        font.family: root.fontFamily
                                    }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: nowPlayingBridge.enableBandsintownClicked() }
                                }
                            }

                            // Events list
                            Column {
                                visible: root.bandsintownEnabled
                                width: parent.width
                                spacing: 4

                                Text {
                                    visible: root.tourEvents.length === 0
                                    text: "No upcoming shows"
                                    color: root.textSecondary
                                    font.pixelSize: 12
                                    topPadding: 6
                                    font.family: root.fontFamily
                                }

                                Repeater {
                                    model: root.tourEvents
                                    delegate: Rectangle {
                                        width: tourCardCol.width
                                        height: 50
                                        visible: index < root.tourLimit || root.tourShowAll
                                        radius: 6
                                        color: tourRowHov.containsMouse ? root.hoverColor : "transparent"

                                        Row {
                                            anchors.fill: parent
                                            anchors.topMargin: 2; anchors.bottomMargin: 2
                                            spacing: 10

                                            Rectangle {
                                                width: 38; height: 42
                                                anchors.verticalCenter: parent.verticalCenter
                                                radius: 6
                                                color: root.panelBgColor
                                                Column {
                                                    anchors.centerIn: parent
                                                    spacing: 0
                                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.month; color: root.accentColor; font.pixelSize: 9; font.bold: true; font.family: root.fontFamily }
                                                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.day;   color: root.textPrimary; font.pixelSize: 14; font.bold: true; font.family: root.fontFamily }
                                                }
                                            }
                                            Column {
                                                width: parent.width - 38 - 10
                                                anchors.verticalCenter: parent.verticalCenter
                                                spacing: 1
                                                Text { width: parent.width; text: modelData.venue; color: root.textPrimary; font.pixelSize: root.fontSizeSecondary; font.family: root.fontFamily; elide: Text.ElideRight }
                                                Text { width: parent.width; text: modelData.place; color: root.textSecondary; font.pixelSize: root.fontSizeSecondary - 2; font.family: root.fontFamily; elide: Text.ElideRight }
                                            }
                                        }
                                        MouseArea {
                                            id: tourRowHov; anchors.fill: parent; hoverEnabled: true
                                            cursorShape: modelData.url !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
                                            onClicked: if (modelData.url !== "") Qt.openUrlExternally(modelData.url)
                                        }
                                    }
                                }

                                Text {
                                    visible: root.tourEvents.length > root.tourLimit
                                    text: root.tourShowAll ? "Show less" : "Show " + (root.tourEvents.length - root.tourLimit) + " more"
                                    color: root.accentColor
                                    font.pixelSize: 11
                                    topPadding: 2
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.tourShowAll = !root.tourShowAll }
                                }

                                Text {
                                    visible: root.tourEvents.length > 0
                                    text: "Tour data via Bandsintown"
                                    color: root.textSecondary
                                    font.pixelSize: 10
                                    topPadding: 4
                                    font.family: root.fontFamily
                                }
                            }
                        }
                    }
                }
            }
        }
    }

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
}
