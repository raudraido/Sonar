import QtQuick

Rectangle {
    id: root
    color: panelBgColor

    // ── Theme ────────────────────────────────────────────────────────────
    property string panelBgColor:  "#0e0e0e"
    property string borderColor:   "#0e0e0e"
    property int    borderWidth:   1
    property string accentColor:   "#cccccc"
    property string logoTintColor: "#fafafa"

    // Close-button colors — precomputed in Python (avoids Qt.color() math)
    property string closeBtnBg:          "#1a888888"
    property string closeBtnBgHover:     "#66888888"
    property string closeBtnBorder:      "#292929"
    property string closeBtnBorderHover: "#888888"

    // ── Art section state (driven from Python) ──────────────────────────
    property bool   artVisible:       false
    property int    artTargetSize:    0
    property real   crossfadeProgress: 1.0
    property string currentArtId:     ""
    property string oldArtId:         ""

    readonly property int headerHeight: 62
    readonly property int logoSize: 46

    Connections {
        target: leftPanelBridge
        function onPanelBgChanged(c)            { root.panelBgColor  = c }
        function onBorderColorChanged(c)        { root.borderColor   = c }
        function onBorderWidthChanged(w)        { root.borderWidth   = w }
        function onAccentColorChanged(c)        { root.accentColor   = c }
        function onLogoTintColorChanged(c)      { root.logoTintColor = c }
        function onCloseBtnBgChanged(c)         { root.closeBtnBg          = c }
        function onCloseBtnBgHoverChanged(c)    { root.closeBtnBgHover     = c }
        function onCloseBtnBorderChanged(c)     { root.closeBtnBorder      = c }
        function onCloseBtnBorderHoverChanged(c){ root.closeBtnBorderHover = c }
        function onArtVisibleChanged(v)         { root.artVisible    = v }
        function onArtTargetSizeChanged(s)      { root.artTargetSize = s }
        function onCrossfadeProgressChanged(p)  { root.crossfadeProgress = p }
        function onCurrentArtIdChanged(id)      { root.currentArtId  = id }
        function onOldArtIdChanged(id)          { root.oldArtId      = id }
    }

    // ── HEADER ───────────────────────────────────────────────────────────
    Item {
        id: header
        width: parent.width
        height: root.headerHeight
        anchors.top: parent.top

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: root.borderWidth
            color: root.borderColor
        }

        // Logo: full-color base + accent-tinted overlay, triple-click opens
        // the Theme Builder.
        Item {
            id: logoCtr
            x: 8
            anchors.verticalCenter: parent.verticalCenter
            width: root.logoSize
            height: root.logoSize

            Image {
                anchors.fill: parent
                source: leftPanelLogoBase
                cache: false; mipmap: true; smooth: true
            }
            Image {
                anchors.fill: parent
                source: "image://albumicons/shahedron1_" + root.logoTintColor.replace("#", "")
                cache: false; mipmap: true; smooth: true
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: leftPanelBridge.logoClicked()
            }
        }

        // Right side reserved for native back/forward nav buttons, overlaid
        // from Python (LeftPanel.add_header_widget).
    }

    // Right edge border now drawn by LeftPanel's own QWidget stylesheet
    // (see LeftPanel.apply_theme) — a createWindowContainer's native
    // surface doesn't reliably win z-order against the resize-handle
    // overlay sitting on this exact boundary, which silently hid this
    // Rectangle on some platforms.

    // ── ART SECTION ──────────────────────────────────────────────────────
    // Sits at the bottom of the panel, expands upward from height 0.
    Item {
        id: artSection
        anchors.left:   parent.left
        anchors.right:  parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 8
        height: root.artVisible ? root.artTargetSize : 0
        clip: true

        Behavior on height {
            NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
        }

        // Square art — fixed at artTargetSize so it doesn't get squashed
        // mid-animation; top-aligned, horizontally centered.
        Item {
            id: artSquare
            width:  root.artTargetSize
            height: root.artTargetSize
            x: (artSection.width - width) / 2
            y: 0

            Rectangle {
                anchors.fill: parent
                radius: 5
                color: "#121212"
            }

            // Old cover — crossfades out
            Image {
                anchors.fill: parent
                source: (root.oldArtId !== "" && root.crossfadeProgress < 1.0)
                    ? "image://leftpanelcover/old/" + root.oldArtId : ""
                fillMode: Image.PreserveAspectCrop
                cache: false; mipmap: true; smooth: true
                visible: source !== ""
            }

            // Current cover — crossfades in
            Image {
                anchors.fill: parent
                source: root.currentArtId !== ""
                    ? "image://leftpanelcover/current/" + root.currentArtId : ""
                fillMode: Image.PreserveAspectCrop
                cache: false; mipmap: true; smooth: true
                opacity: root.crossfadeProgress
                visible: source !== ""
            }

            // Empty-state placeholder
            Text {
                anchors.centerIn: parent
                text: "💿"  // 💿
                color: "#333333"
                font.pixelSize: Math.max(20, artSquare.width * 0.3)
                visible: root.currentArtId === "" && root.oldArtId === ""
            }
        }

        // Close / collapse button — top-right, hover fade, accent border
        Item {
            id: closeBtn
            width: 24; height: 24
            x: artSquare.width - width - 4
            y: 4
            opacity: closeHover.containsMouse ? 1.0 : 0.0
            visible: root.artVisible && artSection.height >= root.artTargetSize

            Behavior on opacity { NumberAnimation { duration: 180 } }

            Rectangle {
                anchors.fill: parent
                radius: 12
                border.width: 2
                color: closeHover.containsMouse ? root.closeBtnBgHover : root.closeBtnBg
                border.color: closeHover.containsMouse ? root.closeBtnBorderHover : root.closeBtnBorder
            }

            Image {
                anchors.centerIn: parent
                width: 16; height: 16
                source: "image://albumicons/expand_" + (closeHover.containsMouse ? "ffffff" : "515151")
                cache: false; mipmap: true; smooth: true
            }

            MouseArea {
                id: closeHover
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: leftPanelBridge.closeArtClicked()
            }
        }
    }
}
