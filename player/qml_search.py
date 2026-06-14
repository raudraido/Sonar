"""Shared search-bar plumbing for QQuickWidget views.

Pairs with SearchBar.qml. A view exposes a SearchController instance to QML
(e.g. as a context property), connects SearchBar.opened/closed to
controller.setSearchActive, and installs a SearchKeyFilter on its
QQuickWidget so keystrokes are routed into the search box while it's active.
"""

from PyQt6.QtCore import QObject, QEvent, Qt, QDateTime, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QShortcut


class SearchController(QObject):
    """Search-bar state shared between a QML SearchBar and its Python host.

    `on_active_changed(active)` is called whenever the search box opens or
    closes (e.g. to toggle window shortcuts). `on_text_changed(text)` is
    called whenever the search text changes via append/backspace/reset.
    """

    searchOpen          = pyqtSignal()
    searchClose         = pyqtSignal()
    searchReset         = pyqtSignal()
    searchTextAppend    = pyqtSignal(str)
    searchTextBackspace = pyqtSignal()

    def __init__(self, on_active_changed=None, on_text_changed=None, parent=None):
        super().__init__(parent)
        self.active = False
        self.text = ""
        self._on_active_changed = on_active_changed
        self._on_text_changed = on_text_changed
        self._activated_at = 0

    @pyqtSlot(bool)
    def setSearchActive(self, active: bool):
        now = QDateTime.currentMSecsSinceEpoch()
        if not active and self.active:
            if now - self._activated_at < 50:
                return
        self.active = active
        if active:
            self._activated_at = now
        else:
            self._set_text("")
        if self._on_active_changed:
            self._on_active_changed(active)

    def _set_text(self, text: str):
        self.text = text
        if self._on_text_changed:
            self._on_text_changed(text)

    def append(self, ch: str):
        self._set_text(self.text + ch)
        self.searchTextAppend.emit(ch)

    def backspace(self):
        self._set_text(self.text[:-1])
        self.searchTextBackspace.emit()

    def open(self):
        self.searchOpen.emit()

    def close(self):
        self.searchClose.emit()

    def reset(self):
        self.searchReset.emit()

    def restore(self, text: str):
        """Silently populate the search box (e.g. restoring saved state)
        without firing on_text_changed."""
        if not text:
            return
        self.text = text
        self.active = True
        self._activated_at = QDateTime.currentMSecsSinceEpoch()
        self.searchOpen.emit()
        for ch in text:
            self.searchTextAppend.emit(ch)

class SearchKeyFilter(QObject):
    """Widget-level key filter — fires regardless of QML focus state.

    While `controller.active`, printable keys are routed into the search
    box and Escape/Backspace are handled directly. Any other key (arrows,
    enter, page up/down, etc.) is passed to `on_navigate(event)` if given.
    """

    def __init__(self, controller: SearchController, on_navigate=None, parent=None):
        super().__init__(parent)
        self._ctl = controller
        self._on_navigate = on_navigate

    def eventFilter(self, obj, event):
        ctl = self._ctl
        if event.type() == QEvent.Type.ShortcutOverride:
            if ctl.active:
                event.accept()
                return True
        if event.type() != QEvent.Type.KeyPress:
            return False
        if ctl.active:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                ctl.close()
                return True
            if key == Qt.Key.Key_Backspace:
                ctl.backspace()
                return True
            ch = event.text()
            if ch and ch.isprintable():
                ctl.append(ch)
                return True
            # let navigation keys (arrows, enter, etc.) fall through
        if self._on_navigate:
            return self._on_navigate(event)
        return False

class GridSearchKeyFilter(SearchKeyFilter):
    """Widget-level key filter for a grid view's inline search box.

    Routes typing into the grid search box while active. If Return is
    pressed while a search is still debouncing, commits it instantly and
    jumps focus to the first grid item. Once the search has settled (or
    isn't active), Return falls through unhandled to the QML GridView's
    own Keys.onPressed, which opens the currently-selected item.
    """

    def __init__(self, view, parent=None):
        super().__init__(view.grid_bridge.search, on_navigate=self._navigate, parent=parent)
        self._view = view

    def _navigate(self, event):
        if (self._ctl.active and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self._view.search_timer.isActive()):
            self._view.focus_first_grid_item()
            return True
        return False

def set_window_shortcuts_enabled(host, qml_widget, enabled: bool):
    """Enable/disable all QShortcuts on the top-level window, and flag
    `qml_widget` so the global type-to-search interceptor leaves it alone
    while a local QML search box is active."""
    main = host.window()
    if main is None:
        return
    qml_widget._search_active = not enabled
    for sc in main.findChildren(QShortcut):
        sc.setEnabled(enabled)