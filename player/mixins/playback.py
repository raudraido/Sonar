"""
player/mixins/playback.py — Playlist management, transport controls,
gapless logic, BPM, queue building, and drag/drop.
"""
import os
import re
import sys
import json
from player.components.version import __version__
import time
import random

from PyQt6.QtWidgets import QTreeWidgetItem, QFileDialog, QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from player.workers import MetadataWorker

class PlaybackMixin:
    @staticmethod
    def _normalise_duration(track_data: dict):
        """Convert a raw numeric duration (seconds) to 'm:ss' in-place."""
        dur = track_data.get('duration', 0)
        if isinstance(dur, (int, float)):
            h, rem = divmod(int(dur), 3600)
            track_data['duration'] = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"

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

        raw_track_no = track_data.get('trackNumber') or track_data.get('track') or ''
        track_no_str = str(raw_track_no) if raw_track_no else ''

        item = QTreeWidgetItem()
        # col 0 = queue position (set by caller)
        item.setText(1, title_str)
        item.setText(2, title_str)   # hidden sort column
        item.setText(3, artist_str)
        item.setText(4, album_str)
        item.setText(5, year_str)
        item.setText(6, genre_str)
        item.setText(7, fav_str)
        item.setText(8, plays_str)
        item.setText(9, dur_str)
        item.setText(10, track_no_str)

        item.setTextAlignment(0,  Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(7,  Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(9,  Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(10, Qt.AlignmentFlag.AlignCenter)

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
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()

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
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()

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
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()

    def start_radio(self, track):
        """Clear queue, play seed track, then fill queue with similar songs."""
        from player.workers import RadioWorker

        seed_id = str(track.get('id', ''))

        # Clear everything and play the seed as the only track
        self.audio_engine.stop()
        self.playlist_data.clear()
        self.current_index = -1
        self.history.clear()
        self._shuffle_queue.clear()
        self.tree.clear()
        self._refresh_queue_panel()

        self.add_and_play_from_browser(track)

        artist_id   = track.get('artistId') or track.get('artist_id', '')
        artist_name = track.get('artist', '')
        if not artist_id:
            print("[Radio] No artist ID — cannot start radio")
            return

        if hasattr(self, '_radio_worker') and self._radio_worker and self._radio_worker.isRunning():
            self._radio_worker.quit()
            self._radio_worker.wait()

        self._radio_worker = RadioWorker(self.navidrome_client, artist_id, artist_name, seed_id=seed_id)
        self._radio_worker.finished.connect(self._on_radio_tracks_ready)
        self._radio_worker.start()
        if hasattr(self, '_queue_panel'):
            self._queue_panel.set_radio_loading(True)

    def _on_radio_tracks_ready(self, tracks):
        for t in tracks:
            self.add_track_to_queue(t)
        if hasattr(self, '_queue_panel'):
            self._queue_panel.set_radio_loading(False)

    _BATCH_CHUNK = 100   # items inserted per frame

    def add_batch_to_ui(self, batch):
        offset = self.tree.topLevelItemCount()
        cover_ids = []

        # 1. Normalise all tracks and populate playlist_data synchronously
        #    so playback can start before the tree is fully painted.
        all_items = []
        for i, track in enumerate(batch):
            self._normalise_duration(track)
            self.playlist_data.append(track)
            item = self._build_tree_item(track)
            item.setText(0, str(offset + i + 1))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            all_items.append(item)
            cid = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
            if cid:
                cover_ids.append(cid)

        # 2. Insert first chunk synchronously so the user sees tracks immediately.
        self.tree.setUpdatesEnabled(False)
        self.tree.addTopLevelItems(all_items[:self._BATCH_CHUNK])
        self.tree.setUpdatesEnabled(True)
        self.update_indicator(scroll_to_current=False)
        self.update_window_title()
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()

        # 3. Drip-feed remaining chunks without blocking the event loop.
        remaining = all_items[self._BATCH_CHUNK:]
        if remaining:
            self._insert_remaining_chunks(remaining, cover_ids)
        elif hasattr(self, 'playlist_cover_worker'):
            self.playlist_cover_worker.queue_covers(cover_ids)

    def _insert_remaining_chunks(self, items, cover_ids, start=0):
        chunk = items[start:start + self._BATCH_CHUNK]
        if not chunk:
            if hasattr(self, 'playlist_cover_worker'):
                self.playlist_cover_worker.queue_covers(cover_ids)
            if hasattr(self, '_queue_tree_panel'):
                self._queue_tree_panel.update_status()
            return
        self.tree.setUpdatesEnabled(False)
        self.tree.addTopLevelItems(chunk)
        self.tree.setUpdatesEnabled(True)
        QTimer.singleShot(0, lambda: self._insert_remaining_chunks(items, cover_ids, start + self._BATCH_CHUNK))
    
    def play_whole_album(self, data):
        if isinstance(data, list):
            self.playlist_data.clear()
            self.tree.clear()
            self.current_index = -1
            if hasattr(self, '_queue_tree_panel'): self._queue_tree_panel.clear_filter()
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
                if hasattr(self, '_queue_tree_panel'): self._queue_tree_panel.clear_filter()
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
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.clear_filter()
        
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

            # Reset Seek Bar
            self._footer_panel.set_position_ms(0, hard=True)

            # Reset waveform to loading state so "ANALYZING WAVEFORM..." is
            # shown while the C++ engine generates data for the new track.
            # Must run BEFORE load_current_track_metadata_text_only() below
            # — that call restores a cached bpm/beat-grid immediately if
            # this track was already played before, and reset_waveform()
            # unconditionally clears the grid/metronome; the wrong order
            # here meant a cache-hit restore got immediately wiped out by
            # this reset running right after it, so returning to an
            # already-played track silently lost its grid every time.
            self._footer_panel.reset_waveform()

            # UI Updates — update_indicator debounced so rapid clicking only runs once
            self.load_current_track_metadata_text_only()
            self._indicator_debounce.start()   # restarts 60ms window on each click
            self.update_window_title()

            print(f"[TIMING] +{time.time() - t0:.3f}s | UI Text Updated")

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

            # Report now-playing in background (was synchronous — blocked main thread!)
            track_id = track.get('id')
            if track_id and hasattr(self, 'navidrome_client') and self.navidrome_client:
                import threading as _t
                _c = self.navidrome_client; _tid = track_id
                _t.Thread(target=lambda: _c.scrobble(_tid, submission=False), daemon=True).start()

            # Debounce metadata refresh so it only fires for the final clicked track
            self._refresh_debounce.start()

            track['debug_t0'] = t0
            self._play_with_cast_sync(track)

            print(f"[TIMING] +{time.time() - t0:.3f}s | Request Sent to Manager")

    def _rebuild_shuffle_queue(self):
        """Shuffle all visible tracks except the current one; call when starting shuffle or queue exhausted."""
        visible = self.get_visible_indices()
        remaining = [i for i in visible if i != self.current_index]
        random.shuffle(remaining)
        self._shuffle_queue = remaining

    def _next_shuffle_index(self, peek=False):
        """Return the next index from the shuffle queue. Rebuilds when exhausted."""
        visible = set(self.get_visible_indices())
        # Prune entries that are no longer visible
        self._shuffle_queue = [i for i in self._shuffle_queue if i in visible]
        if not self._shuffle_queue:
            self._rebuild_shuffle_queue()
        if not self._shuffle_queue:
            return self.current_index
        return self._shuffle_queue[0] if peek else self._shuffle_queue.pop(0)

    def play_next(self):
        visible_indices = self.get_visible_indices()
        if not visible_indices: return
        if self.is_shuffle:
            new_idx = self._next_shuffle_index(peek=False)
        else:
            if self.current_index in visible_indices:
                current_pos = visible_indices.index(self.current_index)
                next_pos = (current_pos + 1) % len(visible_indices)
                new_idx = visible_indices[next_pos]
            else:
                new_idx = visible_indices[0]
        # Record history BEFORE mutating current_index so play_song sees the right old value
        if self.current_index != -1 and self.current_index != new_idx:
            self.history.append(self.current_index)
        self.current_index = new_idx
        self.play_song(self.current_index, record_history=False)

    def play_prev(self):
        # Always use history so back retraces the exact path forward (shuffle or not)
        if self.history:
            prev_idx = self.history.pop()
            self.play_song(prev_idx, record_history=False)
            return

        # No history yet — fall back to sequential previous
        visible_indices = self.get_visible_indices()
        if not visible_indices: return
        if self.current_index in visible_indices:
            current_pos = visible_indices.index(self.current_index)
            target_index = visible_indices[(current_pos - 1) % len(visible_indices)]
        else:
            target_index = visible_indices[0]
        self.play_song(target_index, record_history=False)
    
    def toggle_playback(self):
        if not self.playlist_data: 
            if hasattr(self, 'import_music'): self.import_music()
            return
        
        if self.audio_engine.is_playing:
            self.audio_engine.pause()
            self._footer_panel.set_playing(False)
            self._cast_relay_pause()
        else:
            # If nothing is selected, default to the top track
            if self.current_index == -1:
                self.current_index = 0

            # Let your native playback manager handle the fresh track safely!
            if self.audio_engine.total_ms <= 0:
                track = self.playlist_data[self.current_index]
                self._play_with_cast_sync(track)
            else:
                self.audio_engine.play()
                self._footer_panel.set_playing(True)
                self._cast_relay_play()

        self.refresh_ui_styles()
        self.update_window_title()
        if hasattr(self, '_queue_panel'):
            self._queue_panel.update_playing_state(self.audio_engine.is_playing)

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
        self._footer_panel.set_playing(True)

        # Ask C++ to analyze if we are in Mode 0 or Mode 2
        target_path = track_data.get('stream_url') or track_data.get('path')
        if target_path and self._footer_panel.display_mode in (0, 2):
            track_id = str(track_data.get('id') or track_data.get('path'))
            is_scratch = self._footer_panel.display_mode == 0
            self._waveform_data_is_light = not is_scratch
            self.audio_engine.request_waveform(target_path, num_points=10000, track_id=track_id, light=not is_scratch)
            if is_scratch:
                self.audio_engine.request_waveform_bands(target_path, num_points=10000, track_id=track_id)

        self.sync_playlist_duration()
        self.preload_next()
        self.refresh_ui_styles(scroll_to_current=False)
        self.update_window_title()
        self._refresh_queue_panel()
       
    def _scrobble_complete(self, track_id):
        """Report a fully-played track to the server (submission=True) — this
        is what actually increments the server-side play count; submission=False
        (sent on track start) only marks "now playing" and never does."""
        if not (track_id and hasattr(self, 'navidrome_client') and self.navidrome_client):
            return
        import threading as _t
        _c = self.navidrome_client; _tid = track_id
        _t.Thread(target=lambda: _c.scrobble(_tid, submission=True), daemon=True).start()

    def on_track_finished(self):
        # Safety 1: If a gapless transition just happened, ignore this "End" signal.
        if time.time() - self.last_gapless_time < 2.0:
            return

        # Safety 2: If we have a gapless track queued, let the engine handle the switch.
        if self.queued_next_index != -1:
             return

        if 0 <= self.current_index < len(self.playlist_data):
            self._scrobble_complete(self.playlist_data[self.current_index].get('id'))

        # Standard Behavior: Playlist ended naturally or user stopped playback.
        next_idx = self.get_next_index_calculated()
        if next_idx != -1:
            if self.current_index != -1: self.history.append(self.current_index)
            self.current_index = next_idx
            self.play_song(self.current_index)
        else:
            self._media_stop()

    def on_gapless_transition(self):
        print("Gapless transition triggered.")

        if 0 <= self.current_index < len(self.playlist_data):
            self._scrobble_complete(self.playlist_data[self.current_index].get('id'))

        # Keep motor running on gapless changes
        self._footer_panel.set_playing(True)

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

            # Pull the track from the playlist and use the 'track' variable
            track = self.playlist_data[self.current_index]
            target_path = track.get('stream_url') or track.get('path')
            needs_waveform = target_path and self._footer_panel.display_mode in (0, 2)

            # Reset BEFORE restoring cached bpm/beat-grid below — same
            # ordering fix as play_song(): reset_waveform() unconditionally
            # clears grid/metronome, so running it after the cache-hit
            # restore in load_current_track_metadata_text_only() silently
            # wiped that restore out whenever a gapless transition landed
            # on an already-played track.
            if needs_waveform:
                self._footer_panel.reset_waveform()

            # UI Updates
            self.load_current_track_metadata_text_only()
            self.update_indicator()
            self.update_window_title()

            if needs_waveform:
                track_id = str(track.get('id') or track.get('path'))
                is_scratch = self._footer_panel.display_mode == 0
                self._waveform_data_is_light = not is_scratch
                self.audio_engine.request_waveform(target_path, num_points=10000, track_id=track_id, light=not is_scratch)
                if is_scratch:
                    self.audio_engine.request_waveform_bands(target_path, num_points=10000, track_id=track_id)

            self.visual_update_timer.start(350)
            self.preload_next()
            self._cast_relay_track(track)
            self._refresh_queue_panel()
        else:
            self._media_stop()
    
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
        self.audio_engine.stop()

        self._footer_panel.set_playing(False)
        self._footer_panel.set_position_ms(0, hard=True)

        if getattr(self, 'visualizer', None):
            self.visualizer.reset()

        self.refresh_ui_styles(scroll_to_current=False)
        self.update_window_title()
        if hasattr(self, '_queue_panel'):
            self._queue_panel.update_playing_state(False)

    def get_next_index_calculated(self):
        visible_indices = self.get_visible_indices()
        if not visible_indices: return -1
        
        # If Repeat One is on, we don't preload a "next" song usually, 
        # but for gapless we might want to re-queue the same file.
        if self.is_repeat: 
            return self.current_index

        if self.is_shuffle:
            return self._next_shuffle_index(peek=True)

        # Sequential Logic
        if self.current_index in visible_indices:
            current_pos = visible_indices.index(self.current_index)
            next_pos = current_pos + 1
            if next_pos >= len(visible_indices):
                return -1  # last track, no repeat → stop
            return visible_indices[next_pos]
        else:
            return visible_indices[0]
   
    def get_visible_indices(self): 
        return [i for i in range(self.tree.topLevelItemCount()) if not self.tree.topLevelItem(i).isHidden()]
    
    def toggle_shuffle(self, on=None):
        self.is_shuffle = (not self.is_shuffle) if on is None else bool(on)
        self._footer_panel.set_shuffle(self.is_shuffle)
        if self.is_shuffle:
            self._rebuild_shuffle_queue()
        else:
            self._shuffle_queue.clear()
        self.refresh_ui_styles()
        self.preload_next()

    def toggle_repeat(self, on=None):
        self.is_repeat = (not self.is_repeat) if on is None else bool(on)
        self._footer_panel.set_repeat(self.is_repeat)
        self.refresh_ui_styles()
        self.preload_next()

    def handle_duration_change(self, duration):
        if duration > 0:
            self._footer_panel.set_duration_ms(duration)

    def update_ui_state(self, position):
        """Continuous decoder poll (~20-62Hz) — never a deliberate jump. QML
        is responsible for smoothing/extrapolating this between polls and
        guarantees it never displays backward (see footer_bar.qml's
        positionClock FrameAnimation); this just forwards the raw fact."""
        if time.time() < getattr(self, '_seek_settle_until', 0):
            return

        is_djing = self._footer_panel.is_dragging or self._footer_panel.is_spinning_freely
        if is_djing:
            return

        self._footer_panel.set_position_ms(position)

        if hasattr(self, '_queue_panel'):
            self._queue_panel.update_lyrics_position(int(position))

    def on_engine_position_jump(self, ms):
        """Deliberate discontinuity (seek/track-start/stop/loop) from
        AudioEngine.positionJumped — snaps the display exactly, and briefly
        suppresses update_ui_state so a stale pre-jump poll already in
        flight can't overwrite it."""
        self._seek_settle_until = time.time() + 1.0
        self._footer_panel.set_position_ms(ms, hard=True)

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
        ms = int(ms); total_sec = ms // 1000
        h, rem = divmod(total_sec, 3600)
        return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"
    
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
        self._cast_relay_seek(target_ms)

    def on_waveform_toggled(self, mode_int):
        """If user turns waveform back on mid-song, fetch the data on-demand."""
        # Modes 0 and 2 BOTH require audio waveform data! Scratch mode (0)
        # additionally needs full-density data, not the light fixed-size
        # decode bars/minimal use — see request_waveform's light= param. So
        # a light fetch already sitting in has_real_data from a prior
        # bars-mode visit must NOT count as satisfying scratch mode here,
        # or scratch would be stuck rendering off ~2000 points forever.
        needs_waveform = (mode_int in (0, 2))
        needs_upgrade_to_full = mode_int == 0 and getattr(self, '_waveform_data_is_light', False)

        if needs_waveform and (not self._footer_panel.has_real_data or needs_upgrade_to_full):
            if 0 <= self.current_index < len(self.playlist_data):
                track = self.playlist_data[self.current_index]
                target_path = track.get('stream_url') or track.get('path')
                if target_path:
                    self._footer_panel.reset_waveform()
                    track_id = str(track.get('id') or track.get('path'))
                    is_scratch = mode_int == 0
                    self._waveform_data_is_light = not is_scratch
                    self.audio_engine.request_waveform(target_path, num_points=10000, track_id=track_id, light=not is_scratch)
                    if is_scratch:
                        self.audio_engine.request_waveform_bands(target_path, num_points=10000, track_id=track_id)
                    return

        # Independent of the overall-waveform gate above: scratch mode's
        # band coloring needs samplesLow/Mid/High, which the overall-samples
        # fetch above never requests by itself — without this, switching
        # into scratch mode after the track's bars/minimal waveform already
        # loaded (the common case, since has_real_data above is already
        # true) would silently never fetch band data at all.
        if mode_int == 0 and not self._footer_panel.has_real_band_data:
            if 0 <= self.current_index < len(self.playlist_data):
                track = self.playlist_data[self.current_index]
                target_path = track.get('stream_url') or track.get('path')
                if target_path:
                    track_id = str(track.get('id') or track.get('path'))
                    self.audio_engine.request_waveform_bands(target_path, num_points=10000, track_id=track_id)
    
    def load_bpm_cache(self):
        """Loads the saved BPM dictionary from the app_data folder."""
        # 1. Safely find the directory, whether we are in dev mode or a frozen .exe!
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # 2. Build the target folder path: working_dir/app_data
        cache_dir = os.path.join(base_dir, "app_data")
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

    def load_beatgrid_cache(self):
        """Loads the saved beat-grid dictionary (track_id -> [beat_position_ms,
        ...]) from the app_data folder. Kept separate from bpm_cache so
        existing cached BPMs (from before the beat-grid feature existed)
        don't need their format migrated — see _perform_heavy_visual_update's
        gating, which re-runs BPMWorker once per track until both caches
        have it. Entries are filtered to lists only — an earlier version of
        this cache stored a single scalar anchor_ms per track instead of the
        full beat-position list; those stale entries are dropped here so
        they're treated as a cache miss and recomputed in the new format,
        rather than crashing downstream code expecting a list.

        File name bumped to _v2 — get_file_beat_grid switched from raw
        per-beat onset positions (later, a "snap to grid" hybrid) to a pure
        constant-tempo grid (matching Mixxx's actual default behavior).
        Both old formats are structurally identical (a list of floats), so
        there's no way to detect staleness from the data alone the way the
        scalar-vs-list check above can — a noisy old per-beat list's average
        interval can easily still fall within tolerance of the cached BPM.
        A clean file-name break forces every track to recompute once under
        the new algorithm, with no risk of silently keeping stale data."""
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cache_dir = os.path.join(base_dir, "app_data")
        os.makedirs(cache_dir, exist_ok=True)
        self.beatgrid_cache_file = os.path.join(cache_dir, "beatgrid_cache_v2.json")
        if os.path.exists(self.beatgrid_cache_file):
            try:
                with open(self.beatgrid_cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if isinstance(v, list)}
            except Exception:
                return {}
        return {}

    def save_beatgrid_cache(self):
        try:
            with open(self.beatgrid_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.beatgrid_cache, f)
        except Exception as e:
            print(f"Could not save beat-grid cache: {e}")

    def load_metronome_downbeat_cache(self):
        """Loads the saved per-track metronome downbeat-offset dictionary
        (track_id -> int 0-3) from the app_data folder. Per-track because
        the underlying problem it corrects — the beat detector's anchor
        landing on a noise transient instead of the real first beat of a
        bar — varies independently per track, same reasoning as
        beatgrid_cache/bpm_cache being per-track rather than a single
        global value."""
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cache_dir = os.path.join(base_dir, "app_data")
        os.makedirs(cache_dir, exist_ok=True)
        self.metronome_downbeat_cache_file = os.path.join(cache_dir, "metronome_downbeat_cache.json")
        if os.path.exists(self.metronome_downbeat_cache_file):
            try:
                with open(self.metronome_downbeat_cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return {k: int(v) % 4 for k, v in data.items() if isinstance(v, (int, float))}
            except Exception:
                return {}
        return {}

    def save_metronome_downbeat_cache(self):
        try:
            with open(self.metronome_downbeat_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.metronome_downbeat_cache, f)
        except Exception as e:
            print(f"Could not save metronome downbeat cache: {e}")

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
            self.bpm_cache[track_id] = bpm
            self.save_bpm_cache()

            # Update the track dict in playlist_data so it carries the detected value
            for t in self.playlist_data:
                if str(t.get('id') or t.get('path', '')) == track_id:
                    t['bpm'] = bpm
                    break

            # Push to tracks browser row if visible
            if hasattr(self, 'tracks_browser'):
                self.tracks_browser.refresh_track_bpm(track_id, bpm)

            # Push to the other tracklists too (album/playlist detail —
            # both the tab-embedded view AND the hidden "global" one used
            # when navigating in from elsewhere, e.g. Favorites — plus
            # favorites itself). Same live-update, no need to leave and
            # come back for it to show up.
            for attr in ('album_browser', 'playlists_browser'):
                detail = getattr(getattr(self, attr, None), 'detail_view', None)
                if detail:
                    detail.refresh_track_bpm(track_id, bpm)
            for attr in ('global_album_view', 'global_playlist_view', '_favorites_tab'):
                view = getattr(self, attr, None)
                if view:
                    view.refresh_track_bpm(track_id, bpm)

            current_track_id = str(self.playlist_data[self.current_index].get('id') or self.playlist_data[self.current_index].get('path'))
            if track_id == current_track_id:
                self.file_type_label.setText(f"{self.current_file_type_text}   •   {bpm:.1f} BPM")
                self._footer_panel.set_bpm(bpm)
        else:
            self.file_type_label.setText(self.current_file_type_text)

    def _on_beatgrid_calculated(self, bpm, beat_positions_ms, track_id):
        """Caches the real detected beat positions (see get_file_beat_grid
        in audio_core.cpp) and pushes them to the footer's waveform if it's
        still for the track currently playing — a worker for a since-skipped
        track can finish after the fact."""
        if not hasattr(self, 'beatgrid_cache'):
            self.beatgrid_cache = {}
        self.beatgrid_cache[track_id] = beat_positions_ms
        self.save_beatgrid_cache()

        current_track_id = str(self.playlist_data[self.current_index].get('id') or self.playlist_data[self.current_index].get('path'))
        if track_id == current_track_id:
            self._footer_panel.set_beatgrid(bpm, beat_positions_ms)

    def import_music(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Open Music Files", "", "Audio Files (*.mp3 *.flac *.wav *.ogg *.m4a)")
        if files:
            self.audio_engine.stop()
            self.playlist_data = []
            self.tree.clear()
            self.current_index = -1
            if hasattr(self, '_queue_tree_panel'):
                self._queue_tree_panel.clear_filter()
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
        self.setWindowTitle(f"Icosahedron {__version__}")
        for i in range(self.tree.topLevelItemCount()): self.tree.topLevelItem(i).setText(0, str(i + 1))
        self.refresh_ui_styles()
        self.update_indicator()
        if hasattr(self, '_queue_tree_panel'):
            self._queue_tree_panel.update_status()
        self._refresh_queue_panel()

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
        self._refresh_queue_panel()
      
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

    def _deferred_song_refresh(self):
        """Called after 200ms debounce — spawns metadata refresh only for the current track."""
        idx = self.current_index
        if not (0 <= idx < len(self.playlist_data)): return
        track_id = self.playlist_data[idx].get('id')
        if not track_id or not getattr(self, 'navidrome_client', None): return
        from player.workers import SongRefreshWorker
        old = getattr(self, '_song_refresh_worker', None)
        if old:
            try: old.refreshed.disconnect()
            except: pass
        self._song_refresh_worker = SongRefreshWorker(self.navidrome_client, track_id, idx)
        self._song_refresh_worker.refreshed.connect(self._on_song_refreshed)
        self._song_refresh_worker.start()

    def _on_song_refreshed(self, ridx, fresh):
        """Called on the main thread when SongRefreshWorker gets fresh metadata."""
        if ridx >= len(self.playlist_data):
            return
        entry = self.playlist_data[ridx]
        keys = ('title', 'artist', 'album', 'year', 'duration', 'coverArt', 'cover_id')
        changed = any(entry.get(k) != fresh.get(k) for k in ('title', 'artist', 'album', 'year'))
        if not changed:
            return
        entry.update({k: fresh.get(k, entry.get(k)) for k in keys})
        if self.current_index == ridx:
            self.load_current_track_metadata_text_only()
            self.update_window_title()
        # Patch any visible album detail track list
        for attr in ('global_album_view', 'album_browser'):
            view = getattr(self, attr, None)
            tl = getattr(view, 'track_list', None)
            if tl and hasattr(tl, 'refresh_track_item'):
                tl.refresh_track_item(str(fresh.get('id', '')), fresh)
    

    # ── Cast relay helpers ─────────────────────────────────────────────────────

    def _play_with_cast_sync(self, track):
        """Start PC + cast playback, synchronized when AirPlay 2 is active."""
        cm = getattr(self, '_cast_manager', None)
        if cm and getattr(cm, 'has_airplay2', lambda: False)():
            # Compute a future NTP start time so cliap2 can buffer ahead
            try:
                from player.components.airplay_manager import _ntp_now
                _NTP_PER_S = 1 << 32
                sync_ms = cm._AP2_SYNC_MS
                ntp_start = _ntp_now() + int(sync_ms / 1000.0 * _NTP_PER_S)
            except Exception:
                ntp_start = 0
                sync_ms   = 0
            import threading
            threading.Thread(
                target=lambda: cm.relay_track(track, ntp_start=ntp_start),
                daemon=True,
            ).start()
            if sync_ms > 0:
                # Delay PC audio so both sources start at the same moment
                QTimer.singleShot(sync_ms, lambda t=track: self.playback_manager.play_request(t))
            else:
                self.playback_manager.play_request(track)
        else:
            self.playback_manager.play_request(track)
            self._cast_relay_track(track)

    def _cast_relay_track(self, track):
        cm = getattr(self, '_cast_manager', None)
        if cm:
            import threading
            threading.Thread(target=lambda: cm.relay_track(track), daemon=True).start()

    def _cast_relay_pause(self):
        cm = getattr(self, '_cast_manager', None)
        if cm:
            cm.relay_pause()

    def _cast_relay_play(self):
        cm = getattr(self, '_cast_manager', None)
        if cm:
            cm.relay_play()

    def _cast_relay_seek(self, target_ms: int):
        cm = getattr(self, '_cast_manager', None)
        if cm:
            import threading
            threading.Thread(
                target=cm.relay_seek, args=(target_ms / 1000.0,), daemon=True
            ).start()

    def _cast_relay_volume(self, value: int):
        cm = getattr(self, '_cast_manager', None)
        if cm:
            import threading
            threading.Thread(
                target=cm.relay_volume, args=(value,), daemon=True
            ).start()
