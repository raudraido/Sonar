"""player/theme.py — Single source of truth for all visual settings."""
from __future__ import annotations
import json
import dataclasses
from dataclasses import dataclass


@dataclass
class Theme:
    name: str = "Default"

    # ── Accent colour ────────────────────────────────────────────────────────
    accent: str = "#fafafa"
    dynamic_accent: bool = True          # follow album art dominant colour
    auto_bg_from_accent: bool = True     # derive panel BGs as a dim tint of the accent

    # ── Panel backgrounds (RGB components, no "rgb()" wrapper) ───────────────
    left_panel_bg:   str = "14,14,14"
    queue_panel_bg:  str = "14,14,14"
    footer_panel_bg: str = "14,14,14"
    main_panel_bg:   str = "14,14,14"
    header_panel_bg: str = "14,14,14"

    # ── Now Playing card background ──────────────────────────────────────────
    now_playing_card_bg: str = "#1e1e1e"

    # ── Font family ──────────────────────────────────────────────────────────
    app_font: str = ""              # empty = system default (Segoe UI)

    # ── Typography ───────────────────────────────────────────────────────────
    font_size_primary:   int = 14
    font_size_secondary: int = 12
    font_color_primary:   str = "#dddddd"
    font_color_secondary: str = "#999999"

    # ── Queue panel font size offset (-5 … +5 relative to global sizes) ──────
    queue_font_size_offset: int = 0

    # ── Menu hover ───────────────────────────────────────────────────────────
    auto_menu_hover:  bool = True
    menu_hover_color: str  = "#555555"

    # ── Border ───────────────────────────────────────────────────────────────
    border_width: int = 1
    border_color: str = "#0e0e0e"        # runtime-computed; never persisted
    auto_border_from_accent: bool = True
    manual_border_color: str = "#2a2a2a"

    # Only truly runtime/computed fields are excluded from serialisation.
    _NO_PERSIST = frozenset({"border_width", "border_color"})

    # ── Serialisation ────────────────────────────────────────────────────────
    def to_json(self) -> str:
        d = dataclasses.asdict(self)
        for k in Theme._NO_PERSIST:
            d.pop(k, None)
        return json.dumps(d)

    @staticmethod
    def from_json(s: str) -> "Theme":
        try:
            d = json.loads(s)
            valid = {f.name for f in dataclasses.fields(Theme)} - Theme._NO_PERSIST
            return Theme(**{k: v for k, v in d.items() if k in valid})
        except Exception:
            return Theme()

    @staticmethod
    def from_legacy(_visual_settings: dict, master_color: str, dynamic_color: bool) -> "Theme":
        t = Theme()
        t.accent         = master_color or t.accent
        t.dynamic_accent = dynamic_color
        return t


def load_presets() -> dict[str, "Theme"]:
    """Load all *.json files from player/themes/ as named Theme presets."""
    import os, glob
    themes_dir = os.path.join(os.path.dirname(__file__), "themes")
    presets: dict[str, Theme] = {}
    for path in sorted(glob.glob(os.path.join(themes_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            t = Theme.from_json(raw)
            presets[t.name] = t
        except Exception:
            pass
    return presets
