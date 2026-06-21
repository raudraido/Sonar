import QtQuick
import QtQuick.Controls
import "../shared_qml"

Rectangle {
    id: root
    color: "transparent"
    focus: true

    // ── Theme ────────────────────────────────────────────────────────────
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

    // ── Page-specific state (driven by favoritesBridge) ──────────────────
    property bool   tracksLoading:     false
    property string songsStatusText:   ""
    property bool   genreFilterActive: false
    property bool   clearFiltersVisible: false

    // ── Bridge connections ─────────────────────────────────────────────
    Connections {
        target: favoritesBridge
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
        function onTracksLoadingChanged(v)        { root.tracksLoading        = v }
        function onSongsStatusChanged(t)          { root.songsStatusText      = t }
        function onGenreFilterActiveChanged(v)    { root.genreFilterActive    = v }
        function onClearFiltersVisibleChanged(v)  { root.clearFiltersVisible  = v }
    }

    TrackListView {
        id: trackListView
        anchors.fill: parent
        bridge:         favoritesBridge
        trackListModel: favoritesTrackModel
        enableRowReorder:  false
        enableTrackColumn: true
        enableAlbumColumn: true
        elasticCol:    "track"
        iconProvider:        "favoritesicons"
        trackThumbProvider:  "favoritestrackcovers"
        tracksLoading: root.tracksLoading

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

        // ── HEADER CARD: favorite artists/albums carousels + songs controls ──
        headerCard: Component {
            Item {
                id: headerArea
                width: parent.width
                height: favColumn.implicitHeight

                Column {
                    id: favColumn
                    width: parent.width
                    spacing: 24

                    Carousel {
                        width: parent.width
                        title: "Artists"
                        model: favoritesArtistsModel
                        showPlayButton:   false
                        subtextClickable: false

                        accentColor:       root.accentColor
                        skeletonColor:     root.skeletonColor
                        hoverColor:        root.hoverColor
                        textPrimary:       root.textPrimary
                        textSecondary:     root.textSecondary
                        fontSizePrimary:   root.fontSizePrimary
                        fontSizeSecondary: root.fontSizeSecondary

                        onCardClicked: (index) => favoritesBridge.artistCardClicked(index)
                    }

                    Carousel {
                        width: parent.width
                        title: "Albums"
                        model: favoritesAlbumsModel

                        accentColor:       root.accentColor
                        skeletonColor:     root.skeletonColor
                        hoverColor:        root.hoverColor
                        textPrimary:       root.textPrimary
                        textSecondary:     root.textSecondary
                        fontSizePrimary:   root.fontSizePrimary
                        fontSizeSecondary: root.fontSizeSecondary

                        onCardClicked:          (index) => favoritesBridge.albumCardClicked(index)
                        onPlayClicked:          (index) => favoritesBridge.albumPlayClicked(index)
                        onArtistSubtextClicked: (name, artistId) => favoritesBridge.artistNameClicked(name, artistId)
                    }

                    Carousel {
                        width: parent.width
                        title: "Top Artists by Favorites"
                        model: favoritesTopArtistsModel
                        showPlayButton:   false
                        subtextClickable: false

                        accentColor:       root.accentColor
                        skeletonColor:     root.skeletonColor
                        hoverColor:        root.hoverColor
                        textPrimary:       root.textPrimary
                        textSecondary:     root.textSecondary
                        fontSizePrimary:   root.fontSizePrimary
                        fontSizeSecondary: root.fontSizeSecondary

                        onCardClicked: (index) => favoritesBridge.topArtistCardClicked(index)
                    }

                    // ── Favorite Songs controls ─────────────────────────────
                    Column {
                        width: parent.width
                        spacing: 8

                        Row {
                            spacing: 8
                            Text {
                                id: songsTitleText
                                text: "Favorite Songs"
                                color: root.textPrimary
                                font.pixelSize: root.fontSizePrimary + 1
                                font.bold: true
                                font.family: root.fontFamily
                            }
                            Text {
                                text: root.songsStatusText
                                color: "#666666"
                                font.pixelSize: root.fontSizeSecondary
                                font.family: root.fontFamily
                                anchors.verticalCenter: songsTitleText.verticalCenter
                            }
                        }

                        Row {
                            spacing: 10
                            topPadding: 8

                            // Play All — ring button matching album/playlist detail style
                            Item {
                                id: playCircle
                                width: 58; height: 58

                                Image {
                                    readonly property int sp: 20
                                    x: -sp; y: -sp
                                    width: parent.width + sp * 2; height: parent.height + sp * 2
                                    source: "image://favoritesplaybtn/btn/" + root.accentColor.replace("#", "")
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
                                    source: "image://favoritesicons/play_" + root.accentColor.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }

                                MouseArea {
                                    id: playHover
                                    anchors.fill: parent; hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: favoritesBridge.playAllClicked()
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
                                    source: "image://favoritesicons/shuffle_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                MouseArea {
                                    id: shuffleHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: favoritesBridge.shuffleClicked()
                                }
                            }

                            // Genre filter
                            Item {
                                width: 40; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Rectangle {
                                    anchors.fill: parent; radius: 8
                                    color: root.genreFilterActive ? root.accentColor : root.hoverColor
                                    opacity: root.genreFilterActive ? 0.22 : (genreHover.containsMouse ? 1.0 : 0.0)
                                    Behavior on opacity { NumberAnimation { duration: 150 } }
                                }
                                Image {
                                    anchors.centerIn: parent; width: 20; height: 20
                                    source: "image://favoritesicons/filter_" + root.textSecondary.replace("#", "")
                                    cache: false; mipmap: true; smooth: true
                                }
                                // Active-filter indicator dot
                                Rectangle {
                                    visible: root.genreFilterActive
                                    width: 7; height: 7; radius: 3.5
                                    color: root.accentColor
                                    anchors.top: parent.top; anchors.right: parent.right
                                    anchors.topMargin: 2; anchors.rightMargin: 2
                                }
                                MouseArea {
                                    id: genreHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: (mouse) => {
                                        var gp = mapToGlobal(mouse.x, mouse.y)
                                        favoritesBridge.genreFilterClicked(gp.x, gp.y)
                                    }
                                }
                            }

                            // Clear filters
                            Item {
                                visible: root.clearFiltersVisible
                                width: clearText.implicitWidth + 16; height: 40
                                anchors.verticalCenter: playCircle.verticalCenter
                                Text {
                                    id: clearText
                                    anchors.centerIn: parent
                                    text: "✕  Clear filters"
                                    color: root.accentColor
                                    font.pixelSize: root.fontSizeSecondary - 1
                                    font.family: root.fontFamily
                                    font.underline: clearHover.containsMouse
                                }
                                MouseArea {
                                    id: clearHover; anchors.fill: parent
                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: favoritesBridge.clearFiltersClicked()
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
