#!/usr/bin/env python3
"""
build_appimage.py — Builds a single portable Linux binary for Sonar.

Uses PyInstaller --onefile which self-extracts to /tmp at runtime.
No FUSE, no AppImage tooling, no _internal/ folder — just one file.

Output: dist/Sonar  (run it anywhere on Linux x86_64)
"""
import os
import sys
import platform
import subprocess

# Reuse helpers from build_exe.py
from build_exe import (
    resolve_and_install_dependencies,
    bundle_linux_qt_platform_plugins,
    find_audio_binary,
    _SEP,
)

APP_NAME    = "Sonar"
ENTRY_POINT = "main.py"


def _find_python_shared_lib():
    """Return the path to the versioned libpython .so needed at runtime."""
    import sysconfig
    import glob

    ver   = f"{sys.version_info.major}.{sys.version_info.minor}"
    ldver = sysconfig.get_config_var("LDVERSION") or ver

    search_names = [
        f"libpython{ldver}.so.1.0",
        f"libpython{ver}.so.1.0",
        f"libpython{ver}.so",
    ]
    search_dirs = [
        sysconfig.get_config_var("LIBDIR") or "",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib",
        "/usr/local/lib",
        os.path.join(sys.base_prefix, "lib"),
    ]

    for name in search_names:
        for d in search_dirs:
            candidate = os.path.join(d, name)
            if os.path.exists(candidate):
                return candidate
        for d in search_dirs:
            matches = glob.glob(os.path.join(d, name))
            if matches:
                return matches[0]
    return None


def collect_assets():
    added = []
    asset_exts = ('.png', '.jpg', '.jpeg', '.ico', '.gif', '.svg')

    print("\n--- Collecting Assets ---")
    if os.path.exists("img"):
        for f in os.listdir("img"):
            if f.lower().endswith(asset_exts):
                added.append(f"--add-data={os.path.join('img', f)}{_SEP}img")
                print(f"  Asset: {f}")

    print("\n--- Collecting QML Files ---")
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ("dist", "build", "__pycache__", ".git")]
        for f in files:
            if f.lower().endswith(".qml"):
                src = os.path.join(root, f)
                dest = root.lstrip("./\\") or "."
                added.append(f"--add-data={src}{_SEP}{dest}")
                print(f"  QML: {src}")

    bundle_linux_qt_platform_plugins(added)

    print("\n--- Detecting Python Shared Library ---")
    py_so = _find_python_shared_lib()
    if py_so:
        added.append(f"--add-binary={py_so}{_SEP}.")
        print(f"  Bundled: {os.path.basename(py_so)}")
    else:
        print("  WARNING: libpython .so not found — run: sudo apt install libpython3-dev")

    print("\n--- Detecting Audio Engine Binary ---")
    binary = find_audio_binary()
    if binary:
        added.append(f"--add-binary={binary}{_SEP}.")
        print(f"  Bundled: {binary}")
    else:
        print("  WARNING: audio_core.so not found — run build.py first.")

    return added


def _write_desktop_file(binary_path):
    """Write a .desktop file next to the binary and install the icon so the OS taskbar shows it."""
    import shutil
    binary_abs = os.path.abspath(binary_path)
    dist_dir   = os.path.dirname(binary_abs)

    # Copy icon into dist/ so it travels with the binary
    src_icon = os.path.join("img", "icon.png")
    dst_icon = os.path.join(dist_dir, "sonar.png")
    if os.path.exists(src_icon):
        shutil.copy2(src_icon, dst_icon)
        print(f"  Icon copied: {dst_icon}")

    desktop_content = f"""[Desktop Entry]
Name=Sonar
Comment=Sonar Music Player
Exec=env QT_QPA_PLATFORM=xcb {binary_abs}
Icon={dst_icon}
Type=Application
Categories=Audio;Music;Player;
Terminal=false
StartupWMClass=Sonar
"""
    desktop_path = os.path.join(dist_dir, "Sonar.desktop")
    with open(desktop_path, "w") as f:
        f.write(desktop_content)
    os.chmod(desktop_path, 0o755)
    print(f"  Desktop file: {desktop_path}")
    print(f"  To install system-wide so the taskbar shows your icon:")
    print(f"    cp {dst_icon} ~/.local/share/icons/sonar.png")
    print(f"    cp {desktop_path} ~/.local/share/applications/")
    print(f"    update-desktop-database ~/.local/share/applications/")


def build():
    if platform.system() != "Linux":
        print("This script is for Linux builds only.")
        sys.exit(1)

    resolve_and_install_dependencies(ENTRY_POINT)
    added_data = collect_assets()

    print("\n--- Running PyInstaller (onefile) ---")

    args = [
        ENTRY_POINT,
        f"--name={APP_NAME}",
        "--noconsole",
        "--onefile",       # single self-extracting binary — no _internal/ needed
        "--clean",
        "--windowed",
    ] + added_data

    import PyInstaller.__main__
    PyInstaller.__main__.run(args)

    output = os.path.join("dist", APP_NAME)
    if os.path.exists(output):
        _write_desktop_file(output)
        print(f"\n  SUCCESS: {output}")
        print(f"  Copy this one file anywhere and run:")
        print(f"    chmod +x {APP_NAME}")
        print(f"    QT_QPA_PLATFORM=xcb ./{APP_NAME}")
    else:
        print("\n  Build failed — check PyInstaller output above.")


if __name__ == "__main__":
    build()
