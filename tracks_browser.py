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
                             QSpacerItem, QSizePolicy, QToolButton, QTreeWidget, QFrame)



from PyQt6.QtCore import (Qt, pyqtSignal, QTimer, QModelIndex, QEvent, QPoint, QRect,
                          QPropertyAnimation, QEasingCurve, QSize, QParallelAnimationGroup,
                          QRectF, QThread, QSettings, QObject)

from PyQt6.QtGui import QAction, QColor, QCursor, QFontMetrics, QIcon, QPainter, QPixmap, QPainterPath, QFont

from albums_browser import resource_path
from components import PaginationFooter, SmartSearchContainer

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
                    except Exception as e:
                        pass

class LiveTrackWorker(QThread):
    results_ready = pyqtSignal(list, int, int, int)

    def __init__(self, client, query_text, page, page_size, is_album_mode, album_id, known_total=0, sort_field="title", sort_order="ASC"):
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

                if self.query_text:
                    # 🟢 SEARCH MODE: Use search3 (reliable cross-field search).
                    # The native API's _q param is unreliable — search3 always works.
                    tracks = self.client.get_tracks_live(
                        query=self.query_text,
                        size=self.page_size,
                        offset=start
                    )

                    # 🛑 Exit immediately if the thread was cancelled!
                    if self.is_cancelled: return

                    # Sort search results client-side to honour the header sort state
                    reverse = (self.sort_order == "DESC")
                    def _sort_key(t):
                        # play_count is stored as snake_case in the track dict
                        field = 'play_count' if self.sort_field == 'playCount' else self.sort_field
                        v = t.get(field, '') or ''
                        if self.sort_field in ('year', 'trackNumber', 'playCount'):
                            try: return int(v)
                            except: return 0
                        return str(v).lower()
                    tracks.sort(key=_sort_key, reverse=reverse)

                    count = len(tracks)
                    # Estimate total for pagination
                    if count == self.page_size:
                        total_items = start + self.page_size + 1  # There may be more pages
                    else:
                        total_items = start + count
                else:
                    # 🟢 BROWSE MODE: Native API for true server-side sorting + pagination
                    tracks, total_items = self.client.get_tracks_native_page(
                        sort_by=self.sort_field,
                        order=self.sort_order,
                        start=start,
                        end=end,
                        query=""
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

# --- SMART DELEGATES ---

class SmartSortHeader(QHeaderView):
    def __init__(self, parent=None):
        
        super().__init__(Qt.Orientation.Horizontal, parent)
        
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setStretchLastSection(False)
        self.up_icon = QIcon(resource_path("img/filter_up.png"))
        self.down_icon = QIcon(resource_path("img/filter_down.png"))

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
        
        arrow_size = 14
        spacing = 6
        has_arrow = self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex
        
        content_width = text_width
        if has_arrow: content_width += spacing + arrow_size
            
        alignment = opt.textAlignment

        # 🟢 FORCE ALIGNMENTS
        if logicalIndex in (0, 7, 9):
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
        
        if has_arrow:
            arrow_x = start_x + text_width + spacing
            arrow_y = rect.center().y() - (arrow_size // 2)
            
            if arrow_x + arrow_size <= rect.right():
                if self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder:
                    icon = self.down_icon
                else:
                    icon = self.up_icon
                
                icon.paint(painter, int(arrow_x), int(arrow_y), arrow_size, arrow_size)

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
            if is_over_text:
                if self.hovered_index != index:
                    self.hovered_index = index
                    self.parent().viewport().update()
                    return True
            else:
                if self.hovered_index == index:
                    self.hovered_index = None
                    self.parent().viewport().update()
                    return True
                    
        elif event.type() == QEvent.Type.MouseButtonRelease:
            if is_over_text and self.hovered_index == index:
                self.clicked.emit(index)
                return True
        return False

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
            if hit_artist:
                self.artist_clicked.emit(hit_artist.strip())
                return True
        return False

# --- DELEGATE 3: MULTI-GENRE (For Genre Column) ---

class MultiGenreDelegate(QStyledItemDelegate):
    """Delegate for displaying multiple genres with separators, now using strict 3-line wrapping and vertical centering"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_color = QColor("#cccccc")
        # Use same separators as artist delegate
        self.split_regex = re.compile(r'( /// | • | / |, )')
    
    def set_master_color(self, color):
        """Update the master color for this delegate"""
        self.master_color = QColor(color)
    
    def paint(self, painter, option, index):
        if not index.isValid():
            return
        
        opts = QStyleOptionViewItem(option)
        self.initStyleOption(opts, index)
        opts.state &= ~QStyle.StateFlag.State_HasFocus
        
        # Draw background
        style = opts.widget.style() if opts.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opts, painter, opts.widget)
        
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
            
        is_selected = (opts.state & QStyle.StateFlag.State_Selected)
        is_row_hover = (opts.state & QStyle.StateFlag.State_MouseOver)
        
        if is_selected or is_row_hover:
            base_color = self.master_color
        else:
            base_color = QColor("#cccccc")
            
        painter.save()
        
        
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
        max_lines = getattr(self, 'max_lines', 3)
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
        
        
        start_y = draw_rect.top() + (draw_rect.height() - total_height) // 2 + fm.ascent()
        
        for i, line_str in enumerate(display_lines):
            # Split the line to color the separators gray
            parts = self.split_regex.split(line_str)
            current_x = draw_rect.left()
            
            for part in parts:
                if not part: continue
                
                
                if self.split_regex.fullmatch(part):
                    painter.setPen(QColor("#777"))
                else:
                    painter.setPen(base_color)
                    
                painter.drawText(int(current_x), int(start_y + i * line_spacing), part)
                current_x += fm.horizontalAdvance(part)
                
        painter.restore()
    
    def editorEvent(self, event, model, option, index):
        """Handle events - genres are non-clickable so just return False"""
        return False

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
        title_font.setPointSize(11)
        title_font.setBold(True)
        
        artist_font = QFont(opts.font)
        artist_font.setPointSize(10)

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
        
        # 🟢 CLEAN HEADER ASSEMBLY
        header_layout.addWidget(self.status_label)
        header_layout.addStretch() # Pushes the label to the far left, and search to the far right!
        header_layout.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)

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
     
        
        
        self.tree.setHeaderLabels(["#", "TRACK", "TITLE", "ARTIST", "ALBUM", "YEAR", "GENRE", "♥", "PLAYS", "LENGTH"])
        self.tree.headerItem().setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.tree.headerItem().setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)
        self.tree.headerItem().setTextAlignment(9, Qt.AlignmentFlag.AlignCenter)
        
        # 🟢 Tell Qt to center the TRACK header natively
        self.tree.headerItem().setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        
        
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows) 
        
        #self.tree.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tree.setMouseTracking(True) 
        
        self.tree.header().setSectionsMovable(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().setSortIndicatorShown(True)
        self.tree.header().sectionClicked.connect(self.on_header_clicked)
        self.tree.header().setSortIndicator(self.sort_col, self.sort_order)
        
        # 🟢 Force column 0 (#) to perfectly shrink-to-fit its content!
        # 🟢 SPEED FIX: Fixed width prevents Qt from measuring 500 fonts!
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(0, 45)        
        # 🟢 THE FIX: Tell TRACK (1) and ALBUM (4) to dynamically absorb window resizes (Stretch mode)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        
        # Keep all the other columns Interactive (they stay the exact size you defined, but can be dragged manually)
        for i in [2, 3, 5, 6, 7, 8, 9]: 
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        # 🟢 Updated column widths (Matching your exact requested specs)
        self.tree.setColumnWidth(1, 350) # TRACK (Combined)
        self.tree.setColumnWidth(2, 200) # TITLE
        self.tree.setColumnWidth(3, 200) # ARTIST
        self.tree.setColumnWidth(4, 240) # ALBUM
        self.tree.setColumnWidth(5, 70)  # YEAR
        self.tree.setColumnWidth(6, 120) # GENRE
        self.tree.setColumnWidth(7, 60)  # ♥
        self.tree.setColumnWidth(8, 70)  # PLAYS
        self.tree.setColumnWidth(9, 75)  # LENGTH

        # 🟢 NEW: Define minimum widths and attach the drag-limiter
        self.min_widths = {2: 200, 3: 200, 5: 70, 6: 120, 7: 60, 8: 70, 9: 75}
        self.tree.header().sectionResized.connect(self.enforce_min_widths)

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
        self.tree.setItemDelegateForColumn(6, self.genre_delegate)
        
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
        self.load_from_db(reset=True)

    
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

    def showEvent(self, event):
        super().showEvent(event)
        self.check_for_updates()

    def check_for_updates(self):
        pass  # Updates are driven by SmartBackgroundSyncer signals, not local DB polling.
    
    # --- BURGER MENU: COLUMN VISIBILITY ---
    
    def show_column_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #222; color: #ddd; border: 1px solid #444; } QMenu::item { padding: 6px 25px; } QMenu::item:selected { background-color: #333; }")
        
        headers = ["#", "TRACK", "TITLE", "ARTIST", "ALBUM", "YEAR", "GENRE", "♥", "PLAYS", "LENGTH"]
        
        for i, name in enumerate(headers):
            # Skip TRACK column so it is always visible and can't be hidden
            if i == 1: continue
                
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

    def load_from_db(self, reset=False):
        if getattr(self, 'album_mode_id', None): return
        if reset: self.current_page = 1
        self._start_worker(is_album=False, album_id=None)

    def load_album_tracks(self, album_id):
        self._start_worker(is_album=True, album_id=album_id)

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

        self.live_worker = LiveTrackWorker(
            client=self.client, 
            query_text=self.current_query, 
            page=self.current_page, 
            page_size=self.page_size, 
            is_album_mode=is_album, 
            album_id=album_id,
            known_total=known_total,
            sort_field=sort_field,     # 🟢 Pass it in
            sort_order=sort_order      # 🟢 Pass it in
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
        
        for i in range(10): self.tree.setItemDelegateForColumn(i, None)
        self.tree.setItemDelegateForColumn(1, self.combined_delegate)
        self.tree.setItemDelegateForColumn(3, self.artist_delegate)
        self.tree.setItemDelegateForColumn(4, self.album_delegate)
        self.tree.setItemDelegateForColumn(6, self.genre_delegate)
        
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
                    total_calc_h += 30 # Header height
                
                track_num = t.get('trackNumber', i + 1)
                items_to_add.append(self.create_track_item(t, track_num))
                total_calc_h += row_px # Track height
                
            # 🟢 THE SPEED FIX: Insert all 500 items in exactly 1 operation!
            self.tree.addTopLevelItems(items_to_add)
            
            # 🟢 THE HEIGHT FIX: Apply the calculated height instantly (bypasses slow adjust_height loops)
            MAX_TREE_H = 800
            capped_h = min(total_calc_h, MAX_TREE_H)
            self.tree.setMinimumHeight(capped_h)
            self.tree.setMaximumHeight(total_calc_h)
            self.setMinimumHeight(capped_h + 50) 
            self.setMaximumHeight(total_calc_h + 50)
            
        else:
            if hasattr(self, 'status_label'):
                self.status_label.setText(f"{self.total_items} tracks")
                
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

        self.tree.setUpdatesEnabled(True)
        self.start_cover_loader(tracks)

    
    def show_skeleton_ui(self):
        """Draws the exact visual from your screenshot: Numbers on the left, pills on the right!"""
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        
        if not hasattr(self, 'skeleton_delegate'):
            self.skeleton_delegate = SkeletonDelegate(self.tree)
            
        # 🟢 Skip column 0 so it uses the standard text renderer for the numbers!
        for i in range(1, 10): 
            self.tree.setItemDelegateForColumn(i, self.skeleton_delegate)
            
        offset = (self.current_page - 1) * self.page_size
        for i in range(15):
            item = QTreeWidgetItem([""] * 10)
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
        
        # Store a reference to the original dict — avoid dict(t) copy overhead.
        # Overwrite 'starred' in-place so toggle logic stays consistent.
        t['starred'] = is_fav
        item.setData(0, Qt.ItemDataRole.UserRole, {'data': t, 'type': 'track'})
        
        return item
    
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
        
        normal_font = QFont("sans-serif", 11)
        normal_font.setBold(False)
        bold_font_large = QFont("sans-serif", 16); bold_font_large.setBold(True)
        bold_font_medium = QFont("sans-serif", 13); bold_font_medium.setBold(True)
        
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
                
                item.setFont(0, bold_font_large)
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, highlight_bg)
                    if col != 7: # Skip Heart column
                        item.setForeground(col, rgb)
                        if col > 0: item.setFont(col, bold_font_medium)
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
        self.tree.setColumnHidden(col, not is_checked)
        self.save_column_state()

    def save_column_state(self):
        state = {}
        for i in range(10): # 🟢 Now 10 columns
            state[str(i)] = self.tree.isColumnHidden(i)
        try:
            self._settings.setValue('tracks_columns_hidden', json.dumps(state))
        except: pass

    def load_column_state(self):
        try:
            state_str = self._settings.value('tracks_columns_hidden')
            if state_str:
                state = json.loads(state_str)
                for col_str, is_hidden in state.items():
                    col_idx = int(col_str)
                    if col_idx < 10: self.tree.setColumnHidden(col_idx, is_hidden)
            else:
                self.tree.setColumnHidden(2, True) # 🟢 Default hide standard Title
                self.tree.setColumnHidden(3, True) # 🟢 Default hide standard Artist
        except:
            self.tree.setColumnHidden(2, True)
            self.tree.setColumnHidden(3, True) # 🟢 Default hide standard Artist
    
    
    def enforce_min_widths(self, logicalIndex, oldSize, newSize):
        """Prevents the user from manually dragging non-stretch columns smaller than their initial size."""
        if getattr(self, 'is_album_mode', False): return
        if hasattr(self, 'min_widths') and logicalIndex in self.min_widths:
            min_w = self.min_widths[logicalIndex]
            if newSize < min_w:
                # Silently snap it back to the minimum size if they drag it too small
                self.tree.header().blockSignals(True)
                self.tree.header().resizeSection(logicalIndex, min_w)
                self.tree.header().blockSignals(False)

    def resizeEvent(self, event):
        """Custom Window Resize Logic: Shrink oversized columns before squishing TRACK and ALBUM"""
        if not getattr(self, 'is_album_mode', False) and hasattr(self, 'min_widths'):
            old_w = event.oldSize().width()
            new_w = event.size().width()
            
            # 🟢 If the window is shrinking...
            if old_w > 0 and new_w < old_w:
                diff = old_w - new_w
                
                # ...steal back the missing pixels from any column the user made larger than minimum!
                for col, min_w in self.min_widths.items():
                    if diff <= 0: break
                    if not self.tree.isColumnHidden(col):
                        current_w = self.tree.columnWidth(col)
                        if current_w > min_w:
                            # Calculate how much we can safely steal from this column
                            steal = min(diff, current_w - min_w)
                            
                            self.tree.header().blockSignals(True)
                            self.tree.setColumnWidth(col, current_w - steal)
                            self.tree.header().blockSignals(False)
                            
                            diff -= steal # Update remaining pixels to steal
                            
        # Now pass the event to Qt, where TRACK and ALBUM (Stretch) will absorb whatever shrink is left over!
        super().resizeEvent(event)

    def on_header_clicked(self, logical_index):
        # Ignore click on # column (index 0) if desired
        if logical_index == 0: 
            self.tree.header().setSortIndicator(self.sort_col, self.sort_order)
            return

        if getattr(self, 'album_mode_id', None): 
            return
            
        # Ignore click on # column
        if logical_index == 0: 
            self.tree.header().setSortIndicator(self.sort_col, self.sort_order)
            return
        
        # Toggle sort order
        if self.sort_col == logical_index:
            if self.sort_order == Qt.SortOrder.AscendingOrder:
                self.sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.sort_order = Qt.SortOrder.AscendingOrder
        else:
            self.sort_col = logical_index
            self.sort_order = Qt.SortOrder.AscendingOrder
            
        # Update Header State
        self.tree.header().setSortIndicator(self.sort_col, self.sort_order)
        
        # 🟢 FIX: Force the header to repaint immediately so the arrow flips
        self.tree.header().viewport().update()
        
        # 🟢 NEW: Save the user's choice to the database!
        self.save_sort_state()
        
        # Reload Data
        self.load_from_db(reset=True)

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
        else: field = "title"
        
        return f"{field} {order}"

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.search_timer.start()
    
    def execute_search(self): 
        # 🟢 STRICT ROUTING: Send the search to the correct loader and nowhere else!
        if getattr(self, 'album_mode_id', None):
            # 🟢 THE FIX: Pass the album ID that we saved!
            self.load_album_tracks(self.album_mode_id)
        else:
            self.load_from_db(reset=True)

    def set_album_mode(self, enabled=True):
        self.is_album_mode = enabled
        self.combined_delegate.is_album_mode = enabled
        if enabled:
            # Hide redundant columns
            for col in [2, 3, 4, 5, 6, 8]: 
                self.tree.hideColumn(col)
                
            self.tree.showColumn(0) # #
            self.tree.showColumn(1) # TRACK
            self.tree.showColumn(7) # Heart
            self.tree.showColumn(9) # LENGTH
            
            header = self.tree.header()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(0, 45)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            
            # 🟢 THE FIX: Change to Fixed so Qt actually listens to our widths!
            header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)
            
            # Now we can manually set them exactly how wide we want them!
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

        goto_menu = menu.addMenu("Go to")
        if is_multi: goto_menu.setEnabled(False) 
        else:
            album_data = {'id': first_track.get('albumId') or first_track.get('parent'), 'title': first_track.get('album', 'Unknown'), 'artist': first_track.get('artist'), 'coverArt': first_track.get('coverArt')}
            if album_data['id']: goto_menu.addAction(f"Album: {album_data['title']}").triggered.connect(lambda: self.switch_to_album_tab.emit(album_data))
            goto_menu.addSeparator()
            artists = [p.strip() for p in re.split(r'(?: /// | • | / | feat\. | Feat\. | vs\. | Vs\. | pres\. | Pres\. |, )', first_track.get('artist', 'Unknown')) if p.strip()]
            for art in artists: goto_menu.addAction(f"Artist: {art}").triggered.connect(lambda checked, a=art: self.switch_to_artist_tab.emit(a))

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
            if hasattr(self, 'genre_delegate'): self.genre_delegate.set_master_color(color)
            
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
        QTreeWidget {{ background: rgba(12, 12, 12, {alpha}); border: none; font-size: 12pt; outline: none; }} 
        QTreeWidget::item {{ height: {row_height}; padding: 0 4px; border-top: 1px solid rgba(255,255,255,0.05); border-bottom: none; color: #ddd; }}
        QTreeWidget::item:selected {{ background: rgba(255,255,255,0.1); color: {color_hex}; }} 
        QTreeWidget::item:hover {{ background: rgba(255,255,255,0.05); color: {color_hex}; }}
        
        QHeaderView::section {{ background: transparent; color: #888; border: none; border-bottom: 1px solid rgba(255,255,255,0.1); padding: 5px; padding-right: 5px; font-weight: bold; text-transform: uppercase; font-size: 11px; }} 
        QHeaderView::section:first {{ padding-right: 5px; }} 
        QHeaderView::section:first:hover {{ background: transparent; }} 
        QHeaderView::section:first:pressed {{ background: transparent; }}
        """
        self.tree.setStyleSheet(css)
        
        # 🟢 ALSO apply the alpha to the outer container so the blank space at the bottom matches!
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")