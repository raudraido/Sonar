<p align="center">
  <img src="img/icon.png" width="120" alt="Sonar logo">
</p>

# Sonar Music Player

Keyboard warrior friendly desktop music player for self-hosted [Navidrome](https://www.navidrome.org/) servers (Subsonic-compatible API). Built with Python/PyQt6 and a custom C++ audio engine for gapless playback.

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-GPL--3.0-green)

---

## Screenshots

| Albums | Now Playing |
|--------|-------------|
| ![Albums browser](media/1.png) | ![Now Playing queue](media/2.png) |

| Tracks & Search | Artist Page |
|-----------------|-------------|
| ![Tracks browser](media/3.png) | ![Artist detail](media/4.png) |

---

## Features

- **Gapless playback** — tracks cross-fade seamlessly via the C++ audio engine
- **Waveform scrubber** — real-time waveform display with turntable scratch mode
- **BPM detection** — automatic BPM analysis cached per track (via QM DSP Library)
- **Dynamic theming** — accent colour extracted from album art, or pick your own
- **Spotlight search** — global search across artists, albums, and tracks
- **Media key support** — play/pause/next/prev via keyboard media keys (Windows & Linux)
- **Crossfade backgrounds** — blurred album art as the window background
- **Now Playing queue** — drag-to-reorder, favourite toggling, context menus
- **Reorganizable tabs** — reorder browser tabs to match your workflow
- **Cast support** — stream to Chromecast and AirPlay devices

---

## Requirements

- Python 3.10+
- A running [Navidrome](https://www.navidrome.org/) server (or any Subsonic-compatible server)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/raudraido/Sonar.git
cd Sonar
```

### 2. Install a C++ compiler with libcurl

The audio engine is a compiled C++ library. You need g++ and libcurl before running `build.py`.

**Windows — install MSYS2 (recommended)**

1. Download and install [MSYS2](https://www.msys2.org/)
2. Open the **MSYS2 MinGW64** terminal and run:
   ```bash
   pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-curl
   ```
3. Add `C:\msys64\mingw64\bin` to your Windows PATH  
   *(Search "Edit the system environment variables" → Environment Variables → Path → New)*

**Linux (Debian/Ubuntu)**

```bash
sudo apt install g++ libcurl4-openssl-dev
```

**Linux (Fedora/RHEL)**

```bash
sudo dnf install gcc-c++ libcurl-devel
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

**Linux only** — install evdev for media key support:
```bash
pip install evdev
```

### 4. Build the C++ audio engine

```bash
python build.py
```

This compiles `audio_core.cpp` and outputs `audio_core.dll` (Windows) or `audio_core.so` (Linux) in the project root. On Windows it also copies the required runtime DLLs into `libs/` automatically.

### 5. Run

```bash
python main.py
```

On first launch you will be prompted to enter your Navidrome server URL, username, and password. Credentials can optionally be saved to your OS keyring.

---

## Building a Standalone Executable

```bash
python build_exe.py
```

This uses PyInstaller to produce a single-file executable in `dist/`. The C++ `.dll`/`.so` is bundled automatically.

---

## Linux Media Keys

The Linux media key listener reads directly from `/dev/input/`. Your user may need to be in the `input` group:

```bash
sudo usermod -aG input $USER
# Log out and back in for this to take effect
```

The player auto-detects the correct input device at startup.

---

## Configuration

All settings are stored via Qt's `QSettings` (registry on Windows, `~/.config` on Linux). Passwords are stored in the OS keyring — never in plain text.

---

## Contributing

Pull requests are welcome. Please open an issue first for anything larger than a bug fix.

---

## License

Sonar is free software released under the **GNU General Public License v3.0**.  
See [LICENSE](LICENSE) for the full license text.

This means you are free to use, study, modify, and distribute Sonar, provided
that any distributed version (modified or not) is also released under the GPL-3.0.

---

## Third-Party Acknowledgements

Sonar is built on the shoulders of several excellent open-source libraries.  
See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the full list of
dependencies and their respective copyright notices and licenses.

Key dependencies include:

| Library | License | Purpose |
|---------|---------|---------|
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) | GPL-3.0 | UI framework |
| [miniaudio](https://miniaud.io/) | MIT-0 / Public Domain | Audio playback engine |
| [mutagen](https://mutagen.readthedocs.io/) | GPL-2.0+ | Audio tag reading |
| [Pillow](https://python-pillow.org/) | HPND | Image processing |
| [requests](https://requests.readthedocs.io/) | Apache-2.0 | HTTP / Navidrome API |
| [qm-dsp](https://github.com/c4dm/qm-dsp) | GPL-2.0 | BPM detection |
| [pychromecast](https://github.com/home-assistant-libs/pychromecast) | MIT | Chromecast support |
| [pyatv](https://pyatv.dev/) | MIT | AirPlay support |
| [keyring](https://github.com/jaraco/keyring) | MIT | Secure credential storage |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | Memory monitoring |
| [pynput](https://github.com/moses-palmer/pynput) | LGPL-3.0 | Media key support |
