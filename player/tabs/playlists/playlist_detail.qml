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
    property string panelBgColor:    "#0e0e0e"

    // ── Playlist state (page-specific header card only) ─────────────────────
    property string playlistTitle:      ""
    property string playlistOwner:      ""
    property string playlistMeta:       ""
    property string coverId:            ""
    property bool   isPublic:           false

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
    }

    TrackListView {
        id: trackListView
        anchors.fill: parent
        bridge:         playlistDetailBridge
        trackListModel: playlistTrackModel
        enableRowReorder:  true
        enableTrackColumn: true
        enableAlbumColumn: true
        elasticCol:    "track"
        iconProvider:  "playlisticons"
        tracksLoading: root.playlistMeta === "Loading..."

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

        // ── HEADER CARD: playlist cover/title/meta + action buttons ──────────
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
                            clip: true
                            ShimmerSweep {}
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
        }
    }
}
