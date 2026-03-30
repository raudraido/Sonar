from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                             QCheckBox, QPushButton, QMessageBox, QComboBox)
from PyQt6.QtCore import Qt

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Server")
        
        # Make the window taller and wider for a premium layout
        self.setFixedSize(400, 500)
        
        # Modern Flat Styling
        self.setStyleSheet("""
            QDialog { 
                background-color: #0a0a0a; 
            }
            QLabel#Title {
                color: #ffffff;
                font-size: 28px;
                font-weight: 900;
                letter-spacing: -1px;
            }
            QLabel#Subtitle {
                color: #888888;
                font-size: 14px;
                margin-bottom: 20px;
            }
            QLabel { 
                color: #b3b3b3; 
                font-size: 11px; 
                font-weight: bold; 
                letter-spacing: 1px;
            }
            QLineEdit, QComboBox { 
                background-color: #141414; 
                border: 1px solid #2a2a2a; 
                border-radius: 8px; 
                padding: 12px 16px; 
                color: #ffffff; 
                font-size: 14px;
            }
            QLineEdit:focus, QComboBox:focus { 
                border: 1px solid #ffffff; 
                background-color: #1a1a1a;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox QAbstractItemView {
                background-color: #141414;
                color: white;
                selection-background-color: #333333;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
                outline: none;
            }
            QPushButton#ConnectBtn {
                background-color: #ffffff; 
                color: #000000; 
                border-radius: 8px; 
                padding: 8px 14px; /* 8px top/bottom, 14px left/right */
                min-height: 45px;  /* Force the button to stay tall enough */
                font-size: 15px; 
                font-weight: bold; 
                margin-top: 10px;
            }
            QPushButton#ConnectBtn:hover { 
                background-color: #e0e0e0; 
            }
            QPushButton#ConnectBtn:pressed { 
                background-color: #cccccc; 
            }
            QCheckBox { 
                color: #b3b3b3; 
                font-size: 13px; 
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #444;
                background-color: #141414;
            }
            QCheckBox::indicator:hover {
                border: 1px solid #888;
            }
            QCheckBox::indicator:checked {
                background-color: #ffffff;
                border: 1px solid #ffffff;
            }
        """)

        layout = QVBoxLayout(self)
        # Give the whole window massive inner padding so it breathes
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(8)

        # --- HEADER ---
        title = QLabel("Sonar")
        title.setObjectName("Title")
        
        subtitle = QLabel("")
        subtitle.setObjectName("Subtitle")
        
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)

        # --- INPUTS ---
        layout.addWidget(QLabel("SERVER URL"))
        self.url_input = QComboBox()
        self.url_input.setEditable(True)
        self.url_input.lineEdit().setPlaceholderText("e.g. https://music.yourdomain.com")
        layout.addWidget(self.url_input)
        layout.addSpacing(5)

        layout.addWidget(QLabel("USERNAME"))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("Enter your username")
        layout.addWidget(self.user_input)
        layout.addSpacing(5)

        layout.addWidget(QLabel("PASSWORD"))
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("Enter your password")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.pass_input)
        layout.addSpacing(15)

        # --- CONTROLS ---
        self.remember_cb = QCheckBox("Remember my credentials")
        self.remember_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.remember_cb)
        
        layout.addStretch()

        # --- SUBMIT BUTTON ---
        self.login_btn = QPushButton("Connect")
        self.login_btn.setObjectName("ConnectBtn")
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.clicked.connect(self.validate_and_accept)
        layout.addWidget(self.login_btn)

        self.load_saved_credentials()

    def load_saved_credentials(self):
        """Pre-fill the form if the user saved their info last time."""
        from PyQt6.QtCore import QSettings
        import keyring
        
        settings = QSettings()
        
        # 1. Load the history of successful URLs
        history = settings.value("navidrome/url_history", [])
        if not history: history = []
        if isinstance(history, str): history = [history]
        history = [h for h in list(history) if h]
        
        if history:
            self.url_input.addItems(history)
            
        # 2. Load the last used credentials
        saved_url = settings.value("navidrome/url", "")
        saved_user = settings.value("navidrome/username", "")

        if saved_url:
            self.url_input.setCurrentText(saved_url)
            
        if saved_user:
            self.user_input.setText(saved_user)
            saved_pass = keyring.get_password("Sonar", saved_user)
            if saved_pass:
                self.pass_input.setText(saved_pass)
                self.remember_cb.setChecked(True)

    def validate_and_accept(self):
        url = self.url_input.currentText().strip()
        user = self.user_input.text().strip()
        pwd = self.pass_input.text()

        if not url or not user or not pwd:
            QMessageBox.warning(self, "Error", "All fields are required!")
            return

        if not url.startswith("http"):
            url = "http://" + url
            self.url_input.setCurrentText(url)

        from PyQt6.QtWidgets import QApplication
        from subsonic_client import SubsonicClient
        
        
        self.login_btn.setText("Connecting...")
        self.login_btn.setEnabled(False)
        QApplication.processEvents()

        # Test the connection with detailed error reporting
        temp_client = SubsonicClient(url, user, pwd)
        success, error_msg = temp_client.test_connection()
        
        if success:
            self.accept()
        else:
            QMessageBox.critical(self, "Login Failed", error_msg)
            
            self.login_btn.setText("Connect")
            self.login_btn.setEnabled(True)