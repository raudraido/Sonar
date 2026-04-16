import re
import os
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



from PyQt6.QtCore import (Qt, pyqtSignal, QTimer, QModelIndex, QEvent, QPoint, QRect,
                          QPropertyAnimation, QEasingCurve, QSize, QParallelAnimationGroup,
                          QRectF, QThread, QSettings, QObject)

from PyQt6.QtGui import QAction, QColor, QCursor, QFontMetrics, QIcon, QPainter, QPixmap, QPainterPath, QFont

from albums_browser import resource_path
from components import PaginationFooter, SmartSearchContainer, TrackInfoDialog

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import OrderedDict

import os as _os
_COVER_WORKERS = min(6, (_os.cpu_count() or 2) + 2)



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
            from cover_cache import THUMB_SIZE
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
                            from cover_cache import CoverCache
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


class ColumnFilterPopup(QFrame):
    """Excel-style column filter popup: sort rows + search box + multi-select checklist."""
    filters_applied = pyqtSignal(int, set)  # col, selected values
    sort_requested  = pyqtSignal(int, str)  # col, "ASC" or "DESC"

    # Columns that map to server-side ID lists — Navidrome limits these
    ID_FILTER_COLS = {3, 4, 6}
    MAX_ID_FILTER_VALUES = 10

    def __init__(self, col, values, active_values, up_icon, down_icon, accent_color="#cccccc", parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.col = col
        self.active_values = set(active_values) if active_values else set()
        self.all_values = sorted(values, key=lambda v: str(v).lower())
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            ColumnFilterPopup {{
                background: #0d0d0d; border: 1px solid #2a2a2a; border-radius: 6px;
            }}
            QLineEdit {{
                background: #080808; color: #ddd; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid #555; }}
            QListWidget {{
                background: transparent; border: none; color: #ddd; font-size: 13px;
            }}
            QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
            QListWidget::item:hover {{ background: rgba(255,255,255,0.07); }}
            QListWidget::item:selected {{ background: rgba(255,255,255,0.07); color: #ddd; }}
            QPushButton {{
                background: #111; color: #ddd; border: 1px solid #2a2a2a;
                border-radius: 4px; padding: 4px 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.1); }}
            QFrame#sort_row {{ background: transparent; }}
            QFrame#sort_row:hover {{ background: rgba(255,255,255,0.07); border-radius: 3px; }}
            QScrollBar:vertical {{ border: none; background: transparent; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {accent_color}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            QScrollBar:horizontal {{ border: none; background: transparent; height: 10px; margin: 0; }}
            QScrollBar::handle:horizontal {{ background: #333; min-width: 30px; border-radius: 5px; }}
            QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {accent_color}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
        """)
        self.setFixedWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        icon_size = 14
        clear_filter_icon = QIcon(resource_path("img/filter_off-2.png"))
        has_filter = bool(active_values)

        def _make_action_row(icon, label, callback, enabled=True, tint=True, tint_color=None):
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
            color = (tint_color if tint_color else "#ddd") if enabled else "#555"
            lbl_text = QLabel(label)
            lbl_text.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
            hl.addWidget(lbl_icon)
            hl.addWidget(lbl_text)
            hl.addStretch()
            if enabled:
                row.mousePressEvent = lambda e: callback()
            return row

        layout.addWidget(_make_action_row(up_icon,        "Sort ascending",  lambda: self._sort("ASC")))
        layout.addWidget(_make_action_row(down_icon,      "Sort descending", lambda: self._sort("DESC")))
        layout.addWidget(_make_action_row(clear_filter_icon, "Clear filter",   self._clear_filter, enabled=has_filter, tint=has_filter, tint_color="#ff4444" if has_filter else None))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)

        layout.setSpacing(6)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search…")
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


class SmartSortHeader(QHeaderView):
    section_drag_finished = pyqtSignal()
    filter_clicked = pyqtSignal(int, QRect)  # col, icon rect in global coords
    sort_clicked   = pyqtSignal(int)          # col — for sort-only columns

    SORT_COLS = {1, 2, 8, 9, 10, 11}  # TRACK, TITLE, PLAYS, LENGTH, NO., DATE ADDED — show sort icon, not filter

    FILTER_ICON_SIZE = 14

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setStretchLastSection(False)
        self.up_icon   = QIcon(resource_path("img/filter_up.png"))
        self.down_icon = QIcon(resource_path("img/filter_down.png"))
        self.filter_icon     = QIcon(resource_path("img/filter.png"))
        self.filter_off_icon = QIcon(resource_path("img/filter.png"))
        self._active_filter_cols = set()  # cols that have an active filter
        self._filter_icon_rects = {}  # logical_index -> QRect (viewport coords, set during paint)
        self._hovered_col = -1
        self._pending_click_col = None
        self._pending_click_pos = None
        self.album_mode = False
        self.viewport().setMouseTracking(True)

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
        col = self.logicalIndexAt(event.pos())
        hovered = col if col != 0 else -1
        if hovered != self._hovered_col:
            self._hovered_col = hovered
            self.viewport().update()
        # Cancel pending click if the mouse moved enough to be a drag
        if (self._pending_click_pos is not None
                and abs(event.pos().x() - self._pending_click_pos.x()) > self._CLICK_THRESHOLD):
            self._pending_click_col = None
            self._pending_click_pos = None
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hovered_col != -1:
            self._hovered_col = -1
            self.viewport().update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
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
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            logical = self.logicalIndexAt(pos)
            if logical > 0 and not self._is_resize_zone(pos):
                self._pending_click_col = logical
                self._pending_click_pos = pos
        super().mousePressEvent(event)

    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex
        
        label_text = opt.text
        if not label_text:
            model = self.model()
            if model:
                label_text = model.headerData(logicalIndex, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        
        if label_text is None: label_text = ""
        
        # Clear default indicator so we can draw our own
        opt.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        opt.text = "" 
        
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        
        painter.setFont(self.font())
        
        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(label_text)

        sz = self.FILTER_ICON_SIZE
        icon_spacing = 4
        show_icon = logicalIndex != 0  # col 0 = # — no icon
        content_width = text_width + (icon_spacing + sz if show_icon else 0)

        alignment = opt.textAlignment

        # 🟢 FORCE ALIGNMENTS
        if logicalIndex in (0, 7, 9, 10):
             alignment = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        elif logicalIndex == 1:
             alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        padding_left = 5

        if alignment & Qt.AlignmentFlag.AlignRight:
            start_x = rect.right() - padding_left - content_width
        elif alignment & Qt.AlignmentFlag.AlignCenter:
            start_x = rect.left() + (rect.width() - content_width) // 2
        else:
            start_x = rect.left() + padding_left

        # 🟢 THE FOOLPROOF FIX: Brutally force the TRACK header to the left edge,
        # completely ignoring whatever centering rules Qt is trying to apply to it!
        if logicalIndex == 1:
            start_x = rect.left() + 15

        text_rect = QRect(int(start_x), rect.top(), int(text_width) + 10, rect.height())

        painter.setPen(QColor("#888"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label_text)

        # Highlight whole section on hover (not in album mode)
        is_hovered = (not self.album_mode) and logicalIndex == self._hovered_col
        if is_hovered and show_icon:
            painter.fillRect(rect, QColor(255, 255, 255, 18))

        # Draw icon right after text — only when hovered or active/sorted; never in album mode
        is_sort_indicator_shown = self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex
        is_sorted = logicalIndex in self.SORT_COLS and is_sort_indicator_shown
        is_col_sorted = is_sort_indicator_shown  # any col can be sorted
        is_active = logicalIndex in self._active_filter_cols
        show_icon_now = (not self.album_mode) and show_icon and (is_hovered or is_sorted or is_col_sorted or is_active)

        if show_icon_now:
            fx = int(start_x + text_width + icon_spacing)
            fy = rect.center().y() - sz // 2

            if is_active and logicalIndex not in self.SORT_COLS:
                # Active filter takes priority — show filter_off icon, white tint
                px = self.filter_off_icon.pixmap(sz, sz)
                tint = QColor("#ffffff")
            elif is_col_sorted and logicalIndex not in self.SORT_COLS:
                # Filter column with active sort — show up/down arrow
                icon = self.down_icon if self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else self.up_icon
                tint = QColor("#ffffff") if is_hovered else QColor("#aaaaaa")
                px = icon.pixmap(sz, sz)
            elif logicalIndex in self.SORT_COLS:
                icon = (self.down_icon if self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
                        else self.up_icon) if is_sorted else self.up_icon
                tint = QColor("#ffffff") if is_hovered else QColor("#aaaaaa")
                px = icon.pixmap(sz, sz)
            else:
                # Hovered filter column, no active filter or sort
                px = self.filter_icon.pixmap(sz, sz)
                tint = QColor("#ffffff") if is_hovered else QColor("#555555")

            tinted = QPixmap(px.size())
            tinted.fill(Qt.GlobalColor.transparent)
            p2 = QPainter(tinted)
            p2.drawPixmap(0, 0, px)
            p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p2.fillRect(tinted.rect(), tint)
            p2.end()
            painter.drawPixmap(fx, int(fy), tinted)

        if show_icon and not show_icon_now:
            # Still cache a zeroed rect so click detection stays consistent
            self._filter_icon_rects[logicalIndex] = QRect()

        painter.restore()

class NoFocusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # Remove the focus state so the dotted line/blue border never appears
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)

class SkeletonDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setPen(QColor(255, 255, 255, 10))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        
        rect = option.rect
        col = index.column()
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3a3a3a")) # Brighter gray pill color
        
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


# --- DELEGATE 1: SINGLE LINK (For Albums) ---

class LinkDelegate(QStyledItemDelegate):
    clicked = pyqtSignal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hovered_index = None
        self.master_color = QColor("#cccccc") 

    def set_master_color(self, color):
        self.master_color = QColor(color)

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
        is_selected = (opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = (opts.state & QStyle.StateFlag.State_MouseOver)

        painter.save()

        if is_selected or is_row_hover:
            painter.setPen(self.master_color)
            if is_hovering:
                f = painter.font(); f.setUnderline(True); painter.setFont(f)
        elif is_hovering:
            painter.setPen(QColor("#ffffff"))
            f = painter.font(); f.setUnderline(True); painter.setFont(f)
        else:
            painter.setPen(QColor("#cccccc"))

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
            painter.drawText(draw_rect.left(), int(start_y + i * line_spacing), line_str)
            
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

        is_selected = bool(opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = bool(opts.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        if is_selected or is_row_hover:
            painter.setPen(self.master_color)
        else:
            painter.setPen(QColor("#cccccc"))

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
            painter.drawText(draw_rect.left(), int(start_y + i * line_spacing), line_str)
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
        """Update the master color for this delegate"""
        self.master_color = QColor(color)

    def paint(self, painter, option, index):
        if not index.isValid(): return

        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus
        
        # Draw background
        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)

        painter.save()
        rect = opts.rect
        text = index.data(Qt.ItemDataRole.DisplayRole)
        
        is_selected = (opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = (opts.state & QStyle.StateFlag.State_MouseOver)
        
        if is_selected or is_row_hover:
            base_color = self.master_color
        else:
            base_color = QColor("#cccccc")

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
            
            
            painter.setPen(base_color)

            f = painter.font()
            f.setUnderline(is_hovering)
            painter.setFont(f)

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

        is_selected = (opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = (opts.state & QStyle.StateFlag.State_MouseOver)
        base_color = self.master_color if (is_selected or is_row_hover) else QColor("#cccccc")

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
                painter.setPen(QColor("#777") if is_sep else base_color)
                f = painter.font()
                f.setUnderline(is_hovering)
                painter.setFont(f)
                painter.drawText(int(x), y, part)
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

        is_selected = (opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = (opts.state & QStyle.StateFlag.State_MouseOver)
        
        if is_selected or is_row_hover: 
            main_text_color = self.master_color.name()
        else: 
            main_text_color = "#cccccc"

        painter.save()

        # --- 1. FONTS & TEXT SETUP ---
        title_font = QFont(opts.font)
        title_font.setPointSize(10)
        title_font.setBold(True)
        
        artist_font = QFont(opts.font)
        artist_font.setPointSize(9)

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
                
                from cover_cache import CoverCache
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
            painter.setPen(QColor("#555555"))
            f_music = painter.font()
            f_music.setPixelSize(30)
            painter.setFont(f_music)
            painter.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter, "♪")

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
        
        if is_selected or is_row_hover:
            base_artist_pen = QColor(main_text_color)
        else:
            base_artist_pen = QColor("#aaaaaa")

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

class TracksBrowser(QWidget):
    play_track = pyqtSignal(dict)
    play_multiple_tracks = pyqtSignal(list)
    shuffle_tracks = pyqtSignal(list)
    _scan_done = pyqtSignal()
    queue_track = pyqtSignal(dict)
    play_next = pyqtSignal(dict)
    switch_to_artist_tab = pyqtSignal(str)
    switch_to_album_tab = pyqtSignal(dict)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.last_reload_time = time.time()
        self._settings = QSettings("Sonar", "Sonar")
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
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- HEADER ---
        header_container = QWidget()
        header_container.setFixedHeight(50)
        header_container.setStyleSheet("QWidget { background-color: #111; border-top-left-radius: 5px; border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        
        header_layout = QHBoxLayout(header_container)
        # 🟢 FIX: Set right margin to 2 (or 0) to push icon to the very edge
        header_layout.setContentsMargins(15, 0, 10, 0) 
        header_layout.setSpacing(15)
        
        self.status_label = QLabel("Tracks")
        self.status_label.setStyleSheet("color: #888; font-weight: bold; background: transparent; border: none;")
        
        # (Sync button and progress bar completely removed from here!)

        # --- SMART SEARCH CONTAINER ---
        self.search_container = SmartSearchContainer(placeholder="Search tracks...")
        self.search_container.text_changed.connect(self.on_search_text_changed)

        # 🟢 Restore keyboard focus to the track list when the search bar is dismissed
        if hasattr(self.search_container, 'search_input') and hasattr(self.search_container.search_input, 'focus_lost'):
            self.search_container.search_input.focus_lost.connect(
                lambda: QTimer.singleShot(50, lambda: self.tree.setFocus(Qt.FocusReason.OtherFocusReason))
            )

        # 🟢 NEW: Add handlers for Enter and Down Arrow inside the search box!
            self.search_container.search_input.returnPressed.connect(self.focus_first_tree_item)
            self.search_container.search_input.installEventFilter(self)
        
        # Tracks doesn't use the burger menu for sorting yet, but we grab the reference for tinting!
        self.burger_btn = self.search_container.get_burger_btn()
        self.burger_btn.clicked.connect(self.show_column_menu) 
        
        # --- CLEAR FILTERS BUTTON ---
        self.clear_filters_btn = QPushButton()
        self.clear_filters_btn.setToolTip("Clear all column filters")
        self.clear_filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_filters_btn.setFixedSize(32, 32)
        self.clear_filters_btn.setFlat(True)
        self.clear_filters_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.clear_filters_btn.clicked.connect(self._clear_all_filters)
        self.clear_filters_btn.setIconSize(QSize(18, 18))
        self.clear_filters_btn.hide()

        # --- PLAY FILTERED BUTTON ---
        self.play_filtered_btn = QPushButton()
        self.play_filtered_btn.setToolTip("Play filtered tracks (hold for shuffle)")
        self.play_filtered_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_filtered_btn.setFixedSize(32, 32)
        self.play_filtered_btn.setFlat(True)
        self.play_filtered_btn.setIcon(QIcon(resource_path("img/play-button.png")))
        self.play_filtered_btn.setIconSize(QSize(18, 18))
        self.play_filtered_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.play_filtered_btn.hide()
        # Long-press detection
        self._play_filtered_timer = QTimer(self)
        self._play_filtered_timer.setSingleShot(True)
        self._play_filtered_timer.setInterval(600)
        self._play_filtered_timer.timeout.connect(self._shuffle_filtered_tracks)
        self._play_filtered_held = False
        self.play_filtered_btn.pressed.connect(lambda: (self._play_filtered_timer.start(), setattr(self, '_play_filtered_held', False)))
        self.play_filtered_btn.released.connect(self._on_play_filtered_released)

        # --- REFRESH BUTTON ---
        self.refresh_btn = QPushButton()
        self.refresh_btn.setToolTip("Refresh library from server")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setFixedSize(32, 32)
        self.refresh_btn.setFlat(True)
        self.refresh_btn.setIcon(QIcon(resource_path("img/refresh.png")))
        self.refresh_btn.setIconSize(QSize(18, 18))
        self.refresh_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.refresh_btn.clicked.connect(self._refresh_library)

        # 🟢 CLEAN HEADER ASSEMBLY
        filter_btns = QWidget()
        filter_btns.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        filter_btns_layout = QHBoxLayout(filter_btns)
        filter_btns_layout.setContentsMargins(0, 0, 0, 0)
        filter_btns_layout.setSpacing(2)
        filter_btns_layout.addWidget(self.play_filtered_btn)
        filter_btns_layout.addWidget(self.clear_filters_btn)

        right_group = QWidget()
        right_group.setStyleSheet("background: transparent; border: none;")
        right_group_layout = QHBoxLayout(right_group)
        right_group_layout.setContentsMargins(0, 0, 0, 0)
        right_group_layout.setSpacing(0)
        right_group_layout.addWidget(self.search_container)
        right_group_layout.addWidget(self.refresh_btn)

        header_layout.addWidget(self.status_label)
        header_layout.addWidget(filter_btns)
        header_layout.addStretch()
        header_layout.addWidget(right_group, 0, Qt.AlignmentFlag.AlignRight)

        self.main_layout.addWidget(header_container)

        
        # --- TREE WIDGET ---
        
        self.tree = QTreeWidget()
        from PyQt6.QtWidgets import QAbstractItemView
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.omni_scroller = MiddleClickScroller(self.tree)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setItemDelegate(NoFocusDelegate(self.tree))
        self.setFocusProxy(self.tree)

        self.tree.setHeader(SmartSortHeader(self.tree))
     
        
        
        self.tree.setHeaderLabels(["#", "TRACK", "TITLE", "ARTIST", "ALBUM", "YEAR", "GENRE", "♥", "PLAYS", "LENGTH", "NO.", "DATE ADDED"])
        self.tree.headerItem().setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(9, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(10, Qt.AlignmentFlag.AlignCenter)

        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self.tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tree.setMouseTracking(True)

        self.tree.header().setSectionsMovable(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionsClickable(False)
        self.tree.header().setSortIndicatorShown(True)
        self.tree.header().setSortIndicator(self.sort_col, self.sort_order)

        # Col 0 (#): auto-size to fit content
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        # Cols 1-11: all freely interactive
        for i in range(1, 12):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        self.col_min_widths = {1: 100, 2: 80, 3: 80, 4: 80, 5: 50, 6: 60, 7: 40, 8: 50, 9: 60, 10: 40, 11: 80}
        # These columns never grow beyond their default on window resize (user can still drag them)
        self.col_max_widths = {5: 70, 7: 60, 8: 70, 9: 75, 10: 60, 11: 110}

        self.tree.setColumnWidth(1, 350) # TRACK (Combined)
        self.tree.setColumnWidth(2, 200) # TITLE
        self.tree.setColumnWidth(3, 200) # ARTIST
        self.tree.setColumnWidth(4, 240) # ALBUM
        self.tree.setColumnWidth(5, 70)  # YEAR
        self.tree.setColumnWidth(6, 120) # GENRE
        self.tree.setColumnWidth(7, 60)  # ♥
        self.tree.setColumnWidth(8, 70)  # PLAYS
        self.tree.setColumnWidth(9, 75)  # LENGTH
        self.tree.setColumnWidth(10, 55) # NO.
        self.tree.setColumnWidth(11, 95) # DATE ADDED

        self._col_resize_guard = False
        self.tree.header().sectionResized.connect(self._on_section_resized)
        self.tree.header().section_drag_finished.connect(self._on_drag_finished)

        # Column filters: col -> set of allowed string values (empty set = no filter)
        self._col_filters = {}
        self._col_filter_values = {}  # col -> list of all known values for the popup
        self._col_id_map = {}         # col -> {display_value: server_id}
        self._filter_values_worker = None
        self.tree.header().filter_clicked.connect(self._on_filter_clicked)
        self.tree.header().sort_clicked.connect(self._on_sort_col_clicked)
        self._active_filter_popup = None

        # 🟢 Specific Delegates
        self.combined_delegate = CombinedTrackDelegate(self.tree)
        self.combined_delegate.artist_clicked.connect(self.switch_to_artist_tab.emit)
        self.tree.setItemDelegateForColumn(1, self.combined_delegate)

        self.artist_delegate = MultiLinkArtistDelegate(self.tree)
        self.artist_delegate.artist_clicked.connect(self.switch_to_artist_tab.emit)
        self.tree.setItemDelegateForColumn(3, self.artist_delegate)

        self.album_delegate = LinkDelegate(self.tree)
        self.album_delegate.clicked.connect(self.on_album_link_clicked)
        self.tree.setItemDelegateForColumn(4, self.album_delegate)
        
        self.genre_delegate = MultiGenreDelegate(self.tree)
        self.genre_delegate.genre_filter_requested.connect(lambda g: self._apply_col_filter(6, {g}))
        self.tree.setItemDelegateForColumn(6, self.genre_delegate)

        self.date_added_delegate = PlainWrapDelegate(self.tree)
        self.tree.setItemDelegateForColumn(11, self.date_added_delegate)

        self.year_delegate = LinkDelegate(self.tree)
        self.year_delegate.clicked.connect(lambda idx: self._apply_col_filter(5, {idx.data()}))
        self.tree.setItemDelegateForColumn(5, self.year_delegate)
        
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self) # 🟢 Steal keystrokes from the tree!
        self.main_layout.addWidget(self.tree)

        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.tree.itemClicked.connect(self.on_item_clicked) 
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        
        # --- SETUP FOOTER ---
        self.footer = PaginationFooter()
        self.footer.page_changed.connect(self.change_page)
        self.main_layout.addWidget(self.footer)
        
        self.is_loading_db = False
        self.load_column_state()
        self.load_from_db(reset=True, invalidate_filter_cache=True)

    
    def focus_first_tree_item(self):
        """Forces an instant search and jumps keyboard focus to the first item."""
        if self.search_timer.isActive():
            self.search_timer.stop()
            self.execute_search()
            
        def apply_focus():
            if self.tree.topLevelItemCount() > 0:
                self.tree.setFocus(Qt.FocusReason.ShortcutFocusReason)
                from PyQt6.QtCore import QItemSelectionModel
                self.tree.setCurrentItem(self.tree.topLevelItem(0), 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
                
        # Wait 50ms before grabbing focus so the UI can update!
        QTimer.singleShot(50, apply_focus)
    
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
                self.sort_col = state.get('col', 4) # Default to ALBUM (Column 4)
                self.sort_order = Qt.SortOrder.AscendingOrder if state.get('order', 0) == 0 else Qt.SortOrder.DescendingOrder
            else:
                self.sort_col = 4
                self.sort_order = Qt.SortOrder.AscendingOrder
        except:
            self.sort_col = 4
            self.sort_order = Qt.SortOrder.AscendingOrder
    
   


    # Optional: Force focus loss if clicking the background area of the widget
    def mousePressEvent(self, event):
        self.setFocus() # Steal focus from search input
        super().mousePressEvent(event)
    
        
    def on_cover_loaded(self, cid):
        # Instantly redraw the screen so the empty "♪" turns into the downloaded cover
        self.tree.viewport().update()
    
    
    def get_filtered_count(self, query_text):
        # Count is determined by LiveTrackWorker results; return cached total
        return getattr(self, 'total_items', 0)

    def check_for_updates(self):
        pass  # Updates are driven by SmartBackgroundSyncer signals, not local DB polling.

    def _refresh_library(self):
        """Trigger a server scan then re-fetch when done."""
        if not self.client:
            return
        self.refresh_btn.setEnabled(False)
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
        self._col_filter_values = {}  # invalidate popup cache; keep _col_id_map so active filters still resolve
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setToolTip("Refresh library from server")
        self.load_from_db(reset=True)
    
    # --- BURGER MENU: COLUMN VISIBILITY ---
    
    def show_column_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #222; color: #ddd; border: 1px solid #444; } QMenu::item { padding: 6px 25px; } QMenu::item:selected { background-color: #333; }")
        
        headers = ["#", "TRACK", "TITLE", "ARTIST", "ALBUM", "YEAR", "GENRE", "♥", "PLAYS", "LENGTH", "NO.", "DATE ADDED"]
        
        for i, name in enumerate(headers):
            action = QAction(name, menu)
            action.setCheckable(True)
            action.setChecked(not self.tree.isColumnHidden(i))
            action.triggered.connect(lambda checked, col=i: self.toggle_column(col, checked))
            menu.addAction(action)
            
        menu.exec(self.burger_btn.mapToGlobal(QPoint(0, self.burger_btn.height())))

    # --- ALBUM DETAIL MODE LOGIC ---

    def load_album_view(self, album_id):
        self.album_mode_id = album_id
        if hasattr(self, 'footer'): self.footer.hide()
        if hasattr(self, 'search_container'): self.search_container.hide()
        if hasattr(self, 'status_label'): self.status_label.hide()
        if hasattr(self, 'search_container'):
            self.search_container.show()
            self.search_container.hide_burger()
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.hide()

        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionsClickable(False)
        self.tree.header().setSortIndicatorShown(False)
        
        # Hide unnecessary columns
        self.tree.setColumnHidden(4, True) # Album
        self.tree.setColumnHidden(5, True) # Year
        self.tree.setColumnHidden(6, True) # Genre
        
        col_widths = {
            0: 70,   
            1: 350,  # TRACK
            3: 350,  # ARTIST
            7: 60,   # Heart
            8: 60,   # Plays
            9: 60    # Length
        }
        
        total_w = 0
        for col, w in col_widths.items():
            self.tree.setColumnWidth(col, w)
            total_w += w
            
        box_width = total_w + 5 
        self.tree.setMaximumWidth(box_width)
        self.setMaximumWidth(box_width) 
        
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        self.load_album_tracks(album_id)

    
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
        if getattr(self, 'album_mode_id', None): return
        if reset:
            self.current_page = 1
            self.total_items = 0
            self.total_pages = 1
        if invalidate_filter_cache:
            # Only clear filter values when the search query changes, not on filter apply
            self._col_filter_values = {}
            self._col_id_map = {}
        self._start_worker(is_album=False, album_id=None)

    def load_album_tracks(self, album_id):
        self._col_filter_values = {}
        self._col_id_map = {}
        self._start_filter_values_worker(is_album=True, album_id=album_id)
        self._start_worker(is_album=True, album_id=album_id)

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
        
        self.skeleton_timer.start(75) 
        self.live_worker.start()

    def on_worker_finished(self, tracks, total_items, total_pages, target_page):
        # 🟢 SAFETY CHECK
        if self.sender() and getattr(self.sender(), 'is_cancelled', False):
            return

        self.skeleton_timer.stop()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        
        for i in range(12): self.tree.setItemDelegateForColumn(i, None)
        self.tree.setItemDelegateForColumn(1, self.combined_delegate)
        self.tree.setItemDelegateForColumn(3, self.artist_delegate)
        self.tree.setItemDelegateForColumn(4, self.album_delegate)
        self.tree.setItemDelegateForColumn(5, self.year_delegate)
        self.tree.setItemDelegateForColumn(6, self.genre_delegate)
        self.tree.setItemDelegateForColumn(11, self.date_added_delegate)
        
        self.total_items = total_items
        self.total_pages = total_pages
        self.current_page = target_page
        
        # 👇 🟢 THE BATCHING FIX: Collect all items into a list!
        items_to_add = []
        total_calc_h = self.tree.header().height() + 10
        row_px = 50 if getattr(self, 'is_album_mode', False) else 75
        
        if getattr(self, 'album_mode_id', None):
            if not tracks:
                self.tree.setUpdatesEnabled(True)
                return
            all_discs = set(t.get('discNumber', 1) or t.get('disc_number', 1) for t in tracks)
            show_headers = len(all_discs) > 1
            current_disc = None

            for i, t in enumerate(tracks):
                disc_num = t.get('discNumber', 1) or t.get('disc_number', 1)
                if show_headers and disc_num != current_disc:
                    current_disc = disc_num
                    header = QTreeWidgetItem([f"Disc {current_disc}"] + [""] * 9)
                    header.setFirstColumnSpanned(True)
                    f = header.font(0); f.setBold(True); f.setPointSize(11); header.setFont(0, f)
                    header.setForeground(0, QColor("#ffffff")); header.setBackground(0, QColor(0, 0, 0, 80))
                    items_to_add.append(header)
                    total_calc_h += 30
                track_num = t.get('trackNumber', i + 1)
                items_to_add.append(self.create_track_item(t, track_num))
                total_calc_h += row_px

            # Show first batch immediately so the view isn't blank
            BATCH = 50
            self.tree.addTopLevelItems(items_to_add[:BATCH])
            self.tree.setUpdatesEnabled(True)

            # Apply final height now (based on full list) so scroll area sizes correctly
            MAX_TREE_H = 800
            capped_h = min(total_calc_h, MAX_TREE_H)
            self.tree.setMinimumHeight(capped_h)
            self.tree.setMaximumHeight(total_calc_h)
            self.setMinimumHeight(capped_h + 50)
            self.setMaximumHeight(total_calc_h + 50)

            # Schedule remaining batches — each fires after Qt returns to event loop
            remaining = items_to_add[BATCH:]
            captured_worker = self.live_worker

            def _add_batch(offset):
                if getattr(captured_worker, 'is_cancelled', True):
                    return
                chunk = remaining[offset:offset + BATCH]
                if chunk:
                    self.tree.addTopLevelItems(chunk)
                if offset + BATCH < len(remaining):
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: _add_batch(offset + BATCH))

            if remaining:
                from PyQt6.QtCore import QTimer as _QT
                _QT.singleShot(0, lambda: _add_batch(0))

        else:
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"{self.total_items:,} tracks")

            if hasattr(self, 'footer'):
                self.footer.render_pagination(self.current_page, self.total_pages)

            offset = (self.current_page - 1) * self.page_size
            for i, t in enumerate(tracks):
                items_to_add.append(self.create_track_item(t, offset + i + 1))

            self.tree.addTopLevelItems(items_to_add)

        if self.tree.topLevelItemCount() > 0:
            from PyQt6.QtCore import QItemSelectionModel
            focus_idx = 0
            
            if getattr(self, 'pending_focus_direction', 'top') == 'bottom':
                focus_idx = self.tree.topLevelItemCount() - 1
            self.pending_focus_direction = 'top' 
            
            self.tree.setCurrentItem(self.tree.topLevelItem(focus_idx), 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
            
            if focus_idx > 0:
                self.tree.verticalScrollBar().setValue(self.tree.verticalScrollBar().maximum())
            else:
                self.tree.verticalScrollBar().setValue(0)

        if hasattr(self, 'current_playing_id'):
            self.update_playing_status(getattr(self, 'current_playing_id'), getattr(self, 'is_playing', False), getattr(self, 'playing_color', "#1DB954"))

        if not getattr(self, 'album_mode_id', None):
            self.tree.setUpdatesEnabled(True)
        self.start_cover_loader(tracks)

        # Kick off filter values worker in background after first data load,
        # so values+IDs are ready by the time user opens a filter popup.
        if not getattr(self, 'album_mode_id', None):
            if not self._col_filter_values:
                is_album = False
                self._start_filter_values_worker(is_album=is_album, album_id=None)

    def show_skeleton_ui(self):
        """Draws the exact visual from your screenshot: Numbers on the left, pills on the right!"""
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        
        if not hasattr(self, 'skeleton_delegate'):
            self.skeleton_delegate = SkeletonDelegate(self.tree)
            
        # 🟢 Skip column 0 so it uses the standard text renderer for the numbers!
        for i in range(1, 12):
            self.tree.setItemDelegateForColumn(i, self.skeleton_delegate)

        offset = (self.current_page - 1) * self.page_size
        for i in range(15):
            item = QTreeWidgetItem([""] * 12)
            if not getattr(self, 'album_mode_id', None):
                item.setText(0, str(offset + i + 1))
                item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
                item.setForeground(0, QColor("#666"))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(item)
            
        self.tree.setUpdatesEnabled(True)
    
    def start_cover_loader(self, tracks):
        if not hasattr(self, 'client') or not self.client: return
        
        if not hasattr(self, 'tb_cover_worker'):
            self.tb_cover_worker = TBCoverWorker(self.client)
            self.tb_cover_worker.cover_ready.connect(self.on_tb_cover_loaded)
            self.tb_cover_worker.start()
        elif not self.tb_cover_worker.isRunning():
            self.tb_cover_worker.start() 
            
        self.tb_cover_worker.queue.clear()
        
        # 👇 🟢 THE MATH FIX: Use a temporary set to make lookups instant!
        queued_set = set()
            
        for t in tracks:
            raw_cid = t.get('cover_id') or t.get('coverArt') or t.get('albumId')
            if raw_cid:
                cid_str = str(raw_cid) 
                if cid_str not in queued_set:
                    queued_set.add(cid_str)
                    self.tb_cover_worker.queue.append(cid_str)

    def on_tb_cover_loaded(self, cover_id, image_data):
        # 🟢 Safely build the QPixmap on the main thread where it belongs!
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        
        if hasattr(self, 'combined_delegate'):
            # 🟢 THE FIX: Use .set() for LRUCache!
            self.combined_delegate.cover_cache.set(str(cover_id), pixmap)
            self.tree.viewport().update()
   
    def create_track_item(self, t, index_label):
        item = QTreeWidgetItem()
        
        # 🟢 BOOLEAN FIX
        raw_state = t.get('starred')
        if isinstance(raw_state, str): is_fav = raw_state.lower() in ('true', '1')
        else: is_fav = bool(raw_state)
        
        item.setText(0, str(index_label))
        item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
        item.setForeground(0, QColor("#888"))

        item.setData(0, Qt.ItemDataRole.UserRole + 1, str(index_label))
        
        item.setText(1, str(t.get('title') or 'Unknown')) 
        item.setText(2, str(t.get('title') or 'Unknown')) 
        item.setText(3, str(t.get('artist') or 'Unknown'))
        item.setText(4, str(t.get('album') or 'Unknown'))
        item.setText(5, str(t.get('year') or ''))
        
        genre_raw = t.get('genre', '')
        if genre_raw:
            if ' • ' not in genre_raw:
                genre_formatted = genre_raw
                for delimiter in ['; ', ';', ' | ', '|', ' /// ', ' / ', '/', ', ']:
                    genre_formatted = genre_formatted.replace(delimiter, ' • ')
                item.setText(6, genre_formatted)
            else: item.setText(6, genre_raw)
        else: item.setText(6, '')
        
        item.setText(7, "♥" if is_fav else "♡")
        # 🟢 MASTER COLOR FIX
        item.setForeground(7, QColor(self.current_accent) if is_fav else QColor("#555"))
        item.setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)
        
        # 🟢 PLAY COUNT FIX
        raw_plays = t.get('playCount') or t.get('play_count') or 0
        try: plays = int(raw_plays)
        except: plays = 0
        item.setText(8, str(plays) if plays > 0 else "")
        
        # 🟢 LENGTH COLUMN FIX: Safely handles both formatted strings ("3:45") and raw seconds
        raw_dur = t.get('duration', 0)
        time_str = ""
        try:
            # If the server already formatted it as "3:45", just use it directly!
            if isinstance(raw_dur, str) and ":" in raw_dur:
                time_str = raw_dur
            else:
                # Otherwise, treat it as raw seconds and do the math
                seconds = int(float(raw_dur)) if raw_dur else 0
                if seconds > 0:
                    m, s = divmod(seconds, 60)
                    time_str = f"{m}:{s:02d}"
        except Exception: 
            pass
            
        item.setText(9, time_str)
        item.setTextAlignment(9, Qt.AlignmentFlag.AlignCenter)

        track_num = t.get('trackNumber') or t.get('track') or ''
        item.setText(10, str(track_num) if track_num else '')
        item.setTextAlignment(10, Qt.AlignmentFlag.AlignCenter)

        created_raw = t.get('created') or ''
        if created_raw:
            try:
                from datetime import datetime
                import platform
                dt = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
                fmt = '%#d %b %Y' if platform.system() == 'Windows' else '%-d %b %Y'
                date_str = dt.strftime(fmt)
            except Exception:
                try:
                    date_str = created_raw[:10]  # fallback: YYYY-MM-DD
                except Exception:
                    date_str = ''
        else:
            date_str = ''
        item.setText(11, date_str)
        item.setData(11, Qt.ItemDataRole.UserRole, created_raw)  # store raw ISO for sort

        # Store a reference to the original dict — avoid dict(t) copy overhead.
        # Overwrite 'starred' in-place so toggle logic stays consistent.
        t['starred'] = is_fav
        item.setData(0, Qt.ItemDataRole.UserRole, {'data': t, 'type': 'track'})
        
        return item
    
    def refresh_track_item(self, track_id, fresh):
        """Find the tree item for track_id and update its text columns with fresh metadata."""
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if not d or d.get('type') != 'track':
                continue
            t = d.get('data', {})
            if str(t.get('id', '')) != str(track_id):
                continue
            # Update text columns
            for key, col in (('title', 1), ('title', 2), ('artist', 3), ('album', 4), ('year', 5)):
                item.setText(col, str(fresh.get(key) or t.get(key) or ''))
            # Patch stored dict so future reads are correct too
            t.update({k: fresh.get(k, t.get(k)) for k in ('title', 'artist', 'album', 'year')})
            break
        self.tree.viewport().update()

    def update_playing_status(self, playing_id, is_playing, color_hex):
        """Highlights the playing track with the animated GIF, matching the Now Playing tab."""
        self.current_playing_id = playing_id
        self.is_playing = is_playing
        self.playing_color = color_hex
        
        if not hasattr(self, '_pi_movie'):
            from PyQt6.QtGui import QMovie
            from PyQt6.QtCore import QSize
            self._pi_movie = QMovie(resource_path("img/playing.gif"))
            self._pi_movie.setScaledSize(QSize(30, 30))
            
        from PyQt6.QtWidgets import QLabel, QGraphicsColorizeEffect
        from PyQt6.QtGui import QColor, QFont
        from PyQt6.QtCore import Qt
        
        rgb = QColor(color_hex) if color_hex else QColor("#1DB954")
        highlight_bg = QColor(rgb.red(), rgb.green(), rgb.blue(), 40)
        default_color = QColor("#ddd")
        transparent = QColor(0, 0, 0, 0)
        
        normal_font = QFont("sans-serif", 10)
        normal_font.setBold(False)
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.isFirstColumnSpanned(): continue # Skip Disc headers
                
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data.get('type') != 'track': continue
                
            track = data['data']
            track_id = track.get('id')
            orig_num = item.data(0, Qt.ItemDataRole.UserRole + 1) or ""
            
            if track_id and playing_id and str(track_id) == str(playing_id):
                if is_playing:
                    item.setText(0, "")
                    pi_label = QLabel()
                    pi_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    pi_label.setStyleSheet("background: transparent;")
                    pi_label.setMovie(self._pi_movie)
                    pi_effect = QGraphicsColorizeEffect(pi_label)
                    pi_effect.setColor(rgb)
                    pi_label.setGraphicsEffect(pi_effect)
                    self.tree.setItemWidget(item, 0, pi_label)
                    self._pi_movie.start()
                else:
                    if self.tree.itemWidget(item, 0):
                        self.tree.removeItemWidget(item, 0)
                    item.setText(0, orig_num)
                    self._pi_movie.stop()
                
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, highlight_bg)
                    if col != 7: # Skip Heart column
                        item.setForeground(col, rgb)
            else:
                if self.tree.itemWidget(item, 0):
                    self.tree.removeItemWidget(item, 0)
                item.setText(0, orig_num)
                item.setFont(0, normal_font)
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, transparent)
                    if col != 7: # Skip Heart column
                        item.setForeground(col, default_color)
                        if col > 0: item.setFont(col, normal_font)
    
    
    def toggle_column(self, col, is_checked):
        self._col_resize_guard = True
        self.tree.setColumnHidden(col, not is_checked)
        if is_checked:
            self.tree.header().resizeSection(col, self.col_min_widths.get(col, 60))
        self._col_resize_guard = False
        self._fit_columns_to_viewport()
        self.save_column_state()

    def save_column_state(self):
        hdr = self.tree.header()
        state = {}
        for i in range(12):
            state[str(i)] = {
                'hidden': self.tree.isColumnHidden(i),
                'width': self.tree.columnWidth(i),
                'visual': hdr.visualIndex(i),
            }
        try:
            self._settings.setValue('tracks_columns_hidden', json.dumps(state))
        except: pass

    def load_column_state(self):
        try:
            state_str = self._settings.value('tracks_columns_hidden')
            if state_str:
                state = json.loads(state_str)
                self._col_resize_guard = True
                hdr = self.tree.header()
                # First pass: hidden + width
                for col_str, val in state.items():
                    col_idx = int(col_str)
                    if col_idx >= 12: continue
                    if isinstance(val, dict):
                        self.tree.setColumnHidden(col_idx, val.get('hidden', False))
                        w = val.get('width', 0)
                        if w > 0:
                            hdr.resizeSection(col_idx, w)
                    else:
                        self.tree.setColumnHidden(col_idx, val)
                # Second pass: visual order (col 0 is fixed, skip it)
                order = [(int(col_str), val['visual'])
                         for col_str, val in state.items()
                         if isinstance(val, dict) and 'visual' in val and int(col_str) > 0]
                order.sort(key=lambda x: x[1])  # sort by saved visual index
                for logical, target_visual in order:
                    if logical >= 12: continue
                    current_visual = hdr.visualIndex(logical)
                    if current_visual != target_visual:
                        hdr.moveSection(current_visual, target_visual)
                # Sanity check: corrupt state if <3 columns visible OR any single
                # column is wider than 1200px (pushed everything off-screen)
                visible = sum(1 for i in range(1, 12) if not self.tree.isColumnHidden(i))
                any_insane = any(self.tree.columnWidth(i) > 1200 for i in range(1, 12))
                if visible < 3 or any_insane:
                    self._settings.remove('tracks_columns_hidden')
                    for i in range(1, 12):
                        self.tree.setColumnHidden(i, False)
                        self.tree.header().resizeSection(i, self.col_min_widths.get(i, 80))
                    self.tree.setColumnHidden(2, True)
                    self.tree.setColumnHidden(3, True)
                    self.tree.setColumnHidden(11, True)
                self._col_resize_guard = False
            else:
                self._col_resize_guard = True
                self.tree.setColumnHidden(2, True)
                self.tree.setColumnHidden(3, True)
                self.tree.setColumnHidden(11, True)
                self._col_resize_guard = False
        except:
            self._col_resize_guard = True
            self.tree.setColumnHidden(2, True)
            self.tree.setColumnHidden(3, True)
            self.tree.setColumnHidden(11, True)
            self._col_resize_guard = False
    
    
    def _last_visible_col(self):
        """Return the logical index of the last visible interactive column (the stretch column)."""
        for i in range(11, 0, -1):
            if not self.tree.isColumnHidden(i):
                return i
        return -1

    def _on_section_resized(self, logical_index, old_size, new_size):
        """Enforce minimum widths while dragging, then persist the new size."""
        if getattr(self, '_col_resize_guard', False): return
        if getattr(self, 'is_album_mode', False): return
        if logical_index not in self.col_min_widths: return
        if self.tree.isColumnHidden(logical_index): return
        min_w = self.col_min_widths[logical_index]
        if new_size < min_w:
            self._col_resize_guard = True
            self.tree.header().resizeSection(logical_index, min_w)
            self._col_resize_guard = False
        self.save_column_state()

    def _clamp_columns(self):
        """After drag ends: shrink any column that pushed others below their minimum."""
        if getattr(self, 'is_album_mode', False): return
        viewport_w = self.tree.viewport().width()
        self._col_resize_guard = True
        for col in range(1, 12):
            if self.tree.isColumnHidden(col): continue
            used_by_others = self.tree.columnWidth(0)
            for other in range(1, 12):
                if other == col or self.tree.isColumnHidden(other): continue
                used_by_others += self.col_min_widths.get(other, 60)
            max_w = viewport_w - used_by_others
            cur_w = self.tree.columnWidth(col)
            if cur_w > max_w:
                self.tree.header().resizeSection(col, max(self.col_min_widths.get(col, 60), max_w))
        self._col_resize_guard = False

    def _on_drag_finished(self):
        self._clamp_columns()
        self._fit_columns_to_viewport()
        self.save_column_state()

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
        """Derive unique filter values for col from the currently loaded tree items."""
        vals = set()
        # Multi-value separator pattern (matches artist/genre delegate splitting)
        sep = re.compile(r' /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, ')
        multi_val_cols = {3, 6}  # artist, genre — may have multiple values per cell
        for i in range(self.tree.topLevelItemCount()):
            text = self.tree.topLevelItem(i).text(col)
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
        # If other filters are active, derive values from the loaded tree (cascading)
        other_filters_active = any(c != col for c in self._col_filters)
        if other_filters_active:
            values = self._values_from_tree(col)
        else:
            values = self._col_filter_values.get(col, [])
        active = self._col_filters.get(col, set())
        hdr = self.tree.header()
        popup = ColumnFilterPopup(col, values, active, hdr.up_icon, hdr.down_icon, accent_color=getattr(self, 'current_accent', '#cccccc'), parent=self)
        popup.filters_applied.connect(self._apply_col_filter)
        popup.sort_requested.connect(self._on_sort_from_popup)
        popup.move(global_rect.topLeft())
        popup.show()
        self._active_filter_popup = popup
        popup.destroyed.connect(lambda: setattr(self, '_active_filter_popup', None))

    def _apply_col_filter(self, col, values):
        if values:
            self._col_filters[col] = values
        else:
            self._col_filters.pop(col, None)
        self.tree.header().set_active_filters(self._col_filters.keys())
        self._active_filter_popup = None
        self._update_clear_filters_btn()
        self.load_from_db(reset=True)

    def _clear_all_filters(self):
        self._col_filters = {}
        self.tree.header().set_active_filters([])
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
        if not hasattr(self, 'clear_filters_btn'):
            return
        if self._col_filters:
            self.clear_filters_btn.show()
            self.play_filtered_btn.show()
        else:
            self.clear_filters_btn.hide()
            self.play_filtered_btn.hide()

    def _get_filtered_tracks(self):
        tracks = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') == 'track':
                tracks.append(data['data'])
        return tracks

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

    def _on_play_filtered_released(self):
        if self._play_filtered_timer.isActive():
            self._play_filtered_timer.stop()
            self._fetch_all_filtered_tracks(lambda tracks: self.play_multiple_tracks.emit(tracks) if tracks else None)

    def _shuffle_filtered_tracks(self):
        self._play_filtered_held = True
        import random
        def _do_shuffle(tracks):
            if tracks:
                shuffled = tracks[:]
                random.shuffle(shuffled)
                self.play_multiple_tracks.emit(shuffled)
        self._fetch_all_filtered_tracks(_do_shuffle)

    def _on_sort_col_clicked(self, col):
        """Toggle sort asc/desc when TRACK or TITLE column header is clicked."""
        if self.sort_col == col:
            self.sort_order = (Qt.SortOrder.DescendingOrder
                               if self.sort_order == Qt.SortOrder.AscendingOrder
                               else Qt.SortOrder.AscendingOrder)
        else:
            self.sort_col = col
            # PLAYS, LENGTH and DATE ADDED default to descending on first click
            DESC_FIRST_COLS = {8, 9, 11}
            self.sort_order = (Qt.SortOrder.DescendingOrder if col in DESC_FIRST_COLS
                               else Qt.SortOrder.AscendingOrder)
        self.tree.header().setSortIndicator(self.sort_col, self.sort_order)
        self.save_sort_state()
        self.load_from_db(reset=True)

    def _on_sort_from_popup(self, col, order):
        self._active_filter_popup = None
        qt_order = Qt.SortOrder.AscendingOrder if order == "ASC" else Qt.SortOrder.DescendingOrder
        self.sort_col = col
        self.sort_order = qt_order
        self.tree.header().setSortIndicator(col, qt_order)
        self.load_from_db(reset=True)

    def _visible_cols(self):
        return [i for i in range(1, 12) if not self.tree.isColumnHidden(i)]

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_columns_to_viewport)
        self.check_for_updates()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, 'is_album_mode', False):
            QTimer.singleShot(0, self._fit_columns_to_viewport)

    def _fit_columns_to_viewport(self):
        """Scale flex columns proportionally to fill viewport; fixed-size cols stay capped."""
        if getattr(self, 'is_album_mode', False): return
        cols = self._visible_cols()
        if not cols: return
        viewport_w = self.tree.viewport().width()
        if viewport_w <= 0: return

        # Fixed-size cols: cap at their max, don't change unless user dragged them smaller
        fixed_cols = [c for c in cols if c in self.col_max_widths]
        flex_cols  = [c for c in cols if c not in self.col_max_widths]

        self._col_resize_guard = True

        # First pass: apply caps to fixed-size cols
        for col in fixed_cols:
            cur_w = self.tree.columnWidth(col)
            capped = min(cur_w, self.col_max_widths[col])
            if cur_w != capped:
                self.tree.header().resizeSection(col, capped)

        if not flex_cols:
            self._col_resize_guard = False
            return

        # Available space for flex cols = viewport - col0 - all fixed-size cols
        used = self.tree.columnWidth(0)
        for col in fixed_cols:
            used += self.tree.columnWidth(col)
        available = viewport_w - used
        if available <= 0:
            self._col_resize_guard = False
            return

        total_cur = sum(self.tree.columnWidth(c) for c in flex_cols)
        if total_cur <= 0:
            self._col_resize_guard = False
            return

        # Distribute available space proportionally among flex cols
        assigned = 0
        for i, col in enumerate(flex_cols):
            if i == len(flex_cols) - 1:
                new_w = available - assigned
            else:
                new_w = int(self.tree.columnWidth(col) / total_cur * available)
            new_w = max(new_w, self.col_min_widths.get(col, 60))
            self.tree.header().resizeSection(col, new_w)
            assigned += new_w

        self._col_resize_guard = False


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
        else: field = "title"
        
        return f"{field} {order}"

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()
    
    def execute_search(self):
        # Reset all column filters when user starts a local search
        if self._col_filters:
            self._col_filters = {}
            self.tree.header().set_active_filters([])
            self._update_clear_filters_btn()
        if getattr(self, 'album_mode_id', None):
            self.load_album_tracks(self.album_mode_id)
        else:
            self.load_from_db(reset=True, invalidate_filter_cache=True)

    def set_album_mode(self, enabled=True):
        self.is_album_mode = enabled
        self.combined_delegate.is_album_mode = enabled
        self.tree.header().album_mode = enabled
        if enabled:
            # Hide redundant columns
            for col in [2, 3, 4, 5, 6, 8, 10, 11]:
                self.tree.hideColumn(col)

            self.tree.showColumn(0) # #
            self.tree.showColumn(1) # TRACK
            self.tree.showColumn(7) # Heart
            self.tree.showColumn(9) # LENGTH
            
            header = self.tree.header()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(7, 60)
            self.tree.setColumnWidth(9, 70)
            
            # Disable inner scrollbars
            self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            
            if hasattr(self, 'current_accent'):
                self.set_accent_color(self.current_accent)

    def keyPressEvent(self, event):
        key = event.key()
        
        # 🟢 ENTER / RETURN: Play the currently selected track
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            curr_item = self.tree.currentItem()
            if curr_item:
                data = curr_item.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get('type') == 'track':
                    self.play_track.emit(data['data'])
            event.accept()
            return
            
        # 🟢 CTRL + A: Select All tracks in the view
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_A:
            self.tree.selectAll()
            event.accept()
            return
            
        # Let the standard Qt engine handle Up/Down naturally to prevent infinite recursion
        super().keyPressEvent(event)
    
    def adjust_height(self):
        count = self.tree.topLevelItemCount()
        if count == 0: return

        row_px = 50 if getattr(self, 'is_album_mode', False) else 75
        total_h = self.tree.header().height() + 10
        for i in range(count):
            item = self.tree.topLevelItem(i)
            total_h += 30 if item.isFirstColumnSpanned() else row_px

        # Cap at 800px so Qt never allocates a backing store for 25 000+px.
        MAX_TREE_H = 800
        capped_h = min(total_h, MAX_TREE_H)

        self.tree.setMinimumHeight(capped_h)
        self.tree.setMaximumHeight(total_h)
        
        # 🟢 THE FIX: Change 20 to 50! The header_container is 50px tall!
        self.setMinimumHeight(capped_h + 50) 
        self.setMaximumHeight(total_h + 50)
    
    

    def on_album_link_clicked(self, index):
        item = self.tree.itemFromIndex(index)
        if not item: return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get('type') != 'track': return
        track = data['data']
        artist_name = track.get('album_artist') or track.get('artist')
        album_data = {'id': track.get('albumId') or track.get('parent'), 'title': track.get('album'), 'artist': artist_name, 'coverArt': track.get('coverArt')}
        if album_data['id']: self.switch_to_album_tab.emit(album_data)

    def eventFilter(self, obj, event):
        # 🟢 NEW: SEARCH BAR FOCUS JUMP & ESCAPE CATCHER
        if hasattr(self, 'search_container') and hasattr(self.search_container, 'search_input'):
            if obj == self.search_container.search_input and event.type() == QEvent.Type.KeyPress:
                # Down Arrow jumps into the list
                if event.key() == Qt.Key.Key_Down:
                    self.focus_first_tree_item()
                    return True
                # Escape clears the box, collapses it, and jumps out
                elif event.key() == Qt.Key.Key_Escape:
                    self.search_container.search_input.clear()
                    if hasattr(self.search_container, 'collapse'):
                        self.search_container.collapse()
                    self.tree.setFocus(Qt.FocusReason.ShortcutFocusReason)
                    return True

        # 🟢 THE FIX: If the tree isn't built yet during startup, ignore tree events!
        if not hasattr(self, 'tree'):
            return super().eventFilter(obj, event)

        # 🟢 1. INTERCEPT KEY PRESSES
        if obj == self.tree and event.type() == QEvent.Type.KeyPress:
            text = event.text()
            key = event.key()
            from PyQt6.QtCore import QItemSelectionModel
            
            # 🟢 ESCAPE: Clear & collapse the search filter while navigating the tracklist!
            if key == Qt.Key.Key_Escape:
                if hasattr(self, 'search_container') and hasattr(self.search_container, 'search_input'):
                    acted = False
                    if self.search_container.search_input.text() != "":
                        self.search_container.search_input.clear()
                        acted = True
                    # If it's visually open, close it!
                    if self.search_container.search_input.maximumWidth() > 0:
                        if hasattr(self.search_container, 'collapse'):
                            self.search_container.collapse()
                        acted = True
                        
                    if acted:
                        return True # Eat the keypress so it doesn't bubble up!
            
            # 🟢 NEW: Edge-Bumping Pagination for Tracks!
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                curr_item = self.tree.currentItem()
                if curr_item:
                    curr_idx = self.tree.indexOfTopLevelItem(curr_item)
                    max_idx = self.tree.topLevelItemCount() - 1
                    
                    # Bumping DOWN at the very bottom -> Next Page
                    if curr_idx == max_idx and key == Qt.Key.Key_Down:
                        if hasattr(self, 'current_page') and self.current_page < self.total_pages:
                            self.pending_focus_direction = 'top' # Tell worker to focus top
                            self.change_page(self.current_page + 1)
                            return True
                            
                    # Bumping UP at the very top -> Previous Page
                    elif curr_idx == 0 and key == Qt.Key.Key_Up:
                        if hasattr(self, 'current_page') and self.current_page > 1:
                            self.pending_focus_direction = 'bottom' # Tell worker to focus bottom
                            self.change_page(self.current_page - 1)
                            return True

            # 🟢 THE IMMORTAL FAILSAFE: Catch Arrow Keys before the black hole!
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                if not self.tree.currentItem() and self.tree.topLevelItemCount() > 0:
                    self.tree.setCurrentItem(self.tree.topLevelItem(0), 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
                    return True # We saved the key press! Stop Qt from deleting it!
            
            # Ignore Spacebar so your Play/Pause shortcut still works perfectly
            if text and text.isprintable() and key != Qt.Key.Key_Space and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
                main_win = self.window()
                if hasattr(main_win, 'spotlight') and not main_win.spotlight.isVisible():
                    main_win.spotlight.show_search(initial_char=text)
                    return True 

        # 🟢 2. HANDLE MOUSE HOVERS (Your existing logic)
        if obj == self.tree.viewport() and event.type() == QEvent.Type.MouseMove:
            pos = event.position().toPoint()
            index = self.tree.indexAt(pos)
            
            # Clear delegate hovers for shifted columns
            if index.isValid() and index.column() != 4: self.album_delegate.clear_hover()
            if index.isValid() and index.column() != 3: self.artist_delegate.clear_hover()
            if index.isValid() and index.column() != 1: self.combined_delegate.clear_hover()
            if index.isValid() and index.column() != 5: self.year_delegate.clear_hover()
            if index.isValid() and index.column() != 6: self.genre_delegate.clear_hover()
        
        return super().eventFilter(obj, event)
    
    # --- ALBUM DETAIL MODE LOGIC ---


    def fit_to_columns(self):
        # Only apply this strict resizing when viewing an album
        if not getattr(self, 'album_mode_id', None): 
            return
            
        total_w = 0
        for i in range(self.tree.columnCount()):
            if not self.tree.isColumnHidden(i):
                total_w += self.tree.columnWidth(i)
                
        # Add 25px for the vertical scrollbar and a tiny visual buffer
        self.tree.setMaximumWidth(total_w + 25)
        
        # Align the tree to the left side of the window so it doesn't float in the center
        self.main_layout.setAlignment(self.tree, Qt.AlignmentFlag.AlignLeft)
    
    def on_item_clicked(self, item, column):
        if column == 7: 
            data_variant = item.data(0, Qt.ItemDataRole.UserRole)
            if not data_variant or data_variant.get('type') != 'track': return
            track = data_variant['data']
            
            # 🟢 BOOLEAN FIX
            raw_state = track.get('starred')
            if isinstance(raw_state, str): current_state = raw_state.lower() in ('true', '1')
            else: current_state = bool(raw_state)
            new_state = not current_state
            
            track['starred'] = new_state
            data_variant['data'] = track
            item.setData(0, Qt.ItemDataRole.UserRole, data_variant)
            item.setText(7, "♥" if new_state else "♡")
            # 🟢 MASTER COLOR FIX
            item.setForeground(7, QColor(self.current_accent) if new_state else QColor("#555"))
            
            if self.client: self.client.set_favorite(track.get('id'), new_state)

    def on_item_double_clicked(self, item, column):
        if column == 7: return # Skip favorite
        
        # 🟢 Safely check link hovers before playing
        if column == 1:  
            index = self.tree.currentIndex()
            if index.isValid():
                cursor_pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
                if self.combined_delegate.is_over_artist(index, cursor_pos): return
        elif column == 3:  
            index = self.tree.currentIndex()
            if index.isValid():
                cursor_pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
                if self.artist_delegate.is_over_text(index, cursor_pos): return  
        elif column == 4:
            index = self.tree.currentIndex()
            if index.isValid():
                cursor_pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
                if self.album_delegate.is_over_text(index, cursor_pos): return
        elif column == 5:
            index = self.tree.currentIndex()
            if index.isValid():
                cursor_pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
                if self.year_delegate.is_over_text(index, cursor_pos): return
        elif column == 6:
            index = self.tree.currentIndex()
            if index.isValid():
                cursor_pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
                if self.genre_delegate.current_hover[1] is not None: return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data['type'] == 'track':
            self.play_track.emit(data['data'])

    def toggle_track_favorite(self, track, item):
        # 🟢 BOOLEAN FIX
        raw_state = track.get('starred')
        if isinstance(raw_state, str): current_state = raw_state.lower() in ('true', '1')
        else: current_state = bool(raw_state)
        new_state = not current_state
        
        track['starred'] = new_state
        data_variant = item.data(0, Qt.ItemDataRole.UserRole)
        data_variant['data'] = track
        item.setData(0, Qt.ItemDataRole.UserRole, data_variant)
        item.setText(7, "♥" if new_state else "♡")
        # 🟢 MASTER COLOR FIX
        item.setForeground(7, QColor(self.current_accent) if new_state else QColor("#555"))
        
        if self.client: self.client.set_favorite(track.get('id'), new_state)

    def play_full_album(self, album_id):
        if not album_id or not self.client: return
        try:
            tracks = self.client.get_album_tracks(str(album_id))
            if tracks: self.play_multiple_tracks.emit(tracks)
        except Exception as e: print(f"Error fetching album tracks: {e}")
    
    def show_context_menu(self, pos):
        selected_items = self.tree.selectedItems()
        if not selected_items: return
        selected_items.sort(key=lambda i: self.tree.indexOfTopLevelItem(i))
        selected_tracks = []
        for item in selected_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') == 'track': selected_tracks.append(data['data'])
        if not selected_tracks: return
        
        count = len(selected_tracks); is_multi = count > 1; first_track = selected_tracks[0]
        
        # 🟢 NEW: Collect track IDs for playlist operations
        track_ids = [str(t.get('id')) for t in selected_tracks if t.get('id')]
        
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #222; color: #ddd; border: 1px solid #444; } QMenu::item { padding: 6px 25px; } QMenu::item:selected { background-color: #333; } QMenu::item:disabled { color: #555; } QMenu::separator { height: 1px; background: #444; margin: 5px 0; }")
        
        action_play = menu.addAction(f"Play Now ({count})" if is_multi else "Play Now")
        if not is_multi:
            album_id = first_track.get('albumId') or first_track.get('parent')
            if album_id:
                menu.addAction(f"Play Album: {first_track.get('album', 'Unknown')}").triggered.connect(lambda: self.play_full_album(album_id))
        
        action_next = menu.addAction(f"Play Next ({count})" if is_multi else "Play Next")
        action_queue = menu.addAction(f"Add to Queue ({count})" if is_multi else "Add to Queue")
        menu.addSeparator()
        action_fav = menu.addAction(f"Toggle Favorite ({count})" if is_multi else ("Unlove (♥)" if first_track.get('starred') else "Love (♡)"))
        menu.addSeparator()

        # 👇 🟢 NEW: REMOVE FROM PLAYLIST (Only shows if we are inside a playlist!) 👇
        current_playlist_id = getattr(self, 'current_playlist_id', None)
        if current_playlist_id:
            action_remove = menu.addAction(f"Remove from Playlist ({count})" if is_multi else "Remove from Playlist")
            action_remove.triggered.connect(lambda: self._remove_selected_from_playlist(selected_items))
            menu.addSeparator()
        # 👆 🟢 END NEW 👆
        
        # 🟢 NEW: ADD TO PLAYLIST SUBMENU
        if track_ids:
            add_menu = QMenu("Add to Playlist", menu)
            add_menu.setStyleSheet(menu.styleSheet())
            
            new_pl_action = QAction("+ New Playlist...", add_menu)
            new_pl_action.triggered.connect(lambda: self._add_to_new_playlist(track_ids))
            add_menu.addAction(new_pl_action)
            
            # Fetch cached playlists from main window
            main_win = self.window()
            playlists = []
            if hasattr(main_win, 'playlists_browser'):
                playlists = main_win.playlists_browser.all_playlists or []
                
            if playlists:
                add_menu.addSeparator()
                
                # Find out if we are currently looking at a playlist
                current_playlist_id = getattr(self, 'current_playlist_id', None)
                
                for pl in playlists:
                    pl_id = pl.get('id')
                    if not pl_id: continue
                    
                    # 🟢 Skip the current playlist so they can't add a song to the playlist they are already in!
                    if pl_id == current_playlist_id:
                        continue
                        
                    pl_name = pl.get('name', 'Unnamed')
                    pl_count = pl.get('songCount', '')
                    label_text = f"{pl_name}  ({pl_count})" if pl_count != '' else pl_name
                    
                    action = QAction(label_text, add_menu)
                    action.triggered.connect(
                        lambda checked, pid=pl_id, pname=pl_name: 
                            self._add_to_existing_playlist(pid, pname, track_ids)
                    )
                    add_menu.addAction(action)
                    
            menu.addMenu(add_menu)
            menu.addSeparator()
        # 🟢 END NEW

        if not is_multi and not getattr(self, 'album_mode_id', None):
            album_name = first_track.get('album', '')
            album_id   = first_track.get('albumId') or first_track.get('parent')
            primary_artist_id = first_track.get('artist_id') or first_track.get('artistId')
            artists = [p.strip() for p in re.split(
                r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )',
                first_track.get('artist', '')) if p.strip()]

            has_filter_items = bool(album_name and album_id) or bool(artists)
            if has_filter_items:
                menu.addSeparator()
            if album_name and album_id:
                menu.addAction(f"Filter by Album: {album_name}").triggered.connect(
                    lambda: self._filter_by_album(album_name, album_id)
                )
            for i, artist in enumerate(artists):
                aid = primary_artist_id if i == 0 else None
                menu.addAction(f"Filter by Artist: {artist}").triggered.connect(
                    lambda checked=False, a=artist, aid=aid: self._filter_by_artist(a, aid)
                )

        goto_menu = menu.addMenu("Go to")
        if is_multi: goto_menu.setEnabled(False)
        else:
            album_data = {'id': first_track.get('albumId') or first_track.get('parent'), 'title': first_track.get('album', 'Unknown'), 'artist': first_track.get('artist'), 'coverArt': first_track.get('coverArt')}
            if album_data['id']: goto_menu.addAction(f"Album: {album_data['title']}").triggered.connect(lambda: self.switch_to_album_tab.emit(album_data))
            goto_menu.addSeparator()
            artists = [p.strip() for p in re.split(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )', first_track.get('artist', 'Unknown')) if p.strip()]
            for art in artists: goto_menu.addAction(f"Artist: {art}").triggered.connect(lambda checked, a=art: self.switch_to_artist_tab.emit(a))

        if not is_multi:
            menu.addSeparator()
            action_info = menu.addAction("Get Info")
            action_info.triggered.connect(lambda: self._show_track_info(first_track))

        if is_multi:
             action_play.triggered.connect(lambda: self.play_multiple_tracks.emit(selected_tracks))
             action_next.triggered.connect(lambda: [self.play_next.emit(t) for t in reversed(selected_tracks)])
             action_queue.triggered.connect(lambda: [self.queue_track.emit(t) for t in selected_tracks])
             action_fav.triggered.connect(lambda: [self.toggle_track_favorite(selected_tracks[i], selected_items[i]) for i in range(len(selected_tracks))])
        else:
             action_play.triggered.connect(lambda: self.play_track.emit(first_track))
             action_next.triggered.connect(lambda: self.play_next.emit(first_track))
             action_queue.triggered.connect(lambda: self.queue_track.emit(first_track))
             action_fav.triggered.connect(lambda: self.toggle_track_favorite(first_track, selected_items[0]))
        menu.exec(self.tree.mapToGlobal(pos))

    def _show_track_info(self, track):
        client = getattr(self, 'client', None)
        accent = getattr(self, 'current_accent', '#1DB954')
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
        )
        dlg.exec()
        
    
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
                
            # 🟢 THE THREAD-SAFE FIX: No QTimers allowed!
            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            import time
            
            if hasattr(self, 'status_label'):
                # 1. Flash the success message
                QMetaObject.invokeMethod(
                    self.status_label, "setText",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, msg)
                )
                
                # 2. Pause the background thread for 3 seconds
                time.sleep(3)
                
                # 3. Restore the original text safely
                QMetaObject.invokeMethod(
                    self.status_label, "setText",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, f"{getattr(self, 'total_items', 0)} tracks")
                )

        threading.Thread(target=worker, daemon=True).start()

    def _add_to_new_playlist(self, track_ids):
        """Prompts for a name using the custom dialog, creates the playlist, then appends the tracks."""
        from components import NewPlaylistDialog
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
                    
                from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
                import time
                
                if hasattr(self, 'status_label'):
                    # 1. Safely flash the success message
                    QMetaObject.invokeMethod(
                        self.status_label, "setText",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, msg)
                    )
                    
                    # 2. Pause the background thread for 3 seconds (UI stays perfectly smooth!)
                    time.sleep(3)
                    
                    # 3. Safely restore the original track count text
                    QMetaObject.invokeMethod(
                        self.status_label, "setText",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, f"{getattr(self, 'total_items', 0)} tracks")
                    )

            threading.Thread(target=worker, daemon=True).start()
    
    def _remove_selected_from_playlist(self, selected_items):
        """Removes items from the UI and tells the playlist detail view to sync the changes to the server."""
        if not selected_items:
            return
            
        # 1. Remove the items from the UI instantly
        for item in selected_items:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx != -1:
                self.tree.takeTopLevelItem(idx)
                
        # 2. Trigger the "orderChanged" signal. 
        # Since PlaylistDetailView is already listening to this to save Drag & Drop changes,
        # it will automatically read the new tracks list and save the deletion to Navidrome!
        if hasattr(self.tree, 'drag_helper'):
            self.tree.drag_helper.orderChanged.emit()
            
        # 3. Update the track count in the header
        new_total = self.tree.topLevelItemCount()
        self.total_items = new_total
        if hasattr(self, 'status_label'):
            self.status_label.setText(f"{new_total} tracks")
    
    def set_accent_color(self, color, alpha=0.3):
        if getattr(self, 'current_accent', None) == color and getattr(self, 'current_alpha', None) == alpha:
            return
            
        self.current_accent = color
        self.current_alpha = alpha
        
        # 🟢 FREEZE THE UI: Prevents all layout jumping and stylesheet flickering!
        self.setUpdatesEnabled(False)
        try:
            # 1. Update Delegates
            if hasattr(self, 'combined_delegate'): self.combined_delegate.set_master_color(color)
            if hasattr(self, 'artist_delegate'): self.artist_delegate.set_master_color(color)
            if hasattr(self, 'album_delegate'): self.album_delegate.set_master_color(color)
            if hasattr(self, 'year_delegate'): self.year_delegate.set_master_color(color)
            if hasattr(self, 'genre_delegate'): self.genre_delegate.set_master_color(color)
            if hasattr(self, 'date_added_delegate'): self.date_added_delegate.set_master_color(color)
            
            # 2. Safely Update Components & Force Hide if needed
            in_album = getattr(self, 'album_mode_id', None) is not None
            
            if hasattr(self, 'search_container'):
                h = self.search_container.isHidden()
                self.search_container.set_accent_color(color)
                if h: self.search_container.hide()
                
            if hasattr(self, 'burger_btn'):
                h = self.burger_btn.isHidden()
                try:
                    icon_path = resource_path("img/burger.png")
                    pixmap = QPixmap(icon_path)
                    if not pixmap.isNull():
                        painter = QPainter(pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                        painter.fillRect(pixmap.rect(), QColor(color))
                        painter.end()
                        self.burger_btn.setIcon(QIcon(pixmap))
                except Exception as e:
                    print(f"Error tinting burger icon: {e}")
                if h or in_album: self.burger_btn.hide()

            if hasattr(self, 'refresh_btn'):
                try:
                    pixmap = QPixmap(resource_path("img/refresh.png")).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    if not pixmap.isNull():
                        painter = QPainter(pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                        painter.fillRect(pixmap.rect(), QColor(color))
                        painter.end()
                        self.refresh_btn.setIcon(QIcon(pixmap))
                except Exception as e:
                    print(f"Error tinting refresh icon: {e}")

            if hasattr(self, 'clear_filters_btn'):
                h = self.clear_filters_btn.isHidden()
                try:
                    icon_path = resource_path("img/filter_off-2.png")
                    pixmap = QPixmap(icon_path).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    if not pixmap.isNull():
                        painter = QPainter(pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                        painter.fillRect(pixmap.rect(), QColor(color))
                        painter.end()
                        self.clear_filters_btn.setIcon(QIcon(pixmap))
                except Exception as e:
                    print(f"Error tinting clear filters icon: {e}")
                if h: self.clear_filters_btn.hide()

            if hasattr(self, 'play_filtered_btn'):
                h = self.play_filtered_btn.isHidden()
                try:
                    pixmap = QPixmap(resource_path("img/play-button.png")).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    if not pixmap.isNull():
                        painter = QPainter(pixmap)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                        painter.fillRect(pixmap.rect(), QColor(color))
                        painter.end()
                        self.play_filtered_btn.setIcon(QIcon(pixmap))
                except Exception as e:
                    print(f"Error tinting play filtered icon: {e}")
                if h: self.play_filtered_btn.hide()
            
            if hasattr(self, 'footer'):
                h = self.footer.isHidden()
                self.footer.set_accent_color(color)
                if h or in_album: self.footer.hide()
                
            # 🟢 3. Route to the new styling engine!
            self.update_scrollbar_color(color, alpha)
            
            self.tree.viewport().update()
        finally:
            self.setUpdatesEnabled(True)

    def update_scrollbar_color(self, color_hex, alpha=0.3):
        row_height = "50px" if getattr(self, 'is_album_mode', False) else "75px"
        
        css = f"""
        QScrollBar:vertical {{ border: none; background: rgba(0, 0, 0, 0.05); width: 10px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }}
        QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {color_hex}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        
        QScrollBar:horizontal {{ border: none; background: rgba(0, 0, 0, 0.05); height: 10px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: #333; min-width: 30px; border-radius: 5px; }}
        QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {color_hex}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
        
        /* 🟢 THE FIX: Alpha injected into QTreeWidget background! */
        QTreeWidget {{ background: rgba(12, 12, 12, {alpha}); border: none; font-size: 10pt; outline: none; }} 
        QTreeWidget::item {{ height: {row_height}; padding: 0 4px; border-top: 1px solid rgba(255,255,255,0.05); border-bottom: none; color: #ddd; }}
        QTreeWidget::item:selected {{ background: rgba(255,255,255,0.1); color: {color_hex}; }} 
        QTreeWidget::item:hover {{ background: rgba(255,255,255,0.05); color: {color_hex}; }}
        
        QHeaderView::section {{ background: transparent; color: #888; border: none; border-bottom: 1px solid rgba(255,255,255,0.1); padding: 10px 5px; font-weight: bold; text-transform: uppercase; font-size: 11px; }} 
        QHeaderView::section:first {{ padding-right: 5px; }} 
        QHeaderView::section:first:hover {{ background: transparent; }} 
        QHeaderView::section:first:pressed {{ background: transparent; }}
        """
        self.tree.setStyleSheet(css)
        
        # 🟢 ALSO apply the alpha to the outer container so the blank space at the bottom matches!
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")