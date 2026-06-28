import os
import sys
import platform
import glob
import subprocess
import shutil


def find_curl_flags():
    """Auto-detect curl include/lib flags using pkg-config, then common fallback paths.
    Returns (cflags, libs, bin_dir) where bin_dir is the runtime DLL directory (Windows only)."""
    # Try pkg-config first (works on Linux and macOS with Homebrew)
    if shutil.which("pkg-config"):
        try:
            cflags = subprocess.check_output(
                ["pkg-config", "--cflags", "libcurl"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            libs = subprocess.check_output(
                ["pkg-config", "--libs", "libcurl"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            print(f"  curl flags via pkg-config: {cflags} {libs}")
            return cflags, libs, None
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass

    current_os = platform.system()

    if current_os == "Windows":
        # Common install locations on Windows
        candidates = [
            r"C:\msys64\mingw64",
            r"C:\msys64\ucrt64",
            r"C:\curl",
            r"C:\Program Files\curl",
            r"C:\vcpkg\installed\x64-windows",
        ]
        for base in candidates:
            if os.path.exists(os.path.join(base, "include", "curl", "curl.h")):
                inc = os.path.join(base, "include")
                lib = os.path.join(base, "lib")
                bin_dir = os.path.join(base, "bin")
                print(f"  curl found at: {base}")
                return f"-I \"{inc}\"", f"-L \"{lib}\" -lcurl", bin_dir
        print("  ERROR: libcurl not found.")
        print("  Install MSYS2 from https://www.msys2.org/ then run:")
        print("    pacman -S mingw-w64-x86_64-curl")
        print("  And add C:\\msys64\\mingw64\\bin to your Windows PATH.")
        print("  Or set CURL_DIR to your curl install root.")
        curl_dir = os.environ.get("CURL_DIR", "")
        if curl_dir:
            return (f"-I \"{os.path.join(curl_dir, 'include')}\"",
                    f"-L \"{os.path.join(curl_dir, 'lib')}\" -lcurl",
                    os.path.join(curl_dir, "bin"))
        return "", "-lcurl", None

    elif current_os == "Darwin":
        # Homebrew puts curl here
        brew_curl = "/opt/homebrew/opt/curl"
        if not os.path.exists(brew_curl):
            brew_curl = "/usr/local/opt/curl"
        if os.path.exists(brew_curl):
            return f"-I {brew_curl}/include", f"-L {brew_curl}/lib -lcurl", None
        return "", "-lcurl", None

    else:
        # Linux — system curl is usually enough
        return "", "-lcurl", None


def bundle_runtime_dlls(dll_name, bin_dir, libs_dir):
    """Copy all non-system runtime DLLs needed by dll_name into libs_dir."""
    if not bin_dir or not os.path.isdir(bin_dir):
        return

    os.makedirs(libs_dir, exist_ok=True)
    visited = set()
    queue = [os.path.abspath(dll_name)]
    while queue:
        target = queue.pop()
        try:
            out = subprocess.check_output(["objdump", "-p", target], text=True, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for dep in [line.split()[-1] for line in out.splitlines() if "DLL Name:" in line]:
            if dep in visited:
                continue
            visited.add(dep)
            src = os.path.join(bin_dir, dep)
            dest = os.path.join(libs_dir, dep)
            if os.path.isfile(src) and not os.path.isfile(dest):
                shutil.copy2(src, dest)
                print(f"  Bundled: {dep}")
                queue.append(dest)


def _pyqt6_qt_version():
    """Returns PyQt6-Qt6's installed version string (e.g. "6.10.2"), or None.
    The native plugin must match this build exactly — PyQt6 on Windows bundles
    an MSVC-built Qt6 (msvcp140.dll/vcruntime140.dll ship alongside it), so a
    MinGW-built plugin can never load into it regardless of version, and even
    an MSVC-built plugin against the wrong minor/patch version gets rejected
    by Qt's plugin loader with "uses incompatible Qt library"."""
    try:
        from importlib.metadata import version
        return version("PyQt6-Qt6")
    except Exception:
        return None


def _find_qt6_cmake_prefix():
    """Best-effort Qt6 CMake prefix detection for environments where it
    isn't already on CMAKE_PREFIX_PATH by default — mainly Windows, where
    neither MSYS2's mingw64 Qt6 package nor the official Qt installer
    register themselves globally, and a CI runner's PATH ordering between
    a pre-installed system cmake.exe and MSYS2's isn't reliable enough to
    depend on. Returns None (and cmake falls back to its own default
    search, which already works fine on Linux/apt and macOS/Homebrew) if
    nothing is found here.

    On Windows this must resolve to an MSVC-built Qt6 kit (see
    _pyqt6_qt_version's docstring) — MinGW kits (MSYS2's qt6-declarative,
    or the official installer's mingw_64 component) are only considered as
    a last-resort fallback that will compile but fail to load at runtime."""
    current_os = platform.system()
    if current_os == "Windows":
        target_version = _pyqt6_qt_version()
        qt_root = r"C:\Qt"
        msvc_candidates = []
        mingw_candidates = [r"C:\msys64\mingw64"]
        if os.path.isdir(qt_root):
            for entry in sorted(os.listdir(qt_root), reverse=True):
                entry_dir = os.path.join(qt_root, entry)
                if not os.path.isdir(entry_dir):
                    continue
                for kit in os.listdir(entry_dir):
                    if kit.startswith("msvc"):
                        # Exact version match (entry == target_version) sorts
                        # first since we want the closest possible ABI match.
                        msvc_candidates.append((entry != target_version, os.path.join(entry_dir, kit)))
                    elif kit.startswith("mingw"):
                        mingw_candidates.append(os.path.join(entry_dir, kit))
        msvc_candidates.sort(key=lambda pair: pair[0])
        for _, base in msvc_candidates:
            if os.path.exists(os.path.join(base, "lib", "cmake", "Qt6", "Qt6Config.cmake")):
                return base, "msvc"
        for base in mingw_candidates:
            if os.path.exists(os.path.join(base, "lib", "cmake", "Qt6", "Qt6Config.cmake")):
                print(f"  WARNING: only found a MinGW Qt6 kit ({base}) — PyQt6 on Windows")
                print(f"           uses an MSVC-built Qt6, so this plugin won't load at")
                print(f"           runtime. Install a matching msvc2022_64 kit instead.")
                return base, "mingw"
    elif current_os == "Darwin":
        for base in ("/opt/homebrew/opt/qt6", "/usr/local/opt/qt6",
                      "/opt/homebrew/opt/qt", "/usr/local/opt/qt"):
            if os.path.exists(os.path.join(base, "lib", "cmake", "Qt6", "Qt6Config.cmake")):
                return base, None
    return None, None


def build_scratch_waveform_plugin():
    """Builds the native QML scratch-waveform plugin (player/native/scratch_waveform/)
    via CMake — a separate Qt6 Quick module, not part of audio_core's plain-
    ctypes build above. Required for scratch mode's waveform view; the rest
    of the app runs fine without it. Non-fatal if cmake or Qt6 dev tools
    aren't installed — prints what's missing and continues."""
    print()
    print("Attempting to build the scratch-waveform QML plugin...")

    plugin_dir = os.path.join("player", "native", "scratch_waveform")
    if not os.path.exists(os.path.join(plugin_dir, "CMakeLists.txt")):
        print("ERROR: scratch_waveform CMakeLists.txt not found — skipping.")
        return

    if not shutil.which("cmake"):
        print("SKIPPED: cmake not found — scratch mode's waveform view won't be available.")
        print("  Linux:   sudo apt install cmake qt6-base-dev qt6-declarative-dev")
        print("  macOS:   brew install cmake qt6")
        print("  Windows: install CMake (https://cmake.org/download/) and Qt6")
        print("           (https://www.qt.io/download-qt-installer) with the Quick/QML modules,")
        print("           then re-run this script from a shell where both are on PATH.")
        return

    build_dir = os.path.join(plugin_dir, "build")
    configure_cmd = ["cmake", "-S", plugin_dir, "-B", build_dir]
    qt_prefix, qt_kit_type = _find_qt6_cmake_prefix()
    if qt_prefix:
        configure_cmd.append(f"-DCMAKE_PREFIX_PATH={qt_prefix}")
        print(f"  Qt6 found at: {qt_prefix}")
    # Generator must match the located Qt6 kit's compiler ABI: PyQt6 bundles
    # an MSVC-built Qt6 on Windows, so an MSVC kit needs MSVC (cl.exe) — the
    # default VS generator auto-detects cl.exe reliably even when MinGW's
    # g++ is also on PATH (unlike the Ninja generator, which just picks
    # whichever compiler comes first in PATH and silently produces a MinGW-
    # linked plugin that fails with unresolved __imp_* symbols against
    # MSVC-built Qt import libs). A MinGW kit (fallback only — see
    # _find_qt6_cmake_prefix) needs MinGW Makefiles explicitly, or cmake
    # defaults to VS/MSVC against MinGW Qt headers/libs, which fails to
    # compile at all (ABI mismatch — the literal symptom is an MSVC
    # /Zc:__cplusplus error from qcompilerdetection.h). CMakeLists.txt
    # itself forces flat (non-per-config) output dirs so the VS generator's
    # multi-config Debug/Release subfolders don't break the plugin's
    # expected runtime path.
    if qt_kit_type == "mingw":
        configure_cmd += ["-G", "MinGW Makefiles"]
    sys.stdout.flush()
    result = subprocess.run(configure_cmd)
    if result.returncode != 0:
        print("SKIPPED: cmake configure failed (likely Qt6 not found) — scratch mode's")
        print("  waveform view won't be available. See the cmake output above for details.")
        return

    sys.stdout.flush()
    build_cmd = ["cmake", "--build", build_dir]
    if qt_kit_type == "msvc":
        # PyQt6 bundles a Release Qt6 build; the VS generator defaults to
        # Debug if --config isn't given. Qt's plugin loader rejects a Debug-
        # built plugin against a Release runtime ("Cannot mix debug and
        # release libraries"), same as the version/ABI checks above.
        build_cmd += ["--config", "Release"]
    result = subprocess.run(build_cmd)
    if result.returncode == 0:
        print("SUCCESS: Built the scratch-waveform QML plugin.")
    else:
        print("FAILED: scratch-waveform plugin compile error. See output above.")


def build():
    print("Attempting to compile audio_core.cpp...")

    src_dir = os.path.join("player", "components")

    if not os.path.exists(os.path.join(src_dir, "audio_core.cpp")):
        print("ERROR: audio_core.cpp not found!")
        return

    if not os.path.exists(os.path.join(src_dir, "miniaudio.h")):
        print("ERROR: miniaudio.h not found!")
        return

    if not shutil.which("g++"):
        if current_os == "Windows":
            print("ERROR: g++ not found.")
            print("  Install MSYS2 from https://www.msys2.org/")
            print("  Then in the MSYS2 MinGW64 terminal run:")
            print("    pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-curl")
            print("  And add C:\\msys64\\mingw64\\bin to your Windows PATH.")
        elif current_os == "Darwin":
            print("ERROR: g++ not found. Install Xcode command line tools: xcode-select --install")
        else:
            print("ERROR: g++ not found. Install it with: sudo apt install g++ libcurl4-openssl-dev")
        return

    # qm-dsp sources (Queen Mary beat tracker — replaces SoundTouch BPMDetect)
    qm_sources = " ".join([
        "qm-dsp/dsp/onsets/DetectionFunction.cpp",
        "qm-dsp/dsp/tempotracking/TempoTrackV2.cpp",
        "qm-dsp/dsp/transforms/FFT.cpp",
        "qm-dsp/dsp/phasevocoder/PhaseVocoder.cpp",
        "qm-dsp/maths/MathUtilities.cpp",
        "qm-dsp/ext/kissfft/kiss_fft.c",
        "qm-dsp/ext/kissfft/tools/kiss_fftr.c",
    ])

    current_os = platform.system()
    print(f"Detected Platform: {current_os}")
    print("Using Queen Mary qm-dsp for BPM detection.")

    curl_inc, curl_lib, curl_bin = find_curl_flags()

    qm_defines = "-Dkiss_fft_scalar=double"

    if current_os == "Windows":
        output_filename = os.path.join(src_dir, "audio_core.dll")
        flags = f"-O2 -static-libgcc -static-libstdc++ -Wl,--export-all-symbols -I . -I ./qm-dsp {qm_defines} {curl_inc} {curl_lib}"
    elif current_os == "Darwin":
        output_filename = os.path.join(src_dir, "audio_core.dylib")
        flags = f"-O2 -fPIC -dynamiclib -I . -I ./qm-dsp {qm_defines} {curl_inc} {curl_lib}"
    else:
        output_filename = os.path.join(src_dir, "audio_core.so")
        flags = f"-O2 -fPIC -I . -I ./qm-dsp {qm_defines} {curl_inc} {curl_lib}"

    cmd = f"g++ -shared -o {output_filename} {os.path.join(src_dir, 'audio_core.cpp')} {qm_sources} {flags}"
    print(f"Executing: {cmd}")

    sys.stdout.flush()
    result = subprocess.run(cmd, shell=True)
    if result.returncode == 0:
        print(f"SUCCESS: Compiled {output_filename}")
        if current_os == "Windows":
            bundle_runtime_dlls(output_filename, curl_bin, os.path.join(src_dir, "libs"))
    else:
        print("FAILED: Compile error. Check the output above for details.")

    build_scratch_waveform_plugin()


if __name__ == "__main__":
    build()
