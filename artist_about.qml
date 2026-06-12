import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    color: root.panelBgColor
    width: parent ? parent.width : 600
    height: aboutArea.height
    onHeightChanged: heightReportTimer.restart()

    // ── Theme ──────────────────────────────────────────────────────────────────
    property string textPrimary:     "#eeeeee"
    property string textSecondary:   "#aaaaaa"
    property int    fontSizeSecondary:  12
    property string fontFamily:      ""
    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"
    property string panelBgColor:    "#0e0e0e"

    // ── Artist state ───────────────────────────────────────────────────────────
    property string artistName:   "Artist Name"
    property string bioText:      ""
    property bool   bioCollapsed: true

    // ── Bridge connections ─────────────────────────────────────────────────────
    Connections {
        target: artistBridge
        function onCardBgChanged(c)             { root.cardBgColor       = c }
        function onCardBorderChanged(c)         { root.cardBorderColor   = c }
        function onFontSizeSecondaryChanged(s)  { root.fontSizeSecondary = s }
        function onFontColorPrimaryChanged(c)   { root.textPrimary       = c }
        function onFontColorSecondaryChanged(c) { root.textSecondary     = c }
        function onFontFamilyChanged(f)         { root.fontFamily        = f }
        function onPanelBgChanged(c)            { root.panelBgColor      = c }
        function onArtistDataChanged(name, stats, isFav) { root.artistName = name }
        function onBioChanged(text) {
            root.bioText      = text
            root.bioCollapsed = true
        }
    }

    Timer {
        id: heightReportTimer
        interval: 0
        repeat: false
        onTriggered: artistBridge.reportAboutHeight(root.height)
    }

    // ── ABOUT CARD ───────────────────────────────────────────────────
    Item {
        id: aboutArea
        // Match mainCol's inset in artist_detail.qml so this card lines up
        // with the header card above (12px left margin, 6px right margin
        // to offset the page scrollbar).
        x: 12
        width: root.width - 12 - 6
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
            onImplicitHeightChanged: heightReportTimer.restart()

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
