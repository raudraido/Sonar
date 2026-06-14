from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, QEasingCurve

from player.panels.footer.waveform_scrubber import WaveformScrubber
from player.widgets import NowPlayingFooterWidget, ClickableSlider, StatusButton, PlayButton


class FooterPanel(QWidget):
    """Transport bar: playback controls, seek bar, now-playing info, volume/cast/settings."""

    def __init__(self, window):
        super().__init__()
        self.setObjectName("FooterPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#FooterPanel { background-color: rgba(14, 14, 14, 0.75); border-top: 1px solid rgba(255, 255, 255, 0.1); }")

        window.cast_btn = QPushButton("")
        window.cast_btn.setFixedSize(40, 40)
        window.cast_btn.setIconSize(QSize(22, 22))
        window.cast_btn.setFlat(True)
        window.cast_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        window.cast_btn.setStyleSheet("background: transparent; border: none;")
        window.cast_btn.setToolTip("Cast to device")
        window.cast_btn.clicked.connect(window._on_cast_clicked)

        window.settings_btn = QPushButton("")
        window.settings_btn.setFixedSize(40, 40)
        window.settings_btn.setIconSize(QSize(20, 20))
        window.settings_btn.clicked.connect(window.open_settings)
        window.settings_btn.setToolTip("Settings")

        window.btn_stop = QPushButton("")
        window.btn_stop.setFixedSize(40, 40)
        window.btn_stop.clicked.connect(window._media_stop)
        window.btn_stop.setToolTip("Stop")

        window.btn_shuffle = StatusButton("")
        window.btn_shuffle.setCheckable(True)
        window.btn_shuffle.setFixedSize(40, 40)
        window.btn_shuffle.clicked.connect(window.toggle_shuffle)
        window.btn_shuffle.setToolTip("Shuffle")

        window.btn_prev = QPushButton("")
        window.btn_prev.setFixedSize(50, 50)
        window.btn_prev.clicked.connect(window.play_prev)
        window.btn_prev.setToolTip("Previous Track")

        window.btn_play = PlayButton()
        window.btn_play.setFixedSize(58, 58)
        window.btn_play.clicked.connect(window.toggle_playback)
        window.btn_play.setToolTip("Play/Pause")

        window.btn_next = QPushButton("")
        window.btn_next.setFixedSize(50, 50)
        window.btn_next.clicked.connect(window.play_next)
        window.btn_next.setToolTip("Next Track")

        window.btn_repeat = StatusButton("")
        window.btn_repeat.setCheckable(True)
        window.btn_repeat.setFixedSize(40, 40)
        window.btn_repeat.clicked.connect(window.toggle_repeat)
        window.btn_repeat.setToolTip("Repeat")

        window.vol_slider = ClickableSlider(Qt.Orientation.Horizontal, window, is_volume=True)
        window.vol_slider.setFixedWidth(100)
        window.vol_slider.setRange(0, 100)
        window.vol_slider.setValue(window.last_volume)
        window.vol_slider.valueChanged.connect(window.update_volume)
        window.vol_slider.sliderMoved.connect(window.vol_slider.update_tooltip_pos)

        window.vol_icon_label = QPushButton()
        window.vol_icon_label.setFixedSize(40, 40)
        window.vol_icon_label.setIconSize(QSize(window._VOL_ICON_SIZE, window._VOL_ICON_SIZE))
        window.vol_icon_label.setToolTip("Mute/Unmute")
        window.vol_icon_label.setCursor(Qt.CursorShape.PointingHandCursor)
        window.vol_icon_label.setFlat(True)
        window.vol_icon_label.clicked.connect(window.toggle_mute)

        window.current_time_label = QLabel("0:00")

        # SWAP THE SLIDER FOR THE WAVEFORM
        window.seek_bar = WaveformScrubber(master_color=window.theme.accent, parent=window)
        window.seek_bar.seek_requested.connect(window.on_waveform_seek)
        window.seek_bar.mode_toggled.connect(window.on_waveform_toggled)

        # THE SCRATCH CONNECTION (Wire physics straight to C++)
        window.seek_bar.scratch_mode_changed.connect(window.audio_engine.set_scratch_mode)
        window.seek_bar.velocity_changed.connect(window.audio_engine.set_scratch_velocity)

        # THE UI CONNECTION (Update the time text while scrubbing)
        window.seek_bar.position_updated.connect(
            lambda ms: window.current_time_label.setText(window.format_time(ms)) if hasattr(window, 'current_time_label') else None
        )

        saved_mode = int(window.settings.value('waveform_mode', 2))
        if saved_mode in (1, 2):
            window.seek_bar.display_mode = saved_mode
            window.seek_bar.render_timer.stop()

        saved_vis = int(window.settings.value('vis_mode', 0))
        if saved_vis and getattr(window, 'visualizer', None):
            window.visualizer.vis_mode = saved_vis

        window.total_time_label = QLabel("0:00")

        window.controls_layout = QHBoxLayout()
        window.controls_layout.setSpacing(20)
        window.controls_layout.addStretch()
        window.controls_layout.addWidget(window.btn_stop)
        window.controls_layout.addWidget(window.btn_shuffle)
        window.controls_layout.addWidget(window.btn_prev)
        window.controls_layout.addWidget(window.btn_play)
        window.controls_layout.addWidget(window.btn_next)
        window.controls_layout.addWidget(window.btn_repeat)
        window.controls_layout.addStretch()

        window.slider_layout = QHBoxLayout()
        window.slider_layout.setContentsMargins(0, 0, 0, 0)
        window.slider_layout.setSpacing(15)
        window.slider_layout.addWidget(window.current_time_label, alignment=Qt.AlignmentFlag.AlignCenter)
        window.slider_layout.addWidget(window.seek_bar, 1)
        window.slider_layout.addWidget(window.total_time_label, alignment=Qt.AlignmentFlag.AlignCenter)

        main_footer_layout = QHBoxLayout(self)
        main_footer_layout.setContentsMargins(8, 0, 20, 0)
        main_footer_layout.setSpacing(0)

        footer_left = QWidget()
        left_layout = QHBoxLayout(footer_left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        window.now_playing_widget = NowPlayingFooterWidget()
        window.now_playing_widget.artist_clicked.connect(window.on_footer_artist_click)
        window.now_playing_widget.album_clicked.connect(window.on_footer_album_click)
        window.now_playing_widget.title_clicked.connect(window.on_footer_title_click)
        window.now_playing_widget.track_right_clicked.connect(window._show_footer_track_context_menu)
        # art left-click intentionally unbound
        window.now_playing_widget.bpm_adjusted.connect(window._on_footer_bpm_adjusted)
        window.now_playing_widget.expand_art_clicked.connect(window._toggle_sidebar_art)

        # Footer art slide animation (width) — needs now_playing_widget to exist
        window._footer_art_anim = QPropertyAnimation(
            window.now_playing_widget.art_label, b"maximumWidth"
        )
        window._footer_art_anim.setDuration(250)
        window._footer_art_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        window._footer_art_anim.valueChanged.connect(
            lambda v: window.now_playing_widget.art_label.setMinimumWidth(int(v))
        )

        left_layout.addWidget(window.now_playing_widget)

        footer_center = QWidget()
        center_layout = QVBoxLayout(footer_center)
        center_layout.setContentsMargins(10, 10, 10, 0)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addLayout(window.controls_layout)
        center_layout.addLayout(window.slider_layout)

        footer_right = QWidget()
        right_layout = QHBoxLayout(footer_right)
        right_layout.setContentsMargins(8, 0, 8, 0)
        right_layout.setSpacing(8)

        right_layout.addStretch()
        right_layout.addWidget(window.settings_btn)
        right_layout.addWidget(window.vol_icon_label)
        right_layout.addSpacing(4)
        right_layout.addWidget(window.vol_slider)
        right_layout.addWidget(window.cast_btn)

        main_footer_layout.addWidget(footer_left, 2)
        main_footer_layout.addWidget(footer_center, 3)
        main_footer_layout.addWidget(footer_right, 2)
