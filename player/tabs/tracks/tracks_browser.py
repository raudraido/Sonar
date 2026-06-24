import re
import os
from player.mixins.visuals import scrollbar_css, install_scroll_reveal, menu_hover, apply_menu_palette, resolve_menu_hover, SmoothScroller, SpinRefreshButton
import time
import json
import math
import threading

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
                             QHeaderView, QAbstractItemView, QMenu, QPushButton,
                             QHBoxLayout, QLabel, QProgressBar, QApplication, QLineEdit,
                             QStyledItemDelegate, QStyle, QStyleOptionHeader, QStyleOptionViewItem,
                             QSpacerItem, QSizePolicy, QToolButton, QTreeWidget, QFrame,
                             QListWidget, QListWidgetItem, QCheckBox, QScrollArea)



from PyQt6.QtCore import (Qt, pyqtSignal, pyqtSlot, pyqtProperty, QTimer, QModelIndex, QEvent, QPoint, QRect,
                          QPropertyAnimation, QEasingCurve, QSize, QParallelAnimationGroup,
                          QRectF, QThread, QSettings, QObject, QPointF, QAbstractListModel, QUrl)

from PyQt6.QtGui import QAction, QColor, QCursor, QFontMetrics, QIcon, QPainter, QPixmap, QPainterPath, QFont, QPen

from player import resource_path
from player.components.shared_widgets import PaginationFooter, SmartSearchContainer, TrackInfoDialog
from player.widgets import QMLGridWrapper, TrackThumbProvider, AlbumIconProvider
from player.qml_search import SearchController, SearchKeyFilter, set_window_shortcuts_enabled
from PyQt6.QtQuickWidgets import QQuickWidget

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import OrderedDict

import os as _os
import platform as _platform
from datetime import datetime as _datetime

_PLATFORM_WINDOWS = _platform.system() == "Windows"
_COVER_WORKERS = min(6, (_os.cpu_count() or 2) + 2)
_PLATFORM_LINUX = _platform.system() == "Linux"

def _trim_glibc_heap():
    """Ask glibc to return freed memory to the OS (Linux only)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass



class MiddleClickScroller(QObject):
    """Adds Web-Browser style Middle-Click Omni-Scrolling to any QScrollArea or QTreeWidget."""
    def __init__(self, target_scroll_area):
        super().__init__(target_scroll_area)
        self.target = target_scroll_area
        self.viewport = target_scroll_area.viewport()
        
        self.is_scrolling = False
        self.origin_y = 0
        self.exact_y = 0.0 
        self.click_time = 0
        
        self.timer = QTimer(self)
        self.timer.start(7) 
        self.timer.timeout.connect(self._process_scroll)
        
        self.viewport.installEventFilter(self)
        # 👇 🟢 THE FIX: Tell the event filter to monitor the main widget too!
        self.target.installEventFilter(self)
        

    def eventFilter(self, obj, event):
        # 👇 🟢 THE FIX: If the tab is hidden, instantly kill the scrolling!
        if obj == self.target and event.type() == QEvent.Type.Hide:
            if self.is_scrolling:
                self._stop()
            return False

        if obj == self.viewport:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    if self.is_scrolling:
                        self._stop()
                    else:
                        self._start(event.globalPosition().toPoint().y())
                    return True 
                elif self.is_scrolling:
                    self._stop() 
                    return True
                    
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.MiddleButton and self.is_scrolling:
                    import time
                    if time.time() - self.click_time > 0.2:
                        self._stop()
                    return True
                    
        return super().eventFilter(obj, event)

    def _start(self, start_y):
        import time
        self.is_scrolling = True
        self.origin_y = start_y
        self.click_time = time.time()
        # 🟢 Grab the exact starting position as a float
        self.exact_y = float(self.target.verticalScrollBar().value()) 
        self.viewport.setCursor(Qt.CursorShape.SizeVerCursor) 

    def _stop(self):
        self.is_scrolling = False
        self.viewport.unsetCursor()

    def _process_scroll(self):
        if not self.is_scrolling: return
            
        # 👇 🟢 THE BULLETPROOF KILL-SWITCH
        # If the tab hides, the window loses focus, or you left/right click ANYTHING, kill it instantly!
        from PyQt6.QtWidgets import QApplication
        buttons = QApplication.mouseButtons()
        if not self.target.isVisible() or not QApplication.activeWindow() or (buttons & Qt.MouseButton.LeftButton) or (buttons & Qt.MouseButton.RightButton):
            self._stop()
            return
            
        current_y = QCursor.pos().y()
        delta = current_y - self.origin_y
        
        deadzone = 15
        if abs(delta) < deadzone: return
            
        speed = (abs(delta) - deadzone) * 0.03
        direction = 1 if delta > 0 else -1
        
        self.exact_y += (speed * direction)
        
        vbar = self.target.verticalScrollBar()
        vbar.setValue(int(self.exact_y))

class LRUCache:
    def __init__(self, max_size=50):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key, default=None):
        if key not in self.cache:
            return default
        self.cache.move_to_end(key) # Mark as recently used
        return self.cache[key]

    def set(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False) # Delete the oldest item

    def __contains__(self, key):
        return key in self.cache

class TBCoverWorker(QThread):

    cover_ready = pyqtSignal(str, bytes)
    
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.queue = []
        self.running = True

    def _download_task(self, cid):
        """Runs in parallel on one of the 10 background threads"""
        # 🟢 THE FIX: Request THUMB_SIZE instead of FULL_SIZE for the tiny list icons!
        try:
            from player.components.cover_cache import THUMB_SIZE
            return self.client.get_cover_art(cid, size=THUMB_SIZE)
        except Exception: return None

    def run(self):
        with ThreadPoolExecutor(max_workers=_COVER_WORKERS) as executor:
            futures = {}
            while self.running:
                # 🟢 Prioritize the END of the queue (newest requests)
                while self.queue and len(futures) < _COVER_WORKERS:
                    cid = self.queue.pop(0) # Pops from the right!
                    futures[executor.submit(self._download_task, cid)] = cid

                if not futures:
                    time.sleep(0.1)
                    continue

                done, _ = wait(futures.keys(), timeout=0.1, return_when=FIRST_COMPLETED)
                
                for f in done:
                    cid = futures.pop(f)
                    try:
                        data = f.result()
                        if data:
                            # 🟢 THE FIX: Save it to the thumb cache, not the full cache!
                            from player.components.cover_cache import CoverCache
                            CoverCache.instance().save_thumb(cid, data)
                            self.cover_ready.emit(cid, data)
                    except Exception:
                        pass

class LiveTrackWorker(QThread):
    results_ready = pyqtSignal(list, int, int, int)

    def __init__(self, client, query_text, page, page_size, is_album_mode, album_id, known_total=0, sort_field="title", sort_order="ASC", server_filters=None):
        super().__init__()
        self.client = client
        self.query_text = query_text
        self.page = page
        self.page_size = page_size
        self.is_album_mode = is_album_mode
        self.album_id = album_id
        self.known_total = known_total
        self.sort_field = sort_field
        self.sort_order = sort_order
        self.server_filters = server_filters or {}
        self.is_cancelled = False

    def run(self):
        try:
            if not self.client: return

            if self.is_album_mode and self.album_id:
                # 🟢 SINGLE ALBUM MODE (Sort the small batch locally)
                tracks = self.client.get_album_tracks(self.album_id)
                
                # 🛑 Exit immediately if the thread was cancelled!
                if self.is_cancelled: return
                
                if self.query_text:
                    query = self.query_text.lower()
                    tracks = [t for t in tracks if query in str(t.get('title', '')).lower() or query in str(t.get('artist', '')).lower()]
                
                # 🟢 THE FIX: Force perfect Album order! 
                # This completely ignores the global UI sort state so albums are never scrambled.
                def safe_album_sort(x):
                    try: d = int(x.get('discNumber') or 1)
                    except: d = 1
                    try: t = int(x.get('trackNumber') or 0)
                    except: t = 0
                    return (d, t)
                    
                tracks.sort(key=safe_album_sort)

                if not self.is_cancelled:
                    self.results_ready.emit(tracks, len(tracks), 1, 1)

            else:
                start = (self.page - 1) * self.page_size
                end = start + self.page_size

                # One call for both search and browse — title= param filters server-side,
                # X-Total-Count header gives exact total. Same as Feishin /api/song approach.
                tracks, total_items = self.client.get_tracks_native_page(
                    sort_by=self.sort_field,
                    order=self.sort_order,
                    start=start,
                    end=end,
                    query=self.query_text,
                    server_filters=self.server_filters or None,
                )
                    
                # 🛑 Exit immediately if the thread was cancelled!
                if self.is_cancelled: return

                total_pages = max(1, math.ceil(total_items / self.page_size)) if total_items > 0 else 1

                if not self.is_cancelled:
                    self.results_ready.emit(tracks, total_items, total_pages, self.page)

        except Exception as e:
            print(f"[LiveTrackWorker] Error: {e}")
            if not self.is_cancelled:
                self.results_ready.emit([], 0, 1, 1)

class FilterValuesWorker(QThread):
    """Fetches ALL tracks for the current query, collects distinct column values + server ID maps."""
    values_ready = pyqtSignal(dict, dict)  # col_values, id_maps

    _COL_FIELD = {
        2: 'title', 3: 'artist', 4: 'album',
        5: 'year',  6: 'genre', 7: 'starred',
        8: 'play_count', 9: 'duration',
    }

    def __init__(self, client, query_text, is_album_mode, album_id):
        super().__init__()
        self.client = client
        self.query_text = query_text
        self.is_album_mode = is_album_mode
        self.album_id = album_id
        self.is_cancelled = False

    def run(self):
        try:
            col_values = {}
            id_maps = {}

            if self.is_album_mode and self.album_id:
                # Album mode: track data is small, fetch it directly
                tracks = self.client.get_album_tracks(self.album_id)
                if self.is_cancelled: return
                for col, field in self._COL_FIELD.items():
                    vals = set()
                    for t in tracks:
                        v = "True" if col == 7 and t.get('starred') else str(t.get(field, '') or '').strip()
                        if v: vals.add(v)
                    col_values[col] = sorted(vals, key=lambda x: x.lower())
            else:
                # Use dedicated fast endpoints instead of fetching all songs

                # Genres (col 6) — /api/genre
                try:
                    genre_map = self.client.get_genres_native()
                    id_maps[6] = genre_map
                    col_values[6] = sorted(genre_map.keys(), key=str.lower)
                except Exception: pass
                if self.is_cancelled: return

                # Artists (col 3) — /api/artist
                try:
                    artist_map = self.client.get_all_artists_native()
                    id_maps[3] = artist_map
                    col_values[3] = sorted(artist_map.keys(), key=str.lower)
                except Exception: pass
                if self.is_cancelled: return

                # Albums (col 4) — /api/album
                try:
                    album_map = self.client.get_all_albums_native()
                    id_maps[4] = album_map
                    col_values[4] = sorted(album_map.keys(), key=str.lower)
                except Exception: pass
                if self.is_cancelled: return

                # Starred (col 7) — static values
                col_values[7] = ["False", "True"]

                # Year, title, play_count, duration (cols 2,5,8,9):
                # These have no dedicated endpoint; collect from a sample page of songs
                try:
                    sample_tracks, _ = self.client.get_tracks_native_page(
                        sort_by="title", order="ASC",
                        start=0, end=500,
                        query=self.query_text or "",
                    )
                    if self.is_cancelled: return
                    for col, field in [(2, 'title'), (5, 'year'), (8, 'play_count'), (9, 'duration'), (10, 'trackNumber')]:
                        vals = set()
                        for t in sample_tracks:
                            v = str(t.get(field, '') or '').strip()
                            if v: vals.add(v)
                        col_values[col] = sorted(vals, key=lambda x: x.lower())
                except Exception: pass

            if not self.is_cancelled:
                self.values_ready.emit(col_values, id_maps)
        except Exception as e:
            print(f"[FilterValuesWorker] {e}")


# --- SMART DELEGATES ---

class _CheckableListWidget(QListWidget):
    """QListWidget that toggles checkboxes on any click within the row (text or checkbox area)."""
    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            new = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
            item.setCheckState(new)
            self.itemClicked.emit(item)  # needed so _on_list_item_clicked fires
            event.accept()
            return
        super().mousePressEvent(event)


def _checkmark_svg_path(color: str) -> str:
    import tempfile
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'>"
        f"<polyline points='1.5,6 4.5,9.5 10.5,2.5' stroke='{color}' stroke-width='2'"
        f" fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    )
    path = os.path.join(tempfile.gettempdir(), f"sonar_check_{color.lstrip('#')}.svg")
    with open(path, 'w') as f:
        f.write(svg)
    return path.replace('\\', '/')


class ColumnFilterPopup(QFrame):
    """Excel-style column filter popup: sort rows + search box + multi-select checklist."""
    filters_applied = pyqtSignal(int, set)  # col, selected values
    sort_requested  = pyqtSignal(int, str)  # col, "ASC" or "DESC"

    # Columns that map to server-side ID lists — Navidrome limits these
    ID_FILTER_COLS = {3, 4, 6}
    MAX_ID_FILTER_VALUES = 10

    _SHADOW_PAD = 24   # shadow area around the visible popup

    def __init__(self, col, values, active_values, up_icon, down_icon, accent_color="#cccccc", parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.col = col
        self.active_values = set(active_values) if active_values else set()
        self.all_values = sorted(values, key=lambda v: str(v).lower())
        self.setFrameShape(QFrame.Shape.NoFrame)
        _theme = getattr(parent.window() if parent else None, 'theme', None)
        _bg  = getattr(_theme, 'main_panel_bg',       '13,13,13')
        _bc  = getattr(_theme, 'border_color',        '#2a2a2a')
        _fg  = getattr(_theme, 'font_color_primary',  '#dddddd')
        _fg2 = getattr(_theme, 'font_color_secondary','#999999')
        from player.mixins.visuals import resolve_menu_hover
        _hov = resolve_menu_hover(_theme)
        self._paint_bg = QColor(*[int(x) for x in _bg.split(',')])
        if _theme and not getattr(_theme, 'auto_border_from_accent', True):
            self._paint_bc = QColor(getattr(_theme, 'manual_border_color', '#2a2a2a'))
        else:
            self._paint_bc = QColor(_bc)
        self.setStyleSheet(f"""
            ColumnFilterPopup {{
                background: rgb({_bg}); border: 1px solid {_bc}; border-radius: 6px;
            }}
            QLineEdit {{
                background: rgb({_bg}); color: {_fg2}; border: 1px solid {_bc};
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {_bc}; }}
            QListWidget {{
                background: transparent; border: none; color: {_fg2}; font-size: 13px;
            }}
            QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
            QListWidget::item:hover {{ background: {_hov}; }}
            QListWidget::item:selected {{ background: {_hov}; color: {_fg2}; }}
            QListWidget::indicator {{
                width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid {_bc}; background: rgb({_bg});
            }}
            QListWidget::indicator:checked {{
                background: rgb({_bg});
                image: url("{_checkmark_svg_path(accent_color)}");
            }}
            QPushButton {{
                background: transparent; color: {_fg2}; border: 1px solid {_bc};
                border-radius: 4px; padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {_hov}; }}
            QFrame#sort_row {{ background: transparent; }}
            QFrame#sort_row:hover {{ background: {_hov}; border-radius: 3px; }}
            {scrollbar_css(accent_color)}
        """)
        pad = self._SHADOW_PAD
        self.setFixedWidth(240 + 2 * pad)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(pad + 8, pad + 8, pad + 8, pad + 8)
        layout.setSpacing(4)

        icon_size = 14
        clear_filter_icon = QIcon(resource_path("img/filter_off-2.png"))
        has_filter = bool(active_values)

        def _make_action_row(icon, label, callback, enabled=True, tint=True, tint_color=None, text_color=None):
            row = QFrame()
            row.setObjectName("sort_row")
            if enabled:
                row.setCursor(Qt.CursorShape.PointingHandCursor)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(4, 4, 4, 4)
            hl.setSpacing(6)
            lbl_icon = QLabel()
            px = icon.pixmap(icon_size, icon_size)
            if tint:
                c = QColor(tint_color) if tint_color else QColor("#ffffff")
                tinted_px = QPixmap(px.size())
                tinted_px.fill(Qt.GlobalColor.transparent)
                p2 = QPainter(tinted_px)
                p2.setOpacity(1.0 if enabled else 0.3)
                p2.drawPixmap(0, 0, px)
                p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                p2.fillRect(tinted_px.rect(), c)
                p2.end()
                px = tinted_px
            else:
                if not enabled:
                    dim = QPixmap(px.size())
                    dim.fill(Qt.GlobalColor.transparent)
                    p2 = QPainter(dim)
                    p2.setOpacity(0.3)
                    p2.drawPixmap(0, 0, px)
                    p2.end()
                    px = dim
            lbl_icon.setPixmap(px)
            lbl_icon.setFixedSize(icon_size, icon_size)
            color = (text_color or _fg2) if enabled else "#555"
            lbl_text = QLabel(label)
            lbl_text.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
            hl.addWidget(lbl_icon)
            hl.addWidget(lbl_text)
            hl.addStretch()
            if enabled:
                row.mousePressEvent = lambda e: callback()
            return row

        layout.addWidget(_make_action_row(up_icon,   "Sort ascending",  lambda: self._sort("ASC"),  tint_color=accent_color))
        layout.addWidget(_make_action_row(down_icon, "Sort descending", lambda: self._sort("DESC"), tint_color=accent_color))
        layout.addWidget(_make_action_row(clear_filter_icon, "Clear filter",   self._clear_filter, enabled=has_filter, tint=has_filter, tint_color="#ff4444" if has_filter else None, text_color="#ff4444" if has_filter else None))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_bc};")
        layout.addWidget(sep)

        layout.setSpacing(6)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search…")
        from PyQt6.QtGui import QPalette as _Pal
        _pal = self.search_box.palette()
        _pal.setColor(_Pal.ColorRole.PlaceholderText, QColor(_fg2))
        self.search_box.setPalette(_pal)
        self.search_box.textChanged.connect(self._filter_list)
        self.search_box.returnPressed.connect(self._apply)
        layout.addWidget(self.search_box)

        self.list_widget = _CheckableListWidget()
        self.list_widget.setFixedHeight(200)
        layout.addWidget(self.list_widget)

        # Warning shown when too many ID-based values are selected
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #f0a030; font-size: 11px; padding: 2px 4px;")
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_ok     = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok.clicked.connect(self._apply)
        self.btn_cancel.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_ok)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self._populate(active_values)
        self.adjustSize()
        self.setMinimumHeight(0)

    SELECT_ALL_TEXT = "(Select All)"

    def _populate(self, active_values):
        self._has_active_filter = bool(active_values)
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        # "Select All" header item
        all_checked = not active_values or len(active_values) == len(self.all_values)
        sa = QListWidgetItem(self.SELECT_ALL_TEXT)
        sa.setFlags(sa.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        sa.setCheckState(Qt.CheckState.Checked if all_checked else Qt.CheckState.Unchecked)
        font = sa.font()
        font.setBold(True)
        sa.setFont(font)
        self.list_widget.addItem(sa)

        # "Add current selection to filter" — styled like Select All, hidden until needed
        add_item = QListWidgetItem(self.ADD_TO_FILTER_TEXT)
        add_item.setFlags(add_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        add_item.setCheckState(Qt.CheckState.Unchecked)
        f2 = add_item.font()
        f2.setBold(True)
        add_item.setFont(f2)
        self.list_widget.addItem(add_item)
        add_item.setHidden(True)  # must be AFTER addItem, otherwise Qt ignores it

        for v in self.all_values:
            item = QListWidgetItem(str(v))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = not active_values or v in active_values
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
            # setHidden must be called AFTER addItem, otherwise Qt ignores it
            if active_values and not checked:
                item.setHidden(True)

        self.list_widget.blockSignals(False)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.itemClicked.connect(self._on_list_item_clicked)

    def _on_item_changed(self, changed_item):
        # Ignore the "Add to filter" item — handled separately via click
        if changed_item.text() == self.ADD_TO_FILTER_TEXT:
            return
        self.list_widget.blockSignals(True)
        if changed_item.text() == self.SELECT_ALL_TEXT:
            state = changed_item.checkState()
            for i in range(1, self.list_widget.count()):
                it = self.list_widget.item(i)
                if it.text() != self.ADD_TO_FILTER_TEXT and (it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    it.setCheckState(state)
        else:
            all_checked = all(
                self.list_widget.item(i).checkState() == Qt.CheckState.Checked
                for i in range(1, self.list_widget.count())
                if self.list_widget.item(i).text() != self.ADD_TO_FILTER_TEXT
                and (self.list_widget.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable)
            )
            self.list_widget.item(0).setCheckState(
                Qt.CheckState.Checked if all_checked else Qt.CheckState.Unchecked
            )
        self.list_widget.blockSignals(False)
        self._update_warning()

    def _on_list_item_clicked(self, item):
        pass  # handled in _apply

    def _update_warning(self):
        if self.col not in self.ID_FILTER_COLS:
            return
        checked_count = sum(
            1 for i in range(1, self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        )
        total = self.list_widget.count() - 1
        # No filter (all checked) or single value = fine
        if checked_count == total or checked_count <= self.MAX_ID_FILTER_VALUES:
            self.warning_label.hide()
        else:
            self.warning_label.setText(
                f"⚠ Server supports up to {self.MAX_ID_FILTER_VALUES} values "
                f"({checked_count} selected — results may be incomplete)."
            )
            self.warning_label.show()
        self.adjustSize()

    ADD_TO_FILTER_TEXT = "(Add current selection to filter)"

    def _filter_list(self, text):
        q = text.lower()
        self.list_widget.item(0).setHidden(False)  # always show "Select All"

        # First pass: show/hide value items, detect if any visible result is outside active filter
        has_new = False
        for i in range(1, self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.text() == self.ADD_TO_FILTER_TEXT:
                continue
            if q:
                hidden = q not in item.text().lower()
                item.setHidden(hidden)
                if not hidden:
                    # Pre-check visible results so user sees them selected
                    self.list_widget.blockSignals(True)
                    item.setCheckState(Qt.CheckState.Checked)
                    self.list_widget.blockSignals(False)
                    if item.text() not in self.active_values:
                        has_new = True
            else:
                unchecked = item.checkState() == Qt.CheckState.Unchecked
                item.setHidden(self._has_active_filter and unchecked)

        # "Add to filter" visible ONLY when: filter is active AND user typed a query AND
        # at least one visible result is not already in the active filter
        show_add = self._has_active_filter and bool(q) and has_new
        for i in range(1, self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.text() == self.ADD_TO_FILTER_TEXT:
                item.setHidden(not show_add)
                if show_add:
                    item.setCheckState(Qt.CheckState.Unchecked)
                break
                break

    def _apply(self):
        q = self.search_box.text().strip()

        # Check if "Add to filter" is checked — merge mode
        add_checked = False
        for i in range(1, self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.text() == self.ADD_TO_FILTER_TEXT:
                add_checked = it.checkState() == Qt.CheckState.Checked
                break

        if add_checked:
            new_values = set(
                self.list_widget.item(i).text()
                for i in range(1, self.list_widget.count())
                if not self.list_widget.item(i).isHidden()
                and self.list_widget.item(i).text() != self.ADD_TO_FILTER_TEXT
            )
            checked = self.active_values | new_values
        elif q:
            checked = set(
                self.list_widget.item(i).text()
                for i in range(1, self.list_widget.count())
                if not self.list_widget.item(i).isHidden()
                and self.list_widget.item(i).text() != self.ADD_TO_FILTER_TEXT
            )
        else:
            checked = set(
                self.list_widget.item(i).text()
                for i in range(1, self.list_widget.count())
                if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
                and self.list_widget.item(i).text() != self.ADD_TO_FILTER_TEXT
            )
            if len(checked) == len(self.all_values):
                checked = set()

        self.filters_applied.emit(self.col, checked)
        self.close()

    def _add_to_filter(self):
        """Merge all visible search results into the existing active filter."""
        new_values = set(
            self.list_widget.item(i).text()
            for i in range(1, self.list_widget.count())
            if not self.list_widget.item(i).isHidden()
            and self.list_widget.item(i).text() != self.ADD_TO_FILTER_TEXT
        )
        merged = self.active_values | new_values
        self.filters_applied.emit(self.col, merged)
        self.close()

    def _sort(self, order):
        self.sort_requested.emit(self.col, order)
        self.close()

    def _clear_filter(self):
        self.filters_applied.emit(self.col, set())
        self.close()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter as _P
        from PyQt6.QtCore import QRectF
        p = _P(self)
        p.setRenderHint(_P.RenderHint.Antialiasing)
        pad = self._SHADOW_PAD
        content = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        # Shadow: same style as ShadowContextMenu
        BLUR = 22; OY = 8; MAX_A = 45
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(16, 0, -1):
            t = i / 16; alpha = int(MAX_A * (1 - t) ** 2); ex = BLUR * t
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(content.adjusted(-ex*.7, -ex*.4+OY*(1-t), ex*.7, ex+OY*t),
                              8 + ex*.2, 8 + ex*.2)
        p.setPen(self._paint_bc)
        p.setBrush(self._paint_bg)
        p.drawRoundedRect(content, 8, 8)
        p.end()


class SmartSortHeader(QHeaderView):
    section_drag_finished = pyqtSignal()
    filter_clicked = pyqtSignal(int, QRect)  # col, icon rect in global coords
    sort_clicked   = pyqtSignal(int)          # col — for sort-only columns

    SORT_COLS = {1, 2, 8, 9, 10, 11, 12}  # TRACK, TITLE, PLAYS, LENGTH, NO., DATE ADDED, BPM — show sort icon, not filter

    FILTER_ICON_SIZE = 14

    def setGeometry(self, *args):
        # Force full width — ignore the narrowed rect Qt passes when a scrollbar is visible
        if len(args) == 1:
            r = args[0]
        else:
            from PyQt6.QtCore import QRect
            r = QRect(*args)
        if self.parentWidget():
            r.setWidth(self.parentWidget().width())
        super().setGeometry(r)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(30)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setStretchLastSection(False)
        self.up_icon   = QIcon(resource_path("img/filter_up.png"))
        self.down_icon = QIcon(resource_path("img/filter_down.png"))
        self.filter_icon     = QIcon(resource_path("img/filter.png"))
        self._active_filter_cols = set()
        self._filter_icon_rects = {}
        self._pending_click_col = None
        self._pending_click_pos = None
        self.album_mode = False
        self.filter_sort_disabled = False
        self._accent = QColor('#555555')
        self.viewport().setMouseTracking(True)

    def set_accent(self, color: str):
        self._accent = QColor(color)
        self.viewport().update()

    def _theme(self):
        w = self.window() if hasattr(self, 'window') else None
        return getattr(w, 'theme', None)
    def _secondary_px(self):
        t = self._theme(); return getattr(t, 'font_size_secondary', 12) if t else 12
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#555555') if t else '#555555'
    def _border_qcolor(self):
        t = self._theme()
        if t is None:
            return QColor('#2a2a2a')
        if getattr(t, 'auto_border_from_accent', True):
            return QColor(getattr(t, 'accent', '#cccccc')).darker(250)
        return QColor(getattr(t, 'manual_border_color', '#2a2a2a'))

    _CLICK_THRESHOLD = 4  # pixels — more than this = drag, not click

    def _is_resize_zone(self, pos):
        grip = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderGripMargin) + 2
        for i in range(self.count()):
            boundary = self.sectionViewportPosition(i) + self.sectionSize(i)
            if abs(pos.x() - boundary) <= grip:
                return True
        return False

    def set_active_filters(self, cols):
        self._active_filter_cols = set(cols)
        self.viewport().update()

    def _filter_icon_rect(self, logical_index):
        """Return the QRect for the filter icon in viewport coordinates (cached from last paint)."""
        return self._filter_icon_rects.get(logical_index, QRect())

    def mouseMoveEvent(self, event):
        if (self._pending_click_pos is not None
                and abs(event.pos().x() - self._pending_click_pos.x()) > self._CLICK_THRESHOLD):
            self._pending_click_col = None
            self._pending_click_pos = None
        if self.filter_sort_disabled:
            self.unsetCursor()
            return super().mouseMoveEvent(event)
        if not self._is_resize_zone(event.pos()):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        _is_click = self._pending_click_col is not None
        super().mouseReleaseEvent(event)
        if not _is_click:
            self.section_drag_finished.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._pending_click_col is not None:
            col = self._pending_click_col
            self._pending_click_col = None
            self._pending_click_pos = None
            if col in self.SORT_COLS:
                self.sort_clicked.emit(col)
            else:
                sec_pos = self.sectionViewportPosition(col)
                sec_w   = self.sectionSize(col)
                global_tl = self.viewport().mapToGlobal(QPoint(sec_pos, self.viewport().height()))
                global_rect = QRect(global_tl, QSize(sec_w, self.viewport().height()))
                self.filter_clicked.emit(col, global_rect)
        else:
            self._pending_click_col = None
            self._pending_click_pos = None

    def mousePressEvent(self, event):
        if not self.filter_sort_disabled and event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            logical = self.logicalIndexAt(pos)
            if logical > 0 and not self._is_resize_zone(pos):
                self._pending_click_col = logical
                self._pending_click_pos = pos
        super().mousePressEvent(event)

    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, Qt.GlobalColor.transparent)

        text = self.model().headerData(logicalIndex, Qt.Orientation.Horizontal) or ''
        f = QFont(); f.setPixelSize(self._secondary_px()); f.setBold(True)
        fm = QFontMetrics(f)
        painter.setFont(f)
        painter.setPen(QColor(self._secondary_color()))

        centered_cols = {0, 5, 7, 8, 9, 10, 11, 12}

        # Pre-calculate icon visibility so text placement can account for it
        show_icon = logicalIndex != 0
        is_sort_shown = self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex
        is_active = logicalIndex in self._active_filter_cols
        # Active filters no longer show an icon — accent text color is used instead
        show_icon_now = (not self.album_mode) and show_icon and is_sort_shown

        sz = self.FILTER_ICON_SIZE
        text_w = fm.horizontalAdvance(text)

        painter.setPen(self._accent if is_active else QColor(self._secondary_color()))

        if logicalIndex in centered_cols and show_icon_now:
            # Draw text + icon as a centered group so the icon never overlaps text
            content_w = text_w + 4 + sz
            group_x = rect.left() + 4 + max(0, (rect.width() - 8 - content_w) // 2)
            painter.drawText(
                QRect(group_x, rect.top(), text_w, rect.height() - 8),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
            fx = group_x + text_w + 4
        else:
            h_align = Qt.AlignmentFlag.AlignHCenter if logicalIndex in centered_cols else Qt.AlignmentFlag.AlignLeft
            painter.drawText(rect.adjusted(4, 0, -4, -8), h_align | Qt.AlignmentFlag.AlignBottom, text)
            fx = rect.left() + 4 + text_w + 4

        fy = rect.bottom() - sz - 8

        # Bottom border
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Column separator
        if logicalIndex != 0 and self.visualIndex(logicalIndex) < self.count() - 1:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border_qcolor(), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(rect.right(), rect.top() + 8, rect.right(), rect.bottom() - 8)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if show_icon_now:
            icon = self.down_icon if self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else self.up_icon
            px = icon.pixmap(sz, sz)
            tint = self._accent

            tinted = QPixmap(px.size())
            tinted.fill(Qt.GlobalColor.transparent)
            p2 = QPainter(tinted)
            p2.drawPixmap(0, 0, px)
            p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p2.fillRect(tinted.rect(), tint)
            p2.end()
            painter.drawPixmap(fx, int(fy), tinted)
            self._filter_icon_rects[logicalIndex] = QRect(fx, int(fy), sz, sz)
        elif show_icon:
            self._filter_icon_rects[logicalIndex] = QRect()

        painter.restore()

class _TrackTree(QTreeWidget):
    """QTreeWidget with inset separators and inset hover/selection highlights."""
    _sep_pen  = QPen(QColor(255, 255, 255, 13), 1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hov_row = -1
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        SmoothScroller(self)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == QEvent.Type.MouseMove:
                row = self.indexAt(event.pos()).row()
                if row != self._hov_row:
                    self._hov_row = row
                    self.viewport().update()
            elif t == QEvent.Type.Leave:
                if self._hov_row != -1:
                    self._hov_row = -1
                    self.viewport().update()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._fix_scrollbar_geometry)

    def _fix_scrollbar_geometry(self):
        sb = self.verticalScrollBar()
        hdr_h = self.header().height() if not self.isHeaderHidden() else 0
        geo = sb.geometry()
        if geo.y() != hdr_h:
            sb.setGeometry(geo.x(), hdr_h, geo.width(), self.height() - hdr_h)

    def drawRow(self, painter, option, index):
        sb = self.verticalScrollBar()
        if getattr(self.header(), 'album_mode', False):
            ext_sb = getattr(self, '_ext_sb', None)
            right_inset = max(8 - (ext_sb.width() if (ext_sb and ext_sb.isVisible()) else 0), 0)
            rect = option.rect.adjusted(0, 0, -right_inset, 0)
        else:
            right_inset = max(8 - (sb.width() if sb.isVisible() else 0), 0)
            rect = option.rect.adjusted(8, 0, -right_inset, 0)
        is_sel = self.selectionModel().isRowSelected(index.row(), index.parent())
        is_hov = index.row() == self._hov_row
        item = self.itemFromIndex(index)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        track_id = (data.get('data', {}).get('id') if isinstance(data, dict) else None)
        playing_id = getattr(self, 'current_playing_id', None)
        is_playing = bool(track_id and playing_id and str(track_id) == str(playing_id))
        if is_sel or is_hov or is_playing:
            _theme = getattr(self.window(), 'theme', None)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(resolve_menu_hover(_theme)))
            if is_sel:
                sm = self.selectionModel()
                par = index.parent()
                prev_sel = sm.isRowSelected(index.row() - 1, par)
                next_sel = sm.isRowSelected(index.row() + 1, par)
                r = 6.0
                f = QRectF(rect)
                x, y, w, h = f.x(), f.y(), f.width(), f.height()
                path = QPainterPath()
                path.moveTo(x + (r if not prev_sel else 0), y)
                path.lineTo(x + w - (r if not prev_sel else 0), y)
                if not prev_sel:
                    path.arcTo(x + w - 2*r, y, 2*r, 2*r, 90, -90)
                else:
                    path.lineTo(x + w, y)
                path.lineTo(x + w, y + h - (r if not next_sel else 0))
                if not next_sel:
                    path.arcTo(x + w - 2*r, y + h - 2*r, 2*r, 2*r, 0, -90)
                else:
                    path.lineTo(x + w, y + h)
                path.lineTo(x + (r if not next_sel else 0), y + h)
                if not next_sel:
                    path.arcTo(x, y + h - 2*r, 2*r, 2*r, 270, -90)
                else:
                    path.lineTo(x, y + h)
                path.lineTo(x, y + (r if not prev_sel else 0))
                if not prev_sel:
                    path.arcTo(x, y, 2*r, 2*r, 180, -90)
                else:
                    path.lineTo(x, y)
                path.closeSubpath()
                painter.drawPath(path)
            else:
                painter.drawRoundedRect(QRectF(rect), 6, 6)
            painter.restore()
        super().drawRow(painter, option, index)


class NoFocusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # Remove the focus state so the dotted line/blue border never appears
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)

class SkeletonDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, base_color="#282828"):
        super().__init__(parent)
        self._phase = 0.0
        self.set_base_color(base_color)
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)   # ~25 fps

    def _tick(self):
        import math
        self._phase = (self._phase + 0.04) % 1.0
        p = self.parent()
        if p:
            p.viewport().update()

    def set_base_color(self, hex_color: str):
        c = QColor(hex_color)
        self._base_r = min(255, c.red()   + 18)
        self._base_g = min(255, c.green() + 18)
        self._base_b = min(255, c.blue()  + 18)

    def paint(self, painter, option, index):
        import math
        phase  = (self._phase + index.row() * 0.18) % 1.0
        factor = 1.0 + 0.12 * math.sin(phase * 2 * math.pi)
        r = min(255, int(self._base_r * factor))
        g = min(255, int(self._base_g * factor))
        b = min(255, int(self._base_b * factor))
        pill_color = QColor(r, g, b)

        painter.save()
        painter.setPen(QColor(255, 255, 255, 10))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        rect = option.rect
        col = index.column()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pill_color)
        
        h = 12
        y = rect.top() + (rect.height() - h) // 2
        x = rect.left() + 10
        
        if col == 1:
            # Draw Cover Square + 2 Text Lines for the Combined Track column
            cover_size = 40 if rect.height() < 60 else 65
            cover_y = rect.top() + (rect.height() - cover_size) // 2
            painter.drawRoundedRect(int(x), int(cover_y), cover_size, cover_size, 6, 6)
            painter.drawRoundedRect(int(x + cover_size + 15), int(cover_y + cover_size//2 - 12), 150, 10, 5, 5)
            painter.drawRoundedRect(int(x + cover_size + 15), int(cover_y + cover_size//2 + 6), 90, 8, 4, 4)
            painter.restore()
            return
        elif col == 2: w = 180
        elif col == 3: w = 140
        elif col == 4: w = 160
        elif col == 5: w = 40
        elif col == 6: w = 90
        elif col == 7: w = 16; x = rect.left() + (rect.width() - w) // 2
        elif col == 8: w = 30
        elif col == 9: w = 35; x = rect.left() + (rect.width() - w) // 2
        elif col == 10: w = 25; x = rect.left() + (rect.width() - w) // 2
        else: w = 50
        
        w = min(w, max(10, rect.width() - 20)) 
        painter.drawRoundedRect(int(x), int(y), int(w), int(h), h//2, h//2)
        painter.restore()


# --- DELEGATE: HEART / FAVORITE ---

class HeartDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filled_pix = QPixmap()
        self._empty_pix  = QPixmap()
        self._rebuild_pixmaps("#E91E63")

    def set_master_color(self, color: str):
        pass  # heart colors are fixed, not accent-dependent

    def _rebuild_pixmaps(self, accent: str):
        self._filled_pix = self._tinted_pix(resource_path("img/heart_filled.png"), "#E91E63")
        self._empty_pix  = self._tinted_pix(resource_path("img/heart.png"), "#555555")

    @staticmethod
    def _tinted_pix(path: str, color: str) -> QPixmap:
        base = QPixmap(path)
        if base.isNull():
            return QPixmap()
        base = base.scaled(QSize(16, 16), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.drawPixmap(0, 0, base)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pix.rect(), QColor(color))
        p.end()
        return pix

    def paint(self, painter, option, index):
        is_fav = index.data() == '♥'
        pix = self._filled_pix if is_fav else self._empty_pix
        if pix.isNull():
            super().paint(painter, option, index)
            return
        painter.save()
        px = option.rect.center().x() - pix.width() // 2
        py = option.rect.center().y() - pix.height() // 2
        painter.drawPixmap(px, py, pix)
        painter.restore()


# --- DELEGATE 1: SINGLE LINK (For Albums) ---

class LinkDelegate(QStyledItemDelegate):
    clicked = pyqtSignal(QModelIndex)

    def __init__(self, parent=None, center_text=False):
        super().__init__(parent)
        self.hovered_index = None
        self.master_color = QColor("#cccccc")
        self.center_text = center_text

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent(); w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)
    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#aaaaaa') if t else '#aaaaaa'
    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def clear_hover(self):
        if self.hovered_index is not None:
            self.hovered_index = None
            self.parent().viewport().update()
    
    def is_over_text(self, index, pos):
        """Check if a position is over the text in this cell"""
        if not index.isValid():
            return False
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return False
        
        # Get the item rect
        tree = self.parent()
        rect = tree.visualRect(index)
        fm = tree.fontMetrics()
        text_width = fm.horizontalAdvance(text)
        
        start_x = rect.left() + 5
        end_x = min(start_x + text_width, rect.right() - 5)
        
        return start_x <= pos.x() <= end_x

    def paint(self, painter, option, index):
        if not index.isValid(): return
        
        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus
        
        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text: return

        
        is_hovering = getattr(self, 'hovered_index', None) == index
        painter.save()

        if is_hovering:
            painter.setPen(QColor(self._secondary_color()))
            f = painter.font(); f.setUnderline(True); painter.setFont(f)
        else:
            painter.setPen(QColor(self._secondary_color()))

        # 🟢 Define the drawing area (with a 5px buffer on the sides)
        draw_rect = opts.rect.adjusted(5, 0, -5, 0)
        
        # 🟢 THE FIX: Use QTextLayout to perfectly calculate 1, 2, or 3 lines of text
        from PyQt6.QtGui import QTextLayout
        text_layout = QTextLayout(text, painter.font())
        text_layout.beginLayout()
        
        lines_data = []
        while True:
            line = text_layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(draw_rect.width())
            lines_data.append((line.textStart(), line.textLength()))
        text_layout.endLayout()
        
        fm = painter.fontMetrics()
        max_lines = getattr(self, 'max_lines', 3) # 🟢 Support single-line overrides!
        display_lines = []
        
        for i in range(min(len(lines_data), max_lines)):
            start, length = lines_data[i]
            line_str = text[start:start+length].strip()
            
            # If this is the final allowed line, but the text keeps going, elide it perfectly!
            if i == max_lines - 1 and len(lines_data) > max_lines:
                remainder = text[start:].strip()
                line_str = fm.elidedText(remainder, Qt.TextElideMode.ElideRight, draw_rect.width())
                
            display_lines.append(line_str)
            
        line_spacing = fm.lineSpacing()
        total_height = len(display_lines) * line_spacing
        
        # 🟢 EXACT VERTICAL CENTERING: Dynamically centers based on if it has 1, 2, or 3 lines
        start_y = draw_rect.top() + (draw_rect.height() - total_height) // 2 + fm.ascent()
        
        for i, line_str in enumerate(display_lines):
            x = (draw_rect.left() + (draw_rect.width() - fm.horizontalAdvance(line_str)) // 2
                 if self.center_text else draw_rect.left())
            painter.drawText(x, int(start_y + i * line_spacing), line_str)

        painter.restore()

    def editorEvent(self, event, model, option, index):
        
        if event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton): return False
        if event.type() not in [QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease]: return False

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text: return False

        rect = option.rect
        fm = option.fontMetrics
        text_width = fm.horizontalAdvance(text)
        start_x = rect.left() + 5
        end_x = min(start_x + text_width, rect.right() - 5)
        mouse_x = event.position().x()
        is_over_text = (start_x <= mouse_x <= end_x)

        if event.type() == QEvent.Type.MouseMove:
            new = index if is_over_text else None
            if self.hovered_index != new:
                self.hovered_index = new
                self.parent().viewport().update()
            if option.widget:
                option.widget.setCursor(Qt.CursorShape.PointingHandCursor if is_over_text else Qt.CursorShape.ArrowCursor)
            return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton: return False
            if is_over_text and self.hovered_index == index:
                self.clicked.emit(index)
                return True
        return False

# --- DELEGATE 1b: PLAIN WRAP (non-interactive multi-line text) ---

class PlainWrapDelegate(QStyledItemDelegate):
    """Plain text delegate that wraps up to 3 lines — no hover, no click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#cccccc")

    def _theme(self):
        p = self.parent(); w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)
    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#aaaaaa') if t else '#aaaaaa'

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def paint(self, painter, option, index):
        if not index.isValid(): return
        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus
        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text: return

        painter.save()
        painter.setPen(QColor(self._secondary_color()))

        draw_rect = opts.rect.adjusted(5, 0, -5, 0)
        from PyQt6.QtGui import QTextLayout
        text_layout = QTextLayout(text, painter.font())
        text_layout.beginLayout()
        lines_data = []
        while True:
            line = text_layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(draw_rect.width())
            lines_data.append((line.textStart(), line.textLength()))
        text_layout.endLayout()

        fm = painter.fontMetrics()
        max_lines = 3
        display_lines = []
        for i in range(min(len(lines_data), max_lines)):
            start, length = lines_data[i]
            line_str = text[start:start + length].strip()
            if i == max_lines - 1 and len(lines_data) > max_lines:
                line_str = fm.elidedText(text[start:].strip(), Qt.TextElideMode.ElideRight, draw_rect.width())
            display_lines.append(line_str)

        line_spacing = fm.lineSpacing()
        total_height = len(display_lines) * line_spacing
        start_y = draw_rect.top() + (draw_rect.height() - total_height) // 2 + fm.ascent()
        for i, line_str in enumerate(display_lines):
            painter.drawText(draw_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, line_str)
        painter.restore()

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        return hint


# --- DELEGATE 2: MULTI-LINK (For Artists) ---

class MultiLinkArtistDelegate(QStyledItemDelegate):
    artist_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_hover = (None, None) 
        self.master_color = QColor("#cccccc")  # Will be updated via set_master_color
        #self.split_regex = re.compile(r'( /// | • | / | feat\. | vs\. |, )')
        self.split_regex = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )')
        
    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent(); w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)
    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#aaaaaa') if t else '#aaaaaa'
    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def paint(self, painter, option, index):
        if not index.isValid(): return

        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus

        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        painter.save()
        rect = opts.rect
        text = index.data(Qt.ItemDataRole.DisplayRole)

        base_color = QColor(self._secondary_color())

        parsed_parts = self.parse_text(text)
        x_offset = rect.left() + 5 
        fm = painter.fontMetrics()
        
        h = rect.height()
        y = rect.top()
        
        hovered_idx, hovered_part_text = self.current_hover
        
        for part_text, is_link in parsed_parts:
            width = fm.horizontalAdvance(part_text)
            
            if x_offset + width > rect.right() - 5:
                part_text = fm.elidedText(part_text, Qt.TextElideMode.ElideRight, rect.right() - 5 - x_offset)
                width = fm.horizontalAdvance(part_text)

            token_rect = QRect(int(x_offset), int(y), int(width), int(h))
            
            # Check if this specific artist link is being hovered
            is_hovering = is_link and (index == hovered_idx) and (hovered_part_text == part_text)
            
            
            f = painter.font()
            f.setUnderline(is_hovering)
            painter.setFont(f)

            if part_text.strip() == '•':
                r = max(1.2, fm.height() * 0.09)
                painter.save()
                painter.setBrush(base_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(token_rect.center()), r, r)
                painter.restore()
            else:
                painter.setPen(base_color)
                painter.drawText(token_rect, Qt.AlignmentFlag.AlignVCenter, part_text)
            x_offset += width
            if x_offset >= rect.right() - 5:
                break

        painter.restore()

    def clear_hover(self):
        if self.current_hover != (None, None):
            self.current_hover = (None, None)
            self.parent().viewport().update()
    
    def is_over_text(self, index, pos):
        """Check if a position is over any artist link in this cell"""
        if not index.isValid():
            return False
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return False
        
        parsed_parts = self.parse_text(text)
        tree = self.parent()
        rect = tree.visualRect(index)
        fm = tree.fontMetrics()
        
        x_offset = rect.left() + 5
        
        for part_text, is_link in parsed_parts:
            width = fm.horizontalAdvance(part_text)
            token_rect = QRect(int(x_offset), int(rect.top()), int(width), int(rect.height()))
            if token_rect.contains(pos):
                return True
            x_offset += width
            if x_offset > rect.right() - 5:
                break
        
        return False

    def parse_text(self, text):
        if not text: return []
        parts = self.split_regex.split(text)
        result = []
        for i, part in enumerate(parts):
            is_link = (i % 2 == 0)
            if part: result.append((part, is_link))
        return result

    def editorEvent(self, event, model, option, index):
    
        if event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton): return False
        if event.type() not in [QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease]: return False

        text = index.data(Qt.ItemDataRole.DisplayRole)
        parsed_parts = self.parse_text(text)
        mouse_pos = event.position().toPoint()
        rect = option.rect
        x_offset = rect.left() + 5
        hit_artist = None
        fm = option.fontMetrics
        
        for part_text, is_link in parsed_parts:
            width = fm.horizontalAdvance(part_text)
            token_rect = QRect(int(x_offset), int(rect.top()), int(width), int(rect.height()))
            if token_rect.contains(mouse_pos):
                if is_link: hit_artist = part_text
                break
            x_offset += width
            if x_offset > rect.right() - 5: break

        if event.type() == QEvent.Type.MouseMove:
            new_hover = (index, hit_artist)
            if self.current_hover != new_hover:
                self.current_hover = new_hover
                self.parent().viewport().update()
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton: return False
            if hit_artist:
                self.artist_clicked.emit(hit_artist.strip())
                return True
        return False

# --- DELEGATE 3: MULTI-GENRE (For Genre Column) ---

class MultiGenreDelegate(QStyledItemDelegate):
    """Genre column delegate: per-token hover highlight + click-to-filter."""
    genre_filter_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#cccccc")
        self.split_regex = re.compile(r'( /// | • | / |, )')
        self.current_hover = (None, None)  # (QModelIndex, genre_str)

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent(); w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)
    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#aaaaaa') if t else '#aaaaaa'
    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14

    def clear_hover(self):
        if self.current_hover != (None, None):
            self.current_hover = (None, None)
            self.parent().viewport().update()

    def _parse_tokens(self, text):
        if not text: return []
        parts = self.split_regex.split(text)
        return [(p, i % 2 == 0) for i, p in enumerate(parts) if p]

    def paint(self, painter, option, index):
        if not index.isValid():
            return

        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus

        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return

        base_color = QColor(self._secondary_color())

        hovered_idx, hovered_genre = self.current_hover
        painter.save()

        draw_rect = opts.rect.adjusted(5, 0, -5, 0)
        fm = painter.fontMetrics()
        max_lines = getattr(self, 'max_lines', 3)

        display_lines = self._layout_lines(text, painter.font(), draw_rect.width(), max_lines, fm)

        line_spacing = fm.lineSpacing()
        total_height = len(display_lines) * line_spacing
        start_y = draw_rect.top() + (draw_rect.height() - total_height) // 2 + fm.ascent()

        for line_idx, line_str in enumerate(display_lines):
            y = int(start_y + line_idx * line_spacing)
            x = draw_rect.left()
            for part in self.split_regex.split(line_str):
                if not part:
                    continue
                is_sep = bool(self.split_regex.fullmatch(part))
                width = fm.horizontalAdvance(part)
                is_hovering = (not is_sep) and index == hovered_idx and hovered_genre == part.strip()
                f = painter.font()
                f.setUnderline(is_hovering)
                painter.setFont(f)
                _draw_sep_token(painter, int(x), y, part, fm, QColor("#777") if is_sep else base_color)
                x += width

        painter.restore()

    def _layout_lines(self, text, font, width, max_lines, fm):
        """Return up to max_lines display strings, wrapping at width, eliding the last if needed."""
        from PyQt6.QtGui import QTextLayout
        tl = QTextLayout(text, font)
        tl.beginLayout()
        lines_data = []
        while True:
            line = tl.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            lines_data.append((line.textStart(), line.textLength()))
        tl.endLayout()

        display = []
        for i in range(min(len(lines_data), max_lines)):
            start, length = lines_data[i]
            line_str = text[start:start + length].strip()
            if i == max_lines - 1 and len(lines_data) > max_lines:
                line_str = fm.elidedText(text[start:].strip(), Qt.TextElideMode.ElideRight, width)
            display.append(line_str)
        return display

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
            return False
        if event.type() not in [QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease]:
            return False

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return False

        hit_genre = self._hit_test(text, event.position().toPoint(), option)

        if event.type() == QEvent.Type.MouseMove:
            new_hover = (index, hit_genre) if hit_genre else (None, None)
            if self.current_hover != new_hover:
                self.current_hover = new_hover
                self.parent().viewport().update()
            if option.widget:
                option.widget.setCursor(Qt.CursorShape.PointingHandCursor if hit_genre else Qt.CursorShape.ArrowCursor)
            return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if hit_genre:
                self.genre_filter_requested.emit(hit_genre.strip())
                return True
        return False

    def _hit_test(self, text, pos, option):
        """Return the genre token string under pos, or None."""
        draw_rect = option.rect.adjusted(5, 0, -5, 0)
        fm = option.fontMetrics
        max_lines = getattr(self, 'max_lines', 3)
        display_lines = self._layout_lines(text, option.font, draw_rect.width(), max_lines, fm)

        line_spacing = fm.lineSpacing()
        total_height = len(display_lines) * line_spacing
        start_y = draw_rect.top() + (draw_rect.height() - total_height) // 2

        for line_idx, line_str in enumerate(display_lines):
            y_top = int(start_y + line_idx * line_spacing)
            if not (y_top <= pos.y() < y_top + line_spacing):
                continue
            x = draw_rect.left()
            for part in self.split_regex.split(line_str):
                if not part:
                    continue
                width = fm.horizontalAdvance(part)
                is_sep = bool(self.split_regex.fullmatch(part))
                if QRect(int(x), y_top, int(width), line_spacing).contains(pos):
                    return None if is_sep else part.strip()
                x += width
            return None
        return None

# --- DELEGATE 4: COMBINED TRACK (Cover + Title + Artist) ---

class CombinedTrackDelegate(QStyledItemDelegate):
    artist_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#cccccc")
        self.sep_pattern = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )')
        self.current_hover = (None, None)
        self.cover_cache = LRUCache(max_size=300)

    def set_master_color(self, color):
        self.master_color = QColor(color)

    def _theme(self):
        p = self.parent(); w = p.window() if p and hasattr(p, 'window') else None
        return getattr(w, 'theme', None)
    def _primary_color(self):
        t = self._theme(); return getattr(t, 'font_color_primary', '#dddddd') if t else '#dddddd'
    def _primary_px(self):
        t = self._theme(); return getattr(t, 'font_size_primary', 14) if t else 14
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#aaaaaa') if t else '#aaaaaa'
    def _secondary_px(self):
        t = self._theme(); return getattr(t, 'font_size_secondary', 12) if t else 12

    def paint(self, painter, option, index):
        if not index.isValid(): return

        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus

        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        rect = opts.rect
        title_str = index.data(Qt.ItemDataRole.DisplayRole)
        user_data = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        
        if not user_data:
            return
        
        track_data = user_data.get('data', {}) if isinstance(user_data, dict) else {}
        artist_str = track_data.get('artist', 'Unknown')

        main_text_color = self._primary_color()

        painter.save()

        # --- 1. FONTS & TEXT SETUP ---
        title_font = QFont(opts.font)
        title_font.setPixelSize(self._primary_px())
        title_font.setBold(True)

        artist_font = QFont(opts.font)
        artist_font.setPixelSize(self._secondary_px())

        fm_title = QFontMetrics(title_font)
        fm_artist = QFontMetrics(artist_font)

        # --- 2. LAYOUT MATH (Dynamic based on Mode) ---
        if getattr(self, 'is_album_mode', False):
            cover_size = 40
            padding_left = 10
            gap = 12
            start_x = rect.left() + padding_left
            cover_x = start_x
            cover_y = rect.top() + (rect.height() - cover_size) // 2
            cover_rect = QRect(int(cover_x), int(cover_y), cover_size, cover_size)
            text_x = cover_rect.right() + gap
            max_x = rect.right() - 10
            max_w = max_x - text_x
        else:
            cover_size = 65
            padding_left = 10
            gap = 15
            start_x = rect.left() + padding_left
            cover_x = start_x
            cover_y = rect.top() + (rect.height() - cover_size) // 2
            cover_rect = QRect(int(cover_x), int(cover_y), cover_size, cover_size)
            text_x = cover_rect.right() + gap
            max_x = rect.right() - 10
            max_w = max_x - text_x

        # --- 3. DRAW COVER ART ---
        pixmap = None
        raw_cid = track_data.get('cover_id') or track_data.get('coverArt') or track_data.get('albumId')
        cid = str(raw_cid) if raw_cid else None 
        
        if cid:
            
            pixmap = self.cover_cache.get(cid)
            
            if not pixmap:
                
                from player.components.cover_cache import CoverCache
                data = CoverCache.instance().get_thumb(cid)
                if data:
                    pix = QPixmap()
                    pix.loadFromData(data)
                    if not pix.isNull():
                        pix = pix.scaled(cover_size, cover_size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                       
                        self.cover_cache.set(cid, pix)
                        pixmap = pix
        
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if pixmap and not pixmap.isNull():
            path = QPainterPath()
            path.addRoundedRect(QRectF(cover_rect), 4, 4)
            painter.setClipPath(path)
            painter.drawPixmap(cover_rect, pixmap)
            painter.setClipping(False)
        else:
            painter.setBrush(QColor("#222222"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cover_rect, 4, 4)
            # Vector-drawn music note — avoids relying on a "♪" glyph, whose
            # font-fallback resolution floods the console with bearing warnings
            # when painted on every row, every repaint/scroll.
            painter.save()
            painter.setBrush(QColor("#555555"))
            note_h = cover_rect.height() * 0.5
            cx, cy = cover_rect.center().x(), cover_rect.center().y()
            stem_x = cx + note_h * 0.18
            stem_top_y = cy - note_h * 0.5
            stem_bottom_y = cy + note_h * 0.32
            head_r = max(1.0, note_h * 0.16)
            painter.drawLine(QPointF(stem_x, stem_top_y), QPointF(stem_x, stem_bottom_y))
            painter.drawEllipse(QPointF(stem_x - head_r * 0.9, stem_bottom_y), head_r, head_r * 0.8)
            painter.restore()

        # --- 4. DYNAMIC TEXT BLOCK CALCULATION ---
        from PyQt6.QtGui import QTextLayout
        text_layout = QTextLayout(title_str, title_font)
        text_layout.beginLayout()
        lines_data = []
        while True:
            line = text_layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(max_w)
            lines_data.append((line.textStart(), line.textLength()))
        text_layout.endLayout()

        max_title_lines = getattr(self, 'max_title_lines', 2)
        display_title_lines = []
        for i in range(min(len(lines_data), max_title_lines)):
            start, length = lines_data[i]
            line_str = title_str[start:start+length].strip()
            if i == max_title_lines - 1 and len(lines_data) > max_title_lines:
                remainder = title_str[start:].strip()
                line_str = fm_title.elidedText(remainder, Qt.TextElideMode.ElideRight, max_w)
            display_title_lines.append(line_str)

        if not display_title_lines: display_title_lines = [""]

        # Calculate exact pixel heights of the title lines, the artist line, and the gap
        title_block_h = len(display_title_lines) * fm_title.lineSpacing()
        artist_block_h = fm_artist.lineSpacing()
        text_gap = 2
        total_text_h = title_block_h + text_gap + artist_block_h

        # PERFECT VERTICAL CENTERING OF THE COMBINED BLOCK!
        start_y = rect.top() + (rect.height() - total_text_h) // 2
        
        painter.setFont(title_font)
        painter.setPen(QColor(main_text_color))
        title_y = start_y + fm_title.ascent()
        for i, line_str in enumerate(display_title_lines):
            painter.drawText(int(text_x), int(title_y + i * fm_title.lineSpacing()), line_str)

        # Build the exact Artist hit-box underneath the dynamically sized title
        artist_rect_y = start_y + title_block_h + text_gap
        artist_rect = QRect(int(text_x), int(artist_rect_y), int(max_w), int(artist_block_h))


        # --- 5. DRAW ARTIST LINKS ---
        painter.setFont(artist_font)
        
        parts = self.sep_pattern.split(artist_str) if artist_str else []
        current_x = text_x
        
        base_artist_pen = QColor(self._secondary_color())

        hovered_idx, hovered_token = self.current_hover

        for i, part in enumerate(parts):
            if not part: continue
            
            is_link = (i % 2 == 0)
            
            if is_link:
                display_text = part.strip()
                full_width = fm_artist.horizontalAdvance(part) 
                stripped_width = fm_artist.horizontalAdvance(display_text)
                
                leading_space_width = fm_artist.horizontalAdvance(part[:len(part) - len(part.lstrip())])
                
                if current_x + full_width > max_x:
                    available_w = max(0, max_x - current_x - leading_space_width)
                    elided = fm_artist.elidedText(display_text, Qt.TextElideMode.ElideRight, available_w)
                    painter.setPen(base_artist_pen)
                    painter.drawText(current_x + leading_space_width, artist_rect.y(), available_w, artist_rect.height(), Qt.AlignmentFlag.AlignVCenter, elided)
                    break

                text_draw_rect = QRect(int(current_x + leading_space_width), artist_rect.y(), int(stripped_width), artist_rect.height())
                is_token_hover = (index == hovered_idx and display_text == hovered_token)
                
                painter.setPen(base_artist_pen)
                
               
                if is_token_hover:
                    f_underline = QFont(artist_font)
                    f_underline.setUnderline(True)
                    painter.setFont(f_underline)
                else:
                    painter.setFont(artist_font)
                
                painter.drawText(text_draw_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_text)
                current_x += full_width
                
            else:
                painter.setFont(artist_font)
                painter.setPen(base_artist_pen)
                width = fm_artist.horizontalAdvance(part)
                
                if current_x + width > max_x:
                    elided = fm_artist.elidedText(part, Qt.TextElideMode.ElideRight, max_x - current_x)
                    painter.drawText(current_x, artist_rect.y(), max_x - current_x, artist_rect.height(), Qt.AlignmentFlag.AlignVCenter, elided)
                    break
                    
                sep_rect = QRect(int(current_x), artist_rect.y(), int(width), artist_rect.height())
                if part.strip() == '•':
                    r = max(1.2, fm_artist.height() * 0.09)
                    painter.save()
                    painter.setBrush(base_artist_pen)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(QPointF(sep_rect.center()), r, r)
                    painter.restore()
                else:
                    painter.drawText(sep_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, part)

                current_x += width

        painter.restore()

    def is_over_artist(self, index, pos):
        if not index.isValid(): return None
        user_data = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        track_data = user_data.get('data', {}) if isinstance(user_data, dict) else {}
        artist_str = track_data.get('artist', 'Unknown')
        title_str = index.data(Qt.ItemDataRole.DisplayRole) or ""
        
        tree = self.parent()
        rect = tree.visualRect(index)
        
        title_font = QFont(tree.font())
        title_font.setPointSize(11)
        title_font.setBold(True)
        
        artist_font = QFont(tree.font())
        artist_font.setPointSize(10)
        
        fm_title = QFontMetrics(title_font)
        fm_artist = QFontMetrics(artist_font)
        
        if getattr(self, 'is_album_mode', False):
            cover_size = 40
            padding_left = 10
            gap = 12
            start_x = rect.left() + padding_left
            text_x = start_x + cover_size + gap
            max_x = rect.right() - 10
            max_w = max_x - text_x
        else:
            cover_size = 65
            padding_left = 10
            gap = 15
            start_x = rect.left() + padding_left
            text_x = start_x + cover_size + gap
            max_x = rect.right() - 10
            max_w = max_x - text_x

        # Use identical math to locate where the artist text was drawn
        from PyQt6.QtGui import QTextLayout
        text_layout = QTextLayout(title_str, title_font)
        text_layout.beginLayout()
        lines_count = 0
        while True:
            line = text_layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(max_w)
            lines_count += 1
        text_layout.endLayout()
        
        max_title_lines = getattr(self, 'max_title_lines', 2)
        actual_lines = min(lines_count, max_title_lines)
        if actual_lines == 0: actual_lines = 1
        
        title_block_h = actual_lines * fm_title.lineSpacing()
        artist_block_h = fm_artist.lineSpacing()
        text_gap = 2
        total_text_h = title_block_h + text_gap + artist_block_h
        
        start_y = rect.top() + (rect.height() - total_text_h) // 2
        artist_rect_y = start_y + title_block_h + text_gap
        
        artist_rect = QRect(int(text_x), int(artist_rect_y), int(max_w), int(artist_block_h))

        if not artist_rect.contains(pos): return None
            
        parts = self.sep_pattern.split(artist_str) if artist_str else []
        current_x = text_x
        
        for i, part in enumerate(parts):
            if not part: continue
            
            is_link = (i % 2 == 0)
            full_width = fm_artist.horizontalAdvance(part)
            
            if is_link:
                display_text = part.strip()
                stripped_width = fm_artist.horizontalAdvance(display_text)
                leading_space_width = fm_artist.horizontalAdvance(part[:len(part) - len(part.lstrip())])
                
                token_rect = QRect(int(current_x + leading_space_width), artist_rect.y(), int(stripped_width), artist_rect.height())
                if token_rect.contains(pos): 
                    return display_text
                    
            current_x += full_width
            if current_x > max_x: break
            
        return None
            
        return None
    
    def editorEvent(self, event, model, option, index):
    
        if event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton): return False
        if event.type() not in [QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease]: return False

        pos = event.position().toPoint()
        hovered_token = self.is_over_artist(index, pos)

        if event.type() == QEvent.Type.MouseMove:
            new_hover = (index, hovered_token) if hovered_token else (None, None)
            if self.current_hover != new_hover:
                self.current_hover = new_hover
                if option.widget: option.widget.viewport().update()
            
            if hovered_token and option.widget:
                option.widget.setCursor(Qt.CursorShape.PointingHandCursor)
            elif not hovered_token and option.widget:
                option.widget.setCursor(Qt.CursorShape.ArrowCursor)
            return True
            
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton: return False
            if hovered_token:
                self.artist_clicked.emit(hovered_token)
                return True
        return False
        
    def clear_hover(self):
        if self.current_hover != (None, None):
            self.current_hover = (None, None)
            if self.parent(): self.parent().viewport().update()


_ARTIST_SEP_RE = re.compile(r'( /// | • | / | feat\. | Feat\. | vs\. )')

def _split_artist(artist: str):
    return [(p, bool(_ARTIST_SEP_RE.match(p))) for p in _ARTIST_SEP_RE.split(artist) if p]


_GENRE_SEP_RE = re.compile(r' /// | • | / |,\s*|;\s*')

def _split_genres(text):
    """Return list of (text, is_sep) pairs, splitting on bullet/comma/semicolon/slash."""
    parts = [p.strip() for p in _GENRE_SEP_RE.split(text.strip()) if p.strip()]
    result = []
    for i, p in enumerate(parts):
        result.append((p, False))
        if i < len(parts) - 1:
            result.append((' • ', True))
    return result


def _draw_sep_token(painter, x, baseline_y, part, fm, color):
    """Draw a separator token. A lone '•' is drawn as a vector dot instead of
    text — Qt's font-fallback resolution for that glyph floods the console
    with bearing warnings on some systems when drawn every row, every repaint."""
    if part.strip() == '•':
        r = max(1.2, fm.height() * 0.09)
        cx = x + fm.horizontalAdvance(part) / 2.0
        cy = baseline_y - fm.ascent() * 0.35
        painter.save()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.restore()
    else:
        painter.setPen(color)
        painter.drawText(x, baseline_y, part)


class _TrackHeader(QHeaderView):
    _FLEX_COL   = 1   # TITLE — Stretch, absorbs space
    _FLEX_NEXT  = 2   # ARTIST — resized when TITLE's right boundary is dragged
    _HANDLE_PX  = 5   # hit-test tolerance in pixels

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._accent = QColor('#555555')
        self._left_drag = False
        self._left_start_x = 0
        self._left_start_w = 0

    def set_accent(self, color: str):
        self._accent = QColor(color)
        self.update()

    def _theme(self):
        w = self.window() if hasattr(self, 'window') else None
        return getattr(w, 'theme', None)
    def _secondary_px(self):
        t = self._theme(); return getattr(t, 'font_size_secondary', 12) if t else 12
    def _secondary_color(self):
        t = self._theme(); return getattr(t, 'font_color_secondary', '#555555') if t else '#555555'
    def _border_qcolor(self):
        t = self._theme()
        if t is None:
            return QColor('#2a2a2a')
        if getattr(t, 'auto_border_from_accent', True):
            return QColor(getattr(t, 'accent', '#cccccc')).darker(250)
        return QColor(getattr(t, 'manual_border_color', '#2a2a2a'))

    def _flex_boundary_x(self):
        return self.sectionViewportPosition(self._FLEX_COL) + self.sectionSize(self._FLEX_COL)

    def _near_flex_boundary(self, x):
        return abs(x - self._flex_boundary_x()) <= self._HANDLE_PX

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._near_flex_boundary(event.pos().x()):
            self._left_drag = True
            self._left_start_x = event.pos().x()
            self._left_start_w = self.sectionSize(self._FLEX_NEXT)
            self.setCursor(Qt.CursorShape.SplitHCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._left_drag:
            delta = event.pos().x() - self._left_start_x
            # drag right → ARTIST shrinks (direction = -1, matching psysonic)
            new_w = max(80, self._left_start_w - delta)
            self.resizeSection(self._FLEX_NEXT, new_w)
            event.accept()
            return
        if self._near_flex_boundary(event.pos().x()):
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._left_drag:
            self._left_drag = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, Qt.GlobalColor.transparent)

        text = self.model().headerData(logical_index, Qt.Orientation.Horizontal) or ''
        f = QFont(); f.setPixelSize(self._secondary_px()); f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(self._secondary_color()))
        h_align = Qt.AlignmentFlag.AlignHCenter if logical_index in (0, 3, 5) else Qt.AlignmentFlag.AlignLeft
        painter.drawText(rect.adjusted(4, 0, -4, -8),
                         h_align | Qt.AlignmentFlag.AlignBottom, text)

        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        if logical_index > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(self._border_qcolor(), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(rect.right(), rect.top() - 5, rect.right(), rect.bottom() - 8)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.restore()


class _TrackListDelegate(QStyledItemDelegate):
    """Adds 8px top gap on row 0; draws hover/selection/playing backgrounds manually."""
    def __init__(self, parent=None, heart_col=3):
        super().__init__(parent)
        self._heart_col = heart_col   # column index that shows ♥/♡ (-1 = none)
        self.playing_row = -1
        self.accent = QColor('#cccccc')
        self._active_hover = QColor(204, 204, 204, 45)  # updated via set_active_hover
        self.font_size = 14
        self._movie = None
        self._is_playing = False
        self._hover_artist = None   # (row, part_text)
        self._hover_genre  = None   # (row, genre_text)
        self._heart_filled_pix = QPixmap()
        self._heart_empty_pix  = QPixmap()
        self._kbd_row = -1
        self.max_genres = -1   # -1 = no limit

    def set_font_size(self, size: int):
        self.font_size = size

    def _primary_px(self) -> int:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_size_primary', self.font_size) if _theme else self.font_size

    def _secondary_px(self) -> int:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_size_secondary', max(self.font_size - 2, 10)) if _theme else max(self.font_size - 2, 10)

    def _primary_color(self) -> str:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_color_primary', '#dddddd') if _theme else '#dddddd'

    def _secondary_color(self) -> str:
        _p = self.parent()
        _theme = getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None
        return getattr(_theme, 'font_color_secondary', '#aaaaaa') if _theme else '#aaaaaa'

    def _theme(self):
        _p = self.parent()
        return getattr(getattr(_p, 'window', lambda: None)(), 'theme', None) if _p else None

    def _hover_qcolor(self) -> QColor:
        return QColor(resolve_menu_hover(self._theme()))

    def set_movie(self, movie):
        self._movie = movie

    def set_heart_pixmaps(self, filled: QPixmap, empty: QPixmap):
        self._heart_filled_pix = filled
        self._heart_empty_pix  = empty

    def set_active_hover(self, color: 'QColor'):
        self._active_hover = color
        if self.parent():
            self.parent().viewport().update()

    def set_playing(self, row: int, accent: str, is_playing: bool = True):
        self.playing_row = row
        self._is_playing = is_playing
        self.accent = QColor(accent)
        if self.parent():
            self.parent().viewport().update()

    def sizeHint(self, option, index):
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        from PyQt6.QtWidgets import QStyle
        draw_rect = option.rect
        is_playing_row = (index.row() == self.playing_row)

        # Disc separator rows — draw label in col 1 only, skip everything else
        user_data = index.sibling(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(user_data, dict) and user_data.get('_is_disc_header'):
            if index.column() == 1:
                text = index.data() or ''
                view = option.widget
                full_w = view.viewport().width() if view else draw_rect.width()
                span_rect = draw_rect.__class__(draw_rect.left(), draw_rect.y(), full_w - draw_rect.left(), draw_rect.height())
                f = QFont()
                f.setPixelSize(self._secondary_px())
                f.setBold(True)
                fm = QFontMetrics(f)
                painter.save()
                painter.setFont(f)
                painter.setPen(QColor(self._secondary_color()))
                painter.drawText(span_rect.left() + 4, span_rect.center().y() + fm.ascent() // 2, text)
                painter.restore()
            return

        # Draw background once per row (col 0 only) spanning full width
        if index.column() == 0:
            is_kbd = (index.row() == self._kbd_row)
            if option.state & QStyle.StateFlag.State_MouseOver:
                color = self._hover_qcolor()
            elif is_kbd:
                color = self._active_hover
            else:
                color = None
            if color:
                view = option.widget
                full_w = view.viewport().width() if view else option.rect.width()
                row_rect = option.rect.__class__(0, option.rect.y(), full_w, option.rect.height())
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(row_rect, 6, 6)
                painter.restore()

            # Draw playing GIF instead of track number
            if is_playing_row and self._movie and self._is_playing:
                pix = self._movie.currentPixmap()
                if not pix.isNull():
                    px = draw_rect.left() + (draw_rect.width() - pix.width()) // 2
                    py = draw_rect.top()  + (draw_rect.height() - pix.height()) // 2
                    painter.drawPixmap(px, py, pix)
                return  # skip text rendering for col 0 on playing row

        # Col 3 — draw heart pixmap centered
        if self._heart_col >= 0 and index.column() == self._heart_col:
            is_fav = index.data() == '♥'
            pix = self._heart_filled_pix if is_fav else self._heart_empty_pix
            if not pix.isNull():
                px = draw_rect.center().x() - pix.width() // 2
                py = draw_rect.center().y() - pix.height() // 2
                painter.drawPixmap(px, py, pix)
            return

        # Col 2 — draw artist parts manually so we can underline on hover
        if index.column() == 2:
            artist = index.data() or ''
            f = QFont()
            f.setPixelSize(self._secondary_px())
            fm = QFontMetrics(f)
            ax = draw_rect.left() + 4
            ay = draw_rect.center().y()
            right_edge = draw_rect.right() - 4
            row = index.row()
            painter.save()
            painter.setFont(f)
            for part, is_sep in _split_artist(artist):
                pw = fm.horizontalAdvance(part)
                available = right_edge - ax
                if ax + pw > right_edge:
                    if available > 0:
                        elided = fm.elidedText(part, Qt.TextElideMode.ElideRight, available)
                        painter.setPen(QColor(120, 120, 120) if is_sep else (self.accent if row == self.playing_row else QColor(self._secondary_color())))
                        painter.drawText(ax, ay + fm.ascent() // 2, elided)
                    break
                hovered = (not is_sep and self._hover_artist == (row, part.strip()))
                color = QColor(120, 120, 120) if is_sep else (self.accent if row == self.playing_row else QColor(self._secondary_color()))
                _draw_sep_token(painter, ax, ay + fm.ascent() // 2, part, fm, color)
                if hovered:
                    painter.drawLine(ax, ay + fm.ascent() // 2 + 2, ax + pw, ay + fm.ascent() // 2 + 2)
                ax += pw
            painter.restore()
            return

        # Col 1 — title at theme primary font size
        if index.column() == 1:
            text = index.data() or ''
            f = QFont()
            f.setPixelSize(self._primary_px())
            f.setBold(True)
            fm = QFontMetrics(f)
            painter.save()
            painter.setFont(f)
            painter.setPen(QColor(index.data(Qt.ItemDataRole.ForegroundRole)) if index.data(Qt.ItemDataRole.ForegroundRole) else QColor(self._primary_color()))
            x = draw_rect.left() + 4
            y = draw_rect.center().y() + fm.ascent() // 2
            painter.drawText(x, y, fm.elidedText(text, Qt.TextElideMode.ElideRight, draw_rect.width() - 8))
            painter.restore()
            return

        # Col 4 — genre, each part separately clickable with hover underline
        if index.column() == 4:
            genre_text = index.data() or ''
            if genre_text:
                f = QFont()
                f.setPixelSize(self._secondary_px())
                fm = QFontMetrics(f)
                ax = draw_rect.left() + 4
                ay = draw_rect.center().y()
                right_edge = draw_rect.right() - 4
                row = index.row()
                painter.save()
                painter.setFont(f)
                genre_count = 0
                for part, is_sep in _split_genres(genre_text):
                    if self.max_genres > 0 and genre_count >= self.max_genres:
                        break
                    if not is_sep:
                        genre_count += 1
                    pw = fm.horizontalAdvance(part)
                    available = right_edge - ax
                    if ax >= right_edge:
                        break
                    if ax + pw > right_edge and available > 0:
                        elided = fm.elidedText(part, Qt.TextElideMode.ElideRight, available)
                        painter.setPen(QColor(120, 120, 120) if is_sep else QColor(self._secondary_color()))
                        painter.drawText(ax, ay + fm.ascent() // 2, elided)
                        break
                    hovered = (not is_sep and self._hover_genre == (row, part))
                    color = QColor(120, 120, 120) if is_sep else QColor(self._secondary_color())
                    _draw_sep_token(painter, ax, ay + fm.ascent() // 2, part, fm, color)
                    if hovered:
                        painter.drawLine(ax, ay + fm.ascent() // 2 + 2, ax + pw, ay + fm.ascent() // 2 + 2)
                    ax += pw
                painter.restore()
                return

        # All other columns — strip hover/selected so super() doesn't re-draw background
        opt = option.__class__(option)
        opt.rect = draw_rect
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver & ~QStyle.StateFlag.State_Selected
        f = QFont(opt.font)
        f.setPixelSize(self._secondary_px())
        opt.font = f
        from PyQt6.QtGui import QPalette
        pal = QPalette(opt.palette)
        pal.setColor(QPalette.ColorRole.Text, QColor(self._secondary_color()))
        opt.palette = pal
        super().paint(painter, opt, index)


class TracksTrackModel(QAbstractListModel):
    """Backs the QML tracklist (tracks_list.qml / TrackListView.qml). Role
    layout mirrors PlaylistDetailTrackModel (playlists_browser.py) — same
    positional contract TrackListView.qml already reads from every host
    bridge — but field formatting (genre delimiters, date format, BPM,
    duration) is copied verbatim from this file's own create_track_item()
    to preserve exactly what the Tracks tab has always shown."""
    TRACK_IDX      = Qt.ItemDataRole.UserRole + 1
    TRACK_ID       = Qt.ItemDataRole.UserRole + 2
    TRACK_NUMBER   = Qt.ItemDataRole.UserRole + 3
    TRACK_TITLE    = Qt.ItemDataRole.UserRole + 4
    ARTIST_NAME    = Qt.ItemDataRole.UserRole + 5
    IS_FAVORITE    = Qt.ItemDataRole.UserRole + 6
    DURATION_STR   = Qt.ItemDataRole.UserRole + 7
    PLAY_COUNT_STR = Qt.ItemDataRole.UserRole + 8
    TRACK_GENRE    = Qt.ItemDataRole.UserRole + 9
    COVER_ART_ID   = Qt.ItemDataRole.UserRole + 10
    ALBUM_NAME     = Qt.ItemDataRole.UserRole + 11
    ALBUM_ID       = Qt.ItemDataRole.UserRole + 12
    ALBUM_TRACK_NO = Qt.ItemDataRole.UserRole + 13
    YEAR_STR       = Qt.ItemDataRole.UserRole + 14
    DATE_ADDED_STR = Qt.ItemDataRole.UserRole + 15
    BPM_STR        = Qt.ItemDataRole.UserRole + 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        if role == self.TRACK_IDX:      return r.get('_idx', 0)
        if role == self.TRACK_ID:       return r.get('_id', '')
        if role == self.TRACK_NUMBER:   return r.get('_num', '')
        if role == self.TRACK_TITLE:    return r.get('_title', '')
        if role == self.ARTIST_NAME:    return r.get('_artist', '')
        if role == self.IS_FAVORITE:    return r.get('_fav', False)
        if role == self.DURATION_STR:   return r.get('_dur', '')
        if role == self.PLAY_COUNT_STR: return r.get('_plays', '')
        if role == self.TRACK_GENRE:    return r.get('_genre', '')
        if role == self.COVER_ART_ID:   return r.get('_cover_id', '')
        if role == self.ALBUM_NAME:     return r.get('_album', '')
        if role == self.ALBUM_ID:       return r.get('_album_id', '')
        if role == self.ALBUM_TRACK_NO: return r.get('_album_track_no', '')
        if role == self.YEAR_STR:       return r.get('_year', '')
        if role == self.DATE_ADDED_STR: return r.get('_date_added', '')
        if role == self.BPM_STR:        return r.get('_bpm', '')
        return None

    def roleNames(self):
        return {
            self.TRACK_IDX:      b"trackIdx",
            self.TRACK_ID:       b"trackId",
            self.TRACK_NUMBER:   b"trackNumber",
            self.TRACK_TITLE:    b"trackTitle",
            self.ARTIST_NAME:    b"artistName",
            self.IS_FAVORITE:    b"isFavorite",
            self.DURATION_STR:   b"durationStr",
            self.PLAY_COUNT_STR: b"playCountStr",
            self.TRACK_GENRE:    b"trackGenre",
            self.COVER_ART_ID:   b"coverArtId",
            self.ALBUM_NAME:     b"albumName",
            self.ALBUM_ID:       b"albumId",
            self.ALBUM_TRACK_NO: b"albumTrackNo",
            self.YEAR_STR:       b"yearStr",
            self.DATE_ADDED_STR: b"dateAddedStr",
            self.BPM_STR:        b"bpmStr",
        }

    @staticmethod
    def _build_row(t: dict, index_label) -> dict:
        raw_state = t.get('starred')
        is_fav = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)

        genre_raw = t.get('genre', '') or ''
        if genre_raw and ' • ' not in genre_raw:
            for delimiter in ['; ', ';', ' | ', '|', ' /// ', ' / ', '/', ', ']:
                genre_raw = genre_raw.replace(delimiter, ' • ')

        raw_plays = t.get('playCount') or t.get('play_count') or 0
        try: plays = int(raw_plays)
        except Exception: plays = 0

        raw_dur = t.get('duration', 0)
        time_str = ""
        try:
            if isinstance(raw_dur, str) and ":" in raw_dur:
                time_str = raw_dur
            else:
                seconds = int(float(raw_dur)) if raw_dur else 0
                if seconds > 0:
                    m, s = divmod(seconds, 60)
                    time_str = f"{m}:{s:02d}"
        except Exception:
            pass

        track_num = t.get('trackNumber') or t.get('track') or ''

        created_raw = t.get('created') or ''
        date_str = ''
        if created_raw:
            try:
                dt = _datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
                fmt = '%#d %b %Y' if _PLATFORM_WINDOWS else '%-d %b %Y'
                date_str = dt.strftime(fmt)
            except Exception:
                try: date_str = created_raw[:10]
                except Exception: date_str = ''

        raw_bpm = t.get('bpm') or ''
        try:
            bpm_val = float(raw_bpm)
            bpm_str = f"{bpm_val:.1f}" if bpm_val > 0 else ''
        except (ValueError, TypeError):
            bpm_str = ''

        t['starred'] = is_fav
        return {
            '_idx':    t.get('_row_idx', 0),
            '_id':     str(t.get('id', '')),
            '_num':    str(index_label),
            '_title':  str(t.get('title') or 'Unknown'),
            '_artist': str(t.get('artist') or 'Unknown'),
            '_fav':    is_fav,
            '_dur':    time_str,
            '_plays':  str(plays) if plays > 0 else '',
            '_genre':  genre_raw,
            '_cover_id': str(t.get('cover_id') or t.get('coverArt') or t.get('albumId') or ''),
            '_album':    str(t.get('album') or 'Unknown'),
            '_album_id': str(t.get('albumId') or t.get('parent') or ''),
            '_album_track_no': str(track_num) if track_num else '',
            '_year':           str(t.get('year') or ''),
            '_date_added':     date_str,
            '_bpm':            bpm_str,
        }

    def set_tracks(self, tracks: list, index_offset: int = 0):
        rows = []
        for i, t in enumerate(tracks):
            t['_row_idx'] = i
            rows.append(self._build_row(t, index_offset + i + 1))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update_favorite(self, track_idx: int, is_fav: bool):
        for i, r in enumerate(self._rows):
            if r.get('_idx') == track_idx:
                r['_fav'] = is_fav
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.IS_FAVORITE])
                break

    def update_bpm(self, track_id: str, bpm_str: str):
        for i, r in enumerate(self._rows):
            if r.get('_id') == track_id:
                r['_bpm'] = bpm_str
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.BPM_STR])
                break


class TracksBridge(QObject):
    """Bridge for tracks_list.qml's TrackListView — handles play/favorite/
    navigation clicks, column width/order/visibility/sort persistence, the
    burger column-visibility menu, and the new colFilterClicked/
    trackMultiContextMenuRequested hooks (see UI_MANIFEST.md additions for
    the Tracks tab QML conversion). Structurally mirrors PlaylistDetailBridge
    (playlists_browser.py) since both are flat (non-disc-grouped) lists."""
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
    tracksLoadingChanged      = pyqtSignal(bool)
    trackCountChanged         = pyqtSignal(str)
    filtersActiveChanged      = pyqtSignal(bool)
    playingStatusChanged      = pyqtSignal(str, bool)
    selectedTrackChanged      = pyqtSignal(int)
    multiSelectRangeChanged   = pyqtSignal('QVariantList')  # trkIdx values to highlight (Shift+arrow range)
    scrollToModelRow          = pyqtSignal(int)
    scrollToTopOfView         = pyqtSignal()
    scrollToBottomOfView      = pyqtSignal()
    showTrackChanged          = pyqtSignal(bool)
    showTitleChanged          = pyqtSignal(bool)
    showArtistChanged         = pyqtSignal(bool)
    showFavChanged            = pyqtSignal(bool)
    showGenreChanged          = pyqtSignal(bool)
    showDurChanged            = pyqtSignal(bool)
    showPlaysChanged          = pyqtSignal(bool)
    showAlbumChanged          = pyqtSignal(bool)
    showTrackNoChanged        = pyqtSignal(bool)
    showYearChanged           = pyqtSignal(bool)
    showDateChanged           = pyqtSignal(bool)
    showBpmChanged            = pyqtSignal(bool)
    sortStateChanged          = pyqtSignal(str, str)   # col, 'asc'|'desc'|''

    # Positional contract shared with every other *TrackModel/*Bridge pair
    # that TrackListView.qml reads — see comment above
    # PlaylistDetailBridge.getColWidths in playlists_browser.py.
    COL_ORDER_DEFAULT = ["track", "title", "artist", "album", "fav", "genre", "dur", "plays", "trackno", "year", "date", "bpm"]
    # QML col-id -> legacy integer column index (get_sort_string()'s mapping)
    # 'genre' (col 6) is filterable but not in TrackListView.qml's
    # _isSortable list, so colHeaderClicked never sends it — it's only here
    # so colFilterClicked's lookup and the popup's own sort_requested
    # (ColumnFilterPopup allows sorting by any filterable column) resolve.
    _COL_STR_TO_INT = {'title': 1, 'artist': 3, 'album': 4, 'year': 5, 'genre': 6, 'fav': 7,
                       'plays': 8, 'dur': 9, 'trackno': 10, 'date': 11, 'bpm': 12}
    _COL_INT_TO_STR = {v: k for k, v in _COL_STR_TO_INT.items()}
    _DESC_FIRST_COLS = {8, 9, 11, 12}  # plays, length, date added, bpm

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._selected_trkidx = -1
        self._select_anchor_trkidx = -1  # set when a Shift+arrow range starts
        self._multi_select_range = []    # current Shift+arrow range, for Enter-to-play-all
        self.search = SearchController(
            on_active_changed=lambda active: view._set_window_shortcuts_enabled(not active)
            if hasattr(view, '_set_window_shortcuts_enabled') else None)

    @pyqtProperty(QObject, constant=True)
    def searchCtl(self):
        return self.search

    def navigateRow(self, delta: int, extend: bool = False):
        """Move the keyboard selection cursor by `delta` rows (respecting the
        active search filter). `extend=True` (Shift+arrow) grows a
        multi-select range from the position the cursor was at when Shift
        was first held, instead of moving a single selection."""
        rows = self._view.tracks_model._rows
        if not rows:
            return
        st = self.search.text.lower()
        if st:
            nav = [(i, r['_idx']) for i, r in enumerate(rows)
                   if st in r.get('_title', '').lower() or st in r.get('_artist', '').lower()
                   or st in r.get('_genre', '').lower() or st in r.get('_album', '').lower()]
        else:
            nav = [(i, r['_idx']) for i, r in enumerate(rows)]
        if not nav:
            return
        pos = next((p for p, (_, idx) in enumerate(nav) if idx == self._selected_trkidx), -1)
        if pos == -1:
            new_pos = 0 if delta > 0 else len(nav) - 1
        else:
            new_pos = pos + delta
            if new_pos < 0:
                self.scrollToTopOfView.emit(); return
            if new_pos >= len(nav):
                self.scrollToBottomOfView.emit(); return
        mr, ti = nav[new_pos]
        self._selected_trkidx = ti
        if extend:
            if self._select_anchor_trkidx < 0:
                self._select_anchor_trkidx = nav[pos][1] if pos != -1 else ti
            anchor_pos = next((p for p, (_, idx) in enumerate(nav)
                               if idx == self._select_anchor_trkidx), new_pos)
            lo, hi = sorted((anchor_pos, new_pos))
            self._multi_select_range = [nav[p][1] for p in range(lo, hi + 1)]
            self.multiSelectRangeChanged.emit(self._multi_select_range)
        else:
            self._select_anchor_trkidx = -1
            self._multi_select_range = []
            self.multiSelectRangeChanged.emit([])
            self.selectedTrackChanged.emit(ti)
        self.scrollToModelRow.emit(mr)

    @pyqtSlot()
    def playSelected(self):
        """Enter key: if a Shift+arrow range is active, play the whole
        selection (like the context menu's "Play Now (N)"); otherwise just
        the single cursor row."""
        if len(self._multi_select_range) > 1:
            tracks = self._view.tracks
            selected = [tracks[i] for i in sorted(self._multi_select_range) if 0 <= i < len(tracks)]
            if selected:
                self._view.play_multiple_tracks.emit(selected)
            return
        if self._selected_trkidx >= 0:
            self.trackPlayClicked(self._selected_trkidx)

    @pyqtSlot(int)
    def trackPlayClicked(self, track_idx: int):
        tracks = self._view.tracks
        if 0 <= track_idx < len(tracks):
            self._selected_trkidx = track_idx
            self.selectedTrackChanged.emit(track_idx)
            self._view.play_track.emit(tracks[track_idx])

    @pyqtSlot(str)
    def trackArtistClicked(self, name: str):
        if name:
            self._view.switch_to_artist_tab.emit(name)

    @pyqtSlot(str)
    def trackGenreClicked(self, genre: str):
        if genre:
            self._view._apply_col_filter(6, {genre})

    @pyqtSlot(str)
    def trackYearClicked(self, year: str):
        if year:
            self._view._apply_col_filter(5, {year})

    @pyqtSlot(int)
    def trackFavoriteClicked(self, track_idx: int):
        self._view.toggle_track_favorite(track_idx)

    @pyqtSlot(str, str)
    def trackAlbumClicked(self, album_id: str, album_name: str):
        if not album_id:
            return
        artist_name = ''
        cover = album_id
        for t in self._view.tracks:
            if str(t.get('albumId') or t.get('parent') or '') == str(album_id):
                artist_name = t.get('album_artist') or t.get('artist') or ''
                cover = t.get('coverArt') or t.get('cover_id') or album_id
                break
        album_data = {'id': album_id, 'title': album_name, 'artist': artist_name, 'coverArt': cover}
        self._view.switch_to_album_tab.emit(album_data)

    @pyqtSlot(int, float, float)
    def trackContextMenuRequested(self, track_idx: int, global_x: float, global_y: float):
        tracks = self._view.tracks
        if 0 <= track_idx < len(tracks):
            self._view._show_track_context_menu_at([track_idx], int(global_x), int(global_y))

    @pyqtSlot('QVariantList', float, float)
    def trackMultiContextMenuRequested(self, indices, global_x: float, global_y: float):
        self._view._show_track_context_menu_at([int(i) for i in indices], int(global_x), int(global_y))

    @pyqtSlot(str, float, float, float, float)
    def colFilterClicked(self, col: str, gx: float, gy: float, w: float, h: float):
        col_idx = self._COL_STR_TO_INT.get(col)
        if col_idx is None or col_idx not in self._view._COL_FIELD:
            return
        from PyQt6.QtCore import QPoint, QSize, QRect as _QRect
        global_rect = _QRect(QPoint(int(gx), int(gy)), QSize(int(w), int(h)))
        self._view._on_filter_clicked(col_idx, global_rect)

    @pyqtSlot(str)
    def colHeaderClicked(self, col: str):
        col_idx = self._COL_STR_TO_INT.get(col)
        if col_idx is None:
            return
        view = self._view
        if view.sort_col == col_idx:
            view.sort_order = (Qt.SortOrder.DescendingOrder
                               if view.sort_order == Qt.SortOrder.AscendingOrder
                               else Qt.SortOrder.AscendingOrder)
        else:
            view.sort_col = col_idx
            view.sort_order = (Qt.SortOrder.DescendingOrder if col_idx in self._DESC_FIRST_COLS
                               else Qt.SortOrder.AscendingOrder)
        view.save_sort_state()
        self.sortStateChanged.emit(col, 'asc' if view.sort_order == Qt.SortOrder.AscendingOrder else 'desc')
        view.load_from_db(reset=True)

    @pyqtSlot(result='QVariantList')
    def getSortState(self):
        view = self._view
        col = self._COL_INT_TO_STR.get(view.sort_col, '')
        dir_ = 'asc' if view.sort_order == Qt.SortOrder.AscendingOrder else 'desc'
        return [col, dir_]

    @pyqtSlot()
    def favHeaderClicked(self):
        pass

    @pyqtSlot()
    def refreshClicked(self):
        self._view.refresh_btn.click()

    @pyqtSlot()
    def rowInteracted(self):
        """Called on every row click from QML — clicking inside the native
        QQuickView surface doesn't reliably hand OS-level keyboard focus
        back to the embedding QWidget, which silently breaks Up/Down
        navigation after any row interaction. Reclaim it explicitly."""
        self._view.qml_view.setFocus(Qt.FocusReason.MouseFocusReason)

    @pyqtSlot(str)
    def trackSearchTextChanged(self, text: str):
        self._view.on_search_text_changed(text)

    @pyqtSlot()
    def clearFiltersClicked(self):
        self._view._clear_all_filters()

    @pyqtSlot()
    def playFilteredClicked(self):
        self._view._fetch_all_filtered_tracks(
            lambda tracks: self._view.play_multiple_tracks.emit(tracks) if tracks else None)

    @pyqtSlot()
    def shuffleFilteredClicked(self):
        self._view._shuffle_filtered_tracks()

    @pyqtSlot()
    def albumHeaderClicked(self):
        pass

    @pyqtSlot(result='QVariantList')
    def getColWidths(self):
        saved = QSettings().value('tracks/track_col_widths')
        defaults = {'track': 350, 'title': 200, 'artist': 200, 'fav': 68, 'dur': 75,
                    'plays': 70, 'genre': 120, 'album': 205, 'trackno': 55, 'year': 70,
                    'date': 110, 'bpm': 56}
        if isinstance(saved, dict):
            return [int(saved.get(k, v)) for k, v in defaults.items()]
        return list(defaults.values())

    @pyqtSlot(int, int, int, int, int, int, int, int, int, int, int, int)
    def saveColWidths(self, track, title, artist, fav, dur, plays, genre, album, trackno, year, date, bpm):
        QSettings().setValue('tracks/track_col_widths',
                             {'track': track, 'title': title, 'artist': artist, 'fav': fav,
                              'dur': dur, 'plays': plays, 'genre': genre, 'album': album,
                              'trackno': trackno, 'year': year, 'date': date, 'bpm': bpm})

    @pyqtSlot(result='QVariantList')
    def getColVisibility(self):
        saved = QSettings().value('tracks/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        # Matches this tab's historical defaults (load_column_state's
        # "no saved state" branch): everything visible except TITLE/ARTIST
        # (redundant with the combined TRACK column) and BPM.
        return [bool(saved.get('track',  True)),  bool(saved.get('title',  False)),
                bool(saved.get('artist', False)),  bool(saved.get('fav',    True)),
                bool(saved.get('genre',  True)),   bool(saved.get('dur',    True)),
                bool(saved.get('plays',  True)),   bool(saved.get('album',  True)),
                bool(saved.get('trackno', True)),  bool(saved.get('year',   True)),
                bool(saved.get('date',    True)),  bool(saved.get('bpm',    False))]

    @pyqtSlot(float, float)
    def burgerClicked(self, gx: float, gy: float):
        from player.widgets import themed_shadow_menu, popup_menu_at_global
        saved = QSettings().value('tracks/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        cols = [
            ('track',   'Track',      self.showTrackChanged,   True),
            ('title',   'Title',      self.showTitleChanged,   False),
            ('artist',  'Artist',     self.showArtistChanged,  False),
            ('fav',     'Favorite',   self.showFavChanged,     True),
            ('genre',   'Genre',      self.showGenreChanged,   True),
            ('dur',     'Duration',   self.showDurChanged,     True),
            ('plays',   'Plays',      self.showPlaysChanged,   True),
            ('album',   'Album',      self.showAlbumChanged,   True),
            ('trackno', 'No.',        self.showTrackNoChanged, True),
            ('year',    'Year',       self.showYearChanged,    True),
            ('date',    'Date Added', self.showDateChanged,    True),
            ('bpm',     'BPM',        self.showBpmChanged,     False),
        ]
        menu = themed_shadow_menu(self._view)
        for key, label, sig, default_vis in cols:
            vis = bool(saved.get(key, default_vis))
            menu.add_action(label, lambda k=key, v=vis, s=sig: self._set_col_vis(k, not v, s),
                            icon_path='img/yes.png' if vis else '')
        popup_menu_at_global(menu, int(gx), int(gy))

    def _set_col_vis(self, key: str, visible: bool, signal):
        saved = QSettings().value('tracks/col_visibility', {})
        if not isinstance(saved, dict): saved = {}
        saved[key] = visible
        QSettings().setValue('tracks/col_visibility', saved)
        signal.emit(visible)

    @pyqtSlot(result='QVariantList')
    def getColOrder(self):
        default = self.COL_ORDER_DEFAULT
        known = set(default)
        saved = QSettings().value('tracks/col_order')
        if isinstance(saved, list) and set(saved) <= known and len(saved) > 0:
            result = [c for c in saved if c in known]
            for c in default:
                if c not in result:
                    result.append(c)
            return result
        return default

    @pyqtSlot('QVariantList')
    def saveColOrder(self, order):
        QSettings().setValue('tracks/col_order', list(order))


class _TracksKeyFilter(SearchKeyFilter):
    """Widget-level key filter for the QML tracklist — fires regardless of
    QML focus state. Routes typing into the inline search box while active
    (opened via "/", same global convention as album/playlist detail);
    otherwise handles row navigation/play/escape. Mirrors albums_browser.py's
    _AlbumKeyFilter."""

    def __init__(self, bridge, qml_widget, parent=None):
        super().__init__(bridge.search, on_navigate=self._navigate, parent=parent)
        self._b = bridge
        self._qml = qml_widget

    def eventFilter(self, obj, event):
        # Clicking inside the QML tracklist consumes the mouse press without
        # necessarily handing OS-level keyboard focus back to the native
        # container — without this, the first click after any focus loss
        # (e.g. tab switch, dialog close) leaves Up/Down navigation dead
        # until the user tabs/clicks their way back to it some other way.
        if event.type() == QEvent.Type.MouseButtonPress:
            self._qml.setFocus(Qt.FocusReason.MouseFocusReason)
        return super().eventFilter(obj, event)

    def _navigate(self, event):
        key    = event.key()
        mods   = event.modifiers()
        extend = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        b      = self._b
        if key == Qt.Key.Key_Down:
            b.navigateRow(1, extend); return True
        if key == Qt.Key.Key_Up:
            b.navigateRow(-1, extend); return True
        if key == Qt.Key.Key_PageDown:
            b.navigateRow(max(5, self._qml.height() // 58), extend); return True
        if key == Qt.Key.Key_PageUp:
            b.navigateRow(-max(5, self._qml.height() // 58), extend); return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if b._selected_trkidx >= 0:
                b.playSelected(); return True
        if key == Qt.Key.Key_Escape and b._selected_trkidx >= 0:
            b._selected_trkidx = -1
            b.selectedTrackChanged.emit(-1)
            return True
        return False


class TracksBrowser(QWidget):
    play_track = pyqtSignal(dict)
    play_multiple_tracks = pyqtSignal(list)
    shuffle_tracks = pyqtSignal(list)
    _scan_done = pyqtSignal()
    queue_track = pyqtSignal(dict)
    play_next = pyqtSignal(dict)
    start_radio = pyqtSignal(dict)
    switch_to_artist_tab = pyqtSignal(str)
    switch_to_album_tab = pyqtSignal(dict)

    @property
    def client(self):
        return getattr(self, '_client', None)

    @client.setter
    def client(self, value):
        # External code (mixins/navigation.py's _ensure_tracks_client_ready)
        # assigns this lazily after construction — keep the QML cover image
        # provider's client reference in sync when that happens.
        self._client = value
        if hasattr(self, '_track_thumb_provider'):
            self._track_thumb_provider.set_client(value)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.last_reload_time = time.time()
        self._settings = QSettings("Icosahedron", "Icosahedron")
        self.sync_worker = None
        self.current_accent = "#0066cc" # Default, updated by set_accent_color
     
        # --- PAGINATION SETTINGS ---
        self.page_size = 200
        self.current_page = 1
        self.total_pages = 1
        self.total_items = 0
        
        self.current_query = ""
        self.load_sort_state() # 🟢 Loads saved state, defaults to ALBUM (4) Ascending
        
        self.search_timer = QTimer()

        self.page_click_times = []
        self.db_worker = None
        self.skeleton_timer = QTimer()
        self.skeleton_timer.setSingleShot(True)
        self.skeleton_timer.timeout.connect(self.show_skeleton_ui)

        # 🟢 THE FIX: Debounce timer to prevent rapid clicking from freezing UI!
        self.page_debounce_timer = QTimer()
        self.page_debounce_timer.setSingleShot(True)
        self.page_debounce_timer.setInterval(150) # Wait 150ms after user STOPS clicking
        self.page_debounce_timer.timeout.connect(self._execute_load)
        self.pending_page = None
        self.db_worker = None


        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(400)
        self.search_timer.timeout.connect(self.execute_search)
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 0; }")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- HEADER ---
        # Search box, refresh, burger (column picker), clear-filters and
        # play-filtered controls all live inside tracks_list.qml's
        # TrackListView toolbar now (see TrackListView.qml's
        # enableOwnSearch/enableRefreshButton/enableClearFiltersButton/
        # enablePlayFilteredButton). refresh_btn is kept as a plain
        # (never-shown) QObject purely so its existing start_spin/
        # set_color calls elsewhere keep working without change.
        self.refresh_btn = SpinRefreshButton(
            icon_path=resource_path("img/refresh.png"),
            icon_size=18, btn_size=32, color='#ffffff')
        self.refresh_btn.setToolTip("Refresh library from server")
        self.refresh_btn.clicked.connect(self._refresh_library)

        # The QML "play filtered" button's click-vs-press+hold(600ms) timing
        # is handled entirely in TrackListView.qml; TracksBridge.
        # playFilteredClicked/shuffleFilteredClicked call straight into
        # _fetch_all_filtered_tracks/_shuffle_filtered_tracks below.

        # --- QML TRACK LIST (TrackListView.qml, via tracks_list.qml) ───────
        # Column filters: col -> set of allowed string values (empty set = no filter)
        self._col_filters = {}
        self._col_filter_values = {}  # col -> list of all known values for the popup
        self._col_id_map = {}         # col -> {display_value: server_id}
        self._filter_values_worker = None
        self._active_filter_popup = None

        self.tracks = []             # current page's track dicts (parallel to tracks_model rows)
        self.tracks_model = TracksTrackModel()
        self.tracks_bridge = TracksBridge(self)

        self.qml_view = QMLGridWrapper()
        self.qml_view.setClearColor(QColor(14, 14, 14))  # avoid white-flash before theme applies
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self.qml_view)

        self._key_filter = _TracksKeyFilter(self.tracks_bridge, self.qml_view)
        self.qml_view.installEventFilter(self._key_filter)

        self._track_thumb_provider = TrackThumbProvider()
        self._track_thumb_provider.set_client(self.client)
        self._icon_provider = AlbumIconProvider()
        engine = self.qml_view.engine()
        engine.addImageProvider("trackscovers", self._track_thumb_provider)
        engine.addImageProvider("albumicons",   self._icon_provider)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("tracksModel",  self.tracks_model)
        ctx.setContextProperty("tracksBridge", self.tracks_bridge)
        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("player/tabs/tracks/tracks_list.qml")))

        self.main_layout.addWidget(self.qml_view, 1)

        # --- SETUP FOOTER ---
        self.footer = PaginationFooter()
        self.footer.page_changed.connect(self.change_page)
        self.main_layout.addWidget(self.footer)

        self.is_loading_db = False
        self.load_from_db(reset=True, invalidate_filter_cache=True)

    def save_sort_state(self):
        state = {
            'col': self.sort_col,
            'order': 0 if self.sort_order == Qt.SortOrder.AscendingOrder else 1
        }
        try:
            self._settings.setValue('tracks_sort_state', json.dumps(state))
        except: pass

    def load_sort_state(self):
        try:
            state_str = self._settings.value('tracks_sort_state')
            if state_str:
                state = json.loads(state_str)
                self.sort_col = state.get('col', 11)
                self.sort_order = Qt.SortOrder.AscendingOrder if state.get('order', 0) == 0 else Qt.SortOrder.DescendingOrder
            else:
                self.sort_col = 11
                self.sort_order = Qt.SortOrder.DescendingOrder
        except:
            self.sort_col = 11
            self.sort_order = Qt.SortOrder.DescendingOrder
    
   


    # Optional: Force focus loss if clicking the background area of the widget
    def mousePressEvent(self, event):
        self.setFocus() # Steal focus from search input
        super().mousePressEvent(event)
    
        
    def get_filtered_count(self, query_text):
        # Count is determined by LiveTrackWorker results; return cached total
        return getattr(self, 'total_items', 0)

    def check_for_updates(self):
        pass  # Updates are driven by SmartBackgroundSyncer signals, not local DB polling.

    def _refresh_library(self):
        """Trigger a server scan then re-fetch when done."""
        if not self.client:
            return
        self.refresh_btn.start_spin()
        self.refresh_btn.setToolTip("Scanning library…")
        self._scan_done.connect(self._on_scan_finished)

        # Trigger scan in background thread, poll until done, then reload
        def _do_refresh():
            try:
                self.client.start_scan()
                import time
                for _ in range(60):  # wait up to 30s
                    time.sleep(0.5)
                    if not self.client.is_scanning():
                        # Navidrome's is_scanning() flag can flip to "done"
                        # slightly before the index write actually commits —
                        # settle briefly and re-check once before trusting it,
                        # so the refetch that follows doesn't race a still-
                        # finishing scan and silently return pre-scan data.
                        time.sleep(1.5)
                        if not self.client.is_scanning():
                            break
            except Exception as e:
                print(f"[Refresh] scan error: {e}")
            finally:
                self._scan_done.emit()

        import threading
        threading.Thread(target=_do_refresh, daemon=True).start()

    def _on_scan_finished(self):
        try: self.client.reset_caches()
        except: pass
        try: self.client._api_cache.cache.clear()
        except: pass
        try: self.client._page_cache.clear()
        except: pass
        self._col_filter_values = {}  # invalidate popup cache; keep _col_id_map so active filters still resolve
        self.refresh_btn._do_stop()
        self.refresh_btn.setToolTip("Refresh library from server")
        self.load_from_db(reset=True)
    
    def change_page(self, page):
        if page < 1 or page > self.total_pages: return
        
        self.current_page = page
        if hasattr(self, 'footer'):
            self.footer.render_pagination(self.current_page, self.total_pages)
        
        self.load_from_db(reset=False)

    def _execute_load(self):
        """Fires 150ms after the user stops clicking."""
        if self.pending_page is not None:
            self.current_page = self.pending_page
            self.pending_page = None
        self.load_from_db(reset=False)

    def load_from_db(self, reset=False, invalidate_filter_cache=False):
        if reset:
            self.current_page = 1
            self.total_items = 0
            self.total_pages = 1
        if invalidate_filter_cache:
            # Only clear filter values when the search query changes, not on filter apply
            self._col_filter_values = {}
            self._col_id_map = {}
        self._start_worker(is_album=False, album_id=None)

    def _start_filter_values_worker(self, is_album, album_id):
        if not self.client:
            return
        if self._filter_values_worker and self._filter_values_worker.isRunning():
            self._filter_values_worker.is_cancelled = True
            try: self._filter_values_worker.values_ready.disconnect()
            except: pass
        w = FilterValuesWorker(self.client, self.current_query, is_album, album_id)
        w.values_ready.connect(self._on_filter_values_ready)
        self._filter_values_worker = w
        w.start()

    def _on_filter_values_ready(self, col_values, id_maps):
        self._col_filter_values = col_values
        self._col_id_map = id_maps
        # Reconnect the normal handler (may have been temporarily replaced)
        w = self._filter_values_worker
        if w:
            try: w.values_ready.disconnect()
            except: pass
            w.values_ready.connect(self._on_filter_values_ready)

    def _build_server_filters(self):
        """Convert _col_filters to Navidrome /api/song params. Always server-side, no client fallback."""
        server_params = {}

        for col, allowed in self._col_filters.items():
            if not allowed:
                continue

            if col == 7:  # starred — single boolean param
                val = next(iter(allowed))
                server_params['starred'] = 'true' if val == 'True' else 'false'

            elif col == 5:  # year — single value
                server_params['year'] = next(iter(allowed))

            elif col in (3, 4, 6):  # artist_id / album_id / genre_id — supports multiple
                id_map = self._col_id_map.get(col, {})
                param = {3: 'artist_id', 4: 'album_id', 6: 'genre_id'}[col]
                ids = [id_map[v] for v in allowed if v in id_map]
                if ids:
                    server_params[param] = ids

        return server_params

    def _start_worker(self, is_album, album_id):
        # 🟢 THE CRASH FIX: Save old workers in a graveyard so Python doesn't delete them while C++ is still running!
        if not hasattr(self, 'dead_workers'): 
            self.dead_workers = []
            
        if hasattr(self, 'live_worker') and self.live_worker is not None:
            self.live_worker.is_cancelled = True
            
            # 🟢 FIX: The signal is called results_ready!
            try: self.live_worker.results_ready.disconnect()
            except: pass
            
            # Send to graveyard instead of deleting
            self.dead_workers.append(self.live_worker)
            self.live_worker = None
            
        # Clean up any dead workers that have finally finished their ghost tasks
        self.dead_workers = [w for w in self.dead_workers if w.isRunning()]

        known_total = getattr(self, 'total_items', 0)
        
        # 🟢 Grab the current UI sort state!
        sort_str = self.get_sort_string()
        sort_field, sort_order = sort_str.split(" ")

        server_params = self._build_server_filters() if self._col_filters else {}

        self.live_worker = LiveTrackWorker(
            client=self.client,
            query_text=self.current_query,
            page=self.current_page,
            page_size=self.page_size,
            is_album_mode=is_album,
            album_id=album_id,
            known_total=known_total,
            sort_field=sort_field,
            sort_order=sort_order,
            server_filters=server_params or None,
        )
        self.live_worker.results_ready.connect(self.on_worker_finished)

        self.show_skeleton_ui()
        self.live_worker.start()

    def on_worker_finished(self, tracks, total_items, total_pages, target_page):
        # 🟢 SAFETY CHECK
        if self.sender() and getattr(self.sender(), 'is_cancelled', False):
            return

        # Only update total/pages from a successful (non-empty) response so a
        # transient error never resets total_pages to 1 and breaks navigation.
        if total_items > 0 or not getattr(self, 'total_items', 0):
            self.total_items = total_items
            self.total_pages = total_pages
        self.current_page = target_page

        count_text = f"{self.total_items:,} tracks".replace(",", " ")
        self.tracks_bridge.trackCountChanged.emit(count_text)
        if hasattr(self, 'footer'):
            self.footer.render_pagination(self.current_page, self.total_pages)

        # Detected BPM always beats the ID3 tag value
        win = self.window()
        bpm_cache = getattr(win, 'bpm_cache', {}) if win else {}
        if bpm_cache:
            for t in tracks:
                tid = str(t.get('id', ''))
                if tid in bpm_cache:
                    t['bpm'] = bpm_cache[tid]

        self.tracks = tracks
        offset = (self.current_page - 1) * self.page_size
        self.tracks_model.set_tracks(tracks, index_offset=offset)
        self.tracks_bridge.tracksLoadingChanged.emit(False)

        if hasattr(self, 'current_playing_id'):
            self.update_playing_status(getattr(self, 'current_playing_id'), getattr(self, 'is_playing', False), getattr(self, 'playing_color', "#1DB954"))

        if self.tracks:
            focus_idx = len(self.tracks) - 1 if getattr(self, 'pending_focus_direction', 'top') == 'bottom' else 0
            self.pending_focus_direction = 'top'
            self.tracks_bridge.selectedTrackChanged.emit(focus_idx)
            if focus_idx > 0:
                self.tracks_bridge.scrollToBottomOfView.emit()
            else:
                self.tracks_bridge.scrollToTopOfView.emit()

        # Kick off filter values worker in background after first data load,
        # so values+IDs are ready by the time user opens a filter popup.
        if not self._col_filter_values:
            self._start_filter_values_worker(is_album=False, album_id=None)

    def show_skeleton_ui(self):
        self.tracks_bridge.tracksLoadingChanged.emit(True)

    def refresh_track_item(self, track_id, fresh):
        """Patch a single track's metadata in-place (called externally when a
        background metadata fetch completes) and push the refresh into the model."""
        for t in self.tracks:
            if str(t.get('id', '')) == str(track_id):
                t.update({k: fresh.get(k, t.get(k)) for k in ('title', 'artist', 'album', 'year')})
                break
        for i, r in enumerate(self.tracks_model._rows):
            if r.get('_id') == str(track_id):
                r['_title']  = str(fresh.get('title')  or r['_title'])
                r['_artist'] = str(fresh.get('artist') or r['_artist'])
                r['_album']  = str(fresh.get('album')  or r['_album'])
                r['_year']   = str(fresh.get('year')   or r['_year'])
                model_idx = self.tracks_model.index(i, 0)
                self.tracks_model.dataChanged.emit(model_idx, model_idx)
                break

    def refresh_track_bpm(self, track_id, bpm):
        for t in self.tracks:
            if str(t.get('id', '')) == str(track_id):
                t['bpm'] = bpm
                break
        bpm_str = f"{bpm:.1f}" if bpm > 0 else ''
        self.tracks_model.update_bpm(str(track_id), bpm_str)

    def update_playing_status(self, playing_id, is_playing, color_hex):
        self.current_playing_id = playing_id
        self.is_playing = is_playing
        self.playing_color = color_hex
        self.tracks_bridge.playingStatusChanged.emit(str(playing_id or ''), bool(is_playing))

    # --- COLUMN FILTERS ---

    # Map column index to track dict key
    _COL_FIELD = {
        3: 'artist', 4: 'album',
        5: 'year',   6: 'genre', 7: 'starred',
    }

    def _on_filter_clicked(self, col, global_rect):
        if col not in self._COL_FIELD:
            return
        if self._active_filter_popup:
            self._active_filter_popup.close()
            self._active_filter_popup = None

        # If worker is still running, wait for it then open the popup
        worker = self._filter_values_worker
        if worker and worker.isRunning():
            def _open_when_ready(col_values, id_maps, col=col, global_rect=global_rect):
                self._on_filter_values_ready(col_values, id_maps)
                self._open_filter_popup(col, global_rect)
            try: worker.values_ready.disconnect()
            except: pass
            worker.values_ready.connect(_open_when_ready)
            return

        self._open_filter_popup(col, global_rect)

    def _values_from_tree(self, col):
        """Derive unique filter values for col from the currently loaded page's tracks."""
        vals = set()
        # Multi-value separator pattern (matches artist/genre delegate splitting)
        sep = re.compile(r' /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, ')
        multi_val_cols = {3, 6}  # artist, genre — may have multiple values per cell
        field = self._COL_FIELD.get(col)
        for t in self.tracks:
            text = str(t.get(field, '') or '')
            if not text:
                continue
            if col in multi_val_cols:
                for part in sep.split(text):
                    part = part.strip()
                    if part:
                        vals.add(part)
            else:
                vals.add(text)
        return sorted(vals, key=lambda x: str(x).lower())

    def _open_filter_popup(self, col, global_rect):
        # If other filters are active, derive values from the loaded page (cascading)
        other_filters_active = any(c != col for c in self._col_filters)
        if other_filters_active:
            values = self._values_from_tree(col)
        else:
            values = self._col_filter_values.get(col, [])
        active = self._col_filters.get(col, set())
        up_icon = QIcon(resource_path("img/filter_up.png"))
        down_icon = QIcon(resource_path("img/filter_down.png"))
        popup = ColumnFilterPopup(col, values, active, up_icon, down_icon, accent_color=getattr(self, 'current_accent', '#cccccc'), parent=self)
        popup.filters_applied.connect(self._apply_col_filter)
        popup.sort_requested.connect(self._on_sort_from_popup)
        pad = popup._SHADOW_PAD
        popup.move(global_rect.left() - pad, global_rect.bottom() - pad)
        popup.show()
        self._active_filter_popup = popup
        popup.destroyed.connect(lambda: setattr(self, '_active_filter_popup', None))

    def _apply_col_filter(self, col, values):
        if values:
            self._col_filters[col] = values
        else:
            self._col_filters.pop(col, None)
        self._active_filter_popup = None
        self._update_clear_filters_btn()
        self.load_from_db(reset=True)

    def _clear_all_filters(self):
        self._col_filters = {}
        self._update_clear_filters_btn()
        self.load_from_db(reset=True)

    def _filter_by_album(self, album_name, album_id):
        """Apply album column filter, seeding the ID map from the track data directly."""
        if 4 not in self._col_id_map:
            self._col_id_map[4] = {}
        self._col_id_map[4][album_name] = album_id
        self._apply_col_filter(4, {album_name})

    def _filter_by_artist(self, artist_name, artist_id=None):
        """Apply artist column filter, seeding the ID map from the track data if needed."""
        if 3 not in self._col_id_map:
            self._col_id_map[3] = {}
        if artist_id and artist_name not in self._col_id_map[3]:
            self._col_id_map[3][artist_name] = artist_id
        self._apply_col_filter(3, {artist_name})

    def _update_clear_filters_btn(self):
        self.tracks_bridge.filtersActiveChanged.emit(bool(self._col_filters))

    def _get_filtered_tracks(self):
        return list(self.tracks)

    def _fetch_all_filtered_tracks(self, callback):
        """Fetch all filtered tracks across all pages, then call callback(tracks)."""
        total = getattr(self, 'total_items', 0)
        if total <= self.page_size:
            callback(self._get_filtered_tracks())
            return

        sort_str = self.get_sort_string()
        sort_field, sort_order = sort_str.split(" ")
        server_params = self._build_server_filters() if self._col_filters else {}
        client = self.client
        query = self.current_query

        worker = LiveTrackWorker(
            client=client,
            query_text=query,
            page=1,
            page_size=total,
            is_album_mode=False,
            album_id=None,
            known_total=total,
            sort_field=sort_field,
            sort_order=sort_order,
            server_filters=server_params or None,
        )
        if not hasattr(self, 'dead_workers'):
            self.dead_workers = []
        self.dead_workers.append(worker)

        def _on_ready(tracks, *_):
            try: worker.results_ready.disconnect()
            except: pass
            callback(tracks)

        worker.results_ready.connect(_on_ready)
        worker.start()

    def _shuffle_filtered_tracks(self):
        import random
        def _do_shuffle(tracks):
            if tracks:
                shuffled = tracks[:]
                random.shuffle(shuffled)
                self.play_multiple_tracks.emit(shuffled)
        self._fetch_all_filtered_tracks(_do_shuffle)

    def _on_sort_from_popup(self, col, order):
        self._active_filter_popup = None
        qt_order = Qt.SortOrder.AscendingOrder if order == "ASC" else Qt.SortOrder.DescendingOrder
        self.sort_col = col
        self.sort_order = qt_order
        self.save_sort_state()
        col_str = self.tracks_bridge._COL_INT_TO_STR.get(col, '')
        self.tracks_bridge.sortStateChanged.emit(col_str, 'asc' if qt_order == Qt.SortOrder.AscendingOrder else 'desc')
        self.load_from_db(reset=True)

    def showEvent(self, event):
        super().showEvent(event)
        self.check_for_updates()
        # Grab keyboard focus every time this tab becomes visible — don't
        # rely solely on mixins/navigation.py's tab-switch handler, which
        # may run before the QML view is fully laid out on first visit.
        QTimer.singleShot(0, lambda: self.qml_view.setFocus(Qt.FocusReason.OtherFocusReason))

    def get_sort_string(self):
        col = self.sort_col
        order = "ASC" if self.sort_order == Qt.SortOrder.AscendingOrder else "DESC"
        
        # 🟢 Map directly to Navidrome internal database columns!
        if col == 0: field = "trackNumber" 
        elif col == 1: field = "title"
        elif col == 2: field = "title"
        elif col == 3: field = "artist" 
        elif col == 4: field = "album" 
        elif col == 5: field = "year"
        elif col == 6: field = "genre"
        elif col == 7: field = "starred" 
        elif col == 8: field = "playCount"
        elif col == 9: field = "duration"
        elif col == 10: field = "trackNumber"
        elif col == 11: field = "createdAt"
        elif col == 12: field = "bpm"
        else: field = "title"
        
        return f"{field} {order}"

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()
    
    def execute_search(self):
        # Reset all column filters when user starts a local search
        if self._col_filters:
            self._col_filters = {}
            self._update_clear_filters_btn()
        self.load_from_db(reset=True, invalidate_filter_cache=True)

    def keyPressEvent(self, event):
        key = event.key()

        # 🟢 ENTER / RETURN: Play the currently selected track
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            idx = self.tracks_bridge._selected_trkidx
            if 0 <= idx < len(self.tracks):
                self.play_track.emit(self.tracks[idx])
            event.accept()
            return

        super().keyPressEvent(event)

    def toggle_track_favorite(self, track_idx: int):
        """Toggle favorite for a single track index, syncing the model + server."""
        if not (0 <= track_idx < len(self.tracks)):
            return
        track = self.tracks[track_idx]
        raw_state = track.get('starred')
        current_state = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)
        new_state = not current_state
        track['starred'] = new_state
        self.tracks_model.update_favorite(track_idx, new_state)
        if self.client:
            self.client.set_favorite(track.get('id'), new_state)
    
    def _show_track_context_menu_at(self, indices: list, gx: int, gy: int):
        indices = sorted(i for i in indices if 0 <= i < len(self.tracks))
        if not indices: return
        selected_tracks = [self.tracks[i] for i in indices]

        count = len(selected_tracks); is_multi = count > 1; first_track = selected_tracks[0]
        track_ids = [str(t.get('id')) for t in selected_tracks if t.get('id')]

        from player.widgets import ShadowContextMenu
        main_win = self.window()
        _theme = getattr(main_win, 'theme', None)
        bg  = getattr(_theme, 'main_panel_bg',        '14,14,14') if _theme else '14,14,14'
        bc  = getattr(_theme, 'border_color',          '#2a2a2a') if _theme else '#2a2a2a'
        fg  = getattr(_theme, 'font_color_primary',    '#dddddd') if _theme else '#dddddd'
        fg2 = getattr(_theme, 'font_color_secondary',  '#555555') if _theme else '#555555'
        px  = getattr(_theme, 'font_size_primary',     14)        if _theme else 14
        acc = getattr(_theme, 'accent',                '#cccccc') if _theme else '#cccccc'
        if _theme and not getattr(_theme, 'auto_border_from_accent', True):
            bc = getattr(_theme, 'manual_border_color', '#2a2a2a')
        hov = resolve_menu_hover(_theme)

        menu = ShadowContextMenu(self)
        menu.configure(bg, bc, fg, fg2, hov, px, accent=acc)

        # ── Playback ──────────────────────────────────────────────────────────
        play_lbl  = f"Play Now ({count})"  if is_multi else "Play Now"
        next_lbl  = f"Play Next ({count})" if is_multi else "Play Next"
        queue_lbl = f"Add to Queue ({count})" if is_multi else "Add to Queue"

        if is_multi:
            menu.add_action(play_lbl,  lambda: self.play_multiple_tracks.emit(selected_tracks), icon_path='img/sub_play.png')
            menu.add_action(next_lbl,  lambda: [self.play_next.emit(t) for t in reversed(selected_tracks)], icon_path='img/sub_next.png')
            menu.add_action(queue_lbl, lambda: [self.queue_track.emit(t) for t in selected_tracks], icon_path='img/queue.png')
        else:
            menu.add_action(play_lbl,  lambda: self.play_track.emit(first_track),  icon_path='img/sub_play.png')
            menu.add_action(next_lbl,  lambda: self.play_next.emit(first_track),   icon_path='img/sub_next.png')
            menu.add_action(queue_lbl, lambda: self.queue_track.emit(first_track), icon_path='img/queue.png')
            artist = first_track.get('artist', '')
            album_id = first_track.get('albumId') or first_track.get('parent')
            album_data = {'id': album_id, 'title': first_track.get('album', ''),
                          'artist': artist,
                          'coverArt': first_track.get('coverArt') or first_track.get('cover_id', '')}
            menu.add_action('Go to Artist', lambda: self.switch_to_artist_tab.emit(artist) if artist else None,
                            enabled=bool(artist), icon_path='img/sub_artist.png')
            menu.add_action('Open Album',   lambda: self.switch_to_album_tab.emit(album_data) if album_id else None,
                            enabled=bool(album_id), icon_path='img/album.png')
            menu.add_action('Start Radio',  lambda: self.start_radio.emit(first_track), icon_path='img/radio.png')

        # ── Playlist ──────────────────────────────────────────────────────────
        if track_ids:
            playlists = getattr(getattr(main_win, 'playlists_browser', None), 'all_playlists', None) or []
            pl_items = [('New Playlist…', lambda: self._add_to_new_playlist(track_ids), 'img/add.png')]
            pl_items += [(f"{pl.get('name','Unnamed')}  ({pl.get('songCount','')})" if pl.get('songCount','') != '' else pl.get('name','Unnamed'),
                          lambda _, pid=pl.get('id'), pn=pl.get('name',''): self._add_to_existing_playlist(pid, pn, track_ids),
                          'img/playlist.png')
                         for pl in playlists if pl.get('id')]
            menu.add_submenu('Add to Playlist', pl_items, icon_path='img/playlist.png')

        # ── Unique: Filter by album/artist ────────────────────────────────────
        if not is_multi:
            album_name = first_track.get('album', '')
            album_id   = first_track.get('albumId') or first_track.get('parent')
            primary_artist_id = first_track.get('artist_id') or first_track.get('artistId')
            f_artists = [p.strip() for p in re.split(
                r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )',
                first_track.get('artist', '')) if p.strip()]
            filter_items = []
            if album_name and album_id:
                filter_items.append((f"Album: {album_name}",
                                     lambda _an=album_name, _aid=album_id: self._filter_by_album(_an, _aid),
                                     'img/album.png'))
            for i, art in enumerate(f_artists):
                aid = primary_artist_id if i == 0 else None
                filter_items.append((f"Artist: {art}",
                                     lambda _a=art, _aid=aid: self._filter_by_artist(_a, _aid),
                                     'img/sub_artist.png'))
            if filter_items:
                menu.add_submenu('Filter by', filter_items, icon_path='img/filter.png')

        # ── Unique: Adjust BPM ───────────────────────────────────────────────
        if not is_multi:
            _bpm_cache = getattr(main_win, 'bpm_cache', {}) if main_win else {}
            raw_bpm = _bpm_cache.get(str(first_track.get('id', ''))) or first_track.get('bpm') or 0
            try: current_bpm = float(raw_bpm)
            except (TypeError, ValueError): current_bpm = 0.0
            if current_bpm > 0:
                def _fmt(v): return f"{v:.2f}".rstrip('0').rstrip('.') + ' BPM'
                bpm_items = [(f"{lbl}  |  {_fmt(current_bpm * m)}",
                              lambda _, v=current_bpm * m: self._apply_bpm(first_track, v))
                             for lbl, m in [("Half",.5),("2/3",2/3),("3/4",3/4),("4/3",4/3),("3/2",3/2),("Double",2.)]]
                menu.add_submenu('Adjust BPM', bpm_items, icon_path='img/bpm.png')

        # ── Get Info / Favorite ───────────────────────────────────────────────
        if not is_multi:
            menu.add_action('Get Info', lambda: self._show_track_info(first_track), icon_path='img/info.png')

        is_fav = bool(first_track.get('starred')) if not is_multi else False
        fav_lbl = (f"Toggle Favorite ({count})" if is_multi
                   else ('Remove from Favorites' if is_fav else 'Add to Favorites'))
        if is_multi:
            menu.add_action(fav_lbl,
                            lambda: [self.toggle_track_favorite(i) for i in indices],
                            color='#E91E63', icon_path='img/heart.png')
        else:
            menu.add_action(fav_lbl,
                            lambda: self.toggle_track_favorite(indices[0]),
                            color='#E91E63',
                            icon_path='img/heart_filled.png' if is_fav else 'img/heart.png')

        from PyQt6.QtCore import QPoint as _QPoint
        gp = _QPoint(int(gx), int(gy))
        menu.exec_at(gp.__class__(gp.x() - menu._PAD, gp.y() - menu._PAD), window=main_win)

    def _apply_bpm(self, track, new_bpm):
        rounded = round(new_bpm, 1)
        track['bpm'] = rounded
        song_id = str(track.get('id', ''))

        win = self.window()
        if song_id and hasattr(win, 'bpm_cache'):
            win.bpm_cache[song_id] = rounded
            if hasattr(win, 'save_bpm_cache'):
                win.save_bpm_cache()
            # Update matching entries in playlist_data
            if hasattr(win, 'playlist_data'):
                for t in win.playlist_data:
                    if str(t.get('id', '')) == song_id:
                        t['bpm'] = rounded
            # Refresh footer if this is the current track
            if hasattr(win, 'current_index') and hasattr(win, 'playlist_data'):
                idx = win.current_index
                if 0 <= idx < len(win.playlist_data):
                    if str(win.playlist_data[idx].get('id', '')) == song_id:
                        if hasattr(win, 'now_playing_widget'):
                            win.now_playing_widget.set_bpm(rounded)
                        if hasattr(win, 'file_type_label') and hasattr(win, 'current_file_type_text'):
                            win.file_type_label.setText(
                                f"{win.current_file_type_text}   •   {rounded:.1f} BPM"
                            )

        self.refresh_track_bpm(song_id, rounded)

    def _show_track_info(self, track):
        client = getattr(self, 'client', None) or getattr(self.window(), 'navidrome_client', None)
        win = self.window()
        accent = getattr(getattr(win, 'theme', None), 'accent', None) or getattr(win, 'master_color', None) or '#1DB954'
        bpm_cache = getattr(win, 'bpm_cache', {}) if win else {}
        tid = str(track.get('id', ''))
        detected_bpm = bpm_cache.get(tid)
        # Store original ID3 BPM separately so the dialog can show both
        track['_id3_bpm'] = track.get('_id3_bpm') or track.get('bpm')
        album_data = {
            'id': track.get('albumId'),
            'title': track.get('album', ''),
            'artist': track.get('artist', ''),
            'coverArt': track.get('cover_id'),
        }
        dlg = TrackInfoDialog(
            track, client=client, accent_color=accent, parent=self,
            on_artist_click=lambda name: self.switch_to_artist_tab.emit(name),
            on_album_click=lambda _: self.switch_to_album_tab.emit(album_data),
            on_genre_click=lambda g: win.navigate_to_genre(g) if win and hasattr(win, 'navigate_to_genre') else self._apply_col_filter(6, {g}),
            detected_bpm=detected_bpm,
        )
        if hasattr(win, 'show_dim'):
            win.show_dim()
        dlg.exec()
        if hasattr(win, 'hide_dim'):
            win.hide_dim()
        
    
    def _add_to_existing_playlist(self, playlist_id, playlist_name, track_ids):
        """Appends tracks to an existing playlist; shows brief feedback in the status label."""
        import threading
        if not self.client: return

        def worker():
            try:
                # 🟢 Using the self.client fix we added earlier!
                self.client.add_tracks_to_playlist(playlist_id, track_ids)
                msg = f"Added {len(track_ids)} tracks to playlist"
            except Exception as e:
                msg = f"Failed: {e}"
                
            # pyqtSignal.emit() is thread-safe (queues to the GUI thread) —
            # no QMetaObject.invokeMethod dance needed.
            import time
            self.tracks_bridge.trackCountChanged.emit(msg)
            time.sleep(3)
            self.tracks_bridge.trackCountChanged.emit(f"{getattr(self, 'total_items', 0)} tracks")

        threading.Thread(target=worker, daemon=True).start()

    def _add_to_new_playlist(self, track_ids):
        """Prompts for a name using the custom dialog, creates the playlist, then appends the tracks."""
        from player.components.shared_widgets import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        
        if not self.client: return
        
        # Inherit the current accent color from the TracksBrowser
        accent = getattr(self, 'current_accent', "#1DB954")
        
        # Show our sleek custom dialog
        dialog = NewPlaylistDialog(self.window(), accent_color=accent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            is_public = dialog.is_public() # 🟢 GET TOGGLE
            if not name: return

            def worker():
                try:
                    # 🟢 THE FIX: Add "self." before client!
                    new_id = self.client.create_playlist(name, public=is_public)
                    
                    if new_id:
                        self.client.add_tracks_to_playlist(new_id, track_ids)
                        msg = f"Added {len(track_ids)} tracks to new playlist"
                    else:
                        msg = "Failed to create playlist"
                        
                except Exception as e:
                    msg = f"Failed: {e}"

                import time
                self.tracks_bridge.trackCountChanged.emit(msg)
                time.sleep(3)
                self.tracks_bridge.trackCountChanged.emit(f"{getattr(self, 'total_items', 0)} tracks")

            threading.Thread(target=worker, daemon=True).start()

    def set_accent_color(self, color):
        _theme = getattr(self.window(), 'theme', None)
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.set_color(color)

        if hasattr(self, 'footer'):
            self.footer.set_accent_color(color)

        self.current_accent = color

        b = self.tracks_bridge
        b.accentColorChanged.emit(color)
        b.hoverColorChanged.emit(resolve_menu_hover(_theme) if _theme else '#555555')
        if _theme:
            b.fontSizePrimaryChanged.emit(_theme.font_size_primary)
            b.fontSizeSecondaryChanged.emit(_theme.font_size_secondary)
            b.fontColorPrimaryChanged.emit(_theme.font_color_primary)
            b.fontColorSecondaryChanged.emit(_theme.font_color_secondary)
            b.fontFamilyChanged.emit(getattr(_theme, 'app_font', ''))
            b.skeletonColorChanged.emit(getattr(_theme, 'skeleton_base', '#282828'))
            b.cardBgChanged.emit(getattr(_theme, 'now_playing_card_bg', '#1e1e1e'))
            border = getattr(_theme, 'border_color', '#2a2a2a')
            if not getattr(_theme, 'auto_border_from_accent', True):
                border = getattr(_theme, 'manual_border_color', '#2a2a2a')
            b.cardBorderChanged.emit(border)
            raw_bg = getattr(_theme, 'main_panel_bg', '14,14,14')
            try:
                r, g, bb = (int(x) for x in raw_bg.split(','))
                b.panelBgChanged.emit('#{:02x}{:02x}{:02x}'.format(r, g, bb))
            except Exception:
                b.panelBgChanged.emit('#0e0e0e')

        if hasattr(self, 'current_playing_id'):
            self.update_playing_status(self.current_playing_id, getattr(self, 'is_playing', False), color)

    def update_scrollbar_color(self, color_hex):
        """Kept for backward compatibility with callers that still poke the
        scrollbar color directly — the QML scrollbar reads accentColor from
        set_accent_color, so this just forwards there."""
        self.set_accent_color(color_hex)

    def _toggle_track_search(self):
        """Global "/" shortcut entry point (player/mixins/keyboard.py) —
        same convention as AlbumDetailView/PlaylistDetailView."""
        if self.tracks_bridge.search.active:
            self.tracks_bridge.search.close()
        else:
            self.tracks_bridge.search.open()

    def _set_window_shortcuts_enabled(self, enabled: bool):
        set_window_shortcuts_enabled(self, self.qml_view, enabled)

    def set_bg_color(self, c: str):
        self._bg_color = c
        self.setStyleSheet(f"#{self.objectName()} {{ background-color: rgb({c}); border-radius: 0; }}")
        try:
            r, g, b = (int(x) for x in c.split(','))
            self.qml_view.setClearColor(QColor(r, g, b))
        except Exception:
            pass