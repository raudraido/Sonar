"""
now_playing.py
──────────────
PlaylistTree   – QTreeWidget with 10 columns + drag-drop reordering.
NowPlayingPanel – Wrapper that adds a header bar with:
                    • status label   ("N tracks" / "N / M when filtered")
                    • SmartSearchContainer  (local row-hide filter, 250 ms debounce)
                    • burger menu    (column visibility, state persisted to DB)
                  Delegates are imported directly from tracks_browser so the
                  Now Playing tab renders identically to the Tracks browser.
"""

import json

from PyQt6.QtWidgets import (
    QTreeWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QMenu, QAbstractItemView, QHeaderView, QApplication, QFrame
)

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QRect, QPoint, QTimer, QSettings

from PyQt6.QtGui   import (
    QPainter, QColor, QDrag, QPixmap, QCursor, QIcon, QAction,
)

# ── Reuse every delegate from TracksBrowser ──────────────────────────────────
from tracks_browser import (
    CombinedTrackDelegate,
    MultiLinkArtistDelegate,
    LinkDelegate,
    MultiGenreDelegate,
    NoFocusDelegate,
)

# ── Column index constants (imported by main.py too) ─────────────────────────
COL_NUM    = 0
COL_TRACK  = 1   # CombinedTrackDelegate: cover + title + artist
COL_TITLE  = 2
COL_ARTIST = 3
COL_ALBUM  = 4
COL_YEAR   = 5
COL_GENRE  = 6
COL_FAV    = 7
COL_PLAYS  = 8
COL_LENGTH = 9
NUM_COLS   = 10

HEADERS = ["#", "TRACK", "TITLE", "ARTIST", "ALBUM", "YEAR", "GENRE", "♥", "PLAYS", "LENGTH"]

# Default hidden (only TRACK, ALBUM, ♥, LENGTH are shown by default)
DEFAULT_HIDDEN = {COL_TITLE, COL_ARTIST, COL_YEAR, COL_GENRE, COL_PLAYS}


# ─────────────────────────────────────────────────────────────────────────────
#  PlaylistTree  –  10-column QTreeWidget with drag-drop reordering
# ─────────────────────────────────────────────────────────────────────────────

class PlaylistTree(QTreeWidget):
    orderChanged     = pyqtSignal()
    sig_drag_started = pyqtSignal(QPixmap, QPoint)
    sig_drag_moved   = pyqtSignal(QPoint)
    sig_drag_ended   = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setColumnCount(NUM_COLS)
        self.setHeaderLabels(HEADERS)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRootIsDecorated(False)
        self.setFrameShape(QFrame.Shape.NoFrame)

        # Header alignment hints
        self.headerItem().setTextAlignment(COL_NUM,    Qt.AlignmentFlag.AlignCenter)
        self.headerItem().setTextAlignment(COL_FAV,    Qt.AlignmentFlag.AlignCenter)
        self.headerItem().setTextAlignment(COL_LENGTH, Qt.AlignmentFlag.AlignCenter)

        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(COL_NUM,    QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_TRACK,  QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_TITLE,  QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_ARTIST, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_ALBUM,  QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_YEAR,   QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_GENRE,  QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_FAV,    QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_PLAYS,  QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_LENGTH, QHeaderView.ResizeMode.Fixed)

        self.setColumnWidth(COL_NUM,    50)
        self.setColumnWidth(COL_TITLE,  200)
        self.setColumnWidth(COL_ARTIST, 200)
        self.setColumnWidth(COL_YEAR,   70)
        self.setColumnWidth(COL_GENRE,  120)
        self.setColumnWidth(COL_FAV,    50)
        self.setColumnWidth(COL_PLAYS,  70)
        self.setColumnWidth(COL_LENGTH, 75)

        # Drag-drop (internal move for playlist reordering)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

        # Context menu for "Add to Playlist"
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._emit_context_menu)

    def _emit_context_menu(self, pos):
        # Walk up to NowPlayingPanel which has access to main_window / client
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, NowPlayingPanel):
                parent._show_track_context_menu(self.mapToGlobal(pos))
                return
            parent = parent.parent()

    # ── Drag-drop ─────────────────────────────────────────────────────────────


    def mousePressEvent(self, event):
        self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        item_rect    = self.visualItemRect(items[0])
        full_row_rect = QRect(0, item_rect.y(), self.viewport().width(), item_rect.height())
        captured     = self.viewport().grab(full_row_rect)

        final = QPixmap(captured.size())
        final.fill(Qt.GlobalColor.transparent)
        p = QPainter(final)
        p.setOpacity(0.8)
        p.fillRect(final.rect(), QColor(25, 25, 25, 255))
        p.drawPixmap(0, 0, captured)
        p.end()

        row_tl  = self.viewport().mapTo(self, full_row_rect.topLeft())
        click   = self._drag_start_pos if hasattr(self, '_drag_start_pos') else self.mapFromGlobal(QCursor.pos())
        self._hot_spot_vector = click - row_tl
        global_start = self.mapToGlobal(row_tl)

        self.sig_drag_started.emit(final, global_start)
        QApplication.processEvents()

        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        drag.setHotSpot(self._hot_spot_vector)
        drag.exec(supportedActions)
        self.sig_drag_ended.emit()

    def dragMoveEvent(self, event):
        pos    = event.position().toPoint()
        target = pos - getattr(self, '_hot_spot_vector', QPoint(0,0))
        self.sig_drag_moved.emit(self.mapToGlobal(target))
        super().dragMoveEvent(event)
        
        
        from PyQt6.QtWidgets import QAbstractItemView
        if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem:
            event.ignore()

    def dropEvent(self, event):
      
        if event.source() is self:
            drop_target = self.itemAt(event.position().toPoint())
            drop_index = self.indexOfTopLevelItem(drop_target) if drop_target else self.topLevelItemCount()
            
            from PyQt6.QtWidgets import QAbstractItemView
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
                        drop_index -= 1
                    taken = self.takeTopLevelItem(idx)
                    self.insertTopLevelItem(drop_index, taken)
                    taken.setSelected(True)
                    drop_index += 1
                    
            event.accept()
            self.orderChanged.emit()
        else:
            super().dropEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  NowPlayingPanel  –  PlaylistTree + search bar + column toggle header
# ─────────────────────────────────────────────────────────────────────────────

class NowPlayingPanel(QWidget):
    """
    Drop-in replacement for the raw PlaylistTree in the Now Playing tab.
    Exposes self.tree for all existing main.py self.tree.* references.
    Forwards artist_clicked / album_clicked so main.py can connect them
    exactly the same way it connected the old playlist_delegate.* signals.
    """
    artist_clicked = pyqtSignal(str)
    album_clicked  = pyqtSignal(dict)

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window
        self._current_query  = ""
        self._current_accent = "#cccccc"
        self._current_alpha  = 0.3

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("NowPlayingPanel")
        self.setStyleSheet(
            "#NowPlayingPanel { background-color: rgba(12,12,12,0.3); border-radius: 5px; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(50)
        header.setStyleSheet(
            "QWidget { background-color: #111; border-top-left-radius: 5px; "
            "border-top-right-radius: 5px; border-bottom: 1px solid #222; }"
        )
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(15, 0, 10, 0)
        hbox.setSpacing(15)

        self.status_label = QLabel("0 tracks")
        self.status_label.setStyleSheet(
            "color: #888; font-weight: bold; background: transparent; border: none;"
        )
        hbox.addWidget(self.status_label)
        hbox.addStretch()

        self.search_container = None
        self.burger_btn       = None
        try:
            from components import SmartSearchContainer
            self.search_container = SmartSearchContainer(placeholder="Search queue…")
            self.search_container.text_changed.connect(self._on_search_changed)
            if (hasattr(self.search_container, 'search_input') and
                    hasattr(self.search_container.search_input, 'focus_lost')):
                self.search_container.search_input.focus_lost.connect(
                    lambda: QTimer.singleShot(
                        50, lambda: self.tree.setFocus(Qt.FocusReason.OtherFocusReason)
                    )
                )
            self.burger_btn = self.search_container.get_burger_btn()
            self.burger_btn.clicked.connect(self._show_column_menu)
            hbox.addWidget(self.search_container, 0, Qt.AlignmentFlag.AlignRight)
        except Exception as e:
            print(f"[NowPlayingPanel] SmartSearchContainer unavailable: {e}")

        root.addWidget(header)

        # ── Debounce timer ────────────────────────────────────────────────────
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._apply_filter)

        # ── PlaylistTree ──────────────────────────────────────────────────────
        self.tree = PlaylistTree()
        root.addWidget(self.tree)

        # 5) Attach delegates
        self.combined_delegate = CombinedTrackDelegate(self.tree)
        self.combined_delegate.is_album_mode = True
        self.combined_delegate.max_title_lines = 1 
        
        
        self.combined_delegate.artist_clicked.connect(self.artist_clicked.emit) 
        self.tree.setItemDelegateForColumn(COL_TRACK, self.combined_delegate)

        self.artist_delegate = MultiLinkArtistDelegate(self.tree)
        
        
        self.artist_delegate.artist_clicked.connect(self.artist_clicked.emit)
        self.tree.setItemDelegateForColumn(COL_ARTIST, self.artist_delegate)

        self.album_delegate = LinkDelegate(self.tree)
        self.album_delegate.max_lines = 1 
        
        
        self.album_delegate.clicked.connect(self._on_album_link_clicked) 
        self.tree.setItemDelegateForColumn(COL_ALBUM, self.album_delegate)

        self.genre_delegate = MultiGenreDelegate(self.tree)
        self.genre_delegate.max_lines = 1
        self.tree.setItemDelegateForColumn(COL_GENRE, self.genre_delegate)

    # ── Public API ─────────────────────────────────────────────────────────────

    @pyqtSlot()
    def update_status(self):
        total = self.tree.topLevelItemCount()
        if self._current_query:
            visible = sum(
                1 for i in range(total) if not self.tree.topLevelItem(i).isHidden()
            )
            self.status_label.setText(f"{visible} / {total} tracks")
        else:
            self.status_label.setText(f"{total} tracks")

    def clear_filter(self):
        if self.search_container:
            self.search_container.blockSignals(True)
            try:
                if hasattr(self.search_container, 'search_input'):
                    self.search_container.search_input.setText("")
            finally:
                self.search_container.blockSignals(False)
        self._current_query = ""
        self._show_all_rows()
        self.update_status()

    def set_accent_color(self, color: str, alpha: float = 0.3):
        self._current_accent = color
        self._current_alpha  = alpha

        # Tree stylesheet (matches TracksBrowser style)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background: rgba(12, 12, 12, {alpha});
                font-size: 10pt; 
                border: none; border-radius: 5px; color: #ddd; outline: none;
            }}
            QTreeWidget::item {{
                height: 50px; background: transparent;
                border-bottom: 1px solid rgba(255,255,255,0.02); color: #ddd;
            }}
            QTreeWidget::item:selected {{ background: rgba(255,255,255,0.1); color: {color}; }}
            QTreeWidget::item:hover    {{ background: rgba(255,255,255,0.05); color: {color}; }}
            QHeaderView::section {{
                background: transparent; color: #888; border: none;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                padding: 5px; font-weight: bold; text-transform: uppercase; font-size: 11px;
            }}
            QScrollBar:vertical {{
                border: none; background: rgba(0,0,0,0.05); width: 10px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #333; min-height: 30px; border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:vertical:pressed {{ background: {color}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical {{ background: none; }}
        """)

        # Tint burger icon
        if self.burger_btn is not None:
            try:
                from albums_browser import resource_path
                pix = QPixmap(resource_path("img/burger.png"))
                if not pix.isNull():
                    tinted = QPixmap(pix.size())
                    tinted.fill(Qt.GlobalColor.transparent)
                    p = QPainter(tinted)
                    p.drawPixmap(0, 0, pix)
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    p.fillRect(tinted.rect(), QColor(color))
                    p.end()
                    self.burger_btn.setIcon(QIcon(tinted))
            except Exception:
                pass

        # Forward to search container and delegates
        if self.search_container and hasattr(self.search_container, 'set_accent_color'):
            self.search_container.set_accent_color(color)
        for d in (self.combined_delegate, self.artist_delegate,
                  self.album_delegate, self.genre_delegate):
            if hasattr(d, 'set_master_color'):
                d.set_master_color(color)

    def load_column_state(self):
        try:
            settings = QSettings("Sonar", "Sonar")
            raw = settings.value('now_playing_columns_hidden')
            if raw:
                state = json.loads(raw)
                for col_str, hidden in state.items():
                    col = int(col_str)
                    if col < NUM_COLS:
                        self.tree.setColumnHidden(col, hidden)
                return
        except Exception: pass
        for col in DEFAULT_HIDDEN:
            self.tree.setColumnHidden(col, True)

    def save_column_state(self):
        state = {str(i): self.tree.isColumnHidden(i) for i in range(NUM_COLS)}
        try:
            settings = QSettings("Sonar", "Sonar")
            settings.setValue('now_playing_columns_hidden', json.dumps(state))
        except Exception: pass

    # ── Private helpers ────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        self._current_query = text.strip().lower()
        self._search_timer.start()

    def _apply_filter(self):
        q = self._current_query
        for i in range(self.tree.topLevelItemCount()):
            item    = self.tree.topLevelItem(i)
            if not q:
                item.setHidden(False)
                continue
            wrapped = item.data(0, Qt.ItemDataRole.UserRole)
            track   = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else {}
            title   = (track.get('title',  '') or '').lower()
            album   = (track.get('album',  '') or '').lower()
            artist  = (track.get('artist', '') or '').lower()
            item.setHidden(q not in title and q not in album and q not in artist)
        self.update_status()

    def _show_all_rows(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setHidden(False)

    def _show_column_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #222; color: #ddd; border: 1px solid #444; } "
            "QMenu::item { padding: 6px 25px; } "
            "QMenu::item:selected { background-color: #333; }"
        )
        for i, name in enumerate(HEADERS):
            if i == COL_TRACK:   # TRACK column is always visible
                continue
            action = QAction(name, menu)
            action.setCheckable(True)
            action.setChecked(not self.tree.isColumnHidden(i))
            action.triggered.connect(lambda checked, col=i: self._toggle_column(col, checked))
            menu.addAction(action)
        if self.burger_btn:
            menu.exec(self.burger_btn.mapToGlobal(QPoint(0, self.burger_btn.height())))

    def _toggle_column(self, col: int, is_checked: bool):
        self.tree.setColumnHidden(col, not is_checked)
        self.save_column_state()

    def _on_album_link_clicked(self, model_index):
        item = self.tree.topLevelItem(model_index.row())
        if not item:
            return
        wrapped = item.data(0, Qt.ItemDataRole.UserRole)
        track   = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else {}
        album_data = {
            'id':       track.get('albumId') or track.get('album_id'),
            'title':    track.get('album'),
            'artist':   track.get('albumArtist') or track.get('artist'),
            'coverArt': track.get('cover_id') or track.get('coverArt') or track.get('albumId'),
        }
        if album_data['id']:
            self.album_clicked.emit(album_data)

    def _on_item_clicked(self, item, column):
        if column == COL_FAV:
            self._toggle_favorite(item)

    def _show_track_context_menu(self, global_pos):
        """Right-click context menu on a Now Playing row.
        Reads playlists from the already-loaded PlaylistsBrowser cache — instant, no network."""
        selected = self.tree.selectedItems()
        if not selected:
            return

        client = None
        if self.main_window and hasattr(self.main_window, 'navidrome_client'):
            client = self.main_window.navidrome_client
        if not client:
            return

        # Collect track IDs for all selected rows
        track_ids = []
        track_names = []
        tracks = []
        for item in selected:
            wrapped = item.data(0, Qt.ItemDataRole.UserRole)
            track = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else {}
            tid = track.get('id')
            if tid:
                track_ids.append(str(tid))
                track_names.append(track.get('title', 'Unknown'))
                tracks.append(track)

        if not track_ids:
            return

        # Read cached playlists — already fetched by PlaylistsBrowser, zero latency
        playlists = []
        if self.main_window and hasattr(self.main_window, 'playlists_browser'):
            playlists = self.main_window.playlists_browser.all_playlists or []

        self._build_and_show_menu(global_pos, client, track_ids, track_names, playlists, tracks)

    def _build_and_show_menu(self, global_pos, client, track_ids, track_names, playlists, tracks=None):
        """Builds and shows the context menu. Always called on the main thread."""
        from components import TrackInfoDialog
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #1e1e1e; color: #ddd; border: 1px solid #444;"
            "        border-radius: 6px; padding: 4px; }"
            "QMenu::item { padding: 7px 28px 7px 14px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #333; color: #fff; }"
            "QMenu::item:disabled { color: #555; }"
            "QMenu::separator { height: 1px; background: #333; margin: 4px 8px; }"
        )

        label = track_names[0] if len(track_names) == 1 else f"{len(track_names)} tracks"
        header_action = QAction(f"  {label}", menu)
        header_action.setEnabled(False)
        menu.addAction(header_action)
        menu.addSeparator()

        # Remove from Now Playing queue
        remove_action = QAction("Remove from Now Playing", menu)
        remove_action.triggered.connect(
            lambda: self.main_window.delete_selected_tracks()
            if self.main_window and hasattr(self.main_window, 'delete_selected_tracks')
            else None
        )
        menu.addAction(remove_action)
        menu.addSeparator()

        add_menu = QMenu("Add to Playlist", menu)
        add_menu.setStyleSheet(menu.styleSheet())

        new_pl_action = QAction("+ New Playlist...", add_menu)
        new_pl_action.triggered.connect(lambda: self._add_to_new_playlist(client, track_ids))
        add_menu.addAction(new_pl_action)

        if playlists:
            add_menu.addSeparator()
            for pl in playlists:
                pl_name = pl.get('name', 'Unnamed')
                pl_id   = pl.get('id')
                if not pl_id:
                    continue
                count      = pl.get('songCount', '')
                label_text = f"{pl_name}  ({count})" if count != '' else pl_name
                action = QAction(label_text, add_menu)
                action.triggered.connect(
                    lambda checked, pid=pl_id, pname=pl_name:
                        self._add_to_existing_playlist(client, pid, pname, track_ids)
                )
                add_menu.addAction(action)

        menu.addMenu(add_menu)

        if tracks and len(tracks) == 1:
            menu.addSeparator()
            info_action = QAction("Get Info", menu)
            first_track = tracks[0]
            accent = getattr(self, '_current_accent', '#1DB954')
            mw = self.main_window
            album_data = {
                'id': first_track.get('albumId'),
                'title': first_track.get('album', ''),
                'artist': first_track.get('artist', ''),
                'coverArt': first_track.get('cover_id'),
            }
            def _open_info():
                TrackInfoDialog(
                    first_track, client=client, accent_color=accent, parent=self,
                    on_artist_click=lambda name: mw.navigate_to_artist(name) if mw and hasattr(mw, 'navigate_to_artist') else None,
                    on_album_click=lambda _: mw.navigate_to_album(album_data) if mw and hasattr(mw, 'navigate_to_album') else None,
                ).exec()
            info_action.triggered.connect(_open_info)
            menu.addAction(info_action)

        menu.exec(global_pos)

    def _add_to_existing_playlist(self, client, playlist_id, playlist_name, track_ids):
        """Appends tracks to an existing playlist; shows brief feedback in the status label."""
        import threading

        def worker():
            try:
                nav_client = self.main_window.navidrome_client
                nav_client.add_tracks_to_playlist(playlist_id, track_ids)
                msg = f"Added {len(track_ids)} tracks to playlist"
            except Exception as e:
                msg = f"Failed: {e}"
                
            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            import time

            # 1. Flash the success message
            QMetaObject.invokeMethod(
                self.status_label, "setText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg)
            )
            
            # 2. Pause the background thread for 3 seconds
            time.sleep(3)
            
            # 3. Ask the panel to recalculate its status text
            QMetaObject.invokeMethod(self, "update_status", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=worker, daemon=True).start()

    def _add_to_new_playlist(self, client, track_ids):
        """Prompts for a name using the custom dialog, creates the playlist, then appends the tracks."""
        from components import NewPlaylistDialog
        from PyQt6.QtWidgets import QDialog
        import threading
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG, QTimer

        # Grab the active accent color to keep styling perfectly synced
        accent = getattr(self, '_current_accent', "#1DB954")
        
        # Launch our new modern dialog
        dialog = NewPlaylistDialog(self.window(), accent_color=accent)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.get_name()
            is_public = dialog.is_public()
            if not name: return
            
            def worker():
                try:
                    
                    nav_client = self.main_window.navidrome_client
                    new_id = nav_client.create_playlist(name, public=is_public)
                    
                    if new_id:
                        nav_client.add_tracks_to_playlist(new_id, track_ids)
                        msg = f"Added {len(track_ids)} tracks to new playlist"
                    else:
                        msg = "Failed to create playlist"
                        
                except Exception as e:
                    msg = f"Failed: {e}"
                    
                from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
                import time

                # 1. Safely flash the success message
                QMetaObject.invokeMethod(
                    self.status_label, "setText",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, msg)
                )
                
                # 2. Pause the background thread for 3 seconds
                time.sleep(3)
                
                # 3. Safely call the panel's built-in update_status method to recalculate the text!
                QMetaObject.invokeMethod(self, "update_status", Qt.ConnectionType.QueuedConnection)

            threading.Thread(target=worker, daemon=True).start()

    def _toggle_favorite(self, item):
        wrapped = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(wrapped, dict):
            return
        track = wrapped.get('data', wrapped)
        
        # BOOLEAN
        raw_state = track.get('starred')
        if isinstance(raw_state, str): current_state = raw_state.lower() in ('true', '1')
        else: current_state = bool(raw_state)
        new_state = not current_state
        
        track['starred'] = new_state
        item.setText(COL_FAV, "♥" if new_state else "♡")
        
        # MASTER COLOR
        item.setForeground(COL_FAV, QColor("#E91E63") if new_state else QColor("#555555"))
        
        # SYNC WITH MAIN.PY PLAYLIST DATA
        if self.main_window:
            idx = self.tree.indexOfTopLevelItem(item)
            if 0 <= idx < len(self.main_window.playlist_data):
                self.main_window.playlist_data[idx]['starred'] = new_state
                

            print(f"[NowPlayingPanel] favorite update failed: {e}")
            
        if self.main_window and hasattr(self.main_window, 'navidrome_client'):
            client = self.main_window.navidrome_client
            if client:
                try: client.set_favorite(track.get('id'), new_state)
                except Exception: pass