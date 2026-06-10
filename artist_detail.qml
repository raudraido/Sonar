import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: root.panelBgColor
    focus: true
    width: parent ? parent.width : 600
    height: mainCol.implicitHeight + 24

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

    // ── Artist state ───────────────────────────────────────────────────────────
    property string artistName:    "Artist Name"
    property string artistStats:   "Loading..."
    property bool   artistFavorite: false
    property string photoId:       ""
    property string bioText:       ""
    property bool   bioCollapsed:  true

    // ── Bridge connections ─────────────────────────────────────────────────────
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

    Timer {
        id: heightReportTimer
        interval: 0
        repeat: false
        onTriggered: artistBridge.reportHeight(mainCol.implicitHeight + 24)
    }

    Column {
        id: mainCol
        // The page scrollbar (6px, see scrollbar_css) eats into the right side
        // only — shrink the right margin by that amount so both sides look even.
        x: 12; y: 12
        width: root.width - 12 - 6
        spacing: 10
        onImplicitHeightChanged: heightReportTimer.restart()

            // ── HEADER CARD ──────────────────────────────────────────────────
            Item {
                id: headerArea
                width: parent.width
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

            // ── ABOUT CARD ───────────────────────────────────────────────────
            Item {
                id: aboutArea
                width: parent.width
                visible: root.bioText !== ""
                height: visible ? (24 + aboutCol.implicitHeight + 24) : 0

                Rectangle {
                    anchors.fill: parent
                    radius: 10
                    color: root.cardBgColor
                    border.color: root.cardBorderColor
                    border.width: 1
                    visible: aboutArea.visible
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
        }
}
