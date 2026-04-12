import os, sys, threading

from PyQt6.QtWidgets import (QDialog, QToolButton, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QWidget, QCheckBox, QFrame, QScrollArea, QSizePolicy,
                              QApplication)
from PyQt6.QtCore import (Qt, pyqtSignal, pyqtProperty, QSize,
                          QPropertyAnimation, QEasingCurve, QMetaObject, Q_ARG, QObject, QPoint, QUrl, QTimer, QEvent)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QDesktopServices, QGuiApplication

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

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
    def __init__(self, parent=None, accent_color="#1DB954"):
        super().__init__(parent)
        self.accent_color = accent_color
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
        self.bg_frame.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e; /* Solid dark background */
                border: 1px solid #333333; /* Subtle border */
                border-radius: 10px;       /* Smooth rounded corners */
            }
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
                background-color: #111;
                color: white;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 10px 12px;
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 1px solid {self.accent_color};
            }}
        """)
        self.name_input.returnPressed.connect(self.accept_dialog)
        container_layout.addWidget(self.name_input)

        # 2. Modern Toggle Switch Row
        toggle_layout = QHBoxLayout()
        toggle_layout.setContentsMargins(0, 5, 0, 5)

        public_label = QLabel("Public Playlist?")
        public_label.setStyleSheet("color: #aaa; font-size: 13px; font-weight: bold; border: none; background: transparent;")

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

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #aaa;
                border: none;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 15px;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_create = QPushButton("Create")
        self.btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton:hover {{
                background-color: {self.accent_color}dd;
            }}
        """)
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
        
        self.search_input.setStyleSheet("""
            QLineEdit { 
                background-color: #080808; color: #ddd; 
                border: 1px solid #333; border-radius: 4px; 
                padding-left: 10px; padding-right: 25px; font-size: 13px; 
            } 
            QLineEdit:focus { border: 1px solid #555; }
        """)
        
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

    def set_accent_color(self, color):
        
        try:
            pixmap = QPixmap(resource_path("img/search.png"))
            if not pixmap.isNull():
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(color))
                painter.end()
                self.search_btn.setIcon(QIcon(pixmap))
        except Exception as e: print(f"Error tinting search icon: {e}")

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

class PaginationFooter(QWidget):
    page_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_accent = "#888888"
        self.current_page = 1
        self.total_pages = 1

        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(50)
        self.setStyleSheet("""
            PaginationFooter { 
                background-color: #111; 
                border-bottom-left-radius: 5px; 
                border-bottom-right-radius: 5px; 
                border-top: 1px solid #222; 
            }
        """)

        # Main layout for the bar
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(15, 0, 15, 0)
        
        # Inner layout just for the buttons
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(5)
        
        
        self.main_layout.addLayout(self.btn_layout)
        self.main_layout.addStretch() # Pushes buttons to the left just like before!

    def set_accent_color(self, color):
        self.current_accent = color
        self.render_pagination(self.current_page, self.total_pages)

    def _create_btn(self, text, page=None, active=False, enabled=True, visible=True):
        btn = QPushButton(text)
        btn.setFixedSize(32, 32)
        
        if not visible:
            btn.setStyleSheet("background: transparent; border: none;")
            btn.setEnabled(False)
            return btn

        btn.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        
        if active:
            style = f"""
                QPushButton {{ 
                    background-color: {self.current_accent}; 
                    color: white; 
                    border: none; 
                    border-radius: 4px; 
                    font-weight: bold; 
                }}
            """
        else:
            style = """
                QPushButton { background-color: #1a1a1a; color: #ddd; border: none; border-radius: 4px; }
                QPushButton:hover { background-color: #333; }
                QPushButton:disabled { color: #555; background-color: #111; }
            """
        btn.setStyleSheet(style)
        
        if page is not None and enabled:
            btn.clicked.connect(lambda checked, p=page: self.page_changed.emit(p))
        else:
            btn.setEnabled(False)
        
        return btn

    def render_pagination(self, current_page, total_pages):
        self.current_page = current_page
        self.total_pages = total_pages

        # Clear old buttons from the inner layout
        while self.btn_layout.count():
            item = self.btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self.total_pages <= 1:
            self.hide()
            return
        else:
            self.show()

        # PREV BUTTON
        self.btn_layout.addWidget(self._create_btn("<", self.current_page - 1, enabled=(self.current_page > 1)))

        items = []
        if self.total_pages <= 7:
            for p in range(1, self.total_pages + 1):
                items.append(p)
        else:
            items.append(1)
            if self.current_page > 4: items.append("...")
            
            w_start = max(2, self.current_page - 1)
            w_end = min(self.total_pages - 1, self.current_page + 1)
            
            if self.current_page <= 4:
                w_start = 2; w_end = 5
            elif self.current_page >= self.total_pages - 3:
                w_start = self.total_pages - 4; w_end = self.total_pages - 1
            
            for p in range(w_start, w_end + 1):
                items.append(p)
                
            if self.current_page < self.total_pages - 3:
                items.append("...")
                
            items.append(self.total_pages)

        MAX_SLOTS = 7
        while len(items) < MAX_SLOTS:
            items.append(None) 
            
        for item in items[:MAX_SLOTS]:
            if item is None:
                self.btn_layout.addWidget(self._create_btn("", visible=False))
            elif item == "...":
                self.btn_layout.addWidget(self._create_btn("...", enabled=False))
            else:
                is_active = (item == self.current_page)
                self.btn_layout.addWidget(self._create_btn(str(item), item, active=is_active))

        # NEXT BUTTON
        self.btn_layout.addWidget(self._create_btn(">", self.current_page + 1, enabled=(self.current_page < self.total_pages)))


class TrackInfoDialog(QDialog):
    """Shows detailed metadata for a single track, with async enrichment via getSong."""

    _update_signal = pyqtSignal(dict)

    def __init__(self, track: dict, client=None, accent_color='#1DB954', parent=None,
                 on_artist_click=None, on_album_click=None):
        super().__init__(parent)
        self.track = track
        self.client = client
        self.accent_color = accent_color
        self.on_artist_click = on_artist_click
        self.on_album_click = on_album_click
        self._value_labels = {}

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_win = parent.window() if parent else None
        margin = 40
        if main_win:
            self.setFixedWidth(min(620, main_win.width() - margin * 2))
            self.setFixedHeight(main_win.height() - margin * 2)
        else:
            self.setFixedWidth(620)
            self.setFixedHeight(700)

        self._build_ui()
        self._update_signal.connect(self._apply_full_data)

        if client and track.get('id'):
            threading.Thread(target=self._fetch_full_data, daemon=True).start()

    def exec(self):
        from PyQt6.QtCore import QEventLoop
        main_win = self.parent().window() if self.parent() else None
        margin = 40
        if main_win:
            max_h = main_win.height() - margin * 2
            content_h = self._rows_content.sizeHint().height()
            dialog_h = min(content_h + 62, max_h)
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
            QTimer.singleShot(150, self._maybe_close)
        super().changeEvent(event)

    def _maybe_close(self):
        from PyQt6.QtWidgets import QApplication
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            self.reject()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.bg = QFrame()
        self.bg.setObjectName("trackInfoBg")
        self.bg.setStyleSheet("""
            QFrame#trackInfoBg {
                background-color: #111;
                border: 1px solid #2a2a2a;
                border-radius: 10px;
            }
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
        title_lbl.setStyleSheet("color: #fff; font-size: 17px; font-weight: bold; background: transparent;")
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
        div.setStyleSheet("background: #222;")
        root.addWidget(div)

        # Scrollable rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
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
            ('Genres',         t.get('genre', '')),
            ('Duration',       t.get('duration', '')),
            ('Is compilation', '__bool__:' + str(bool(t.get('compilation')))),
            ('Codec',          t.get('suffix', '') or t.get('codec', '')),
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
        img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'img', 'yes.png' if checked else 'no.png')
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
            lbl.setStyleSheet("color: #666; font-size: 13px; background: transparent;")
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
                    fallback_style="color: #ddd; font-size: 13px; background: transparent;"
                )
                lay.addWidget(val_lbl, 1)

            elif value.startswith('__album__:'):
                album_name = value[len('__album__:'):]
                val_lbl = self._build_link_label(
                    album_name, separator=None,
                    callback=self.on_album_click,
                    fallback_style="color: #ddd; font-size: 13px; background: transparent;"
                )
                lay.addWidget(val_lbl, 1)

            else:
                val_lbl = QLabel(value or '—')
                val_lbl.setStyleSheet("color: #ddd; font-size: 13px; background: transparent;")
                val_lbl.setWordWrap(True)
                val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                val_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                lay.addWidget(val_lbl, 1)

            self._value_labels[label] = lay.itemAt(lay.count() - 1).widget()

        self.rows_layout.addWidget(row_widget)
        sep_wrap = QHBoxLayout()
        sep_wrap.setContentsMargins(20, 0, 20, 0)
        sep = QWidget()
        sep.setFixedHeight(2)
        sep.setStyleSheet("background: #2a2a2a;")
        sep_wrap.addWidget(sep)
        self.rows_layout.addLayout(sep_wrap)

    def _build_link_label(self, text, separator, callback, fallback_style):
        """Returns a QLabel. If callback is set, renders clickable HTML links."""
        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lbl.setStyleSheet("background: transparent;")

        if callback and text:
            parts = [p.strip() for p in text.split(separator)] if separator else [text]
            parts = [p for p in parts if p]
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
            lbl.linkActivated.connect(lambda href: callback(href))
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

        def load_white_icon(img_name, size=14):
            img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img', img_name)
            px = QPixmap(img_path).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
            white = QPixmap(px.size())
            white.fill(QColor('transparent'))
            p = QPainter(white)
            p.drawPixmap(0, 0, px)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(white.rect(), QColor('#ffffff'))
            p.end()
            return QIcon(white)

        def icon_btn(img_name, tip, callback):
            b = QPushButton()
            b.setIcon(load_white_icon(img_name))
            b.setIconSize(QSize(14, 14))
            b.setToolTip(tip)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background: #222; border: 1px solid #333; border-radius: 3px;
                }
                QPushButton:hover { background: #2e2e2e; }
            """)
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
        lbl_header.setStyleSheet("color: #666; font-size: 13px; background: transparent;")
        left_lay.addWidget(lbl_header)

        icons_row = QHBoxLayout()
        icons_row.setSpacing(4)
        icons_row.setContentsMargins(0, 0, 0, 0)
        self._current_path = path
        self._copy_btn = icon_btn("copy-path.png", "Copy path",
                                   lambda: QGuiApplication.clipboard().setText(self._current_path))
        def _open_folder():
            p = self._current_path
            if p and os.path.isabs(p):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))
        self._open_btn = icon_btn("open-path.png", "Open containing folder", _open_folder)
        icons_row.addWidget(self._copy_btn)
        icons_row.addWidget(self._open_btn)
        icons_row.addStretch()
        left_lay.addLayout(icons_row)
        outer.addWidget(left)

        # Right column: path text, word-wrapped, aligned with other values
        self._path_lbl = QLabel(path or '—')
        self._path_lbl.setStyleSheet("color: #ddd; font-size: 13px; background: transparent;")
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

        updates = {
            'Codec':           raw.get('suffix', ''),
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