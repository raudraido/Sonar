import QtQuick
import "../shared_qml"

// Tracks tab host — thin wrapper around the shared TrackListView. No
// headerCard (the page header is just the toolbar/column-titles card, pinned
// via stickyHeader). Pagination is the sticky in-card footer (enablePagination)
// rather than a separate QWidget below the QQuickView, so header/rows/footer
// read as one continuous card while paginating/sorting/filtering.
Rectangle {
    id: root
    color: "transparent"
    focus: true

    // ── Theme ──────────────────────────────────────────────────────────────
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
    property bool   tracksLoading:   false
    property string trackCountText:  ""
    property bool   filtersActive:   false
    property int    currentPage:     1
    property int    totalPages:      1

    Connections {
        target: tracksBridge
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
        function onTracksLoadingChanged(v)      { root.tracksLoading     = v }
        function onTrackCountChanged(t)         { root.trackCountText    = t }
        function onFiltersActiveChanged(v)      { root.filtersActive     = v }
        function onCurrentPageChanged(p)        { root.currentPage       = p }
        function onTotalPagesChanged(p)         { root.totalPages        = p }
    }

    TrackListView {
        id: trackListView
        anchors.fill: parent
        bridge:            tracksBridge
        trackListModel:    tracksModel
        enableRowReorder:  false
        enableTrackColumn: true
        enableAlbumColumn: true
        enableMultiSelect: true
        elasticCol:        "track"
        iconProvider:       "albumicons"
        trackThumbProvider: "trackscovers"
        tracksLoading:      root.tracksLoading
        filterableCols:     ["artist", "album", "year", "genre"]
        enableOwnSearch:           true
        searchIsServerSide:        true
        enableRefreshButton:       true
        enableClearFiltersButton:  true
        enablePlayFilteredButton:  true
        filtersActive:             root.filtersActive
        toolbarStatusText:         root.trackCountText
        stickyHeader:              true
        enablePagination:          true
        currentPage:               root.currentPage
        totalPages:                root.totalPages

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
    }

    Component.onCompleted: {
        var s = tracksBridge.getPaginationState()
        root.currentPage = s[0]; root.totalPages = s[1]
    }
}
