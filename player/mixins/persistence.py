"""
player/mixins/persistence.py — Save/load playlist, server connection
bootstrap, RAM monitoring, and window lifecycle events.
"""
import os
import sys
import json
import keyring
import psutil
import gc

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt, QSettings, QProcess, QThread, pyqtSignal, QTimer

from player.workers import PlaylistLoaderWorker
from player.widgets import SettingsWindow

class PersistenceMixin:
    def save_playlist(self):
        try:
            self.settings.setValue('window_width',  str(self.width()))
            self.settings.setValue('window_height', str(self.height()))
            self.settings.setValue('current_playlist', json.dumps(self.playlist_data))
            self.settings.setValue('saved_current_index', str(self.current_index))
            self.settings.setValue('saved_position', str(self.seek_bar.position_ms))
            self.settings.setValue('last_master_color', self.master_color)
            self.settings.setValue('visual_settings', json.dumps(self.visual_settings))
            self.settings.setValue('waveform_mode', self.seek_bar.display_mode)
            
            
            if hasattr(self, '_splitter'):
                self.settings.setValue('splitter_state', self._splitter.saveState().toHex().data().decode())
            if getattr(self, 'static_bg_path', None):
                self.settings.setValue('static_bg_path', self.static_bg_path)
            else:
                self.settings.remove('static_bg_path')
            if hasattr(self, 'album_browser') and hasattr(self.album_browser, 'get_state'):
                self.settings.setValue('album_state', json.dumps(self.album_browser.get_state()))
            if hasattr(self, 'artist_browser') and hasattr(self.artist_browser, 'get_state'):
                self.settings.setValue('artist_state', json.dumps(self.artist_browser.get_state()))
                
        except Exception as e: print(f"Playlist Save Error: {e}")

    def load_playlist(self):
        try:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                available = screen.availableGeometry()
                saved_w = int(self.settings.value('window_width',  0) or 0)
                saved_h = int(self.settings.value('window_height', 0) or 0)
                if saved_w > 0 and saved_h > 0:
                    w = min(saved_w, available.width())
                    h = min(saved_h, available.height())
                    self.resize(w, h)
                else:
                    w = self.width()
                    h = self.height()
                x = available.x() + (available.width()  - w) // 2
                y = available.y() + (available.height() - h) // 2
                self.move(x, y)
        except Exception as e:
            print(f"Window size restore error: {e}")

        try:
            splitter_hex = self.settings.value('splitter_state')
            if splitter_hex and hasattr(self, '_splitter'):
                from PyQt6.QtCore import QByteArray, QTimer
                state = QByteArray.fromHex(splitter_hex.encode())
                QTimer.singleShot(0, lambda: self._splitter.restoreState(state))
            elif hasattr(self, '_splitter'):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._splitter.setSizes([569, 1056]))
        except Exception as e:
            print(f"Splitter restore error: {e}")

        try:
            saved_json = self.settings.value('current_playlist')
            
            
            if not saved_json: 
                self.load_random_startup_track()
                return
                
            saved_data = json.loads(saved_json)
            if not saved_data: 
                self.load_random_startup_track()
                return
            
            self.playlist_loader = PlaylistLoaderWorker(saved_data)
            self.playlist_loader.progress.connect(self.add_batch_to_ui)
            self.playlist_loader.finished.connect(self.on_playlist_load_finished)
            self.playlist_loader.start()
        except Exception as e: 
            print(f"Playlist Load Error: {e}")
            self.load_random_startup_track()

    def load_random_startup_track(self):
        """Fetches a random song using a proper Qt Background Thread."""
        if not hasattr(self, 'navidrome_client') or not self.navidrome_client: 
            return

        
        from PyQt6.QtCore import QThread, pyqtSignal
        
        class RandomTrackWorker(QThread):
            track_ready = pyqtSignal(dict)
            
            def __init__(self, client):
                super().__init__()
                self.client = client
                
            def run(self):
                try:
                    tracks = []
                    if hasattr(self.client, 'get_random_songs'):
                        try:
                            # Use count=1 for standard Subsonic APIs
                            tracks = self.client.get_random_songs(count=1)
                        except TypeError:
                            # Bulletproof fallback just in case
                            all_random = self.client.get_random_songs()
                            if all_random: tracks = [all_random[0]]
                            
                    if tracks and len(tracks) > 0:
                        self.track_ready.emit(tracks[0])
                except Exception as e:
                    print(f"Random track fetch failed: {e}")

        # Start the Qt thread and connect its signal to the UI function
        self._random_worker = RandomTrackWorker(self.navidrome_client)
        self._random_worker.track_ready.connect(self._apply_random_startup_track)
        self._random_worker.start()

    def _apply_random_startup_track(self, track):
        """Silently loads the UI for the track without touching C++ audio buffers."""
        # 1. Add it to the UI queue
        self.add_track_to_queue(track)
        self.current_index = 0
        
        # 2. Update text info (DO NOT CALL C++ BPM OR WAVEFORM HERE)
        if hasattr(self, 'load_current_track_metadata_text_only'):
            self.load_current_track_metadata_text_only()
        
        # 3. Highlight it in the queue and set window title
        self.update_indicator(scroll_to_current=True)
        self.update_window_title()
        
        # 4. Set waveform to an empty loading state
        if getattr(self.seek_bar, 'display_mode', 0) in (0, 2):
            self.seek_bar.reset_waveform()
                
        # 5. Trigger the heavy visual update (Background blur, cover art, master color)
        self.visual_update_timer.start(50)
    
    def on_playlist_load_finished(self):
        """Called automatically when the startup playlist finishes populating the UI."""
        self.update_window_title()
        try:
            saved_idx = int(self.settings.value('saved_current_index', -1))
            
            if 0 <= saved_idx < len(self.playlist_data):
                self.current_index = saved_idx
                
                # Update the UI text instantly! (Background was already handled in __init__)
                self.load_current_track_metadata_text_only()
                self.update_indicator(scroll_to_current=True)
                
                # Restore the seek bar position
                saved_pos = int(self.settings.value('saved_position', 0))
                if saved_pos > 0:
                    self.seek_bar.blockSignals(True)
                    self.seek_bar.setValue(saved_pos)
                    self.seek_bar.blockSignals(False)
                    self.current_time_label.setText(self.format_time(saved_pos))
                    
                    # Restore the Total Time label
                    track = self.playlist_data[saved_idx]
                    dur_str = track.get('duration', '0:00')
                    self.total_time_label.setText(dur_str)
                    
                    try:
                        parts = dur_str.split(':')
                        total_ms = (int(parts[0]) * 60 + int(parts[1])) * 1000
                        self.seek_bar.setMaximum(total_ms)
                    except: pass
        except Exception as e:
            print(f"Error restoring previous track state: {e}")

        if hasattr(self, '_queue_panel') and self._queue_panel.isVisible():
            self._refresh_queue_panel()

    def test_navidrome_fetch(self):
        client = self.navidrome_client

        if not client:
            print("[CRITICAL] No Navidrome client provided! The app requires an active connection.")
            return

        print("Initializing UI tabs with Navidrome connection...")

        if client.ping():
            print("Success! Live connected to Navidrome.")

            # Store client for lazy initialization of tabs not yet visited
            self._pending_client = client
            if not hasattr(self, '_initialized_tabs'):
                self._initialized_tabs = set()

            # --- 1. HOME TAB (visible on startup — initialize immediately) ---
            if hasattr(self, 'home_tab') and 'home' not in self._initialized_tabs:
                self._init_tab_home(client)

            # --- 2-5: Wire up signals only; data loading deferred until first visit ---
            if hasattr(self, 'album_browser'):
                try: self.album_browser.switch_to_artist_tab.disconnect()
                except: pass
                self.album_browser.switch_to_artist_tab.connect(self.navigate_to_artist)

            if hasattr(self, 'artist_browser'):
                try: self.artist_browser.switch_to_album_tab.disconnect()
                except: pass
                self.artist_browser.switch_to_album_tab.connect(self.navigate_to_album)

            # --- 6. SYNC & REFRESH ---
            self.playlist_cover_worker.client = client
            if self.playlist_cover_worker.queue and not self.playlist_cover_worker.isRunning():
                self.playlist_cover_worker.start()

        else:
            print("[ERROR] Connection to Navidrome failed. Check your server status.")

    # --- Per-tab initialization (called lazily on first visit) ---

    def _init_tab_home(self, client):
        self.home_tab.initialize(client)
        try: self.home_tab.album_clicked.disconnect()
        except: pass
        self.home_tab.album_clicked.connect(self.navigate_to_album)
        self._initialized_tabs.add('home')
        # Preload all other tabs in the background after home is ready
        QTimer.singleShot(1500, self._background_preload_tabs)

    def _background_preload_tabs(self):
        client = getattr(self, '_pending_client', None)
        if not client:
            return
        if 'albums' not in self._initialized_tabs:
            self._init_tab_albums(client)
        if 'artists' not in self._initialized_tabs:
            self._init_tab_artists(client)
        if 'tracks' not in self._initialized_tabs:
            self._init_tab_tracks(client)
        if 'playlists' not in self._initialized_tabs:
            self._init_tab_playlists(client)
        # Restore home focus after all background loading fires
        QTimer.singleShot(2000, self._restore_home_focus_if_active)

    def _restore_home_focus_if_active(self):
        if hasattr(self, 'home_tab') and self.tabs.currentWidget() is self.home_tab:
            self.home_tab.focus_first_grid()

    def _init_tab_albums(self, client):
        self.album_browser.set_client(client)
        try: self.album_browser.switch_to_artist_tab.disconnect()
        except: pass
        self.album_browser.switch_to_artist_tab.connect(self.navigate_to_artist)
        self._initialized_tabs.add('albums')

    def _init_tab_artists(self, client):
        self.artist_browser.client = client
        if not getattr(self.artist_browser, 'cover_worker', None):
            from albums_browser import GridCoverWorker
            self.artist_browser.cover_worker = GridCoverWorker(client)
            self.artist_browser.cover_worker.cover_ready.connect(self.artist_browser.apply_cover)
            self.artist_browser.cover_worker.start()
        else:
            self.artist_browser.cover_worker.client = client
        if hasattr(self.artist_browser, 'load_artists_page'):
            self.artist_browser.load_artists_page(reset=True)
        try: self.artist_browser.switch_to_album_tab.disconnect()
        except: pass
        self.artist_browser.switch_to_album_tab.connect(self.navigate_to_album)
        self._initialized_tabs.add('artists')

    def _init_tab_tracks(self, client):
        self.tracks_browser.client = client
        if hasattr(self.tracks_browser, '_start_worker'):
            self.tracks_browser._start_worker(is_album=False, album_id=None)
        self._initialized_tabs.add('tracks')

    def _init_tab_playlists(self, client):
        self.playlists_browser.set_client(client)
        self._initialized_tabs.add('playlists')

    def _ensure_tab_initialized(self, widget):
        """Lazily initialize a tab the first time it becomes visible."""
        client = getattr(self, '_pending_client', None)
        if not client:
            return
        if not hasattr(self, '_initialized_tabs'):
            self._initialized_tabs = set()

        if widget is getattr(self, 'album_browser', None) and 'albums' not in self._initialized_tabs:
            print("[LazyInit] Albums tab")
            self._init_tab_albums(client)
        elif widget is getattr(self, 'artist_browser', None) and 'artists' not in self._initialized_tabs:
            print("[LazyInit] Artists tab")
            self._init_tab_artists(client)
        elif widget is getattr(self, 'tracks_browser', None) and 'tracks' not in self._initialized_tabs:
            print("[LazyInit] Tracks tab")
            self._init_tab_tracks(client)
        elif widget is getattr(self, 'playlists_browser', None) and 'playlists' not in self._initialized_tabs:
            print("[LazyInit] Playlists tab")
            self._init_tab_playlists(client)
   
    def _safe_discard_worker(self, worker):
        """Parks workers in a graveyard so they don't crash the app on disposal."""
        if not worker: return
        
        # Disconnect signals so it doesn't trigger UI updates as a ghost
        try: worker.status_ready.disconnect()
        except: pass
        try: worker.finished.disconnect()
        except: pass
        
        if not hasattr(self, '_worker_graveyard'):
            self._worker_graveyard = set()
            
        self._worker_graveyard.add(worker)
        
        def remove_from_grave():
            if hasattr(self, '_worker_graveyard') and worker in self._worker_graveyard:
                self._worker_graveyard.remove(worker)
                
        try: worker.finished.connect(remove_from_grave)
        except: pass
    
    def print_ram_usage(self):
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / (1024 * 1024)
        # Only force a full GC cycle when memory is actually bloated (>400 MB).
        # Running gc.collect() every 5s on a large heap was causing periodic CPU spikes.
        if mem_mb > 400:
            gc.collect()
        print(f"[RAM Monitor] Active Memory: {mem_mb:.1f} MB")
    
    def open_settings(self): 
        self.swin = SettingsWindow(self)
        self.swin.show()
    
    def closeEvent(self, event):
        # 1. Stop the keyboard listener
        if hasattr(self, 'media_key_listener'):
            self.media_key_listener.stop_listener()
            
        # 2. Gracefully shut down the C++ Audio Manager
        if hasattr(self, 'playback_manager'):
            self.playback_manager.command_queue.put({'type': 'quit'})
            self.playback_manager.quit()
            self.playback_manager.wait()
            
        # 3. Stop all background cover art downloaders safely
        if hasattr(self, 'playlist_cover_worker') and self.playlist_cover_worker.isRunning():
            self.playlist_cover_worker.running = False
            self.playlist_cover_worker.quit()
            self.playlist_cover_worker.wait()
            
        if hasattr(self, 'album_browser') and getattr(self.album_browser, 'cover_worker', None):
            self.album_browser.cover_worker.stop()
            self.album_browser.cover_worker.quit()
            self.album_browser.cover_worker.wait()
            
        if hasattr(self, 'artist_browser') and getattr(self.artist_browser, 'cover_worker', None):
            self.artist_browser.cover_worker.stop()
            self.artist_browser.cover_worker.quit()
            self.artist_browser.cover_worker.wait()

        # 4. Stop remaining Python worker threads
        if hasattr(self, 'blur_thread') and self.blur_thread and self.blur_thread.isRunning(): 
            self.blur_thread.quit()
            self.blur_thread.wait()
            
        if hasattr(self, 'loading_thread') and self.loading_thread and self.loading_thread.isRunning(): 
            self.loading_thread.quit()
            self.loading_thread.wait()
        
        if hasattr(self, 'playlist_loader') and self.playlist_loader and self.playlist_loader.isRunning():
            self.playlist_loader.quit()
            self.playlist_loader.wait()
            
        # 5. Stop the active audio engine
        if hasattr(self, 'audio_engine'): 
            self.audio_engine.stop()
        
        # 6. Clean up temporary cache files
        for f in self.temp_files:
            try:
                if os.path.exists(f): os.unlink(f)
            except: pass
        
        # 7. Save user state (current song, index, color theme)
        # Skip if we're logging out — settings were already wiped intentionally
        if not getattr(self, '_logging_out', False):
            self.save_playlist()
        
                
        # Allow the window to close
        event.accept()

    def resizeEvent(self, event):
        # 1. Resize the background label containers (free geometry operation)
        if hasattr(self, 'bg_label_old'):
            self.bg_label_old.resize(self.size())
        if hasattr(self, 'bg_label'):
            self.bg_label.resize(self.size())

        # Actual SmoothTransformation pixel scaling is deferred 120 ms so it only fires
        # once when the user stops dragging — not on every pixel of resize.
        if hasattr(self, '_resize_debounce'):
            self._resize_debounce.start()
        
        # 2. Truncate artist text if window gets too small
        if hasattr(self, 'track_artist') and self.track_artist.property("full_text"): 
            self.set_elided_text(self.track_artist, self.track_artist.property("full_text"))
        
        # 3. Keep navigation buttons centered in the tab bar
        self.reposition_nav_buttons()
        
        # 4. Keep the sync overlay pinned to the bottom right corner
        if hasattr(self, 'sync_overlay') and self.sync_overlay.isVisible():
            self.sync_overlay.move(self.width() - self.sync_overlay.width() - 30, self.height() - self.sync_overlay.height() - 95)

        # 5. Keep the floating queue panel anchored above footer
        if hasattr(self, '_queue_panel') and self._queue_panel.isVisible():
            self._reposition_queue_panel()

        super().resizeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)

