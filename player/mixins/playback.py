"""
player/mixins/playback.py — Playlist management, transport controls,
gapless logic, BPM, queue building, and drag/drop.
"""
import os
import re
import sys
import json
import time
import random

from PyQt6.QtWidgets import QTreeWidgetItem, QFileDialog, QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

class PlaybackMixin:
    @staticmethod
    def _normalise_duration(track_data: dict):
        """Convert a raw numeric duration (seconds) to 'm:ss' in-place."""
        dur = track_data.get('duration', 0)
        if isinstance(dur, (int, float)):
            m, s = divmod(int(dur), 60)
            track_data['duration'] = f"{m}:{s:02d}"

    def _build_tree_item(self, track_data: dict) -> QTreeWidgetItem:
        """
        Build a fully-styled QTreeWidgetItem from a track dict.
        The caller is responsible for setting column 0 (track number) and
        inserting the item into the tree at the correct position.
        """
        title_str  = str(track_data.get('title')  or 'Unknown')
        album_str  = str(track_data.get('album')  or 'Unknown')
        artist_str = str(track_data.get('artist') or 'Unknown')
        year_str   = str(track_data.get('year')   or '')
        genre_str  = str(track_data.get('genre')  or '')
        dur_str    = str(track_data.get('duration', '0:00'))

        raw_plays = track_data.get('playCount') or track_data.get('play_count') or 0
        try:
            plays_str = str(int(raw_plays)) if int(raw_plays) > 0 else ""
        except (ValueError, TypeError):
            plays_str = ""

        raw_state = track_data.get('starred')
        if isinstance(raw_state, str):
            is_fav = raw_state.lower() in ('true', '1')
        else:
            is_fav = bool(raw_state)
        fav_str = "♥" if is_fav else "♡"

        item = QTreeWidgetItem()
        # col 0 = track number (set by caller)
        item.setText(1, title_str)
        item.setText(2, title_str)   # hidden sort column
        item.setText(3, artist_str)
        item.setText(4, album_str)
        item.setText(5, year_str)
        item.setText(6, genre_str)
        item.setText(7, fav_str)
        item.setText(8, plays_str)
        item.setText(9, dur_str)

        item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(9, Qt.AlignmentFlag.AlignCenter)

        fav_color = QColor("#E91E63") if is_fav else QColor("#555555")
        item.setForeground(7, fav_color)

        item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'track', 'data': track_data})
        return item

    def add_and_play_from_browser(self, track_data):
        self._normalise_duration(track_data)
        self.playlist_data.append(track_data)

        item = self._build_tree_item(track_data)
        item.setText(0, str(len(self.playlist_data)))
        self.tree.addTopLevelItem(item)

        idx = len(self.playlist_data) - 1
        self.play_song(idx)
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()

        cid = track_data.get('cover_id') or track_data.get('coverArt') or track_data.get('albumId')
        if cid:
            self.playlist_cover_worker.queue_covers([cid])

    def add_track_to_queue(self, track_data):
        self._normalise_duration(track_data)
        self.playlist_data.append(track_data)

        item = self._build_tree_item(track_data)
        item.setText(0, str(self.tree.topLevelItemCount()))
        self.tree.addTopLevelItem(item)

        cid = track_data.get('cover_id') or track_data.get('coverArt') or track_data.get('albumId')
        if cid:
            self.playlist_cover_worker.queue_covers([cid])
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()

    def play_track_next(self, track_data):
        self._normalise_duration(track_data)

        if not self.playlist_data:
            self.add_and_play_from_browser(track_data)
            return

        insert_pos = self.current_index + 1
        self.playlist_data.insert(insert_pos, track_data)

        item = self._build_tree_item(track_data)
        self.tree.insertTopLevelItem(insert_pos, item)
        for i in range(insert_pos, self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setText(0, str(i + 1))

        if self.current_index != -1:
            self.preload_next()
        cid = track_data.get('cover_id') or track_data.get('coverArt') or track_data.get('albumId')
        if cid:
            self.playlist_cover_worker.queue_covers([cid])
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()

    def add_batch_to_ui(self, batch):
        self.tree.setUpdatesEnabled(False)
        cover_ids = []

        for track in batch:
            self._normalise_duration(track)
            self.playlist_data.append(track)

            item = self._build_tree_item(track)
            item.setText(0, str(self.tree.topLevelItemCount() + 1))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            self.tree.addTopLevelItem(item)

            cid = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
            if cid:
                cover_ids.append(cid)

        if hasattr(self, 'playlist_cover_worker'):
            self.playlist_cover_worker.queue_covers(cover_ids)
        self.tree.setUpdatesEnabled(True)
        self.update_indicator(scroll_to_current=False)
        self.update_window_title()
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()
    
    def play_whole_album(self, data):
        if isinstance(data, list):
            self.playlist_data.clear()
            self.tree.clear()
            self.current_index = -1
            if hasattr(self, '_now_playing_panel'): self._now_playing_panel.clear_filter()
            self.add_batch_to_ui(data)
            if self.playlist_data: self.play_song(0)
            return

        album_id = data.get('id')
        if not album_id: return
        
        if hasattr(self, 'navidrome_client') and self.navidrome_client:
            tracks = self.navidrome_client.get_album_tracks(album_id)
            if tracks:
                self.playlist_data.clear()
                self.tree.clear()
                self.current_index = -1
                if hasattr(self, '_now_playing_panel'): self._now_playing_panel.clear_filter()
                self.add_batch_to_ui(tracks)
                self.play_song(0)
      
    def play_full_album_by_id(self, album_id, shuffle=False):
        try:
            if hasattr(self, 'navidrome_client') and self.navidrome_client:
                tracks = self.navidrome_client.get_album_tracks(album_id)
                if tracks:
                    if shuffle:
                        
                        random.shuffle(tracks)
                    self.play_whole_album(tracks)
        except Exception as e: print(f"Error playing global album: {e}")

    def play_artist_by_name(self, artist_name):
        try:
            if hasattr(self, 'navidrome_client') and self.navidrome_client:
                raw_tracks = self.navidrome_client.search_artist_tracks(artist_name)
                if raw_tracks:
                    
                    target = artist_name.lower().strip()
                    
                    # Only split on explicit multi-artist database tags!
                    # We removed '&', 'vs.', 'feat.', and ',' so duos stay intact.
                    _split_re = re.compile(r'(?: /// | • | / )')

                    def _tokens(s):
                        return {p.strip().lower() for p in _split_re.split(s) if p.strip()}

                    filtered = []
                    for t in raw_tracks:
                        artist_tokens     = _tokens(str(t.get('artist') or ''))
                        alb_artist_tokens = _tokens(str(t.get('albumArtist') or t.get('album_artist') or ''))
                        if target in artist_tokens or target in alb_artist_tokens:
                            filtered.append(t)

                    filtered.sort(key=lambda x: (x.get('album', ''), int(x.get('discNumber', 1)), int(x.get('trackNumber', 0))))
                    self.play_whole_album(filtered)
        except Exception as e: print(f"Error fetching artist tracks: {e}")
    def play_track_from_data(self, track_data):
        # 1. Stop current playback
        self.audio_engine.stop()
        
        # 2. Clear the internal data and the visual tree
        self.playlist_data.clear()
        self.tree.clear()
        self.current_index = -1
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.clear_filter()
        
        # 3. Use your existing native method to add and play the track!
        self.add_and_play_from_browser(track_data)

    def play_song(self, idx, record_history=True):
        t0 = time.time()
        print(f"[TIMING] 0.00s | User Clicked Play / Next")

        if 0 <= idx < len(self.playlist_data):
            if record_history and self.current_index != -1 and self.current_index != idx:
                self.history.append(self.current_index)
            
            self.current_index = idx
            track = self.playlist_data[idx]
            self.queued_next_index = -1  

            # UI Updates
            self.load_current_track_metadata_text_only()
            self.update_indicator()
            self.update_window_title()
            
            print(f"[TIMING] +{time.time() - t0:.3f}s | UI Text Updated")

            # Reset Seek Bar
            self.seek_bar.update_position(0)
            self.current_time_label.setText("0:00")

            # Reset waveform to loading state so "ANALYZING WAVEFORM..." is shown
            # while the C++ engine generates data for the new track.
            self.seek_bar.reset_waveform()

            # Stop Preloads
            self.preload_timer.stop() 
            if hasattr(self, 'cache_worker') and self.cache_worker:
                 self.cache_worker.stop()
            self.visual_update_timer.stop()

            if self.blur_thread and self.blur_thread.isRunning():
                try: self.blur_thread.finished.disconnect()
                except: pass
                self.blur_thread.quit()

            self.visual_update_timer.start(350)
            
            track['debug_t0'] = t0 
            self.playback_manager.play_request(track)
            
            print(f"[TIMING] +{time.time() - t0:.3f}s | Request Sent to Manager")

    def play_next(self):
        visible_indices = self.get_visible_indices()
        if not visible_indices: return
        if self.is_shuffle:
            if len(visible_indices) > 1:
                new_idx = self.current_index
                while new_idx == self.current_index: new_idx = random.choice(visible_indices)
                self.current_index = new_idx
            else: self.current_index = visible_indices[0]
        else:
            if self.current_index in visible_indices:
                current_pos = visible_indices.index(self.current_index); next_pos = (current_pos + 1) % len(visible_indices); self.current_index = visible_indices[next_pos]
            else: self.current_index = visible_indices[0]
        self.play_song(self.current_index)
    
    def play_prev(self):
        if self.is_shuffle and self.history:
            prev_idx = self.history.pop()
            self.play_song(prev_idx, record_history=False)
            return

        visible_indices = self.get_visible_indices()
        if not visible_indices: return
        
        target_index = -1
        if self.current_index in visible_indices:
            current_pos = visible_indices.index(self.current_index)
            prev_pos = (current_pos - 1) % len(visible_indices)
            target_index = visible_indices[prev_pos]
        else:
            target_index = visible_indices[0]
        self.play_song(target_index, record_history=False)
    
    def toggle_playback(self):
        if not self.playlist_data: 
            if hasattr(self, 'import_music'): self.import_music()
            return
        
        if self.audio_engine.is_playing: 
            self.audio_engine.pause()
            self.smooth_timer.stop() 
            if hasattr(self, 'seek_bar'):
                self.seek_bar.is_playing = False
        else:
            # If nothing is selected, default to the top track
            if self.current_index == -1: 
                self.current_index = 0

            # Let your native playback manager handle the fresh track safely!
            if self.audio_engine.total_ms <= 0:
                track = self.playlist_data[self.current_index]
                self.playback_manager.play_request(track)
            else:
                self.audio_engine.play()
                self.last_engine_update_time = time.time()
                self.smooth_timer.start() 
                if hasattr(self, 'seek_bar'):
                    self.seek_bar.is_playing = True
        
        self.refresh_ui_styles()
        self.update_window_title()

    def play_global_album(self):
        if hasattr(self.global_album_view, 'current_album_id') and self.global_album_view.current_album_id: 
            self.play_full_album_by_id(self.global_album_view.current_album_id, shuffle=False)

    def shuffle_global_album(self):
        if hasattr(self.global_album_view, 'current_album_id') and self.global_album_view.current_album_id: 
            self.play_full_album_by_id(self.global_album_view.current_album_id, shuffle=True)

    def play_global_playlist(self):
        if hasattr(self.global_playlist_view, 'current_tracks') and self.global_playlist_view.current_tracks: 
            self.play_whole_album(self.global_playlist_view.current_tracks)

    def shuffle_global_playlist(self):
        if hasattr(self.global_playlist_view, 'current_tracks') and self.global_playlist_view.current_tracks: 
            
            shuffled = list(self.global_playlist_view.current_tracks)
            random.shuffle(shuffled)
            self.play_whole_album(shuffled)    
    
    def on_play_started(self, track_data=None):
        if hasattr(self, 'seek_bar'):
            self.seek_bar.is_playing = True

                
        # Ask C++ to analyze if we are in Mode 0 or Mode 2
        target_path = track_data.get('stream_url') or track_data.get('path')
        if target_path and getattr(self.seek_bar, 'display_mode', 0) in (0, 2):
            self.audio_engine.request_waveform(target_path, num_points=10000)
            
        self.sync_playlist_duration()
        self.preload_next() 
        self.refresh_ui_styles(scroll_to_current=False)
        self.update_window_title()
       
    def on_track_finished(self):
        # Safety 1: If a gapless transition just happened, ignore this "End" signal.
        if time.time() - self.last_gapless_time < 2.0:
            return

        # Safety 2: If we have a gapless track queued, let the engine handle the switch.
        if self.queued_next_index != -1:
             return

        # Standard Behavior: Playlist ended naturally or user stopped playback.
        next_idx = self.get_next_index_calculated()
        if next_idx != -1:
            if self.current_index != -1: self.history.append(self.current_index)
            self.current_index = next_idx
            self.play_song(self.current_index)

    def on_gapless_transition(self):
        print("Gapless transition triggered.")
        
        # Keep motor running on gapless changes
        if hasattr(self, 'seek_bar'):
            self.seek_bar.is_playing = True
            
        self.last_gapless_time = time.time() 
        
        new_index = -1
        if self.queued_next_index != -1:
            new_index = self.queued_next_index
            self.queued_next_index = -1 
        else:
            new_index = self.get_next_index_calculated()

        if new_index != -1:
            if self.current_index != -1: 
                self.history.append(self.current_index)
            
            self.current_index = new_index
            
            # UI Updates
            self.load_current_track_metadata_text_only()
            self.update_indicator()
            self.update_window_title()
            
            # Pull the track from the playlist and use the 'track' variable
            track = self.playlist_data[self.current_index]
            target_path = track.get('stream_url') or track.get('path')
            
            if target_path and getattr(self.seek_bar, 'display_mode', 0) in (0, 2):
                self.seek_bar.reset_waveform()
                self.audio_engine.request_waveform(target_path, num_points=10000)
            
            self.visual_update_timer.start(350)
            self.preload_next()
    
    def preload_next(self):
        """
        Calculates the next index and schedules a preload.
        """
        self.preload_timer.stop()
        if hasattr(self, 'cache_worker') and self.cache_worker:
            self.cache_worker.stop()
            self.cache_worker = None

        next_idx = self.get_next_index_calculated()
        
        if next_idx == -1 or next_idx == self.current_index:

            return

        self.queued_next_index = next_idx 

        next_track = self.playlist_data[next_idx]
        
        is_local = (next_track.get('path') and os.path.exists(next_track['path']))
        is_cached = (next_track.get('cached_path') and os.path.exists(next_track['cached_path']))
        
        if is_local or is_cached:
            self._execute_preload_now()
        else:

            self.preload_timer.start(4000)

    def _execute_preload_now(self):
        next_idx = self.get_next_index_calculated()
        if next_idx == -1: return

        next_track = self.playlist_data[next_idx]
        
        if next_track.get('path') and os.path.exists(next_track['path']):
            self.queued_next_index = next_idx
            self.playback_manager.queue_request(next_track['path'])
            
        elif next_track.get('stream_url'):
            self.queued_next_index = next_idx
            # Tell C++ to pull the URL directly into RAM in the background!
            self.audio_engine.lib.preload_network_stream(next_track['stream_url'].encode('utf-8'))
    
    def _media_stop(self):
        # Turn off the motor when media completely stops
        if hasattr(self, 'seek_bar'):
            self.seek_bar.is_playing = False
            
        self.audio_engine.stop()
        self.update_window_title()

    def get_next_index_calculated(self):
        visible_indices = self.get_visible_indices()
        if not visible_indices: return -1
        
        # If Repeat One is on, we don't preload a "next" song usually, 
        # but for gapless we might want to re-queue the same file.
        if self.is_repeat: 
            return self.current_index

        if self.is_shuffle:
            # Shuffle logic might be the cause of "skipping" if it's random
            # Ideally shuffle order should be pre-determined to be gapless-safe
            if len(visible_indices) > 1:
                # Simple random for now (risk of repeating)
                new_idx = self.current_index
                while new_idx == self.current_index: 
                    new_idx = random.choice(visible_indices)
                return new_idx
            else: 
                return visible_indices[0]

        # Sequential Logic
        if self.current_index in visible_indices:
            current_pos = visible_indices.index(self.current_index)
            next_pos = (current_pos + 1) % len(visible_indices)
            return visible_indices[next_pos]
        else: 
            return visible_indices[0]
   
    def get_visible_indices(self): 
        return [i for i in range(self.tree.topLevelItemCount()) if not self.tree.topLevelItem(i).isHidden()]
    
    def toggle_shuffle(self):
        self.is_shuffle = self.btn_shuffle.isChecked()
        self.btn_shuffle.setToolTip(f"Shuffle {'on' if self.is_shuffle else 'off'}")
        self.refresh_ui_styles()
        self.preload_next() 

    def toggle_repeat(self):
        self.is_repeat = self.btn_repeat.isChecked()
        self.btn_repeat.setToolTip(f"Repeat {'on' if self.is_repeat else 'off'}")
        self.refresh_ui_styles()
        self.preload_next()

    def handle_duration_change(self, duration):
        if duration > 0: 
            self.seek_bar.update_duration(duration)
            self.total_time_label.setText(self.format_time(duration))
    
    def update_ui_state(self, position):
        # 1. SHIELD MUST BE FIRST
        if time.time() < getattr(self, 'ignore_updates_until', 0):
            return
            
        # 2. DO NOT INTERFERE IF SCRATCHING
        is_djing = getattr(self.seek_bar, 'is_dragging', False) or getattr(self.seek_bar, 'is_spinning_freely', False)
        if is_djing:
            return

        # 3. SAFE TO UPDATE
        self.last_engine_pos = position
        self.last_engine_update_time = time.time()
        
        if hasattr(self.seek_bar, 'update_position'):
            self.seek_bar.update_position(position)
            
        if hasattr(self, 'current_time_label'): 
            self.current_time_label.setText(self.format_time(position))

    def run_smooth_interpolator(self):
        is_djing = getattr(self.seek_bar, 'is_dragging', False) or getattr(self.seek_bar, 'is_spinning_freely', False)
        
        if self.audio_engine.is_playing and not is_djing:
            # Safe elapsed time calculation
            elapsed_ms = (time.time() - getattr(self, 'last_engine_update_time', time.time())) * 1000
            predicted_pos = int(getattr(self, 'last_engine_pos', 0) + elapsed_ms)
            
            # Cap it to duration
            duration = getattr(self.seek_bar, 'duration_ms', 0)
            if duration > 0:
                predicted_pos = min(predicted_pos, duration)
            
            if hasattr(self.seek_bar, 'update_position'):
                self.seek_bar.update_position(predicted_pos)
            
            if hasattr(self, 'current_time_label'):
                self.current_time_label.setText(self.format_time(predicted_pos))
    
    def sync_playlist_duration(self):
        if 0 <= self.current_index < self.tree.topLevelItemCount():
            duration_ms = self.audio_engine.total_ms
            if duration_ms > 0:
                formatted = self.format_time(duration_ms)
                item = self.tree.topLevelItem(self.current_index)
                
                
                if item.text(9) != formatted: 
                    item.setText(9, formatted)
                    wrapped = item.data(0, Qt.ItemDataRole.UserRole)
                    if wrapped:
                        track = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else wrapped
                        track['duration'] = formatted
                        if 0 <= self.current_index < len(self.playlist_data):
                            self.playlist_data[self.current_index]['duration'] = formatted
     
    def format_time(self, ms): 
        ms = int(ms); seconds = (ms // 1000) % 60; minutes = (ms // 60000) % 60
        return f"{minutes}:{seconds:02d}"
    
    def on_waveform_seek(self, target_ms): 
        is_pending = 0
        if hasattr(self.audio_engine, 'lib'): 
            is_pending = self.audio_engine.lib.is_transition_pending()

        if is_pending == 1:
            if 0 <= self.current_index < len(self.playlist_data):
                track = self.playlist_data[self.current_index]
                if track.get('path') and os.path.exists(track['path']):
                    self.audio_engine.load_track(track['path'])
                    self.audio_engine.seek(target_ms)
                else: self.audio_engine.seek(target_ms)
        else: self.audio_engine.seek(target_ms)
        
        self.queued_next_index = -1; self.preload_next()
        self.ignore_updates_until = time.time() + 1.0
        self.last_engine_pos = target_ms
        self.last_engine_update_time = time.time()

    def on_live_scratch(self, target_ms):
        """Fires 60 times a second while the user is dragging the waveform."""
        

        if hasattr(self, 'current_time_label'):
            self.current_time_label.setText(self.format_time(target_ms))
    
    def on_waveform_toggled(self, mode_int):
        """If user turns waveform back on mid-song, fetch the data on-demand."""
        # Modes 0 and 2 BOTH require audio waveform data!
        needs_waveform = (mode_int in (0, 2))
        
        if needs_waveform and not getattr(self.seek_bar, 'has_real_data', False):
            if 0 <= self.current_index < len(self.playlist_data):
                track = self.playlist_data[self.current_index]
                target_path = track.get('stream_url') or track.get('path')
                if target_path:
                    self.seek_bar.reset_waveform()
                    self.audio_engine.request_waveform(target_path, num_points=10000)
    
    def load_bpm_cache(self):
        """Loads the saved BPM dictionary from the app_data/json_data folder."""
        # 1. Safely find the directory, whether we are in dev mode or a frozen .exe!
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        # 2. Build the target folder path: working_dir/app_data/json_data
        cache_dir = os.path.join(base_dir, "app_data", "json_data")
        os.makedirs(cache_dir, exist_ok=True)
        self.bpm_cache_file = os.path.join(cache_dir, "bpm_cache.json")

        # 3. Use data pre-loaded by the background thread if available
        import player.mixins.playback as _self_module
        if getattr(_self_module, '_preloaded_bpm_cache', None) is not None:
            data = _self_module._preloaded_bpm_cache
            _self_module._preloaded_bpm_cache = None  # free memory
            return data

        # 4. Fall back to reading the file normally
        if os.path.exists(self.bpm_cache_file):
            try:
                with open(self.bpm_cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_bpm_cache(self):
        """Saves the current dictionary to disk."""
        try:
            with open(self.bpm_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.bpm_cache, f)
        except Exception as e:
            print(f"Could not save BPM cache: {e}")
    
    def _on_bpm_calculated(self, bpm, track_id):
        """Receives the float from C++, caches it, and appends it to the UI."""
        if bpm > 0:
            # 1. Save it to our permanent memory
            self.bpm_cache[track_id] = bpm
            self.save_bpm_cache()
            
            # 2. Only update the UI if the user hasn't skipped to a different song
            current_track_id = str(self.playlist_data[self.current_index].get('id') or self.playlist_data[self.current_index].get('path'))
            if track_id == current_track_id:
                self.file_type_label.setText(f"{self.current_file_type_text}   •   {bpm:.1f} BPM")
        else:
            self.file_type_label.setText(self.current_file_type_text) # Just MP3 if it fails
    
    def import_music(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Open Music Files", "", "Audio Files (*.mp3 *.flac *.wav *.ogg *.m4a)")
        if files:
            self.audio_engine.stop()
            self.playlist_data = []
            self.tree.clear()
            self.current_index = -1
            if hasattr(self, '_now_playing_panel'):
                self._now_playing_panel.clear_filter()
            self.loading_thread = MetadataWorker(files)
            self.loading_thread.progress.connect(self.add_batch_to_ui)
            self.loading_thread.progress.connect(self._play_first_batch_auto)
            self.loading_thread.start()
            self.refresh_ui_styles()
    
    def process_new_items(self, file_paths):
        self.loading_thread = MetadataWorker(file_paths)
        self.loading_thread.progress.connect(self.add_batch_to_ui)
        self.loading_thread.start()
    
    def _play_first_batch_auto(self, batch):
        if self.current_index == -1 and self.playlist_data: self.play_song(0)
        self.loading_thread.progress.disconnect(self._play_first_batch_auto)
    
    def delete_selected_tracks(self):
        selected_items = self.tree.selectedItems()
        if not selected_items: return
        self.history.clear()
        playing_track = None
        if 0 <= self.current_index < len(self.playlist_data): playing_track = self.playlist_data[self.current_index]
        for item in selected_items:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx != -1:
                if 0 <= idx < len(self.playlist_data): self.playlist_data.pop(idx)
                self.tree.takeTopLevelItem(idx)
        self.current_index = -1
        if playing_track:
            try: self.current_index = self.playlist_data.index(playing_track)
            except ValueError: self.current_index = -1
        if self.current_index == -1 and playing_track: self.audio_engine.stop()
        self.setWindowTitle("Sonar")
        for i in range(self.tree.topLevelItemCount()): self.tree.topLevelItem(i).setText(0, str(i + 1))
        self.refresh_ui_styles()
        self.update_indicator()
        if hasattr(self, '_now_playing_panel'):
            self._now_playing_panel.update_status()
    
    def sync_data_after_drag(self):
        playing_track = None
        self.history.clear()
        
        if 0 <= self.current_index < len(self.playlist_data): 
            playing_track = self.playlist_data[self.current_index]
            
        new_data_list = []
        new_playing_index = self.current_index
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            
            wrapped    = item.data(0, Qt.ItemDataRole.UserRole)
            track_data = wrapped.get('data', wrapped) if isinstance(wrapped, dict) else None
            
            if not track_data:
                title = item.text(2) # Title is now 2
                album = item.text(4) # Album is now 4
                for old_track in self.playlist_data:
                    if old_track.get('title') == title and old_track.get('album') == album:
                        track_data = old_track
                        item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'track', 'data': track_data})
                        break
                        
            if track_data: 
                new_data_list.append(track_data)
                
            
            if playing_track and track_data:
                is_match = False
                for key in ('id', 'path', 'stream_url'):
                    if track_data.get(key) and playing_track.get(key) and track_data.get(key) == playing_track.get(key):
                        is_match = True
                        break
                
                # Fallback to dictionary equality
                if not is_match and track_data == playing_track:
                    is_match = True
                    
                if is_match:
                    new_playing_index = i 
                
        self.playlist_data = new_data_list
        self.current_index = new_playing_index
        self.last_index = -1  

        # 2. Scrub ALL rows to fix track numbers and completely erase lingering GIFs!
        from PyQt6.QtGui import QFont, QColor
        
        normal_font = QFont("sans-serif", 11)
        normal_font.setBold(False)
        default_color = QColor("#ddd")
        transparent = QColor(0, 0, 0, 0) # Safe RGBA transparency to avoid Qt import errors
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            
            self.tree.removeItemWidget(item, 0) # Destroys the old GIF if it moved
            item.setText(0, str(i + 1))         # Resets the track number sequentially
            item.setFont(0, normal_font)
            
            # Wipe away old highlights (except for the heart column!)
            for col in range(self.tree.columnCount()):
                item.setBackground(col, transparent)
                if col != 7:
                    item.setForeground(col, default_color)
                    if col > 0: item.setFont(col, normal_font)
        
        # 3. Redraw the living GIF at its perfect new location!
        self.update_indicator(scroll_to_current=False)
      
    def on_queue_item_clicked(self, item, column):
        if column == 7: 
            data_variant = item.data(0, Qt.ItemDataRole.UserRole)
            if not data_variant: return
            
            track = data_variant.get('data', data_variant) if isinstance(data_variant, dict) else data_variant
            
            
            raw_state = track.get('starred')
            if isinstance(raw_state, str): current_state = raw_state.lower() in ('true', '1')
            else: current_state = bool(raw_state)
            new_state = not current_state
            
            track['starred'] = new_state
            item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'track', 'data': track})
            
            # Instantly update visual state using Master Color!
            item.setText(7, "♥" if new_state else "♡")
            item.setForeground(7, QColor("#E91E63") if new_state else QColor("#555"))
            
            # Sync to the master playlist array so it doesn't revert on next song
            idx = self.tree.indexOfTopLevelItem(item)
            if 0 <= idx < len(self.playlist_data):
                self.playlist_data[idx]['starred'] = new_state
            
            if hasattr(self, 'navidrome_client') and self.navidrome_client:
                self.navidrome_client.set_favorite(track.get('id'), new_state)
    
    def on_item_double_clicked(self, item, column):
        index = self.tree.indexOfTopLevelItem(item)
        if index != -1: self.play_song(index)
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()
    
    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                for root, dirs, filenames in os.walk(path):
                    for filename in filenames:
                        if filename.lower().endswith(('.mp3', '.flac', '.wav', '.ogg', '.m4a')): files.append(os.path.join(root, filename))
            elif path.lower().endswith(('.mp3', '.flac', '.wav', '.ogg', '.m4a')): files.append(path)
        files.sort(); 
        if files: self.process_new_items(files)
    
    def show_ghost_drag(self, pixmap, global_pos):
        self.ghost_label.setPixmap(pixmap)
        self.ghost_label.adjustSize()
        self.ghost_label.raise_()
        self.ghost_label.show()
        local_pos = self.mapFromGlobal(global_pos); self.ghost_label.move(local_pos)
    
    def move_ghost_drag(self, global_pos): 
        local_pos = self.mapFromGlobal(global_pos)
        self.ghost_label.move(local_pos)
    
