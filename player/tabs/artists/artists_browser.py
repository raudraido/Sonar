import random
import re
import time
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget)

from PyQt6.QtCore import Qt, QSize, pyqtSignal, pyqtProperty, QTimer, QPoint, QRectF, QThread, QEvent, QAbstractListModel, QModelIndex, pyqtSlot, QObject, QUrl, QMetaObject
from PyQt6.QtGui import QPixmap, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider

from player.widgets import CoverImageProvider, QMLGridWrapper, QMLMiddleClickScroller, AlbumModel, AlbumIconProvider, AlbumDetailCoverProvider
from player.qml_search import SearchController, GridSearchKeyFilter, set_window_shortcuts_enabled
from player import resource_path
from player.workers import GridCoverWorker

from player.mixins.visuals import resolve_menu_hover, CoverDecodeWorker


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

class LiveArtistDetailWorker(QThread): # Fetches everything for ONE artist's detail page (albums, top songs, bio/similar artists, appears-on) via parallel network calls, then emits it all at once via content_ready so the page can be built in its final shape in a single pass.
    content_ready = pyqtSignal(dict, list, list, list, list)  # info, main_albums, singles, top_songs, appears_on

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

                # If native already has both the bio and similar artists, don't block
                # on subsonic's getArtistInfo2 — that call can trigger a slow live
                # Last.fm round-trip on a cache miss. Otherwise wait for it (it was
                # already started in parallel above), since it's the only source
                # that reliably returns similarArtist on this server.
                if native_result.get('biography') and native_result.get('similarArtist'):
                    sub = {}
                else:
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
            with ThreadPoolExecutor(max_workers=4) as pool:
                f_artist = pool.submit(_fetch_artist)
                f_songs  = pool.submit(_fetch_top_songs)
                f_info2  = pool.submit(_fetch_info2)

                info = f_artist.result() or {}
                print(f"[TIMING]   get_artist done at {_ms(_t0)} (parallel started {_ms(_t_parallel)} ago)")
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

                # Start appears_on search now that we have own_album_ids — runs in the
                # background while we wait for top songs / bio below.
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

                # Wait for everything — top songs, bio/similar, and appears-on —
                # then build the whole page in one shot. No sections popping in,
                # reordering, or stat changes after first paint.
                top_songs = f_songs.result() or []

                extra = f_info2.result() or {}
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

                appears_on = f_appears.result() or []

                print(f"[TIMING]   → content_ready emit at {_ms(_t0)}  "
                      f"top_songs={len(top_songs)}  has_bio={bool(bio)}  similar={len(similar)}  "
                      f"appears={len(appears_on)}")
                self.content_ready.emit(info, main_albums, singles, top_songs, appears_on)

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

class TrackListModel(QAbstractListModel): # Model backing the "Popular" tracks QML list: track number, title, album, duration, cover id, and the raw song dict.
    TRACK_NUMBER_ROLE = Qt.ItemDataRole.UserRole + 1
    TITLE_ROLE        = Qt.ItemDataRole.UserRole + 2
    ALBUM_ROLE        = Qt.ItemDataRole.UserRole + 3
    DURATION_ROLE     = Qt.ItemDataRole.UserRole + 4
    COVER_ID_ROLE     = Qt.ItemDataRole.UserRole + 5
    RAW_DATA_ROLE     = Qt.ItemDataRole.UserRole + 6

    def __init__(self):
        super().__init__()
        self.tracks = []

    def rowCount(self, parent=QModelIndex()): return len(self.tracks)

    def data(self, index, role):
        if not index.isValid(): return None
        t = self.tracks[index.row()]
        if role == self.TRACK_NUMBER_ROLE: return index.row() + 1
        if role == self.TITLE_ROLE: return t.get('title') or 'Unknown'
        if role == self.ALBUM_ROLE: return t.get('album') or ''
        if role == self.DURATION_ROLE: return t.get('duration') or ''
        if role == self.COVER_ID_ROLE: return t.get('coverId_forced') or t.get('coverArt') or t.get('albumId') or ''
        if role == self.RAW_DATA_ROLE: return t
        return None

    def roleNames(self):
        return {
            self.TRACK_NUMBER_ROLE: b"trackNumber",
            self.TITLE_ROLE: b"trackTitle",
            self.ALBUM_ROLE: b"trackAlbum",
            self.DURATION_ROLE: b"trackDuration",
            self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData",
        }

    def set_tracks(self, tracks):
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def update_cover(self, cover_id):
        forced_id = f"{cover_id}?t={time.time()}"
        for i, t in enumerate(self.tracks):
            raw = str(t.get('coverArt') or t.get('albumId') or '')
            if raw == cover_id:
                t['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class PopularTrackCoverProvider(QQuickImageProvider): # Serves rounded-rect track covers for the "Popular" QML list, with a class-level cache shared across loads.

    _cache = {}

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id, _requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import QRectF
        real_id = id.split("?t=")[0]
        data = self._cache.get(real_id)
        size = 80
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
                path.addRoundedRect(QRectF(0, 0, size, size), 8, 8)
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()
        return img, img.size()

class TrackListBridge(QObject): # Bridge for signals between the "Popular" tracks QML list and Python.
    selectIndex   = pyqtSignal(int)
    trackClicked  = pyqtSignal(int)
    albumClicked  = pyqtSignal(int)

    @pyqtSlot(int)
    def emitTrackClicked(self, idx):
        self.trackClicked.emit(idx)

    @pyqtSlot(int)
    def emitAlbumClicked(self, idx):
        self.albumClicked.emit(idx)

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

class SectionGridBridge(QObject): # Single shared bridge for all album-section grids: signals/slots carry a leading sectionRow (the chunk's row index in ArtistSectionsModel) alongside the item index.
    selectIndex       = pyqtSignal(int, int)  # sectionRow, itemIndex
    itemClicked       = pyqtSignal(int, int)
    playClicked       = pyqtSignal(int, int)
    artistNameClicked = pyqtSignal(str, str)  # name, artist_id

    @pyqtSlot(int, int)
    def emitItemClicked(self, section_row, idx):
        self.itemClicked.emit(section_row, idx)

    @pyqtSlot(int, int)
    def emitPlayClicked(self, section_row, idx):
        self.playClicked.emit(section_row, idx)

    @pyqtSlot(str, str)
    def emitArtistNameClicked(self, name, artist_id=""):
        self.artistNameClicked.emit(name, artist_id)

class ArtistSectionsModel(QAbstractListModel): # One row per album-section chunk (Albums/Singles/Appears On, split into _QML_CHUNK-sized pieces): sectionTitle/sectionCount are only set on a chunk's first row, and albumModel is that chunk's AlbumModel, bound directly by the QML GridView delegate.
    TITLE_ROLE       = Qt.ItemDataRole.UserRole + 1
    COUNT_ROLE       = Qt.ItemDataRole.UserRole + 2
    ALBUM_MODEL_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self):
        super().__init__()
        self.rows = []  # [{'title': str, 'count': int, 'model': AlbumModel}, ...]

    def rowCount(self, parent=QModelIndex()): return len(self.rows)

    def data(self, index, role):
        if not index.isValid(): return None
        row = self.rows[index.row()]
        if role == self.TITLE_ROLE: return row['title']
        if role == self.COUNT_ROLE: return row['count']
        if role == self.ALBUM_MODEL_ROLE: return row['model']
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE: b"sectionTitle",
            self.COUNT_ROLE: b"sectionCount",
            self.ALBUM_MODEL_ROLE: b"albumModel",
        }

    def set_sections(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

class ArtistRowModel(QAbstractListModel): # Model backing the related-artists QML strip: name, circular cover id, and the raw artist dict.
    NAME_ROLE     = Qt.ItemDataRole.UserRole + 1
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 2
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self):
        super().__init__()
        self.artists = []

    def rowCount(self, parent=QModelIndex()): return len(self.artists)

    def data(self, index, role):
        if not index.isValid(): return None
        a = self.artists[index.row()]
        if role == self.NAME_ROLE: return a.get('name') or a.get('title') or ''
        if role == self.COVER_ID_ROLE: return a.get('coverId_forced') or a.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return a
        return None

    def roleNames(self):
        return {
            self.NAME_ROLE: b"artistName",
            self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData",
        }

    def set_artists(self, artists):
        self.beginResetModel()
        self.artists = list(artists)
        self.endResetModel()

    def update_cover(self, cover_id):
        forced_id = f"{cover_id}?t={time.time()}"
        for i, a in enumerate(self.artists):
            raw = str(a.get('cover_id') or a.get('coverArt') or a.get('id') or '')
            if raw == cover_id:
                a['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class RelatedArtistCoverProvider(QQuickImageProvider): # Serves circular artist photos for the related-artists strip, with a class-level cache shared across loads.

    _cache = {}

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, id, _requestedSize):
        from PyQt6.QtGui import QImage, QPainter, QPainterPath
        from PyQt6.QtCore import QRectF
        real_id = id.split("?t=")[0]
        data = self._cache.get(real_id)
        size = 220
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
                path.addEllipse(QRectF(0, 0, size, size))
                painter.setClipPath(path)
                painter.drawImage(0, 0, source)
                painter.end()
        return img, img.size()

class RelatedArtistsBridge(QObject): # Bridge for signals between the related-artists QML strip and Python.
    selectIndex  = pyqtSignal(int)
    itemClicked  = pyqtSignal(int)
    playClicked  = pyqtSignal(int)

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        self.itemClicked.emit(idx)

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        self.playClicked.emit(idx)

class _ArtistPhotoOverlay(QWidget): # A full-screen tinted overlay showing the artist photo large and centered.

    def __init__(self, pixmap, parent):
        # Top-level frameless window rather than a child widget: QQuickView-backed
        # views (createWindowContainer) always paint above regular child widgets,
        # so a child overlay would be hidden behind them.
        super().__init__(None,
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint)
        self._pixmap = pixmap
        self._parent_window = parent
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.geometry())
        self.setCursor(Qt.CursorShape.ArrowCursor)
        parent.installEventFilter(self)
        # No QObject parent (top-level window), so keep a strong Python ref
        # on the main window — otherwise this gets garbage-collected right
        # after __init__ returns.
        parent._photo_overlay = self
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def eventFilter(self, obj, event):
        if obj is self._parent_window and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.setGeometry(self._parent_window.geometry())
        return False

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
        self._close()

    def keyPressEvent(self, event):
        self._close()

    def _close(self):
        self._parent_window.removeEventFilter(self)
        if getattr(self._parent_window, '_photo_overlay', None) is self:
            self._parent_window._photo_overlay = None
        self.close()
        self.deleteLater()

class _ArtistLoadingOverlay(QWidget): # Opaque overlay + spinner shown over the artist body while it loads.

    _SIZE = 52  # spinner diameter

    def __init__(self, parent_view):
        # Top-level frameless window, same pattern as _ArtistPhotoOverlay
        # (UI_MANIFEST.md Pattern A): the page is QQuickView-backed
        # (createWindowContainer), whose native window always paints above
        # regular sibling QWidgets regardless of z-order/raise_(). A
        # top-level WA_AlwaysStackOnTop window sits above it unconditionally.
        super().__init__(None,
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint |
            Qt.WindowType.WindowDoesNotAcceptFocus)
        self._parent_view = parent_view
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Without this, the frameless Tool window gets a system drop-shadow
        # painted around it on some platforms even though we paint a fully
        # opaque fill ourselves (same as _CoverOverlay/_ArtistPhotoOverlay).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._bg_color = QColor(14, 14, 14)
        self._spin_color = QColor('#cccccc')
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def set_bg_color(self, color: QColor):
        self._bg_color = QColor(color)
        if self.isVisible():
            self.update()

    def set_spinner_color(self, color):
        self._spin_color = QColor(color)
        if self.isVisible():
            self.update()

    def sync_geometry(self):
        # Cover the whole page, in global coordinates since this is now a
        # top-level window.
        view = self._parent_view
        top_left = view.mapToGlobal(QPoint(0, 0))
        self.setGeometry(top_left.x(), top_left.y(), view.width(), view.height())

    def start(self):
        self.sync_geometry()
        self._angle = 0
        self._timer.start()
        self.show()
        self.raise_()
        self._parent_view.window().installEventFilter(self)

    def stop(self):
        self._timer.stop()
        self.hide()
        self._parent_view.window().removeEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._parent_view.window() and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.sync_geometry()
        return False

    def _tick(self):
        self._angle = (self._angle + 5) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self._bg_color)

        s = self._SIZE
        m = 5
        rect = QRectF((self.width() - s) / 2 + m, (self.height() - s) / 2 + m, s - 2 * m, s - 2 * m)
        pen = QPen(QColor(255, 255, 255, 35), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(rect)

        arc_color = QColor(self._spin_color)
        arc_color.setAlpha(210)
        pen.setColor(arc_color)
        p.setPen(pen)
        p.drawArc(rect, int(-self._angle * 16), int(100 * 16))
        p.end()

class ArtistDetailCoverProvider(AlbumDetailCoverProvider): # Provides the artist photo for the main header in the artist detail view, with a circular shadow and clip to suit the round photo.
    

    def _shadow_shape(self, pad, oy, art, r):
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad + oy, art, art))
        return path

    def _art_clip(self, pad, art, r):
        path = QPainterPath()
        path.addEllipse(QRectF(pad, pad, art, art))
        return path

class ArtistDetailBridge(QObject): # Bridge for the artist detail header card in artist_detail_page.qml: forwards button clicks (play/like/lastfm/wikipedia/photo zoom) to ArtistRichDetailView, drives QML tooltips, and pushes theme/typography/color/bio/stats signals to QML.
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
    
    _QML_CHUNK = 80  # Max albums per AlbumModel chunk — keeps each texture well under GPU limits

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

        self._artist_liked = False
        self._artist_stats_text = "Loading..."
        self._cur_photo_pixmap = None
        self.current_header_cover_id = None

        self._photo_provider = ArtistDetailCoverProvider()
        self._artist_icon_provider = AlbumIconProvider()
        self._section_cover_provider = SectionCoverProvider()
        self._popular_track_cover_provider = PopularTrackCoverProvider()
        self._related_artist_cover_provider = RelatedArtistCoverProvider()

        self._artist_bridge = ArtistDetailBridge(self)
        self._section_bridge = SectionGridBridge()
        self._track_list_bridge = TrackListBridge()
        self._related_artists_bridge = RelatedArtistsBridge()

        self.track_model = TrackListModel()
        self.related_artist_model = ArtistRowModel()
        self.sections_model = ArtistSectionsModel()

        # Single QQuickView for the whole page — QQuickView in a window
        # container renders at the monitor's real refresh rate, unlike
        # QQuickWidget which caps at ~60Hz regardless of display Hz, and
        # internal scrolling is handled by MomentumScroll in QML instead of
        # repositioning a forest of native child windows.
        self._qml = QMLGridWrapper()
        self._qml.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._qml.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._set_qml_clear_color(QColor(14, 14, 14))

        engine = self._qml.engine()
        engine.addImageProvider("artistdetailcover",   self._photo_provider)
        engine.addImageProvider("albumicons",          self._artist_icon_provider)
        engine.addImageProvider("sectioncovers",       self._section_cover_provider)
        engine.addImageProvider("populartrackcovers",  self._popular_track_cover_provider)
        engine.addImageProvider("relatedartistcovers", self._related_artist_cover_provider)

        ctx = self._qml.rootContext()
        ctx.setContextProperty("artistBridge", self._artist_bridge)
        ctx.setContextProperty("sectionBridge", self._section_bridge)
        ctx.setContextProperty("trackListBridge", self._track_list_bridge)
        ctx.setContextProperty("trackListModel", self.track_model)
        ctx.setContextProperty("relatedArtistsBridge", self._related_artists_bridge)
        ctx.setContextProperty("relatedArtistModel", self.related_artist_model)
        ctx.setContextProperty("sectionsModel", self.sections_model)

        self._qml.setSource(QUrl.fromLocalFile(resource_path("player/tabs/artists/artist_detail_page.qml")))

        self.layout.addWidget(self._qml)

        self._track_list_bridge.trackClicked.connect(self._on_track_clicked)
        self._track_list_bridge.albumClicked.connect(self._on_popular_album_clicked)
        self._section_bridge.itemClicked.connect(self._on_section_item_clicked)
        self._section_bridge.playClicked.connect(self._on_section_play_clicked)
        self._section_bridge.artistNameClicked.connect(self._on_section_artist_name_clicked)
        self._related_artists_bridge.itemClicked.connect(self._on_related_artist_item_clicked)
        self._related_artists_bridge.playClicked.connect(self._on_related_artist_play_clicked)

        self._qml.installEventFilter(self)

        # Opaque overlay + centered spinner shown over the page while it
        # loads, hiding the in-progress layout until everything settles.
        self._loading_overlay = _ArtistLoadingOverlay(self)
        self._loading_overlay.set_bg_color(self._bg_qcolor())
        self._spinner_pending = False

        self._nav_chain_idx = 0
        self._nav_item_idx = 0

        # This widget receives keyboard focus directly; all key handling for
        # the page happens in eventFilter() on the single QML view.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._loading_overlay.isVisible():
            self._loading_overlay.sync_geometry()

    def _on_track_clicked(self, idx):
        if 0 <= idx < len(self.track_model.tracks):
            self.play_track.emit(self.track_model.tracks[idx])

    def _on_popular_album_clicked(self, idx):
        if not (0 <= idx < len(self.track_model.tracks)):
            return
        data = self.track_model.tracks[idx]
        alb_data = {
            'id': data.get('albumId'),
            'title': data.get('album'),
            'artist': data.get('albumArtist') or data.get('artist'),
            'coverArt': data.get('coverArt') or data.get('albumId'),
            'cover_id': data.get('coverArt') or data.get('albumId')
        }
        if alb_data['id']:
            self.album_clicked.emit(alb_data)

    def _on_section_item_clicked(self, section_row, idx):
        albums = self.sections_model.rows[section_row]['model'].albums
        if 0 <= idx < len(albums):
            self.album_clicked.emit(albums[idx])

    def _on_section_play_clicked(self, section_row, idx):
        albums = self.sections_model.rows[section_row]['model'].albums
        if 0 <= idx < len(albums):
            self.play_album.emit(albums[idx])

    def _on_section_artist_name_clicked(self, name, artist_id):
        self.artist_clicked.emit({'name': name, 'id': artist_id or None})

    def _on_related_artist_item_clicked(self, idx):
        artists = self.related_artist_model.artists
        if 0 <= idx < len(artists):
            data = artists[idx]
            self._on_related_artist_clicked({'id': data.get('id'), 'name': data.get('name', '')})

    def _on_related_artist_play_clicked(self, idx):
        artists = self.related_artist_model.artists
        if 0 <= idx < len(artists):
            self._play_related_artist_tracks(artists[idx].get('name', ''))

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.KeyPress and self._qml._owns(source):
            chain = self._nav_chain()
            if not chain:
                return super().eventFilter(source, event)

            chain_idx = max(0, min(self._nav_chain_idx, len(chain) - 1))
            entry = chain[chain_idx]
            item_idx = max(0, min(self._nav_item_idx, entry['count'] - 1))
            key = event.key()

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.isAutoRepeat():
                    return True
                if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                    self.play_current_artist_tracks()
                    return True
                self._activate_nav_entry(entry, item_idx, event.modifiers())
                return True

            if key == Qt.Key.Key_Space:
                if event.isAutoRepeat():
                    return True
                self._activate_nav_entry(entry, item_idx, Qt.KeyboardModifier.ShiftModifier)
                return True

            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
                ipr = max(1, entry['ipr'])
                count = entry['count']
                col = item_idx % ipr
                cur_row = item_idx // ipr
                last_row = (count - 1) // ipr

                if key == Qt.Key.Key_Right:
                    if item_idx < count - 1:
                        self._select_nav_entry(chain_idx, item_idx + 1)
                elif key == Qt.Key.Key_Left:
                    if item_idx > 0:
                        self._select_nav_entry(chain_idx, item_idx - 1)
                elif key == Qt.Key.Key_Down:
                    if cur_row < last_row:
                        self._select_nav_entry(chain_idx, min(item_idx + ipr, count - 1))
                    else:
                        self._jump_to_chain_entry(chain, chain_idx, 1, col)
                elif key == Qt.Key.Key_Up:
                    if cur_row > 0:
                        self._select_nav_entry(chain_idx, item_idx - ipr)
                    else:
                        self._jump_to_chain_entry(chain, chain_idx, -1, col)
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
        r, g, b = (int(x) for x in c.split(','))
        self._set_qml_clear_color(QColor(r, g, b))
        self._loading_overlay.set_bg_color(QColor(r, g, b))

    def set_accent_color(self, color):
        self.current_accent = color

        self.setStyleSheet(f"#DetailBackground {{ background-color: rgb({getattr(self, '_bg_color', '14,14,14')}); border-radius: 0; }}")

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
            self._set_qml_clear_color(QColor(r, g, b))
            self._loading_overlay.set_bg_color(QColor(r, g, b))

        self._loading_overlay.set_spinner_color(color)

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

    def _set_qml_clear_color(self, color: QColor):
        self._qml.setClearColor(color)

    def _bg_qcolor(self):
        try:
            r, g, b = (int(x) for x in getattr(self, '_bg_color', '14,14,14').split(','))
        except Exception:
            r, g, b = 14, 14, 14
        return QColor(r, g, b)

    def set_related_artists(self, similar_artists):
        if not similar_artists:
            self.related_artist_model.set_artists([])
            return

        # Cap at 10, normalise to the fields ArtistRowModel/QML expect
        similar_artists = similar_artists[:10]
        normalised = []
        for a in similar_artists:
            normalised.append({
                'id': a.get('id'),
                'name': a.get('name', ''),
                'cover_id': a.get('coverArt') or a.get('id') or '',
            })
        self.related_artist_model.set_artists(normalised)

        worker = getattr(self, 'cover_worker', None)
        for a in normalised:
            cover_id = a.get('cover_id')
            if not cover_id:
                continue
            self.pending_qml_sections.setdefault(cover_id, []).append((RelatedArtistCoverProvider._cache, self.related_artist_model))
            if worker:
                worker.queue_cover(cover_id)

    def _on_related_artist_clicked(self, data):
        browser = getattr(self, '_browser', None)
        if browser:
            browser.show_artist_details(data)
        else:
            self.artist_clicked.emit(data)

    def _play_related_artist_tracks(self, name):
        client = getattr(self, 'client', None)
        if not client or not name:
            return
        self._related_play_worker = ArtistPlayWorker(client, name)
        self._related_play_worker.tracks_ready.connect(self.play_multiple_tracks.emit)
        self._related_play_worker.start()

    def set_top_songs(self, songs):
        self.track_model.set_tracks(list(songs))
        if not songs:
            return
        worker = getattr(self, 'cover_worker', None)
        for t in songs:
            cid = t.get('coverArt') or t.get('albumId') or ''
            if cid:
                self.pending_qml_sections.setdefault(cid, []).append((PopularTrackCoverProvider._cache, self.track_model))
                if worker:
                    worker.queue_cover(cid)

    def clear_sections(self):
        self.sections_model.set_sections([])

    def add_section(self, title, albums, cover_worker):
        if not albums:
            return []

        sorted_albums = sorted(albums, key=lambda x: (int(x.get('playCount', 0)), str(x.get('year', '0000'))), reverse=True)

        # Normalise so AlbumModel's cover_id key is always populated — raw
        # Subsonic album dicts only carry coverArt/id, not cover_id.
        normalised = []
        for a in sorted_albums:
            d = dict(a)
            if not d.get('cover_id'):
                d['cover_id'] = d.get('coverArt') or d.get('id') or ''
            normalised.append(d)
        sorted_albums = normalised

        # Split into chunks to avoid exceeding GPU texture size limits
        chunk_size = self._QML_CHUNK
        chunks = [sorted_albums[i:i + chunk_size] for i in range(0, len(sorted_albums), chunk_size)]

        rows = []
        for chunk_idx, chunk in enumerate(chunks):
            # Only first chunk gets the section title + total count badge
            chunk_title = title if chunk_idx == 0 else ""
            chunk_count = len(sorted_albums) if chunk_idx == 0 else 0

            model = AlbumModel()
            model.set_albums(chunk)
            rows.append({'title': chunk_title, 'count': chunk_count, 'model': model})

            for album in chunk:
                cid = album.get('cover_id') or ''
                if cid:
                    self.pending_qml_sections.setdefault(cid, []).append((SectionCoverProvider._cache, model))
                    if cover_worker:
                        cover_worker.queue_cover(cid)

        return rows

    def _items_per_row(self):
        avail = max(1, self._qml.width() - 8)
        return max(1, int(avail // 200))

    def _nav_chain(self):
        """Ordered list of focusable sections on this page: popular tracks,
        then each album-section chunk, then related artists. Each entry is a
        lightweight descriptor — the actual selection/scroll happens in QML
        via the *Bridge.selectIndex signals."""
        chain = []
        if self.track_model.rowCount() > 0:
            chain.append({'kind': 'tracks', 'count': self.track_model.rowCount(), 'ipr': 1})
        ipr = self._items_per_row()
        for i, row in enumerate(self.sections_model.rows):
            count = row['model'].rowCount()
            if count > 0:
                chain.append({'kind': 'section', 'count': count, 'ipr': ipr, 'section_row': i})
        related_count = self.related_artist_model.rowCount()
        if related_count > 0:
            chain.append({'kind': 'related', 'count': related_count, 'ipr': related_count})
        return chain

    def _select_nav_entry(self, chain_idx, item_idx):
        chain = self._nav_chain()
        if not chain:
            return
        chain_idx = max(0, min(chain_idx, len(chain) - 1))
        entry = chain[chain_idx]
        item_idx = max(0, min(item_idx, entry['count'] - 1))
        self._nav_chain_idx = chain_idx
        self._nav_item_idx = item_idx

        if entry['kind'] == 'tracks':
            self._track_list_bridge.selectIndex.emit(item_idx)
        elif entry['kind'] == 'section':
            self._section_bridge.selectIndex.emit(entry['section_row'], item_idx)
        elif entry['kind'] == 'related':
            self._related_artists_bridge.selectIndex.emit(item_idx)

    def _activate_nav_entry(self, entry, item_idx, modifiers):
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if entry['kind'] == 'tracks':
            if not (0 <= item_idx < len(self.track_model.tracks)):
                return
            if shift:
                self._on_popular_album_clicked(item_idx)
            else:
                self._on_track_clicked(item_idx)
        elif entry['kind'] == 'section':
            albums = self.sections_model.rows[entry['section_row']]['model'].albums
            if not (0 <= item_idx < len(albums)):
                return
            if shift:
                self.play_album.emit(albums[item_idx])
            else:
                self.album_clicked.emit(albums[item_idx])
        elif entry['kind'] == 'related':
            artists = self.related_artist_model.artists
            if not (0 <= item_idx < len(artists)):
                return
            data = artists[item_idx]
            if shift:
                self._play_related_artist_tracks(data.get('name', ''))
            else:
                self._on_related_artist_clicked({'id': data.get('id'), 'name': data.get('name', '')})

    def _jump_to_chain_entry(self, chain, from_idx, direction, col):
        target_idx = from_idx + direction
        if not (0 <= target_idx < len(chain)):
            self._invoke_qml('scrollToTop' if direction < 0 else 'scrollToBottom')
            return
        target = chain[target_idx]
        ipr = max(1, target['ipr'])
        count = target['count']
        if direction > 0:
            landing = min(col, ipr - 1, count - 1)
        else:
            last_row = (count - 1) // ipr
            landing = min(last_row * ipr + col, count - 1)
        self._select_nav_entry(target_idx, max(0, landing))

    def _invoke_qml(self, method_name):
        root = self._qml.rootObject()
        if root is not None:
            QMetaObject.invokeMethod(root, method_name)

    def auto_focus(self):
        # setFocus first (Qt focus + root.forceActiveFocus), then _select_nav_entry
        # so popularList.forceActiveFocus() runs last and wins QML activeFocus.
        self._qml.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._invoke_qml('scrollToTop')
        chain = self._nav_chain()
        if chain:
            self._select_nav_entry(0, 0)

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
        # of where the previous artist's view was scrolled to.
        self._invoke_qml('scrollToTop')

        self.pending_qml_sections = {}  # cover_id -> [(provider_cache, model), ...]
        self.current_artist_name = artist_data.get('name', 'Unknown')
        self.current_artist_id = artist_data.get('id')
        self._artist_liked = bool(artist_data.get('starred'))

        # 1. EMPTY PAGE + SPINNER — an opaque overlay covers the whole page first,
        # so nothing (old content, half-built new content) is visible while
        # everything settles. _reveal_content hides it once the new content
        # is in place.
        print(f"[TIMING-UI] load_artist({self.current_artist_name!r}, id={self.current_artist_id}) at {time.time():.3f}")
        self._set_stats("Loading...")
        self.set_bio("")
        self.set_top_songs([])
        self.set_related_artists([])
        self.clear_sections()
        self._spinner_pending = False
        self._loading_overlay.start()

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
            try: self.live_detail_worker.content_ready.disconnect()
            except: pass
            self._safe_discard_worker(self.live_detail_worker)

        self.live_detail_worker = LiveArtistDetailWorker(
            self.client,
            self.current_artist_id,
            self.current_artist_name
        )
        self.live_detail_worker.content_ready.connect(self._on_content_ready)
        self.live_detail_worker.start()

    def _on_content_ready(self, info, main_albums, singles, top_songs, appears_on):
        """Fires once everything for the page — albums, top songs, bio/similar,
        and appears-on — has arrived. Builds the whole body in its final shape
        in one pass: the page reveals once QML has had a frame to settle."""
        print(f"[TIMING-UI] _on_content_ready({self.current_artist_name!r}) at {time.time():.3f}  "
              f"main_albums={len(main_albums)}  singles={len(singles)}  top_songs={len(top_songs)}  "
              f"appears={len(appears_on)}  has_bio={'biography' in info}")

        if info:
            self._artist_liked = bool(info.get('starred'))
            self._update_like_btn()

            # Prefer high-res Last.fm/MusicBrainz URL from getArtistInfo2 over getCoverArt thumb
            artist_img_url = info.get('artistImageUrl', '')
            if artist_img_url:
                self._exact_artist_image = True
                from player.components.artist_info_panel import _ImageWorker
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

            if 'biography' in info:
                self.set_bio(info['biography'])

            self.set_related_artists(info.get('similar_artists', []))

        total_releases = len(main_albums) + len(singles)
        appears_count  = len(appears_on)

        if total_releases == 0 and appears_count == 0:
            self._set_stats("No releases found")
        elif total_releases == 0:
            self._set_stats(f"Guest Artist • {appears_count} appearances")
        else:
            suffix = f" • {appears_count} appearances" if appears_count else ""
            self._set_stats(f"{total_releases} releases{suffix}")

        self.set_top_songs(top_songs)

        worker = getattr(self, 'cover_worker', None)
        rows = []
        if main_albums:  rows += self.add_section("Albums",      main_albums, worker)
        if singles:      rows += self.add_section("Singles & EPs", singles,   worker)
        if appears_on:   rows += self.add_section("Appears on & Compilations", appears_on, worker)
        self.sections_model.set_sections(rows)

        self._spinner_pending = True
        print(f"[REVEAL] _on_content_ready done at {time.time():.3f}")
        QTimer.singleShot(0, self._reveal_content)

    def _reveal_content(self):
        if not self._spinner_pending:
            return
        self._spinner_pending = False
        self.auto_focus()
        self._loading_overlay.stop()
        print(f"[REVEAL] reveal complete at {time.time():.3f}")

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
        has_qml    = cover_id in getattr(self, 'pending_qml_sections', {})

        if not (is_header or has_qml):
            return

        # QML sections only need raw bytes — store sync (instant)
        if has_qml:
            for cache_dict, model in self.pending_qml_sections[cover_id]:
                cache_dict[cover_id] = image_data
                model.update_cover(cover_id)
            del self.pending_qml_sections[cover_id]

        # Pixmap work (decode + scale) goes to background thread
        if is_header:
            self._decode_worker.enqueue(cover_id, image_data, side=400)

    def _on_cover_decoded(self, cover_id: str, img):
        from PyQt6.QtGui import QPixmap
        pix  = QPixmap.fromImage(img)

        if getattr(self, 'current_header_cover_id', None) == str(cover_id):
            self.set_header_image(pix)
            cid = str(cover_id)
            import threading
            def _fetch_full(cid=cid):
                if not getattr(self, 'client', None):
                    return
                try:
                    from player.components.cover_cache import CoverCache
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

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("player/tabs/artists/artist_grid.qml")))
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

        if not artists:
            # Fetch failed/empty (e.g. timeout under concurrent load) — drop the
            # "loaded" mark so the next viewport check retries this chunk
            # instead of leaving its rows stuck as placeholders forever.
            if hasattr(self, 'loaded_chunks'):
                self.loaded_chunks.discard(chunk_index)
            return

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