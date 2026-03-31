import json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QStackedWidget, QListWidget, QListWidgetItem,
                             QScrollArea, QPushButton, QApplication)
from PyQt6.QtCore import (Qt, pyqtSignal, QSize, QThread, QTimer,
                          QAbstractListModel, QModelIndex, pyqtSlot, QObject, QUrl, QPoint)
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtQuick import QQuickImageProvider

from albums_browser import resource_path, GridItemDelegate, GridCoverWorker, CoverImageProvider, QMLGridWrapper
from components import PaginationFooter, SmartSearchContainer
from tracks_browser import TracksBrowser

class PlaylistModel(QAbstractListModel):
    TITLE_ROLE    = Qt.ItemDataRole.UserRole + 1
    SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
    COVER_ID_ROLE = Qt.ItemDataRole.UserRole + 3
    RAW_DATA_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self):
        super().__init__()
        self.playlists = []

    def rowCount(self, parent=QModelIndex()): 
        return len(self.playlists)

    def data(self, index, role):
        if not index.isValid(): return None
        p = self.playlists[index.row()]
        if role == self.TITLE_ROLE:    return p.get('title') or p.get('name') or 'Unknown'
        if role == self.SUBTITLE_ROLE: return p.get('subtitle', '')
        if role == self.COVER_ID_ROLE: return p.get('coverId_forced') or p.get('cover_id') or ''
        if role == self.RAW_DATA_ROLE: return p
        return None

    def roleNames(self):
        return {
            self.TITLE_ROLE:    b"playlistTitle",
            self.SUBTITLE_ROLE: b"playlistSubtitle",
            self.COVER_ID_ROLE: b"coverId",
            self.RAW_DATA_ROLE: b"rawData",
        }

    def reset_data(self, new_playlists):
        self.beginResetModel()
        self.playlists = new_playlists
        self.endResetModel()

    def update_cover(self, cover_id):
        import time
        forced_id = f"{cover_id}?t={time.time()}"
        for i, p in enumerate(self.playlists):
            if p.get('cover_id') == cover_id:
                p['coverId_forced'] = forced_id
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [self.COVER_ID_ROLE])

class PlaylistBridge(QObject):
    itemClicked        = pyqtSignal(dict)
    playClicked        = pyqtSignal(dict)
    itemRightClicked   = pyqtSignal(int)
    backgroundRightClicked = pyqtSignal()
    accentColorChanged = pyqtSignal(str)
    bgAlphaChanged     = pyqtSignal(float)
    keyTextForwarded   = pyqtSignal(str)
    slashPressed       = pyqtSignal()

    def __init__(self, playlist_model):
        super().__init__()
        self.playlist_model = playlist_model

    @pyqtSlot(int)
    def emitItemClicked(self, idx):
        if 0 <= idx < len(self.playlist_model.playlists):
            self.itemClicked.emit(self.playlist_model.playlists[idx])

    @pyqtSlot(int)
    def emitPlayClicked(self, idx):
        if 0 <= idx < len(self.playlist_model.playlists):
            self.playClicked.emit(self.playlist_model.playlists[idx])

    @pyqtSlot(int)
    def emitItemRightClicked(self, idx):
        self.itemRightClicked.emit(idx)

    @pyqtSlot()
    def emitBackgroundRightClicked(self):
        self.backgroundRightClicked.emit()

    @pyqtSlot(str)
    def forwardKeyText(self, text):
        self.keyTextForwarded.emit(text)

    @pyqtSlot()
    def forwardSlash(self):
        self.slashPressed.emit()

class PlaylistsWorker(QThread):
    results_ready = pyqtSignal(list)
    def __init__(self, client):
        super().__init__()
        self.client = client
    
    def run(self):
        if not self.client: return
        playlists = self.client.get_playlists()
        self.results_ready.emit(playlists)

class PlaylistTracksWorker(QThread):
    results_ready = pyqtSignal(dict, list)
    def __init__(self, client, playlist_data):
        super().__init__()
        self.client = client
        self.playlist_data = playlist_data
    
    def run(self):
        if not self.client: return
        tracks = self.client.get_playlist_tracks(self.playlist_data.get('id'))
        self.results_ready.emit(self.playlist_data, tracks)

class DragDropHelper(QObject):
    orderChanged = pyqtSignal()
    sig_drag_started = pyqtSignal(QPixmap, QPoint)
    sig_drag_moved = pyqtSignal(QPoint)
    sig_drag_ended = pyqtSignal()

class PlaylistDetailView(QWidget):
    play_clicked = pyqtSignal()
    shuffle_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_playlist_id = None
        self.current_accent = "#0066cc"
        self.current_tracks = []
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailBackground")
        self.setStyleSheet("#DetailBackground { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        from PyQt6.QtWidgets import QFrame
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QWidget#ScrollContent { background: transparent; }
        """)
        
        self.content_widget = QWidget()
        self.content_widget.setObjectName("ScrollContent")
        self.content_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(20)

        # --- HEADER ---
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(25)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(220, 220)
        self.cover_label.setStyleSheet("background-color: #222; border-radius: 8px; border: 1px solid #333;")
        self.cover_label.setScaledContents(True)

        meta_container = QWidget()
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 10, 0, 10)
        meta_layout.setSpacing(5)

        self.lbl_type = QLabel("PLAYLIST")
        self.lbl_type.setStyleSheet("color: #ddd; font-weight: bold; font-size: 11px;")

        self.lbl_title = QLabel("Playlist Title")
        self.lbl_title.setStyleSheet("color: white; font-weight: 900; font-size: 36px;")
        self.lbl_title.setWordWrap(True)

        self.lbl_meta = QLabel("Loading...")
        self.lbl_meta.setStyleSheet("color: #aaa; font-weight: bold; font-size: 13px;")

        self.lbl_artist = QLabel("Owner")
        self.lbl_artist.setStyleSheet("color: #aaa; font-weight: bold; font-size: 13px;")
        
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 15, 0, 0)
        btn_layout.setSpacing(15)
        
        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(60, 60) 
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        
        self.btn_shuffle = QPushButton()
        self.btn_shuffle.setFixedSize(40, 40)
        self.btn_shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shuffle.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
        self.btn_shuffle.setStyleSheet("QPushButton { outline: none; background: transparent; border: 2px solid #555; border-radius: 20px; } QPushButton:hover { border-color: white; }")
        
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QSize
        try:
            from albums_browser import resource_path
            self.btn_play.setIcon(QIcon(resource_path("img/play.png")))
            self.btn_play.setIconSize(QSize(15, 15)) 
        except: pass
        
        self.btn_play.clicked.connect(self.play_clicked.emit)
        self.btn_shuffle.clicked.connect(self.shuffle_clicked.emit)
        
        btn_layout.addWidget(self.btn_play)
        btn_layout.addWidget(self.btn_shuffle)
        btn_layout.addStretch()
        
        meta_layout.addWidget(self.lbl_type)
        meta_layout.addWidget(self.lbl_title)
        meta_layout.addWidget(self.lbl_meta)
        meta_layout.addWidget(self.lbl_artist)
        meta_layout.addWidget(btn_row)
        meta_layout.addStretch()
        
        header_layout.addWidget(self.cover_label)
        header_layout.addWidget(meta_container)
        
        self.layout.addWidget(header_container)

        # --- TRACKS BROWSER ---
        self.track_list = TracksBrowser(None)
        self.track_list.set_album_mode(True)
        self.track_list.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.layout.addWidget(self.track_list)
        self.layout.addStretch() 
        
        # DRAG & DROP INJECTION
        import types
        from PyQt6.QtWidgets import QAbstractItemView, QTreeWidget
        from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor, QCursor
        from PyQt6.QtCore import QRect, QPoint
        
        tree = self.track_list.tree
        tree.setDragEnabled(True)
        tree.setAcceptDrops(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        tree.setDropIndicatorShown(True)
        tree.setDragDropOverwriteMode(False)
        tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        
        self.drag_helper = DragDropHelper(self)
        tree.drag_helper = self.drag_helper
        
        def mousePressEvent(self, event):
            self._drag_start_pos = event.position().toPoint()
            QTreeWidget.mousePressEvent(self, event)

        def startDrag(self, supportedActions):
            items = self.selectedItems()
            if not items: return
            item_rect = self.visualItemRect(items[0])
            full_row_rect = QRect(0, item_rect.y(), self.viewport().width(), item_rect.height())
            captured = self.viewport().grab(full_row_rect)

            final = QPixmap(captured.size())
            final.fill(Qt.GlobalColor.transparent)
            p = QPainter(final)
            p.setOpacity(0.8)
            p.fillRect(final.rect(), QColor(25, 25, 25, 255))
            p.drawPixmap(0, 0, captured)
            p.end()

            row_tl = self.viewport().mapTo(self, full_row_rect.topLeft())
            click = self._drag_start_pos if hasattr(self, '_drag_start_pos') else self.mapFromGlobal(QCursor.pos())
            self._hot_spot_vector = click - row_tl
            global_start = self.mapToGlobal(row_tl)

            self.drag_helper.sig_drag_started.emit(final, global_start)
            
            drag = QDrag(self)
            drag.setMimeData(self.mimeData(items))
            drag.setHotSpot(self._hot_spot_vector)
            drag.exec(supportedActions)
            self.drag_helper.sig_drag_ended.emit()

        def dragMoveEvent(self, event):
            pos = event.position().toPoint()
            target = pos - getattr(self, '_hot_spot_vector', QPoint(0,0))
            self.drag_helper.sig_drag_moved.emit(self.mapToGlobal(target))
            QTreeWidget.dragMoveEvent(self, event)
            
            
            if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem:
                event.ignore()

        def dropEvent(self, event):
            
            if event.source() is self:
                drop_target = self.itemAt(event.position().toPoint())
                drop_index = self.indexOfTopLevelItem(drop_target) if drop_target else self.topLevelItemCount()
                
                indicator = self.dropIndicatorPosition()
                if indicator == QAbstractItemView.DropIndicatorPosition.OnItem:
                    event.ignore()
                    return
                elif indicator == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    drop_index += 1
                
                for item in self.selectedItems():
                    idx = self.indexOfTopLevelItem(item)
                    if idx != -1:
                        if idx < drop_index:
                            drop_index -= 1 # Adjust index because we are removing an item above it
                        taken = self.takeTopLevelItem(idx)
                        self.insertTopLevelItem(drop_index, taken)
                        taken.setSelected(True) # Keep it highlighted after dropping!
                        drop_index += 1
                        
                event.accept()
                self.drag_helper.orderChanged.emit()
            else:
                QTreeWidget.dropEvent(self, event)

        tree.mousePressEvent = types.MethodType(mousePressEvent, tree)
        tree.startDrag = types.MethodType(startDrag, tree)
        tree.dragMoveEvent = types.MethodType(dragMoveEvent, tree)
        tree.dropEvent = types.MethodType(dropEvent, tree)
        
        self.drag_helper.orderChanged.connect(self.on_playlist_reordered)
        # ----------------------------------------------------

        # Finish setting up the single track list
        self.scroll_area.setWidget(self.content_widget)
        main_layout.addWidget(self.scroll_area)
        self.track_list.tree.installEventFilter(self)

        if hasattr(self.track_list, 'search_container'):
            try: self.track_list.search_container.text_changed.disconnect()
            except: pass
            self.track_list.search_container.text_changed.connect(self.filter_local_tracks)

    def on_playlist_reordered(self):
        new_tracks = []
        tree = self.track_list.tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            item.setText(0, str(i + 1)) # Instantly re-number the "#" column
            
            from PyQt6.QtCore import Qt
            wrapped = item.data(0, Qt.ItemDataRole.UserRole)
            if wrapped:
                track = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else wrapped
                new_tracks.append(track)
                
        old_length = len(self.current_tracks)
        self.current_tracks = new_tracks
        
        # Sync to Server in the background so the UI doesn't freeze
        client = getattr(self.track_list, 'client', None)
        if client and getattr(self, 'current_playlist_id', None):
            track_ids = [t.get('id') for t in new_tracks if t.get('id')]
            import threading
            def save_order():
                try:
                    client.update_playlist_tracks(self.current_playlist_id, old_length, track_ids)
                except Exception as e:
                    print(f"Failed to save reordered playlist: {e}")
            threading.Thread(target=save_order, daemon=True).start()
    
    def filter_local_tracks(self, text):
        if not hasattr(self, 'current_tracks') or not self.current_tracks:
            return
            
        query = text.lower().strip()
        self.track_list.tree.setUpdatesEnabled(False)
        self.track_list.tree.clear()
        
        for i, t in enumerate(self.current_tracks):
            title = str(t.get('title', t.get('name', ''))).lower()
            artist = str(t.get('artist', '')).lower()
            album = str(t.get('album', '')).lower()
            
            # Show track if it matches, and preserve original track number (i + 1)
            if query in title or query in artist or query in album:
                if hasattr(self.track_list, 'create_track_item'):
                    item = self.track_list.create_track_item(t, i + 1)
                    self.track_list.tree.addTopLevelItem(item)
                    
        self.track_list.tree.setUpdatesEnabled(True)
        
        # Snap the height of the UI to fit the filtered results
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self.adjust_tree_height)
    
    def eventFilter(self, source, event):
        from PyQt6.QtCore import QEvent, Qt, QTimer
        
        if hasattr(self, 'track_list') and source is getattr(self.track_list, 'tree', None) and event.type() == QEvent.Type.KeyPress:
            
            # DETAIL VIEW LOCAL SEARCH
            if event.text() == "/":
                self.scroll_area.verticalScrollBar().setValue(0)
                if hasattr(self.track_list, 'search_container'):
                    self.track_list.search_container.show_search()
                    QTimer.singleShot(50, self.track_list.search_container.search_input.setFocus)
                return True 
            
            key = event.key()
            
            # DETAIL VIEW PLAY ALL (SHIFT+ENTER)
            if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.btn_play.animateClick()
                return True  
                
            # CAMERA TRACKING & EDGE SCROLLING
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                tree = self.track_list.tree
                old_item = tree.currentItem()
                old_idx = tree.indexOfTopLevelItem(old_item) if old_item else -1
                
                if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    jump_size = 12 
                    if key == Qt.Key.Key_PageDown:
                        target_idx = min(tree.topLevelItemCount() - 1, old_idx + jump_size)
                    else:
                        target_idx = max(0, old_idx - jump_size)
                        
                    if target_idx >= 0:
                        tree.setCurrentItem(tree.topLevelItem(target_idx))
                
                def check_scroll():
                    new_item = tree.currentItem()
                    new_idx = tree.indexOfTopLevelItem(new_item) if new_item else -1
                    
                    if old_idx == new_idx:
                        if key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp) and new_idx <= 0:
                            self.scroll_area.verticalScrollBar().setValue(0)
                            return
                        elif key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown) and new_idx >= tree.topLevelItemCount() - 1:
                            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
                            return
                    
                    if new_item:
                        rect = tree.visualItemRect(new_item)
                        pt = tree.viewport().mapTo(self.content_widget, rect.topLeft())
                        self.scroll_area.ensureVisible(pt.x(), pt.y() + rect.height() // 2, 0, 100)
                        
                if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    check_scroll()
                    return True
                    
                QTimer.singleShot(0, check_scroll)
                return False
                
        return super().eventFilter(source, event)
    
    def set_accent_color(self, color, alpha=0.3):
        self.current_accent = color
        self.setStyleSheet(f"#DetailBackground {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")
        
        play_btn_style = f"""
            QPushButton {{ background-color: {color}; border-radius: 30px; border: none; }} 
            QPushButton:hover {{ background-color: white; }}
        """
        self.btn_play.setStyleSheet(play_btn_style)
        
        scrollbar_style = f"""
            QScrollArea {{ background: transparent; border: none; }}
            QWidget#ScrollContent {{ background: transparent; }}
            QScrollBar:vertical {{ border: none; background: rgba(0, 0, 0, 0.05); width: 10px; margin: 0; }} 
            QScrollBar::handle:vertical {{ background: #333; min-height: 30px; border-radius: 5px; }} 
            QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {color}; }} 
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }} 
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }} 
            QScrollBar:horizontal {{ height: 0px; }}
        """
        self.scroll_area.setStyleSheet(scrollbar_style)
        self.track_list.set_accent_color(color, alpha)
        
        import os
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon
        from PyQt6.QtCore import QSize
        try:
            from albums_browser import resource_path
            icon_path = resource_path("img/shuffle.png")
            if os.path.exists(icon_path):
                pixmap = QPixmap(icon_path)
                colored = QPixmap(pixmap.size())
                colored.fill(QColor(0,0,0,0))
                painter = QPainter(colored)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.fillRect(colored.rect(), QColor(color))
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                painter.drawPixmap(0, 0, pixmap)
                painter.end()
                self.btn_shuffle.setIcon(QIcon(colored))
                self.btn_shuffle.setIconSize(QSize(22, 22))
        except: pass

    def adjust_tree_height(self):
        try:
            tree = self.track_list.tree
            count = tree.topLevelItemCount()
            if count == 0: return

            header_h = tree.header().height()
            rows_h = count * 75
            natural_h = header_h + rows_h + 10

            MAX_TREE_H = 800
            capped_h = min(natural_h, MAX_TREE_H)

            tree.setMinimumHeight(capped_h)
            tree.setMaximumHeight(natural_h)
            self.track_list.setMinimumHeight(capped_h + 10)
            self.track_list.setMaximumHeight(natural_h + 60)
        except Exception:
            pass

    def populate_view(self, playlist_data, tracks):
        
        self.current_playlist_id = playlist_data.get('id')
        self.current_tracks = tracks

        # Wire up the ghost drag signals safely
        main_win = self.window()
        if hasattr(main_win, 'show_ghost_drag') and not getattr(self, '_ghost_connected', False):
            self.drag_helper.sig_drag_started.connect(main_win.show_ghost_drag)
            self.drag_helper.sig_drag_moved.connect(main_win.move_ghost_drag)
            self.drag_helper.sig_drag_ended.connect(main_win.ghost_label.hide)
            self._ghost_connected = True

        title = playlist_data.get('title', playlist_data.get('name', 'Unknown Playlist'))
        self.lbl_title.setText(title)

        dur_raw = playlist_data.get('duration', 0)
        try: dur = int(dur_raw)
        except: dur = 0
        hrs = dur // 3600
        mins = (dur % 3600) // 60
        time_str = f"{hrs} hr {mins} min" if hrs > 0 else f"{mins} min"
        self.lbl_meta.setText(f"{len(tracks)} songs • {time_str}")
        self.lbl_artist.setText(playlist_data.get('owner', 'Various Artists'))

        # --- COVER ---
        # open_playlist_detail already set it from cache if available.
        # This is only a fallback for the rare case where the cover wasn't cached yet.
        cid = str(playlist_data.get('cover_id') or playlist_data.get('coverArt') or '')
        pending_cid = getattr(self, '_pending_cover_id', None)
        if cid and cid == pending_cid:
            cover_provider = getattr(self, '_cover_provider', None)
            cover_worker   = getattr(self, '_cover_worker', None)
            img_data = cover_provider.image_cache.get(cid) if cover_provider else None
            if img_data:
                pix = QPixmap()
                pix.loadFromData(img_data)
                if not pix.isNull():
                    self.cover_label.setPixmap(pix)
            elif cover_worker:
                def _on_cover(dl_id, dl_data, _cid=cid):
                    if str(dl_id) == _cid:
                        pix = QPixmap()
                        pix.loadFromData(dl_data)
                        if not pix.isNull():
                            self.cover_label.setPixmap(pix)
                        try:
                            cover_worker.cover_ready.disconnect(_on_cover)
                        except Exception:
                            pass
                cover_worker.cover_ready.connect(_on_cover)
                cover_worker.queue_cover(cid, priority=True)
        
           
        if hasattr(self.track_list, 'on_worker_finished'):
            self.track_list.on_worker_finished(tracks, len(tracks), 1, 1)

        if hasattr(self.track_list, 'search_container'):
            self.track_list.search_container.search_input.blockSignals(True)
            self.track_list.search_container.search_input.clear()
            self.track_list.search_container.search_input.blockSignals(False)
            if hasattr(self.track_list.search_container, 'collapse'):
                self.track_list.search_container.collapse()
            
        if hasattr(self.track_list, 'status_label'):
            self.track_list.status_label.hide()
            
        if hasattr(self.track_list, 'search_container'):
            if hasattr(self.track_list.search_container, 'hide_burger'):
                self.track_list.search_container.hide_burger()
            elif hasattr(self.track_list.search_container, 'burger_btn'):
                self.track_list.search_container.burger_btn.hide()
                
        self.track_list.tree.setSortingEnabled(False)
        self.track_list.tree.header().setSortIndicatorShown(False)
        self.track_list.tree.header().setSectionsClickable(False)
            
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self.adjust_tree_height)
            
        main_win = self.window()
        if hasattr(main_win, 'update_indicator'):
            main_win.update_indicator(scroll_to_current=False)
            
        
        self.track_list.tree.setFocus(Qt.FocusReason.OtherFocusReason)

class PlaylistsBrowser(QWidget):
    play_track_signal = pyqtSignal(dict)
    play_album_signal = pyqtSignal(list) 
    queue_track_signal = pyqtSignal(dict)
    play_next_signal = pyqtSignal(dict)
    switch_to_artist_tab = pyqtSignal(str)
    playlist_clicked = pyqtSignal(dict, object)
    album_clicked = pyqtSignal(dict)

    def __init__(self, client=None):
        super().__init__()
        self.client = client
        self.current_accent = "#0066cc"
        self.current_query = ""
        self.all_playlists = []

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("PlaylistsBrowser")
        self.setStyleSheet("#PlaylistsBrowser { background-color: rgba(12, 12, 12, 0.3); border-radius: 5px; }")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- 1. HEADER ---
        self.header_container = QWidget()
        self.header_container.setFixedHeight(50)
        self.header_container.setStyleSheet("QWidget { background-color: #111; border-top-left-radius: 5px; border-top-right-radius: 5px; border-bottom: 1px solid #222; }")
        
        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(15, 0, 10, 0) 
        header_layout.setSpacing(15)
        
        self.status_label = QLabel("0 Playlists")
        self.status_label.setStyleSheet("color: #888; font-weight: bold; background: transparent; border: none;")
        
        self.search_container = SmartSearchContainer(placeholder="Search playlists...")
        self.search_container.text_changed.connect(self.on_search_text_changed)
        
        self.burger_btn = self.search_container.get_burger_btn()
        self.burger_btn.setToolTip("Create New Playlist")
        
        
        try: self.burger_btn.clicked.disconnect()
        except: pass
        
        self.burger_btn.clicked.connect(self.on_add_playlist_clicked)
        
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()
        header_layout.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)

        self.main_layout.addWidget(self.header_container)
        
        # --- 2. MAIN CONTENT STACK ---
        self.stack = QStackedWidget()

        # QML Grid View
        self.qml_view = QMLGridWrapper()
        self.qml_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.qml_view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.qml_view.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.qml_view.setClearColor(Qt.GlobalColor.transparent)
        self.qml_view.setStyleSheet("background: transparent; border: none;")

        self.playlist_model = PlaylistModel()
        self.grid_bridge = PlaylistBridge(self.playlist_model)

        self.grid_bridge.itemClicked.connect(self.on_playlist_data_clicked)
        self.grid_bridge.playClicked.connect(self.on_playlist_play_clicked)
        self.grid_bridge.itemRightClicked.connect(self.show_item_context_menu)
        self.grid_bridge.backgroundRightClicked.connect(self.show_bg_context_menu)
        self.grid_bridge.keyTextForwarded.connect(self._on_key_forwarded)
        self.grid_bridge.slashPressed.connect(self._on_slash_pressed)

        ctx = self.qml_view.rootContext()
        ctx.setContextProperty("playlistModel", self.playlist_model)
        ctx.setContextProperty("playlistBridge", self.grid_bridge)

        engine = self.qml_view.engine()
        self.cover_provider = CoverImageProvider()
        engine.addImageProvider("plcovers", self.cover_provider)

        self.qml_view.setSource(QUrl.fromLocalFile(resource_path("playlist_grid.qml")))

        self.grid_view = self.qml_view  # alias so existing code stays compatible
        
        # Detail View
        self.detail_view = PlaylistDetailView()
        self.detail_view.play_clicked.connect(self.play_current_playlist)
        self.detail_view.shuffle_clicked.connect(self.shuffle_current_playlist)
        
        # Route TracksBrowser Signals!
        self.detail_view.track_list.play_track.connect(self.play_track_signal.emit)
        self.detail_view.track_list.queue_track.connect(self.queue_track_signal.emit)
        self.detail_view.track_list.play_next.connect(self.play_next_signal.emit)
        self.detail_view.track_list.play_multiple_tracks.connect(self.play_album_signal.emit)
        self.detail_view.track_list.switch_to_artist_tab.connect(self.switch_to_artist_tab.emit)
        
        self.stack.addWidget(self.grid_view)
        self.stack.addWidget(self.detail_view)
        self.main_layout.addWidget(self.stack)

        self.nav_history = []
        self.nav_index = -1
        self.current_album_id = None
        self.cover_worker = None
        self.add_to_history({'type': 'root'})
    
    def show_bg_context_menu(self):
        """Shows menu when clicking empty space in the grid."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #222; color: white; border: 1px solid #444; border-radius: 4px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #333; }
        """)
        
        add_action = menu.addAction("Add New Playlist")
        
        action = menu.exec(QCursor.pos())
        if action == add_action:
            self.on_add_playlist_clicked()

    def show_item_context_menu(self, idx):
        """Shows rename/delete menu when right-clicking a playlist."""
        if not (0 <= idx < len(self.playlist_model.playlists)): return
        playlist = self.playlist_model.playlists[idx]
        
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #222; color: white; border: 1px solid #444; border-radius: 4px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #333; }
        """)
        
        rename_action = menu.addAction("Rename Playlist")
        delete_action = menu.addAction("Delete Playlist")
        
        action = menu.exec(QCursor.pos())
        
        if action == rename_action:
            self.rename_playlist_dialog(playlist)
        elif action == delete_action:
            self.delete_playlist_dialog(playlist)

    def rename_playlist_dialog(self, playlist):
        from PyQt6.QtWidgets import QInputDialog, QMessageBox, QLineEdit
        
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Rename Playlist")
        dialog.setLabelText("Enter a new name:")
        
        # Pre-fill the current name
        current_name = playlist.get('name', playlist.get('title', ''))
        dialog.setTextValue(current_name)
        dialog.setTextEchoMode(QLineEdit.EchoMode.Normal)
        
        # Use your custom dark styling
        dialog.setStyleSheet("""
            QInputDialog, QDialog { background-color: #121212; }
            QLabel { color: white; font-size: 13px; font-weight: bold; }
            QLineEdit { background-color: #222; color: white; border: 1px solid #444; border-radius: 4px; padding: 5px; font-size: 13px; }
            QPushButton { background-color: #333; color: white; border: none; border-radius: 4px; padding: 6px 15px; font-weight: bold; }
            QPushButton:hover { background-color: #555; }
        """)
        
        if dialog.exec() == QInputDialog.DialogCode.Accepted:
            new_name = dialog.textValue().strip()
            if new_name and new_name != current_name:
                try:
                    self.client.rename_playlist(playlist.get('id'), new_name)
                    self.load_playlists()
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to rename: {e}")

    def delete_playlist_dialog(self, playlist):
        from PyQt6.QtWidgets import QMessageBox
        
        name = playlist.get('name', playlist.get('title', ''))
        
        # 1. Create the message box manually
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Playlist")
        msg_box.setText(f"Delete '{name}'?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        
        # 2. Apply the solid dark stylesheet
        dark_style = """
            QMessageBox, QDialog {
                background-color: #121212;
            }
            QLabel {
                color: white;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #333;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 15px;
                font-weight: bold;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """
        msg_box.setStyleSheet(dark_style)
        
        # 3. Execute and check the result
        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            try:
                self.client.delete_playlist(playlist.get('id'))
                self.load_playlists()
            except Exception as e:
                # Make sure the error popup is also styled nicely!
                err_box = QMessageBox(self)
                err_box.setWindowTitle("Error")
                err_box.setText(f"Failed to delete: {e}")
                err_box.setIcon(QMessageBox.Icon.Warning)
                err_box.setStyleSheet(dark_style)
                err_box.exec()
    
    def on_add_playlist_clicked(self):
        """Opens the custom frameless dialog to create a new playlist and refreshes the grid."""
        from components import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        import threading
        from PyQt6.QtCore import QMetaObject, Qt
        
        if not self.client: 
            return
        
        # Grab the active accent color to keep the styling perfectly synced
        accent = getattr(self, 'current_accent', "#1DB954")
        
        # Launch our new modern dialog
        dialog = NewPlaylistDialog(self.window(), accent_color=accent)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            if not name: 
                return
            
        
            def worker():
                try:
                    self.client.create_playlist(name)
                    
                    # Safely tell the main thread to refresh the grid using your existing method!
                    QMetaObject.invokeMethod(self, "load_playlists", Qt.ConnectionType.QueuedConnection)
                        
                except Exception as e:
                    print(f"Failed to create playlist: {e}")
                    
            threading.Thread(target=worker, daemon=True).start()
    
    def _on_key_forwarded(self, text):
        """QML forwards printable keystrokes to Spotlight."""
        main_win = self.window()
        if main_win and hasattr(main_win, 'keyPressEvent'):
            from PyQt6.QtGui import QKeyEvent
            from PyQt6.QtCore import QEvent
            fake = QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, text)
            main_win.keyPressEvent(fake)

    def _on_slash_pressed(self):
        """QML forwards '/' to open local search."""
        if hasattr(self, 'search_container'):
            self.search_container.show_search()
            QTimer.singleShot(50, self.search_container.search_input.setFocus)

    def eventFilter(self, source, event):
        return super().eventFilter(source, event)

    def add_to_history(self, state):
        if not hasattr(self, 'nav_history'):
            self.nav_history = []
            self.nav_index = -1
            
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_history = self.nav_history[:self.nav_index + 1]
        self.nav_history.append(state)
        self.nav_index += 1
        
        if len(self.nav_history) > 20:
            self.nav_history = self.nav_history[-20:]
            self.nav_index = len(self.nav_history) - 1
            
        self.render_state(state)

    def on_nav_back(self):
        if hasattr(self, 'nav_index') and self.nav_index > 0:
            self.nav_index -= 1
            self.render_state(self.nav_history[self.nav_index])

    def on_nav_fwd(self):
        if hasattr(self, 'nav_index') and self.nav_index < len(self.nav_history) - 1:
            self.nav_index += 1
            self.render_state(self.nav_history[self.nav_index])
       
    def set_client(self, client):
        self.client = client
        self.detail_view.track_list.client = client
        if not self.cover_worker:
            self.cover_worker = GridCoverWorker(client)
            self.cover_worker.cover_ready.connect(self.apply_cover)
            self.cover_worker.start()
        # Eagerly fetch playlists in the background so they're available for
        # the right-click context menu even before the Playlists tab is opened.
        if self.client:
            self.load_playlists()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, 'all_playlists', []) and self.client:
            self.load_playlists()
        else:
            self.qml_view.setFocus()
    
    @pyqtSlot()
    def load_playlists(self):
        if not self.client: return
        self.worker = PlaylistsWorker(self.client)
        self.worker.results_ready.connect(self.on_playlists_loaded)
        self.worker.start()

    def on_playlists_loaded(self, playlists):
        self.all_playlists = playlists
        self.refresh_grid()
    
    @pyqtSlot()
    def refresh_grid(self):
        query = self.current_query.lower()
        filtered = [p for p in getattr(self, 'all_playlists', []) if query in p.get('name', '').lower()]

        processed = []
        for p in filtered:
            p_data = p.copy()
            p_data['title']    = p.get('name', 'Unknown Playlist')
            p_data['subtitle'] = f"{p.get('songCount', 0)} tracks"
            p_data['cover_id'] = p.get('coverArt', '')
            processed.append(p_data)

        self.playlist_model.reset_data(processed)
        self.status_label.setText(f"{len(processed)} Playlists")

        if self.cover_worker:
            for p_data in processed:
                cid = p_data.get('cover_id')
                if cid:
                    self.cover_worker.queue_cover(cid, priority=False)

        search_has_focus = (hasattr(self, 'search_container') and
                            hasattr(self.search_container, 'search_input') and
                            self.search_container.search_input.hasFocus())
        if not search_has_focus and hasattr(self, 'qml_view') and self.isVisible():
            self.qml_view.setFocus()

    def start_play_fetch(self, data):
        self.instant_play_worker = PlaylistTracksWorker(self.client, data)
        self.instant_play_worker.results_ready.connect(self._on_instant_play_ready)
        self.instant_play_worker.start()

    def _on_instant_play_ready(self, playlist_data, tracks):
        if tracks and hasattr(self, 'play_album_signal'):
            self.play_album_signal.emit(tracks)

    def play_current_playlist(self):
        if hasattr(self.detail_view, 'current_tracks') and self.detail_view.current_tracks:
            self.play_album_signal.emit(self.detail_view.current_tracks)

    def shuffle_current_playlist(self):
        if hasattr(self.detail_view, 'current_tracks') and self.detail_view.current_tracks:
            import random
            tracks = list(self.detail_view.current_tracks)
            random.shuffle(tracks)
            self.play_album_signal.emit(tracks)

    def render_state(self, state):
        s_type = state.get('type')
        self.current_album_id = None # Tricks main.py into knowing we left detail view
        
        if s_type == 'root':
            main_win = self.window()
            if hasattr(main_win, 'update_indicator'):
                main_win.update_indicator()
            self.go_to_root(record_history=False)
        elif s_type == 'playlist':
            self.open_playlist_detail(state['data'], state.get('pixmap'), record_history=False)

    def on_playlist_data_clicked(self, data):
        """Called by bridge when user clicks a playlist card."""
        if not data: return
        
        
        pixmap = None
        cid = str(data.get('cover_id') or data.get('coverArt') or '')
        
        if cid and hasattr(self, 'cover_provider'):
            img_data = self.cover_provider.image_cache.get(cid)
            if img_data:
                from PyQt6.QtGui import QPixmap
                pix = QPixmap()
                pix.loadFromData(img_data)
                if not pix.isNull():
                    pixmap = pix
                    
        # Now we emit BOTH the data and the actual image!
        self.playlist_clicked.emit(data, pixmap)

    def on_playlist_play_clicked(self, data):
        """Called by bridge when user clicks the play button on a playlist card."""
        if not data: return
        self.start_play_fetch(data)

    def show_album_details(self, data, record_history=True):
        self.open_playlist_detail(data, None, record_history)

    def open_playlist_detail(self, data, pixmap, record_history=True):
        if record_history:
            self.add_to_history({'type': 'playlist', 'data': data, 'pixmap': pixmap})
            return

        self.current_album_id = data.get('id')

        # Image is already rendered in the grid — grab bytes from cache immediately
        cid = str(data.get('cover_id') or data.get('coverArt') or '')
        if cid and hasattr(self, 'cover_provider'):
            img_data = self.cover_provider.image_cache.get(cid)
            if img_data:
                pix = QPixmap()
                pix.loadFromData(img_data)
                if not pix.isNull():
                    self.detail_view.cover_label.setPixmap(pix)

        # Pass resources to detail view in case cover isn't cached yet (e.g. first open)
        self.detail_view._cover_provider = getattr(self, 'cover_provider', None)
        self.detail_view._cover_worker   = getattr(self, 'cover_worker', None)
        self.detail_view._pending_cover_id = cid

        if hasattr(self, 'main_layout'):
            for i in range(self.main_layout.count()):
                widget = self.main_layout.itemAt(i).widget()
                if widget and widget != getattr(self, 'stack', None):
                    widget.hide()

        self.stack.setCurrentIndex(1)
        self.detail_view.scroll_area.verticalScrollBar().setValue(0)

        if hasattr(self.detail_view.track_list, 'show_skeleton_loader'):
            self.detail_view.track_list.show_skeleton_loader(10)

        self.tracks_worker = PlaylistTracksWorker(self.client, data)
        self.tracks_worker.results_ready.connect(self.detail_view.populate_view)
        self.tracks_worker.start()

    def go_to_root(self, record_history=True):
        if record_history:
            self.add_to_history({'type': 'root'})
            return
            
        if hasattr(self, 'main_layout'):
            for i in range(self.main_layout.count()):
                widget = self.main_layout.itemAt(i).widget()
                if widget and widget != getattr(self, 'stack', None):
                    widget.show()
            
        if hasattr(self, 'search_container'):
            self.search_container.show_search()
            self.search_container.show_burger()
            
        self.stack.setCurrentIndex(0)

        if hasattr(self, 'qml_view') and self.isVisible():
            self.qml_view.setFocus()

        if getattr(self, 'current_query', ""):
            self.current_query = ""
            if hasattr(self, 'search_container'):
                self.search_container.search_input.blockSignals(True)
                self.search_container.search_input.clear()
                self.search_container.search_input.blockSignals(False)
        
        self.nav_history = [{'type': 'root'}]
        self.nav_index = 0
        self.current_album_id = None
        
        self.load_playlists()

    def on_search_text_changed(self, text):
        self.current_query = text.strip()
        self.refresh_grid()

    def apply_cover(self, cover_id, image_data):
        if hasattr(self, 'cover_provider'):
            self.cover_provider.image_cache[str(cover_id)] = image_data
        if hasattr(self, 'playlist_model'):
            self.playlist_model.update_cover(str(cover_id))

    def set_accent_color(self, color, alpha=0.3):
        self.current_accent = color
        self.setStyleSheet(f"#PlaylistsBrowser {{ background-color: rgba(12, 12, 12, {alpha}); border-radius: 5px; }}")
        if hasattr(self, 'grid_bridge'):
            self.grid_bridge.accentColorChanged.emit(color)
            self.grid_bridge.bgAlphaChanged.emit(alpha)
        self.detail_view.set_accent_color(color, alpha)
        if hasattr(self, 'search_container'): self.search_container.set_accent_color(color)
        if hasattr(self, 'burger_btn'):
            try:
                from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QPen
                from PyQt6.QtCore import Qt
                
                # Create a crisp 24x24 transparent canvas
                pixmap = QPixmap(24, 24)
                pixmap.fill(QColor(0, 0, 0, 0))
                
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                
                # Draw a clean, modern Plus sign matching the master color
                pen = QPen(QColor(color))
                pen.setWidth(3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                
                painter.drawLine(12, 4, 12, 20) # Vertical line
                painter.drawLine(4, 12, 20, 12) # Horizontal line
                painter.end()
                
                self.burger_btn.setIcon(QIcon(pixmap))
            except Exception as e: 
                print(f"Failed to draw plus icon: {e}")