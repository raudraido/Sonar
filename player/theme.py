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

    # ── Border ───────────────────────────────────────────────────────────────
    border_width: int = 1                # accent border thickness in px

    # Fields that are code constants — never saved to or loaded from QSettings.
    _NO_PERSIST = frozenset({"border_width"})

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
        """Migrate old visual_settings dict + loose colour fields to a Theme."""
        t = Theme()
        t.accent         = master_color or t.accent
        t.dynamic_accent = dynamic_color
        return t
