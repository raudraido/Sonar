"""
player — Core application package for Sonar Music Player.

Submodules
----------
player.workers  — Background QThread workers (BPM, blur, cover art, etc.)
player.widgets  — Custom Qt widgets (sliders, tooltips, settings window, etc.)
player.window   — SonarPlayer main window (composes the mixins below)

player.mixins.playback    — Transport, queue management, BPM, drag/drop
player.mixins.navigation  — Tab routing, back/forward, spotlight
player.mixins.visuals     — Cover art, theming, indicators
player.mixins.keyboard    — keyPressEvent, eventFilter, shortcuts
player.mixins.persistence — Save/load, server connection, lifecycle
"""

import os
import sys

def resource_path(relative_path: str) -> str:
    """Resolve a resource path for both dev mode and PyInstaller bundles."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
