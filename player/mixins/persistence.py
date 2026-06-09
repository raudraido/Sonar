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
from player.widgets import SettingsWindow, _DimOverlay

class PersistenceMixin:
    @staticmethod
    def _serializable_track(track: dict) -> dict:
        _ok = (str, int, float, bool, type(None))
        return {k: v for k, v in track.items() if isinstance(v, _ok)}

    def save_playlist(self):
        try:
            self.settings.setValue('window_width',  str(self.width()))
            self.settings.setValue('window_height', str(self.height()))
            safe = [self._serializable_track(t) for t in self.playlist_data]
            self.settings.setValue('current_playlist', json.dumps(safe))
            self.settings.setValue('saved_current_index', str(self.current_index))
            self.settings.setValue('saved_position', str(self.seek_bar.position_ms))
            self.settings.setValue('theme', self.theme.to_json())
            self.settings.setValue('waveform_mode', self.seek_bar.display_mode)
            if getattr(self, 'visualizer', None):
                self.settings.setValue('vis_mode', self.visualizer.vis_mode)
            
            
            if hasattr(self, 'album_browser') and hasattr(self.album_browser, 'get_state'):
                self.settings.setValue('album_state', json.dumps(self.album_browser.get_state()))
            if hasattr(self, 'artist_browser') and hasattr(self.artist_browser, 'get_state'):
                self.settings.setValue('artist_state', json.dumps(self.artist_browser.get_state()))
                
        except Exception as e: print(f"Playlist Save Error: {e}")

    def load_playlist(self):


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
            saved_idx = int(float(self.settings.value('saved_current_index', -1)))
            
            if 0 <= saved_idx < len(self.playlist_data):
                self.current_index = saved_idx
                
                # Update the UI text instantly! (Background was already handled in __init__)
                self.load_current_track_metadata_text_only()
                self.update_indicator(scroll_to_current=True)
                
                # Restore the seek bar position
                saved_pos = int(float(self.settings.value('saved_position', 0)))
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

        self._refresh_queue_panel()

    def test_navidrome_fetch(self):
        client = self.navidrome_client
        if not client:
            print("[CRITICAL] No Navidrome client provided!")
            return

        class _PingThread(QThread):
            done = pyqtSignal(bool)
            def __init__(self, c): super().__init__(); self._c = c
            def run(self):
                ok = self._c.ping()
                if ok:
                    if hasattr(self._c, 'warm_artist_name_cache'):
                        self._c.warm_artist_name_cache()
                    if hasattr(self._c, 'authenticate_native'):
                        self._c.authenticate_native()
                self.done.emit(ok)

        self._ping_thread = _PingThread(client)
        self._ping_thread.done.connect(lambda ok: self._on_connection_result(ok, client))
        self._ping_thread.start()

    def _on_connection_result(self, ok: bool, client):
        if not ok:
            from PyQt6.QtWidgets import QMessageBox
            msg = QMessageBox(self)
            msg.setWindowTitle("Connection Failed")
            msg.setText("Could not reach the Navidrome server.\nCheck your connection or server status.")
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            return

        print("Success! Live connected to Navidrome.")
        self._pending_client = client
        if not hasattr(self, '_initialized_tabs'):
            self._initialized_tabs = set()

        if hasattr(self, 'home_tab') and 'home' not in self._initialized_tabs:
            self._init_tab_home(client)

        if hasattr(self, 'album_browser'):
            try: self.album_browser.switch_to_artist_tab.disconnect()
            except: pass
            self.album_browser.switch_to_artist_tab.connect(self.navigate_to_artist)

        if hasattr(self, 'artist_browser'):
            try: self.artist_browser.switch_to_album_tab.disconnect()
            except: pass
            self.artist_browser.switch_to_album_tab.connect(self.navigate_to_album)

        self.playlist_cover_worker.client = client
        if self.playlist_cover_worker.queue and not self.playlist_cover_worker.isRunning():
            self.playlist_cover_worker.start()

    # --- Per-tab initialization (called lazily on first visit) ---

    def _early_home_init(self):
        """Start home tab loading immediately at startup without waiting for ping."""
        client = self.navidrome_client
        if not client or not hasattr(self, 'home_tab'):
            return
        if not hasattr(self, '_initialized_tabs'):
            self._initialized_tabs = set()
        if 'home' not in self._initialized_tabs:
            self._init_tab_home(client)

    def _init_tab_home(self, client):
        self.home_tab.initialize(client)
        try: self.home_tab.album_clicked.disconnect()
        except: pass
        self.home_tab.album_clicked.connect(self.navigate_to_album)
        self._initialized_tabs.add('home')
        # Preload all other tabs in the background after home is ready
        QTimer.singleShot(1500, self._background_preload_tabs)

    def _background_preload_tabs(self):
        # Tabs initialize lazily on first visit via _ensure_tab_initialized.
        # Exception: playlists browser must be initialized for "Add to Playlist"
        # context menus to work regardless of which tab is active.
        client = getattr(self, '_pending_client', None)
        if client and hasattr(self, 'playlists_browser') and 'playlists' not in self._initialized_tabs:
            self._init_tab_playlists(client)
        QTimer.singleShot(0, self._restore_home_focus_if_active)

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
        # Restore cached count+chunk so first visit is instant (same as albums)
        _sort = getattr(self.artist_browser, 'current_sort', 'alphabetical')
        _cached_count = client.stale_cache_get('artists_count')
        _cached_chunk = client.stale_cache_get(f'artists_chunk_0_{_sort}')
        if _cached_count and isinstance(_cached_count, int) and _cached_count > 0:
            self.artist_browser.true_server_count = _cached_count
        self.artist_browser._pending_cached_chunk = _cached_chunk or None
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

        # Show loading state immediately (same frame as click), then init next frame
        if widget is getattr(self, 'album_browser', None) and 'albums' not in self._initialized_tabs:
            self._initialized_tabs.add('albums')
            if hasattr(self.album_browser, 'show_loading'):
                self.album_browser.show_loading()
            def _do_init_albums():
                self.album_browser.set_client(client)
                try: self.album_browser.switch_to_artist_tab.disconnect()
                except: pass
                self.album_browser.switch_to_artist_tab.connect(self.navigate_to_artist)
                self._initialized_tabs.add('albums')
            QTimer.singleShot(50, _do_init_albums)
        elif widget is getattr(self, 'artist_browser', None) and 'artists' not in self._initialized_tabs:
            self._initialized_tabs.add('artists')
            if hasattr(self.artist_browser, 'show_loading'):
                self.artist_browser.show_loading()
            def _do_init_artists():
                self._init_tab_artists(client)
            QTimer.singleShot(16, _do_init_artists)
        elif widget is getattr(self, 'tracks_browser', None) and 'tracks' not in self._initialized_tabs:
            self._initialized_tabs.add('tracks')
            QTimer.singleShot(0, lambda: self._init_tab_tracks(client))
        elif widget is getattr(self, 'playlists_browser', None) and 'playlists' not in self._initialized_tabs:
            self._initialized_tabs.add('playlists')
            QTimer.singleShot(0, lambda: self._init_tab_playlists(client))
   
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
    
    def _dim_overlay(self) -> _DimOverlay:
        if not hasattr(self, '_dim_ov') or self._dim_ov is None:
            self._dim_ov = _DimOverlay(self)
        return self._dim_ov

    def show_dim(self):
        self._dim_overlay()._refit()
        self._dim_overlay().fade_in()

    def hide_dim(self):
        self._dim_overlay().fade_out()

    def open_settings(self):
        self.show_dim()
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

        # Stop artist detail workers (LiveArtistDetailWorker instances do blocking
        # network calls; without terminating them the process hangs after window close)
        for _av in ('global_artist_view',):
            _view = getattr(self, _av, None)
            if _view and hasattr(_view, 'stop_all_workers'):
                _view.stop_all_workers()
        _ab = getattr(self, 'artist_browser', None)
        _inner = getattr(_ab, 'artist_view', None)
        if _inner and hasattr(_inner, 'stop_all_workers'):
            _inner.stop_all_workers()

        # Stop album browser workers (chunk loaders, live search, compilations, graveyard)
        _alb = getattr(self, 'album_browser', None)
        if _alb and hasattr(_alb, 'stop_all_workers'):
            _alb.stop_all_workers()

        # Stop cover workers on tabs that don't have a stop_all_workers
        for _tab_attr, _wk_attr in (
            ('home_tab',          'cover_worker'),
            ('playlists_browser', 'cover_worker'),
            ('_favorites_tab',    '_cover_worker'),
        ):
            _tab = getattr(self, _tab_attr, None)
            _wk  = getattr(_tab, _wk_attr, None) if _tab else None
            if _wk and _wk.isRunning():
                if hasattr(_wk, 'stop'):
                    _wk.stop()
                _wk.quit()
                if not _wk.wait(500):
                    _wk.terminate()

        # Stop tracks browser cover worker (TBCoverWorker — no stop(), uses .running flag)
        _tb = getattr(self, 'tracks_browser', None)
        _tbw = getattr(_tb, 'tb_cover_worker', None) if _tb else None
        if _tbw and _tbw.isRunning():
            _tbw.running = False
            _tbw.quit()
            if not _tbw.wait(500):
                _tbw.terminate()

        # Stop main-window tracked workers that aren't covered above
        for _attr in ('blur_thread', 'bpm_worker', '_sync_checker', 'cover_loader'):
            _w = getattr(self, _attr, None)
            if _w and _w.isRunning():
                _w.quit()
                if not _w.wait(600):
                    _w.terminate()

        # Drain the main-window graveyard
        for _w in list(getattr(self, '_worker_graveyard', set())):
            if _w.isRunning():
                _w.quit()
                if not _w.wait(400):
                    _w.terminate()
        if hasattr(self, '_worker_graveyard'):
            self._worker_graveyard.clear()

        # 4. Stop remaining Python worker threads
        if hasattr(self, 'loading_thread') and self.loading_thread and self.loading_thread.isRunning(): 
            self.loading_thread.quit()
            self.loading_thread.wait()
        
        if hasattr(self, 'playlist_loader') and self.playlist_loader and self.playlist_loader.isRunning():
            self.playlist_loader.quit()
            self.playlist_loader.wait()
            
        # 5. Stop PlaybackManager before the audio engine — it must exit its queue loop
        #    cleanly (via the 'quit' command) rather than being terminate()d mid-C++ call,
        #    which causes an access violation on Windows.
        if getattr(self, 'playback_manager', None) and self.playback_manager.isRunning():
            self.playback_manager.command_queue.put({'type': 'quit'})
            if not self.playback_manager.wait(2000):
                self.playback_manager.terminate()

        # 5b. Stop the active audio engine, then call close() → lib.cleanup() to signal
        #     the C++ engine's internal threads to exit.  cleanup() returns before those
        #     threads have fully exited, so we pump the Qt event loop once and sleep briefly
        #     to let them finish.  Without this, QApplication destroys QThreadStorage while
        #     the C++ thread is still alive → abort().
        if hasattr(self, 'audio_engine'):
            self.audio_engine.update_timer.stop()
            self.audio_engine.stop()
            self.audio_engine.close()

        # 5a. Send stop to any active cast/DLNA devices
        if hasattr(self, '_cast_manager'):
            try: self._cast_manager._disconnect_all()
            except Exception: pass
        
        # 6. Clean up temporary cache files
        for f in self.temp_files:
            try:
                if os.path.exists(f): os.unlink(f)
            except: pass
        
        # 7. Save user state (current song, index, color theme)
        # Skip if we're logging out — settings were already wiped intentionally
        if not getattr(self, '_logging_out', False):
            self.save_playlist()
        
                
        # ── Catch-all: stop any QThread that targeted cleanup missed ─────────
        import gc as _gc
        from PyQt6.QtCore import QThread as _QThread
        _stragglers = [obj for obj in _gc.get_objects()
                       if isinstance(obj, _QThread) and obj.isRunning()]
        if _stragglers:
            print(f"[CLOSE] {len(_stragglers)} straggler QThread(s) — stopping:")
            for _t in _stragglers:
                print(f"  {type(_t).__name__!r}  module={type(_t).__module__!r}  name={_t.objectName()!r}")
                if hasattr(_t, 'command_queue'):
                    _t.command_queue.put({'type': 'quit'})  # PlaybackManager
                elif hasattr(_t, 'stop'):
                    _t.stop()           # workers with a proper stop() method
                elif hasattr(_t, 'running'):
                    _t.running = False  # workers that just check a .running flag
                _t.quit()
                if not _t.wait(500):
                    _t.terminate()
        else:
            print("[CLOSE] All QThreads stopped cleanly.")
        # ─────────────────────────────────────────────────────────────────────

        # Allow the window to close, then immediately exit the process.
        # audio_core.dll's cleanup() is non-blocking — its C++ thread outlives the call.
        # QApplication's destructor then hits QThreadStorage::cleanup() while that thread
        # is still alive → abort().  os._exit(0) terminates before Qt's destructor runs;
        # all DB writes and file flushes are already done above.
        event.accept()
        self.settings.sync()
        import os as _os
        _os._exit(0)

    def resizeEvent(self, event):

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

        super().resizeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, 'swin') and self.swin and self.swin.isVisible():
            self.swin.close()
            self.hide_dim()

