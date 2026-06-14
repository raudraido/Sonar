"""
hotkeys.py — Central registry of all rebindable keyboard shortcuts.

DEFAULT_HOTKEYS defines the canonical list. HotkeyManager loads saved
bindings from QSettings, exposes them to window.py for QShortcut
creation, and handles live rebinding when the user changes a key.
"""

DEFAULT_HOTKEYS = [
    # (id,                 description,              default_key)
    ("play_pause",         "Play / Pause",           "Space"),
    ("seek_back",          "Seek Back 5s",           "Shift+Left"),
    ("seek_fwd",           "Seek Forward 5s",        "Shift+Right"),
    ("prev_track",         "Previous Track",         "Ctrl+Left"),
    ("next_track",         "Next Track",             "Ctrl+Right"),
    ("nav_back",           "Navigate Back",          "Alt+Left"),
    ("nav_fwd",            "Navigate Forward",       "Alt+Right"),
    ("next_tab",           "Next Tab",               "Ctrl+Tab"),
    ("prev_tab",           "Previous Tab",           "Ctrl+Shift+Tab"),
    ("vol_up",             "Volume Up",              "Ctrl+Up"),
    ("vol_down",           "Volume Down",            "Ctrl+Down"),
    ("mute",               "Toggle Mute",            "Ctrl+M"),
    ("shuffle",            "Toggle Shuffle",         "Ctrl+S"),
    ("repeat",             "Toggle Repeat",          "Ctrl+R"),
    ("spotlight",          "Spotlight Search",       "Ctrl+F"),
    ("local_search",       "Local Search",           "/"),
    ("local_search_alt",   "Local Search (Alt)",     "Ctrl+Shift+F"),
]


class HotkeyManager:
    def __init__(self, settings):
        self.settings = settings
        self._bindings  = {}   # id -> current key string
        self._shortcuts = {}   # id -> QShortcut instance
        self._load()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self):
        for hid, _desc, default in DEFAULT_HOTKEYS:
            self._bindings[hid] = self.settings.value(f"hotkey/{hid}", default)

    # ── read ──────────────────────────────────────────────────────────────────

    def get(self, hid):
        for h, _desc, default in DEFAULT_HOTKEYS:
            if h == hid:
                return self._bindings.get(hid, default)
        return ""

    def default(self, hid):
        for h, _desc, default in DEFAULT_HOTKEYS:
            if h == hid:
                return default
        return ""

    # ── QShortcut factory ─────────────────────────────────────────────────────

    def register(self, hid, parent, callback):
        """Create and remember a QShortcut wired to the saved (or default) key."""
        from PyQt6.QtGui import QShortcut, QKeySequence
        sc = QShortcut(QKeySequence(self.get(hid)), parent)
        sc.activated.connect(callback)
        self._shortcuts[hid] = sc
        return sc

    # ── write ─────────────────────────────────────────────────────────────────

    def rebind(self, hid, new_key):
        """Change a shortcut's key, save it, and update the live QShortcut."""
        from PyQt6.QtGui import QKeySequence
        self._bindings[hid] = new_key
        self.settings.setValue(f"hotkey/{hid}", new_key)
        if hid in self._shortcuts:
            self._shortcuts[hid].setKey(QKeySequence(new_key))

    def reset(self, hid):
        self.rebind(hid, self.default(hid))

    def reset_all(self):
        for hid, _desc, _default in DEFAULT_HOTKEYS:
            self.rebind(hid, self.default(hid))
