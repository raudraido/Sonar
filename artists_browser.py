import random
import re
import time
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                             QListWidgetItem, QStackedWidget,
                             QLabel, QScrollArea,
                             QTreeWidgetItem, QTreeWidget, QHeaderView, QAbstractItemView,
                             QStyledItemDelegate, QStyle)

from PyQt6.QtCore import Qt, QSize, pyqtSignal, pyqtProperty, QTimer, QPoint, QRect, QRectF, QThread, QEvent, QAbstractListModel, QModelIndex, pyqtSlot, QObject, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont, QPainterPath, QPen, QFontMetrics, QPolygon, QPalette
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider

from player.widgets import CoverImageProvider, QMLGridWrapper, QMLMiddleClickScroller, AlbumModel, ArrowButton, AlbumIconProvider, AlbumDetailCoverProvider
from player.qml_search import SearchController, GridSearchKeyFilter, set_window_shortcuts_enabled
from player import resource_path
from player.workers import GridCoverWorker

from player.mixins.visuals import scrollbar_css, install_scroll_reveal, resolve_menu_hover, SmoothScroller, CoverDecodeWorker


class ArtistPlayWorker(QThread): # Fetches all tracks matching an artist (incl. as albumArtist, splitting multi-artist strings), sorted by album/disc/track number for playback.
    
    tracks_ready = pyqtSignal(list)

    def __init__(self, client, name):
        super().__init__()
        self.client = client
        self.name = name

    def run(self):
        try:
            raw_tracks = self.client.search_artist_tracks(self.name)
            target = self.name.lower().strip()
            
            
            _split_re = re.compile(r'(?: /// | • | / | feat\. | Feat\. | ft\. | Ft\. | vs\. | Vs\. )')

            def _tokens(s):
                return {p.strip().lower() for p in _split_re.split(s) if p.strip()}

            filtered = []
            for t in raw_tracks:
                artist_tokens = _tokens(str(t.get('artist') or ''))
                alb_artist_tokens = _tokens(str(t.get('albumArtist') or t.get('album_artist') or ''))
                
                if target in artist_tokens or target in alb_artist_tokens:
                    filtered.append(t)

            filtered.sort(key=lambda x: (x.get('album', ''), int(x.get('discNumber', 1)), int(x.get('trackNumber', 0))))
            self.tracks_ready.emit(filtered)
        except Exception as e:
            print(f"Error: {e}")
            self.tracks_ready.emit([])

class LiveArtistDetailWorker(QThread): # Fetches everything for ONE artist's detail page (albums, top songs, bio/similar artists, appears-on) via parallel network calls, streaming albums_ready/top_songs_ready/appears_ready as each piece arrives.
    albums_ready    = pyqtSignal(dict, list, list)   # info, main_albums, singles
    top_songs_ready = pyqtSignal(list)               # top_songs
    appears_ready   = pyqtSignal(list)               # appears_on

    def __init__(self, client, artist_id, artist_name):
        super().__init__()
        self.client = client
        self.artist_id = artist_id
        self.artist_name = artist_name

    def run(self):
        try:
            from concurrent.futures import ThreadPoolExecutor

            if not self.client: return

            _t0 = time.time()
            def _ms(t): return f"{(time.time() - t) * 1000:.0f}ms"

            _split_re = re.compile(r'(?: /// | • | / | feat\. | Feat\. | ft\. | Ft\. | vs\. | Vs\. )')

            def _artist_tokens(raw: str) -> set:
                parts = _split_re.split(raw)
                return {p.strip().lower() for p in parts if p.strip()}

            def sort_by_year(x):
                try: return int(x.get('year', 0) or 0)
                except: return 0

            print(f"[TIMING] {self.artist_name!r} — worker started")

            # ── PHASE 1: resolve ID if missing — uses in-memory cache to avoid repeat search3 ──
            if not self.artist_id and self.artist_name:
                _t1 = time.time()
                if hasattr(self.client, 'resolve_artist_id'):
                    self.artist_id = self.client.resolve_artist_id(self.artist_name)
                else:
                    target_name = self.artist_name.lower().strip()
                    try:
                        sr = self.client.search3(self.artist_name, size=0, artist_count=5, album_count=0)
                        for a in sr.get('artist', []):
                            if a.get('name', '').lower().strip() == target_name:
                                self.artist_id = a.get('id')
                                break
                    except Exception:
                        pass
                print(f"[TIMING]   phase1 ID resolve: {_ms(_t1)}  id={self.artist_id}")

            # ── PHASES 2+3+4 in PARALLEL ─────────────────────────────────────
            # get_artist, get_top_songs, and get_artist_info2 are all independent
            # network calls — run them concurrently so total wait ≈ slowest one.
            def _fetch_artist():
                _t = time.time()
                result = self.client.get_artist(self.artist_id) or {} if self.artist_id else {}
                print(f"[TIMING]   get_artist: {_ms(_t)}  albums={len(result.get('album', []))}")
                return result

            def _fetch_top_songs():
                _t = time.time()
                result = []
                if self.artist_name:
                    try: result = self.client.get_top_songs(self.artist_name, count=5)
                    except Exception: pass
                print(f"[TIMING]   get_top_songs: {_ms(_t)}  count={len(result)}")
                return result

            def _fetch_info2():
                if not self.artist_id:
                    return {}
                import threading as _th

                # Run native and subsonic simultaneously — native is preferred when it
                # has data (fast DB read), subsonic is the fallback that may hit Last.fm.
                # Starting both at once saves up to ~500ms vs the old sequential approach.
                sub_result  = [{}]
                sub_done    = _th.Event()

                def _run_subsonic():
                    try:
                        _t = time.time()
                        r = self.client.get_artist_info2(self.artist_id) if hasattr(self.client, 'get_artist_info2') else {}
                        sub_result[0] = r or {}
                        print(f"[TIMING]   get_artist_info2: {_ms(_t)}  has_bio={bool(sub_result[0].get('biography'))}")
                    except Exception:
                        pass
                    finally:
                        sub_done.set()

                if hasattr(self.client, 'get_artist_info2'):
                    _th.Thread(target=_run_subsonic, daemon=True).start()
                else:
                    sub_done.set()

                native_result = {}
                if hasattr(self.client, 'get_artist_info_native'):
                    _t = time.time()
                    try:
                        native_result = self.client.get_artist_info_native(self.artist_id) or {}
                        print(f"[TIMING]   get_artist_info_native: {_ms(_t)}  has_bio={bool(native_result.get('biography'))}  similar={len(native_result.get('similarArtist', []))}")
                    except Exception as e:
                        print(f"[TIMING]   get_artist_info_native FAILED: {e}")

                # Always wait for subsonic so we can merge similar artists
                sub_done.wait()
                sub = sub_result[0]

                # Merge: prefer native for bio (faster), prefer whichever has similar artists
                merged = {}
                merged['biography'] = (native_result.get('biography') or
                                       sub.get('biography') or '')
                merged['similarArtist'] = (native_result.get('similarArtist') or
                                           sub.get('similarArtist') or [])
                for key in ('largeImageUrl', 'mediumImageUrl', 'smallImageUrl'):
                    merged[key] = native_result.get(key) or sub.get(key) or ''
                return merged

            _t_parallel = time.time()
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_artist = pool.submit(_fetch_artist)
                f_songs  = pool.submit(_fetch_top_songs)
                f_info2  = pool.submit(_fetch_info2)

                # Albums: unblock as soon as get_artist returns
                info = f_artist.result() or {}
                print(f"[TIMING]   → albums_ready emit 1 at {_ms(_t0)} (parallel started {_ms(_t_parallel)} ago)")
                if not info:
                    info = {'name': self.artist_name or "Unknown"}

                raw_albums = info.get('album', [])
                main_albums, singles = [], []
                own_album_ids = set()
                target_lower = (self.artist_name or info.get('name', '')).lower().strip()

                for a in raw_albums:
                    aid_str = str(a.get('id', ''))
                    if aid_str:
                        own_album_ids.add(aid_str)
                    rtypes_raw = a.get('releaseTypes') or []
                    rtype = ' '.join(rtypes_raw).lower() if isinstance(rtypes_raw, list) else str(rtypes_raw).lower()
                    if not rtype:
                        rtype = str(a.get('albumType') or a.get('releaseType') or a.get('type') or '').lower()
                    if 'single' in rtype or 'ep' in rtype:
                        singles.append(a)
                    else:
                        main_albums.append(a)

                main_albums.sort(key=sort_by_year, reverse=True)
                singles.sort(key=sort_by_year, reverse=True)

                # Emit albums immediately — UI shows real content, skeleton is replaced
                self.albums_ready.emit(info, main_albums, singles)

                # Start appears_on search now that we have own_album_ids
                def _fetch_appears():
                    _t = time.time()
                    result = []
                    seen = set()
                    try:
                        # Use search3 directly (capped at 500) rather than the
                        # full search_artist_tracks call which fetches 2000 songs.
                        sr     = self.client.search3(self.artist_name, size=500, artist_count=0, album_count=0) if self.artist_name else {}
                        tracks = sr.get('song', [])
                        for t in tracks:
                            alb_id = str(t.get('albumId') or t.get('album_id') or '')
                            if not alb_id or alb_id in own_album_ids or alb_id in seen:
                                continue
                            track_tokens = _artist_tokens(str(t.get('artist') or ''))
                            alb_tokens   = _artist_tokens(str(t.get('albumArtist') or t.get('album_artist') or ''))
                            if target_lower in alb_tokens or target_lower not in track_tokens:
                                continue
                            alb_artist_raw = str(t.get('albumArtist') or t.get('album_artist') or '')
                            seen.add(alb_id)
                            result.append({
                                'id': alb_id,
                                'title': t.get('album') or 'Unknown Album',
                                'artist': t.get('albumArtist') or t.get('album_artist') or t.get('artist') or 'Unknown Artist',
                                'albumArtist': alb_artist_raw,
                                'year': str(t.get('year') or ''),
                                'coverArt': t.get('coverArt') or alb_id,
                                'cover_id': t.get('coverArt') or alb_id,
                            })
                    except Exception as e:
                        print(f"[LiveArtistDetailWorker] appears_on failed: {e}")
                    print(f"[TIMING]   search3/appears: {_ms(_t)}  found={len(result)}")
                    result.sort(key=sort_by_year, reverse=True)
                    return result

                f_appears = pool.submit(_fetch_appears)

                # Emit top_songs and bio each as soon as ready — whichever finishes first
                from concurrent.futures import wait as _wait, FIRST_COMPLETED
                pending = {f_songs, f_info2}
                top_songs = []
                while pending:
                    done, pending = _wait(pending, return_when=FIRST_COMPLETED)
                    for f in done:
                        if f is f_songs:
                            top_songs = f.result() or []
                            print(f"[TIMING]   → top_songs_ready emit at {_ms(_t0)}  count={len(top_songs)}")
                            self.top_songs_ready.emit(top_songs)
                        else:
                            extra = f.result() or {}
                            bio = extra.get('biography') or extra.get('bio') or ''
                            if bio:
                                info['biography'] = bio
                            similar = extra.get('similarArtist') or []
                            if isinstance(similar, dict):
                                similar = [similar]
                            if similar:
                                info['similar_artists'] = similar
                            img_url = (extra.get('largeImageUrl') or
                                       extra.get('mediumImageUrl') or
                                       extra.get('smallImageUrl') or '')
                            if img_url:
                                info['artistImageUrl'] = img_url
                            if bio or similar or img_url:
                                print(f"[TIMING]   → albums_ready emit 2 (bio/similar/img) at {_ms(_t0)}  has_bio={bool(bio)}  similar={len(similar)}  has_img={bool(img_url)}")
                                self.albums_ready.emit(info, main_albums, singles)
                            else:
                                print(f"[TIMING]   → no bio/similar/img at {_ms(_t0)}")

                # Appears on — slowest, emit last
                appears_on = f_appears.result() or []
                print(f"[TIMING]   → appears_ready emit at {_ms(_t0)}  count={len(appears_on)}")
                self.appears_ready.emit(appears_on)

            print(f"[TIMING] {self.artist_name!r} — worker DONE at {_ms(_t0)}")

        except Exception as e:
            print(f"[LiveArtistDetailWorker] Error: {e}")

class LiveArtistWorker(QThread): # Fetches ONE page of artist tiles for the artist grid: server-paginated browse mode for plain sorts, or full-list fetch with local sort (incl. seeded random)/search filtering/relevance ranking + pagination otherwise.
    page_ready = pyqtSignal(list, int, int)

    def __init__(self, client, query, sort_type, is_ascending, page, page_size, random_seed=0):
        super().__init__()
        self.client = client
        self.query = query.lower().strip() if query else ""
        self.sort_type = sort_type
        self.is_ascending = is_ascending
        self.page = page
        self.page_size = page_size
        self.random_seed = random_seed
        self.is_cancelled = False

    def run(self):
        try:
            import math
            if not self.client: return

            if hasattr(self.client, 'get_artists_native_page') and self.sort_type != 'random' and not self.query:
                # BROWSE MODE (no query): use native API for fast server-side sort + pagination
                native_sort = "name"
                if self.sort_type == 'albums_count': native_sort = "albumCount"
                elif self.sort_type == 'most_played': native_sort = "playCount"
                
                native_order = "ASC" if self.is_ascending else "DESC"
                start = (self.page - 1) * self.page_size
                end = start + self.page_size
                
                page_items, total_items = self.client.get_artists_native_page(
                    sort_by=native_sort, order=native_order, start=start, end=end, query=""
                )
                
                if self.is_cancelled: return
                
                total_pages = max(1, math.ceil(total_items / self.page_size))
                self.page_ready.emit(page_items, total_items, total_pages)
                return
            
            # SEARCH MODE (query present) or random sort: fetch all, filter locally, paginate
            # The native API's _q param is unreliable -- only page 1 appears correct because
            # populate_grid re-filters client-side; pages 2+ return unfiltered server data.
            artists = self.client.get_artists_live()
            filtered = [a for a in artists if self.query in a.get('name', '').lower()] if self.query else list(artists)
            import random
            rnd = random.Random(self.random_seed)
            if self.sort_type == 'random':
                rnd.shuffle(filtered)
            elif self.sort_type == 'albums_count':
                filtered.sort(key=lambda a: int(a.get('albumCount', 0) or 0), reverse=not self.is_ascending)
            elif self.sort_type == 'most_played':
                filtered.sort(key=lambda a: int(a.get('playCount', 0) or 0), reverse=not self.is_ascending)
            else:  # alphabetical
                filtered.sort(key=lambda a: (a.get('name') or '').lower(), reverse=not self.is_ascending)
            
            if self.query:
                def get_relevance(a):
                    name_lower = (a.get('name') or '').lower().strip()
                    
                    if name_lower == self.query:
                        return 0  # 1st Tier: Exact Match ("Jam")
                    if name_lower.startswith(self.query + " "):
                        return 1  # 2nd Tier: Starts with exact word ("Jam & Spoon")
                    if name_lower.startswith(self.query):
                        return 2  # 3rd Tier: Starts with substring ("James", "Jamie")
                    if f" {self.query} " in f" {name_lower} ":
                        return 3  # 4th Tier: Contains as whole word ("Pearl Jam")
                    return 4      # 5th Tier: Contains as substring ("Benjamin")
                
                filtered.sort(key=get_relevance)
            
            total_items = len(filtered)
            total_pages = max(1, math.ceil(total_items / self.page_size))
            offset = (min(self.page, total_pages) - 1) * self.page_size
            
            if self.is_cancelled: return
            
            self.page_ready.emit(filtered[offset : offset + self.page_size], total_items, total_pages)
            
        except Exception as e:
            print(f"[LiveArtistWorker] Error: {e}")
            self.page_ready.emit([], 0, 1)

class TrackLoaderWorker(QThread): # Fetches the track list for a single album and emits it when ready.
    
    tracks_ready = pyqtSignal(list, str)   # (tracks, album_id)

    def __init__(self, client, album_id):
        super().__init__()
        self.client   = client
        self.album_id = str(album_id)

    def run(self):
        try:
            tracks = self.client.get_album_tracks(self.album_id)
            self.tracks_ready.emit(tracks or [], self.album_id)
        except Exception as e:
            print(f"[TrackLoaderWorker] Error fetching album {self.album_id}: {e}")
            self.tracks_ready.emit([], self.album_id)

class PopularTrackDelegate(QStyledItemDelegate): # Render track items with cover art, title, and hover/selection effects.
    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent_color = "#ffffff"

    def update_color(self, color):
        self.accent_color = color

    def _theme(self):
        p = self.parent()
        w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)

    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'

    def paint(self, painter, option, index):
        painter.save()
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        if index.column() == 1 and (is_hovered or is_selected):
            view = option.widget
            vp_w = view.viewport().width() if view else option.rect.width()
            # The SongListWidget has no scrollbar; check parent QScrollArea instead
            sb_w = 0
            p = view.parentWidget() if view else None
            while p:
                if isinstance(p, QScrollArea):
                    sb = p.verticalScrollBar()
                    if sb and sb.isVisible():
                        sb_w = sb.width()
                    break
                p = p.parentWidget()
            right_inset = max(1, 8 - sb_w)
            row_rect = QRectF(8, option.rect.y(), vp_w - 8 - right_inset, option.rect.height())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(resolve_menu_hover(self._theme())))
            painter.drawRoundedRect(row_rect, 6, 6)

        if index.column() == 1:
            track = index.data(Qt.ItemDataRole.UserRole)
            if not track:
                painter.restore()
                return

            rect = option.rect
            
            # 2. Draw Cover Art
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            cover_size = 40
            margin = (rect.height() - cover_size) // 2
            cover_rect = QRect(rect.x() + 5, rect.y() + margin, cover_size, cover_size)
            
            if icon and not icon.isNull():
                pixmap = icon.pixmap(cover_size, cover_size)
                path = QPainterPath()
                path.addRoundedRect(QRectF(cover_rect), 4, 4) 
                painter.setClipPath(path)
                painter.drawPixmap(cover_rect, pixmap)
                painter.setClipping(False)
            else:
                painter.setBrush(QColor("#222"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(cover_rect, 4, 4)

            # 3. Draw Title (Vertically Centered, No Subtitle)
            text_x = cover_rect.right() + 15
            title = track.get('title', 'Unknown')
            
            f = QFont(); f.setPixelSize(self._primary_px())
            painter.setFont(f)

            if is_selected or is_hovered:
                painter.setPen(QColor(self.accent_color))
            else:
                painter.setPen(QColor(self._primary_color()))
                
            title_rect = QRect(text_x, rect.y(), rect.width() - text_x, rect.height())
            
            fm = painter.fontMetrics()
            elided_title = fm.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width() - 10)
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)
            
        else:
            super().paint(painter, option, index)
            
        painter.restore()

class AlbumLinkDelegate(QStyledItemDelegate): # Render album links with hover effects and underlining when hovered.
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered_row = -1
        self.accent_color = "#ffffff"

    def update_color(self, color):
        self.accent_color = color

    def _theme(self):
        p = self.parent()
        w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)

    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    
    def set_hovered(self, row):
        self.hovered_row = row

    def paint(self, painter, option, index):
        painter.save()
        is_selected = option.state & QStyle.StateFlag.State_Selected
        is_row_hovered = option.state & QStyle.StateFlag.State_MouseOver
        is_cell_hovered = (index.row() == self.hovered_row)
        
        font = QFont(); font.setPixelSize(self._primary_px())

        if is_cell_hovered:
            font.setUnderline(True)
            painter.setPen(QColor(self.accent_color))
        elif is_row_hovered or is_selected:
            painter.setPen(QColor(self.accent_color))
        else:
            painter.setPen(QColor(self._primary_color())) 
            
        painter.setFont(font)
        
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            rect = option.rect
            text_rect = QRect(rect.x() + 5, rect.y(), rect.width() - 10, rect.height())
            
            fm = painter.fontMetrics()
            elided_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_text)
        
        painter.restore()

class SongListWidget(QTreeWidget): # Display a list of songs with cover art, title, album, and duration; supports hover effects and emits signals on interactions.
    play_track = pyqtSignal(dict)
    album_clicked = pyqtSignal(dict) 

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["#", "Track", "Album", "🕒"])
        self.setHeaderHidden(True) 
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        
        # 1. FORCE Qt to allow StrongFocus on both the widget and viewport!
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.itemClicked.connect(self.on_item_single_clicked) 
        
        self.setFixedHeight(0) 
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        
        self.track_delegate = PopularTrackDelegate(self)
        self.setItemDelegateForColumn(1, self.track_delegate)
        
        self.album_delegate = AlbumLinkDelegate(self)
        self.setItemDelegateForColumn(2, self.album_delegate)
        
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 350)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAllColumnsShowFocus(False)

        self.setColumnWidth(0, 45) 
        self.setColumnWidth(3, 70)
        
        self.update_style("#ffffff")

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.pos())
        # If hovering over column 2 (Album), trigger the underline!
        if index.isValid() and index.column() == 2:
            self.album_delegate.set_hovered(index.row())
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.album_delegate.set_hovered(-1)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            
        self.viewport().update() # Force an instant visual refresh
        super().mouseMoveEvent(event)
        
    def leaveEvent(self, event):
        self.album_delegate.set_hovered(-1)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.viewport().update()
        super().leaveEvent(event)

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        self.viewport().update()

    def update_style(self, accent_color):
        self.track_delegate.update_color(accent_color)
        self.album_delegate.update_color(accent_color)

        theme = getattr(self.window(), 'theme', None) if self.window() else None
        pri_color = getattr(theme, 'font_color_primary', '#dddddd') if theme else '#dddddd'
        pri_size  = getattr(theme, 'font_size_primary', 14) if theme else 14

        self.setStyleSheet(f"""
            QTreeWidget {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QTreeWidget::item {{
                height: 44px;
                color: {pri_color};
                font-size: {pri_size}px;
                border: none;
                outline: 0;
            }}
            QTreeWidget::item:hover {{
                background: transparent;
                color: {accent_color};
                border: none;
                outline: 0;
            }}
            QTreeWidget::item:selected {{
                background: transparent;
                color: {pri_color};
                border: none;
                outline: 0;
            }}
        """)
        self.viewport().update()

    def populate(self, songs, cover_worker=None, pending_items=None):
        self.clear()
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt
        theme = getattr(self.window(), 'theme', None) if self.window() else None
        pri_size = getattr(theme, 'font_size_primary', 14) if theme else 14
        normal_font = QFont(); normal_font.setPixelSize(pri_size)
        
        for i, s in enumerate(songs):
            item = QTreeWidgetItem([str(i+1), "", s.get('album', ''), s.get('duration', '')])
            item.setData(0, Qt.ItemDataRole.UserRole, s)
            item.setData(1, Qt.ItemDataRole.UserRole, s) 
            
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setFont(0, normal_font)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFont(3, normal_font)
            
            self.addTopLevelItem(item)
            
            if cover_worker and pending_items is not None:
                cid = s.get('coverArt') or s.get('albumId')
                if cid:
                    if cid not in pending_items:
                        pending_items[cid] = []
                    pending_items[cid].append(item)
                    cover_worker.queue_cover(cid, priority=True)
        
        row_h = 44
        total_h = (len(songs) * row_h) + 20
        self.setFixedHeight(total_h)

    def on_item_single_clicked(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and col == 2:
            self.album_clicked.emit(data)

    def on_item_double_clicked(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.play_track.emit(data)

class SectionCoverProvider(QQuickImageProvider): # Serves album covers for the artist detail grids, with a class-level cache to share loaded images across all sections.
    
    _cache = {}

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id, _requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import QRectF
        real_id = id.split("?t=")[0]
        data = self._cache.get(real_id)
        size = 250
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        if data:
            source = QImage()
            source.loadFromData(data)
            if not source.isNull():
                source = source.scaled(size, size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                painter = QPainter(img)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                path = QPainterPath()
                path.addRoundedRect(QRectF(0, 0, size, size), 12, 12)
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()
        return img, img.size()

class SectionGridBridge(QObject): # Bridge for signals between the QML section grids and the Python code; also reports content height changes for dynamic resizing.
    accentColorChanged        = pyqtSignal(str)
    selectIndex               = pyqtSignal(int)
    itemClicked               = pyqtSignal(int)
    playClicked               = pyqtSignal(int)
    artistNameClicked         = pyqtSignal(str, str)  # name, artist_id
    contentHeightChanged      = pyqtSignal(int)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    skeletonBaseColorChanged  = pyqtSignal(str)
    explicitWidthChanged      = pyqtSignal(int)

    def __init__(self, model):
        super().__init__()
        self.model = model

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        self.itemClicked.emit(idx)

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        self.playClicked.emit(idx)

    @pyqtSlot(str, str)
    def emitArtistNameClicked(self, name, artist_id=""):
        self.artistNameClicked.emit(name, artist_id)

    @pyqtSlot(float)
    def reportContentHeight(self, h):
        self.contentHeightChanged.emit(max(1, int(h) + 1))

class QMLAlbumSectionWidget(QWidget): # Widget for an album section in the artist detail view, using a QML GridView for smooth resizing and dynamic content.
    
    album_clicked       = pyqtSignal(dict)
    play_album          = pyqtSignal(dict)
    artist_name_clicked = pyqtSignal(str, str)  # name, artist_id

    def __init__(self, title, count, albums):
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 5, 0, 10)
        outer.setSpacing(5)

        # ── title bar ──────────────────────────────────────────────────────
        title_container = QWidget()
        self.title_layout = QHBoxLayout(title_container)
        self.title_layout.setContentsMargins(10, 0, 10, 0)
        self.title_layout.setSpacing(10)
        self.title_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 20px;")
        self.lbl_count = QLabel(str(count))
        self.lbl_count.setFixedHeight(22)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setStyleSheet(
            "color: #aaa; background: transparent; border: 1px solid #444;"
            " border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")

        self.title_layout.addWidget(self.lbl_title)
        self.title_layout.addWidget(self.lbl_count)
        self.title_layout.addStretch()
        outer.addWidget(title_container)
        title_container.setVisible(bool(title))

        # ── QML grid ───────────────────────────────────────────────────────
        self.qml_widget = QQuickWidget()
        self.qml_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_widget.setMinimumHeight(10)
        # QQuickWidget paints white for the first frame or two before its scene
        # graph is ready, so give it an opaque palette matching the clear color
        # to avoid a white flash while the QML content is still loading.
        self._set_qml_widget_color(QColor(14, 14, 14))
        outer.addWidget(self.qml_widget)

        self.album_model = AlbumModel()
        self.bridge = SectionGridBridge(self.album_model)

        self.bridge.itemClicked.connect(self._on_item_clicked)
        self.bridge.playClicked.connect(self._on_play_clicked)
        self.bridge.artistNameClicked.connect(self.artist_name_clicked)

        engine = self.qml_widget.engine()
        self._cover_provider = SectionCoverProvider()
        engine.addImageProvider("sectioncovers", self._cover_provider)

        ctx = self.qml_widget.rootContext()
        ctx.setContextProperty("sectionAlbumModel", self.album_model)
        ctx.setContextProperty("sectionBridge", self.bridge)

        self.qml_widget.setSource(QUrl.fromLocalFile(resource_path("artist_section_grid.qml")))
        from PyQt6.QtCore import QTimer as _QTimer
        def _emit_section_typography():
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
                self.lbl_title.setStyleSheet(f"color: {theme.font_color_primary}; font-weight: bold; font-size: 20px;")
                self.lbl_count.setStyleSheet(f"color: {theme.font_color_primary}; background: transparent; border: 1px solid {theme.border_color}; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")
        _QTimer.singleShot(0, _emit_section_typography)

        # facade so legacy code doing `row.list_widget.count()` etc. still works
        self.list_widget = _SectionListFacade(self)
        self.current_index = 0

        self.populate(albums)

    def items_per_row(self):
        avail = max(1, self.qml_widget.width() - 40)  # leftMargin + rightMargin = 40
        return max(1, int(avail / (180 + 20)))         # baseItemSize + itemGap*2

    def select(self, idx):
        count = self.album_model.rowCount()
        if count == 0:
            return
        idx = max(0, min(idx, count - 1))
        self.current_index = idx
        self.bridge.selectIndex.emit(idx)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = event.size().width()
        if w != event.oldSize().width() and w > 0:
            self.bridge.explicitWidthChanged.emit(w)
            self._set_height(w)

    def _set_height(self, w=None):
        if w is None:
            w = self.qml_widget.width()
        if w <= 0:
            return
        n = self.album_model.rowCount()
        if n == 0:
            return
        avail     = max(1, w - 4 - 4)          # leftMargin + rightMargin
        per_row   = max(1, int(avail // 200))   # baseItemSize(180) + itemGap*2(20)
        cell_w    = int(avail // per_row)
        cell_h    = cell_w + 70                 # matches QML cellHeight: widthPerItem + 70
        n_rows    = (n + per_row - 1) // per_row
        h         = n_rows * cell_h + 4 + 4    # topMargin + bottomMargin
        self.qml_widget.setFixedHeight(max(10, h))

    def _on_item_clicked(self, idx):
        if 0 <= idx < len(self.album_model.albums):
            self.album_clicked.emit(self.album_model.albums[idx])

    def _on_play_clicked(self, idx):
        if 0 <= idx < len(self.album_model.albums):
            self.play_album.emit(self.album_model.albums[idx])

    def set_bg_color(self, c: str):
        self._bg_color = c
        r, g, b = (int(x) for x in c.split(','))
        self._set_qml_widget_color(QColor(r, g, b))

    def _set_qml_widget_color(self, color: QColor):
        self.qml_widget.setClearColor(color)
        self.qml_widget.setAutoFillBackground(True)
        pal = self.qml_widget.palette()
        pal.setColor(QPalette.ColorRole.Window, color)
        self.qml_widget.setPalette(pal)

    def set_accent_color(self, color):
        self.bridge.accentColorChanged.emit(color)
        theme = getattr(self.window(), 'theme', None)
        if theme:
            self.bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
            self.bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            self.bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
            self.bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
            self.bridge.skeletonBaseColorChanged.emit(
                getattr(theme, 'skeleton_base', '#282828'))
            self.lbl_title.setStyleSheet(f"color: {theme.font_color_primary}; font-weight: bold; font-size: 20px;")
            self.lbl_count.setStyleSheet(f"color: {theme.font_color_primary}; background: transparent; border: 1px solid {theme.border_color}; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")

    def populate(self, albums):
        # Normalise so AlbumModel's cover_id key is always populated
        normalised = []
        for a in albums:
            d = dict(a)
            if not d.get('cover_id'):
                d['cover_id'] = d.get('coverArt') or d.get('id') or ''
            normalised.append(d)
        self.album_model.beginResetModel()
        self.album_model.albums = normalised
        self.album_model.endResetModel()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._set_height)

    def apply_cover(self, cover_id, image_data):
        SectionCoverProvider._cache[cover_id] = image_data
        self.album_model.update_cover(cover_id)

class _SectionListFacade: # Adapter so QMLAlbumSectionWidget exposes the same list_widget API (count, setFocus, setCurrentRow, visualItemRect, etc.) as QListWidget-based rows, letting shared section-navigation code treat both uniformly.

    def __init__(self, section: QMLAlbumSectionWidget):
        self._s = section

    def count(self):
        return len(self._s.album_model.albums)

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        self._s.qml_widget.setFocus(reason)
        self._s.bridge.selectIndex.emit(0)

    def setCurrentRow(self, row):
        self._s.bridge.selectIndex.emit(max(0, row))

    def clearSelection(self):
        pass

    def installEventFilter(self, obj):
        self._s.qml_widget.installEventFilter(obj)

    def viewport(self):
        return self._s.qml_widget

    # visualItemRect is only used for scroll-to-item; return the section top instead
    def visualItemRect(self, _item):
        return QRect(0, 0, self._s.qml_widget.width(), self._s.qml_widget.height() // max(1, self.count()))

    def item(self, _idx):
        return None

    def currentRow(self):
        return self._s.current_index

class CircularArtistDelegate(QStyledItemDelegate): # Render artist cards with circular photos, hover effects, and names below, for the related artists strip.
  

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#1db954")

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent()
        w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)

    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#eeeeee') if t else '#eeeeee'

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect     = option.rect
        size     = rect.width() - 20
        img_rect = QRect(rect.x() + 10, rect.y() + 10, size, size)

        is_hovered  = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # --- circular clip (same pattern as GridItemDelegate) ---
        clip = QPainterPath()
        clip.addEllipse(img_rect.x(), img_rect.y(), img_rect.width(), img_rect.height())

        painter.save()
        painter.setClipPath(clip)

        # Draw image
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pix = icon.pixmap(size, size)
            painter.drawPixmap(img_rect.x() + (size - pix.width()) // 2,
                               img_rect.y() + (size - pix.height()) // 2, pix)
        else:
            painter.setBrush(QColor("#333333"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(img_rect)

        # Dark hover overlay inside clip
        if is_hovered or is_selected:
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(img_rect)

            # Play button (smaller than album cards)
            center    = img_rect.center()
            play_size = min(40, size // 3)
            play_rect = QRect(0, 0, play_size, play_size)
            play_rect.moveCenter(center)
            painter.setBrush(self.master_color)
            painter.drawEllipse(play_rect)

            tri = play_size // 3
            p1 = QPoint(center.x() - tri // 3, center.y() - tri // 2)
            p2 = QPoint(center.x() - tri // 3, center.y() + tri // 2)
            p3 = QPoint(center.x() + tri // 2 + 2, center.y())
            painter.setBrush(QColor("#111111"))
            painter.drawPolygon(QPolygon([p1, p2, p3]))

        painter.restore()  # end clip

        # Accent ring drawn OUTSIDE clip so full pen width is visible
        if is_hovered or is_selected:
            painter.setPen(QPen(self.master_color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(img_rect.adjusted(-1, -1, 1, 1))

        # Artist name below (unclipped)
        data = index.data(Qt.ItemDataRole.UserRole)
        if data:
            name = data.get('name') or data.get('title') or ''
            text_color = self.master_color.name() if (is_hovered or is_selected) else self._primary_color()
            painter.setPen(QColor(text_color))
            font = painter.font()
            font.setBold(True)
            font.setPixelSize(self._primary_px())
            painter.setFont(font)
            fm = QFontMetrics(font)
            text_y = img_rect.bottom() + 10
            text_w = rect.width() - 10
            painter.drawText(
                QRect(rect.x() + 5, text_y, text_w, 20),
                Qt.AlignmentFlag.AlignHCenter,
                fm.elidedText(name, Qt.TextElideMode.ElideRight, text_w)
            )

        painter.restore()

class RelatedArtistRowWidget(QWidget): # A horizontally scrollable strip of related artists, showing circular photos with names below, and left/right arrows for navigation; max 10 items.
   
    artist_clicked = pyqtSignal(dict)

    CELL_W = 220

    def __init__(self, title, artists):
        super().__init__()
        self._artists = artists
        self._btn_left = None
        self._btn_right = None

        cell_h = self.CELL_W + 50

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 10)
        layout.setSpacing(5)

        # Title row
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(10, 0, 10, 0)
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color: white; font-weight: bold; font-size: 20px;")
        self.lbl_count = QLabel(str(len(artists)))
        self.lbl_count.setFixedHeight(22)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setStyleSheet("color: #aaa; background: transparent; border: 1px solid #444; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")
        self._accent_color = "#888888"
        self._btn_left  = ArrowButton("left",  self._accent_color)
        self._btn_right = ArrowButton("right", self._accent_color)

        self._btn_left.clicked.connect(lambda: self._scroll_by(-self.CELL_W))
        self._btn_right.clicked.connect(lambda: self._scroll_by(self.CELL_W))

        title_layout.addWidget(self.lbl_title)
        title_layout.addWidget(self.lbl_count)
        title_layout.addStretch()
        title_layout.addWidget(self._btn_left)
        title_layout.addWidget(self._btn_right)
        layout.addWidget(title_row)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setWrapping(False)
        self.list_widget.setMouseTracking(True)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.installEventFilter(self)
        self.list_widget.setIconSize(QSize(self.CELL_W, self.CELL_W))
        self.list_widget.setGridSize(QSize(self.CELL_W, cell_h))
        self.list_widget.setFixedHeight(cell_h + 12)
        self.list_widget.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item:hover { background: #1a1a1a; }
            QListWidget::item:selected { background: transparent; }
        """)

        self.delegate = CircularArtistDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self.delegate)

        placeholder = QPixmap(self.CELL_W, self.CELL_W)
        placeholder.fill(QColor("#333"))
        placeholder_icon = QIcon(placeholder)

        for artist in artists:
            item = QListWidgetItem()
            item.setIcon(placeholder_icon)
            item.setData(Qt.ItemDataRole.UserRole, artist)
            item.setSizeHint(QSize(self.CELL_W, cell_h))
            self.list_widget.addItem(item)

        self.list_widget.horizontalScrollBar().valueChanged.connect(self._update_arrow_buttons)
        self.list_widget.horizontalScrollBar().rangeChanged.connect(self._update_arrow_buttons)

        layout.addWidget(self.list_widget)

        from PyQt6.QtCore import QTimer as _QTimer
        def _apply_title_color():
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.lbl_title.setStyleSheet(f"color: {theme.font_color_primary}; font-weight: bold; font-size: 20px;")
                self.lbl_count.setStyleSheet(f"color: {theme.font_color_primary}; background: transparent; border: 1px solid {theme.border_color}; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")
        _QTimer.singleShot(0, _apply_title_color)

    def _scroll_by(self, delta):
        sb = self.list_widget.horizontalScrollBar()
        sb.setValue(sb.value() + delta)

    def _update_arrow_buttons(self, *_):
        sb = self.list_widget.horizontalScrollBar()
        self._btn_left.setEnabled(sb.value() > sb.minimum())
        self._btn_right.setEnabled(sb.value() < sb.maximum())

    def set_accent_color(self, color):
        self._accent_color = color
        self._btn_left.set_color(color)
        self._btn_right.set_color(color)
        self.delegate.set_master_color(color)
        theme = getattr(self.window(), 'theme', None)
        if theme:
            self.lbl_title.setStyleSheet(f"color: {theme.font_color_primary}; font-weight: bold; font-size: 20px;")
            self.lbl_count.setStyleSheet(f"color: {theme.font_color_primary}; background: transparent; border: 1px solid {theme.border_color}; border-radius: 4px; padding: 0px 8px; font-size: 12px; font-weight: bold;")

    def eventFilter(self, source, event):
        if source is self.list_widget and event.type() == event.Type.Wheel:
            delta = event.angleDelta()
            scroll = delta.x() if delta.x() != 0 else delta.y()
            self.list_widget.horizontalScrollBar().setValue(
                self.list_widget.horizontalScrollBar().value() - scroll
            )
            return True
        return super().eventFilter(source, event)

class _ArtistPhotoOverlay(QWidget): # A full-screen tinted overlay showing the artist photo large and centered.
   
    def __init__(self, pixmap, parent):
        super().__init__(parent)
        self._pixmap = pixmap
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.raise_()
        self.setFocus()
        self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(0, 0, 0, 175))
        if self._pixmap and not self._pixmap.isNull():
            max_dim = min(int(self.width() * 0.55), int(self.height() * 0.65))
            scaled = self._pixmap.scaled(max_dim, max_dim,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
            x = (self.width()  - scaled.width())  // 2
            y = (self.height() - scaled.height()) // 2
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, scaled.width(), scaled.height()), 18, 18)
            p.setClipPath(path)
            p.drawPixmap(x, y, scaled)
        p.end()

    def mousePressEvent(self, _):
        self.close()
        self.deleteLater()

    def keyPressEvent(self, event):
        self.close()
        self.deleteLater()

class ArtistDetailCoverProvider(AlbumDetailCoverProvider): # Provides the artist photo for the main header in the artist detail view, with a circular shadow and clip to suit the round photo.
    

    def _shadow_shape(self, pad, oy, art, r):
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad + oy, art, art))
        return path

    def _art_clip(self, pad, art, r):
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad, art, art))
        return path

class ArtistDetailBridge(QObject): # Bridge for the artist detail header QML (artist_detail.qml): forwards button clicks (play/like/lastfm/wikipedia/photo zoom) to ArtistRichDetailView, reports content height for dynamic resizing, drives QML tooltips, and pushes theme/typography/color signals to QML.
    # → QML
    accentColorChanged        = pyqtSignal(str)
    hoverColorChanged         = pyqtSignal(str)
    skeletonColorChanged      = pyqtSignal(str)
    cardBgChanged             = pyqtSignal(str)
    cardBorderChanged         = pyqtSignal(str)
    panelBgChanged            = pyqtSignal(str)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    fontFamilyChanged         = pyqtSignal(str)
    artistDataChanged         = pyqtSignal(str, str, bool)  # name, stats, isFav
    photoIdChanged            = pyqtSignal(str)
    bioChanged                = pyqtSignal(str)

    def __init__(self, view):
        super().__init__()
        self._view = view

    @pyqtSlot()
    def playClicked(self):
        self._view.play_current_artist_tracks()

    @pyqtSlot()
    def likeClicked(self):
        self._view._toggle_artist_like()

    @pyqtSlot()
    def lastfmClicked(self):
        self._view._open_artist_url('lastfm')

    @pyqtSlot()
    def wikipediaClicked(self):
        self._view._open_artist_url('wikipedia')

    @pyqtSlot()
    def photoClicked(self):
        self._view._show_photo_zoom()

    @pyqtSlot(float)
    def reportHeight(self, h: float):
        self._view._on_qml_height_changed(h)

    @pyqtSlot(str, int, int, int)
    def showTooltip(self, text: str, cx: int, above_y: int, below_y: int):
        from PyQt6.QtWidgets import QApplication
        t = getattr(self, '_tip_hide_timer', None)
        if t and t.isActive():
            t.stop()
        for w in QApplication.topLevelWidgets():
            tf = getattr(w, '_tooltip_filter', None)
            if tf:
                tf._qml_mode = True
                tf._ensure_tip().show_at(cx, above_y, below_y, text)
                break

    @pyqtSlot()
    def hideTooltip(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        def _do_hide():
            for w in QApplication.topLevelWidgets():
                tf = getattr(w, '_tooltip_filter', None)
                if tf:
                    tf._qml_mode = False
                    if tf._tip and tf._tip.isVisible():
                        tf._tip.hide()
                    break
        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(_do_hide)
        t.start(120)
        self._tip_hide_timer = t

class ArtistRichDetailView(QWidget): # The single-artist detail page: a QML header (photo, stats, bio, action buttons) for smooth resizing, the popular tracks list, album sections (Albums/Singles/Appears On as QML grids), and the related artists strip.
    album_clicked = pyqtSignal(dict)
    play_album = pyqtSignal(dict)
    play_multiple_tracks = pyqtSignal(list)
    play_track = pyqtSignal(dict)
    play_artist = pyqtSignal()
    artist_clicked = pyqtSignal(dict)
    artist_favorite_toggled = pyqtSignal(bool)
    
    def __init__(self):
        super().__init__()

        self.current_accent = "#888888"
        self._decode_worker = CoverDecodeWorker(self)
        self._decode_worker.decoded.connect(self._on_cover_decoded)
        self._decode_worker.start() 
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 0; }")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_widget.setObjectName('_ac')
        self.content_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.content_widget.setStyleSheet('#_ac { background: transparent; }')
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 50)
        self.content_layout.setSpacing(10)

        # HEADER + ABOUT — full QML (artist_detail.qml)
        self._artist_liked = False
        self._artist_stats_text = "Loading..."
        self._cur_photo_pixmap = None
        self.current_header_cover_id = None

        self._photo_provider = ArtistDetailCoverProvider()
        self._artist_icon_provider = AlbumIconProvider()
        self._artist_bridge = ArtistDetailBridge(self)

        self._qml = QQuickWidget()
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeViewToRootObject)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # QQuickWidget defaults to a white clear color, and the QML root Rectangle's
        # height can briefly lag behind the widget's own height (set via reportHeight)
        # while content is loading. Match the panel background so any gap reads as
        # background instead of a white flash.
        self._set_header_qml_color(QColor(14, 14, 14))

        engine = self._qml.engine()
        engine.addImageProvider("artistdetailcover", self._photo_provider)
        engine.addImageProvider("albumicons",         self._artist_icon_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("artistBridge", self._artist_bridge)

        self._qml.setSource(QUrl.fromLocalFile(resource_path("artist_detail.qml")))

        _header_wrapper = QWidget()
        _header_wrapper.setObjectName('_hw')
        _header_wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _header_wrapper.setStyleSheet('#_hw { background: transparent; }')
        _hw_lo = QVBoxLayout(_header_wrapper)
        _hw_lo.setContentsMargins(0, 0, 0, 0)
        _hw_lo.setSpacing(0)
        _hw_lo.addWidget(self._qml)

        self.header = _header_wrapper
        self.content_layout.addWidget(self.header)

        # POPULAR TRACKS
        self.lbl_top_tracks = QLabel("Popular")
        self.lbl_top_tracks.setStyleSheet("color: white; font-weight: bold; font-size: 20px; margin-left: 12px; margin-top: 10px;")
        self.lbl_top_tracks.hide()
        self.content_layout.addWidget(self.lbl_top_tracks)

        self.song_list = SongListWidget()
        self.song_list.play_track.connect(self.play_track.emit)
        self.song_list.album_clicked.connect(self._on_popular_album_clicked)
        self.song_list.installEventFilter(self)
        self.song_list.viewport().installEventFilter(self)
        self.song_list.hide()
        self.content_layout.addWidget(self.song_list)

        self.sections_container = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_container)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(0)

        self.content_layout.addWidget(self.sections_container)

        # RELATED ARTISTS ROW — populated from getArtistInfo2 similarArtist list
        self.related_artists_row = None  # created lazily in set_related_artists

        self.content_layout.addStretch()
        
        self.scroll.setWidget(self.content_widget)
        self.layout.addWidget(self.scroll)
        self._smooth_scroller = SmoothScroller(self.scroll)


        # For the artist view, song_list has FocusPolicy.NoFocus so no child
        # widget steals keyboard events. We can simply override keyPressEvent
        # on this widget itself — but we also need it to be able to receive focus.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _on_popular_album_clicked(self, data):
        
        alb_data = {
            'id': data.get('albumId'), 
            'title': data.get('album'), 
            'artist': data.get('albumArtist') or data.get('artist'),
            'coverArt': data.get('coverArt') or data.get('albumId'),
            'cover_id': data.get('coverArt') or data.get('albumId')
        }
        if alb_data['id']:
            self.album_clicked.emit(alb_data)  
 
    def eventFilter(self, source, event):
        # --- RELATED ARTISTS: handle click directly on viewport ---
        related = getattr(self, 'related_artists_row', None)
        if related and source is related.list_widget.viewport():
            if event.type() == QEvent.Type.MouseButtonRelease:
                item = related.list_widget.itemAt(event.position().toPoint())
                if item:
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if data:
                        artist_data = {'id': data.get('id'), 'name': data.get('name', '')}
                        # Detect play zone (center circle) vs rest of card
                        visual_rect = related.list_widget.visualItemRect(item)
                        cell_w = RelatedArtistRowWidget.CELL_W
                        img_rect = QRect(visual_rect.x() + 10, visual_rect.y() + 10, cell_w - 20, cell_w - 20)
                        center = img_rect.center()
                        play_size = min(60, img_rect.width() // 2)
                        click_pos = event.position().toPoint()
                        dist = ((click_pos.x() - center.x())**2 + (click_pos.y() - center.y())**2) ** 0.5
                        in_play_zone = dist <= play_size / 2

                        if in_play_zone:
                            def _play(d=artist_data):
                                client = getattr(self, 'client', None)
                                if not client: return
                                w = ArtistPlayWorker(client, d.get('name', ''))
                                w.tracks_ready.connect(self.play_multiple_tracks.emit)
                                w.start()
                                self._related_play_worker = w
                            QTimer.singleShot(0, _play)
                        else:
                            def _navigate(d=artist_data):
                                browser = getattr(self, '_browser', None)
                                if browser:
                                    browser.show_artist_details(d)
                                else:
                                    self.artist_clicked.emit(d)
                            QTimer.singleShot(0, _navigate)
            return False

        if event.type() == QEvent.Type.KeyPress:
            # --- 1. HANDLE POPULAR TRACKS MOVEMENT ---
            if source in (self.song_list, self.song_list.viewport()): # Catch BOTH!
                key = event.key()
                tree = self.song_list # Always command the main widget
                
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    current_item = tree.currentItem()
                    current_idx = tree.indexOfTopLevelItem(current_item) if current_item else -1
                    
                    if key == Qt.Key.Key_Down:
                        if current_idx < tree.topLevelItemCount() - 1:
                            # Move down and force the visual highlight
                            next_idx = 0 if current_idx == -1 else current_idx + 1
                            next_item = tree.topLevelItem(next_idx)
                            tree.setCurrentItem(next_item)
                            next_item.setSelected(True) 
                            
                            # THE CAMERA FIX: Smoothly scroll down to keep the track visible!
                            rect = tree.visualItemRect(next_item)
                            pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                            
                            return True
                        else:
                            # Jump seamlessly to the first album grid!
                            for i in range(self.sections_layout.count()):
                                row = self.sections_layout.itemAt(i).widget()
                                if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                                    tree.clearSelection()
                                    tree.setCurrentItem(None)
                                    row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                    row.list_widget.setCurrentRow(0)
                                    
                                    rect = row.list_widget.visualItemRect(row.list_widget.item(0))
                                    pt = row.list_widget.viewport().mapTo(self.content_widget, rect.topLeft())
                                    self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                                    return True
                                    
                    elif key == Qt.Key.Key_Up:
                        if current_idx > 0:
                            # Move up and force the visual highlight
                            prev_item = tree.topLevelItem(current_idx - 1)
                            tree.setCurrentItem(prev_item)
                            prev_item.setSelected(True) 
                            
                            # Smoothly scroll up to keep the track visible!
                            rect = tree.visualItemRect(prev_item)
                            pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                            
                            return True
                        else:
                            self.scroll.verticalScrollBar().setValue(0)
                            return True
                            
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.play_current_artist_tracks()
                        return True

                    curr_item = tree.currentItem()
                    if curr_item:
                        data = curr_item.data(0, Qt.ItemDataRole.UserRole)
                        if data: self.play_track.emit(data)
                    return True

                return super().eventFilter(source, event)
            
        
            
            # --- 2. HANDLE ALBUM GRIDS (QListWidget) MOVEMENT ---
            if isinstance(source, QListWidget):
                key = event.key()
                
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                    old_row = source.currentRow()
                    
                    def check_jump_and_scroll():
                        new_row = source.currentRow()
                        
                        def do_scroll(target_grid, target_idx):
                            item = target_grid.item(target_idx)
                            if item:
                                rect = target_grid.visualItemRect(item)
                                pt = target_grid.viewport().mapTo(self.content_widget, rect.topLeft())
                                center_y = pt.y() + rect.height() // 2
                                y_margin = rect.height() // 2 + 40
                                self.scroll.ensureVisible(pt.x(), center_y, 0, y_margin)

                        if old_row == new_row:
                            lists = []
                            for i in range(self.sections_layout.count()):
                                r = self.sections_layout.itemAt(i).widget()
                                if r and hasattr(r, 'list_widget') and r.list_widget.count() > 0:
                                    lists.append(r.list_widget)
                            related = getattr(self, 'related_artists_row', None)
                            if related and related.list_widget.count() > 0:
                                lists.append(related.list_widget)
                                    
                            if source in lists:
                                current_idx = lists.index(source)
                                next_idx = current_idx + (1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Right) else -1)
                                
                                if 0 <= next_idx < len(lists):
                                    source.clearSelection() 
                                    next_grid = lists[next_idx]
                                    next_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                    
                                    target_row = 0
                                    if key == Qt.Key.Key_Right: target_row = 0
                                    elif key == Qt.Key.Key_Left: target_row = next_grid.count() - 1
                                    elif key == Qt.Key.Key_Down:
                                        old_x = source.visualItemRect(source.item(old_row)).x()
                                        best_dist = float('inf')
                                        for i in range(min(15, next_grid.count())): 
                                            dist = abs(next_grid.visualItemRect(next_grid.item(i)).x() - old_x)
                                            if dist < best_dist: best_dist = dist; target_row = i
                                    elif key == Qt.Key.Key_Up:
                                        old_x = source.visualItemRect(source.item(old_row)).x()
                                        last_idx = next_grid.count() - 1
                                        last_y = next_grid.visualItemRect(next_grid.item(last_idx)).y()
                                        best_dist = float('inf'); target_row = last_idx
                                        for i in range(last_idx, -1, -1):
                                            rect = next_grid.visualItemRect(next_grid.item(i))
                                            if rect.y() < last_y: break 
                                            dist = abs(rect.x() - old_x)
                                            if dist < best_dist: best_dist = dist; target_row = i
                                                
                                    next_grid.setCurrentRow(target_row)
                                    do_scroll(next_grid, target_row)
                                    return
                                    
                                # --- THE BRIDGE: Jumping UP from the very first Album Grid! ---
                                elif next_idx < 0 and key == Qt.Key.Key_Up:
                                    # If Popular Tracks exist, seamlessly jump up into them!
                                    if not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
                                        source.clearSelection()
                                        self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                        # Highlight the very last popular track
                                        last_item = self.song_list.topLevelItem(self.song_list.topLevelItemCount() - 1)
                                        self.song_list.setCurrentItem(last_item)
                                        last_item.setSelected(True)
                                        
                                        rect = self.song_list.visualItemRect(last_item)
                                        pt = self.song_list.viewport().mapTo(self.content_widget, rect.topLeft())
                                        self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                                    else:
                                        self.scroll.verticalScrollBar().setValue(0)
                                    return
                                    
                                elif next_idx >= len(lists) and key == Qt.Key.Key_Down:
                                    self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
                                    return
                        
                        do_scroll(source, new_row)

                    QTimer.singleShot(0, check_jump_and_scroll)
                    return False 
                    
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.isAutoRepeat(): return True
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.play_current_artist_tracks(); return True
                    curr_item = source.currentItem()
                    if curr_item:
                        data = curr_item.data(Qt.ItemDataRole.UserRole)
                        if data:
                            related = getattr(self, 'related_artists_row', None)
                            if related and source is related.list_widget:
                                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                                    w = ArtistPlayWorker(self.client, data.get('name', ''))
                                    w.tracks_ready.connect(self.play_multiple_tracks.emit)
                                    w.start()
                                    self._related_play_worker = w
                                else:
                                    self.artist_clicked.emit({'id': data.get('id'), 'name': data.get('name', '')})
                            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                                self.play_album.emit(data)
                            else:
                                self.album_clicked.emit(data)
                    return True

            # --- 3. HANDLE QML ALBUM SECTION GRIDS ---
            section = None
            for s in self._get_qml_sections():
                if source is s.qml_widget:
                    section = s
                    break
            if section is not None:
                key = event.key()
                ipr = section.items_per_row()
                count = section.album_model.rowCount()
                idx = section.current_index
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                    col = idx % ipr
                    cur_row = idx // ipr
                    last_row = (count - 1) // ipr

                    if key == Qt.Key.Key_Right:
                        if idx < count - 1:
                            section.select(idx + 1)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        return True
                    elif key == Qt.Key.Key_Left:
                        if idx > 0:
                            section.select(idx - 1)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        return True
                    elif key == Qt.Key.Key_Down:
                        if cur_row < last_row:
                            section.select(min(idx + ipr, count - 1))
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        else:
                            self._qml_jump_next(section, col)
                        return True
                    elif key == Qt.Key.Key_Up:
                        if cur_row > 0:
                            section.select(idx - ipr)
                            self._qml_scroll_to_cell(section, section.current_index // ipr)
                        else:
                            self._qml_jump_prev(section, col)
                        return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.isAutoRepeat(): return True
                    if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                        self.play_current_artist_tracks(); return True
                    if 0 <= idx < count:
                        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            self.play_album.emit(section.album_model.albums[idx])
                        else:
                            self.album_clicked.emit(section.album_model.albums[idx])
                    return True
                if key == Qt.Key.Key_Space:
                    if event.isAutoRepeat(): return True
                    if 0 <= idx < count:
                        self.play_album.emit(section.album_model.albums[idx])
                    return True

        return super().eventFilter(source, event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)):
            self.play_current_artist_tracks()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'set_bg_color'):
                row.set_bg_color(c)

    def set_accent_color(self, color):

        self.current_accent = color

        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")
        
        scrollbar_style = f"""
            QScrollArea {{ border: none; background: transparent; }}
            {scrollbar_css(color)}
        """
        self.scroll.setStyleSheet(scrollbar_style)

        theme = getattr(self.window(), 'theme', None)
        self._artist_bridge.accentColorChanged.emit(color)
        self._artist_bridge.hoverColorChanged.emit(resolve_menu_hover(theme) if theme else '#555555')
        if theme:
            self._artist_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
            self._artist_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
            self._artist_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
            self._artist_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
            self._artist_bridge.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))
            self._artist_bridge.skeletonColorChanged.emit(
                getattr(theme, 'skeleton_base', '#282828'))
            self._artist_bridge.cardBgChanged.emit(
                getattr(theme, 'now_playing_card_bg', '#1e1e1e'))
            border = getattr(theme, 'border_color', '#2a2a2a')
            if not getattr(theme, 'auto_border_from_accent', True):
                border = getattr(theme, 'manual_border_color', '#2a2a2a')
            self._artist_bridge.cardBorderChanged.emit(border)
            raw_bg = getattr(theme, 'main_panel_bg', '14,14,14')
            try:
                r, g, b = (int(x) for x in raw_bg.split(','))
                panel_hex = '#{:02x}{:02x}{:02x}'.format(r, g, b)
            except Exception:
                r, g, b = 14, 14, 14
                panel_hex = '#0e0e0e'
            self._artist_bridge.panelBgChanged.emit(panel_hex)
            self._set_header_qml_color(QColor(r, g, b))

        if not hasattr(self, '_scroll_reveal'):
            self._scroll_reveal = install_scroll_reveal(self.scroll.viewport(), self.scroll.verticalScrollBar())
        self._scroll_reveal.color = color

        pri_color = getattr(theme, 'font_color_primary', '#dddddd') if theme else '#dddddd'
        if hasattr(self, 'lbl_top_tracks'):
            self.lbl_top_tracks.setStyleSheet(f"color: {pri_color}; font-weight: bold; font-size: 20px; margin-left: 12px; margin-top: 10px;")

        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'set_accent_color'):
                row.set_accent_color(color)
        self.song_list.update_style(color)

    def set_header_image(self, pixmap):
        from PyQt6.QtCore import QBuffer, QByteArray
        import time as _time

        if pixmap and not pixmap.isNull():
            self._cur_photo_pixmap = pixmap
            cov_id = self.current_header_cover_id or "artist"
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            pixmap.save(buf, "PNG")
            buf.close()
            self._photo_provider.cache[cov_id] = bytes(ba)
            self._artist_bridge.photoIdChanged.emit(f"{cov_id}?t={_time.time()}")
        else:
            self._cur_photo_pixmap = None
            self._artist_bridge.photoIdChanged.emit("")

    def set_bio(self, text):
        if text:
            import re as _re
            clean = _re.sub(r'<[^>]+>', '', text).strip()
            clean = _re.sub(r'\s*Read more on Last\.fm\.?\s*$', '', clean, flags=_re.IGNORECASE).strip()
            if clean:
                self._artist_bridge.bioChanged.emit(clean)
                return
        self._artist_bridge.bioChanged.emit("")

    def _show_photo_zoom(self):
        pix = getattr(self, '_cur_photo_pixmap', None)
        if pix and not pix.isNull():
            _ArtistPhotoOverlay(pix, self.window())

    def _on_qml_height_changed(self, h):
        self._qml.setFixedHeight(max(1, round(h)))

    def _set_header_qml_color(self, color: QColor):
        self._qml.setClearColor(color)
        self._qml.setAutoFillBackground(True)
        pal = self._qml.palette()
        pal.setColor(QPalette.ColorRole.Window, color)
        self._qml.setPalette(pal)

    def set_related_artists(self, similar_artists):
        # Remove old row if present
        if self.related_artists_row is not None:
            self.content_layout.removeWidget(self.related_artists_row)
            self.related_artists_row.deleteLater()
            self.related_artists_row = None

        if not similar_artists:
            return

        # Cap at 10
        similar_artists = similar_artists[:10]

        # Normalise: ensure each dict has 'title' for the delegate's name display
        normalised = []
        for a in similar_artists:
            normalised.append({
                'id': a.get('id'),
                'name': a.get('name', ''),
                'title': a.get('name', ''),
                'coverArt': a.get('coverArt') or a.get('id'),
                '_is_artist': True,
            })

        row = RelatedArtistRowWidget("Related Artists", normalised)
        row.set_accent_color(self.current_accent)
        row.list_widget.installEventFilter(self)
        row.list_widget.viewport().installEventFilter(self)

        # Register list items in pending_items so apply_cover can find them,
        # then queue the cover fetch
        worker = getattr(self, 'cover_worker', None)
        for i, a in enumerate(normalised):
            cover_id = a.get('coverArt')
            if not cover_id:
                continue
            item = row.list_widget.item(i)
            if item:
                self.pending_items.setdefault(cover_id, []).append(item)
            if worker:
                worker.queue_cover(cover_id)

        # Insert before the stretch (second-to-last item)
        insert_pos = self.content_layout.count() - 1
        self.content_layout.insertWidget(insert_pos, row)
        self.related_artists_row = row

    def set_top_songs(self, songs):
        if songs:
            from PyQt6.QtWidgets import QApplication
            current_focus = QApplication.focusWidget()
            
            was_idle = False
            first_album_grid = None
            for i in range(self.sections_layout.count()):
                row = self.sections_layout.itemAt(i).widget()
                if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                    first_album_grid = row.list_widget
                    break
            
            
            if first_album_grid and current_focus == first_album_grid and first_album_grid.currentRow() <= 0:
                was_idle = True
            elif current_focus in (self, self.scroll, self.scroll.widget(), None) or (current_focus and current_focus.parent() == self.header):
                was_idle = True
            
            self.lbl_top_tracks.show()
            self.song_list.show()
            
            worker = getattr(self, 'cover_worker', None)
            self.song_list.populate(songs, worker, getattr(self, 'pending_items', None))
            
            
            if was_idle:
                if first_album_grid:
                    first_album_grid.clearSelection()
                    first_album_grid.setCurrentRow(-1) # Kill the blue box on the album
                
                self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
                first_item = self.song_list.topLevelItem(0)
                if first_item:
                    self.song_list.setCurrentItem(first_item)
                    first_item.setSelected(True)
                self.scroll.verticalScrollBar().setValue(0) 
            elif current_focus: 
                current_focus.setFocus()
        else:
            self.lbl_top_tracks.hide()
            self.song_list.hide()

    def clear_sections(self):
        while self.sections_layout.count():
            child = self.sections_layout.takeAt(0)
            w = child.widget()
            if w:
                if hasattr(w, '_timer'):
                    w._timer.stop()
                w.deleteLater()

    # Sentinel title used to identify the loading-skeleton section
    _SKELETON_SECTION_TITLE = "_skeleton"

    def _show_section_skeleton(self, count=10):
        """Instant visual feedback — animated skeleton cards (matches albums grid)."""
        placeholders = [{'type': 'placeholder', 'title': '', 'cover_id': ''} for _ in range(count)]
        row = QMLAlbumSectionWidget("", 0, placeholders)
        row._section_title = self._SKELETON_SECTION_TITLE
        if hasattr(self, '_bg_color'):
            row.set_bg_color(self._bg_color)
        row.set_accent_color(self.current_accent)
        row.qml_widget.installEventFilter(self)
        self.sections_layout.addWidget(row)

    # Max albums per QML widget — keeps each texture well under GPU limits
    _QML_CHUNK = 80

    def add_section(self, title, albums, cover_worker, pending_items):
        if not albums: return

        sorted_albums = sorted(albums, key=lambda x: (int(x.get('playCount', 0)), str(x.get('year', '0000'))), reverse=True)

        # Split into chunks to avoid exceeding GPU texture size limits
        chunk_size = self._QML_CHUNK
        chunks = [sorted_albums[i:i + chunk_size] for i in range(0, len(sorted_albums), chunk_size)]

        for chunk_idx, chunk in enumerate(chunks):
            # Only first chunk gets the section title + total count badge
            chunk_title = title if chunk_idx == 0 else ""
            chunk_count = len(sorted_albums) if chunk_idx == 0 else 0

            row = QMLAlbumSectionWidget(chunk_title, chunk_count, chunk)
            row._section_title = title  # used by _remove_section to identify this block
            if hasattr(self, '_bg_color'):
                row.set_bg_color(self._bg_color)
            row.set_accent_color(self.current_accent)
            row.album_clicked.connect(self.album_clicked.emit)
            row.play_album.connect(self.play_album.emit)
            row.artist_name_clicked.connect(lambda name, aid: self.artist_clicked.emit({'name': name, 'id': aid or None}))
            row.qml_widget.installEventFilter(self)
            self.sections_layout.addWidget(row)

            # Queue covers and track which section owns them
            for album in row.album_model.albums:
                cid = album.get('cover_id') or ''
                if cid:
                    self.pending_qml_sections.setdefault(cid, []).append(row)
                    if cover_worker:
                        cover_worker.queue_cover(cid)

        if self.sections_layout.count() <= len(chunks):
            QTimer.singleShot(100, self.auto_focus)
        
    def _get_qml_sections(self):
        sections = []
        for i in range(self.sections_layout.count()):
            w = self.sections_layout.itemAt(i).widget()
            if isinstance(w, QMLAlbumSectionWidget):
                sections.append(w)
        return sections

    def _qml_scroll_to_cell(self, section, row_index):
        def _do():
            ipr = section.items_per_row()
            avail = max(1, section.qml_widget.width() - 40)
            cell_h = int(avail / ipr + 70)
            cell_y = 10 + row_index * cell_h
            pt = section.qml_widget.mapTo(self.content_widget, QPoint(0, cell_y))
            self.scroll.ensureVisible(pt.x(), pt.y() + cell_h // 2, 0, cell_h // 2 + 20)
        QTimer.singleShot(0, _do)

    def _qml_jump_next(self, from_section, col):
        sections = self._get_qml_sections()
        if from_section not in sections:
            return
        idx = sections.index(from_section)
        if idx + 1 < len(sections):
            nxt = sections[idx + 1]
            target = min(col, nxt.album_model.rowCount() - 1)
            nxt.select(target)
            nxt.qml_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            self._qml_scroll_to_cell(nxt, 0)
        else:
            related = getattr(self, 'related_artists_row', None)
            if related and related.list_widget.count() > 0:
                related.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                related.list_widget.setCurrentRow(min(col, related.list_widget.count() - 1))
            else:
                self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _qml_jump_prev(self, from_section, col):
        sections = self._get_qml_sections()
        if from_section not in sections:
            return
        idx = sections.index(from_section)
        if idx > 0:
            prev = sections[idx - 1]
            count = prev.album_model.rowCount()
            ipr = prev.items_per_row()
            last_row = (count - 1) // ipr
            target = min(last_row * ipr + col, count - 1)
            prev.select(target)
            prev.qml_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            self._qml_scroll_to_cell(prev, last_row)
        elif not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
            self.song_list.setFocus(Qt.FocusReason.OtherFocusReason)
            last_item = self.song_list.topLevelItem(self.song_list.topLevelItemCount() - 1)
            self.song_list.setCurrentItem(last_item)
            last_item.setSelected(True)
            rect = self.song_list.visualItemRect(last_item)
            pt = self.song_list.viewport().mapTo(self.content_widget, rect.topLeft())
            self.scroll.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
        else:
            self.scroll.verticalScrollBar().setValue(0)

    def auto_focus(self):
           
        
        if not self.song_list.isHidden() and self.song_list.topLevelItemCount() > 0:
            self.song_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
            first_item = self.song_list.topLevelItem(0)
            if first_item:
                self.song_list.setCurrentItem(first_item)
                first_item.setSelected(True)
            self.scroll.verticalScrollBar().setValue(0)
            return
            
        # Otherwise, fall back to focusing the first album grid
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                row.list_widget.setCurrentRow(0)
                # Ensure the scrollbar resets to the absolute top
                self.scroll.verticalScrollBar().setValue(0)
                break
       
    def _safe_discard_worker(self, worker):
        if not worker: return
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
        self._worker_graveyard.add(worker)
        try:
            worker.finished.connect(lambda: self._worker_graveyard.discard(worker) if worker in self._worker_graveyard else None)
        except: pass

    def stop_all_workers(self):
        """Terminate all running workers immediately — called on app shutdown."""
        workers = set(getattr(self, '_worker_graveyard', set()))
        for attr in ('live_detail_worker', '_decode_worker', 'cover_worker'):
            w = getattr(self, attr, None)
            if w: workers.add(w)
        for worker in workers:
            if worker.isRunning():
                if hasattr(worker, 'stop'):
                    worker.stop()  # break internal time.sleep / threading.Event loop
                worker.quit()
                if not worker.wait(400):
                    worker.terminate()
        if hasattr(self, '_worker_graveyard'):
            self._worker_graveyard.clear()

    def load_artist(self, artist_data):
        # Always start a freshly-loaded artist page from the top, regardless
        # of where the previous artist's view was scrolled to. If a smooth-scroll
        # animation is mid-flight, stop it and reset its target too -- otherwise
        # it keeps animating back toward the old (pre-reset) position.
        smoother = getattr(self, '_smooth_scroller', None)
        if smoother:
            smoother._timer.stop()
            smoother._target = 0.0
        self.scroll.verticalScrollBar().setValue(0)

        self.pending_items = {}
        self.pending_qml_sections = {}  # cover_id -> [QMLAlbumSectionWidget]
        self.current_artist_name = artist_data.get('name', 'Unknown')
        self.current_artist_id = artist_data.get('id')
        self._artist_liked = bool(artist_data.get('starred'))

        # 1. INSTANT VISUALS
        self._set_stats("Loading...")
        self.set_bio("")
        self.set_top_songs([])
        self.set_related_artists([])
        self.clear_sections()
        self._show_section_skeleton()

        if not getattr(self, '_header_already_loaded', False):
            self.set_header_image(None)
        self._header_already_loaded = False  
        self._exact_artist_image = False     

        if not hasattr(self, 'cover_worker') and getattr(self, 'client', None):
            from player.workers import GridCoverWorker
            self.cover_worker = GridCoverWorker(self.client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()

        
        if getattr(self, 'live_detail_worker', None) and self.live_detail_worker.isRunning():
            for sig in ('albums_ready', 'top_songs_ready', 'appears_ready'):
                try: getattr(self.live_detail_worker, sig).disconnect()
                except: pass
            self._safe_discard_worker(self.live_detail_worker)

        self._loaded_appears_on  = []
        self._loaded_album_counts = (-1, -1)

        self.live_detail_worker = LiveArtistDetailWorker(
            self.client,
            self.current_artist_id,
            self.current_artist_name
        )
        self.live_detail_worker.albums_ready.connect(self._on_albums_ready)
        self.live_detail_worker.top_songs_ready.connect(self._on_top_songs_ready)
        self.live_detail_worker.appears_ready.connect(self._on_appears_ready)
        self.live_detail_worker.start()

    def _on_albums_ready(self, info, main_albums, singles):
        """Phase 2 handler — fires as soon as get_artist returns. Shows albums immediately."""
        if info:
            self._artist_liked = bool(info.get('starred'))
            self._update_like_btn()
        # Cover image
        if info:
            # Prefer high-res Last.fm/MusicBrainz URL from getArtistInfo2 over getCoverArt thumb
            artist_img_url = info.get('artistImageUrl', '')
            if artist_img_url:
                self._exact_artist_image = True
                from artist_info_panel import _ImageWorker
                prev = getattr(self, '_header_img_worker', None)
                if prev and prev.isRunning():
                    try: prev.done.disconnect()
                    except: pass
                self._header_img_worker = _ImageWorker(artist_img_url)
                self._header_img_worker.done.connect(self.set_header_image)
                self._header_img_worker.start()

            cover_src = info.get('coverArt') or info.get('id')
            if cover_src:
                self.current_header_cover_id = str(cover_src)
                if not getattr(self, '_exact_artist_image', False) and hasattr(self, 'cover_worker'):
                    self.cover_worker.queue_cover(cover_src, priority=True)
                self._exact_artist_image = False

            # Bio may arrive here on first emit (if already cached) or on second emit after Last.fm
            if 'biography' in info:
                self.set_bio(info['biography'])

            # Related artists — may be present on second emit after Last.fm
            self.set_related_artists(info.get('similar_artists', []))

        total_releases = len(main_albums) + len(singles)
        appears_count  = len(getattr(self, '_loaded_appears_on', []))

        if total_releases == 0 and appears_count == 0:
            self._set_stats("Loading...")
        elif total_releases == 0:
            self._set_stats(f"Guest Artist • {appears_count} appearances")
        else:
            suffix = f" • {appears_count} appearances" if appears_count else ""
            self._set_stats(f"{total_releases} releases{suffix}")

        # Rebuild album sections (clear old ones first to avoid duplicates on second emit)
        # Only rebuild if the counts actually changed to avoid visual flicker
        prev_counts = getattr(self, '_loaded_album_counts', (-1, -1))
        new_counts  = (len(main_albums), len(singles))
        if new_counts != prev_counts:
            self._loaded_album_counts = new_counts
            # Remove and re-add only the Albums / Singles sections
            self._remove_section("Albums")
            self._remove_section("Singles & EPs")
            worker = getattr(self, 'cover_worker', None)
            if main_albums: self.add_section("Albums",      main_albums, worker, self.pending_items)
            if singles:     self.add_section("Singles & EPs", singles,   worker, self.pending_items)

        # Drop the loading skeleton once we have releases to show — guest artists
        # (no albums/singles) keep it until _on_appears_ready resolves
        if total_releases > 0:
            self._remove_section(self._SKELETON_SECTION_TITLE)

        self._try_set_focus()

    def _on_top_songs_ready(self, top_songs):
        """Phase 3 handler — fires after get_top_songs returns."""
        self.set_top_songs(top_songs)

    def _on_appears_ready(self, appears_on):
        """Phase 5 handler — fires after search_artist_tracks returns (slowest call)."""
        self._loaded_appears_on = appears_on
        worker = getattr(self, 'cover_worker', None)
        self._remove_section("Appears on & Compilations")
        self._remove_section(self._SKELETON_SECTION_TITLE)
        if appears_on:
            self.add_section("Appears on & Compilations", appears_on, worker, self.pending_items)

        # Update stats label now that we have the final appearance count
        total_releases = sum(getattr(self, '_loaded_album_counts', (0, 0)))
        appears_count  = len(appears_on)
        if total_releases == 0 and appears_count == 0:
            self._set_stats("No releases found")
        elif total_releases == 0:
            self._set_stats(f"Guest Artist • {appears_count} appearances")
        else:
            suffix = f" • {appears_count} appearances" if appears_count else ""
            self._set_stats(f"{total_releases} releases{suffix}")

    def _remove_section(self, title):
        """Remove all section widgets with the given title from sections_layout."""
        for i in range(self.sections_layout.count() - 1, -1, -1):
            item = self.sections_layout.itemAt(i)
            w = item.widget() if item else None
            if w and getattr(w, '_section_title', None) == title:
                self.sections_layout.removeWidget(w)
                if hasattr(w, '_timer'):
                    w._timer.stop()
                w.deleteLater()

    def _try_set_focus(self):
        """Focus first item in first section if no focus is already set."""
        for i in range(self.sections_layout.count()):
            row = self.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                row.list_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
                row.list_widget.setCurrentRow(0)
                break

    def _toggle_artist_like(self):
        self._artist_liked = not self._artist_liked
        self._update_like_btn()
        client = getattr(self, 'client', None)
        artist_id = getattr(self, 'current_artist_id', None)
        _liked = self._artist_liked
        if client and artist_id:
            import threading
            threading.Thread(
                target=lambda: client.set_favorite(artist_id, _liked, id_param='artistId'),
                daemon=True,
            ).start()
        self.artist_favorite_toggled.emit(self._artist_liked)

    def _update_like_btn(self):
        self._artist_bridge.artistDataChanged.emit(
            self.current_artist_name, self._artist_stats_text, self._artist_liked)

    def _set_stats(self, text):
        self._artist_stats_text = text
        self._artist_bridge.artistDataChanged.emit(
            self.current_artist_name, text, self._artist_liked)

    def _open_artist_url(self, service: str):
        import urllib.parse
        from PyQt6.QtGui import QDesktopServices
        name = getattr(self, 'current_artist_name', '') or ''
        if not name or name in ('Loading...', 'Artist Name'):
            return
        if service == 'lastfm':
            url = f'https://www.last.fm/music/{urllib.parse.quote_plus(name)}'
        else:
            url = f'https://en.wikipedia.org/wiki/{urllib.parse.quote(name.replace(" ", "_"), safe="_")}'
        QDesktopServices.openUrl(QUrl(url))

    def play_current_artist_tracks(self):
        """Fetches all artist tracks and emits them for playback — no parent relay needed."""
        client = getattr(self, 'client', None)
        name   = getattr(self, 'current_artist_name', None)
        if not client or not name or name in ('Loading...', 'Artist Name'):
            return
        self._play_artist_worker = ArtistPlayWorker(client, name)
        self._play_artist_worker.tracks_ready.connect(self.play_multiple_tracks.emit)
        self._play_artist_worker.start()
   
    def apply_cover(self, cover_id, image_data):
        is_header  = getattr(self, 'current_header_cover_id', None) == str(cover_id)
        has_items  = cover_id in getattr(self, 'pending_items', {})
        has_qml    = cover_id in getattr(self, 'pending_qml_sections', {})

        if not (is_header or has_items or has_qml):
            return

        # QML sections only need raw bytes — store sync (instant)
        if has_qml:
            for section in self.pending_qml_sections[cover_id]:
                try:
                    section.apply_cover(cover_id, image_data)
                except Exception:
                    pass
            del self.pending_qml_sections[cover_id]

        # Pixmap work (decode + scale) goes to background thread
        if is_header or has_items:
            self._decode_worker.enqueue(cover_id, image_data, side=400)

    def _on_cover_decoded(self, cover_id: str, img):
        from PyQt6.QtGui import QPixmap, QIcon
        pix  = QPixmap.fromImage(img)
        icon = QIcon(pix)

        if cover_id in getattr(self, 'pending_items', {}):
            from PyQt6.QtWidgets import QTreeWidgetItem
            for item in self.pending_items[cover_id]:
                try:
                    if isinstance(item, QTreeWidgetItem):
                        item.setIcon(1, icon)
                    else:
                        item.setIcon(icon)
                except Exception:
                    pass
            del self.pending_items[cover_id]

        if getattr(self, 'current_header_cover_id', None) == str(cover_id):
            self.set_header_image(pix)
            cid = str(cover_id)
            import threading
            def _fetch_full(cid=cid):
                if not getattr(self, 'client', None):
                    return
                try:
                    from cover_cache import CoverCache
                    data = CoverCache.instance().get_full(cid)
                    if not data:
                        data = self.client.get_cover_art(cid, size=None)
                        if data:
                            CoverCache.instance().save_full(cid, data)
                    if data and getattr(self, 'current_header_cover_id', None) == cid:
                        from PyQt6.QtGui import QImage as _QI, QPixmap as _QP
                        from PyQt6.QtCore import QTimer
                        full_img = _QI()
                        full_img.loadFromData(data)
                        if not full_img.isNull():
                            full_pix = _QP.fromImage(full_img)
                            QTimer.singleShot(0, lambda p=full_pix: self.set_header_image(p))
                except Exception:
                    pass
            threading.Thread(target=_fetch_full, daemon=True).start()

class ArtistModel(QAbstractListModel): # Read-only list model backing the main artist grid's QML GridView, exposing artistName/coverId/albumCount/songCount/isLoading/rawData roles per artist.
    NAME_ROLE        = Qt.ItemDataRole.UserRole + 1
    COVER_ID_ROLE    = Qt.ItemDataRole.UserRole + 2
    RAW_DATA_ROLE    = Qt.ItemDataRole.UserRole + 3
    IS_LOADING_ROLE  = Qt.ItemDataRole.UserRole + 4
    ALBUM_COUNT_ROLE = Qt.ItemDataRole.UserRole + 5
    SONG_COUNT_ROLE  = Qt.ItemDataRole.UserRole + 6

    def __init__(self):
        super().__init__()
        self.artists = []

    def rowCount(self, parent=QModelIndex()):
        return len(self.artists)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.artists[index.row()]
        if role == self.NAME_ROLE:
            if a.get('type') == 'placeholder': return ''
            return a.get('name') or a.get('artist') or 'Unknown'
        if role == self.COVER_ID_ROLE:    return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE:    return a
        if role == self.IS_LOADING_ROLE:  return a.get('type') == 'placeholder'
        if role == self.ALBUM_COUNT_ROLE: return int(a.get('albumCount') or 0)
        if role == self.SONG_COUNT_ROLE:  return int(a.get('songCount') or 0)
        return None

    def roleNames(self):
        return {
            self.NAME_ROLE:        b"artistName",
            self.COVER_ID_ROLE:    b"coverId",
            self.RAW_DATA_ROLE:    b"rawData",
            self.IS_LOADING_ROLE:  b"isLoading",
            self.ALBUM_COUNT_ROLE: b"albumCount",
            self.SONG_COUNT_ROLE:  b"songCount",
        }

    def append_artists(self, new_artists):
        start = len(self.artists)
        self.beginInsertRows(QModelIndex(), start, start + len(new_artists) - 1)
        self.artists.extend(new_artists)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.artists = []
        self.endResetModel()

    def update_cover(self, cover_id):
        import time
        forced_id = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self.artists):
            if a.get('cover_id') == cover_id:
                a['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class ArtistGridBridge(QObject): # Bridge for the main artist grid's QML (artist_grid.qml): item/play clicks resolved via artist_model, visible-range reporting for lazy loading, search controller, sort-menu requests, and theme/typography/color signals to QML.
    itemClicked         = pyqtSignal(dict)
    playClicked         = pyqtSignal(dict)
    visibleRangeChanged = pyqtSignal(int, int)
    accentColorChanged        = pyqtSignal(str)
    bgAlphaChanged            = pyqtSignal(float)
    cancelScroll              = pyqtSignal()
    scrollBy                  = pyqtSignal(float)
    fontSizePrimaryChanged    = pyqtSignal(int)
    fontSizeSecondaryChanged  = pyqtSignal(int)
    fontColorPrimaryChanged   = pyqtSignal(str)
    fontColorSecondaryChanged = pyqtSignal(str)
    skeletonBaseColorChanged  = pyqtSignal(str)
    hoverColorChanged         = pyqtSignal(str)
    panelBgChanged            = pyqtSignal(str)
    cardBorderChanged         = pyqtSignal(str)
    fontFamilyChanged         = pyqtSignal(str)
    statusTextChanged         = pyqtSignal(str)
    burgerIconChanged         = pyqtSignal(str)

    def __init__(self, artist_model, view):
        super().__init__()
        self.artist_model = artist_model
        self._view = view
        self.search = SearchController(
            on_active_changed=lambda active: view._set_window_shortcuts_enabled(not active),
            on_text_changed=view._on_grid_search_text_changed)

    @pyqtProperty(QObject, constant=True)
    def searchCtl(self):
        return self.search

    @pyqtSlot(float, float)
    def showSortMenu(self, gx, gy):
        self._view.show_sort_menu_at(gx, gy)

    @pyqtSlot(int, int)
    def reportVisibleRange(self, start, end):
        self.last_start, self.last_end = start, end
        if not hasattr(self, 'scroll_timer'):
            from PyQt6.QtCore import QTimer
            self._scroll_prev = (-1, -1)
            self.scroll_timer = QTimer()
            self.scroll_timer.setInterval(80)
            self.scroll_timer.timeout.connect(self._on_scroll_tick)
        if not self.scroll_timer.isActive():
            self._scroll_prev = (-1, -1)
            self.scroll_timer.start()

    def _on_scroll_tick(self):
        current = (self.last_start, self.last_end)
        if current == self._scroll_prev:
            self.scroll_timer.stop()
            self.visibleRangeChanged.emit(self.last_start, self.last_end)
        else:
            self._scroll_prev = current

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        if 0 <= idx < len(self.artist_model.artists):
            self.itemClicked.emit(self.artist_model.artists[idx])

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        if 0 <= idx < len(self.artist_model.artists):
            self.playClicked.emit(self.artist_model.artists[idx])

class ArtistGridBrowser(QWidget): # Top-level artist-browsing widget: a QStackedWidget toggling between the searchable/sortable artist grid (artist_grid.qml + ArtistModel) and ArtistRichDetailView for a selected artist's detail page.
    play_track_signal = pyqtSignal(dict) 
    play_album_signal = pyqtSignal(list) 
    queue_track_signal = pyqtSignal(dict)
    play_next_signal = pyqtSignal(dict)
    switch_to_album_tab = pyqtSignal(dict)
    artist_clicked = pyqtSignal(dict)

    def __init__(self, client):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.client = client
        self.last_reload_time = time.time()
        
        self.current_page = 1
        self.total_pages = 1
        self.current_query = ""
       
        self.sort_states = {
            'random': True,
            'most_played': False,
            'alphabetical': True,
            'albums_count': False,
        }
        self.current_sort = 'most_played'
        self.random_seed = time.time()

        self.cover_worker = None
        if self.client:
            self.set_client(client)
        
        self.current_query = ""
        self.current_accent = "#888888"  # Default accent color
        
        # --- PAGINATION SETTINGS ---
        self.page_size = 50
        self.current_page = 1
        self.total_pages = 1
        # Search timer for debounced search
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(400)
        self.search_timer.timeout.connect(self.execute_search) 
        
        self.pending_items = {}
        self.current_header_cover_id = None
        self.current_artist_id = None
        self._active_workers = set()
        
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)

        self.qml_view = QMLGridWrapper()
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_view.setClearColor(self._qml_bg_color())
        self.qml_view.setStyleSheet("border: none;")

        self.artist_model = ArtistModel()
        self.grid_bridge = ArtistGridBridge(self.artist_model, self)

        self.grid_bridge.itemClicked.connect(self.on_grid_artist_clicked)
        self.grid_bridge.playClicked.connect(self.on_grid_play_clicked)
        self.grid_bridge.visibleRangeChanged.connect(self.check_viewport_qml)

        # 🟢 OMNI-SCROLLER FIX: same as albums_browser — QMLGridWrapper's DummyScrollBar
        # is a no-op, so we push pixel deltas via the bridge instead.
        self.omni_scroller_qml = QMLMiddleClickScroller(self.qml_view, self.grid_bridge)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("artistModel", self.artist_model)
        ctx.setContextProperty("artistBridge", self.grid_bridge)

        engine = self.qml_view.engine()
        self.cover_provider = engine.imageProvider("artistcovers")
        if not self.cover_provider:
            self.cover_provider = CoverImageProvider()
            engine.addImageProvider("artistcovers", self.cover_provider)

        self._icon_provider = AlbumIconProvider()
        engine.addImageProvider("albumicons", self._icon_provider)

        self._grid_key_filter = GridSearchKeyFilter(self)
        self.qml_view.installEventFilter(self._grid_key_filter)

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("artist_grid.qml")))
        from PyQt6.QtCore import QTimer as _QTimer
        def _emit_initial_typography():
            theme = getattr(self.window(), 'theme', None)
            if theme and hasattr(self, 'grid_bridge'):
                self.grid_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.grid_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.grid_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.grid_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
        _QTimer.singleShot(0, _emit_initial_typography)

        self.grid_view = self.qml_view  # keep alias so existing code doesn't break
        self.stack.addWidget(self.grid_view)

        self.artist_view = ArtistRichDetailView()
        self.artist_view._browser = self  # back-reference for related artist navigation
        self.artist_view.album_clicked.connect(self.on_artist_album_clicked)
        self.artist_view.play_album.connect(self.on_play_artist_album)
        self.artist_view.play_multiple_tracks.connect(self.play_album_signal.emit)
        self.artist_view.play_track.connect(self.play_track_signal.emit)
        self.artist_view.play_artist.connect(self.play_current_artist)
        self.artist_view.artist_clicked.connect(self.show_artist_details)
        self.artist_view.artist_favorite_toggled.connect(self._on_artist_favorite_toggled)
        self.stack.addWidget(self.artist_view)

        self.set_accent_color("#888888")

        
        self.refresh_grid()

    def _on_grid_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()

    def _set_window_shortcuts_enabled(self, enabled: bool):
        set_window_shortcuts_enabled(self, self.qml_view, enabled)

    def set_status_text(self, text):
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.statusTextChanged.emit(text)

    def focus_first_grid_item(self):
        """Forces an instant search and jumps keyboard focus to the first item."""
        if self.search_timer.isActive():
            self.search_timer.stop()
            self.execute_search()

        def apply_focus():
            if self.artist_model.rowCount() > 0:
                self.qml_view.setFocus(Qt.FocusReason.ShortcutFocusReason)

        # Wait 50ms before grabbing focus so the Enter key doesn't bleed into the grid!
        QTimer.singleShot(50, apply_focus)

    def execute_search(self):
        self.filtered_items = None
        self.load_artists_page(reset=True)

    def _safe_discard_worker(self, worker):
        """Monkey-proof thread disposal: Keeps thread alive in RAM until C++ finishes."""
        if not worker: return
        worker.is_cancelled = True
        try: worker.page_ready.disconnect()
        except: pass
        
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
            
        self._worker_graveyard.add(worker)
        
        # Connect to finished so it cleans itself out of the graveyard safely
        def remove_from_grave():
            if hasattr(self, '_worker_graveyard') and worker in self._worker_graveyard:
                self._worker_graveyard.remove(worker)
                
        try: worker.finished.connect(remove_from_grave)
        except: pass

    def check_viewport_qml(self, start_idx, end_idx):
        if len(self.artist_model.artists) == 0: return

        # Lazy placeholder expansion — deferred to avoid re-entrant beginInsertRows during QML layout.
        total = getattr(self, 'true_server_count', 0)
        current_rows = len(self.artist_model.artists)
        if total > current_rows and end_idx >= current_rows - 20:
            if not getattr(self, '_placeholder_expansion_pending', False):
                self._placeholder_expansion_pending = True
                def _expand():
                    self._placeholder_expansion_pending = False
                    rows_now = len(self.artist_model.artists)
                    tot_now  = getattr(self, 'true_server_count', 0)
                    if tot_now > rows_now:
                        expand_to = min(rows_now + 100, tot_now)
                        extra = [{'type': 'placeholder', 'name': ''} for _ in range(expand_to - rows_now)]
                        self.artist_model.append_artists(extra)
                from PyQt6.QtCore import QTimer as _QT
                _QT.singleShot(80, _expand)

        start_chunk = max(0, start_idx // 50)
        end_chunk   = max(0, end_idx   // 50)
        visible_chunks = set(range(start_chunk, end_chunk + 1))

        if not hasattr(self, 'loaded_chunks'):        self.loaded_chunks = set()
        if not hasattr(self, 'active_chunk_workers'): self.active_chunk_workers = {}

        # 1. Cancel out-of-view workers
        for chunk, worker in list(self.active_chunk_workers.items()):
            if chunk not in visible_chunks:
                self._safe_discard_worker(worker)
                del self.active_chunk_workers[chunk]
                if chunk in self.loaded_chunks: self.loaded_chunks.remove(chunk)

        # 2. GC: evict far-away chunks back to placeholders
        for chunk in list(self.loaded_chunks):
            if abs(chunk - start_chunk) > 3:
                self.loaded_chunks.remove(chunk)
                cs = chunk * 50
                ce = min(cs + 50, len(self.artist_model.artists))
                for i in range(cs, ce):
                    cur = self.artist_model.artists[i]
                    self.artist_model.artists[i] = {
                        'type': 'placeholder',
                        'name': cur.get('name', '') if isinstance(cur, dict) else '',
                        'id':   cur.get('id',   '') if isinstance(cur, dict) else '',
                    }
                self.artist_model.dataChanged.emit(
                    self.artist_model.index(cs, 0),
                    self.artist_model.index(ce - 1, 0),
                    [self.artist_model.NAME_ROLE, self.artist_model.COVER_ID_ROLE,
                     self.artist_model.IS_LOADING_ROLE]
                )

        # 3. Abort stale cover fetches; immediately load visible covers in dedicated threads
        if hasattr(self, 'cover_worker') and self.cover_worker:
            self.cover_worker.abort_current_batch()
            urgent = []
            for chunk in sorted(visible_chunks):
                if chunk in self.loaded_chunks:
                    cs = chunk * 50
                    ce = min(cs + 50, len(self.artist_model.artists))
                    for artist in self.artist_model.artists[cs:ce]:
                        cid = artist.get('cover_id')
                        if cid and not self.cover_provider.image_cache.get(str(cid)):
                            urgent.append(cid)
            if urgent:
                self.cover_worker.load_urgent(urgent)

        # 4. Fetch visible chunks not yet loaded
        for chunk in sorted(visible_chunks):
            if chunk not in self.loaded_chunks and chunk not in self.active_chunk_workers:
                self.loaded_chunks.add(chunk)
                self.active_chunk_workers[chunk] = self.fetch_chunk(chunk)

        # 5. Prefetch artist detail caches for visible artists (warms get_artist + bio)
        client = getattr(self, 'client', None)
        if client and hasattr(client, 'prefetch_artist'):
            for i in range(start_idx, min(end_idx + 1, len(self.artist_model.artists))):
                artist = self.artist_model.artists[i]
                if isinstance(artist, dict) and artist.get('type') != 'placeholder':
                    aid = artist.get('id')
                    if aid:
                        client.prefetch_artist(aid)

    def fetch_chunk(self, chunk_index):
        if hasattr(self, '_chunk_data_cache') and chunk_index in self._chunk_data_cache:
            items = self._chunk_data_cache[chunk_index]
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._on_chunk_loaded(items, chunk_index))
            return None

        query = getattr(self, 'current_query', '')
        sort_type = getattr(self, 'current_sort', 'alphabetical')
        is_ascending = getattr(self, 'sort_states', {}).get(sort_type, True)
        
        # LiveArtistWorker uses "page" math, so chunk 0 = page 1
        worker = LiveArtistWorker(
            self.client,
            query=query,
            sort_type=sort_type,
            is_ascending=is_ascending,
            page=chunk_index + 1,
            page_size=50,
            random_seed=getattr(self, 'random_seed', 0)
        )
        worker.page_ready.connect(lambda items, total, pages: self._on_chunk_loaded(items, chunk_index))
        worker.start()
        return worker

    def _sort_icon_path(self, sort_type, is_ascending):
        if sort_type == 'albums_count':
            return 'img/album.png'
        return f"img/sort-{sort_type}-{'a' if is_ascending else 'd'}.png"

    def show_sort_menu_at(self, global_x, global_y):
        """Show dropdown menu with sort options when the burger icon is clicked"""
        from player.widgets import themed_shadow_menu, popup_menu_at_global
        menu = themed_shadow_menu(self)

        for sort_type, label in [('random', 'Random'), ('most_played', 'Most Played'),
                                   ('alphabetical', 'Alphabetical'), ('albums_count', 'Albums Count')]:
            st = sort_type
            menu.add_action(label, lambda s=st: self.toggle_sort_state(s),
                            icon_path=self._sort_icon_path(st, self.sort_states.get(st, True)))

        popup_menu_at_global(menu, global_x, global_y, window=self.window())

    def update_burger_icon(self):
        """Update the burger icon name to reflect the currently active sort."""
        if not hasattr(self, 'current_sort'):
            return

        if self.current_sort == 'albums_count':
            name = 'album'
        else:
            is_ascending = self.sort_states.get(self.current_sort, True)
            name = f"sort-{self.current_sort}-{'a' if is_ascending else 'd'}"

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.burgerIconChanged.emit(name)

    def toggle_sort_state(self, sort_type):
        """Toggle the sort state and update display"""
        import time
        if self.current_sort == sort_type:
            # If already on this sort, just flip the direction
            self.sort_states[sort_type] = not self.sort_states[sort_type]
            if sort_type == 'random': self.random_seed = time.time()
        else:
        
            self.current_sort = sort_type
            
            
            if sort_type in ['most_played', 'albums_count']:
                self.sort_states[sort_type] = False
            else:
                self.sort_states[sort_type] = True
                
            if sort_type == 'random': self.random_seed = time.time()
            
        self.update_burger_icon()
        self.load_artists_page(reset=True)

    def load_artists_page(self, reset=False):
        
        if getattr(self, '_restored_state_waiting', False):
            reset = False 
            self._restored_state_waiting = False
            
        if reset:
            self.current_page = 0

        if not getattr(self, 'client', None): return
        if self.cover_worker: self.cover_worker.queue.clear()

        if reset:
            self._chunk_data_cache = {}

        # 1. MASS CANCEL: Kill any currently running workers safely
        if hasattr(self, 'active_chunk_workers'):
            for chunk, worker in list(self.active_chunk_workers.items()):
                self._safe_discard_worker(worker)
            self.active_chunk_workers.clear()
            
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None

        # 2. Reset our tracking variables
        self.artist_model.clear()
        self.pending_items.clear()
        self.loaded_chunks = set()

        query = getattr(self, 'current_query', '')
        sort_type = getattr(self, 'current_sort', 'alphabetical')
        is_ascending = getattr(self, 'sort_states', {}).get(sort_type, True)
        
        # 2. If it's a search, dump everything instantly (no virtualization)
        if query:
            worker = LiveArtistWorker(self.client, query, sort_type, is_ascending, page=1, page_size=500,
                                       random_seed=getattr(self, 'random_seed', 0))
            worker.page_ready.connect(self._on_search_loaded)
            worker.start()
            self.live_worker = worker
            return

        # 3. IF BROWSING: Find the library size first
        if not hasattr(self, 'true_server_count') or self.true_server_count == 0:
            worker = LiveArtistWorker(self.client, query="", sort_type=sort_type, is_ascending=is_ascending, page=1, page_size=1)
            worker.page_ready.connect(self._on_initial_count_loaded)
            worker.start()
            self.live_worker = worker
            return

        total_count = self.true_server_count
        self.set_status_text(f"{total_count:,} artists".replace(",", " "))
        
        # 4. Inject Placeholders
        if total_count > 0:
            pending = getattr(self, '_pending_cached_chunk', None)
            if pending:
                self.loaded_chunks.add(0)
                if not hasattr(self, 'active_chunk_workers'):
                    self.active_chunk_workers = {}
                self.active_chunk_workers[0] = None  # satisfy is_expected check in _on_chunk_loaded
            initial = min(50, total_count)
            placeholders = [{'type': 'placeholder', 'name': ''} for _ in range(initial)]
            self.artist_model.append_artists(placeholders)
            if pending:
                _cached = pending
                self._pending_cached_chunk = None
                from PyQt6.QtCore import QTimer as _QT
                _QT.singleShot(0, lambda: self._on_chunk_loaded(_cached, 0))
            self.check_viewport_qml(0, 50)

    def _on_initial_count_loaded(self, items, total_items, total_pages):
        self.true_server_count = total_items if total_items else 0
        if self.true_server_count and self.client:
            self.client.stale_cache_set('artists_count', self.true_server_count)
        self.load_artists_page()

    def _on_search_loaded(self, items, total_items, total_pages):
        if hasattr(self, 'live_worker') and self.live_worker:
            self._safe_discard_worker(self.live_worker)
            self.live_worker = None
        self.artist_model.clear()
        self.set_status_text(f"{len(items)} result{'s' if len(items) != 1 else ''}")
        self.populate_grid(items)
        if hasattr(self, 'qml_view') and self.isVisible():
            if not self.grid_bridge.search.active:
                self.qml_view.setFocus()

    def _on_chunk_loaded(self, artists, chunk_index):
        """Callback when a chunk of 50 artists arrives from the server."""
        if chunk_index == 0 and artists and self.client and not getattr(self, 'current_query', ''):
            _sort = getattr(self, 'current_sort', 'alphabetical')
            self.client.stale_cache_set(f'artists_chunk_0_{_sort}', artists)
        is_expected = hasattr(self, 'active_chunk_workers') and chunk_index in self.active_chunk_workers
        if is_expected:
            worker = self.active_chunk_workers.pop(chunk_index)
            self._safe_discard_worker(worker)
        else:
            return  # stale delivery after reset, discard

        if not artists: return

        if not hasattr(self, '_chunk_data_cache'):
            self._chunk_data_cache = {}
        self._chunk_data_cache[chunk_index] = artists

        start_row = chunk_index * 50
        covers_to_queue = []

        for i, artist_data in enumerate(artists):
            target_row = start_row + i
            if target_row >= len(self.artist_model.artists): break

            cid = artist_data.get('artistImageUrl') or artist_data.get('cover_id') or \
                  artist_data.get('coverArt') or artist_data.get('id')
            if cid:
                artist_data['cover_id'] = cid
                covers_to_queue.append(cid)

            self.artist_model.artists[target_row] = artist_data

        items_written = min(len(artists), len(self.artist_model.artists) - start_row)
        if items_written > 0:
            self.artist_model.dataChanged.emit(
                self.artist_model.index(start_row, 0),
                self.artist_model.index(start_row + items_written - 1, 0),
                [self.artist_model.NAME_ROLE, self.artist_model.COVER_ID_ROLE,
                 self.artist_model.IS_LOADING_ROLE, self.artist_model.ALBUM_COUNT_ROLE,
                 self.artist_model.SONG_COUNT_ROLE]
            )

        if hasattr(self, 'cover_worker') and self.cover_worker:
            self.cover_worker.queue_batch(covers_to_queue, priority=True)

    def resizeEvent(self, event): 
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()

    def set_client(self, client):
        self.client = client
        if self.client:
            if self.cover_worker:
                self.cover_worker.terminate()
            self.cover_worker = GridCoverWorker(client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()
            _sort = getattr(self, 'current_sort', 'alphabetical')
            cached_chunk = client.stale_cache_get(f'artists_chunk_0_{_sort}')
            self._pending_cached_chunk = cached_chunk or None
            self.refresh_grid()

    def show_loading(self):
        """Instant visual feedback — show animated skeleton grid before data arrives."""
        self.artist_model.clear()
        self.artist_model.append_artists(
            [{'type': 'placeholder', 'name': '', 'cover_id': ''} for _ in range(20)]
        )
        self.set_status_text("Loading...")

    def refresh_grid(self):
        # 1. Clear the server count so it always fetches fresh
        self.true_server_count = 0

        # 2. Brutally wipe the API cache for artists
        if hasattr(self, 'client') and self.client and hasattr(self.client, '_api_cache'):
            keys_to_delete = [k for k in self.client._api_cache.cache.keys() if 'getArtists' in k]
            for k in keys_to_delete:
                del self.client._api_cache.cache[k]

        self.load_artists_page(reset=True)

    def populate_grid(self, items):
        split_regex = re.compile(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )')
        processed = []
        seen = set()

        for item_data in items:
            name = item_data.get('name') or item_data.get('artist') or ""
            parts = split_regex.split(name)
            tokens = [p.strip() for p in parts if p.strip()]

            for token in tokens:
                token_lower = token.lower()
                if self.current_query and self.current_query.lower() not in token_lower:
                    continue
                if token_lower not in seen:
                    seen.add(token_lower)
                    new_item = dict(item_data)
                    new_item['name'] = token
                    new_item['artist'] = token
                    if len(tokens) > 1:
                        new_item['id'] = None
                    processed.append(new_item)

        self.artist_model.append_artists(processed)

        for artist_data in processed:
            cid = artist_data.get('artistImageUrl') or artist_data.get('cover_id') or \
                  artist_data.get('coverArt') or artist_data.get('id')
            if cid:
                artist_data['cover_id'] = cid
                if self.cover_worker:
                    self.cover_worker.queue_cover(cid, priority=False)

    def filter_grid(self, text):
        self.current_query = text
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.search.restore(text)

        self.load_artists_page(reset=True)

    def start_worker(self, worker):
        self._active_workers.add(worker)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        worker.start()

    def _cleanup_worker(self, worker):
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def show_artist_details(self, artist_data):
        if not artist_data: return
        if isinstance(artist_data, str):
            artist_data = {'name': artist_data, 'id': None}
        if not artist_data.get('id'):
            client = getattr(self, 'client', None)
            if client and hasattr(client, '_artist_name_id'):
                name = artist_data.get('name', '')
                cached_id = client._artist_name_id.get(name.lower().strip())
                if cached_id:
                    artist_data = dict(artist_data)
                    artist_data['id'] = cached_id

        self.stack.setCurrentIndex(1)
        self.artist_view.setFocus()
        self.artist_view.client = self.client

        # Share the grid's cover_worker so the detail view uses the same
        # singleton CoverCache. Connect only once to avoid duplicate emissions.
        if self.cover_worker and not getattr(self.artist_view, '_cover_worker_connected', False):
            self.artist_view.cover_worker = self.cover_worker
            try:
                self.cover_worker.cover_ready.connect(self.artist_view.apply_cover)
            except Exception:
                pass
            self.artist_view._cover_worker_connected = True

        self.artist_view.load_artist(artist_data)

    def apply_cover(self, cover_id, image_data):
        # Feed bytes into the QML image provider
        if hasattr(self, 'cover_provider'):
            self.cover_provider.image_cache[str(cover_id)] = image_data

        # Tell QML to redraw that cover
        if hasattr(self, 'artist_model'):
            self.artist_model.update_cover(str(cover_id))

        # Artist detail header — decode off main thread
        if getattr(self, 'current_header_cover_id', None) == str(cover_id):
            self.artist_view._decode_worker.enqueue(cover_id, image_data, side=400)

    def _qml_bg_color(self):
        r, g, b = (int(x) for x in getattr(self, '_bg_color', '14,14,14').split(','))
        return QColor(r, g, b)

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")
        if hasattr(self, 'qml_view'):
            self.qml_view.setClearColor(self._qml_bg_color())

    def set_accent_color(self, color):
        self.current_accent = color

        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")

        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(1.0)
            theme = getattr(self.window(), 'theme', None)
            if theme:
                self.grid_bridge.fontSizePrimaryChanged.emit(theme.font_size_primary)
                self.grid_bridge.fontSizeSecondaryChanged.emit(theme.font_size_secondary)
                self.grid_bridge.fontColorPrimaryChanged.emit(theme.font_color_primary)
                self.grid_bridge.fontColorSecondaryChanged.emit(theme.font_color_secondary)
                self.grid_bridge.skeletonBaseColorChanged.emit(
                    getattr(theme, 'skeleton_base', '#282828'))
                self.grid_bridge.fontFamilyChanged.emit(getattr(theme, 'app_font', ''))

                self.grid_bridge.hoverColorChanged.emit(resolve_menu_hover(theme))

                border = getattr(theme, 'border_color', '#2a2a2a')
                if not getattr(theme, 'auto_border_from_accent', True):
                    border = getattr(theme, 'manual_border_color', '#2a2a2a')
                self.grid_bridge.cardBorderChanged.emit(border)

                raw_bg = getattr(theme, 'main_panel_bg', '14,14,14')
                try:
                    r, g, b = (int(x) for x in raw_bg.split(','))
                    self.grid_bridge.panelBgChanged.emit('#{:02x}{:02x}{:02x}'.format(r, g, b))
                except Exception:
                    self.grid_bridge.panelBgChanged.emit('#0e0e0e')

        self.artist_view.set_accent_color(color)

        self.update_burger_icon()
 
    def on_grid_artist_clicked(self, data):
        """Called by bridge when user clicks an artist card."""
        if not data: return
        self.artist_clicked.emit(data)

    def on_grid_play_clicked(self, data):
        """Called by bridge when user clicks the play button on an artist card."""
        if not data: return
        artist_name = data.get('name') or data.get('artist')
        if artist_name:
            self._grid_play_worker = ArtistPlayWorker(self.client, artist_name)
            self._grid_play_worker.tracks_ready.connect(self.play_album_signal.emit)
            self._grid_play_worker.start()

    def on_artist_album_clicked(self, album_data): 
        self.switch_to_album_tab.emit(album_data)
    
    def on_play_artist_album(self, album_data): 
        self.start_play_fetch(album_data['id'])

    def play_current_artist(self):
        """Live fetches all tracks for the artist and plays them."""
        artist_name = getattr(self.artist_view, 'current_artist_name', None)
        if not artist_name or artist_name == "Loading...":
            return
        self.play_worker = ArtistPlayWorker(self.client, artist_name)
        self.play_worker.tracks_ready.connect(self.play_album_signal.emit)
        self.play_worker.start()

    def start_play_fetch(self, album_id):
        worker = TrackLoaderWorker(self.client, album_id)
        worker.tracks_ready.connect(lambda tracks, aid: self.play_album_signal.emit(tracks) if tracks else None)
        self.start_worker(worker)

    def _on_artist_favorite_toggled(self, is_liked: bool):
        client = getattr(self, 'client', None)
        artist_id = getattr(self.artist_view, 'current_artist_id', None)
        if client and artist_id:
            import threading
            threading.Thread(
                target=lambda: client.set_favorite(artist_id, is_liked, id_param='artistId'),
                daemon=True,
            ).start()
    
    def go_to_root(self):
        self.stack.setCurrentIndex(0)

        if hasattr(self, 'qml_view'):
            self.qml_view.setFocus()

        # ONLY reload the grid if the user was actively looking at search results!
        is_searching = bool(getattr(self, 'current_query', ""))
        if is_searching:
            self.current_query = ""
            if hasattr(self, 'grid_bridge'):
                self.grid_bridge.search.reset()
            self.load_artists_page(reset=True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.cancelScroll.emit()

    def get_state(self):
        """Returns the current state for saving."""
        return {
            'sort': getattr(self, 'current_sort', 'most_played'),
            'sort_states': getattr(self, 'sort_states', {}),
        }

    def restore_state(self, state):
        """Applies a saved state before the first load."""
        if not state: return
        
    
        self.current_sort = state.get('sort', 'most_played')
        
        saved_sorts = state.get('sort_states', {})
        for k, v in saved_sorts.items():
            self.sort_states[k] = v
            
        self.current_query = state.get('query', '')

        if hasattr(self, 'grid_bridge') and self.current_query:
            self.grid_bridge.search.restore(self.current_query)

        if hasattr(self, 'update_burger_icon'):
            self.update_burger_icon()
            
        self._restored_state_waiting = True