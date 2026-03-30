"""
player/mixins/keyboard.py — keyPressEvent, eventFilter, and
all keyboard shortcut handlers.
"""
import os
import time
from PyQt6.QtWidgets import QApplication, QLineEdit, QSlider, QWidget
from PyQt6.QtCore import Qt, QPoint, QEvent, QItemSelectionModel

class KeyboardMixin:
    def keyPressEvent(self, event):
        nav_keys = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
        key = event.key()

        
        if event.key() == Qt.Key.Key_Escape:
            if hasattr(self, 'spotlight') and self.spotlight.isVisible():
                self.spotlight.hide()           
                event.accept()
                return
                      
        
        # ─── MEDIA KEYS (F5-F8) ────────────────────────────────────────────────
        
        # ⏹️ F5 / Media Stop
        if key in (Qt.Key.Key_F5, Qt.Key.Key_MediaStop):
            if hasattr(self, 'stop_song'): self.stop_song()
            elif hasattr(self, 'stop'): self.stop()
            event.accept(); return

        # ⏮️ F6 / Media Previous
        elif key in (Qt.Key.Key_F6, Qt.Key.Key_MediaPrevious):
            if hasattr(self, 'play_previous'): self.play_previous()
            elif hasattr(self, 'prev_song'): self.prev_song()
            event.accept(); return

        # ⏯️ F7 / Media Play-Pause (Handles all variants)
        elif key in (Qt.Key.Key_F7, Qt.Key.Key_MediaPlay, Qt.Key.Key_MediaPause, 
                    Qt.Key.Key_MediaTogglePlayPause):
            if hasattr(self, 'toggle_play_pause'): self.toggle_play_pause()
            elif hasattr(self, 'play_pause'): self.play_pause()
            event.accept(); return

        # ⏭️ F8 / Media Next
        elif key in (Qt.Key.Key_F8, Qt.Key.Key_MediaNext):
            if hasattr(self, 'play_next'): self.play_next()
            event.accept(); return

        # ─── THE SPOTLIGHT SEARCH TRIGGER ───────────────────────────────────────
        
        # ─── THE SPOTLIGHT SEARCH TRIGGER ───────────────────────────────────────
        from PyQt6.QtWidgets import QLineEdit, QApplication
        active_widget = QApplication.focusWidget()
        
        # Only trigger if they aren't already typing in a standard search bar or capturing a hotkey
        if not isinstance(active_widget, QLineEdit) and not getattr(active_widget, '_capturing', False) and hasattr(self, 'spotlight') and not self.spotlight.isVisible():
            text = event.text()
            # If the key pressed is a printable character (A-Z, 0-9) without Ctrl/Alt
            if text and text.isprintable() and text != "/" and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
                self.spotlight.show_search(initial_char=text)
                
                # Z-ORDER FIX:
                self.spotlight.raise_()
                self.spotlight.activateWindow()
                event.accept()
                return

        # ─── BROWSER & GRID KEYBOARD LOGIC ───────────────────────────────────────

        # ESCAPE: Collapse the local search bar on any tab, even when focus is on the grid/tree
        if key == Qt.Key.Key_Escape:
            current_widget = self.tabs.currentWidget()
            if current_widget and hasattr(current_widget, 'search_container'):
                sc = current_widget.search_container
                if hasattr(sc, 'search_input') and sc.search_input.maximumWidth() > 0:
                    sc.search_input.clear()
                    sc.collapse()
                    event.accept()
                    return

        # BACKSPACE: File-explorer style "Go Back"
        if key == Qt.Key.Key_Backspace:
            self.go_back()
            event.accept()
            return

        current_widget = self.tabs.currentWidget()
        active_tree = None
        active_grid = None
        is_now_playing = (self.tabs.currentWidget() is self._now_playing_panel)

        # 1. THE DRILL-DOWN: Find EXACTLY what is active on the screen right now!
        if is_now_playing:
            active_tree = self.tree
        elif hasattr(current_widget, 'stack'):
            stack_idx = current_widget.stack.currentIndex()
            if stack_idx == 0 and hasattr(current_widget, 'grid_view'):
                active_grid = current_widget.grid_view
            elif stack_idx == 1 and hasattr(current_widget, 'detail_view'):
                active_tree = current_widget.detail_view.track_list.tree
            elif stack_idx == 2 and hasattr(current_widget, 'artist_view'):
                from PyQt6.QtWidgets import QListWidget
                focus_w = QApplication.focusWidget()
                
                # Check if a list ALREADY has focus
                if isinstance(focus_w, QListWidget):
                    active_grid = focus_w
                else:
                    # THE FIX 3: If focus was lost to the background, forcefully find the first active album row!
                    active_grid = None
                    for lw in self._artist_view_lists(current_widget):
                        active_grid = lw
                        active_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                        if active_grid.currentRow() < 0:
                            active_grid.setCurrentRow(0)
                        break
                    
                    # If we STILL didn't find one (empty artist), fallback to just clicking the main play button
                    if not active_grid:
                        if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and
                                event.modifiers() & Qt.KeyboardModifier.ShiftModifier and
                                not event.isAutoRepeat()):
                            current_widget.artist_view.btn_play.animateClick()
                            event.accept()
                            return
        elif hasattr(current_widget, 'tree'):
            active_tree = current_widget.tree
        
        # 2. THE INSTANT FAILSAFE:
        if key in nav_keys:
            if active_grid and not active_grid.hasFocus():
                active_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                if active_grid.count() > 0 and not active_grid.currentItem():
                    from PyQt6.QtCore import QItemSelectionModel
                    active_grid.setCurrentItem(active_grid.item(0), QItemSelectionModel.SelectionFlag.ClearAndSelect)
                
            if active_tree and not active_tree.hasFocus():
                active_tree.setFocus(Qt.FocusReason.ShortcutFocusReason)
                if active_tree.topLevelItemCount() > 0 and not active_tree.currentItem():
                    from PyQt6.QtCore import QItemSelectionModel
                    first_item = active_tree.topLevelItem(0)
                    
                    active_tree.setCurrentItem(first_item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)

        # --- 3. GRID VIEW NATIVE FORWARDING & CRASH PREVENTION ---
        if active_grid:
            # Explicitly list the keys here instead of using 'nav_keys'
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                if active_grid.hasFocus():
                    old_row = active_grid.currentRow()
                    
                    # Inject the key press directly to avoid Qt bubbling bugs
                    active_grid.keyPressEvent(event)
                    
                    new_row = active_grid.currentRow()
                    
                    # Magic Jump between category rows inside Artist Detail View!
                    if old_row == new_row and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                        if hasattr(current_widget, 'artist_view') and hasattr(current_widget.stack, 'currentIndex') and current_widget.stack.currentIndex() == 2:
                            lists = self._artist_view_lists(current_widget)

                            if active_grid in lists:
                                current_idx = lists.index(active_grid)
                                next_idx = current_idx + (1 if key == Qt.Key.Key_Down else -1)
                                if 0 <= next_idx < len(lists):
                                    active_grid.clearSelection() # Clean up old highlight
                                    next_grid = lists[next_idx]
                                    next_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                                    next_grid.setCurrentRow(0) # Jump to the beginning of the next row
                                    
                    event.accept()
                    return
                else:
                    active_grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
                    if active_grid.count() > 0 and not active_grid.currentItem():
                        active_grid.setCurrentRow(0)
                        
                    active_grid.keyPressEvent(event)
                    event.accept()
                    return
                    
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.isAutoRepeat():          
                    event.accept()                
                    return
                
                curr_item = active_grid.currentItem()
                if curr_item:
                    data = curr_item.data(Qt.ItemDataRole.UserRole)
                    if data:
                        stack_idx = current_widget.stack.currentIndex() if hasattr(current_widget, 'stack') else 0
                        
                        # SHIFT + ENTER: Play the highlighted Grid Item immediately!
                        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            if current_widget is self.album_browser: 
                                self.play_whole_album(data) 
                            elif current_widget is self.artist_browser: 
                                if stack_idx == 0: 
                                    artist_name = data.get('name') or data.get('artist')
                                    if artist_name:
                                        self.play_artist_by_name(artist_name)
                                elif stack_idx == 2: 
                                    self.play_whole_album(data)
                                    
                        # REGULAR ENTER: Navigate to the detail view
                        else:
                            if current_widget is self.album_browser: 
                                self.navigate_to_album(data)
                            elif current_widget is self.artist_browser: 
                                if stack_idx == 0:
                                    self.navigate_to_artist(data)
                                elif stack_idx == 2:
                                    self.navigate_to_album(data)
                event.accept()
                return

        # --- 4. LIST VIEW LOGIC (1D Movement) ---
        elif active_tree:
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_A:
                active_tree.selectAll(); event.accept(); return

            if is_now_playing and key == Qt.Key.Key_Delete:
                self.delete_selected_tracks(); event.accept(); return

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.isAutoRepeat():
                    event.accept()
                    return

                # SHIFT + ENTER: Click the detail view's Play button (album or tracks browser)
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if not is_now_playing:
                        # If we're inside a browser's detail view, trigger its Play button.
                        # We do NOT call play_whole_album() directly because the detail view
                        # button wires up the correct signals (shuffle state, queue, etc.).
                        if hasattr(current_widget, 'detail_view') and hasattr(current_widget.detail_view, 'btn_play'):
                            current_widget.detail_view.btn_play.animateClick()
                        elif active_tree.topLevelItemCount() > 0:
                            # Fallback: plain tracks view with no detail header
                            all_tracks = []
                            for i in range(active_tree.topLevelItemCount()):
                                item = active_tree.topLevelItem(i)
                                data = item.data(0, Qt.ItemDataRole.UserRole)
                                if data:
                                    all_tracks.append(data)
                            if all_tracks:
                                self.play_whole_album(all_tracks)
                    event.accept()
                    return

                # REGULAR ENTER: Play just the single highlighted track
                curr_item = active_tree.currentItem()
                if curr_item:
                    if is_now_playing:
                        idx = active_tree.indexOfTopLevelItem(curr_item)
                        if idx != -1: self.play_song(idx)
                    else:
                        data = curr_item.data(0, Qt.ItemDataRole.UserRole)
                        if data: 
                            self.add_and_play_from_browser(data)
                event.accept()
                return

            if key in nav_keys:
                curr_item = active_tree.currentItem()
                curr_index = active_tree.indexOfTopLevelItem(curr_item) if curr_item else -1
                visible_indices = [i for i in range(active_tree.topLevelItemCount()) if not active_tree.topLevelItem(i).isHidden()]
                
                if not visible_indices: event.accept(); return

                try: current_vis_pos = visible_indices.index(curr_index)
                except ValueError: current_vis_pos = -1
                
                target_vis_pos = current_vis_pos
                items_per_page = max(1, active_tree.viewport().height() // 55)

                if key == Qt.Key.Key_Up:
                    if current_vis_pos > 0: target_vis_pos = current_vis_pos - 1
                    elif current_vis_pos == -1: target_vis_pos = 0 
                elif key == Qt.Key.Key_Down:
                    if current_vis_pos < len(visible_indices) - 1: target_vis_pos = current_vis_pos + 1
                    elif current_vis_pos == -1: target_vis_pos = 0
                elif key == Qt.Key.Key_PageUp:
                    target_vis_pos = max(0, current_vis_pos - items_per_page)
                elif key == Qt.Key.Key_PageDown:
                    target_vis_pos = min(len(visible_indices) - 1, current_vis_pos + items_per_page)
                    if current_vis_pos == -1: target_vis_pos = 0

                if target_vis_pos != -1 and target_vis_pos != current_vis_pos:
                    real_index = visible_indices[target_vis_pos]
                    item = active_tree.topLevelItem(real_index)
                    
                    from PyQt6.QtCore import QItemSelectionModel
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        active_tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                    else:
                        active_tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
                    active_tree.scrollToItem(item)
                event.accept(); return

        super().keyPressEvent(event)
     
    def eventFilter(self, source, event):
        # Grab the fast integer type immediately
        e_type = event.type()
        
        # Instantly ignore 99% of events (Paint, Timer, MouseMove) to save CPU and stop the audio stutter!
        if e_type not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride, QEvent.Type.ToolTip, QEvent.Type.Leave):
            return super().eventFilter(source, event)
            
        # --- The remaining 1% of events are safe to run Python logic on ---

        # 1. GLOBAL BOUNCER: Spotlight Search Lockdown
        if hasattr(self, 'spotlight') and self.spotlight.isVisible():
            if e_type in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
                key = event.key()
                
                # BLOCK TAB SWITCHING
                if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    return True # Eat the event completely!
                
                # GLOBAL ESCAPE: Close the spotlight instantly
                if key == Qt.Key.Key_Escape:
                    if e_type == QEvent.Type.KeyPress: 
                        self.spotlight.hide()
                    return True # Eat the event

        # 2. GLOBAL TYPE-TO-SEARCH INTERCEPTOR
        if e_type == QEvent.Type.KeyPress and hasattr(self, 'spotlight') and not self.spotlight.isVisible():
            from PyQt6.QtWidgets import QLineEdit, QApplication
            focus_widget = QApplication.focusWidget()
            
            # Only intercept if they are NOT already typing inside a local search box or capturing a hotkey
            if not isinstance(focus_widget, QLineEdit) and not getattr(focus_widget, '_capturing', False):
                key = event.key()
                text = event.text()
                
                # Ignore Space (Play/Pause), Slash (Local Search), Enter, and Modifiers
                if text and text.isprintable() and key not in (Qt.Key.Key_Space, Qt.Key.Key_Slash, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
                        self.spotlight.show_search(text)
                        
                        # Z-ORDER FIX:
                        self.spotlight.raise_()
                        self.spotlight.activateWindow()
                        return True

        # 3. TOOLTIP LOGIC
        if e_type == QEvent.Type.ToolTip:
            from PyQt6.QtWidgets import QWidget, QSlider, QApplication
            if isinstance(source, QWidget) and source.toolTip():
                if isinstance(source, QSlider): return False

                source_window = source.window()
                if source_window is not self:
                    # Lazily create a TriangleTooltip parented to the source's window
                    # so it naturally stacks above it (e.g. spotlight overlay).
                    if not hasattr(source_window, '_kbd_tooltip'):
                        from player.widgets import TriangleTooltip
                        source_window._kbd_tooltip = TriangleTooltip(source_window, show_triangle=False)
                    tip = source_window._kbd_tooltip
                else:
                    tip = self.generic_tooltip

                tip.setText(source.toolTip())
                tip.adjustSize()

                rect = source.rect()
                global_point = source.mapToGlobal(QPoint(rect.width() // 2, -10))
                tip_w = tip.width()
                tip_h = tip.height()
                tip_x = global_point.x() - tip_w // 2
                tip_y = global_point.y() - tip_h

                screen_rect = QApplication.instance().primaryScreen().availableGeometry()
                if tip_y < screen_rect.y():
                    tip_y = source.mapToGlobal(QPoint(0, rect.height() + 5)).y()
                tip_x = max(screen_rect.x(), min(tip_x, screen_rect.right() - tip_w))

                tip.move(tip_x, tip_y)
                tip.show()
                tip.raise_()
                return True
                
        elif e_type == QEvent.Type.Leave:
            from PyQt6.QtWidgets import QWidget
            if isinstance(source, QWidget) and source.toolTip():
                source_window = source.window()
                if source_window is not self and hasattr(source_window, '_kbd_tooltip'):
                    source_window._kbd_tooltip.hide()
                elif hasattr(self, 'generic_tooltip'):
                    self.generic_tooltip.hide()
                    
        return super().eventFilter(source, event)
    
    def _artist_view_lists(self, current_widget):
        """Returns all navigable QListWidgets in the artist detail view, in order."""
        lists = []
        av = getattr(current_widget, 'artist_view', None)
        if not av:
            return lists
        for i in range(av.sections_layout.count()):
            row = av.sections_layout.itemAt(i).widget()
            if row and hasattr(row, 'list_widget') and row.list_widget.count() > 0:
                lists.append(row.list_widget)
        related = getattr(av, 'related_artists_row', None)
        if related and related.list_widget.count() > 0:
            lists.append(related.list_widget)
        return lists

    def focusNextPrevChild(self, next):
        """Globally disables the Tab and Shift+Tab keys from randomly moving focus between widgets!"""
        return False
    
    def handle_space_shortcut(self):
        self.toggle_playback()
       
    def handle_arrow_shortcut(self, step_ms):
        if self.audio_engine.total_ms > 0:
            current_ms = self.seek_bar.position_ms
            total_ms = self.audio_engine.total_ms
            new_ms = max(0, min(current_ms + step_ms, total_ms))
            
            is_pending = 0
            if hasattr(self.audio_engine, 'lib'):
                 is_pending = self.audio_engine.lib.is_transition_pending()

            time_remaining = total_ms - current_ms
            is_unsafe_zone = (time_remaining < 11000) 
            
            if is_pending == 1 or is_unsafe_zone:
                if 0 <= self.current_index < len(self.playlist_data):
                    track = self.playlist_data[self.current_index]
                    if track.get('path') and os.path.exists(track['path']):
                        self.audio_engine.load_track(track['path'])
            
            self.audio_engine.seek(new_ms)
            self.seek_bar.update_position(new_ms)
            
            if hasattr(self, 'current_time_label'):
                self.current_time_label.setText(self.format_time(new_ms))
            
            self.queued_next_index = -1 
            self.preload_next()
            self.ignore_updates_until = time.time() + 1.0
            self.last_engine_pos = new_ms
            self.last_engine_update_time = time.time()
    
