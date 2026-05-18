"""
lyrics_panel.py — Synchronized and plain lyrics with hover toolbar, search, and offset.

Sources (enabled via QSettings key 'lyrics_sources'):
  - LRCLib    https://lrclib.net
  - NetEase   https://music.163.com
  - SimpMusic https://api-lyrics.simpmusic.org

LRC format: [mm:ss.xx] text  →  [(time_ms, text), ...]
"""
import re
import os
import sys
import urllib.request
import urllib.parse
import json

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QDialog, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QFrame, QAbstractItemView, QApplication, QGraphicsOpacityEffect,
)
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QSettings, QSize, QPropertyAnimation, QEasingCurve

from player.mixins.visuals import resolve_menu_hover, scrollbar_css

# ── constants ─────────────────────────────────────────────────────────────────

SOURCES = ['LRCLib', 'NetEase', 'SimpMusic']
SETTINGS_KEY = 'lyrics_sources'


# ── Local lyrics cache ────────────────────────────────────────────────────────

def _lyrics_dir() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, 'app_data', 'lyrics')
    os.makedirs(d, exist_ok=True)
    return d


def _lyrics_key(track: dict) -> str:
    tid = track.get('id', '')
    if tid:
        return tid
    artist = re.sub(r'[^\w\-]', '_', track.get('artist', 'unknown'))
    title  = re.sub(r'[^\w\-]', '_', track.get('title', track.get('name', 'unknown')))
    return f'{artist}__{title}'


def _load_local_lyrics(track: dict) -> str | None:
    path = os.path.join(_lyrics_dir(), _lyrics_key(track) + '.lrc')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _save_local_lyrics(track: dict, raw: str):
    path = os.path.join(_lyrics_dir(), _lyrics_key(track) + '.lrc')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(raw)


def _remove_local_lyrics(track: dict):
    path = os.path.join(_lyrics_dir(), _lyrics_key(track) + '.lrc')
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def enabled_sources() -> list[str]:
    s = QSettings('Icosahedron', 'Icosahedron')
    val = s.value(SETTINGS_KEY, SOURCES)
    return list(val) if val else SOURCES[:]


# ── LRC parser ────────────────────────────────────────────────────────────────

_LRC_RE = re.compile(r'\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\](.*)')


def parse_lrc(text: str) -> list[tuple[int, str]] | str:
    lines = []
    for raw in text.splitlines():
        m = _LRC_RE.match(raw.strip())
        if m:
            minutes, secs, ms_raw, lyric = m.groups()
            ms = int(ms_raw.ljust(3, '0')) if ms_raw else 0
            time_ms = int(minutes) * 60_000 + int(secs) * 1000 + ms
            if lyric.strip() or lines:
                lines.append((time_ms, lyric))
    if lines:
        return sorted(lines, key=lambda x: x[0])
    return text.strip()


# ── Source fetchers ───────────────────────────────────────────────────────────

_UA = 'Icosahedron/1.0'


def _get(url: str, params: dict = None, timeout: int = 10, headers: dict = None) -> bytes | None:
    full = url + ('?' + urllib.parse.urlencode(params) if params else '')
    try:
        h = {'User-Agent': _UA}
        if headers:
            h.update(headers)
        req = urllib.request.Request(full, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        print(f'[Lyrics] HTTP {e.code} {e.reason} → {full}')
        return None
    except urllib.error.URLError as e:
        print(f'[Lyrics] network error ({e.reason}) → {full}')
        return None
    except Exception as e:
        print(f'[Lyrics] unexpected error ({e}) → {full}')
        return None


# ─ LRCLib ─────────────────────────────────────────────────────────────────────

def lrclib_search(artist: str, title: str) -> list[dict]:
    data = _get('https://lrclib.net/api/search', {'q': f'{artist} {title}'})
    if not data:
        return []
    try:
        results = json.loads(data)
        out = []
        for r in results:
            out.append({
                'id':     str(r['id']),
                'title':  r.get('name', ''),
                'artist': r.get('artistName', ''),
                'source': 'LRCLib',
                'synced': bool(r.get('syncedLyrics')),
            })
        return out
    except Exception:
        return []


def lrclib_fetch(song_id: str) -> str | None:
    data = _get(f'https://lrclib.net/api/get/{song_id}')
    if not data:
        return None
    try:
        j = json.loads(data)
        return j.get('syncedLyrics') or j.get('plainLyrics')
    except Exception:
        return None


def lrclib_direct(artist: str, title: str, album: str = '', duration: float = 0) -> str | None:
    params = {'artist_name': artist, 'track_name': title}
    if album:
        params['album_name'] = album
    if duration:
        params['duration'] = int(duration)
    data = _get('https://lrclib.net/api/get', params)
    if not data:
        return None
    try:
        j = json.loads(data)
        return j.get('syncedLyrics') or j.get('plainLyrics')
    except Exception:
        return None


# ─ NetEase ────────────────────────────────────────────────────────────────────

def netease_search(artist: str, title: str) -> list[dict]:
    data = _get('https://music.163.com/api/search/get', {
        's': f'{artist} {title}', 'type': 1, 'limit': 10, 'offset': 0,
    })
    if not data:
        return []
    try:
        j = json.loads(data)
        songs = j.get('result', {}).get('songs', [])
        out = []
        for s in songs:
            artists = ', '.join(a['name'] for a in s.get('artists', []))
            out.append({
                'id':     str(s['id']),
                'title':  s.get('name', ''),
                'artist': artists,
                'source': 'NetEase',
                'synced': None,
            })
        return out
    except Exception:
        return []


def netease_fetch(song_id: str) -> str | None:
    data = _get('https://music.163.com/api/song/lyric', {
        'id': song_id, 'lv': 1, 'kv': 1, 'tv': -1,
    }, headers={'Referer': 'https://music.163.com/'})
    if not data:
        return None
    try:
        j = json.loads(data)
        return (j.get('lrc') or {}).get('lyric') or (j.get('klyric') or {}).get('lyric')
    except Exception:
        return None


# ─ SimpMusic ──────────────────────────────────────────────────────────────────

def simpmusic_search(artist: str, title: str) -> list[dict]:
    data = _get('https://api-lyrics.simpmusic.org/v1/search', {
        'q': title, 'artist': artist,
    })
    if not data:
        return []
    try:
        j = json.loads(data)
        items = j.get('data', [])
        out = []
        for r in items:
            out.append({
                'id':     r.get('id', ''),
                'title':  r.get('songTitle', ''),
                'artist': r.get('artistName', ''),
                'source': 'SimpMusic',
                'synced': bool(r.get('syncedLyrics')),
            })
        return out
    except Exception:
        return []


def simpmusic_fetch(song_id: str) -> str | None:
    data = _get(f'https://api-lyrics.simpmusic.org/v1/{song_id}')
    if not data:
        return None
    try:
        j = json.loads(data)
        items = j.get('data', [])
        if items:
            r = items[0]
            return r.get('syncedLyrics') or r.get('plainLyric')
    except Exception:
        return None


SEARCH_FNS = {'LRCLib': lrclib_search, 'NetEase': netease_search, 'SimpMusic': simpmusic_search}
FETCH_FNS  = {'LRCLib': lrclib_fetch,  'NetEase': netease_fetch,  'SimpMusic': simpmusic_fetch}


# ── Search worker ─────────────────────────────────────────────────────────────

class _SearchWorker(QThread):
    results_ready = pyqtSignal(list)

    def __init__(self, artist: str, title: str):
        super().__init__()
        self._artist = artist
        self._title  = title

    def run(self):
        sources = enabled_sources()
        all_results = []
        for src in sources:
            fn = SEARCH_FNS.get(src)
            if fn:
                try:
                    all_results.extend(fn(self._artist, self._title))
                except Exception:
                    pass
        self.results_ready.emit(all_results)


class _PreviewWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, source: str, song_id: str):
        super().__init__()
        self._source  = source
        self._song_id = song_id

    def run(self):
        fn = FETCH_FNS.get(self._source)
        raw = fn(self._song_id) if fn else None
        self.done.emit(raw or '')


# ── Search dialog ─────────────────────────────────────────────────────────────

class LyricsSearchDialog(QDialog):
    override_selected = pyqtSignal(dict)   # {source, id, raw_lyrics}

    def __init__(self, artist: str, title: str,
                 active_source: str = '', active_sid: str = '', parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(860, 560)
        self._active_source = active_source
        self._active_sid    = active_sid
        self._drag_pos      = None

        theme = getattr(parent, 'theme', None) if parent else None
        bg  = getattr(theme, 'main_panel_bg',       '24,24,24') if theme else '24,24,24'
        fc1 = getattr(theme, 'font_color_primary',  '#dddddd') if theme else '#dddddd'
        fc2 = getattr(theme, 'font_color_secondary','#888888') if theme else '#888888'
        bc  = getattr(theme, 'border_color',        '#333333') if theme else '#333333'
        acc = getattr(theme, 'accent',              '#ffffff') if theme else '#ffffff'

        # ── Exactly like SettingsWindow ───────────────────────────────────────
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self._bg_frame = QFrame()
        self._bg_frame.setObjectName('lyricSearchBg')
        self._bg_frame.setMouseTracking(True)
        self._bg_frame.setStyleSheet(f"""
            QFrame#lyricSearchBg {{
                background: rgb({bg});
                border: 1px solid {bc};
                border-radius: 10px;
            }}
            QLineEdit {{
                background: rgba(255,255,255,0.06); border: 1px solid {bc};
                border-radius: 6px; padding: 6px 10px; color: {fc1}; font-size: 13px;
            }}
            QListWidget {{
                background: transparent; border: 1px solid {bc};
                border-radius: 6px; outline: none; color: {fc1};
            }}
            QListWidget::item {{ padding: 8px 10px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: rgba(255,255,255,0.1); }}
            QPushButton {{
                background: rgba(255,255,255,0.07); border: 1px solid {bc};
                border-radius: 6px; padding: 6px 16px; color: {fc1}; font-size: 13px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.14); }}
            QPushButton#apply {{ background: {acc}; color: #111; border: none; font-weight: bold; }}
            QPushButton#apply:hover {{ background: {QColor(acc).lighter(115).name()}; }}
            QPushButton:disabled {{ color: {fc2}; }}
            QLabel {{ color: {fc1}; background: transparent; }}
        """ + scrollbar_css(acc))
        root_layout.addWidget(self._bg_frame)

        self._results: list[dict] = []
        self._preview_raw = ''
        self._search_worker: _SearchWorker | None = None
        self._preview_worker: _PreviewWorker | None = None

        root = QVBoxLayout(self._bg_frame)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

        # ── Title row ─────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 4)
        title_lbl = QLabel('Search Lyrics')
        title_lbl.setStyleSheet(f'font-size: 13px; font-weight: bold;')
        close_btn = QPushButton('✕')
        close_btn.setFixedSize(26, 26)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f'QPushButton {{ background: transparent; border: none; color: {fc2}; font-size: 13px; }}'
            f'QPushButton:hover {{ color: {fc1}; }}'
        )
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        # Search fields
        fields = QHBoxLayout()
        self._title_edit  = QLineEdit(title)
        self._title_edit.setPlaceholderText('Title')
        self._artist_edit = QLineEdit(artist)
        self._artist_edit.setPlaceholderText('Artist')
        search_btn = QPushButton('Search')
        search_btn.clicked.connect(self._do_search)
        fields.addWidget(self._title_edit, 2)
        fields.addWidget(self._artist_edit, 2)
        fields.addWidget(search_btn, 1)
        root.addLayout(fields)

        # Results + preview
        body = QHBoxLayout()
        body.setSpacing(10)

        self._result_list = QListWidget()
        self._result_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._result_list.currentRowChanged.connect(self._on_result_selected)
        body.addWidget(self._result_list, 1)

        self._preview_lbl = QLabel('Select a result to preview')
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._preview_lbl.setWordWrap(True)
        self._preview_lbl.setStyleSheet(f'color: {fc2}; font-size: 12px; background: transparent;')
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setStyleSheet(f'QScrollArea {{ border: 1px solid {bc}; border-radius: 6px; background: transparent; }}')
        preview_scroll.setWidget(self._preview_lbl)
        body.addWidget(preview_scroll, 1)

        root.addLayout(body, 1)

        # Status
        self._status_lbl = QLabel('')
        self._status_lbl.setStyleSheet(f'color: {fc2}; font-size: 12px; background: transparent;')
        root.addWidget(self._status_lbl)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()
        from player.mixins.visuals import resolve_menu_hover
        _hover = resolve_menu_hover(theme)
        _btn_base = f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {bc};
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton:hover {{
                background-color: {_hover};
            }}
        """
        cancel_btn = QPushButton('Cancel')
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(_btn_base + f"QPushButton {{ color: {fc2}; }}")
        cancel_btn.clicked.connect(self.reject)
        self._apply_btn = QPushButton('Apply')
        self._apply_btn.setObjectName('apply')
        self._apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_btn.setStyleSheet(_btn_base + f"QPushButton {{ color: {fc1}; }}")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._apply)
        btns.addWidget(cancel_btn)
        btns.addWidget(self._apply_btn)
        root.addLayout(btns)

        # Auto-search on open
        self._do_search()

    def _do_search(self):
        artist = self._artist_edit.text().strip()
        title  = self._title_edit.text().strip()
        if not artist and not title:
            return
        self._result_list.clear()
        self._results = []
        self._status_lbl.setText('Searching…')
        self._apply_btn.setEnabled(False)
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.results_ready.disconnect()
            self._search_worker.quit()
        self._search_worker = _SearchWorker(artist, title)
        self._search_worker.results_ready.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_done(self, results: list):
        self._results = results
        self._result_list.clear()
        if not results:
            self._status_lbl.setText('No results found')
            return
        self._status_lbl.setText(f'{len(results)} result(s)')
        theme  = getattr(self.window(), 'theme', None)
        accent = getattr(theme, 'accent', '#ffffff')
        active_row = -1
        for i, r in enumerate(results):
            sync_tag = '⏱ ' if r.get('synced') else '  '
            label = f"{sync_tag}{r['title']} — {r['artist']}  [{r['source']}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, r)
            is_active = (r['source'] == self._active_source and
                         r['id']     == self._active_sid)
            if is_active:
                item.setForeground(QColor(accent))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                active_row = i
            self._result_list.addItem(item)
        if active_row >= 0:
            self._result_list.setCurrentRow(active_row)

    def _on_result_selected(self, row: int):
        if row < 0 or row >= len(self._results):
            return
        r = self._results[row]
        self._apply_btn.setEnabled(False)
        self._preview_lbl.setText('Loading preview…')
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.done.disconnect()
            self._preview_worker.quit()
        self._preview_worker = _PreviewWorker(r['source'], r['id'])
        self._preview_worker.done.connect(lambda raw: self._on_preview_done(raw, r))
        self._preview_worker.start()

    def _on_preview_done(self, raw: str, result: dict):
        self._preview_raw = raw
        if raw:
            parsed = parse_lrc(raw)
            if isinstance(parsed, list):
                preview_text = '\n'.join(text for _, text in parsed[:30])
                if len(parsed) > 30:
                    preview_text += f'\n… ({len(parsed)} lines total)'
            else:
                preview_text = parsed[:800]
            self._preview_lbl.setText(preview_text)
            self._apply_btn.setEnabled(True)
        else:
            self._preview_lbl.setText('No lyrics found for this result')
            self._apply_btn.setEnabled(False)

    def _apply(self):
        if self._preview_raw:
            row = self._result_list.currentRow()
            r = self._results[row] if 0 <= row < len(self._results) else {}
            self.override_selected.emit({**r, 'raw': self._preview_raw})
            self.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.done.disconnect()
            self._preview_worker.quit()
        super().closeEvent(event)


# ── Hover toolbar ─────────────────────────────────────────────────────────────

class _LyricsToolbar(QWidget):
    search_clicked  = pyqtSignal()
    save_clicked    = pyqtSignal()
    refresh_clicked = pyqtSignal()
    offset_changed  = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset_ms = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)

        lo = QHBoxLayout(self)
        lo.setContentsMargins(8, 4, 8, 4)
        lo.setSpacing(4)

        def _btn(text, tip=''):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setFixedHeight(28)
            return b

        self._search_btn  = _btn('Search', 'Search lyrics')
        self._save_btn    = _btn('Save', 'Save lyrics locally (loads first next time)')
        self._minus_btn   = _btn('−50ms', 'Shift lyrics earlier')
        self._offset_lbl  = QLabel('0 ms')
        self._offset_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._offset_lbl.setFixedWidth(68)
        self._plus_btn    = _btn('+50ms', 'Shift lyrics later')
        self._refresh_btn = _btn('Refresh', 'Clear override and re-fetch')

        self._search_btn.clicked.connect(self.search_clicked)
        self._save_btn.clicked.connect(self.save_clicked)
        self._refresh_btn.clicked.connect(self.refresh_clicked)
        self._minus_btn.clicked.connect(lambda: self._change_offset(-50))
        self._plus_btn.clicked.connect(lambda:  self._change_offset(+50))

        lo.addWidget(self._search_btn)
        lo.addWidget(self._save_btn)
        lo.addStretch()
        lo.addWidget(self._minus_btn)
        lo.addWidget(self._offset_lbl)
        lo.addWidget(self._plus_btn)
        lo.addStretch()
        lo.addWidget(self._refresh_btn)

        self.apply_theme()

    def apply_theme(self, theme=None):
        if theme is None:
            w = self.parent()
            theme = getattr(w.window() if w else None, 'theme', None) if w else None
        from player.mixins.visuals import resolve_menu_hover
        fc1 = getattr(theme, 'font_color_primary', '#cccccc') if theme else '#cccccc'
        hov = resolve_menu_hover(theme)
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; border: none; }}
            QPushButton {{
                background: transparent; border: none;
                border-radius: 5px; color: {fc1}; font-size: 11px; padding: 2px 8px;
            }}
            QPushButton:hover {{ background: {hov}; color: {fc1}; border: none; }}
            QLabel {{ color: {fc1}; font-size: 11px; border: none; background: transparent; }}
        """)

    def set_save_mode(self, is_local: bool):
        if is_local:
            self._save_btn.setText('Remove Local')
            self._save_btn.setToolTip('Delete locally saved lyrics and re-fetch')
        else:
            self._save_btn.setText('Save')
            self._save_btn.setToolTip('Save lyrics locally (loads first next time)')

    def set_offset(self, ms: int):
        self._offset_ms = ms
        self._offset_lbl.setText(f'{ms:+d} ms' if ms else '0 ms')

    def _change_offset(self, delta: int):
        self._offset_ms += delta
        self.set_offset(self._offset_ms)
        self.offset_changed.emit(self._offset_ms)


# ── Lyric line ────────────────────────────────────────────────────────────────

class _LyricLine(QLabel):
    clicked = pyqtSignal(int)

    def __init__(self, text: str, time_ms: int, panel: 'LyricsPanel'):
        super().__init__(text or '♪')
        self._time_ms = time_ms
        self._panel   = panel
        self._active  = False
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor if time_ms >= 0 else Qt.CursorShape.ArrowCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._refresh_style()

    def set_active(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self._refresh_style()

    def _refresh_style(self):
        theme   = getattr(self._panel.window(), 'theme', None)
        accent  = getattr(theme, 'accent',              '#ffffff')
        sec     = getattr(theme, 'font_color_secondary', '#555555')
        pri_px  = getattr(theme, 'font_size_primary',    14)
        if self._active:
            self.setStyleSheet(
                f'color: {accent}; font-size: {pri_px + 2}px; font-weight: bold;'
                ' background: transparent; padding: 2px 12px;'
            )
        else:
            self.setStyleSheet(
                f'color: {sec}; font-size: {pri_px}px;'
                ' background: transparent; padding: 2px 12px;'
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._time_ms >= 0:
            self.clicked.emit(self._time_ms)
        super().mousePressEvent(event)


# ── Fetch worker ──────────────────────────────────────────────────────────────

class _LyricsFetcher(QThread):
    done = pyqtSignal(str, str, str)   # raw, source_name, source_id

    def __init__(self, track: dict, client):
        super().__init__()
        self._track  = track
        self._client = client

    def run(self):
        track  = self._track
        artist = track.get('artist', '')
        title  = track.get('title', track.get('name', ''))
        album  = track.get('album', '')
        try:
            if track.get('duration_ms'):
                duration = float(track['duration_ms']) / 1000
            else:
                raw = track.get('duration') or 0
                if isinstance(raw, str) and ':' in raw:
                    m, s = raw.split(':')
                    duration = int(m) * 60 + int(s)
                else:
                    duration = float(raw or 0)
        except (ValueError, TypeError):
            duration = 0.0

        raw    = None
        source = ''
        sid    = ''

        # 0. Local saved lyrics
        local = _load_local_lyrics(track)
        if local:
            print(f'[Lyrics] local cache hit for "{title}"')
            self.done.emit(local, 'Local', '')
            return

        print(f'[Lyrics] fetching: artist="{artist}" title="{title}" album="{album}" duration={duration:.1f}s')

        # 1. Server (Subsonic getLyrics)
        if self._client:
            try:
                result = self._client.get_lyrics(artist, title)
                if result and result.get('value'):
                    raw    = result['value']
                    source = 'Server'
                    sid    = ''
                    print(f'[Lyrics] ✓ Server hit')
                else:
                    print(f'[Lyrics] Server: no lyrics returned')
            except Exception as e:
                print(f'[Lyrics] Server error: {e}')

        # 2. Enabled remote sources
        if not raw:
            sources = enabled_sources()
            print(f'[Lyrics] trying remote sources: {sources}')

            if 'LRCLib' in sources:
                raw = lrclib_direct(artist, title, album, duration)
                if raw:
                    source = 'LRCLib'
                    print('[Lyrics] ✓ LRCLib direct hit')
                else:
                    print('[Lyrics] LRCLib direct: miss — trying LRCLib search fallback')
                    results = lrclib_search(artist, title)
                    print(f'[Lyrics] LRCLib search: {len(results)} result(s)')
                    if results:
                        sid = results[0]['id']
                        raw = lrclib_fetch(sid)
                        if raw:
                            source = 'LRCLib'
                            print(f'[Lyrics] ✓ LRCLib search hit (id={sid})')
                        else:
                            print(f'[Lyrics] LRCLib fetch failed for id={sid}')

            if 'NetEase' in sources and not raw:
                results = netease_search(artist, title)
                print(f'[Lyrics] NetEase search: {len(results)} result(s)')
                if results:
                    sid = results[0]['id']
                    raw = netease_fetch(sid)
                    if raw:
                        source = 'NetEase'
                        print(f'[Lyrics] ✓ NetEase hit (id={sid})')
                    else:
                        print(f'[Lyrics] NetEase fetch failed for id={sid}')

            if 'SimpMusic' in sources and not raw:
                results = simpmusic_search(artist, title)
                print(f'[Lyrics] SimpMusic search: {len(results)} result(s)')
                if results:
                    sid = results[0]['id']
                    raw = simpmusic_fetch(sid)
                    if raw:
                        source = 'SimpMusic'
                        print(f'[Lyrics] ✓ SimpMusic hit (id={sid})')
                    else:
                        print(f'[Lyrics] SimpMusic fetch failed for id={sid}')

        if not raw:
            print(f'[Lyrics] ✗ not found for "{artist} - {title}"')

        self.done.emit(raw or '', source, sid)


# ── Bottom fade bar ───────────────────────────────────────────────────────────

class _FadeBar(QWidget):
    _FADE_H = 64

    def __init__(self, scroll_area: QScrollArea):
        super().__init__(scroll_area)   # child of the scroll area frame — never scrolls
        self._bg = QColor(20, 20, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.raise_()

    def set_bg(self, color: QColor):
        self._bg = color
        self.update()

    def reposition(self, left_inset: int = 1):
        p = self.parent()
        if p:
            self.setGeometry(left_inset, p.height() - self._FADE_H,
                             p.width() - left_inset, self._FADE_H)

    def paintEvent(self, _):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        c0 = QColor(self._bg); c0.setAlpha(0)
        c1 = QColor(self._bg); c1.setAlpha(255)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        p.fillRect(self.rect(), grad)
        p.end()


# ── Main panel ────────────────────────────────────────────────────────────────

class LyricsPanel(QWidget):
    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background: transparent;')

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scroll area (lyrics content) ──────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        self._scroll.verticalScrollBar().setStyleSheet(
            'QScrollBar:vertical { border: none; background: transparent; width: 4px; margin: 0; }'
            'QScrollBar::handle:vertical { background: rgba(255,255,255,0.15); border-radius: 2px; min-height: 20px; }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }'
        )

        self._container = QWidget()
        self._container.setStyleSheet('background: transparent;')
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 24, 0, 16)
        self._layout.setSpacing(16)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        self._fade_bar = _FadeBar(self._scroll)

        # ── Toolbar bar (always visible at bottom) ────────────────────────────
        self._toolbar = _LyricsToolbar(self)
        self._toolbar.search_clicked.connect(self._open_search)
        self._toolbar.save_clicked.connect(self._save_lyrics)
        self._toolbar.refresh_clicked.connect(self._clear_override_and_reload)
        self._toolbar.offset_changed.connect(self._on_offset_changed)

        self._toolbar_opacity = QGraphicsOpacityEffect(self._toolbar)
        self._toolbar_opacity.setOpacity(0.0)
        self._toolbar.setGraphicsEffect(self._toolbar_opacity)

        self._toolbar_anim = QPropertyAnimation(self._toolbar_opacity, b'opacity', self)
        self._toolbar_anim.setDuration(200)
        self._toolbar_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        root.addWidget(self._toolbar)

        # ── State ─────────────────────────────────────────────────────────────
        self._lines:  list[_LyricLine]      = []
        self._synced: list[tuple[int, str]] = []
        self._active_idx      = -1
        self._offset_ms       = 0
        self._current_track: dict = {}
        self._client          = None
        self._fetcher: _LyricsFetcher | None = None
        self._current_raw:    str = ''
        self._override_raw:   str = ''
        self._active_source:  str = ''
        self._active_sid:     str = ''

        self._advance_timer = QTimer(self)
        self._advance_timer.setSingleShot(True)
        self._advance_timer.timeout.connect(self._on_timer_tick)

        self._scroll_anim = QPropertyAnimation(self._scroll.verticalScrollBar(), b'value', self)
        self._scroll_anim.setDuration(300)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.setMouseTracking(True)
        self._set_status('No track playing')

    # ── hover fade toolbar ────────────────────────────────────────────────────

    def enterEvent(self, event):
        self._toolbar_anim.stop()
        self._toolbar_anim.setEndValue(1.0)
        self._toolbar_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._toolbar_anim.stop()
        self._toolbar_anim.setEndValue(0.0)
        self._toolbar_anim.start()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fade_bar.reposition()

    def apply_theme(self, theme=None):
        for line in self._lines:
            line._refresh_style()
        self._toolbar.apply_theme(theme)
        raw = getattr(theme, 'queue_panel_bg', '20,20,20') if theme else '20,20,20'
        try:
            r, g, b = (int(x) for x in str(raw).split(','))
        except Exception:
            r, g, b = 20, 20, 20
        self._fade_bar.set_bg(QColor(r, g, b))

    # ── public API ────────────────────────────────────────────────────────────

    def set_client(self, client):
        self._client = client

    def load_track(self, track: dict):
        tid = track.get('id')
        if tid and tid == self._current_track.get('id'):
            return
        self._current_track = track
        self._current_raw   = ''
        self._override_raw  = ''
        self._active_source = ''
        self._active_sid    = ''
        self._offset_ms     = 0
        self._toolbar.set_offset(0)
        self._toolbar.set_save_mode(False)
        self._start_fetch(track)

    def update_position(self, pos_ms: int):
        if not self._synced:
            return
        self._advance_to(pos_ms + self._offset_ms)

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _start_fetch(self, track: dict):
        self._cancel_fetch()
        self._clear()
        self._set_status('Loading lyrics…')
        self._active_idx = -1
        self._fetcher = _LyricsFetcher(track, self._client)
        self._fetcher.done.connect(self._on_lyrics_fetched)
        self._fetcher.start()

    def _cancel_fetch(self):
        if self._fetcher and self._fetcher.isRunning():
            self._fetcher.done.disconnect()
            self._fetcher.quit()
        self._fetcher = None
        self._advance_timer.stop()

    def _clear_override_and_reload(self):
        self._override_raw = ''
        self._current_raw  = ''
        self._start_fetch(self._current_track)

    def _save_lyrics(self):
        if not self._current_track:
            return
        if self._active_source == 'Local':
            _remove_local_lyrics(self._current_track)
            self._toolbar.set_save_mode(False)
            print(f'[Lyrics] removed local lyrics for "{self._current_track.get("title", "")}"')
        elif self._current_raw:
            _save_local_lyrics(self._current_track, self._current_raw)
            self._toolbar.set_save_mode(True)
            self._active_source = 'Local'
            print(f'[Lyrics] saved locally for "{self._current_track.get("title", "")}"')

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_lyrics_fetched(self, raw: str, source: str, sid: str):
        if not raw:
            self._set_status('No lyrics found')
            return
        self._active_source = source
        self._active_sid    = sid
        self._current_raw   = raw
        self._toolbar.set_save_mode(source == 'Local')
        self._render(raw, source)

    def _render(self, raw: str, source: str = ''):
        parsed = parse_lrc(raw)
        if isinstance(parsed, list):
            self._show_synced(parsed, source)
        else:
            self._show_plain(parsed, source)

    def _on_offset_changed(self, ms: int):
        self._offset_ms = ms

    def _open_search(self):
        track  = self._current_track
        artist = track.get('artist', '')
        title  = track.get('title', track.get('name', ''))
        dlg = LyricsSearchDialog(
            artist, title,
            active_source=self._active_source,
            active_sid=self._active_sid,
            parent=self.window(),
        )
        mw = self.window()
        dlg.override_selected.connect(self._apply_override)
        if hasattr(mw, 'show_dim'):
            mw.show_dim()
        dlg.exec()
        if hasattr(mw, 'hide_dim'):
            mw.hide_dim()

    def _apply_override(self, data: dict):
        raw = data.get('raw', '')
        if raw:
            src = data.get('source', '')
            self._active_source = src
            self._active_sid    = data.get('id', '')
            self._override_raw  = raw
            self._current_raw   = raw
            self._toolbar.set_save_mode(False)
            self._clear()
            self._render(raw, src)

    # ── display ───────────────────────────────────────────────────────────────

    def _clear(self):
        self._advance_timer.stop()
        self._lines.clear()
        self._synced.clear()
        self._active_idx = -1
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_status(self, msg: str):
        self._clear()
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet('color: #555; font-size: 13px; background: transparent; padding: 32px;')
        self._layout.addWidget(lbl)

    def _provider_label(self, source: str) -> QLabel:
        theme  = getattr(self.window(), 'theme', None)
        accent = getattr(theme, 'accent', '#ffffff')
        lbl = QLabel(source)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f'color: {accent}; font-size: 10px; font-weight: bold; letter-spacing: 1px;'
            ' background: transparent; padding: 0 12px 8px 12px;'
        )
        return lbl

    def _show_plain(self, text: str, source: str = ''):
        self._clear()
        if source:
            self._layout.addWidget(self._provider_label(source))
        for para in text.split('\n'):
            line = _LyricLine(para, -1, self)
            self._layout.addWidget(line)

    def _show_synced(self, lines: list[tuple[int, str]], source: str = ''):
        self._clear()
        if source:
            self._layout.addWidget(self._provider_label(source))
        self._synced = lines
        for ms, text in lines:
            w = _LyricLine(text, ms, self)
            w.clicked.connect(lambda t: self.seek_requested.emit(t / 1000.0))
            self._lines.append(w)
            self._layout.addWidget(w)

    # ── synced highlight ──────────────────────────────────────────────────────

    def _find_active(self, pos_ms: int) -> int:
        idx = -1
        for i, (ms, _) in enumerate(self._synced):
            if pos_ms >= ms:
                idx = i
            else:
                break
        return idx

    def _advance_to(self, pos_ms: int):
        idx = self._find_active(pos_ms)
        self._set_active(idx)
        self._schedule_next(pos_ms)

    def _set_active(self, idx: int):
        if idx == self._active_idx:
            return
        if 0 <= self._active_idx < len(self._lines):
            self._lines[self._active_idx].set_active(False)
        self._active_idx = idx
        if 0 <= idx < len(self._lines):
            self._lines[idx].set_active(True)
            self._scroll_to_active(idx)

    def _scroll_to_active(self, idx: int):
        line   = self._lines[idx]
        target = line.y() - int(self._scroll.viewport().height() * 0.50)
        target = max(0, min(target, self._scroll.verticalScrollBar().maximum()))
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(self._scroll.verticalScrollBar().value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def _schedule_next(self, pos_ms: int):
        self._advance_timer.stop()
        nxt = self._find_active(pos_ms) + 1
        if nxt < len(self._synced):
            delay = max(30, self._synced[nxt][0] - pos_ms)
            self._advance_timer.start(delay)

    def _on_timer_tick(self):
        pass   # update_position drives everything; timer just wakes the loop
