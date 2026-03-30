import os, sys

from PyQt6.QtWidgets import QDialog, QToolButton, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget, QCheckBox, QFrame
from PyQt6.QtCore import (Qt, pyqtSignal, QSize, QParallelAnimationGroup, 
                          QPropertyAnimation, QEasingCurve)
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor

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

class SmartSearchContainer(QWidget):
    text_changed = pyqtSignal(str)
    burger_clicked = pyqtSignal()

    def __init__(self, parent=None, placeholder="Search..."):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        
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
        self.search_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.search_btn.clicked.connect(self.toggle_search)
        
        # --- BURGER BUTTON ---
        self.burger_btn = QPushButton()
        self.burger_btn.setIcon(QIcon(resource_path("img/burger.png")))
        self.burger_btn.setIconSize(QSize(18, 18))
        self.burger_btn.setFixedSize(32, 32)
        self.burger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.burger_btn.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 4px; } QPushButton:hover { background: rgba(255, 255, 255, 0.1); }")
        self.burger_btn.clicked.connect(self.burger_clicked.emit)
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.search_btn)
        layout.addWidget(self.burger_btn)
        
    def _on_text_changed(self, text):
        self.custom_clear_btn.setVisible(bool(text))
        self.text_changed.emit(text)
        
    def toggle_search(self):
        self.anim_group = QParallelAnimationGroup(self)
        anim_min = QPropertyAnimation(self.search_input, b"minimumWidth")
        anim_max = QPropertyAnimation(self.search_input, b"maximumWidth")
        anim_min.setDuration(300); anim_max.setDuration(300)
        anim_min.setEasingCurve(QEasingCurve.Type.InOutQuart)
        anim_max.setEasingCurve(QEasingCurve.Type.InOutQuart)
        
        self.anim_group.addAnimation(anim_min)
        self.anim_group.addAnimation(anim_max)

        TARGET_WIDTH = 230
        
        
        if self.search_input.maximumWidth() == 0:
            anim_min.setStartValue(0); anim_min.setEndValue(TARGET_WIDTH)
            anim_max.setStartValue(0); anim_max.setEndValue(TARGET_WIDTH)
            anim_max.valueChanged.connect(lambda _: self.updateGeometry())
            self.anim_group.start()
            self.search_input.setFocus()
        else:
            current_w = self.search_input.maximumWidth()
            anim_min.setStartValue(current_w); anim_min.setEndValue(0)
            anim_max.setStartValue(current_w); anim_max.setEndValue(0)
            self.anim_group.start()
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