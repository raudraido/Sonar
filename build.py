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

    result = subprocess.run(cmd, shell=True)
    if result.returncode == 0:
        print(f"SUCCESS: Compiled {output_filename}")
        if current_os == "Windows":
            bundle_runtime_dlls(output_filename, curl_bin, os.path.join(src_dir, "libs"))
    else:
        print("FAILED: Compile error. Check the output above for details.")


if __name__ == "__main__":
    build()
