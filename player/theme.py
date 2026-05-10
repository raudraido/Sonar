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

    # ── Panel background (RGB components) ────────────────────────────────────
    panel_color: str = "12,12,12"        # left panel, queue, main content
    footer_color: str = "11,11,11"       # footer bar

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
