import os
import sys
import platform
import glob
import subprocess
import shutil


def find_curl_flags():
    """Auto-detect curl include/lib flags using pkg-config, then common fallback paths."""
    # Try pkg-config first (works on Linux and macOS with Homebrew)
    if shutil.which("pkg-config"):
        try:
            cflags = subprocess.check_output(
                ["pkg-config", "--cflags", "libcurl"], text=True
            ).strip()
            libs = subprocess.check_output(
                ["pkg-config", "--libs", "libcurl"], text=True
            ).strip()
            print(f"  curl flags via pkg-config: {cflags} {libs}")
            return cflags, libs
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass

    current_os = platform.system()

    if current_os == "Windows":
        # Common install locations on Windows
        candidates = [
            r"C:\msys64\mingw64",
            r"C:\curl",
            r"C:\Program Files\curl",
            r"C:\vcpkg\installed\x64-windows",
        ]
        for base in candidates:
            if os.path.exists(os.path.join(base, "include", "curl", "curl.h")):
                inc = os.path.join(base, "include")
                lib = os.path.join(base, "lib")
                print(f"  curl found at: {base}")
                return f"-I \"{inc}\"", f"-L \"{lib}\" -lcurl"
        print("  WARNING: curl not found. Set CURL_DIR env var or install via vcpkg.")
        curl_dir = os.environ.get("CURL_DIR", "")
        if curl_dir:
            return f"-I \"{os.path.join(curl_dir, 'include')}\"", \
                   f"-L \"{os.path.join(curl_dir, 'lib')}\" -lcurl"
        return "", "-lcurl"

    elif current_os == "Darwin":
        # Homebrew puts curl here
        brew_curl = "/opt/homebrew/opt/curl"
        if not os.path.exists(brew_curl):
            brew_curl = "/usr/local/opt/curl"
        if os.path.exists(brew_curl):
            return f"-I {brew_curl}/include", f"-L {brew_curl}/lib -lcurl"
        return "", "-lcurl"

    else:
        # Linux — system curl is usually enough
        return "", "-lcurl"


def build():
    print("Attempting to compile audio_core.cpp...")

    if not os.path.exists("audio_core.cpp"):
        print("ERROR: audio_core.cpp not found!")
        return

    if not os.path.exists("miniaudio.h"):
        print("ERROR: miniaudio.h not found!")
        return

    if not shutil.which("g++"):
        print("ERROR: g++ not found. Install build-essential (Linux), Xcode CLI tools (macOS), or MinGW/MSYS2 (Windows).")
        return

    # Auto-detect SoundTouch
    soundtouch_src = glob.glob("SoundTouch/*.cpp")
    has_st_source = len(soundtouch_src) > 0
    soundtouch_compile_str = "SoundTouch/*.cpp" if has_st_source else ""
    soundtouch_link_str = "" if has_st_source else "-L ./SoundTouch -lSoundTouch"

    current_os = platform.system()
    print(f"Detected Platform: {current_os}")

    if has_st_source:
        print("SoundTouch source detected — compiling directly.")
    else:
        print("No SoundTouch source — linking pre-compiled library.")

    curl_inc, curl_lib = find_curl_flags()

    if current_os == "Windows":
        output_filename = "audio_core.dll"
        flags = f"-O2 -static-libgcc -static-libstdc++ -Wl,--export-all-symbols -I . {curl_inc} {soundtouch_link_str} {curl_lib}"
    elif current_os == "Darwin":
        output_filename = "audio_core.dylib"
        flags = f"-O2 -fPIC -dynamiclib -I . {curl_inc} {soundtouch_link_str} {curl_lib}"
    else:
        output_filename = "audio_core.so"
        flags = f"-O2 -fPIC -I . {curl_inc} {soundtouch_link_str} {curl_lib}"

    cmd = f"g++ -shared -o {output_filename} audio_core.cpp {soundtouch_compile_str} {flags}"
    print(f"Executing: {cmd}")

    result = subprocess.run(cmd, shell=True)
    if result.returncode == 0:
        print(f"SUCCESS: Compiled {output_filename}")
    else:
        print("FAILED: Compile error. Check the output above for details.")


if __name__ == "__main__":
    build()
