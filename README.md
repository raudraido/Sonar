<p align="center">
  <img src="img/icon.png" width="120" alt="Icosahedron logo">
</p>

# Icosahedron

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

### 2. Install build tools

There are two native components: the audio engine (plain g++/libcurl) and the
scratch-mode waveform view, a Qt6 Quick module built with CMake. The second
one is optional — the app runs fine without it, you just won't get the
scratch-mode waveform — so `build.py` skips it with a warning if CMake/Qt6
aren't found instead of failing the whole build.

**Windows — install MSYS2 (recommended) + a matching MSVC Qt6 kit**

The audio engine (`audio_core.cpp`) and the scratch-waveform QML plugin are
two separate native builds with different requirements: the former is a
plain g++/libcurl build, the latter must be compiled against an **MSVC**-built
Qt6 at the *exact* version pinned in `requirements.txt` (`PyQt6-Qt6`). PyQt6
on Windows bundles its own private MSVC-built Qt6 — Qt's QML plugin loader
rejects any version or compiler-ABI (MinGW vs MSVC) mismatch outright, so a
MinGW-built plugin, or one built against a different Qt6 patch version, will
compile fine but silently fail at runtime (the whole footer panel fails to
load, since `footer_bar.qml`'s `import FooterNativeWaveform 1.0` is
unconditional).

1. Download and install [MSYS2](https://www.msys2.org/) — provides g++/curl/cmake
   for the audio engine only:
   ```bash
   pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-curl mingw-w64-x86_64-cmake
   ```
   Add `C:\msys64\mingw64\bin` to your Windows PATH
   *(Search "Edit the system environment variables" → Environment Variables → Path → New)*
2. Install the matching MSVC Qt6 kit via [aqtinstall](https://github.com/miurahr/aqtinstall)
   (the official Qt binaries, fetched without the GUI installer) — keep the
   version in sync with `requirements.txt`'s `PyQt6-Qt6` pin:
   ```bash
   pip install aqtinstall
   aqt install-qt windows desktop 6.10.2 win64_msvc2022_64 -O C:\Qt
   ```
   `build.py` auto-detects this kit under `C:\Qt\<version>\msvc*` and picks
   it over any MinGW Qt6 kit it finds (preferring an exact version match to
   `PyQt6-Qt6`). If you ever bump `PyQt6`/`PyQt6-Qt6` in `requirements.txt`,
   reinstall this kit at the new version too.

**Linux (Debian/Ubuntu)**

```bash
sudo apt install g++ libcurl4-openssl-dev cmake qt6-base-dev qt6-declarative-dev
```

**Linux (Fedora/RHEL)**

```bash
sudo dnf install gcc-c++ libcurl-devel cmake qt6-qtbase-devel qt6-qtdeclarative-devel
```

**macOS**

```bash
brew install cmake qt6
```
(g++/libcurl ship with Xcode command line tools: `xcode-select --install`)

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

**Linux only** — install evdev for media key support:
```bash
pip install evdev
```

### 4. Build the native components

```bash
python build.py
```

This compiles `audio_core.cpp` and outputs `audio_core.dll` (Windows) or
`audio_core.so` (Linux) in `player/components/`. On Windows it also copies
the required runtime DLLs into `libs/` automatically.

It then builds the scratch-mode waveform QML plugin via CMake (into
`player/native/scratch_waveform/build/`). If CMake or Qt6 aren't installed,
this step is skipped with a warning — the rest of the app still works, just
without the scratch-mode waveform view. On Windows, `build.py` looks under
`C:\Qt\` for an MSVC kit matching `PyQt6-Qt6`'s version and builds against
that in `Release` config (see the install step above for why); it falls back
to a MinGW kit with a warning if no MSVC kit is found, but that fallback
plugin won't actually load at runtime.

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

Icosahedron is free software released under the **GNU General Public License v3.0**.  
See [LICENSE](LICENSE) for the full license text.

This means you are free to use, study, modify, and distribute Icosahedron, provided
that any distributed version (modified or not) is also released under the GPL-3.0.

---

## Third-Party Acknowledgements

Icosahedron is built on the shoulders of several excellent open-source libraries.  
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
