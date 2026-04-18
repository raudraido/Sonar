"""
player/mixins/visuals.py — Background blur, cover art crossfade,
dynamic theming, row highlighting, and volume icon updates.
"""
import os
import sys
import time

from PyQt6.QtWidgets import QApplication, QAbstractItemView, QLabel, QListWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPixmap, QPainter

from player import resource_path
from player.workers import BlurWorker, BPMWorker, CoverLoaderWorker

class VisualsMixin:
    def update_background_threaded(self, path, calc_color=True, raw_data_override=None):
        if self.blur_thread and self.blur_thread.isRunning():
            try: self.blur_thread.finished.disconnect()
            except: pass
            self.blur_thread.quit()

        
        art_size = min(self.art_container.width(), self.art_container.height()) if hasattr(self, 'art_container') else 500

        self.blur_thread = BlurWorker(
            path, 
            self.visual_settings['blur'], 
            self.visual_settings['overlay'],
            self.master_color,
            calc_color,
            raw_data_override=raw_data_override,
            target_size=self.size(),
            art_size=art_size
        )
        
        self.blur_thread.finished.connect(self.apply_threaded_art)
        self.blur_thread.start()

    def apply_threaded_art(self, blurred_qimg, cover_qimg, raw_art, dominant_color):
        self.old_cover_pixmap = getattr(self, 'current_cover_pixmap', None)
        if hasattr(self, 'bg_label') and self.bg_label.pixmap() and not self.bg_label.pixmap().isNull():
            self.bg_label_old.setPixmap(self.bg_label.pixmap())
        else:
            self.bg_label_old.setPixmap(QPixmap())
            
        self.current_cover_pixmap = QPixmap()
        if not cover_qimg.isNull():
            self.current_cover_pixmap = QPixmap.fromImage(cover_qimg)
            
        # Store cover_id instead of raw bytes — CoverCache already has the data on disk
        self.current_raw_art = raw_art
        
        if self.dynamic_color:
            self.master_color = dominant_color
            if hasattr(self, 'visualizer'): self.visualizer.bar_color = QColor(self.master_color)
            if hasattr(self, '_queue_panel'): self._queue_panel.set_accent_color(self.master_color)
            if 0 <= self.current_index < len(self.playlist_data):
                track = self.playlist_data[self.current_index]
                raw_artist = track.get('artist', 'Unknown')
                formatted_artist = raw_artist.replace(" /// ", f" <span style='color:{self.master_color}; font-size:24px'>•</span> ")
                year = str(track.get('year', '') or '').strip()
                if year and year != '0':
                    formatted_artist += f"  •  {year}"
                self.track_artist.setText(formatted_artist)
                if hasattr(self, 'heart_btn'):
                    raw_state = track.get('starred')
                    is_fav = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)
                    self.heart_btn.setIcon(self._make_heart_icon(is_fav, self.master_color))
            self.refresh_ui_styles(scroll_to_current=False)

        if not self.current_cover_pixmap.isNull():
            self.now_playing_widget.set_cover(self.current_cover_pixmap)
        else:
            self.now_playing_widget.set_cover(None)

        if not getattr(self, 'static_bg_path', None):
            if not blurred_qimg.isNull():
                bg_pix = QPixmap.fromImage(blurred_qimg)
                self.bg_label.setPixmap(bg_pix)
            else:
                self.bg_label.setPixmap(QPixmap())

        if hasattr(self, 'art_container'):
            self.art_container.scaled_cache = {}

        # Start the crossfade: fade bg_label in over the old frame in bg_label_old
        if not getattr(self, 'static_bg_path', None) and hasattr(self, 'fade_anim'):
            if self.fade_anim.state() == QPropertyAnimation.State.Running:
                self.fade_anim.stop()
            # Recreate the effect each time — Qt deletes the C++ object when
            # setGraphicsEffect(None) is called, leaving a dangling wrapper.
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            self.opacity_effect = QGraphicsOpacityEffect(self.bg_label)
            self.opacity_effect.setOpacity(0.0)
            self.bg_label.setGraphicsEffect(self.opacity_effect)
            self.fade_anim.setTargetObject(self.opacity_effect)
            self.crossfade_progress = 0.0
            self.fade_anim.start()
        else:
            # Fallback: no animation available, switch instantly
            self.crossfade_progress = 1.0
            self.old_cover_pixmap = None
            if hasattr(self, 'bg_label_old'):
                self.bg_label_old.setPixmap(QPixmap())
            self.bg_label.setGraphicsEffect(None)

        if hasattr(self, 'art_container'):
            self.art_container.update()

        import gc; gc.collect()

    def apply_cover_art(self, data):
        self.update_background_threaded(None, raw_data_override=data)

    def apply_static_background(self):
        """Render the static bg image through BlurWorker and lock it in."""
        path = getattr(self, 'static_bg_path', None)
        if not path or not os.path.exists(path):
            return
        worker = BlurWorker(
            path,
            self.visual_settings['blur'],
            self.visual_settings['overlay'],
            self.master_color,
            calc_color=False,
            target_size=self.size(),
        )
        def _apply(blurred_qimg, *_):
            if not blurred_qimg.isNull():
                self.bg_label_old.setPixmap(QPixmap())
                self.bg_label.setPixmap(QPixmap.fromImage(blurred_qimg))
                self.bg_label.setGraphicsEffect(None)
        worker.finished.connect(_apply)
        worker.start()
        self._static_bg_worker = worker  # keep reference

    def _perform_heavy_visual_update(self):
        """Called by timer when user has stopped skipping tracks."""
        if not (0 <= self.current_index < len(self.playlist_data)): return
        
        track = self.playlist_data[self.current_index]
        cid = track.get('cover_id') or track.get('coverArt') or track.get('albumId')

        # Skip if this exact cover was just rendered
        if cid and cid == getattr(self, '_last_rendered_cid', None):
            return
        self._last_rendered_cid = cid

        # 1. Trigger the heavy Blur/Color calculation
        if cid and hasattr(self, 'navidrome_client'):
             cover_id = track.get('cover_id') or track.get('coverArt') or track.get('albumId')
             
             
             if getattr(self, 'cover_loader', None) and self.cover_loader.isRunning():
                 self._safe_discard_worker(self.cover_loader)
                 
             self.cover_loader = CoverLoaderWorker(self.navidrome_client, cover_id)
             self.cover_loader.finished.connect(self.apply_cover_art)
             self.cover_loader.start()
        elif track.get('path'):
            self.update_background_threaded(track['path'])
        else:
            self.update_background_threaded(None)        
   
    def _on_fade_step(self, value):
        self.crossfade_progress = value
        if hasattr(self, 'art_container'):
            self.art_container.update()
       
    def _on_fade_finished(self):
        """Release the old frame's memory once the crossfade is done."""
        self.old_cover_pixmap = None
        if hasattr(self, 'bg_label_old'):
            self.bg_label_old.setPixmap(QPixmap())
            
        
        self.bg_label.setGraphicsEffect(None) 
        import gc; gc.collect()

    def _apply_bg_scale(self):
        """Deferred by _resize_debounce — runs once after the user stops resizing."""
        sz = self.size()
        if self.fade_anim.state() == QPropertyAnimation.State.Running:
            return  # don't fight the crossfade animation
        def _crop_scale(pixmap, sz):
            scaled = pixmap.scaled(sz, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            cx = (scaled.width() - sz.width()) // 2
            cy = (scaled.height() - sz.height()) // 2
            return scaled.copy(cx, cy, sz.width(), sz.height())
        if hasattr(self, 'bg_label_old') and self.bg_label_old.pixmap() and not self.bg_label_old.pixmap().isNull():
            self.bg_label_old.setPixmap(_crop_scale(self.bg_label_old.pixmap(), sz))
        if hasattr(self, 'bg_label') and self.bg_label.pixmap() and not self.bg_label.pixmap().isNull():
            self.bg_label.setPixmap(_crop_scale(self.bg_label.pixmap(), sz))

    def refresh_ui_styles(self, scroll_to_current=True):
        mc = self.master_color
        alpha = self.visual_settings['bg_alpha'] 
        rgb = QColor(mc)

        if not hasattr(self, 'icon_cache'):
            self.icon_cache = {}
            
        if not hasattr(self, 'base_pixmap_cache'):
            self.base_pixmap_cache = {}

        def get_cached_icon(icon_name, color):
            key = (icon_name, color)
            if key in self.icon_cache: return self.icon_cache[key]
            
            if icon_name not in self.base_pixmap_cache:
                path = resource_path(icon_name)
                if not os.path.exists(path): return QIcon()
                self.base_pixmap_cache[icon_name] = QPixmap(path)
                
            pixmap = self.base_pixmap_cache[icon_name]
            colored = QPixmap(pixmap.size()); colored.fill(QColor(0,0,0,0))
            painter = QPainter(colored); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(colored.rect(), QColor(color))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawPixmap(0, 0, pixmap); painter.end()
            
            icon = QIcon(colored)
            if len(self.icon_cache) >= 200:
                # Evict oldest quarter when full
                for old_key in list(self.icon_cache.keys())[:50]:
                    del self.icon_cache[old_key]
            self.icon_cache[key] = icon
            return icon

        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            raw_artist = track.get('artist', 'Unknown')
            formatted_artist = raw_artist.replace(" /// ", f" <span style='color:{mc}; font-size:24px'>•</span> ")
            year = str(track.get('year', '') or '').strip()
            if year and year != '0':
                formatted_artist += f"  •  {year}"
            self.track_artist.setText(formatted_artist)
            if hasattr(self, 'heart_btn'):
                raw_state = track.get('starred')
                is_fav = raw_state.lower() in ('true', '1') if isinstance(raw_state, str) else bool(raw_state)
                self.heart_btn.setIcon(self._make_heart_icon(is_fav, mc))

        # Only walk the tree and repaint the indicator row when the playing track or
        # accent color actually changed — skips the expensive tree scan on volume/tab events.
        is_playing = self.audio_engine.is_playing if hasattr(self, 'audio_engine') else False
        indicator_key = (self.current_index, mc, is_playing)
        
        if getattr(self, '_last_indicator_key', None) != indicator_key:
            self._last_indicator_key = indicator_key
            self.update_indicator(scroll_to_current=scroll_to_current)

        self.btn_play.setIcon(get_cached_icon("img/pause.png" if self.audio_engine.is_playing else "img/play.png", "#111111"))

        
        footer_alpha = self.visual_settings.get('footer_alpha', 0.85)
        queue_alpha  = self.visual_settings.get('queue_alpha', 0.96)
        theme_key = f"{mc}_{alpha}_{footer_alpha}_{queue_alpha}"
        
        if getattr(self, '_last_theme_key', None) == theme_key:
            return 
            
        self._last_theme_key = theme_key

        if hasattr(self, 'visualizer'): self.visualizer.bar_color = QColor(mc)

        self.btn_shuffle.master_color = mc; self.btn_repeat.master_color = mc
        self.btn_shuffle.setIcon(get_cached_icon("img/shuffle.png", mc)); self.btn_repeat.setIcon(get_cached_icon("img/repeat.png", mc))

        vol_img = "img/volume_mute.png" if self.is_muted else "img/volume.png"
        self.vol_icon_label.setPixmap(get_cached_icon(vol_img, "#888888" if self.is_muted else mc).pixmap(24, 24))

        for btn, icon_name in [(self.settings_btn, "img/settings.png"), (self.import_btn, "img/import.png"), (self.btn_prev, "img/prev.png"), (self.btn_next, "img/next.png")]:
            btn.setIcon(get_cached_icon(icon_name, mc))

        active_tab = self.tabs.currentWidget()
        if hasattr(active_tab, 'set_accent_color'):
            active_tab.set_accent_color(mc, alpha)
            
        if not getattr(self, '_tab_hook_set', False):
            self.tabs.currentChanged.connect(lambda: self.tabs.currentWidget().set_accent_color(self.master_color, self.visual_settings['bg_alpha']) if hasattr(self.tabs.currentWidget(), 'set_accent_color') else None)
            self._tab_hook_set = True

        # THE MAGICAL CSS TRICK (Inside refresh_ui_styles)
        tabs_css = f"""
            QTabWidget::pane {{ border: 0; }} 
            QTabWidget::tab-bar {{ alignment: left; }} /* Removed 'left: 15px' */
            QTabBar::tab {{ 
                background: #111; 
                color: #555; 
                padding: 10px 20px; /* Restored to your original preference */
                border: none; 
                font-family: 'sans-serif', sans-serif; 
                font-weight: bold; 
                font-size: 13px; 
                border-top-left-radius: 5px; 
                border-top-right-radius: 5px; 
                margin-right: 4px;
            }}
            QTabBar::tab:selected {{ color: {mc}; background: #181818; border-bottom: 2px solid {mc}; }}
            QTabBar::tab:hover {{ color: #888; background: #222; }}
        """

        toggle_style = "QPushButton { background: transparent; border: none; border-radius: 20px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }"
        self.btn_shuffle.setStyleSheet(toggle_style); self.btn_repeat.setStyleSheet(toggle_style)
        
        side_btn_style = "QPushButton { background: transparent; border: none; border-radius: 20px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }"
        for btn in [self.settings_btn, self.import_btn, self.btn_prev, self.btn_next]: btn.setStyleSheet(side_btn_style)

        # 2. Define the Buttons CSS
        modern_dark_style = f"""
            QPushButton {{ 
                background: #111; 
                color: #888; 
                border: none; 
                border-radius: 5px; 
                font-family: 'sans-serif', sans-serif; 
                font-weight: 900; 
                font-size: 16px; 
                min-width: 30px;   
                min-height: 28px;  
            }} 
            QPushButton:hover {{ 
                background: #222; 
                color: {mc}; 
            }}
            QPushButton:disabled {{
                background: #0a0a0a; 
                color: #333;         
            }}
        """

        # 3. DEFER BOTH STYLES! This prevents the 30ms layout freeze!
        def apply_deferred_styles():
            
            grids_to_save = []
            areas_to_save = []
            
            # 1. Capture the scroll positions of ALL grids and scroll areas before the CSS nuke
            for tab_idx in range(self.tabs.count()):
                widget = self.tabs.widget(tab_idx)
                
                if hasattr(widget, 'grid_view'):
                    grid = widget.grid_view
                    grids_to_save.append({
                        'grid': grid,
                        'scroll': grid.verticalScrollBar().value()
                    })
                    # Disable Batched mode temporarily so max-height calculation is instant & accurate
                    from PyQt6.QtWidgets import QListWidget
                    grid.setLayoutMode(QListWidget.LayoutMode.SinglePass)
                    
                if hasattr(widget, 'scroll_area'):
                    area = widget.scroll_area
                    areas_to_save.append({
                        'area': area,
                        'scroll': area.verticalScrollBar().value()
                    })

            # 2. Apply the CSS (This causes Qt to violently reset all layouts)
            self.tabs.setStyleSheet(tabs_css)

            # 3. Restore all the scrollbars instantly
            for state in grids_to_save:
                grid = state['grid']
                grid.doItemsLayout() # Force rebuild right now
                grid.verticalScrollBar().setValue(state['scroll'])
                from PyQt6.QtWidgets import QListWidget
                grid.setLayoutMode(QListWidget.LayoutMode.Batched) # Restore performance mode
                
            for state in areas_to_save:
                area = state['area']
                area.verticalScrollBar().setValue(state['scroll'])
            # ------------------------------------------------

            if hasattr(self, 'btn_back'):
                self.btn_back.setStyleSheet(modern_dark_style)
                self.btn_fwd.setStyleSheet(modern_dark_style)

        # Apply the Footer Opacity Dynamically!
        footer_alpha = self.visual_settings.get('footer_alpha', 0.85)
        self.footer_container.setStyleSheet(f"QWidget#FooterBar {{ background-color: rgba(11, 11, 11, {footer_alpha}); border-top: 1px solid rgba(255, 255, 255, 0.1); }}")

        # Apply Queue Panel Opacity
        if hasattr(self, '_queue_panel'):
            self._queue_panel.setStyleSheet(
                f'#QueuePanel {{'
                f'  background: rgba(14,14,14,{queue_alpha});'
                f'  border: 1px solid rgba(255,255,255,0.10);'
                f'  border-radius: 10px;'
                f'}}'
            )

              
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, apply_deferred_styles)

        # (The rest of the fast footer styles stay exactly the same)
        self.btn_play.setStyleSheet(f"QPushButton {{ background: {mc}; border-radius: 32px; border: none; }} QPushButton:hover {{ background: white; }}")
        
        slider_style = f"QSlider::groove:horizontal {{ background: #333; height: 5px; border-radius: 2px; }} QSlider::handle:horizontal {{ background: {mc}; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0; }} QSlider::sub-page:horizontal {{ background: {mc}; }}"
        self.vol_slider.setStyleSheet(slider_style)
        self.seek_bar.set_master_color(mc)

        timer_style = f"color: {mc}; font-family: 'sans-serif', sans-serif; font-size: 14px; font-weight: bold; background: transparent;"
        self.current_time_label.setStyleSheet(timer_style); self.total_time_label.setStyleSheet(timer_style)

        if getattr(self, '_last_tree_alpha', None) != alpha:
            if hasattr(self, '_now_playing_panel'):
                self._now_playing_panel.set_accent_color(mc, alpha)
            self._last_tree_alpha = alpha

        pass  # Tooltip styling handled by _TooltipFilter in window.py

    def refresh_visuals(self):
        self.refresh_ui_styles()
        if getattr(self, 'static_bg_path', None):
            self.apply_static_background()
            return
        track_path = None
        if 0 <= self.current_index < len(self.playlist_data): track_path = self.playlist_data[self.current_index].get('path')
        self.update_background_threaded(track_path, raw_data_override=self.current_raw_art)

    def update_indicator(self, scroll_to_current=True):
        """Updates the dancing GIF, row highlight, and broadcasts the playing state to all tabs."""
        if not hasattr(self, 'tree') or not self.tree:
            return
            
                    
        mc = self.master_color
        rgb = QColor(mc)

        # Define styles for the active and inactive rows
        highlight_bg = QColor(rgb.red(), rgb.green(), rgb.blue(), 40)
        default_color = QColor("#ddd")
        transparent = QColor(0, 0, 0, 0)
        
        normal_font = QFont("sans-serif", 10)
        bold_font = QFont("sans-serif", 10)
        bold_font.setBold(True)
        
        target_item = None
        is_playing = hasattr(self, 'audio_engine') and self.audio_engine.is_playing

        # --- 1. UPDATE THE MAIN "NOW PLAYING" QUEUE ---
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            
            if i == getattr(self, 'current_index', -1):
                target_item = item
                
                # Apply the dancing GIF or numbers
                if is_playing and hasattr(self, 'playing_movie'):
                    item.setText(0, "")          # ← clear track number first
                    pi_label = QLabel()
                    pi_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    pi_label.setStyleSheet("background: transparent;")
                    pi_label.setMovie(self.playing_movie)
                    # no QGraphicsColorizeEffect
                    self.tree.setItemWidget(item, 0, pi_label)
                    self.playing_movie.start()
                else:
                    if self.tree.itemWidget(item, 0):
                        self.tree.removeItemWidget(item, 0)
                        if hasattr(self, 'playing_movie'):
                            self.playing_movie.stop()
                    item.setText(0, str(i + 1))
                
                # Apply the row highlight and bold text
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, highlight_bg)
                    if col != 7:  # Skip the Heart/Favorite column
                        item.setForeground(col, rgb)
                        item.setFont(col, bold_font)
            else:
                # Clear styles for non-playing rows
                if self.tree.itemWidget(item, 0):
                    self.tree.removeItemWidget(item, 0)
                item.setText(0, str(i + 1))
                
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, transparent)
                    if col != 7:  # Skip the Heart/Favorite column
                        item.setForeground(col, default_color)
                        item.setFont(col, normal_font)
                        
        # Auto-scroll the Now Playing queue if requested
        if target_item and scroll_to_current: 
            self.tree.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtCenter)
            
        self.last_index = getattr(self, 'current_index', -1)


        # --- 2. GLOBAL GIF SYNC (Broadcast to Albums, Tracks, and Playlists) ---
        playing_id = None
        if hasattr(self, 'playlist_data') and getattr(self, 'current_index', -1) >= 0:
            if self.current_index < len(self.playlist_data):
                playing_id = self.playlist_data[self.current_index].get('id')
            
        # Compile a list of all grids that need to know about the playing track
        browsers_to_update = [
            getattr(self, 'tracks_browser', None), 
            getattr(getattr(self, 'global_album_view', None), 'track_list', None),
            getattr(getattr(self, 'global_playlist_view', None), 'track_list', None)
        ]
        
        # Broadcast the ID, state, and exact color downward
        for tb in browsers_to_update:
            if tb and hasattr(tb, 'update_playing_status'):
                tb.update_playing_status(playing_id, is_playing, mc)

    def update_window_title(self):
        if 0 <= self.current_index < len(self.playlist_data):
            status = "Playing" if self.audio_engine.is_playing else "Paused"
            track = self.playlist_data[self.current_index]
            title = track.get('title', 'Unknown')
            artist = track.get('artist', '')
            self.setWindowTitle(f"({status}) [{self.current_index + 1}/{len(self.playlist_data)}] {title} — {artist}")
        else: 
            self.setWindowTitle("Sonar")

    def update_volume(self, value):
        """Optimized: Only updates audio engine and icon, skips full UI refresh."""
        self.audio_engine.set_volume(value)
        
        should_be_muted = (value == 0)
        
        if should_be_muted != self.is_muted:
            self.is_muted = should_be_muted
            self.update_volume_icon()
        
        if not self.is_muted:
            self.last_volume = value

    def update_volume_icon(self):
        """Lightweight update just for the speaker icon."""
        vol_img = "img/volume_mute.png" if self.is_muted else "img/volume.png"

        v_color = "#888888" if self.is_muted else self.master_color
        
        icon_path = resource_path(vol_img)
        if os.path.exists(icon_path):

            pix = self.get_colored_pixmap(icon_path, v_color, 24)
            self.vol_icon_label.setPixmap(pix)

    def toggle_mute(self):
        if not self.is_muted: self.last_volume = self.vol_slider.value(); self.vol_slider.setValue(0)
        else: self.vol_slider.setValue(self.last_volume)
    
    def adjust_volume_by(self, delta):
        """Changes volume via keyboard and instantly shows the tooltip."""
        new_vol = max(0, min(100, self.vol_slider.value() + delta))
        self.vol_slider.setValue(new_vol)
        self.update_volume(new_vol)
        self.vol_slider.update_tooltip_pos() # Show the user the new %

    def get_colored_pixmap(self, path, color, size):
        """Helper to tint an image without reloading the whole interface."""
        if not os.path.exists(path): return QPixmap()
        
        # 1. Load the original image
        src = QPixmap(path)
        
        # 2. Scale it to fit within the box (preserving aspect ratio)
        src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        
        # 3. Create the final transparent canvas
        colored = QPixmap(size, size)
        colored.fill(QColor(0, 0, 0, 0)) # Transparent background
        
        painter = QPainter(colored)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 4. Draw the Icon FIRST (centered)
        x = (size - src.width()) // 2
        y = (size - src.height()) // 2
        painter.drawPixmap(x, y, src)
        
        # 5. Tint it using SourceIn 
        # (Meaning: "Replace the color of what I just drew with this new color")
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(colored.rect(), QColor(color))
        
        painter.end()
        
        return colored
    
    def load_current_track_metadata(self):
        """
        Wrapper to fix AttributeError.
        1. Updates text immediately (Fast).
        2. Schedules heavy graphics/color update (Debounced).
        """
        # 1. Update Labels Instantly
        self.load_current_track_metadata_text_only()
        
        # 2. Stop any pending heavy updates from previous rapid clicks
        self.visual_update_timer.stop()
        
        # 3. Schedule the heavy update (Blur/Color) to run in 350ms
        # This prevents lag if this method is called rapidly (e.g. gapless transitions)
        self.visual_update_timer.start(350)
    
    def load_current_track_metadata_text_only(self):
        """Updates only the labels. Fast and safe to run instantly."""
        if 0 <= self.current_index < len(self.playlist_data):
            track = self.playlist_data[self.current_index]
            
            # 1. Update Title and Artist
            title = track.get('title', 'Unknown').strip()
            artist = track.get('artist', 'Unknown').strip()
            year = str(track.get('year', '') or '').strip()
            artist_year = f"{artist}  •  {year}" if (year and year != '0') else artist
            self.track_title.setText(title)
            self.track_artist.setText(artist_year)

            # Update heart button state
            if hasattr(self, 'heart_btn'):
                raw_state = track.get('starred')
                if isinstance(raw_state, str):
                    is_fav = raw_state.lower() in ('true', '1')
                else:
                    is_fav = bool(raw_state)
                accent = getattr(self, 'master_color', '#ffffff')
                self.heart_btn.setIcon(self._make_heart_icon(is_fav, accent))
            
            # 2. Determine Base File Type (STREAM vs MP3/FLAC)
            target_path = track.get('path', '')
            stream_url = track.get('stream_url', '')
            
            if stream_url and not target_path:
                self.current_file_type_text = "STREAM"
            else:
                self.current_file_type_text = os.path.splitext(target_path)[1].upper().replace('.', '') if target_path else 'UNKNOWN'

            # 3. Update the Bottom "Now Playing" Widget
            if hasattr(self, 'now_playing_widget'):
                self.now_playing_widget.update_info(
                    title=title,
                    artist=artist,
                    album=track.get('album', '')
                )
                self.now_playing_widget.set_track(track)
            
            # 4. BPM Cache Check & Worker Trigger
            # Get the unique ID (Navidrome ID for streams, or file path for local)
            track_id = str(track.get('id') or track.get('path', 'unknown'))
            
            if hasattr(self, 'bpm_cache') and track_id in self.bpm_cache:
                # INSTANT LOAD FROM CACHE!
                bpm = self.bpm_cache[track_id]
                self.file_type_label.setText(f"{self.current_file_type_text}   •   {bpm:.1f} BPM")
            else:
                # Not cached. Show loading text and start the worker
                self.file_type_label.setText(f"{self.current_file_type_text}   •   BPM...")
                
                # Safely kill the old worker if the user skips tracks quickly
                if hasattr(self, 'bpm_worker') and self.bpm_worker.isRunning():
                    self._safe_discard_worker(self.bpm_worker)
                    
                # Launch the background C++ analyzer
                self.bpm_worker = BPMWorker(self.audio_engine, track)
                self.bpm_worker.bpm_ready.connect(self._on_bpm_calculated)
                self.bpm_worker.start()

    def _make_heart_icon(self, active, color_str):
        path = resource_path("img/heart_filled.png" if active else "img/heart.png")
        base = QPixmap(path)
        if base.isNull():
            return QIcon()
        pix = QPixmap(base.size())
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.drawPixmap(0, 0, base)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pix.rect(), QColor("#E91E63" if active else "#555555"))
        painter.end()
        return QIcon(pix)

    def _toggle_now_playing_favorite(self):
        if not (0 <= self.current_index < len(self.playlist_data)):
            return
        track = self.playlist_data[self.current_index]
        raw_state = track.get('starred')
        if isinstance(raw_state, str):
            current_state = raw_state.lower() in ('true', '1')
        else:
            current_state = bool(raw_state)
        new_state = not current_state
        track['starred'] = new_state
        accent = getattr(self, 'master_color', '#ffffff')
        self.heart_btn.setIcon(self._make_heart_icon(new_state, accent))
        if hasattr(self, 'navidrome_client') and self.navidrome_client:
            import threading
            threading.Thread(
                target=lambda: self.navidrome_client.set_favorite(track.get('id'), new_state),
                daemon=True
            ).start()

    def set_elided_text(self, label, text):
        metrics = QFontMetrics(label.font())
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, label.width())
        label.setText(elided)
    
    def enforce_artist_min_width(self, index, old_size, new_size):
        MIN_WIDTH = 100 
        if index == 1 and new_size < MIN_WIDTH:
            self.tree.header().resizeSection(1, MIN_WIDTH)
    
    def enable_dark_title_bar(self):
        if sys.platform == "win32":
            try:
                import ctypes; hwnd = int(self.winId()); val = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4); ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), 4)
            except: pass
       
