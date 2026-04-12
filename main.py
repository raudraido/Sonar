"""
main.py — Application entry point for Sonar Music Player.

Handles login, credential management, and launches the main window.
All application logic lives in the player/ package.
"""
import os
import sys
import json
import keyring
import ctypes
import platform
import threading

os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
if platform.system() == "Linux":
    os.environ["QT_QPA_PLATFORMTHEME"] = ""  # Disable GTK/KDE theme override
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # Use XWayland so Qt stylesheets apply to tooltips

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QIcon

from subsonic_client import SubsonicClient
from login_dialog import LoginDialog
from player.window import SonarPlayer


def _background_preload():
    """
    Warm the OS file cache and initialise singletons while the user is on the
    login screen (or while auto-login is pinging the server).  Everything here
    is thread-safe and has no Qt objects.
    """
    # 1. Load the audio DLL — the OS keeps it resident after the first load,
    #    so AudioEngine.__init__() finds it already in memory and is instant.
    try:
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        dll_name = 'audio_core.dll' if platform.system() == 'Windows' else 'audio_core.so'
        dll_path = os.path.join(base, dll_name)
        if os.path.exists(dll_path):
            ctypes.CDLL(dll_path)
    except Exception:
        pass

    # 2. Create the CoverCache singleton and ensure the cache directory exists.
    try:
        from cover_cache import CoverCache
        CoverCache.instance()
    except Exception:
        pass

    # 3. Read the BPM cache JSON into the module so SonarPlayer can skip the
    #    file read entirely.
    try:
        import player.mixins.playback as _pb
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        bpm_path = os.path.join(base_dir, 'app_data', 'json_data', 'bpm_cache.json')
        if os.path.exists(bpm_path):
            with open(bpm_path, 'r') as f:
                _pb._preloaded_bpm_cache = json.load(f)
    except Exception:
        pass


# ─── APPLICATION ENTRY POINT ────────────────────────────────────────────────

if __name__ == '__main__':
    # Kick off background preloading immediately — runs in parallel with login UI
    threading.Thread(target=_background_preload, daemon=True).start()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # REQUIRED: Tells QSettings exactly where to save your data in the OS
    app.setApplicationName("Sonar")
    app.setOrganizationName("Sonar")
    _base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    app.setWindowIcon(QIcon(os.path.join(_base, "img", "icon.png")))
    app.setDesktopFileName("Sonar")
    
    settings = QSettings()
    
    # 1. Check if we have saved credentials
    url = settings.value("navidrome/url", "")
    user = settings.value("navidrome/username", "")
    
    # 🟢 Fetch the password securely from the OS Keyring
    password = keyring.get_password("Sonar", user) if user else None
    
    client = None
    
    # 2. Try to auto-login silently in the background
    if url and user and password:
        try:
            client = SubsonicClient(url, user, password)
            # 🟢 NEW: Actually test if the saved credentials still work!
            if not client.ping():
                print("Saved credentials failed or server offline. Falling back to dialog.")
                client = None # Force the login dialog to open
        except Exception as e:
            print(f"Auto-login failed: {e}")
            client = None
            
    # 3. If auto-login fails (or it's the first time), show the Login Dialog
    if not client:
        dialog = LoginDialog()
        
        while True:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                # 🟢 Use currentText() for the Combo Box
                url = dialog.url_input.currentText().strip()  
                user = dialog.user_input.text().strip()
                password = dialog.pass_input.text()
                
                try:
                    # Attempt connection
                    client = SubsonicClient(url, user, password)
                    
                    # 🟢 Connection Succeeded! Add URL to history safely (keep max 5)
                    history = settings.value("navidrome/url_history", [])
                    if not history: history = []
                    if isinstance(history, str): history = [history]
                    history = [h for h in list(history) if h]
                    
                    if url in history: history.remove(url)
                    history.insert(0, url)
                    settings.setValue("navidrome/url_history", history[:5])
                    
                    # 🟢 Handle Credentials Save/Delete
                    if dialog.remember_cb.isChecked():
                        settings.setValue("navidrome/url", url)
                        settings.setValue("navidrome/username", user)
                        keyring.set_password("Sonar", user, password)
                    else:
                        settings.remove("navidrome/url")
                        settings.remove("navidrome/username")
                        try: keyring.delete_password("Sonar", user)
                        except keyring.errors.PasswordDeleteError: pass
                        
                    break # Success! Break the loop and launch the app
                    
                except Exception as e:
                    QMessageBox.critical(None, "Connection Failed", f"Could not connect to Navidrome.\nError: {e}")
            else:
                # User clicked the X to close the window, so kill the app entirely
                sys.exit(0)
                
    # 4. Launch the main UI using our successfully authenticated client!
    window = SonarPlayer(client)
    window.show()

    sys.exit(app.exec())
