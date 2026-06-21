import QtQuick

// Shared rounded-rect card background — the bordered panel used by
// playlist/album detail header cards and (via this component) every card
// section on the Now Playing page. Host sets width/height; content is
// layered on top by the host (this is just the backdrop).
Rectangle {
    radius: 10
    color: cardBgColor
    border.color: cardBorderColor
    border.width: 1

    property string cardBgColor:     "#1e1e1e"
    property string cardBorderColor: "#2a2a2a"
}
