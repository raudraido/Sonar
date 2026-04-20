import os
import sys
import ast
import platform
import importlib
import subprocess
import pkgutil

# PyInstaller uses ':' on Linux/macOS and ';' on Windows for --add-data / --add-binary
_SEP = ";" if platform.system() == "Windows" else ":"

# Maps import names to their pip package name when they differ
IMPORT_TO_PIP = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "wx": "wxPython",
    "yaml": "PyYAML",
    "usb": "pyusb",
    "serial": "pyserial",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "OpenGL": "PyOpenGL",
    "pydub": "pydub",
    "mutagen": "mutagen",
    "sounddevice": "sounddevice",
    "soundfile": "SoundFile",
    "pychromecast": "pychromecast",
    "zeroconf": "zeroconf",
    "aiohttp": "aiohttp",
    "async_upnp_client": "async-upnp-client",
}

# Standard library modules — never try to pip install these
STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()


def collect_imports_from_file(filepath):
    """Parse a Python file with AST and return all top-level imported module names."""
    imports = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, FileNotFoundError) as e:
        print(f"  WARNING: Could not parse {filepath}: {e}")
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    return imports


def collect_all_imports(entry_point):
    """Recursively collect imports from entry point and all local .py files."""
    all_imports = set()
    visited = set()
    queue = [entry_point]

    for file in os.listdir("."):
        if file.endswith(".py") and file != os.path.basename(__file__):
            queue.append(file)

    for filepath in queue:
        if filepath in visited or not os.path.exists(filepath):
            continue
        visited.add(filepath)
        all_imports |= collect_imports_from_file(filepath)

    return all_imports


def is_module_available(module_name):
    return importlib.util.find_spec(module_name) is not None


def install_package(pip_name):
    print(f"  Installing: {pip_name} ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_name],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  ✔ Installed: {pip_name}")
        return True
    else:
        print(f"  ✘ Failed to install {pip_name}:\n{result.stderr.strip()}")
        return False


def resolve_and_install_dependencies(entry_point):
    print("\n--- Scanning for Dependencies ---")
    all_imports = collect_all_imports(entry_point)

    missing = []
    for module in sorted(all_imports):
        if module in STDLIB_MODULES:
            continue
        if is_module_available(module):
            print(f"  ✔ Found:    {module}")
        else:
            pip_name = IMPORT_TO_PIP.get(module, module)
            missing.append((module, pip_name))
            print(f"  ✘ Missing:  {module}  (pip: {pip_name})")

    if not missing:
        print("\n  All dependencies are already satisfied.")
        return True

    print(f"\n--- Installing {len(missing)} Missing Package(s) ---")
    failed = []
    for module, pip_name in missing:
        if not install_package(pip_name):
            failed.append(pip_name)

    if failed:
        print(f"\n  WARNING: The following packages could not be installed automatically:")
        for pkg in failed:
            print(f"    - {pkg}")
        print("  You may need to install them manually before the build will succeed.")
        return False

    return True


def find_qt_plugins_dir():
    """Locate the PyQt6 Qt6 plugins directory using QLibraryInfo."""
    try:
        from PyQt6.QtCore import QLibraryInfo
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
        if os.path.isdir(path):
            return path
    except Exception:
        pass

    # Fallback: walk the PyQt6 package directory
    try:
        import PyQt6
        candidate = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
        if os.path.isdir(candidate):
            return candidate
    except Exception:
        pass

    return None


def bundle_linux_qt_platform_plugins(added_data):
    """
    On Linux, PyInstaller misses the Wayland (and XCB) platform plugins.
    Find them in the PyQt6 package or system Qt6 and add them explicitly.
    """
    print("\n--- Bundling Qt Platform Plugins (Linux) ---")

    plugins_dir = find_qt_plugins_dir()
    platforms_dir = os.path.join(plugins_dir, "platforms") if plugins_dir else None

    # Fallback to the system Qt6 location on Ubuntu/Debian
    if not platforms_dir or not os.path.isdir(platforms_dir):
        system_fallback = "/usr/lib/x86_64-linux-gnu/qt6/plugins/platforms"
        if os.path.isdir(system_fallback):
            platforms_dir = system_fallback
            print(f"  Using system Qt6 platforms: {platforms_dir}")
        else:
            print("  WARNING: Could not find Qt platform plugins directory.")
            return

    want = ("libqwayland-egl.so", "libqwayland-generic.so", "libqxcb.so")
    found_any = False
    for name in want:
        full = os.path.join(platforms_dir, name)
        if os.path.exists(full):
            added_data.append(f"--add-binary={full}{_SEP}PyQt6/Qt6/plugins/platforms")
            print(f"  Bundled: {name}")
            found_any = True
        else:
            print(f"  Not found (optional): {name}")

    if not found_any:
        print("  WARNING: No platform plugins found. Install qt6-wayland:")
        print("    sudo apt install qt6-wayland libqt6waylandclient6")

    # Also bundle the wayland platform integration libraries if present
    wayland_integration_dirs = [
        os.path.join(plugins_dir, "wayland-shell-integration") if plugins_dir else "",
        os.path.join(plugins_dir, "wayland-graphics-integration-client") if plugins_dir else "",
        "/usr/lib/x86_64-linux-gnu/qt6/plugins/wayland-shell-integration",
        "/usr/lib/x86_64-linux-gnu/qt6/plugins/wayland-graphics-integration-client",
    ]
    for d in wayland_integration_dirs:
        if d and os.path.isdir(d):
            folder_name = os.path.basename(d)
            for lib in os.listdir(d):
                if lib.endswith(".so"):
                    full = os.path.join(d, lib)
                    added_data.append(f"--add-binary={full}{_SEP}PyQt6/Qt6/plugins/{folder_name}")
                    print(f"  Bundled: {folder_name}/{lib}")


def find_audio_binary():
    """Return the audio_core binary filename for the current platform, or None."""
    current_os = platform.system()
    candidates = {
        "Windows": "audio_core.dll",
        "Darwin":  "audio_core.dylib",
        "Linux":   "audio_core.so",
    }
    name = candidates.get(current_os)
    if name and os.path.exists(name):
        return name
    # Fallback: check all known names in case of a cross-build
    for n in candidates.values():
        if os.path.exists(n):
            return n
    return None


def generate_ico():
    """Convert the existing icon.png to a perfectly square icon.ico for the Windows build."""
    ico_path = os.path.join('img', 'icon.ico')
    png_path = os.path.join('img', 'icon.png')
    
    try:
        from PIL import Image
    except ImportError:
        print("  Pillow not available — skipping ICO generation.")
        return False

    if not os.path.exists(png_path):
        print(f"  WARNING: {png_path} not found! Cannot generate ICO.")
        return False

    try:
        # 1. Open your PNG
        img = Image.open(png_path)
        
        # 2. Find the longest side to make a perfect square
        max_size = max(img.width, img.height)
        
        # 3. Create a brand new perfectly square, transparent image
        square_img = Image.new('RGBA', (max_size, max_size), (0, 0, 0, 0))
        
        # 4. Paste your original logo dead center into the square
        offset = ((max_size - img.width) // 2, (max_size - img.height) // 2)
        square_img.paste(img, offset)

        # 5. Save the perfectly square image as an ICO
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        square_img.save(ico_path, format='ICO', sizes=sizes)
        
        print(f"  Successfully squared and converted {png_path} to {ico_path}")
        return True
        
    except Exception as e:
        print(f"  ERROR failed to convert icon: {e}")
        return False


def build():
    entry_point = "main.py"
    app_name    = "Sonar"
    current_os  = platform.system()

    # 1. Auto-detect and install missing dependencies
    resolve_and_install_dependencies(entry_point)

    added_data = []

    # 2. Gather image assets
    asset_extensions = ('.png', '.jpg', '.jpeg', '.ico', '.gif', '.svg')
    print("\n--- Collecting Assets ---")
    img_dir = 'img'
    if os.path.exists(img_dir):
        for file in os.listdir(img_dir):
            if file.lower().endswith(asset_extensions):
                src = os.path.join(img_dir, file)
                added_data.append(f'--add-data={src}{_SEP}img')
                print(f"  Added Asset: {file}")

    # 3. Gather QML files
    print("\n--- Collecting QML Files ---")
    qml_found = False
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ("dist", "build", "__pycache__", ".git")]
        for file in files:
            if file.lower().endswith(".qml"):
                src_path = os.path.join(root, file)
                dest_dir = root.lstrip("./\\") or "."
                added_data.append(f'--add-data={src_path}{_SEP}{dest_dir}')
                print(f"  Added QML:   {src_path}  →  {dest_dir}")
                qml_found = True
    if not qml_found:
        print("  No QML files found.")

    # 4. Bundle Qt Wayland + XCB platform plugins on Linux
    if current_os == "Linux":
        bundle_linux_qt_platform_plugins(added_data)

    # 6. Bundle the platform-specific audio engine binary + its dependency DLLs
    print("\n--- Detecting Audio Engine Binary ---")
    binary = find_audio_binary()
    if binary:
        added_data.append(f'--add-binary={binary}{_SEP}.')
        print(f"  Added Binary: {binary}")
    else:
        print("  WARNING: No audio_core binary found. Run build.py first.")

    if current_os == "Windows":
        libs_dir = "libs"
        if os.path.isdir(libs_dir):
            print("\n--- Bundling audio_core dependency DLLs ---")
            for dll in os.listdir(libs_dir):
                if dll.lower().endswith(".dll"):
                    src = os.path.join(libs_dir, dll)
                    added_data.append(f'--add-binary={src}{_SEP}libs')
                    print(f"  Added DLL: {dll}")
        else:
            print("  WARNING: libs/ folder not found — audio_core.dll dependencies may be missing.")

    # 7. PyInstaller arguments
    args = [
        entry_point,
        f'--name={app_name}',
        '--noconsole',
        '--onefile',
        '--clean',
        '--windowed',
        '--collect-all=psutil',
        '--collect-all=pychromecast',
        '--collect-all=zeroconf',
        '--collect-all=aiohttp',
        '--collect-all=async_upnp_client',
        '--collect-all=ifaddr',
        '--hidden-import=pychromecast',
        '--hidden-import=pychromecast.discovery',
        '--hidden-import=pychromecast.controllers',
        '--hidden-import=pychromecast.controllers.media',
        '--hidden-import=zeroconf',
        '--hidden-import=zeroconf._utils.ipaddress',
        '--hidden-import=zeroconf._dns',
        '--hidden-import=aiohttp',
        '--hidden-import=async_upnp_client',
        '--hidden-import=async_upnp_client.search',
    ] + added_data

    # Generate icon.ico from the Sonar design (Windows requires .ico)
    print("\n--- Generating App Icon ---")
    if current_os == "Windows":
        generate_ico()

    # Attach exe/app icon (prefer .ico on Windows, fall back to .png)
    icon_candidates = ['img/icon.ico', 'img/icon.png']
    for candidate in icon_candidates:
        if os.path.exists(candidate):
            args.append(f'--icon={candidate}')
            print(f"  Using app icon: {candidate}")
            break

    # 8. Run PyInstaller
    import PyInstaller.__main__
    print("\n--- Starting Build Process ---")
    PyInstaller.__main__.run(args)

    # 9. Report output path
    print("\n--- Build Complete ---")
    exe_suffix = ".exe" if current_os == "Windows" else ""
    print(f"Check the 'dist' folder for {app_name}{exe_suffix}")


if __name__ == "__main__":
    build()
