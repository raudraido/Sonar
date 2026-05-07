"""
artist_info_panel.py — Artist bio + Bandsintown tour-dates panel shown in the
queue sidebar's "Info" tab.
"""
import re
import json
import urllib.request
import urllib.parse

from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QFrame, QScrollBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QSize
from player.mixins.visuals import scrollbar_css, install_scroll_reveal
from PyQt6.QtGui import QPixmap, QColor, QPainter, QPainterPath

# ── in-process caches (cleared on restart) ───────────────────────────────────
_artist_info_cache: dict = {}   # artist_id  → dict from get_artist_info2
_bit_cache:         dict = {}   # artist_name → list[dict]
_image_cache:       dict = {}   # url         → QPixmap

TOUR_LIMIT  = 5
BIT_APP_ID  = "js_app_id"


# ── worker threads ────────────────────────────────────────────────────────────

class _ArtistInfoWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, client, artist_id):
        super().__init__()
        self._client    = client
        self._artist_id = artist_id

    def run(self):
        if self._artist_id in _artist_info_cache:
            self.done.emit(_artist_info_cache[self._artist_id])
            return
        try:
            info = self._client.get_artist_info2(self._artist_id) or {}
        except Exception:
            info = {}
        _artist_info_cache[self._artist_id] = info
        self.done.emit(info)


class _BandsintownWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, artist_name):
        super().__init__()
        self._name = artist_name

    def run(self):
        key = self._name.strip().lower()
        if key in _bit_cache:
            self.done.emit(_bit_cache[key])
            return
        try:
            encoded = urllib.parse.quote(self._name, safe='')
            url = (f"https://rest.bandsintown.com/artists/{encoded}/events"
                   f"?app_id={BIT_APP_ID}")
            req = urllib.request.Request(url, headers={"User-Agent": "Sonar/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read().decode()
            print(f"[BIT] {self._name} → {raw[:200]}")
            events = json.loads(raw)
            if not isinstance(events, list):
                print(f"[BIT] unexpected response type: {type(events)}")
                events = []
        except Exception as e:
            print(f"[BIT] error for {self._name!r}: {e}")
            events = []
        _bit_cache[key] = events
        self.done.emit(events)


class _ImageWorker(QThread):
    done = pyqtSignal(QPixmap)

    def __init__(self, url):
        super().__init__()
        self._url = url

    def run(self):
        if self._url in _image_cache:
            self.done.emit(_image_cache[self._url])
            return
        try:
            req = urllib.request.Request(self._url, headers={"User-Agent": "Sonar/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            pix = QPixmap()
            pix.loadFromData(data)
        except Exception:
            pix = QPixmap()
        if not pix.isNull():
            _image_cache[self._url] = pix
        self.done.emit(pix)


# ── helper widgets ────────────────────────────────────────────────────────────

def _sep():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.08);")
    return line


def _section_title(text, color="#888"):
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color: {color}; font-size: 10px; font-weight: bold; "
        "letter-spacing: 1px; background: transparent;"
    )
    return lbl


def _round_pixmap(pix: QPixmap, radius: int = 10) -> QPixmap:
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pix.width(), pix.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


# ── main panel ────────────────────────────────────────────────────────────────

class ArtistInfoPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings     = QSettings()
        self._accent       = "#ffffff"
        self._current_id   = None
        self._current_name = None
        self._show_all_tours = False
        self._all_events     = []
        self._bio_expanded   = False
        self._bio_full       = ""

        self._raw_pix      = None
        self._info_worker  = None
        self._bit_worker   = None
        self._img_worker   = None

        self.setWidgetResizable(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        # Overlay scrollbar — floats over the content, takes no layout space
        self._overlay_sb = QScrollBar(Qt.Orientation.Vertical, self)
        self._overlay_sb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._apply_scrollbar_style()
        self._overlay_sb.valueChanged.connect(self.verticalScrollBar().setValue)
        self.verticalScrollBar().valueChanged.connect(self._overlay_sb.setValue)
        self.verticalScrollBar().rangeChanged.connect(self._sync_overlay_sb)
        self._scroll_reveal = install_scroll_reveal(self.viewport(), self._overlay_sb)

        self._root = QWidget()
        self._root.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._root)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(0)
        self._layout.addStretch()
        self.setWidget(self._root)

        self._build_empty("Play something to see artist info")

    # ── public API ────────────────────────────────────────────────────────────

    def _apply_scrollbar_style(self):
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        if hasattr(self, '_overlay_sb'):
            self._overlay_sb.setStyleSheet(
                f"QScrollBar:vertical {{ border: none; background: transparent; width: 6px; margin: 0; }}"
                f"QScrollBar::handle:vertical {{ background: transparent; border-radius: 3px; min-height: 30px; }}"
                f"QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {self._accent}; }}"
                f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
                f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
            )
        if hasattr(self, '_scroll_reveal'):
            self._scroll_reveal.color = self._accent

    def set_accent_color(self, color: str):
        self._accent = color
        self._apply_scrollbar_style()

    def load_track(self, client, artist_id: str, artist_name: str):
        if artist_id == self._current_id and artist_name == self._current_name:
            return
        self._current_id   = artist_id
        self._current_name = artist_name
        self._show_all_tours = False
        self._bio_expanded   = False
        self._all_events     = []
        self._clear()
        self._build_loading()

        if client and artist_id:
            self._info_worker = _ArtistInfoWorker(client, artist_id)
            self._info_worker.done.connect(self._on_artist_info)
            self._info_worker.start()
        else:
            self._on_artist_info({})

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_artist_info(self, info: dict):
        self._artist_info = info
        image_url = (info.get("largeImageUrl") or
                     info.get("mediumImageUrl") or
                     info.get("smallImageUrl") or "")
        self._bio_full = re.sub(r"<a [^>]*>.*?</a>\.?", "", info.get("biography") or "", flags=re.S).strip()

        self._clear()
        self._build_header(image_url)

        bit_enabled = bool(int(self._settings.value("bandsintown_enabled", 0) or 0))
        if bit_enabled and self._current_name:
            self._bit_worker = _BandsintownWorker(self._current_name)
            self._bit_worker.done.connect(self._on_tour_events)
            self._bit_worker.start()
        else:
            self._build_tour_optin()

    def _on_tour_events(self, events: list):
        print(f"[BIT] received {len(events)} events")
        self._all_events = events
        self._rebuild_tour_section()

    def _on_image_loaded(self, pix: QPixmap):
        if not pix.isNull():
            self._raw_pix = pix
            self._apply_image()

    def _apply_image(self):
        if not self._raw_pix or not self._img_lbl:
            return
        w = self.viewport().width() - 16
        if w <= 0:
            return
        scaled = self._raw_pix.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        rounded = _round_pixmap(scaled, radius=10)
        self._img_lbl.setPixmap(rounded)
        self._img_lbl.setFixedHeight(rounded.height())

    def _sync_overlay_sb(self, min_val, max_val):
        self._overlay_sb.setRange(min_val, max_val)
        self._overlay_sb.setPageStep(self.verticalScrollBar().pageStep())
        self._overlay_sb.setSingleStep(self.verticalScrollBar().singleStep())
        self._overlay_sb.setVisible(max_val > min_val)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        sb_w = 6
        self._overlay_sb.setGeometry(self.width() - sb_w, 0, sb_w, self.height())
        self._overlay_sb.raise_()
        self._apply_image()

    # ── builders ──────────────────────────────────────────────────────────────

    def _clear(self):
        # Invalidate image label before deleting widgets so in-flight workers don't crash
        self._img_lbl = None
        self._raw_pix = None
        self._tour_widget = None
        if self._img_worker and self._img_worker.isRunning():
            try: self._img_worker.done.disconnect()
            except: pass
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build_empty(self, msg):
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #444; font-size: 12px; background: transparent;")
        self._layout.addStretch()
        self._layout.addWidget(lbl)
        self._layout.addStretch()

    def _build_loading(self):
        lbl = QLabel("Loading…")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #444; font-size: 12px; background: transparent;")
        self._layout.addStretch()
        self._layout.addWidget(lbl)
        self._layout.addStretch()

    def _build_header(self, image_url):
        # ── artist image ──────────────────────────────────────────────────────
        if image_url:
            self._img_lbl = QLabel()
            self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._img_lbl.setStyleSheet("background: transparent;")
            self._img_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(self._img_lbl)
            self._layout.addSpacing(12)

            self._img_worker = _ImageWorker(image_url)
            self._img_worker.done.connect(self._on_image_loaded)
            self._img_worker.start()

        # ── section label ─────────────────────────────────────────────────────
        self._layout.addWidget(_section_title("Artist"))
        self._layout.addSpacing(4)

        # ── artist name ───────────────────────────────────────────────────────
        name_lbl = QLabel(self._current_name or "Unknown Artist")
        name_lbl.setStyleSheet(
            "color: #eee; font-size: 18px; font-weight: bold; background: transparent;"
        )
        name_lbl.setWordWrap(True)
        self._layout.addWidget(name_lbl)
        self._layout.addSpacing(8)

        # ── bio ───────────────────────────────────────────────────────────────
        if self._bio_full:
            self._bio_lbl = QLabel()
            self._bio_lbl.setWordWrap(True)
            self._bio_lbl.setStyleSheet("color: #888; font-size: 11px; background: transparent; line-height: 1.5;")
            self._bio_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._bio_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._layout.addWidget(self._bio_lbl)
            self._update_bio_text()

            self._bio_toggle = QPushButton()
            self._bio_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self._bio_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._bio_toggle.setStyleSheet(
                f"QPushButton {{ color: {self._accent}; font-size: 11px; background: transparent; border: none; padding: 0; }}"
                f"QPushButton:hover {{ color: white; }}"
            )
            self._bio_toggle.clicked.connect(self._toggle_bio)
            self._layout.addWidget(self._bio_toggle)
            self._update_bio_toggle_text()
            self._layout.addSpacing(4)

        self._layout.addSpacing(12)
        self._layout.addWidget(_sep())
        self._layout.addSpacing(12)

        # placeholder for tour section (added by _rebuild_tour_section)
        self._tour_widget = None
        self._tour_placeholder_idx = self._layout.count()
        self._layout.addStretch()

    def _update_bio_text(self):
        if not self._bio_full:
            return
        if self._bio_expanded:
            self._bio_lbl.setText(self._bio_full)
        else:
            # approx 4 lines at ~60 chars each
            short = self._bio_full[:240]
            if len(self._bio_full) > 240:
                short = short.rsplit(" ", 1)[0] + "…"
            self._bio_lbl.setText(short)

    def _update_bio_toggle_text(self):
        if not hasattr(self, "_bio_toggle"):
            return
        self._bio_toggle.setText("Show less" if self._bio_expanded else "Read more")

    def _toggle_bio(self):
        self._bio_expanded = not self._bio_expanded
        self._update_bio_text()
        self._update_bio_toggle_text()

    def _build_tour_optin(self):
        """Show opt-in prompt for Bandsintown."""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        vbox.addWidget(_section_title("On Tour"))
        vbox.addSpacing(6)

        desc = QLabel("See upcoming tour dates?")
        desc.setStyleSheet("color: #666; font-size: 11px; background: transparent;")
        desc.setWordWrap(True)
        vbox.addWidget(desc)

        sub = QLabel("Optional. Loads concerts via Bandsintown.")
        sub.setStyleSheet("color: #444; font-size: 10px; background: transparent;")
        sub.setWordWrap(True)
        vbox.addWidget(sub)
        vbox.addSpacing(4)

        btn = QPushButton("Enable")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(30)
        btn.setStyleSheet(
            f"QPushButton {{ background: {self._accent}; color: #111; font-weight: bold; "
            f"font-size: 11px; border-radius: 4px; border: none; }}"
            f"QPushButton:hover {{ background: white; }}"
        )
        btn.clicked.connect(self._enable_bandsintown)
        vbox.addWidget(btn)

        self._insert_tour_widget(w)

    def _enable_bandsintown(self):
        self._settings.setValue("bandsintown_enabled", 1)
        if self._current_name:
            self._bit_worker = _BandsintownWorker(self._current_name)
            self._bit_worker.done.connect(self._on_tour_events)
            self._bit_worker.start()
        self._rebuild_tour_section()

    def _rebuild_tour_section(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        vbox.addWidget(_section_title("On Tour"))
        vbox.addSpacing(8)

        bit_enabled = bool(int(self._settings.value("bandsintown_enabled", 0) or 0))

        if not bit_enabled:
            self._build_tour_optin()
            return

        events = self._all_events
        visible = events if self._show_all_tours else events[:TOUR_LIMIT]

        if not events:
            empty = QLabel("No upcoming shows")
            empty.setStyleSheet("color: #444; font-size: 11px; background: transparent;")
            vbox.addWidget(empty)
        else:
            for ev in visible:
                vbox.addWidget(self._build_event_row(ev))
                vbox.addSpacing(2)

            if len(events) > TOUR_LIMIT:
                hidden = len(events) - TOUR_LIMIT
                more_btn = QPushButton(
                    "Show less" if self._show_all_tours
                    else f"Show {hidden} more"
                )
                more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                more_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                more_btn.setStyleSheet(
                    f"QPushButton {{ color: {self._accent}; font-size: 11px; "
                    "background: transparent; border: none; padding-top: 4px; padding-bottom: 4px; }}"
                    "QPushButton:hover { color: white; }"
                )
                more_btn.clicked.connect(self._toggle_show_all)
                vbox.addWidget(more_btn)

        vbox.addSpacing(6)
        credit = QLabel("Tour data via Bandsintown")
        credit.setStyleSheet("color: #333; font-size: 10px; background: transparent;")
        vbox.addWidget(credit)

        self._insert_tour_widget(w)

    def _insert_tour_widget(self, w):
        if self._tour_widget is not None:
            self._layout.removeWidget(self._tour_widget)
            self._tour_widget.deleteLater()
        # Remove trailing stretch
        last = self._layout.count() - 1
        item = self._layout.itemAt(last)
        if item and item.spacerItem():
            self._layout.takeAt(last)
        self._tour_widget = w
        self._layout.addWidget(w)
        self._layout.addStretch()

    def _build_event_row(self, ev: dict) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(
            "QWidget { background: rgba(255,255,255,0.04); border-radius: 4px; }"
            "QWidget:hover { background: rgba(255,255,255,0.08); }"
        )
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(8, 6, 8, 6)
        hbox.setSpacing(10)

        # Date block
        dt = ev.get("datetime", "")
        try:
            from datetime import datetime
            d = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            month = d.strftime("%b").upper()
            day   = str(d.day)
        except Exception:
            month, day = "", ""

        date_w = QWidget()
        date_w.setFixedWidth(32)
        date_w.setStyleSheet("background: transparent;")
        dv = QVBoxLayout(date_w)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(0)
        dv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        month_lbl = QLabel(month)
        month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        month_lbl.setStyleSheet(f"color: {self._accent}; font-size: 9px; font-weight: bold; background: transparent;")
        day_lbl = QLabel(day)
        day_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        day_lbl.setStyleSheet("color: #ddd; font-size: 15px; font-weight: bold; background: transparent;")
        dv.addWidget(month_lbl)
        dv.addWidget(day_lbl)
        hbox.addWidget(date_w)

        # Venue + location
        meta_w = QWidget()
        meta_w.setStyleSheet("background: transparent;")
        mv = QVBoxLayout(meta_w)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(1)

        venue = ev.get("venue", {})
        venue_name = venue.get("name", "") if isinstance(venue, dict) else ""
        city    = venue.get("city", "")    if isinstance(venue, dict) else ""
        region  = venue.get("region", "")  if isinstance(venue, dict) else ""
        country = venue.get("country", "") if isinstance(venue, dict) else ""
        place   = ", ".join(p for p in [city, region, country] if p)

        v_lbl = QLabel(venue_name or place)
        v_lbl.setStyleSheet("color: #ddd; font-size: 11px; font-weight: bold; background: transparent;")
        v_lbl.setWordWrap(False)
        mv.addWidget(v_lbl)

        if place and venue_name:
            p_lbl = QLabel(place)
            p_lbl.setStyleSheet("color: #666; font-size: 10px; background: transparent;")
            mv.addWidget(p_lbl)

        hbox.addWidget(meta_w, 1)

        url = ev.get("url", "")
        if url:
            import webbrowser
            def _open(e, u=url):
                webbrowser.open(u)
            row.mousePressEvent = _open

        return row

    def _toggle_show_all(self):
        self._show_all_tours = not self._show_all_tours
        self._rebuild_tour_section()
