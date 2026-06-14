import QtQuick

// Reusable animated search box: a search/close icon button that expands into
// a text field. Driven entirely through the public API below so it can be
// reused from any QML view (album detail tracklist, albums grid, etc.).
//
// Usage:
//   SearchBar {
//       anchors.right: parent.right
//       anchors.top: parent.top
//       anchors.bottom: parent.bottom
//       accentColor: root.accentColor
//       ...theme props...
//       placeholderText: "Search tracks..."
//       onOpened: bridge.searchCtl.setSearchActive(true)
//       onClosed: bridge.searchCtl.setSearchActive(false)
//   }
//
// Python drives the text field via the SearchController signals
// (searchOpen/searchClose/searchReset/searchTextAppend/searchTextBackspace),
// connected through Connections calling open()/close()/reset()/appendChar()/backspace().
Item {
    id: searchBar

    // ── Theme / styling (bind from the host view) ──────────────────────────
    property string accentColor:      "#1db954"
    property string textPrimary:      "#eeeeee"
    property string textSecondary:    "#999999"
    property string panelBgColor:     "#181818"
    property string borderColor:      "#2a2a2a"
    property string hoverColor:       "#333333"
    property string fontFamily:       ""
    property int    fontSizeSecondary: 12
    property string placeholderText:  "Search..."

    // ── Public state ────────────────────────────────────────────────────────
    readonly property string searchText: input.text
    readonly property bool   isOpen:     expandedWidth > 0

    property real expandedWidth: 0
    Behavior on expandedWidth { NumberAnimation { duration: 250; easing.type: Easing.InOutQuart } }

    width: 32 + expandedWidth
    height: parent ? parent.height : 32

    signal opened()
    signal closed()

    // ── Public API (called from Python via Connections) ───────────────────
    function open() {
        if (expandedWidth === 0) {
            expandedWidth = 204
            opened()
        }
    }
    function close() {
        input.text = ""
        if (expandedWidth > 0) {
            expandedWidth = 0
            closed()
        }
    }
    function reset() {
        input.text = ""
        if (expandedWidth > 0) {
            expandedWidth = 0
            closed()
        }
    }
    function appendChar(ch) { input.text += ch }
    function backspace() {
        if (input.text.length > 0)
            input.text = input.text.slice(0, -1)
    }
    function toggle() {
        if (expandedWidth > 0) {
            if (!input.text) close()
        } else {
            open()
        }
    }

    Rectangle {
        id: inputBox
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(0, searchBar.expandedWidth - 4)
        height: 28; radius: 4
        color: searchBar.panelBgColor
        border.color: searchBar.borderColor; border.width: 1
        clip: true
        visible: width > 2

        TextInput {
            id: input
            anchors.left: parent.left; anchors.leftMargin: 8
            anchors.right: clearBtn.visible ? clearBtn.left : parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            color: searchBar.textPrimary
            font.pixelSize: 13
            font.family: searchBar.fontFamily
            selectionColor: searchBar.accentColor
            selectedTextColor: "#111"
            clip: true
        }

        Text {
            anchors.left: parent.left; anchors.leftMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: searchBar.placeholderText
            color: searchBar.textSecondary
            font.pixelSize: searchBar.fontSizeSecondary
            font.family: searchBar.fontFamily
            visible: !input.text
        }

        Item {
            id: clearBtn
            width: 24; height: parent.height
            anchors.right: parent.right
            visible: input.text !== ""

            Image {
                anchors.centerIn: parent; width: 10; height: 10
                source: "image://albumicons/sub_close_" + searchBar.textSecondary.replace("#", "")
                cache: false; mipmap: true; smooth: true
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: input.text = ""
            }
        }
    }

    Item {
        id: iconArea
        width: 32; height: parent.height
        anchors.right: parent.right

        Rectangle {
            anchors.fill: parent; radius: 4
            color: iconHov.containsMouse ? searchBar.hoverColor : "transparent"
        }
        Image {
            anchors.centerIn: parent; width: 18; height: 18
            source: "image://albumicons/search_" + searchBar.accentColor.replace("#", "")
            cache: false; mipmap: true; smooth: true
        }
        MouseArea {
            id: iconHov
            anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: searchBar.toggle()
        }
    }
}
