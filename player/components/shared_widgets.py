import os, threading

from PyQt6.QtWidgets import (QDialog, QToolButton, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QWidget, QCheckBox, QFrame, QScrollArea, QSizePolicy,
                              QApplication)
from PyQt6.QtCore import (Qt, pyqtSignal, pyqtProperty, QSize,
                          QPropertyAnimation, QEasingCurve, QMetaObject, Q_ARG, QObject, QPoint, QUrl, QTimer, QEvent)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QDesktopServices, QGuiApplication
from player import resource_path

class ToggleSwitch(QCheckBox):
    def __init__(self, accent_color, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22) # Sleek pill size
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent_color = accent_color

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        
        # 1. Draw the pill background
        if self.isChecked():
            p.setBrush(QColor(self.accent_color))
        else:
            p.setBrush(QColor("#333333")) # Dark gray when off
        p.drawRoundedRect(0, 0, self.width(), self.height(), 11, 11)
        
        # 2. Draw the white circle (thumb)
        p.setBrush(QColor("#ffffff"))
        if self.isChecked():
            # Move to the right when checked
            p.drawEllipse(self.width() - 20, 2, 18, 18) 
        else:
            # Stay on the left when unchecked
            p.drawEllipse(2, 2, 18, 18) 
        p.end()

class NewPlaylistDialog(QDialog):
    def __init__(self, parent=None, accent_color="#1DB954", bg_color="#1e1e1e", border_color="#333333", border_width=1, fg_primary="#dddddd", fg_secondary="#999999", hover_color="rgba(255,255,255,0.08)"):
        super().__init__(parent)
        self.accent_color = accent_color
        self.bg_color = bg_color
        self.border_color = border_color
        self.border_width = border_width
        self.fg_primary = fg_primary
        self.fg_secondary = fg_secondary
        self.hover_color = hover_color
        self.playlist_name = ""
        
        # Remove OS borders and make background transparent for rounded corners
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(350)
        
        self.setup_ui()
        
    def setup_ui(self):
        # Main Dialog Layout (No margins so the frame fills it perfectly)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        
        self.bg_frame = QFrame()
        self.bg_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {self.bg_color};
                border: {self.border_width}px solid {self.border_color};
                border-radius: 10px;
            }}
        """)
        
        # The layout INSIDE the solid frame
        frame_layout = QVBoxLayout(self.bg_frame)
        frame_layout.setContentsMargins(20, 20, 20, 20)
        frame_layout.setSpacing(15)

        # Container for Inputs
        container_layout = QVBoxLayout()
        container_layout.setSpacing(10)

        # 1. Playlist Name Input Field
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Playlist name...")
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {self.bg_color};
                color: {self.fg_primary};
                border: {self.border_width}px solid {self.border_color};
                border-radius: 4px;
                padding: 10px 12px;
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: {self.border_width}px solid {self.border_color};
            }}
        """)
        _pal = self.name_input.palette()
        _pal.setColor(_pal.ColorRole.PlaceholderText, QColor(self.fg_secondary))
        self.name_input.setPalette(_pal)
        self.name_input.returnPressed.connect(self.accept_dialog)
        container_layout.addWidget(self.name_input)

        # 2. Modern Toggle Switch Row
        toggle_layout = QHBoxLayout()
        toggle_layout.setContentsMargins(0, 5, 0, 5)

        public_label = QLabel("Public Playlist?")
        public_label.setStyleSheet(f"color: {self.fg_secondary}; font-size: 13px; font-weight: bold; border: none; background: transparent;")

        # Instantiate our custom drawing class
        self.public_toggle = ToggleSwitch(self.accent_color)

        # Add to the row: Label -> Stretch (Empty Space) -> Toggle
        toggle_layout.addWidget(public_label)
        toggle_layout.addStretch()
        toggle_layout.addWidget(self.public_toggle)

        container_layout.addLayout(toggle_layout)
        frame_layout.addLayout(container_layout)

        # 3. Action Buttons (Cancel / Create)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch() # Pushes buttons to the right side

        btn_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {self.fg_secondary};
                border: 1px solid {self.border_color};
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton:hover {{
                background-color: {self.hover_color};
            }}
        """

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(btn_style)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_create = QPushButton("Create")
        self.btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create.setStyleSheet(btn_style.replace(self.fg_secondary, self.fg_primary, 1))
        self.btn_create.clicked.connect(self.accept_dialog)
        self.btn_create.clicked.connect(self.accept_dialog)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_create)

        frame_layout.addLayout(btn_layout)
        
        # Add the solid frame to the invisible dialog
        main_layout.addWidget(self.bg_frame)
        
        # Automatically focus the text input
        self.name_input.setFocus()
        
    def accept_dialog(self):
        name = self.name_input.text().strip()
        if name:
            self.playlist_name = name
            self.accept()
            
    def get_name(self):
        return self.playlist_name

    def is_public(self):
        return self.public_toggle.isChecked()

class AutoCollapseInput(QLineEdit):
    focus_lost = pyqtSignal()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_lost.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            # 1. Erase the text (This auto-resets the grid/list below it)
            self.clear()

            # 2. Drop keyboard focus (This triggers focusOutEvent -> check_collapse!)
            self.clearFocus()

            event.accept()
        else:
            super().keyPressEvent(event)

    def getAnimWidth(self):
        return self.maximumWidth()

    def setAnimWidth(self, w):
        # Set both constraints in one call before any layout pass fires
        self.setMinimumWidth(w)
        self.setMaximumWidth(w)

    animWidth = pyqtProperty(int, getAnimWidth, setAnimWidth)

class SmartSearchContainer(QWidget):
    text_changed = pyqtSignal(str)
    burger_clicked = pyqtSignal()

    TARGET_WIDTH = 230

    def __init__(self, parent=None, placeholder="Search..."):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        # Fixed width always = input + two buttons.
        # Parent header never changes this, so it never reflows during animation.
        self.setFixedWidth(self.TARGET_WIDTH + 70)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- INPUT ---
        self.search_input = AutoCollapseInput()
        self.search_input.setPlaceholderText(placeholder)
        self.search_input.setMaximumWidth(0)
        self.search_input.setFixedHeight(28)
        self.search_input.setClearButtonEnabled(False)
        
        # Clear button
        self.custom_clear_btn = QToolButton(self.search_input)
        self.custom_clear_btn.setIcon(QIcon(resource_path("img/close.png")))
        self.custom_clear_btn.setIconSize(QSize(10, 10))
        self.custom_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.custom_clear_btn.setStyleSheet("""
            QToolButton { border: none; background: transparent; padding: 2px; }
            QToolButton:hover { background: #333; border-radius: 8px; }
        """)
        self.custom_clear_btn.hide()
        self.custom_clear_btn.clicked.connect(self.search_input.clear)
        
        input_layout = QHBoxLayout(self.search_input)
        input_layout.setContentsMargins(0, 0, 5, 0)
        input_layout.addStretch()
        input_layout.addWidget(self.custom_clear_btn)
        
        self.search_input.setStyleSheet(
            "background-color: #0e0e0e; border: 1px solid #2a2a2a;"
            " border-radius: 4px; padding-left: 10px; padding-right: 25px; font-size: 13px;"
        )
        
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.focus_lost.connect(self.check_collapse)
        
        # --- SEARCH BUTTON ---
        self.search_btn = QPushButton()
        self.search_btn.setIcon(QIcon(resource_path("img/search.png")))
        self.search_btn.setIconSize(QSize(18, 18))
        self.search_btn.setFixedSize(32, 32)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setToolTip("Search")
        self.search_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.search_btn.clicked.connect(self.toggle_search)

        # --- BURGER BUTTON ---
        self.burger_btn = QPushButton()
        self.burger_btn.setIcon(QIcon(resource_path("img/burger.png")))
        self.burger_btn.setIconSize(QSize(18, 18))
        self.burger_btn.setFixedSize(32, 32)
        self.burger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.burger_btn.setToolTip("Select columns")
        self.burger_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.burger_btn.clicked.connect(self.burger_clicked.emit)
        
        layout.addStretch(1)  # absorbs empty space; input grows leftward from the search button
        layout.addWidget(self.search_input)
        layout.addSpacing(4)
        layout.addWidget(self.search_btn)
        layout.addWidget(self.burger_btn)
        
    def _on_text_changed(self, text):
        self.custom_clear_btn.setVisible(bool(text))
        self.text_changed.emit(text)
        
    def toggle_search(self):
        if getattr(self, '_search_animating', False):
            return
        self._search_animating = True

        self._search_anim = QPropertyAnimation(self.search_input, b"animWidth")
        self._search_anim.setDuration(300)
        self._search_anim.setEasingCurve(QEasingCurve.Type.InOutQuart)
        self._search_anim.finished.connect(lambda: setattr(self, '_search_animating', False))

        if self.search_input.maximumWidth() == 0:
            self._search_anim.setStartValue(0)
            self._search_anim.setEndValue(self.TARGET_WIDTH)
            self._search_anim.start()
            self.search_input.setFocus()
        else:
            current_w = self.search_input.maximumWidth()
            self._search_anim.setStartValue(current_w)
            self._search_anim.setEndValue(0)
            self._search_anim.start()
            self.search_input.clearFocus()
            self.search_input.clear()

    def check_collapse(self):
        if self.search_input.maximumWidth() > 0 and not self.search_input.text():
            self.toggle_search()

    def apply_input_theme(self, bg, border_color, border_width, fg_primary, fg_secondary, hover_color, accent_color):
        self.search_input.setStyleSheet(
            f"background-color: rgb({bg});"
            f"border: {border_width}px solid {border_color};"
            f"border-radius: 4px;"
            f"padding-left: 10px; padding-right: 25px; font-size: 13px;"
        )
        _pal = self.search_input.palette()
        _pal.setColor(_pal.ColorRole.Text, QColor(fg_primary))
        _pal.setColor(_pal.ColorRole.PlaceholderText, QColor(fg_secondary))
        self.search_input.setPalette(_pal)
        self.search_input.update()

        _btn_style = f"QPushButton {{ background: transparent; border: none; border-radius: 4px; }} QPushButton:hover {{ background: {hover_color}; }}"
        self.search_btn.setStyleSheet(_btn_style)
        self.burger_btn.setStyleSheet(_btn_style)

        try:
            pixmap = QPixmap(resource_path("img/search.png"))
            if not pixmap.isNull():
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(accent_color))
                painter.end()
                self.search_btn.setIcon(QIcon(pixmap))
        except Exception as e: print(f"Error tinting search icon: {e}")

        try:
            pixmap = QPixmap(resource_path("img/close.png"))
            if not pixmap.isNull():
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(accent_color))
                painter.end()
                self.custom_clear_btn.setIcon(QIcon(pixmap))
        except Exception as e: print(f"Error tinting clear icon: {e}")

    def set_accent_color(self, color):
        from player.mixins.visuals import resolve_menu_hover
        _theme = getattr(self.window(), 'theme', None)
        self.apply_input_theme(
            bg           = getattr(_theme, 'main_panel_bg',        '14,14,14'),
            border_color = getattr(_theme, 'border_color',         '#2a2a2a'),
            border_width = getattr(_theme, 'border_width',         1),
            fg_primary   = getattr(_theme, 'font_color_primary',   '#dddddd'),
            fg_secondary = getattr(_theme, 'font_color_secondary', '#999999'),
            hover_color  = resolve_menu_hover(_theme),
            accent_color = color,
        )

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_theme_applied', False):
            _theme = getattr(self.window(), 'theme', None)
            self.set_accent_color(getattr(_theme, 'accent', '#ffffff'))
            self._theme_applied = True

    def get_text(self):
        return self.search_input.text()
    
    def set_text(self, text): 
        self.search_input.setText(text)
    
    def hide_burger(self): 
        self.burger_btn.hide()
    
    def show_burger(self): 
        self.burger_btn.show()
    
    def get_burger_btn(self): 
        return self.burger_btn
    
    def hide_search(self): 
        self.search_btn.hide()
        self.search_input.hide()
    
    def show_search(self): 
        self.search_btn.show()
        self.search_input.show()
    
    def collapse(self):
        
        if self.search_input.maximumWidth() > 0:
            self.search_input.clear()
            self.search_input.setMaximumWidth(0)
            self.search_input.setMinimumWidth(0)

def _fmt_bpm(raw):
    try:
        v = float(raw)
        return f"{v:.1f}" if v > 0 else ''
    except (TypeError, ValueError):
        return ''


class TrackInfoDialog(QDialog):
    """Shows detailed metadata for a single track, with async enrichment via getSong."""

    _update_signal = pyqtSignal(dict)

    def __init__(self, track: dict, client=None, accent_color='#1DB954', parent=None,
                 on_artist_click=None, on_album_click=None, on_genre_click=None, detected_bpm=None):
        super().__init__(parent)
        self.track = track
        self.client = client
        self.accent_color = accent_color
        self.on_artist_click = on_artist_click
        self.on_album_click = on_album_click
        self.on_genre_click = on_genre_click
        self.detected_bpm = detected_bpm
        self._value_labels = {}
        self._drag_pos = None

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_win = parent.window() if parent else None
        margin = 40
        if main_win:
            self.setFixedWidth(min(500, main_win.width() - margin * 2))
            self.setFixedHeight(main_win.height() - margin * 2)
        else:
            self.setFixedWidth(620)
            self.setFixedHeight(700)

        self._build_ui()
        self._update_signal.connect(self._apply_full_data)

        if client and track.get('id'):
            threading.Thread(target=self._fetch_full_data, daemon=True).start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._suppress_close = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._suppress_close = False
        super().mouseReleaseEvent(event)

    def exec(self):
        from PyQt6.QtCore import QEventLoop
        main_win = self.parent().window() if self.parent() else None
        margin = 40
        if main_win:
            max_h = main_win.height() - margin * 2
            content_h = self._rows_content.sizeHint().height()
            dialog_h = min(content_h + 80, max_h)
            self.setFixedHeight(dialog_h)

        if main_win:
            top_left = main_win.mapToGlobal(QPoint(0, 0))
            x = top_left.x() + (main_win.width() - self.width()) // 2
            y = top_left.y() + (main_win.height() - self.height()) // 2
            QTimer.singleShot(0, lambda: self.move(x, y))

        # Non-modal so clicks reach the main window (enabling click-away-to-close)
        self.setWindowModality(Qt.WindowModality.NonModal)
        loop = QEventLoop()
        self.finished.connect(loop.quit)
        self.show()
        loop.exec()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            if not getattr(self, '_suppress_close', False):
                QTimer.singleShot(150, self._maybe_close)
        super().changeEvent(event)

    def _maybe_close(self):
        if getattr(self, '_suppress_close', False):
            return
        from PyQt6.QtWidgets import QApplication
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            self.reject()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        main_win = self.parent().window() if self.parent() else None
        theme = getattr(main_win, 'theme', None)
        bg = getattr(theme, 'main_panel_bg', '17,17,17')
        self._bc           = getattr(theme, 'border_color',         '#2a2a2a')
        bc = self._bc
        self._fs_primary  = getattr(theme, 'font_size_primary',   14)
        self._fc_primary  = getattr(theme, 'font_color_primary',  '#dddddd')
        self._fc_secondary = getattr(theme, 'font_color_secondary', '#999999')
        self.bg = QFrame()
        self.bg.setObjectName("trackInfoBg")
        self.bg.setStyleSheet(f"""
            QFrame#trackInfoBg {{
                background-color: rgb({bg});
                border: 1px solid {bc};
                border-radius: 10px;
            }}
        """)
        outer.addWidget(self.bg)

        root = QVBoxLayout(self.bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(24, 20, 16, 12)

        title_lbl = QLabel(self.track.get('title', 'Unknown'))
        title_lbl.setStyleSheet(f"color: {self._fc_primary}; font-size: 17px; font-weight: bold; background: transparent;")
        title_lbl.setWordWrap(True)
        h_lay.addWidget(title_lbl, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #888; font-size: 14px; border: none; }
            QPushButton:hover { color: #fff; }
        """)
        close_btn.clicked.connect(self.reject)
        h_lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background-color: {self._bc};")
        root.addWidget(div)

        # Scrollable rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; }}
            QScrollBar:vertical {{ background: #1a1a1a; width: 6px; border-radius: 3px; }}
            QScrollBar::handle:vertical {{ background: {self.accent_color}; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        root.addWidget(scroll)

        self._rows_content = QWidget()
        self._rows_content.setStyleSheet("background: transparent;")
        self.rows_layout = QVBoxLayout(self._rows_content)
        self.rows_layout.setContentsMargins(0, 0, 0, 16)
        self.rows_layout.setSpacing(0)
        scroll.setWidget(self._rows_content)

        self._populate_rows(self.track)

    def _populate_rows(self, t):
        def size_fmt(b):
            if not b:
                return ''
            return f"{int(b) / (1024 * 1024):.2f} MiB"

        rows = [
            ('Title',          t.get('title', '')),
            ('__path__',       t.get('path', '')),
            ('Album artist',   '__artist__:' + (t.get('album_artist', '') or '')),
            ('Artists',        '__artist__:' + (t.get('artist', '') or '')),
            ('Album',          '__album__:' + (t.get('album', '') or '')),
            ('Disc',           str(t.get('discNumber', '') or '')),
            ('Track',          str(t.get('trackNumber', '') or '')),
            ('Release year',   str(t.get('year', '') or '')),
            ('Genres',         '__genre__:' + (t.get('genre', '') or '')),
            ('Duration',       t.get('duration', '')),
            ('Is compilation', '__bool__:' + str(bool(t.get('compilation')))),
            ('Codec',          t.get('suffix', '') or t.get('codec', '')),
            ('BPM ID3Tag',     _fmt_bpm(t.get('_id3_bpm'))),
            ('BPM Detected',   _fmt_bpm(self.detected_bpm)),
            ('Bitrate',        (str(t.get('bitRate', '')) + ' kbps') if t.get('bitRate') else ''),
            ('Sample rate',    str(t.get('samplingRate', '') or '')),
            ('Bit depth',      str(t.get('bitDepth', '') or '')),
            ('Channels',       str(t.get('channelCount', '') or '')),
            ('Size',           size_fmt(t.get('size', 0))),
            ('Favorite',       '__bool__:' + str(bool(t.get('starred')))),
            ('Play count',     str(t.get('play_count', 0) or 0)),
            ('Modified',       t.get('created', '')),
            ('Id',             str(t.get('id', ''))),
        ]
        for label, value in rows:
            self._add_row(label, value)

    def _set_bool_icon(self, lbl: QLabel, checked: bool):
        img_path = resource_path(os.path.join('img', 'yes.png' if checked else 'no.png'))
        px = QPixmap(img_path)
        if not px.isNull():
            lbl.setPixmap(px.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
            lbl.setText('')
        else:
            # fallback if image missing
            lbl.setText('✓' if checked else '✗')
            color = '#4caf50' if checked else '#f44336'
            lbl.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")

    def _add_row(self, label: str, value: str):
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")

        if label == '__path__':
            self._build_path_row(row_widget, value)
        else:
            row_widget.setFixedHeight(32)  # ← change this to adjust row height
            lay = QHBoxLayout(row_widget)
            lay.setContentsMargins(24, 0, 24, 0)
            lay.setSpacing(12)

            lbl = QLabel(label)
            lbl.setFixedWidth(110)
            lbl.setStyleSheet(f"color: {self._fc_secondary}; font-size: {self._fs_primary}px; background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(lbl)

            if not isinstance(value, str):
                value = str(value) if value is not None else ''

            if value.startswith('__bool__:'):
                checked = value == '__bool__:True'
                val_lbl = QLabel()
                val_lbl.setStyleSheet("background: transparent;")
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._set_bool_icon(val_lbl, checked)
                lay.addWidget(val_lbl, 1)

            elif value.startswith('__artist__:'):
                names = value[len('__artist__:'):]
                val_lbl = self._build_link_label(
                    names, separator=' • ',
                    callback=self.on_artist_click,
                    fallback_style=f"color: {self._fc_primary}; font-size: {self._fs_primary}px; background: transparent;"
                )
                lay.addWidget(val_lbl, 1)

            elif value.startswith('__album__:'):
                album_name = value[len('__album__:'):]
                val_lbl = self._build_link_label(
                    album_name, separator=None,
                    callback=self.on_album_click,
                    fallback_style=f"color: {self._fc_primary}; font-size: {self._fs_primary}px; background: transparent;"
                )
                lay.addWidget(val_lbl, 1)

            elif value.startswith('__genre__:'):
                import re as _re
                genre_text = value[len('__genre__:'):]
                genre_parts = [p.strip() for p in _re.split(r' /// | • | / |,\s*|;\s*', genre_text.strip()) if p.strip()]
                g_container = QWidget()
                g_container.setStyleSheet('background: transparent;')
                g_lo = QHBoxLayout(g_container)
                g_lo.setContentsMargins(0, 0, 0, 0)
                g_lo.setSpacing(0)
                _g_btn = (
                    f'QPushButton {{ color: {self._fc_primary}; font-size: {self._fs_primary}px;'
                    f' background: transparent; border: none; padding: 0; font-weight: bold; }}'
                    f'QPushButton:hover {{ text-decoration: underline; }}'
                )
                _g_sep = f'color: #666; font-size: {self._fs_primary}px; background: transparent;'
                for i, g in enumerate(genre_parts or ['—']):
                    if i > 0:
                        g_lo.addWidget(QLabel(' • ', styleSheet=_g_sep))
                    if not genre_parts:
                        g_lo.addWidget(QLabel('—', styleSheet=f"color: {self._fc_primary}; font-size: {self._fs_primary}px; background: transparent;"))
                        break
                    btn = QPushButton(g.replace('&', '&&'))
                    btn.setFlat(True)
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.setStyleSheet(_g_btn)
                    if self.on_genre_click:
                        btn.clicked.connect(lambda _=False, genre=g: (self.accept(), self.on_genre_click(genre)))
                    g_lo.addWidget(btn)
                g_lo.addStretch(1)
                val_lbl = g_container
                lay.addWidget(val_lbl, 1)

            else:
                val_lbl = QLabel(value or '—')
                val_lbl.setStyleSheet(f"color: {self._fc_primary}; font-size: {self._fs_primary}px; background: transparent;")
                val_lbl.setWordWrap(True)
                val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                lay.addWidget(val_lbl, 1)

            self._value_labels[label] = lay.itemAt(lay.count() - 1).widget()

        self.rows_layout.addWidget(row_widget)
        sep_wrap = QHBoxLayout()
        sep_wrap.setContentsMargins(20, 0, 20, 0)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {self._bc};")
        sep_wrap.addWidget(sep)
        self.rows_layout.addLayout(sep_wrap)

    def _build_link_label(self, text, separator, callback, fallback_style):
        """Returns a QLabel. If callback is set, renders clickable HTML links."""
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lbl.setStyleSheet("background: transparent;")

        if callback and text:
            if separator:
                import re as _re
                parts = [p.strip() for p in _re.split(_re.escape(separator) + r'|,\s+', text) if p.strip()]
            else:
                parts = [text]
            sep_html = f' <span style="color:#666;">{separator}</span> ' if separator else ''
            accent = self.accent_color

            def make_html(hovered='', _parts=parts, _sep=sep_html, _accent=accent):
                links = [
                    f'<a href="{p}" style="color:{_accent}; text-decoration:{"underline" if p == hovered else "none"}; font-weight:bold;">{p}</a>'
                    for p in _parts
                ]
                return _sep.join(links)

            lbl.setText(make_html())
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setOpenExternalLinks(False)
            lbl.linkHovered.connect(lambda href: lbl.setText(make_html(href)))
            def _on_link_activated(href, _cb=callback):
                self._suppress_close = True
                _cb(href)
                QTimer.singleShot(500, lambda: setattr(self, '_suppress_close', False))
            lbl.linkActivated.connect(_on_link_activated)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            lbl.setText(text or '—')
            lbl.setStyleSheet(fallback_style)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        return lbl

    def _build_path_row(self, row_widget, path):
        # Same two-column layout as other rows
        outer = QHBoxLayout(row_widget)
        outer.setContentsMargins(24, 7, 24, 7)
        outer.setSpacing(12)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        def load_tinted_icon(img_name, size=14, color=None):
            tint = color or self.accent_color
            img_path = resource_path(os.path.join('img', img_name))
            px = QPixmap(img_path).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
            out = QPixmap(px.size())
            out.fill(QColor('transparent'))
            p = QPainter(out)
            p.drawPixmap(0, 0, px)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(out.rect(), QColor(tint))
            p.end()
            return QIcon(out)

        def icon_btn(img_name, tip, callback):
            b = QPushButton()
            b.setIcon(load_tinted_icon(img_name))
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tip)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background: transparent; border: none; }"
                f"QPushButton:hover {{ color: white; }}"
            )
            b.clicked.connect(callback)
            return b

        # Left column: "Path" label + icons stacked
        left = QWidget()
        left.setFixedWidth(110)
        left.setStyleSheet("background: transparent;")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)
        left_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        lbl_header = QLabel("Path")
        lbl_header.setStyleSheet(f"color: {self._fc_secondary}; font-size: {self._fs_primary}px; background: transparent;")
        left_lay.addWidget(lbl_header)

        icons_row = QHBoxLayout()
        icons_row.setSpacing(4)
        icons_row.setContentsMargins(0, 0, 0, 0)
        self._current_path = path

        def _copy_path():
            QGuiApplication.clipboard().setText(self._current_path)
            self._copy_btn.setIcon(load_tinted_icon("yes.png"))
            self._copy_btn.setToolTip("Copied!")
            QTimer.singleShot(1500, lambda: (
                self._copy_btn.setIcon(load_tinted_icon("copy-path.png")),
                self._copy_btn.setToolTip("Copy path"),
            ))

        self._copy_btn = icon_btn("copy-path.png", "Copy path", _copy_path)
        icons_row.addWidget(self._copy_btn)
        icons_row.addStretch()
        left_lay.addLayout(icons_row)
        outer.addWidget(left)

        # Right column: path text, word-wrapped, aligned with other values
        self._path_lbl = QLabel(path or '—')
        self._path_lbl.setStyleSheet(f"color: {self._fc_primary}; font-size: {self._fs_primary}px; background: transparent;")
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self._path_lbl, 1)

    def _fetch_full_data(self):
        try:
            # Native API has the real filesystem path; Subsonic API has extra audio fields
            native = self.client.get_song_native(self.track['id']) or {}
            subsonic = self.client.get_song(self.track['id']) or {}
            # Merge: start with subsonic for audio metadata, overlay native for path
            merged = {**subsonic, **{k: v for k, v in native.items() if v not in (None, '', 0)}}
            # Prefer native path (real filesystem path)
            if native.get('path'):
                merged['path'] = native['path']
            if merged:
                self._update_signal.emit(merged)
        except Exception as e:
            print(f"[TrackInfoDialog] fetch error: {e}")

    def _apply_full_data(self, raw: dict):
        def size_fmt(b):
            if not b:
                return ''
            return f"{int(b) / (1024 * 1024):.2f} MiB"

        # Update path if the full data has a more complete one
        full_path = raw.get('path', '')
        if full_path and hasattr(self, '_path_lbl'):
            self._current_path = full_path
            self._path_lbl.setText(full_path)

        # Rebuild clickable link labels with fresh text
        for field, text, cb in (
            ('Artists',      raw.get('artist', ''),                                          self.on_artist_click),
            ('Album artist', raw.get('albumArtist', '') or raw.get('album_artist', ''),      self.on_artist_click),
            ('Album',        raw.get('album', ''),                                           self.on_album_click),
        ):
            lbl = self._value_labels.get(field)
            if lbl is None:
                continue
            new_lbl = self._build_link_label(
                text or '',
                separator=' • ' if field != 'Album' else None,
                callback=cb,
                fallback_style="color: #ddd; font-size: 13px; background: transparent;",
            )
            parent_lay = lbl.parentWidget().layout() if lbl.parentWidget() else None
            if parent_lay:
                idx = parent_lay.indexOf(lbl)
                if idx >= 0:
                    parent_lay.removeWidget(lbl)
                    lbl.deleteLater()
                    parent_lay.insertWidget(idx, new_lbl, 1)
                    self._value_labels[field] = new_lbl

        updates = {
            'Title':           raw.get('title', ''),
            'Release year':    str(raw.get('year', '') or ''),
            'Codec':           raw.get('suffix', ''),
            'BPM ID3Tag':      _fmt_bpm(raw.get('bpm')),
            'Bitrate':         (str(raw.get('bitRate', '')) + ' kbps') if raw.get('bitRate') else '',
            'Sample rate':     str(raw.get('samplingRate', '') or ''),
            'Bit depth':       str(raw.get('bitDepth', '') or ''),
            'Channels':        str(raw.get('channelCount', '') or ''),
            'Size':            size_fmt(raw.get('size', 0)),
            'Is compilation':  '__bool__:' + str(bool(raw.get('isCompilation') or raw.get('compilation'))),
            'Modified':        raw.get('created', '') or raw.get('modified', ''),
            'Play count':      str(raw.get('playCount', 0) or 0),
            'Favorite':        '__bool__:' + str('starred' in raw),
        }
        for field, value in updates.items():
            lbl = self._value_labels.get(field)
            if lbl is None:
                continue
            if value.startswith('__bool__:'):
                checked = value == '__bool__:True'
                self._set_bool_icon(lbl, checked)
            else:
                lbl.setText(value or '—')