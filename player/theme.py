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

    # ── Panel background (RGB components, used in rgba()) ────────────────────
    panel_color: str = "12,12,12"        # left panel, queue, main content
    footer_color: str = "11,11,11"       # footer bar

    # ── Alpha values (0.0 = fully transparent, 1.0 = fully opaque) ──────────
    # Settings sliders use the inverted user convention: 0 % = solid, 100 % = see-through
    content_alpha: float = 0.75          # tab content panes
    footer_alpha: float = 0.85           # footer bar
    panel_alpha: float = 0.96            # left panel + queue sidebar

    # ── Border ───────────────────────────────────────────────────────────────
    border_width: int = 2                # accent border thickness in px

    # ── Background image processing ──────────────────────────────────────────
    blur: float = 2.5                    # blur radius 0 – 5
    overlay: float = 0.25               # darkness overlay 0 – 1

    # ── Convenience builders ─────────────────────────────────────────────────
    def panel_bg(self, alpha: float | None = None) -> str:
        return f"rgba({self.panel_color},{alpha if alpha is not None else self.panel_alpha})"

    def footer_bg(self, alpha: float | None = None) -> str:
        return f"rgba({self.footer_color},{alpha if alpha is not None else self.footer_alpha})"

    def content_bg(self, alpha: float | None = None) -> str:
        return f"rgba({self.panel_color},{alpha if alpha is not None else self.content_alpha})"

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
    def from_legacy(visual_settings: dict, master_color: str, dynamic_color: bool) -> "Theme":
        """Migrate old visual_settings dict + loose colour fields to a Theme."""
        t = Theme()
        t.accent         = master_color or t.accent
        t.dynamic_accent = dynamic_color
        t.content_alpha  = visual_settings.get('bg_alpha',      t.content_alpha)
        t.footer_alpha   = visual_settings.get('footer_alpha',  t.footer_alpha)
        t.panel_alpha    = visual_settings.get('queue_alpha',   t.panel_alpha)
        t.blur           = visual_settings.get('blur',          t.blur)
        t.overlay        = visual_settings.get('overlay',       t.overlay)
        return t
