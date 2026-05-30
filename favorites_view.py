"""favorites_view.py — Favorites tab: starred artists, albums and top artists."""
from collections import Counter

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QScrollArea,
                              QTreeWidget, QTreeWidgetItem, QHeaderView, QStyle)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QColor, QMovie, QPixmap, QPainter as _QPainter

from home import HomeAlbumRowWidget
from albums_browser import GridCoverWorker, _TrackListDelegate, _TrackHeader, resource_path
from now_playing_info import _Card
from player.mixins.visuals import scrollbar_css, install_scroll_reveal


class _SortableTrackHeader(_TrackHeader):
    """_TrackHeader extended with 3-state sort cycling on sortable columns."""

    sort_changed = pyqtSignal(int, str)   # col, 'asc' | 'desc' | ''

    # Columns that support sorting (indices into favorites' 7-col layout).
    # Genre (col 4) is excluded.
    SORT_COLS = {0, 1, 2, 3, 5, 6}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sort_col   = -1    # currently sorted column (-1 = none)
        self._sort_state = ''    # 'asc' | 'desc' | ''
        self._up_pix   = self._load_icon('img/filter_up.png')
        self._down_pix = self._load_icon('img/filter_down.png')

    @staticmethod
    def _load_icon(path):
        p = QPixmap(resource_path(path))
        if p.isNull():
            return QPixmap()
        return p.scaled(QSize(10, 10), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)

    def _tinted_pix(self, pix):
        if pix.isNull():
            return pix
        color = self._accent
        out = QPixmap(pix.size()); out.fill(Qt.GlobalColor.transparent)
        p = _QPainter(out)
        p.drawPixmap(0, 0, pix)
        p.setCompositionMode(_QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(out.rect(), color)
        p.end()
        return out

    def _in_resize_zone(self, x: int) -> bool:
        grip = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderGripMargin) + 2
        for i in range(self.count()):
            boundary = self.sectionViewportPosition(i) + self.sectionSize(i)
            if abs(x - boundary) <= grip:
                return True
        return False

    # ── Override click to cycle sort state ───────────────────────────────
    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and not self._in_resize_zone(event.pos().x())
                and not self._near_flex_boundary(event.pos().x())):
            col = self.logicalIndexAt(event.pos().x())
            if col in self.SORT_COLS:
                if self._sort_col == col:
                    # Cycle: desc → asc → none
                    self._sort_state = {'desc': 'asc', 'asc': '', '': 'desc'}[self._sort_state]
                else:
                    self._sort_col   = col
                    self._sort_state = 'desc'
                if not self._sort_state:
                    self._sort_col = -1
                self.viewport().update()
                self.sort_changed.emit(col, self._sort_state)
                event.accept()
                return
        super().mousePressEvent(event)

    # Centered columns in the 7-col favorites layout: #(0), DURATION(5), PLAYS(6)
    _CENTER_COLS = {0, 5, 6}

    _ICON_SZ = 14   # matches tracks browser FILTER_ICON_SIZE

    # ── Override paintSection: correct alignment + sort arrow ─────────────
    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        from PyQt6.QtGui import QFont, QPen, QPainter, QFontMetrics
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, Qt.GlobalColor.transparent)

        text   = self.model().headerData(logical_index, Qt.Orientation.Horizontal) or ''
        f      = QFont(); f.setPixelSize(self._secondary_px()); f.setBold(True)
        fm     = QFontMetrics(f)
        painter.setFont(f)
        painter.setPen(QColor(self._secondary_color()))

        has_sort = (logical_index == self._sort_col and bool(self._sort_state))
        sz       = self._ICON_SZ
        fy       = rect.bottom() - sz - 8   # same baseline as text

        centered = logical_index in self._CENTER_COLS
        if centered and has_sort:
            # Text + icon grouped and centred together
            text_w    = fm.horizontalAdvance(text)
            content_w = text_w + 4 + sz
            gx        = rect.left() + 4 + max(0, (rect.width() - 8 - content_w) // 2)
            from PyQt6.QtCore import QRect as _QR
            painter.drawText(_QR(gx, rect.top(), text_w, rect.height() - 8),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
            fx = gx + text_w + 4
        else:
            h_align = Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft
            painter.drawText(rect.adjusted(4, 0, -4, -8),
                             h_align | Qt.AlignmentFlag.AlignBottom, text)
            fx = rect.left() + 4 + fm.horizontalAdvance(text) + 4

        # Bottom border
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Column separator
        if logical_index > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border_qcolor(), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(rect.right(), rect.top() - 5, rect.right(), rect.bottom() - 8)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Sort arrow — same position/tint as tracks browser
        if has_sort:
            src = self._up_pix if self._sort_state == 'asc' else self._down_pix
            pix = self._tinted_pix(src.scaled(
                QSize(sz, sz), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            if not pix.isNull():
                painter.drawPixmap(fx, int(fy), pix)

        painter.restore()


class _StarredWorker(QThread):
    done = pyqtSignal(dict)   # {'songs': [...], 'albums': [...], 'artists': [...]}

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            data = self._client.get_starred_all()
        except Exception as e:
            print(f"[Favorites] fetch error: {e}")
            data = {'songs': [], 'albums': [], 'artists': []}
        self.done.emit(data)


class FavoritesView(QWidget):
    album_clicked  = pyqtSignal(dict)
    artist_clicked = pyqtSignal(str)
    play_album     = pyqtSignal(dict)
    play_track     = pyqtSignal(dict)

    def __init__(self, client=None, parent=None):
        super().__init__(parent)
        self._client       = client
        self._accent       = '#888888'
        self._worker       = None
        self._cover_worker = None

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName('FavoritesPanel')

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName('FavScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_reveal = None

        content = QWidget()
        content.setObjectName('FavContent')
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(5, 20, 4, 50)
        self._layout.setSpacing(10)
        self.scroll.setWidget(content)
        main.addWidget(self.scroll)

        # ── Artists row ───────────────────────────────────────────────────
        self._artists_row = HomeAlbumRowWidget('Artists')
        self._artists_row.album_clicked.connect(self._on_artist_card_clicked)
        self._artists_row.delegate.clickable_artist = False
        self._layout.addWidget(self._artists_row)

        # ── Albums row ────────────────────────────────────────────────────
        self._albums_row = HomeAlbumRowWidget('Albums')
        self._albums_row.album_clicked.connect(self.album_clicked)
        self._albums_row.play_album.connect(self.play_album)
        self._albums_row.artist_clicked.connect(self.artist_clicked)
        self._layout.addWidget(self._albums_row)

        # ── Top Artists by Favorites ──────────────────────────────────────
        self._top_row = HomeAlbumRowWidget('Top Artists by Favorites')
        self._top_row.album_clicked.connect(self._on_artist_card_clicked)
        self._top_row.delegate.clickable_artist = False
        self._layout.addWidget(self._top_row)

        # ── Favorite Songs track list ─────────────────────────────────────
        self._songs_lbl = QLabel('Favorite Songs')
        self._songs_lbl.setStyleSheet(
            'color: #eee; font-size: 15px; font-weight: bold;'
            ' background: transparent; padding: 8px 6px 4px 6px;'
        )
        self._layout.addWidget(self._songs_lbl)

        self._track_tree = QTreeWidget()
        self._track_tree.setColumnCount(7)
        self._track_tree.setHeaderLabels(['#', 'TITLE', 'ARTIST', 'ALBUM', 'GENRE', 'DURATION', 'PLAYS'])
        self._track_tree.setRootIsDecorated(False)
        self._track_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._track_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._track_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._track_tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._track_tree.setMouseTracking(True)
        self._track_tree.viewport().setMouseTracking(True)
        self._track_tree.setStyleSheet("""
            QTreeWidget { background: transparent; border: none; outline: none; }
            QTreeWidget::item { height: 38px; border: none; padding-left: 4px; }
            QTreeWidget::item:hover { background: transparent; }
            QTreeWidget::item:selected { background: transparent; }
            QHeaderView { background: transparent; border: none; }
        """)

        self._track_delegate = _TrackListDelegate(self._track_tree, heart_col=-1)
        self._playing_movie = QMovie(resource_path('img/playing.gif'))
        self._playing_movie.setScaledSize(QSize(30, 30))
        self._playing_movie.frameChanged.connect(
            lambda: self._track_tree.viewport().update())
        self._track_delegate.set_movie(self._playing_movie)

        def _heart_pix(path, color):
            from PyQt6.QtGui import QPixmap, QPainter as _P
            base = QPixmap(resource_path(path)).scaled(
                QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            out = QPixmap(base.size()); out.fill(Qt.GlobalColor.transparent)
            p = _P(out); p.drawPixmap(0, 0, base)
            p.setCompositionMode(_P.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(color)); p.end()
            return out
        self._track_delegate.set_heart_pixmaps(
            _heart_pix('img/heart_filled.png', '#E91E63'),
            _heart_pix('img/heart.png', '#555555'),
        )
        self._track_tree.setItemDelegate(self._track_delegate)

        self._track_header = _SortableTrackHeader(self._track_tree)
        self._track_header.sort_changed.connect(self._on_sort)
        self._track_tree.setHeader(self._track_header)
        hdr = self._track_header
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 7):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 50)
        hdr.resizeSection(2, 160); hdr.resizeSection(3, 160)
        hdr.resizeSection(4, 120); hdr.resizeSection(5, 76)
        hdr.resizeSection(6, 60)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(30)
        self._restore_col_widths()

        self._col_save_timer = QTimer(self)
        self._col_save_timer.setSingleShot(True)
        self._col_save_timer.setInterval(400)
        self._col_save_timer.timeout.connect(self._save_col_widths)
        hdr.sectionResized.connect(lambda *_: self._col_save_timer.start())

        self._track_tree.viewport().setAutoFillBackground(False)
        self._track_tree.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._track_tree.itemDoubleClicked.connect(self._on_track_double_clicked)

        self._track_card = _Card()
        _tcl = QVBoxLayout(self._track_card)
        _tcl.setContentsMargins(0, 16, 0, 0)
        _tcl.setSpacing(0)
        _tcl.addWidget(self._track_tree)
        _card_wrap = QWidget(); _card_wrap.setStyleSheet('background: transparent;')
        _cw_lo = QVBoxLayout(_card_wrap)
        _cw_lo.setContentsMargins(7, 0, 8, 0)
        _cw_lo.setSpacing(0)
        _cw_lo.addWidget(self._track_card)
        self._layout.addWidget(_card_wrap)

        self._layout.addStretch()

        self.set_accent_color('#888888')

        if self._client:
            QTimer.singleShot(0, self.refresh)

    # ── Public API ────────────────────────────────────────────────────────

    def set_client(self, client):
        self._client = client
        if client:
            self.refresh()

    def set_accent_color(self, color: str):
        self._accent = color
        theme = getattr(self.window(), 'theme', None)
        bg    = getattr(theme, 'main_panel_bg', '14,14,14') if theme else '14,14,14'
        fc1   = getattr(theme, 'font_color_primary',  '#dddddd') if theme else '#dddddd'
        fsize = getattr(theme, 'font_size_primary',   14)        if theme else 14
        self.scroll.setStyleSheet(
            f'QScrollArea {{ background: rgb({bg}); border: none; }}'
            + scrollbar_css(color)
        )
        self._songs_lbl.setStyleSheet(
            f'color: {fc1}; font-size: {fsize + 1}px; font-weight: bold;'
            ' background: transparent; padding: 8px 6px 4px 6px;'
        )
        for row in (self._artists_row, self._albums_row, self._top_row):
            row.set_accent_color(color)
        if hasattr(self, '_track_header'):
            self._track_header.set_accent(color)
        if hasattr(self, '_track_card'):
            border  = getattr(theme, 'border_color',        '#2a2a2a') if theme else '#2a2a2a'
            card_bg = getattr(theme, 'now_playing_card_bg', '#1e1e1e') if theme else '#1e1e1e'
            self._track_card.set_border(border)
            self._track_card.set_bg(card_bg)
        if self._scroll_reveal:
            self._scroll_reveal.color = color

    def refresh(self):
        if not self._client:
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = _StarredWorker(self._client)
        self._worker.done.connect(self._on_data)
        self._worker.start()

    # ── Internal ──────────────────────────────────────────────────────────

    def _on_data(self, data: dict):
        songs   = data.get('songs',   [])
        albums  = data.get('albums',  [])
        artists = data.get('artists', [])

        # Build artist-id lookup first — used by both sections
        # _parse_song_data stores artist ID as 'artist_id' (not 'artistId')
        song_cover_lookup: dict = {}
        for s in songs:
            name = s.get('artist', '')
            aid  = s.get('artist_id') or s.get('artistId', '')
            if name and name not in song_cover_lookup and aid:
                # Ensure ar- prefix so Navidrome serves the artist photo
                song_cover_lookup[name] = aid if aid.startswith('ar-') else f'ar-{aid}'

        # Artists row — use artist_id from songs for reliable artist photo
        artist_items = []
        for a in artists:
            card = self._artist_to_card(a)
            name = a.get('name', '')
            if name in song_cover_lookup:
                card['coverArt'] = song_cover_lookup[name]
            artist_items.append(card)
        self._artists_row.populate(artist_items)

        # Albums row
        album_items = [self._album_to_card(a) for a in albums]
        self._albums_row.populate(album_items)

        # Top Artists by Favorites — count songs per artist, cap at 16
        counts = Counter(s.get('artist', '') for s in songs if s.get('artist'))
        artist_lookup = {a.get('name', ''): a for a in artists}

        top = sorted(counts.items(), key=lambda x: -x[1])[:16]
        top_items = []
        for name, count in top:
            a = artist_lookup.get(name, {})
            # Always prefer artistId from songs — it reliably maps to the artist
            # photo via getCoverArt. Starred-artist coverArt can be stale/wrong.
            cid = song_cover_lookup.get(name) or a.get('id', '')
            card = {
                'id':           a.get('id', ''),
                'title':        name,
                'artist':       f'{count} song{"s" if count != 1 else ""}',
                'coverArt':     cid,
                '_is_artist':   True,
                '_artist_name': name,
            }
            top_items.append(card)
        self._top_row.populate(top_items)

        self._populate_tracks(songs)

        # Queue covers through the persistent worker
        if self._client:
            self._ensure_cover_worker()
            all_cover_ids = set()
            for c in artist_items + album_items + top_items:
                cid = c.get('coverArt', '')
                if cid:
                    all_cover_ids.add(cid)
            for cid in all_cover_ids:
                self._cover_worker.queue_cover(cid)

    def _ensure_cover_worker(self):
        if self._cover_worker and self._cover_worker.isRunning():
            return
        self._cover_worker = GridCoverWorker(self._client)
        self._cover_worker.cover_ready.connect(self._on_cover)
        self._cover_worker.start()

    def _on_cover(self, cover_id: str, image_data: bytes):
        for row in (self._artists_row, self._albums_row, self._top_row):
            row.apply_cover(cover_id, image_data)

    # col → sort key function
    _SORT_KEYS = {
        0: lambda t, i: i,
        1: lambda t, i: t.get('title', '').lower(),
        2: lambda t, i: t.get('artist', '').lower(),
        3: lambda t, i: t.get('album', '').lower(),
        5: lambda t, i: t.get('duration_ms', 0) or 0,
        6: lambda t, i: int(t.get('play_count', 0) or 0),
    }

    def _on_sort(self, col: int, state: str):
        songs = list(getattr(self, '_songs_original', self._songs))
        if state and col in self._SORT_KEYS:
            key = self._SORT_KEYS[col]
            songs = sorted(enumerate(songs), key=lambda x: key(x[1], x[0]),
                           reverse=(state == 'desc'))
            songs = [s for _, s in songs]
        self._populate_tracks(songs, update_original=False)

    def _populate_tracks(self, songs: list, update_original: bool = True):
        if update_original:
            self._songs_original = list(songs)
        tree = self._track_tree
        tree.setUpdatesEnabled(False)
        tree.clear()
        theme = getattr(self.window(), 'theme', None)
        pri = QColor(getattr(theme, 'font_color_primary',   '#dddddd') if theme else '#dddddd')
        sec = QColor(getattr(theme, 'font_color_secondary', '#aaaaaa') if theme else '#aaaaaa')
        for idx, t in enumerate(songs):
            dur_ms = t.get('duration_ms', 0) or int(t.get('duration', 0)) * 1000
            secs   = dur_ms // 1000
            dur    = f'{secs // 60}:{secs % 60:02d}'
            item = QTreeWidgetItem([
                str(idx + 1),
                t.get('title', ''),
                t.get('artist', ''),
                t.get('album', '') or '',
                t.get('genre', '') or '',
                dur,
                str(t.get('play_count') or 0) if t.get('play_count') else '-',
            ])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {'_track_idx': idx, 'id': str(t.get('id', ''))})
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(5, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            for col in range(7):
                item.setForeground(col, pri if col == 1 else sec)
            tree.addTopLevelItem(item)
        tree.setUpdatesEnabled(True)
        row_h = tree.sizeHintForRow(0) if songs else 38
        hdr_h = tree.header().height()
        tree.setFixedHeight(hdr_h + row_h * len(songs) + 4)
        self._songs = songs   # store for double-click lookup

    _COL_SETTINGS_KEY = 'favorites/track_col_widths'
    _COL_MIN = {0: 30, 2: 80, 3: 80, 4: 60, 5: 50, 6: 40}

    def _save_col_widths(self):
        from PyQt6.QtCore import QSettings
        hdr = self._track_header
        widths = {str(i): hdr.sectionSize(i) for i in range(hdr.count()) if i != 1}
        QSettings().setValue(self._COL_SETTINGS_KEY, widths)

    def _restore_col_widths(self):
        from PyQt6.QtCore import QSettings
        saved = QSettings().value(self._COL_SETTINGS_KEY)
        if not isinstance(saved, dict):
            return
        hdr = self._track_header
        hdr.blockSignals(True)
        for col_str, width in saved.items():
            col = int(col_str)
            if col != 1:
                hdr.resizeSection(col, max(self._COL_MIN.get(col, 30), int(width)))
        hdr.blockSignals(False)

    def _on_track_double_clicked(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        idx  = data.get('_track_idx', -1)
        if 0 <= idx < len(getattr(self, '_songs', [])):
            self.play_track.emit(self._songs[idx])

    def _on_artist_card_clicked(self, card: dict):
        """Artist cards emit album_clicked with _is_artist=True — route to artist view."""
        if card.get('_is_artist') and card.get('_artist_name'):
            self.artist_clicked.emit(card['_artist_name'])
        else:
            self.album_clicked.emit(card)

    @staticmethod
    def _artist_to_card(a: dict) -> dict:
        n = a.get('albumCount', a.get('album_count', ''))
        subtitle = f"{n} Album{'s' if n != 1 else ''}" if n else ''
        # Navidrome serves artist photos at getCoverArt?id=ar-{artist_id}.
        # Prefer explicit artistImageUrl, then build the ar- prefixed cover ID.
        artist_id = a.get('id', '')
        ar_id = artist_id if artist_id.startswith('ar-') else f'ar-{artist_id}'
        cid = a.get('artistImageUrl') or a.get('coverArt') or ar_id
        return {
            'id':           artist_id,
            'title':        a.get('name', ''),
            'artist':       subtitle,
            'coverArt':     cid,
            '_is_artist':   True,
            '_artist_name': a.get('name', ''),
        }

    @staticmethod
    def _album_to_card(a: dict) -> dict:
        return {
            'id':       a.get('id', ''),
            'title':    a.get('name', a.get('title', '')),
            'artist':   a.get('artist', a.get('artistName', '')),
            'year':     a.get('year', ''),
            'coverArt': a.get('coverArt', ''),
        }

    def showEvent(self, event):
        super().showEvent(event)
        if self._scroll_reveal is None:
            self._scroll_reveal = install_scroll_reveal(
                self.scroll.viewport(),
                self.scroll.verticalScrollBar()
            )
            self._scroll_reveal.color = self._accent
        # Refresh data every time the tab is shown so new favorites appear
        self.refresh()
