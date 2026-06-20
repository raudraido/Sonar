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
    property string fontFamily:      ""
    property string skeletonColor:   "#282828"
    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"
    property string panelBgColor:  "#0e0e0e"

    // ── Album state (page-specific header card only) ───────────────────────
    property string albumTitle:    ""
    property string albumArtist:   ""
    property string albumMeta:     ""
    property string albumType:     ""
    property string coverId:       ""
    property bool   albumFavorite: false

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
    }

    TrackListView {
        id: trackListView
        anchors.fill: parent
        bridge:         albumBridge
        trackListModel: trackModel
        enableRowReorder:  false
        enableTrackColumn: true
        enableAlbumColumn: true
        elasticCol:        "track"
        iconProvider:      "albumicons"
        trackThumbProvider: "albumtrackcovers"
        fixedAlbumName:    root.albumTitle

        accentColor:       root.accentColor
        hoverColor:        root.hoverColor
        textPrimary:       root.textPrimary
        textSecondary:     root.textSecondary
        fontSizePrimary:   root.fontSizePrimary
        fontSizeSecondary: root.fontSizeSecondary
        fontFamily:        root.fontFamily
        skeletonColor:     root.skeletonColor
        cardBgColor:       root.cardBgColor
        cardBorderColor:   root.cardBorderColor
        panelBgColor:      root.panelBgColor

        // ── HEADER CARD: album cover/title/meta + action buttons ────────────
        headerCard: Component {
            Item {
                id: headerArea
                width: parent.width
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
        }
    }
}
