"""
player/mixins/navigation.py — Tab navigation, back/forward history,
spotlight routing, and footer label click handlers.
"""
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QPushButton, QListWidget
from PyQt6.QtCore import Qt, QTimer, QItemSelectionModel

from player.workers import SyncCheckWorker

class NavigationMixin:
    def setup_global_navigation(self):
        self.nav_history = []
        self.nav_index = -1
        self.programmatic_nav = False
        
        if not hasattr(self, 'btn_back'):
            self.btn_back = QPushButton("<")
            self.btn_fwd = QPushButton(">")
            self.btn_back.clicked.connect(self.go_back)
            self.btn_fwd.clicked.connect(self.go_forward)

        self.nav_history.append({'tab': 0, 'view': 'home', 'data': None})
        self.nav_index = 0
        self.update_nav_buttons()
 
    def add_global_nav(self, tab_index, view_type, data=None):
        """
        Records a navigation step.
        tab_index: 0=Home, 1=Playing, 2=Albums, 3=Artists
        view_type: 'home', 'album_grid', 'album_detail', 'artist_grid', 'artist_detail'
        data: The dict required to render the detail view (artist/album obj)
        """
        if self.programmatic_nav: return

        # If we are in the middle of history and move, cut off the future
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_history = self.nav_history[:self.nav_index + 1]

        new_state = {'tab': tab_index, 'view': view_type, 'data': data}

        # Prevent duplicates (clicking the same thing twice)
        if self.nav_history and self.nav_history[-1] == new_state:
            return

        self.nav_history.append(new_state)
        self.nav_index += 1
        # Cap history to avoid unbounded memory growth
        if len(self.nav_history) > 30:
            self.nav_history = self.nav_history[-30:]
            self.nav_index = len(self.nav_history) - 1
        self.update_nav_buttons()

    def go_back(self):
        if self.nav_index > 0:
            self.nav_index -= 1
            self.restore_global_state(self.nav_history[self.nav_index])
            self.update_nav_buttons()

    def go_forward(self):
        if self.nav_index < len(self.nav_history) - 1:
            self.nav_index += 1
            self.restore_global_state(self.nav_history[self.nav_index])
            self.update_nav_buttons()

    def update_nav_buttons(self):
        if hasattr(self, 'btn_back'):
            self.btn_back.setEnabled(self.nav_index > 0)
        if hasattr(self, 'btn_fwd'):
            self.btn_fwd.setEnabled(self.nav_index < len(self.nav_history) - 1)

    def reposition_nav_buttons(self):
        pass 

    def restore_global_state(self, state):
        """Forces the UI to match the history state (Used by Back/Forward buttons)."""
        self.programmatic_nav = True # LOCK history recording
        try:
            target_tab = state['tab']
            view = state['view']
            data = state['data']

            # 1. Switch to the correct main tab
            self.tabs.setCurrentIndex(target_tab)

            # 2. Configure the view inside that tab
            if view == 'home':
                pass 
            elif view == 'album_grid':
                
                
                if hasattr(self, 'album_browser'): 
                    self.album_browser.stack.setCurrentIndex(0)
                    
            elif view == 'artist_grid':
                
                if hasattr(self, 'artist_browser'): 
                    self.artist_browser.stack.setCurrentIndex(0)
                    
            elif view == 'tracks_list':
                pass
            
            elif view == 'playlists_browser':
                if hasattr(self, 'playlists_browser'): 
                    self.playlists_browser.go_to_root()
                
            elif view in ('album', 'album_detail'):
                if hasattr(self, 'global_album_view') and data:
                    current_id = getattr(self.global_album_view, 'current_album_id', None)
                    target_id = data if isinstance(data, str) else data.get('id')
                    
                    if current_id != target_id:
                        if isinstance(data, str): self.global_album_view.load_album({'id': data})
                        else: self.global_album_view.load_album(data)
                        
            elif view in ('artist', 'artist_detail'):
                if hasattr(self, 'global_artist_view') and data:
                    current_id = getattr(self.global_artist_view, 'current_artist_id', None)
                    target_id = data if isinstance(data, str) else data.get('id')
                    
                    if current_id != target_id:
                        if isinstance(data, str): self.global_artist_view.load_artist({'id': None, 'name': data})
                        else: self.global_artist_view.load_artist(data)

            elif view == 'playlist_detail':
                if hasattr(self, 'global_playlist_view') and data:
                    
                    
                    self.global_playlist_view.client = getattr(self, 'navidrome_client', None)
                    self.global_playlist_view.track_list.client = getattr(self, 'navidrome_client', None)
                    
                    current_id = getattr(self.global_playlist_view, 'current_playlist_id', None)
                    target_id = data.get('id') if isinstance(data, dict) else None
                    if current_id != target_id:
                        if hasattr(self.global_playlist_view.track_list, 'show_skeleton_loader'):
                            self.global_playlist_view.track_list.show_skeleton_loader(10)
                        from playlists_browser import PlaylistTracksWorker
                        self._pl_worker = PlaylistTracksWorker(self.navidrome_client, data)
                        self._pl_worker.results_ready.connect(self.global_playlist_view.populate_view)
                        self._pl_worker.start()

        finally:
            self.programmatic_nav = False

    def on_tab_changed_global(self, index):
        #Handles user clicking the tabs manually and manages search bar state."""
        widget = self.tabs.widget(index)

        # Lazy-initialize tab on first visit (runs regardless of programmatic_nav)
        if hasattr(self, '_ensure_tab_initialized'):
            self._ensure_tab_initialized(widget)

        if self.programmatic_nav: return

        if widget is self.home_tab:                view_type = 'home'
        elif widget is self._now_playing_panel:    view_type = 'now_playing'
        elif widget is self.album_browser:         view_type = 'album_grid'
        elif widget is self.artist_browser:        view_type = 'artist_grid'
        elif widget is self.tracks_browser:        view_type = 'tracks_list'
        elif widget is self.playlists_browser:     view_type = 'playlists_browser'
        else:                                      view_type = 'unknown'

        if widget is self.tracks_browser:
            tree = self.tracks_browser.tree
            tree.setFocus(Qt.FocusReason.OtherFocusReason)
            if tree.topLevelItemCount() > 0 and not tree.currentItem():
                tree.setCurrentItem(
                    tree.topLevelItem(0), 0,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
                )
        elif widget is self.album_browser:
            self.album_browser.grid_view.setFocus(Qt.FocusReason.OtherFocusReason)
        elif widget is self.artist_browser:
            self.artist_browser.grid_view.setFocus(Qt.FocusReason.OtherFocusReason)
        elif widget is self.home_tab:
            self.home_tab.focus_first_grid()

        self.add_global_nav(index, view_type)    

    def on_tab_bar_clicked(self, index):
        is_already_active = (self.tabs.currentIndex() == index)
        
        if not is_already_active:
            return  
            
        if hasattr(self, 'navidrome_client') and self.navidrome_client:
            
            
            if QApplication.overrideCursor() is None:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            
            if getattr(self, '_sync_checker', None) and self._sync_checker.isRunning():
                self._safe_discard_worker(self._sync_checker)
                
            self._sync_checker = SyncCheckWorker(self.navidrome_client, index)
            self._sync_checker.status_ready.connect(self._on_sync_check_result)
            self._sync_checker.start()
        
        widget = self.tabs.widget(index)

        # 1. HOME TAB REFRESH
        if widget is self.home_tab:
            if hasattr(self.home_tab, 'scroll_area'):
                self.home_tab.scroll_area.verticalScrollBar().setValue(0)

        # 2. ALBUMS TAB REFRESH
        elif widget is self.album_browser and hasattr(self.album_browser, 'go_to_root'):
            self.album_browser.go_to_root()

        # 3. ARTISTS TAB REFRESH
        elif widget is self.artist_browser and hasattr(self.artist_browser, 'go_to_root'):
            self.artist_browser.go_to_root()

        # 4. PLAYLISTS TAB REFRESH
        elif widget is self.playlists_browser and hasattr(self.playlists_browser, 'go_to_root'):
            self.playlists_browser.go_to_root()

    def _on_tab_moved(self, from_idx, to_idx):
        """Keep hidden utility tabs pinned at the end and strictly invisible."""
        if self._tab_move_in_progress:
            return
        self._tab_move_in_progress = True
        try:
            hidden_widgets = [self.global_album_view, self.global_artist_view, self.global_playlist_view]
            total = self.tabs.count()
            
            for w in hidden_widgets:
                idx = self.tabs.indexOf(w)
                
                # 1. If a hidden tab got mixed into the visible ones, push it to the back
                if idx < (total - len(hidden_widgets)):
                    self.tabs.tabBar().moveTab(idx, total - 1)
                
                
                new_idx = self.tabs.indexOf(w)
                self.tabs.tabBar().setTabVisible(new_idx, False)

            # 3. Update the stored index variables
            self.global_album_tab_idx    = self.tabs.indexOf(self.global_album_view)
            self.global_artist_tab_idx   = self.tabs.indexOf(self.global_artist_view)
            self.global_playlist_tab_idx = self.tabs.indexOf(self.global_playlist_view)
            
            self._save_tab_order()
        finally:
            self._tab_move_in_progress = False

    def _save_tab_order(self):
        from PyQt6.QtCore import QSettings
        settings = QSettings("Navidrome", "Player")
        visible_widgets = [self.home_tab, self._now_playing_panel, self.album_browser,
                           self.artist_browser, self.tracks_browser, self.playlists_browser]
        order = [self.tabs.indexOf(w) for w in visible_widgets]
        settings.setValue('tab_order', order)

    def _restore_tab_order(self):
        from PyQt6.QtCore import QSettings
        settings = QSettings("Navidrome", "Player")
        saved = settings.value('tab_order')
        if not saved or len(saved) != 6: return
        try:
            self._tab_move_in_progress = True
            visible_widgets = [self.home_tab, self._now_playing_panel, self.album_browser,
                               self.artist_browser, self.tracks_browser, self.playlists_browser]
            for desired_pos, widget in sorted(zip(saved, visible_widgets), key=lambda x: int(x[0])):
                current_pos = self.tabs.indexOf(widget)
                if current_pos != int(desired_pos):
                    self.tabs.tabBar().moveTab(current_pos, int(desired_pos))
            self.global_album_tab_idx    = self.tabs.indexOf(self.global_album_view)
            self.global_artist_tab_idx   = self.tabs.indexOf(self.global_artist_view)
            self.global_playlist_tab_idx = self.tabs.indexOf(self.global_playlist_view)
        except Exception as e:
            print(f"[TabRestore] Could not restore tab order: {e}")
        finally:
            self._tab_move_in_progress = False

    def navigate_to_album(self, album_data):
        self.add_global_nav(self.global_album_tab_idx, 'album', album_data)
        
        
        self.programmatic_nav = True
        self.tabs.setCurrentIndex(self.global_album_tab_idx)
        self.programmatic_nav = False
        
        # 2. Feed it the database client dynamically
        self.global_album_view.client = getattr(self, 'navidrome_client', None)
        self.global_album_view.track_list.client = getattr(self, 'navidrome_client', None)
        
        # 3. Load the data
        if isinstance(album_data, str):
             self.global_album_view.load_album({'id': album_data})
        else:
             self.global_album_view.load_album(album_data)
             
        if hasattr(self, 'master_color'):
            self.global_album_view.set_accent_color(self.master_color, self.visual_settings.get('bg_alpha', 0.3))

    def navigate_to_artist(self, artist_data):
        self.add_global_nav(self.global_artist_tab_idx, 'artist', artist_data)
        
        
        self.programmatic_nav = True
        self.tabs.setCurrentIndex(self.global_artist_tab_idx)
        self.programmatic_nav = False
        
        # 2. Feed it the database client dynamically
        self.global_artist_view.client = getattr(self, 'navidrome_client', None)

        # Apply an instant header image before load_artist() wipes it.
        # Priority 1: pixmap stashed by the artist grid on item click (exact artist image).
        # Priority 2: current playing track cover — a reasonable placeholder for navigations
        #             from now-playing, album view, track list etc. where no artist image
        #             is pre-loaded. Replaced by the real artist image once the worker finishes.
        pending_pixmap = getattr(self.artist_browser, '_pending_artist_pixmap', None)
        if pending_pixmap and not pending_pixmap.isNull():
            # Exact image stashed from a grid item click
            self.global_artist_view._header_already_loaded = True
            self.global_artist_view._exact_artist_image = getattr(self.artist_browser, '_pending_artist_pixmap_exact', False)
            self.global_artist_view.set_header_image(pending_pixmap)
            self.artist_browser._pending_artist_pixmap = None
            self.artist_browser._pending_artist_pixmap_exact = False
        else:
            # Not a direct grid click — try two sources in order:
            # 1. A loaded (non-placeholder) grid item matching the artist name
            # 2. CoverCache keyed by artist id (covers virtualized/off-screen items)
            target_name = (artist_data if isinstance(artist_data, str)
                           else (artist_data.get('name') or '')).lower().strip()
            artist_id = None if isinstance(artist_data, str) else artist_data.get('id')
            found_px = None

            grid = self.artist_browser.grid_view
            for i in range(grid.count()):
                item = grid.item(i)
                data = item.data(Qt.ItemDataRole.UserRole) if item else None
                if not data or data.get('type') == 'placeholder':
                    continue
                item_name = (data.get('name') or data.get('artist') or '').lower().strip()
                if item_name == target_name:
                    icon = item.icon()
                    if icon and not icon.isNull():
                        px = icon.pixmap(220, 220)
                        if not px.isNull():
                            found_px = px
                    break

            # Grid item was a placeholder or off-screen — try the cache directly
            if not found_px and artist_id and getattr(self.artist_browser, 'cover_worker', None):
                try:
                    cache = self.artist_browser.cover_worker._cache
                    cid = str(artist_id)
                    raw = (cache.get_thumb(cid)
                           or cache.get_thumb(f'ar-{cid}')
                           or (cache.get_thumb(cid[3:]) if cid.startswith('ar-') else None))
                    if raw:
                        from PyQt6.QtGui import QPixmap
                        px = QPixmap()
                        px.loadFromData(raw)
                        if not px.isNull():
                            found_px = px
                except Exception:
                    pass

            if found_px:
                self.global_artist_view._header_already_loaded = True
                self.global_artist_view._exact_artist_image = True
                self.global_artist_view.set_header_image(found_px)

        # 3. Load the data!
        if isinstance(artist_data, str):
            self.global_artist_view.load_artist({'id': None, 'name': artist_data})
        else:
            self.global_artist_view.load_artist(artist_data)
            
        if hasattr(self, 'master_color'):
            self.global_artist_view.set_accent_color(self.master_color, self.visual_settings.get('bg_alpha', 0.3))
        
    def navigate_to_playlist(self, playlist_data, pixmap=None):
        self.add_global_nav(self.global_playlist_tab_idx, 'playlist_detail', playlist_data)
        
        self.programmatic_nav = True
        self.tabs.setCurrentIndex(self.global_playlist_tab_idx)
        self.programmatic_nav = False
        
        # Give the playlist tab access to the server so it can save!
        self.global_playlist_view.client = getattr(self, 'navidrome_client', None)
        self.global_playlist_view.track_list.client = getattr(self, 'navidrome_client', None)
        
        self.global_playlist_view.track_list.current_playlist_id = playlist_data.get('id')
        
        if pixmap:
            self.global_playlist_view.cover_label.setPixmap(pixmap)
            
        if hasattr(self.global_playlist_view.track_list, 'show_skeleton_loader'):
            self.global_playlist_view.track_list.show_skeleton_loader(10)
            
        from playlists_browser import PlaylistTracksWorker
        self._pl_worker = PlaylistTracksWorker(self.navidrome_client, playlist_data)
        self._pl_worker.results_ready.connect(self.global_playlist_view.populate_view)
        self._pl_worker.start()
        
        if hasattr(self, 'master_color'):
            self.global_playlist_view.set_accent_color(self.master_color, self.visual_settings.get('bg_alpha', 0.3))

    def on_switch_to_artist(self, artist_name):
        """Switches to the Artist tab and loads the requested artist."""
        print(f"Jump to artist: {artist_name}")
        

        self.tabs.setCurrentIndex(self.tabs.indexOf(self.artist_browser))
        
        if hasattr(self, 'artist_browser'):
            self.artist_browser.on_artist_name_clicked(artist_name)

    def on_switch_to_album(self, album_data):
        """Switches to Album tab and opens the specific album."""
        print(f"Switching to album: {album_data.get('title')}")
        # Find the index of the album browser by object reference
        index = self.tabs.indexOf(self.album_browser)
        if index != -1:
            self.programmatic_tab_change = True
            self.tabs.setCurrentIndex(index)
            self.programmatic_tab_change = False
            
        self.album_browser.show_album_details(album_data)
        
    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_duration(track_data: dict):
        """Convert a raw numeric duration (seconds) to 'm:ss' in-place."""
        dur = track_data.get('duration', 0)
        if isinstance(dur, (int, float)):
            m, s = divmod(int(dur), 60)
            track_data['duration'] = f"{m}:{s:02d}"

    def cycle_tab_forward(self):
        
        if hasattr(self, 'spotlight') and self.spotlight.isVisible(): return 
        
        count = self.tabs.count()
        if count > 0:
            next_idx = (self.tabs.currentIndex() + 1) % count
            # Keep skipping until we find a tab that is actually visible!
            while not self.tabs.tabBar().isTabVisible(next_idx):
                next_idx = (next_idx + 1) % count
                
            self.tabs.setCurrentIndex(next_idx)

    def cycle_tab_backward(self):
        
        if hasattr(self, 'spotlight') and self.spotlight.isVisible(): return 
        
        count = self.tabs.count()
        if count > 0:
            prev_idx = (self.tabs.currentIndex() - 1) % count
            # Keep skipping backwards until we find a tab that is actually visible!
            while not self.tabs.tabBar().isTabVisible(prev_idx):
                prev_idx = (prev_idx - 1) % count
                
            self.tabs.setCurrentIndex(prev_idx)
    
    def handle_spotlight_view(self, data):
        """Routes Spotlight's 'Enter View' requests to the correct page."""
        item_type = data.get('type')
        
        if item_type == 'album':
            
            if hasattr(self, 'navigate_to_album'):
                self.navigate_to_album(data)
                
        elif item_type == 'artist':
            
            if hasattr(self, 'navigate_to_artist'):
                self.navigate_to_artist(data)
    
    def focus_spotlight(self):
        """Summons the search overlay via Ctrl+F."""
        if hasattr(self, 'spotlight') and not self.spotlight.isVisible():
            self.spotlight.show_search()
            
            self.spotlight.raise_()
            self.spotlight.activateWindow()

    def focus_local_search(self):
        """Finds the local search bar, visually expands it if collapsed, and focuses it."""
        current_widget = self.tabs.currentWidget()
        if not current_widget: return

        # --- 0. AlbumDetailView IS the tab widget (global_album_view / global_artist detail) ---
        # When navigating to an album, the app switches to a hidden tab whose root widget IS an
        # AlbumDetailView. It has no search_container itself — the search bar lives inside
        # track_list (a TracksBrowser). The QShortcut fires before the AlbumDetailView's own
        # eventFilter sees the key, so we must handle it here.
        track_list = getattr(current_widget, 'track_list', None)
        if track_list is not None:
            container = getattr(track_list, 'search_container', None)
            if container and hasattr(container, 'search_input'):
                # 1. Make the container widget itself visible
                container.show()
                # 2. Expand the search input — same logic as section 1 below:
                #    if maximumWidth==0 the bar is visually collapsed, toggle_search opens it;
                #    otherwise it's already open and we just need to focus it.
                if container.search_input.maximumWidth() == 0:
                    if hasattr(container, 'toggle_search'):
                        container.toggle_search()
                # 3. Scroll so the search bar comes into view (AlbumDetailView has scroll_area)
                scroll_area = getattr(current_widget, 'scroll_area', None)
                if scroll_area:
                    QTimer.singleShot(30, lambda: scroll_area.ensureWidgetVisible(container))
                # 4. Focus the input after the expand animation settles
                def _focus_album_search():
                    container.search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
                    if container.search_input.text() == "/":
                        container.search_input.clear()
                    else:
                        container.search_input.selectAll()
                QTimer.singleShot(55, _focus_album_search)
                return

        # --- 1. SMART SEARCH CONTAINER ---
        if hasattr(current_widget, 'search_container'):
            container = current_widget.search_container
            
            if container.isHidden():
                container.show()
                
            if hasattr(container, 'search_input'):
                # 🟢 1. Use maximumWidth instead of width to bypass layout bugs!
                if container.search_input.maximumWidth() == 0:
                    if hasattr(container, 'toggle_search'):
                        container.toggle_search()
                else:
                    container.search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
                    
                # 🟢 2. If the slash key leaked into the input box, wipe it instantly!
                if container.search_input.text() == "/":
                    container.search_input.clear()
                else:
                    container.search_input.selectAll()
                    
        # --- 2. FALLBACK ---
        else:
            from PyQt6.QtWidgets import QLineEdit
            search_bars = current_widget.findChildren(QLineEdit)
            if search_bars:
                local_bar = search_bars[0]
                local_bar.setFocus()
                if local_bar.text() == "/":
                    local_bar.clear()
                else:
                    local_bar.selectAll()
    
    def route_to_album_from_spotlight(self, album_id):
        # We simulate an album_data dictionary so show_album_details accepts it
        fake_album_data = {'id': album_id}
        self.album_browser.show_album_details(fake_album_data)
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.album_browser)) # Switch to Albums tab

    def route_to_artist_from_spotlight(self, artist_name):
        # Emit the existing signal to trigger your standard artist navigation
        self.album_browser.switch_to_artist_tab.emit(artist_name)
      
    def on_footer_artist_click(self, artist_name):
        """Navigate to the specific artist clicked in the footer."""
        print(f"Footer clicked: {artist_name}")
        self.navigate_to_artist(artist_name)

    def on_footer_album_click(self):
        """Navigate to Album when footer text is clicked."""
        if self.current_index == -1: return
        track = self.playlist_data[self.current_index]
        
        album_artist = track.get('album_artist') or track.get('albumArtist') or track.get('artist', 'Unknown')
        
        album_data = {
            'id': track.get('albumId') or track.get('album_id'),
            'title': track.get('album', 'Unknown'),
            'artist': album_artist,
            'cover_id': track.get('cover_id')
        }
        
        if album_data['id']:
            self.navigate_to_album(album_data)

    def on_footer_title_click(self):
        """Navigate to Now Playing when footer title is clicked."""
        self.tabs.setCurrentIndex(self.tabs.indexOf(self._now_playing_panel))
        if self.current_index != -1:
            item = self.tree.topLevelItem(self.current_index)
            if item:
                self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def on_tab_changed(self, new_index):
        
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, lambda: self.auto_focus_current_tab())

    def auto_focus_current_tab(self, retries=5):
        """Dynamically routes keyboard focus and WAITS for data to load if empty!"""
        idx = self.tabs.currentIndex()
        current_widget = self.tabs.currentWidget()

        def focus_tree(tree_widget):
            if tree_widget.topLevelItemCount() == 0: return False
            tree_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
            if not tree_widget.currentItem():
                from PyQt6.QtCore import QItemSelectionModel
                first_item = tree_widget.topLevelItem(0)
                tree_widget.setCurrentItem(first_item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
            return True

        def focus_grid(grid_widget):
            if grid_widget.count() == 0: return False
            grid_widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
            if not grid_widget.currentItem():
                from PyQt6.QtCore import QItemSelectionModel
                grid_widget.setCurrentItem(grid_widget.item(0), QItemSelectionModel.SelectionFlag.ClearAndSelect)
            return True

        success = False
        
        # 1. Now Playing Queue
        if idx == 1: 
            success = focus_tree(self.tree)
        # 2. Master Tracks Browser
        elif idx == 4 and hasattr(current_widget, 'tree'): 
            success = focus_tree(current_widget.tree)
        # 3. Albums / Artists Browsers (🟢 NEW: Stack-Aware routing!)
        elif hasattr(current_widget, 'stack'): 
            stack_idx = current_widget.stack.currentIndex()
            if stack_idx == 0 and hasattr(current_widget, 'grid_view'):
                success = focus_grid(current_widget.grid_view)
            elif stack_idx == 1 and hasattr(current_widget, 'detail_view'):
                success = focus_tree(current_widget.detail_view.track_list.tree)
            elif stack_idx == 2 and hasattr(current_widget, 'artist_view'):
                for i in range(current_widget.artist_view.sections_layout.count()):
                    row = current_widget.artist_view.sections_layout.itemAt(i).widget()
                    if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                        success = focus_grid(row.list_widget)
                        break
        else:
            success = True

        if not success and retries > 0:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self.auto_focus_current_tab(retries - 1))
    
    def _on_sync_check_result(self, current_server_timestamp, tab_index):
        
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

        last_stamp = getattr(self, '_last_server_timestamp', 0)
        widget = self.tabs.widget(tab_index)

        if current_server_timestamp > last_stamp and current_server_timestamp > 0:
            print(f"[SYNC] Server changed! Old: {last_stamp} -> New: {current_server_timestamp}. Hard Refreshing...")
            self._last_server_timestamp = current_server_timestamp

            if widget is self.home_tab:
                self.home_tab.initialize(self.navidrome_client)
                try: self.home_tab.album_clicked.disconnect()
                except: pass
                self.home_tab.album_clicked.connect(self.navigate_to_album)

            elif widget is self.album_browser:
                self.album_browser.go_to_root()
                if hasattr(self.album_browser, 'refresh_grid'):
                    self.album_browser.refresh_grid()

            elif widget is self.artist_browser:
                self.artist_browser.go_to_root()
                if hasattr(self.artist_browser, 'refresh_grid'):
                    self.artist_browser.refresh_grid()

        else:
            print(f"[SYNC] No changes detected (Stamp: {current_server_timestamp}). Smooth scrolling to top instead!")
            if widget is self.album_browser and hasattr(self.album_browser, 'grid_view'):
                self.album_browser.go_to_root()
                self.album_browser.grid_view.verticalScrollBar().setValue(0)
            elif widget is self.artist_browser and hasattr(self.artist_browser, 'grid_view'):
                self.artist_browser.go_to_root()
                self.artist_browser.grid_view.verticalScrollBar().setValue(0)
            elif widget is self.home_tab and hasattr(self.home_tab, 'scroll_area'):
                self.home_tab.scroll_area.verticalScrollBar().setValue(0)
        
    def toggle_global_fav(self, is_liked):
        if hasattr(self.global_album_view, 'current_album_id') and self.global_album_view.current_album_id:
            if hasattr(self, 'navidrome_client') and self.navidrome_client:
                self.navidrome_client.set_favorite(self.global_album_view.current_album_id, is_liked)


    def play_album_from_detail_view(self):
        """Plays the entire album when Shift+Enter is pressed, from Detail View OR Grid View."""
        
        # --- 1. ALBUM DETAIL VIEW ---
        if 0 <= self.nav_index < len(self.nav_history):
            current_state = self.nav_history[self.nav_index]
            if current_state['view'] == 'album_detail' and current_state['data']:
                self.play_whole_album(current_state['data'])
                return
                
        # --- 2. ALBUM/ARTIST GRID VIEW ---
        current_widget = self.tabs.currentWidget()
        
        # Check if we are in a tab with a grid (stack index 0 = grid view)
        if hasattr(current_widget, 'stack') and current_widget.stack.currentIndex() == 0:
            if hasattr(current_widget, 'grid_view'):
                
                # Get the currently highlighted item
                curr_item = current_widget.grid_view.currentItem()
                if curr_item:
                    # Extract the dictionary data and send it to the player
                    data = curr_item.data(Qt.ItemDataRole.UserRole)
                    if data:
                        self.play_whole_album(data)
    