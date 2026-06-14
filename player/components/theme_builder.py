"""theme_builder.py — Live theme editor dialog."""
from __future__ import annotations
import os, sys, json, dataclasses
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QCheckBox, QSpinBox, QComboBox, QScrollArea, QWidget, QFrame, QSizePolicy,
    QColorDialog, QApplication, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFontDatabase

from player.theme import Theme
from player.mixins.visuals import resolve_menu_hover


def _rgb_str_to_hex(rgb: str) -> str:
    try:
        r, g, b = (int(x.strip()) for x in rgb.split(','))
        return QColor(r, g, b).name()
    except Exception:
        return '#141414'


def _hex_to_rgb_str(hex_color: str) -> str:
    c = QColor(hex_color)
    return f"{c.red()},{c.green()},{c.blue()}"


class _ColorBtn(QPushButton):
    def __init__(self, color: str, label: str, callback, parent=None):
        super().__init__(parent)
        self._color = color
        self._cb = callback
        self._label = label
        self.setFixedSize(110, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self):
        c = QColor(self._color)
        luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        fg = '#000000' if luma > 128 else '#ffffff'
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; color: {fg}; "
            f"border: 1px solid rgba(255,255,255,0.15); border-radius: 4px; "
            f"font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ border: 1px solid rgba(255,255,255,0.4); }}"
        )
        self.setText(self._color.upper())

    def set_color(self, color: str):
        self._color = color
        self._refresh()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, self._label,
                                   QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self._color = c.name()
            self._refresh()
            self._cb(self._color)


class _Toggle(QCheckBox):
    def __init__(self, checked: bool, callback, accent: str, parent=None):
        super().__init__(parent)
        self.setChecked(checked)
        self._accent = accent
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(callback)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._accent if self.isChecked() else '#444444'))
        p.drawRoundedRect(0, 0, self.width(), self.height(), 11, 11)
        p.setBrush(QColor('#ffffff'))
        x = self.width() - 20 if self.isChecked() else 2
        p.drawEllipse(x, 2, 18, 18)
        p.end()


class ThemeBuilderDialog(QDialog):
    def __init__(self, main_win, parent=None):
        super().__init__(parent,
                         Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.setFixedWidth(680)
        self._main = main_win
        self._drag_pos = None
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(80)
        self._live_timer.timeout.connect(self._apply_live)
        self._pending = {}

        t = main_win.theme
        theme = getattr(main_win, 'theme', None)
        bg  = getattr(theme, 'main_panel_bg',       '28,28,28')
        bc  = getattr(theme, 'border_color',         '#333333')
        fc1 = getattr(theme, 'font_color_primary',   '#dddddd')
        fc2 = getattr(theme, 'font_color_secondary', '#888888')
        acc = getattr(theme, 'accent',               '#ffffff')

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._bg = QFrame()
        self._bg.setObjectName('themeBuilderBg')
        self._bg.setStyleSheet(f"""
            QFrame#themeBuilderBg {{
                background: rgb({bg});
                border: 1px solid {bc};
                border-radius: 12px;
            }}
        """)
        def _press(e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = e.globalPosition().toPoint()
        def _move(e):
            if self._drag_pos:
                self.move(self.pos() + e.globalPosition().toPoint() - self._drag_pos)
                self._drag_pos = e.globalPosition().toPoint()
        def _release(e):
            self._drag_pos = None
        self._bg.mousePressEvent   = _press
        self._bg.mouseMoveEvent    = _move
        self._bg.mouseReleaseEvent = _release

        inner = QVBoxLayout(self._bg)
        inner.setContentsMargins(20, 16, 20, 16)
        inner.setSpacing(12)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        lbl = QLabel('Theme Builder')
        lbl.setStyleSheet(f'color: {fc1}; font-size: 15px; font-weight: bold; background: transparent;')
        title_row.addWidget(lbl)
        title_row.addStretch()
        close_btn = QPushButton('✕')
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f'QPushButton {{ background: transparent; color: {fc2}; border: none; font-size: 14px; }} QPushButton:hover {{ color: {fc1}; }}')
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn)
        inner.addLayout(title_row)

        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setStyleSheet(f'color: {bc};')
        inner.addWidget(sep0)

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: rgba(255,255,255,0.06); width: 8px;
                border-radius: 4px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {acc}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {acc}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        content = QWidget()
        content.setStyleSheet('background: transparent;')
        self._form = QVBoxLayout(content)
        self._form.setContentsMargins(0, 0, 8, 0)
        self._form.setSpacing(14)
        scroll.setWidget(content)
        inner.addWidget(scroll, 1)

        # ── Build rows ────────────────────────────────────────────────────────
        self._acc = acc
        self._fc1 = fc1; self._fc2 = fc2; self._bc = bc

        self._add_section('Accent')
        self._add_color_row('Accent Color', 'accent', t.accent, is_rgb=False)
        self._add_toggle_row('Dynamic accent (follows album art)', 'dynamic_accent', t.dynamic_accent)
        self._add_toggle_row('Auto backgrounds from accent', 'auto_bg_from_accent', t.auto_bg_from_accent)

        self._add_section('Panel Backgrounds')
        for key, label in [
            ('main_panel_bg',   'Main Panel'),
            ('left_panel_bg',   'Left Panel'),
            ('queue_panel_bg',  'Queue Panel'),
            ('footer_panel_bg', 'Footer Panel'),
            ('header_panel_bg', 'Header Panel'),
        ]:
            self._add_color_row(label, key, getattr(t, key), is_rgb=True)
        self._add_color_row('Now Playing Cards', 'now_playing_card_bg', t.now_playing_card_bg, is_rgb=False)
        self._add_color_row('Skeleton / Placeholders', 'skeleton_base', t.skeleton_base, is_rgb=False)

        self._add_section('Typography')
        self._add_font_row('Font Family', 'app_font', t.app_font)
        self._add_color_row('Primary Text Color',   'font_color_primary',   t.font_color_primary,   is_rgb=False)
        self._add_color_row('Secondary Text Color', 'font_color_secondary', t.font_color_secondary, is_rgb=False)
        self._add_spin_row('Primary Font Size',   'font_size_primary',   t.font_size_primary,   8, 24)
        self._add_spin_row('Secondary Font Size', 'font_size_secondary', t.font_size_secondary, 8, 20)
        self._add_spin_row('Queue Font Size Offset', 'queue_font_size_offset', t.queue_font_size_offset, -5, 5)

        self._add_section('Border')
        self._add_toggle_row('Auto border from accent', 'auto_border_from_accent', t.auto_border_from_accent)
        self._add_color_row('Manual Border Color', 'manual_border_color', t.manual_border_color, is_rgb=False)
        self._add_spin_row('Border Width', 'border_width', t.border_width, 0, 4)

        self._add_section('Active Hover')
        self._add_toggle_row('Auto (from accent)', 'active_hover_auto', t.active_hover_auto)
        self._add_color_row('Hover Color', 'active_hover_color', t.active_hover_color, is_rgb=False)

        self._add_section('Menu Hover')
        self._add_toggle_row('Auto hover color', 'auto_menu_hover', t.auto_menu_hover)
        self._add_color_row('Hover Color', 'menu_hover_color', t.menu_hover_color, is_rgb=False)

        self._form.addStretch()

        # ── Bottom buttons ─────────────────────────────────────────────────────
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f'color: {bc};')
        inner.addWidget(sep1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_style = f"""QPushButton {{
            background: transparent; color: {fc2};
            border: 1px solid {bc}; border-radius: 4px;
            font-size: 12px; font-weight: bold; padding: 7px 16px;
        }} QPushButton:hover {{ background: {resolve_menu_hover(theme)}; color: {fc1}; }}"""

        save_btn = QPushButton('Save as Preset')
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(btn_style)
        save_btn.clicked.connect(self._save_preset)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()

        reset_btn = QPushButton('Reset')
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet(btn_style)
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)

        done_btn = QPushButton('Done')
        done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        done_btn.setStyleSheet(btn_style.replace(fc2, fc1, 1))
        done_btn.clicked.connect(self.accept)
        btn_row.addWidget(done_btn)
        inner.addLayout(btn_row)

        root.addWidget(self._bg)

    # ── Section helpers ────────────────────────────────────────────────────────

    def _add_section(self, title: str):
        lbl = QLabel(title.upper())
        lbl.setStyleSheet(f'color: {self._fc2}; font-size: 10px; font-weight: bold; letter-spacing: 2px; background: transparent;')
        self._form.addWidget(lbl)

    def _row_wrap(self, label_text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f'color: {self._fc1}; font-size: 13px; background: transparent;')
        lbl.setFixedWidth(200)
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(widget)
        w = QWidget(); w.setLayout(row); w.setStyleSheet('background: transparent;')
        self._form.addWidget(w)
        return row

    def _add_color_row(self, label: str, key: str, value: str, is_rgb: bool):
        hex_val = _rgb_str_to_hex(value) if is_rgb else value

        def on_change(hex_color: str):
            self._pending[key] = _hex_to_rgb_str(hex_color) if is_rgb else hex_color
            self._live_timer.start()

        btn = _ColorBtn(hex_val, label, on_change)
        self._row_wrap(label, btn)

    def _add_toggle_row(self, label: str, key: str, value: bool):
        def on_change(checked: bool):
            self._pending[key] = checked
            self._live_timer.start()

        tog = _Toggle(value, on_change, self._acc)
        self._row_wrap(label, tog)

    def _add_spin_row(self, label: str, key: str, value: int, mn: int, mx: int):
        spin = QSpinBox()
        spin.setRange(mn, mx)
        spin.setValue(value)
        spin.setFixedWidth(70)
        spin.setStyleSheet(
            f'QSpinBox {{ background: transparent; color: {self._fc1}; '
            f'border: 1px solid {self._bc}; border-radius: 4px; padding: 4px 8px; font-size: 13px; }}'
        )

        def on_change(v: int):
            self._pending[key] = v
            self._live_timer.start()

        spin.valueChanged.connect(on_change)
        self._row_wrap(label, spin)

    def _add_font_row(self, label: str, key: str, current: str):
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        fonts_dir = os.path.join(base, 'fonts')
        families: list[str] = []
        if os.path.isdir(fonts_dir):
            for fname in sorted(os.listdir(fonts_dir)):
                if fname.lower().endswith(('.ttf', '.otf')):
                    fid = QFontDatabase.addApplicationFont(os.path.join(fonts_dir, fname))
                    if fid >= 0:
                        for fam in QFontDatabase.applicationFontFamilies(fid):
                            if fam not in families:
                                families.append(fam)

        combo = QComboBox()
        combo.addItem('System Default', '')
        for fam in families:
            combo.addItem(fam, fam)
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.setFixedWidth(160)
        combo.setStyleSheet(
            f'QComboBox {{ background: transparent; color: {self._fc1};'
            f' border: 1px solid {self._bc}; border-radius: 4px; padding: 4px 8px; font-size: 13px; }}'
            f'QComboBox::drop-down {{ border: none; }}'
            f'QComboBox QAbstractItemView {{ background: #1e1e1e; color: {self._fc1}; selection-background-color: {self._bc}; }}'
        )

        def on_change(_):
            self._pending[key] = combo.currentData()
            self._live_timer.start()

        combo.currentIndexChanged.connect(on_change)
        self._row_wrap(label, combo)

    # ── Live apply ─────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            pg = self.parent().geometry()
            self.move(pg.x() + (pg.width() - self.width()) // 2,
                      pg.y() + (pg.height() - self.height()) // 2)

    def _apply_live(self):
        t = self._main.theme
        for k, v in self._pending.items():
            if k != 'border_color':
                setattr(t, k, v)
        self._pending.clear()
        self._main._last_theme_key = None
        if t.auto_bg_from_accent:
            self._main._auto_tint_bg_colors()
        self._main.refresh_ui_styles()

    # ── Save / Reset ───────────────────────────────────────────────────────────

    def _save_preset(self):
        t = self._main.theme
        name, ok = _ask_name(self, 'Save Preset', 'Preset name:', t.name,
                             self._fc1, self._fc2, self._bc,
                             resolve_menu_hover(getattr(self._main, 'theme', None)))
        if not ok or not name.strip():
            return
        t.name = name.strip()
        themes_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'themes')
        path = os.path.join(themes_dir, f'{name.strip().lower().replace(" ", "_")}.json')
        d = dataclasses.asdict(t)
        for k in Theme._NO_PERSIST:
            d.pop(k, None)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=2)

    def _reset(self):
        from player.theme import load_presets
        presets = load_presets()
        t = self._main.theme
        preset = presets.get(t.name)
        if preset:
            for f in dataclasses.fields(preset):
                if f.name not in Theme._NO_PERSIST:
                    setattr(t, f.name, getattr(preset, f.name))
            self._main._last_theme_key = None
            if t.auto_bg_from_accent:
                self._main._auto_tint_bg_colors()
            self._main.refresh_ui_styles()


def _ask_name(parent, title, prompt, default, fc1, fc2, bc, hover):
    dlg = QDialog(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(0, 0, 0, 0)
    bg = QFrame()
    bg.setStyleSheet(f'QFrame {{ background: rgb(28,28,28); border: 1px solid {bc}; border-radius: 8px; }}')
    fl = QVBoxLayout(bg)
    fl.setContentsMargins(16, 14, 16, 14)
    fl.setSpacing(10)
    lbl = QLabel(prompt)
    lbl.setStyleSheet(f'color: {fc1}; font-size: 13px; background: transparent;')
    fl.addWidget(lbl)
    inp = QLineEdit(default)
    inp.setStyleSheet(f'QLineEdit {{ background: transparent; color: {fc1}; border: 1px solid {bc}; border-radius: 4px; padding: 6px 10px; font-size: 13px; }}')
    fl.addWidget(inp)
    btn_r = QHBoxLayout()
    btn_r.setSpacing(8)
    s = f'QPushButton {{ background: transparent; color: {fc2}; border: 1px solid {bc}; border-radius: 4px; padding: 6px 16px; font-size: 12px; font-weight: bold; }} QPushButton:hover {{ background: {hover}; color: {fc1}; }}'
    ok_b = QPushButton('Save'); ok_b.setCursor(Qt.CursorShape.PointingHandCursor); ok_b.setStyleSheet(s.replace(fc2, fc1, 1))
    ca_b = QPushButton('Cancel'); ca_b.setCursor(Qt.CursorShape.PointingHandCursor); ca_b.setStyleSheet(s)
    ok_b.clicked.connect(dlg.accept); ca_b.clicked.connect(dlg.reject)
    btn_r.addStretch(); btn_r.addWidget(ca_b); btn_r.addWidget(ok_b)
    fl.addLayout(btn_r)
    root.addWidget(bg)
    result = dlg.exec()
    return inp.text(), result == QDialog.DialogCode.Accepted
