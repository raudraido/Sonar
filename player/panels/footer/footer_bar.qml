import QtQuick
import "../../tabs/shared_qml"

// Footer transport bar — now-playing info, transport controls, waveform
// scrubber (3 display modes), volume/cast/settings. Ported from the
// QWidget-based FooterPanel (player/panels/footer/__init__.py) per
// UI_MANIFEST.md's QQuickView/QMLGridWrapper + Bridge(QObject) pattern.
//
// Root has no explicit width/height binding — player/panels/footer/__init__.py
// hosts this with SizeRootObjectToView, which imperatively sets the root
// item's size (UI_MANIFEST §1: only safe with no width/height bindings).
Rectangle {
    id: root
    color: "transparent"

    // ── Theme (live updates pushed from footerBridge's signals; initial
    // values come from context properties set before this component was
    // created — see player/panels/footer/__init__.py — so the very first
    // frame already reflects the real theme instead of these fallback
    // literals racing against the bridge's first signal emission) ──────────
    property string accentColor:       initialAccentColor
    property string panelBg:           initialPanelBg
    property string hoverColor:        initialHoverColor
    property string borderColor:       initialBorderColor
    property int    borderWidth:       initialBorderWidth
    property string fontColorPrimary:  initialFontColorPrimary
    property string fontColorSecondary: initialFontColorSecondary
    property int    fontSizePrimary:   initialFontSizePrimary
    property int    fontSizeSecondary: initialFontSizeSecondary
    property string fontFamily:        initialFontFamily

    // ── Playback state ───────────────────────────────────────────────────────
    property bool isPlaying:      false
    // enginePositionMs/enginePositionAtMs are the last raw fact pushed from
    // Python and when it arrived; displayPositionMs is the only thing any
    // paint code/label should read. A single FrameAnimation (positionClock,
    // below) derives displayPositionMs from the other two every frame and
    // guarantees it never moves backward during playback (Math.max against
    // its own last value) — the structural fix for the flicker that came
    // from two independent Python timers fighting over a single positionMs
    // value (one polling the real decoder, one extrapolating). A `hard`
    // jump (seek/track-start/stop/loop) snaps displayPositionMs immediately
    // instead of waiting for the clock to catch up.
    property int  enginePositionMs:   0
    property real enginePositionAtMs: 0
    property real displayPositionMs:  0
    property int  durationMs:     1
    property bool isShuffle:      false
    property bool isRepeat:       false
    property bool isMuted:        false
    property int  volume:         100
    property bool castConnected:  false

    // ── Waveform state ───────────────────────────────────────────────────────
    property int  displayMode:        2       // 0=scratch 1=minimal 2=bars
    property bool showRemainingTime:  false   // totalTimeLbl: total vs. countdown-to-end
    property bool hasRealData:        false
    property var  samples:            []

    // ── Track info ───────────────────────────────────────────────────────────
    property string trackTitle:   ""
    property string trackArtist:  ""
    property string trackAlbum:   ""
    property string bpmText:      ""
    property int    coverVersion: 0
    property bool   sidebarArtExpanded: false

    function hexNoHash(c) { return c.indexOf('#') === 0 ? c.substring(1) : c }
    function tintedIcon(name, colorHex) { return "image://footericons/" + name + "_" + root.hexNoHash(colorHex) }
    function hexToRgb01(hex) {
        hex = root.hexNoHash(hex)
        return {
            r: parseInt(hex.substring(0, 2), 16) / 255,
            g: parseInt(hex.substring(2, 4), 16) / 255,
            b: parseInt(hex.substring(4, 6), 16) / 255
        }
    }

    // QML-typed color binding — assigning a hex string to a `color`-typed
    // property gives access to .r/.g/.b (0-1) without a Qt.color() helper
    // (no such global function exists; this is the idiomatic conversion).
    property color accentQColor: accentColor

    // panelBg is "r,g,b" (matches the theme's footer_panel_bg format) —
    // used as the play ring's center fill, same role as album_detail.qml's
    // root.cardBgColor (blocks the glow halo from showing through the ring).
    function footerBgColor() {
        var parts = root.panelBg.split(',')
        if (parts.length < 3) return Qt.rgba(0.05, 0.05, 0.05, 1.0)
        return Qt.rgba(parseInt(parts[0]) / 255, parseInt(parts[1]) / 255, parseInt(parts[2]) / 255, 1.0)
    }

    function formatTime(ms) {
        ms = Math.max(0, Math.round(ms))
        var totalSec = Math.floor(ms / 1000)
        var h = Math.floor(totalSec / 3600)
        var rem = totalSec % 3600
        var m = Math.floor(rem / 60)
        var s = rem % 60
        var ss = (s < 10 ? "0" : "") + s
        if (h > 0) {
            var mm = (m < 10 ? "0" : "") + m
            return h + ":" + mm + ":" + ss
        }
        return m + ":" + ss
    }

    // Used by drag/scratch/decay handlers below for instant local feedback —
    // rebases both displayPositionMs and the engine baseline to `ms` so a
    // subsequent hard confirmation from Python (after seekRequested) lands
    // exactly where the UI already is, instead of snapping.
    function setLocalPosition(ms) {
        root.displayPositionMs = ms
        root.enginePositionMs = ms
        root.enginePositionAtMs = Date.now()
    }

    Connections {
        target: footerBridge
        function onAccentColorChanged(c)        { root.accentColor        = c }
        function onPanelBgChanged(c)            { root.panelBg            = c }
        function onHoverColorChanged(c)         { root.hoverColor         = c }
        function onBorderColorChanged(c)        { root.borderColor        = c }
        function onBorderWidthChanged(w)        { root.borderWidth        = w }
        function onFontColorPrimaryChanged(c)   { root.fontColorPrimary   = c }
        function onFontColorSecondaryChanged(c) { root.fontColorSecondary = c }
        function onFontSizePrimaryChanged(s)    { root.fontSizePrimary    = s }
        function onFontSizeSecondaryChanged(s)  { root.fontSizeSecondary  = s }
        function onFontFamilyChanged(f)         { root.fontFamily         = f }

        function onIsPlayingChanged(p) {
            root.isPlaying = p
            // Rebase the extrapolation clock to "now" on resume — otherwise
            // the elapsed-since-last-update gap includes however long
            // playback was paused, and positionClock's first tick would
            // extrapolate displayPositionMs forward by that whole gap.
            if (p) root.enginePositionAtMs = Date.now()
        }
        function onPositionMsChanged(v, hard) {
            root.enginePositionMs = v
            root.enginePositionAtMs = Date.now()
            if (hard) root.displayPositionMs = v
        }
        function onDurationMsChanged(v) { root.durationMs = v; waveformCanvas.requestPaint() }
        function onShuffleChanged(v)    { root.isShuffle = v }
        function onRepeatChanged(v)     { root.isRepeat  = v }
        function onMutedChanged(v)      { root.isMuted   = v }
        function onVolumeChanged(v)     { root.volume    = v }
        function onCastConnectedChanged(v) { root.castConnected = v }

        function onDisplayModeChanged(v) { root.displayMode = v; waveformCanvas.requestPaint() }
        function onShowRemainingChanged(v) { root.showRemainingTime = v }
        function onHasRealDataChanged(v) { root.hasRealData = v; waveformCanvas.requestPaint() }
        function onSamplesChanged() {
            root.samples = footerBridge.getSamples()
            waveformCanvas.barPathDirty = true
            waveformCanvas.requestPaint()
        }

        function onCoverVersionChanged(v) { root.coverVersion = v }
        function onTrackInfoChanged(t, a, al) { root.trackTitle = t; root.trackArtist = a; root.trackAlbum = al }
        function onBpmTextChanged(t) { root.bpmText = t }
        function onSidebarArtExpandedChanged(v) { root.sidebarArtExpanded = v }
    }

    // Top divider — was previously also drawn by the host QWidget's
    // stylesheet (border-top), but a createWindowContainer's native child
    // window always paints above sibling QWidget content (UI_MANIFEST §3),
    // so that border never actually showed through; drawn here instead,
    // themed the same way (theme.border_color/border_width).
    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: root.borderWidth
        color: root.borderColor
    }

    // ════════════════════════════════════════════════════════════════════════
    // LEFT — now playing (art + title/artist/album/bpm)
    // ════════════════════════════════════════════════════════════════════════
    Item {
        id: leftBlock
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: Math.max(220, root.width * 0.26)

        Item {
            id: artWrap
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: root.sidebarArtExpanded ? 0 : 84
            height: 84
            clip: true
            visible: width > 0
            Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.InOutCubic } }

            Image {
                id: artImg
                anchors.fill: parent
                source: root.coverVersion > 0 ? ("image://footerart/cover?v=" + root.coverVersion) : ""
                fillMode: Image.PreserveAspectCrop
                smooth: true
                cache: false
                visible: root.coverVersion > 0
            }
            Rectangle {
                anchors.fill: parent
                color: "#222222"
                visible: root.coverVersion <= 0
            }
            MouseArea {
                id: artHoverArea
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.RightButton
                onClicked: footerBridge.trackContextMenuRequested()
            }
            Rectangle {
                id: expandBtn
                width: 24; height: 24; radius: 12
                anchors.top: parent.top; anchors.right: parent.right
                anchors.margins: 2
                color: Qt.rgba(root.accentQColor.r, root.accentQColor.g, root.accentQColor.b,
                                expandClick.containsMouse ? 0.4 : 0.1)
                border.width: 2
                border.color: Qt.rgba(root.accentQColor.r, root.accentQColor.g, root.accentQColor.b,
                                       expandClick.containsMouse ? 1.0 : 0.3)
                opacity: artHoverArea.containsMouse || expandClick.containsMouse ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: 180 } }
                Image {
                    anchors.centerIn: parent
                    width: 16; height: 16
                    sourceSize: Qt.size(16, 16)
                    source: tintedIcon("expand", expandClick.containsMouse ? "#ffffff" : "#515151")
                    cache: false; mipmap: true; smooth: true
                }
                MouseArea {
                    id: expandClick
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: footerBridge.expandArtClicked()
                }
            }
        }

        Column {
            anchors.left: artWrap.right
            anchors.leftMargin: artWrap.width > 0 ? 12 : 0
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3

            Text {
                id: titleLbl
                width: parent.width
                text: root.trackTitle
                elide: Text.ElideRight
                font.family: root.fontFamily
                font.pixelSize: root.fontSizePrimary
                font.bold: true
                color: root.accentColor
                property bool hov: false
                Rectangle {
                    visible: parent.hov
                    y: parent.baselineOffset + 2
                    width: Math.min(parent.paintedWidth, parent.width)
                    height: 1
                    color: parent.color
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onEntered: titleLbl.hov = true
                    onExited: titleLbl.hov = false
                    onClicked: (mouse) => {
                        if (mouse.button === Qt.RightButton) footerBridge.trackContextMenuRequested()
                        else footerBridge.titleClicked()
                    }
                }
            }

            Row {
                spacing: 0
                Repeater {
                    model: root.trackArtist.length ? root.trackArtist.split(/( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )/) : []
                    delegate: Text {
                        property bool isSep: /^( \/\/\/ | • | \/ | feat\. | Feat\. | vs\. )$/.test(modelData)
                        property bool hov: false
                        text: modelData
                        opacity: isSep ? 0.45 : 1.0
                        font.family: root.fontFamily
                        font.pixelSize: root.fontSizeSecondary
                        color: (!isSep && hov) ? root.accentColor : root.fontColorSecondary
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
                            onExited: parent.hov = false
                            onClicked: footerBridge.artistClicked(modelData.trim())
                        }
                    }
                }
            }

            Text {
                id: albumLbl
                visible: root.trackAlbum.length > 0
                text: root.trackAlbum
                font.family: root.fontFamily
                font.pixelSize: root.fontSizeSecondary
                property bool hov: false
                color: hov ? root.accentColor : root.fontColorSecondary
                Rectangle {
                    visible: parent.hov
                    y: parent.baselineOffset + 2
                    width: parent.paintedWidth; height: 1
                    color: parent.color
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onEntered: albumLbl.hov = true
                    onExited: albumLbl.hov = false
                    onClicked: footerBridge.albumClicked()
                }
            }

            Text {
                id: bpmLbl
                visible: root.bpmText.length > 0
                text: root.bpmText
                font.family: root.fontFamily
                font.pixelSize: root.fontSizeSecondary
                color: root.fontColorSecondary
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    acceptedButtons: Qt.RightButton
                    onClicked: footerBridge.bpmContextMenuRequested()
                }
            }
        }
    }

    // ════════════════════════════════════════════════════════════════════════
    // CENTER — transport controls + waveform
    // ════════════════════════════════════════════════════════════════════════
    Item {
        id: centerBlock
        anchors.left: leftBlock.right
        anchors.right: rightBlock.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Column {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 8
            spacing: 6
            width: centerBlock.width - 20

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 20

                // All buttons sit in a 58-tall container (matching the play
                // button) so their smaller visuals end up vertically centered
                // on the same line as the play ring, instead of Row's default
                // top-alignment of mixed-height children.

                // Stop
                Item {
                    width: 36; height: 58
                    IconButton {
                        anchors.centerIn: parent
                        width: 40; height: 40; radius: 22
                        iconSize: 16
                        hoverColor: root.hoverColor
                        iconSource: root.tintedIcon("stop", root.accentColor)
                        onTriggered: footerBridge.stopClicked()
                        onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Stop", cx, ay, by)
                        onHoverExited: footerBridge.hideTooltip()
                    }
                }

                // Shuffle
                Item {
                    width: 45; height: 58
                    IconButton {
                        id: shuffleBtn
                        anchors.centerIn: parent
                        width: 40; height: 40; radius: 22
                        iconSize: 18
                        hoverColor: root.hoverColor
                        iconSource: root.tintedIcon("shuffle", root.accentColor)
                        onTriggered: footerBridge.shuffleToggled(!root.isShuffle)
                        onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Shuffle", cx, ay, by)
                        onHoverExited: footerBridge.hideTooltip()
                    }
                    Rectangle {
                        visible: root.isShuffle
                        width: 5; height: 5; radius: 2.5
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: parent.height / 2 - 16
                        color: root.accentColor
                    }
                }

                // Previous
                Item {
                    width: 45; height: 58
                    IconButton {
                        anchors.centerIn: parent
                        width: 40; height: 40; radius: 22
                        iconSize: 16
                        hoverColor: root.hoverColor
                        iconSource: root.tintedIcon("prev", root.accentColor)
                        onTriggered: footerBridge.prevClicked()
                        onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Previous Track", cx, ay, by)
                        onHoverExited: footerBridge.hideTooltip()
                    }
                }

                // Play/Pause — the same ring+glow button used by
                // album_detail.qml / artist_detail_page.qml (Canvas ring,
                // accent-tinted blurred-shadow halo on hover, 16px icon),
                // just made play/pause-aware via root.isPlaying.
                Item {
                    id: playBtn
                    width: 58; height: 58

                    Image {
                        id: playHalo
                        readonly property int sp: 20
                        x: -sp; y: -sp
                        width: parent.width + sp * 2; height: parent.height + sp * 2
                        source: "image://footerbtnglow/btn/" + root.hexNoHash(root.accentColor)
                        cache: false; mipmap: true; smooth: true
                        opacity: playArea.containsMouse ? 1.0 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                    }

                    Rectangle {
                        anchors.fill: parent; anchors.margins: 2.5
                        radius: width / 2
                        color: root.footerBgColor()
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
                        anchors.horizontalCenterOffset: root.isPlaying ? 0 : 1
                        width: 16; height: 16
                        sourceSize: Qt.size(16, 16)
                        cache: false; mipmap: true; smooth: true
                        source: tintedIcon(root.isPlaying ? "pause" : "play", root.accentColor)
                    }

                    MouseArea {
                        id: playArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: footerBridge.playClicked()
                        onEntered: { var a = mapToGlobal(width / 2, -4); var b = mapToGlobal(width / 2, height + 4); footerBridge.showTooltip("Play/Pause", a.x, a.y, b.y) }
                        onExited: footerBridge.hideTooltip()
                    }
                }

                // Next
                Item {
                    width: 45; height: 58
                    IconButton {
                        anchors.centerIn: parent
                        width: 40; height: 40; radius: 22
                        iconSize: 16
                        hoverColor: root.hoverColor
                        iconSource: root.tintedIcon("next", root.accentColor)
                        onTriggered: footerBridge.nextClicked()
                        onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Next Track", cx, ay, by)
                        onHoverExited: footerBridge.hideTooltip()
                    }
                }

                // Repeat
                Item {
                    width: 36; height: 58
                    IconButton {
                        anchors.centerIn: parent
                        width: 36; height: 36; radius: 18
                        iconSize: 16
                        hoverColor: root.hoverColor
                        iconSource: root.tintedIcon("repeat", root.accentColor)
                        onTriggered: footerBridge.repeatToggled(!root.isRepeat)
                        onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Repeat", cx, ay, by)
                        onHoverExited: footerBridge.hideTooltip()
                    }
                    Rectangle {
                        visible: root.isRepeat
                        width: 5; height: 5; radius: 2.5
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: parent.height / 2 - 16
                        color: root.accentColor
                    }
                }
            }

            Row {
                width: parent.width
                spacing: 15

                // Worst-case time string at this font, measured by an
                // invisible Text (implicitWidth is still computed when
                // visible: false) — gives currentTimeLbl/totalTimeLbl a fixed
                // width below so waveformWrap's width (and therefore every
                // bar's x-position) stays constant regardless of which
                // digits are showing. Without this, each digit-count/glyph-
                // width change (e.g. "0:59" → "1:00") shifted implicitWidth,
                // which shifted waveformWrap's width, which visibly moved
                // the whole waveform left/right every second.
                Text {
                    id: timeWidthRef
                    visible: false
                    font.family: root.fontFamily
                    font.pixelSize: 14; font.bold: true
                    text: "-00:00:00"
                }

                Text {
                    id: currentTimeLbl
                    anchors.verticalCenter: parent.verticalCenter
                    width: timeWidthRef.implicitWidth
                    horizontalAlignment: Text.AlignRight
                    text: root.formatTime(root.displayPositionMs)
                    color: root.accentColor
                    font.family: root.fontFamily
                    font.pixelSize: 14; font.bold: true
                }

                Item {
                    id: waveformWrap
                    width: parent.width - currentTimeLbl.width - totalTimeLbl.width - 30
                    height: 60
                    anchors.verticalCenter: parent.verticalCenter

                    // Tying canvas visibility to its own onPaint callback
                    // (everPainted) didn't stop the startup black flash —
                    // the callback can fire before the canvas's GPU texture
                    // upload/composite has actually completed under
                    // startup load, so anything gated on it gets revealed
                    // too early. Use a deterministic delay instead: cover
                    // the canvas with the real background for a fixed
                    // window after load, then reveal once actual rendering
                    // has had time to settle. The actual covering Rectangle
                    // is declared last (after the Canvas) below, since a
                    // sibling declared earlier paints *underneath* later
                    // siblings — putting it before the Canvas (an earlier
                    // attempt) meant the opaque-black canvas painted over it
                    // and it never actually covered anything.
                    property bool startupSettled: false
                    Timer {
                        interval: 1200; running: true; repeat: false
                        onTriggered: waveformWrap.startupSettled = true
                    }

                    Canvas {
                        id: waveformCanvas
                        anchors.fill: parent
                        renderStrategy: Canvas.Cooperative
                        // Default renderTarget (Canvas.FramebufferObject, a GPU-backed
                        // surface) doesn't reliably get an alpha channel on Windows/
                        // ANGLE, so ctx.clearRect() clears to opaque black instead of
                        // transparent — permanently, every paint, not just the first.
                        // Canvas.Image forces the CPU-side QImage backing store
                        // instead, which properly supports alpha, letting the
                        // footer's real background show through wherever nothing is
                        // drawn (no track loaded, empty mode-1 groove gaps, etc.).
                        renderTarget: Canvas.Image

                        property bool barPathDirty: true
                        property var barPath: []

                        // Scratch-mode fade-to-edges lookup (cached per width,
                        // same caching strategy the old Python renderer used —
                        // see fadeLookupWidth check in paintScratch) — avoids
                        // recomputing Math.pow() per pixel every frame.
                        property var fadeLookup: []
                        property int fadeLookupWidth: -1

                        onWidthChanged: { barPathDirty = true; fadeLookupWidth = -1; requestPaint() }
                        onHeightChanged: { barPathDirty = true; requestPaint() }
                        // Canvas only repaints reactively (onXChanged: requestPaint()
                        // above) — since the root's initial property values are
                        // already correct (no startup flash, see __init__.py's
                        // initial* context properties), nothing actually *changes*
                        // at launch, so none of those handlers fire and the canvas
                        // would otherwise sit on its raw (black) backing texture
                        // until the first real track loads. Force one paint up front.
                        Component.onCompleted: requestPaint()

                        function rebuildBarPath() {
                            var BAR_W = 3, BAR_GAP = 2
                            var totalBar = BAR_W + BAR_GAP
                            var numBars = Math.floor(width / totalBar)
                            var bars = []
                            var total = root.samples.length
                            if (numBars > 0 && total > 0) {
                                var perBar = total / numBars
                                var centerY = height / 2.0
                                for (var i = 0; i < numBars; i++) {
                                    var startIdx = Math.floor(i * perBar)
                                    var endIdx = Math.min(Math.floor((i + 1) * perBar), total)
                                    var sum = 0, cnt = 0
                                    for (var j = startIdx; j < endIdx; j++) { sum += root.samples[j]; cnt++ }
                                    var avg = cnt > 0 ? sum / cnt : 0
                                    var expanded = Math.pow(avg, 1.5)
                                    var val = Math.min(1.0, expanded * 1.8)
                                    var barH = Math.max(4.0, val * (height * 0.85))
                                    var x = i * totalBar + (BAR_W / 2.0)
                                    bars.push({ x: x, top: centerY - barH / 2, bottom: centerY + barH / 2 })
                                }
                            }
                            barPath = bars
                            barPathDirty = false
                        }

                        function colorMix(c1, c2, t) {
                            var r = Math.round((c1.r + (c2.r - c1.r) * t) * 255)
                            var g = Math.round((c1.g + (c2.g - c1.g) * t) * 255)
                            var b = Math.round((c1.b + (c2.b - c1.b) * t) * 255)
                            return "rgb(" + r + "," + g + "," + b + ")"
                        }

                        function paintBars(ctx) {
                            if (barPathDirty) rebuildBarPath()
                            if (barPath.length === 0) {
                                if (!root.hasRealData && root.durationMs > 1) paintAnalyzing(ctx)
                                return
                            }
                            var progress = root.displayPositionMs / Math.max(1, root.durationMs)
                            var playheadX = progress * width

                            ctx.lineCap = "round"
                            ctx.lineWidth = 3
                            ctx.strokeStyle = "rgba(80,80,80,0.6)"
                            ctx.beginPath()
                            for (var i = 0; i < barPath.length; i++) {
                                var b = barPath[i]
                                ctx.moveTo(b.x, b.top); ctx.lineTo(b.x, b.bottom)
                            }
                            ctx.stroke()

                            ctx.save()
                            ctx.beginPath()
                            ctx.rect(0, 0, playheadX, height)
                            ctx.clip()
                            var grad = ctx.createLinearGradient(0, 0, 0, height)
                            var base = root.hexToRgb01(root.accentColor)
                            grad.addColorStop(0.0, colorMix(base, { r: 1, g: 1, b: 1 }, 0.3))
                            grad.addColorStop(0.5, colorMix(base, base, 0))
                            grad.addColorStop(1.0, colorMix(base, { r: 0, g: 0, b: 0 }, 0.4))
                            ctx.strokeStyle = grad
                            ctx.beginPath()
                            for (var k = 0; k < barPath.length; k++) {
                                var b2 = barPath[k]
                                ctx.moveTo(b2.x, b2.top); ctx.lineTo(b2.x, b2.bottom)
                            }
                            ctx.stroke()
                            ctx.restore()
                        }

                        function paintMinimal(ctx) {
                            var trackH = 6
                            var centerY = height / 2.0
                            var trackY = centerY - trackH / 2.0
                            var handleR = 6
                            var margin = handleR
                            var trackW = width - 2 * margin
                            var progress = root.displayPositionMs / Math.max(1, root.durationMs)
                            var playheadX = margin + progress * trackW

                            ctx.fillStyle = "rgba(60,60,60,0.6)"
                            roundRect(ctx, margin, trackY, trackW, trackH, trackH / 2)
                            ctx.fill()

                            var filledW = playheadX - margin
                            if (filledW > 0) {
                                ctx.fillStyle = root.accentColor
                                roundRect(ctx, margin, trackY, filledW, trackH, trackH / 2)
                                ctx.fill()
                            }
                            ctx.fillStyle = root.accentColor
                            ctx.beginPath()
                            ctx.ellipse(playheadX - handleR, centerY - handleR, handleR * 2, handleR * 2)
                            ctx.fill()
                        }

                        function roundRect(ctx, x, y, w, h, r) {
                            if (w <= 0) return
                            r = Math.min(r, w / 2, h / 2)
                            ctx.beginPath()
                            ctx.moveTo(x + r, y)
                            ctx.lineTo(x + w - r, y)
                            ctx.quadraticCurveTo(x + w, y, x + w, y + r)
                            ctx.lineTo(x + w, y + h - r)
                            ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
                            ctx.lineTo(x + r, y + h)
                            ctx.quadraticCurveTo(x, y + h, x, y + h - r)
                            ctx.lineTo(x, y + r)
                            ctx.quadraticCurveTo(x, y, x + r, y)
                            ctx.closePath()
                        }

                        function paintAnalyzing(ctx) {
                            ctx.fillStyle = "#646464"
                            ctx.font = "bold 11px " + (root.fontFamily.length ? ('"' + root.fontFamily + '"') : "sans-serif")
                            ctx.textAlign = "center"
                            ctx.textBaseline = "middle"
                            ctx.fillText("ANALYZING WAVEFORM...", width / 2, height / 2)
                        }

                        // Ported from the old Python numpy renderer's HSV->RGB
                        // helper (scalar instead of vectorized) — h: 0-359, s/v: 0-255.
                        function hsvToRgb(h, s, v) {
                            var hi = h / 60.0
                            var i = Math.floor(hi) % 6
                            var f = hi - Math.floor(hi)
                            var vv = v / 255.0, ss = s / 255.0
                            var p = vv * (1.0 - ss)
                            var q = vv * (1.0 - f * ss)
                            var t = vv * (1.0 - (1.0 - f) * ss)
                            var r, g, b
                            switch (i) {
                                case 0: r = vv; g = t;  b = p;  break
                                case 1: r = q;  g = vv; b = p;  break
                                case 2: r = p;  g = vv; b = t;  break
                                case 3: r = p;  g = q;  b = vv; break
                                case 4: r = t;  g = p;  b = vv; break
                                default: r = vv; g = p; b = q;  break
                            }
                            return {
                                r: Math.max(0, Math.min(255, Math.round(r * 255))),
                                g: Math.max(0, Math.min(255, Math.round(g * 255))),
                                b: Math.max(0, Math.min(255, Math.round(b * 255)))
                            }
                        }

                        function rebuildFadeLookup() {
                            var w = Math.ceil(width)
                            var cx = w / 2.0
                            var arr = new Array(w)
                            for (var i = 0; i < w; i++) {
                                arr[i] = cx > 0 ? Math.max(0, 1.0 - Math.pow(Math.abs(i - cx) / cx, 1.6)) : 0
                            }
                            fadeLookup = arr
                            fadeLookupWidth = w
                        }

                        // Renders scratch mode directly in QML/JS — was
                        // previously a Python+numpy round-trip (compute a
                        // (height,width,4) RGBA buffer, wrap in a QImage, serve
                        // it through an image:// provider, async-load it back
                        // into this canvas). That async hop introduced latency
                        // that, at high zoom (large pixelsPerSample), got
                        // visually amplified into a noticeable stutter since
                        // each frame's on-screen shift is much bigger per
                        // sample of playback time. Drawing per-column here
                        // instead runs synchronously on the same thread as the
                        // paint itself, eliminating that round-trip entirely.
                        // Math mirrors the old renderer's per-pixel formula
                        // exactly (its user_picked flag was always false in
                        // practice, so that branch is folded in directly
                        // rather than plumbed through as a property).
                        function paintScratch(ctx) {
                            var total = root.samples.length
                            if (root.hasRealData && total >= 2) {
                                if (fadeLookupWidth !== Math.ceil(width)) rebuildFadeLookup()

                                var centerX0 = width / 2.0, centerY0 = height / 2.0
                                var maxBarH0 = (height / 2.0) * 0.90
                                var currentIndex = scratchArea.scratchCurrentIndex
                                var pxPerSample = scratchArea.pixelsPerSample
                                var hue = root.accentQColor.hsvHue
                                var baseHue = hue >= 0 ? hue * 360.0 : 150.0
                                var w = Math.ceil(width)

                                for (var x = 0; x < w; x++) {
                                    var exactIndex = currentIndex + (x - centerX0) / pxPerSample
                                    if (exactIndex < 0 || exactIndex >= total - 1) continue

                                    var fade = fadeLookup[x]
                                    var isLeft = x < centerX0
                                    var alpha = isLeft ? (255.0 * fade) : (180.0 * fade)
                                    if (alpha < 1.0) continue

                                    var idx1 = Math.floor(exactIndex)
                                    var frac = exactIndex - idx1
                                    var rawVal = root.samples[idx1] + (root.samples[idx1 + 1] - root.samples[idx1]) * frac
                                    var val = rawVal * rawVal * 0.5 + rawVal * 0.5
                                    var barH = val * maxBarH0

                                    var brightness = isLeft ? (120.0 + 135.0 * fade) : (70.0 + 130.0 * fade)
                                    var hueShift = Math.floor(20.0 * rawVal)
                                    var finalHue = ((baseHue + hueShift) % 360 + 360) % 360
                                    var saturation = 255.0 * Math.max(0.3, 1.0 - rawVal * 0.6)

                                    var rgb = hsvToRgb(finalHue,
                                        Math.max(0, Math.min(255, saturation)),
                                        Math.max(0, Math.min(255, brightness)))

                                    var top = centerY0 - barH
                                    ctx.fillStyle = "rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + "," + (Math.min(255, alpha) / 255.0).toFixed(3) + ")"
                                    ctx.fillRect(x, top, 1, barH * 2)
                                }
                            } else if (root.durationMs > 1) {
                                paintAnalyzing(ctx)
                            }
                            var centerX = width / 2.0, centerY = height / 2.0
                            var maxBarH = (height / 2.0) * 0.90
                            ctx.lineCap = "butt"
                            ctx.strokeStyle = "rgba(255,255,255,0.45)"
                            ctx.lineWidth = 3
                            ctx.beginPath(); ctx.moveTo(centerX, centerY - maxBarH - 4); ctx.lineTo(centerX, centerY + maxBarH + 4); ctx.stroke()
                            ctx.strokeStyle = "rgba(255,255,255,1.0)"
                            ctx.lineWidth = 1
                            ctx.beginPath(); ctx.moveTo(centerX, centerY - maxBarH - 4); ctx.lineTo(centerX, centerY + maxBarH + 4); ctx.stroke()
                        }

                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            if (root.displayMode === 0) paintScratch(ctx)
                            else if (root.displayMode === 1) paintMinimal(ctx)
                            else paintBars(ctx)
                        }
                    }

                    MouseArea {
                        id: scratchArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: root.displayMode === 0 ? Qt.OpenHandCursor : Qt.PointingHandCursor

                        property bool isDraggingLocal: false
                        property bool isSpinningFreely: false
                        property real lastMouseX: 0
                        property real lastMoveTime: 0
                        property real currentVelocity: 0
                        property real scratchCurrentIndex: 0
                        property real pixelsPerSample: 1.5
                        property real basePixelsPerSample: 1.5
                        property real zoomLevel: 1.0

                        function nowSec() { return Date.now() / 1000.0 }

                        // Scratch rendering now happens entirely in
                        // waveformCanvas.paintScratch() (reads scratchCurrentIndex/
                        // pixelsPerSample directly) — this just triggers a repaint.
                        function requestScratchFrame() {
                            waveformCanvas.requestPaint()
                        }

                        onPressed: (mouse) => {
                            isDraggingLocal = true
                            if (root.displayMode !== 0) {
                                footerBridge.scratchModeChanged(true)
                                var ms = Math.max(0, Math.min((mouse.x / width) * root.durationMs, root.durationMs))
                                root.setLocalPosition(ms)
                                footerBridge.positionUpdated(Math.round(ms))
                                waveformCanvas.requestPaint()
                                return
                            }
                            isSpinningFreely = false
                            cursorShape = Qt.ClosedHandCursor
                            lastMouseX = mouse.x
                            lastMoveTime = nowSec()
                            currentVelocity = 0
                            footerBridge.scratchModeChanged(true)
                            footerBridge.velocityChanged(0.0)
                            decayTimer.start()
                        }

                        onPositionChanged: (mouse) => {
                            if (!isDraggingLocal) return
                            if (root.displayMode !== 0) {
                                var ms = Math.max(0, Math.min((mouse.x / width) * root.durationMs, root.durationMs))
                                root.setLocalPosition(ms)
                                footerBridge.positionUpdated(Math.round(ms))
                                waveformCanvas.requestPaint()
                                return
                            }
                            var totalSamples = root.samples.length
                            if (totalSamples < 2) return
                            var now = nowSec()
                            var dt = now - lastMoveTime
                            if (dt < 0.001) dt = 0.001
                            var deltaX = mouse.x - lastMouseX
                            var totalPixels = totalSamples * pixelsPerSample
                            var ratio = deltaX / totalPixels
                            var deltaMs = ratio * root.durationMs
                            currentVelocity = -(deltaMs / (dt * 1000.0))
                            footerBridge.velocityChanged(currentVelocity)

                            var samplesShifted = deltaX / pixelsPerSample
                            scratchCurrentIndex -= samplesShifted
                            scratchCurrentIndex = Math.max(0, Math.min(scratchCurrentIndex, totalSamples - 1))

                            var ratioIdx = scratchCurrentIndex / (totalSamples - 1)
                            root.setLocalPosition(Math.round(ratioIdx * root.durationMs))
                            footerBridge.positionUpdated(Math.round(root.displayPositionMs))

                            lastMouseX = mouse.x
                            lastMoveTime = now
                            decayTimer.start()
                            requestScratchFrame()
                        }

                        onReleased: (mouse) => {
                            if (!isDraggingLocal) return
                            isDraggingLocal = false
                            if (root.displayMode !== 0) {
                                var target = Math.max(0, Math.round(root.displayPositionMs))
                                footerBridge.seekRequested(target)
                                footerBridge.scratchModeChanged(false)
                                return
                            }
                            cursorShape = Qt.OpenHandCursor
                            if (Math.abs(currentVelocity) < 0.5) {
                                isSpinningFreely = false
                                decayTimer.stop()
                                var target2 = Math.max(0, Math.round(root.displayPositionMs))
                                footerBridge.seekRequested(target2)
                                footerBridge.scratchModeChanged(false)
                            } else {
                                isSpinningFreely = true
                            }
                        }

                        onWheel: (wheel) => {
                            if (root.displayMode !== 0) return
                            var delta = wheel.angleDelta.y + wheel.angleDelta.x
                            if (delta === 0) return
                            var zoomFactor = 1.0 + (0.15 * (Math.abs(delta) / 120.0))
                            if (delta > 0) zoomLevel *= zoomFactor
                            else zoomLevel /= zoomFactor
                            zoomLevel = Math.max(0.1, Math.min(zoomLevel, 5.0))
                            pixelsPerSample = basePixelsPerSample * zoomLevel
                            requestScratchFrame()
                        }

                        Timer {
                            id: decayTimer
                            interval: 20; repeat: true
                            onTriggered: {
                                if (scratchArea.isSpinningFreely) {
                                    var targetVel = root.isPlaying ? 1.0 : 0.0
                                    scratchArea.currentVelocity += (targetVel - scratchArea.currentVelocity) * 0.15
                                    var msShifted = scratchArea.currentVelocity * 20.0
                                    root.setLocalPosition(Math.max(0, Math.min(root.displayPositionMs + msShifted, root.durationMs)))
                                    var total = root.samples.length
                                    if (total > 1) {
                                        var ratio = root.durationMs > 0 ? root.displayPositionMs / root.durationMs : 0
                                        scratchArea.scratchCurrentIndex = ratio * (total - 1)
                                    }
                                    footerBridge.velocityChanged(scratchArea.currentVelocity)
                                    footerBridge.positionUpdated(Math.round(root.displayPositionMs))
                                    scratchArea.requestScratchFrame()

                                    if (Math.abs(scratchArea.currentVelocity - targetVel) < 0.05) {
                                        scratchArea.currentVelocity = targetVel
                                        footerBridge.velocityChanged(scratchArea.currentVelocity)
                                        scratchArea.isSpinningFreely = false
                                        decayTimer.stop()
                                        var target = Math.max(0, Math.round(root.displayPositionMs))
                                        footerBridge.seekRequested(target)
                                        footerBridge.scratchModeChanged(false)
                                    }
                                } else {
                                    if (scratchArea.nowSec() - scratchArea.lastMoveTime > 0.08) {
                                        scratchArea.currentVelocity *= 0.5
                                        if (Math.abs(scratchArea.currentVelocity) < 0.05) {
                                            scratchArea.currentVelocity = 0.0
                                            decayTimer.stop()
                                        }
                                        footerBridge.velocityChanged(scratchArea.currentVelocity)
                                    }
                                }
                            }
                        }
                    }

                    // Single clock driving displayPositionMs — extrapolates between
                    // Python's polled position updates (per UI_MANIFEST's
                    // FrameAnimation-not-Timer rule: Timer is capped ~60Hz, this
                    // tracks the real monitor refresh rate) and, in scratch mode,
                    // also drives the waveform auto-scroll (mirrors the old
                    // render_timer-driven _auto_scroll). Math.max against its own
                    // previous value in the formula below is what makes
                    // displayPositionMs structurally monotonic during playback —
                    // see the property comment near the top of this file.
                    FrameAnimation {
                        id: positionClock
                        running: root.isPlaying && !scratchArea.isDraggingLocal && !scratchArea.isSpinningFreely
                        onTriggered: {
                            var candidate = root.enginePositionMs + (Date.now() - root.enginePositionAtMs)
                            root.displayPositionMs = Math.min(root.durationMs, Math.max(root.displayPositionMs, candidate))

                            if (root.displayMode === 0) {
                                var total = root.samples.length
                                if (total >= 2 && root.durationMs > 0) {
                                    scratchArea.scratchCurrentIndex = (root.displayPositionMs / root.durationMs) * (total - 1)
                                    scratchArea.requestScratchFrame()
                                }
                            }
                            waveformCanvas.requestPaint()
                        }
                    }

                    // Waveform-mode toggle switch — fades in on hover, top-right.
                    Rectangle {
                        id: toggleWaveBtn
                        width: 28; height: 28; radius: 14
                        anchors.top: parent.top; anchors.right: parent.right
                        anchors.margins: 5
                        property bool hov: toggleArea.containsMouse
                        color: Qt.rgba(root.accentQColor.r, root.accentQColor.g, root.accentQColor.b,
                                        hov ? 0.4 : 0.1)
                        border.width: 2
                        border.color: Qt.rgba(root.accentQColor.r, root.accentQColor.g, root.accentQColor.b,
                                               hov ? 1.0 : 0.3)
                        opacity: waveformWrap.containsHover || hov ? 1.0 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 250 } }
                        Image {
                            anchors.centerIn: parent
                            width: 16; height: 16
                            sourceSize: Qt.size(16, 16)
                            cache: false; mipmap: true; smooth: true
                            source: tintedIcon("switch", toggleWaveBtn.hov ? "#ffffff" : "#515151")
                        }
                        MouseArea {
                            id: toggleArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: footerBridge.modeToggled((root.displayMode + 1) % 3)
                        }
                    }
                    property bool containsHover: hoverDetect.containsMouse
                    MouseArea {
                        id: hoverDetect
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                        z: -1
                    }

                    // Declared last so it paints on top of the Canvas above —
                    // see startupSettled comment near the top of this Item.
                    // Auto-hides itself forever after the timer fires, so
                    // unlike a permanent background it can't clip the play
                    // button's hover halo bleeding in from the row above —
                    // by the time a user could realistically hover that
                    // button, startup has long since finished.
                    Rectangle {
                        anchors.fill: parent
                        visible: !waveformWrap.startupSettled
                        color: root.footerBgColor()
                    }
                }

                Text {
                    id: totalTimeLbl
                    anchors.verticalCenter: parent.verticalCenter
                    width: timeWidthRef.implicitWidth
                    // Click to toggle between total duration and time
                    // remaining (counts down to 0 as displayPositionMs
                    // advances) — common transport-bar convention. Persisted
                    // Python-side (FooterPanel.show_remaining, settings key
                    // "show_remaining_time") so it survives a restart, same
                    // pattern as displayMode/modeToggled above.
                    text: root.showRemainingTime
                          ? "-" + root.formatTime(Math.max(0, root.durationMs - root.displayPositionMs))
                          : root.formatTime(root.durationMs)
                    color: root.accentColor
                    font.family: root.fontFamily
                    font.pixelSize: 14; font.bold: true

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: footerBridge.remainingToggled(!root.showRemainingTime)
                    }
                }
            }
        }
    }

    // ════════════════════════════════════════════════════════════════════════
    // RIGHT — volume, cast, settings
    // ════════════════════════════════════════════════════════════════════════
    Item {
        id: rightBlock
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: Math.max(220, root.width * 0.26)

        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8

            IconButton {
                width: 40; height: 40; radius: 20
                iconSize: 20
                hoverColor: root.hoverColor
                iconSource: root.tintedIcon("settings", root.accentColor)
                onTriggered: footerBridge.settingsClicked()
                onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Settings", cx, ay, by)
                onHoverExited: footerBridge.hideTooltip()
            }

            IconButton {
                width: 40; height: 40; radius: 20
                iconSize: 29
                hoverColor: root.hoverColor
                iconSource: root.tintedIcon(root.isMuted ? "volume_mute" : "volume", root.isMuted ? "#888888" : root.accentColor)
                onTriggered: footerBridge.muteClicked()
                onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Mute/Unmute", cx, ay, by)
                onHoverExited: footerBridge.hideTooltip()
            }

            Item {
                id: volSliderWrap
                width: 100; height: 24
                anchors.verticalCenter: parent.verticalCenter

                readonly property int handleW: 14
                readonly property real grooveW: width - handleW
                readonly property real ratio: Math.max(0, Math.min(1, root.volume / 100.0))
                readonly property real handleX: (handleW / 2) + ratio * grooveW

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    x: volSliderWrap.handleW / 2
                    width: volSliderWrap.grooveW; height: 5; radius: 2.5
                    color: "#333333"
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    x: volSliderWrap.handleW / 2
                    width: Math.max(0, volSliderWrap.handleX - volSliderWrap.handleW / 2)
                    height: 5; radius: 2.5
                    color: root.accentColor
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    x: volSliderWrap.handleX - 7
                    width: 14; height: 14; radius: 7
                    color: root.accentColor
                }

                MouseArea {
                    id: volArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor

                    function valueFromMouse(mx) {
                        var padding = volSliderWrap.handleW / 2
                        var x = Math.max(0, Math.min(mx - padding, volSliderWrap.grooveW))
                        var ratio = volSliderWrap.grooveW > 0 ? x / volSliderWrap.grooveW : 0
                        return Math.round(ratio * 100)
                    }
                    // Routed through the same native showTooltip/hideTooltip
                    // used by the rest of the footer's buttons (matches the
                    // pre-rewrite ClickableSlider's TriangleTooltip — themed
                    // colors + drop shadow — instead of the plain flat
                    // Rectangle bubble the QML rewrite originally used here).
                    // Pass the just-computed value directly rather than
                    // root.volume, which lags a frame behind during drag
                    // (round-trips through footerBridge.volumeChangedByUser).
                    function showVolTooltip(val) {
                        var a = mapToGlobal(volSliderWrap.handleX, -4)
                        var b = mapToGlobal(volSliderWrap.handleX, height + 4)
                        footerBridge.showTooltip(val + "%", a.x, a.y, b.y)
                    }
                    onEntered: showVolTooltip(root.volume)
                    onExited: { if (!pressed) footerBridge.hideTooltip() }
                    onPressed: (mouse) => {
                        var v = valueFromMouse(mouse.x)
                        footerBridge.volumeChangedByUser(v)
                        showVolTooltip(v)
                    }
                    onPositionChanged: (mouse) => {
                        if (pressed) {
                            var v = valueFromMouse(mouse.x)
                            footerBridge.volumeChangedByUser(v)
                            showVolTooltip(v)
                        }
                    }
                    onReleased: {
                        if (containsMouse) showVolTooltip(root.volume)
                        else footerBridge.hideTooltip()
                    }
                }
            }

            IconButton {
                width: 40; height: 40; radius: 20
                iconSize: 22
                hoverColor: root.hoverColor
                iconSource: root.tintedIcon("cast", root.castConnected ? root.accentColor : "#555555")
                onTriggered: footerBridge.castClicked()
                onHoverEntered: (cx, ay, by) => footerBridge.showTooltip("Cast to device", cx, ay, by)
                onHoverExited: footerBridge.hideTooltip()
            }
        }
    }
}
