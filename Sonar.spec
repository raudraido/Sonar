# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('img\\airplay.png', 'img'), ('img\\album.png', 'img'), ('img\\burger.png', 'img'), ('img\\cast.png', 'img'), ('img\\close.png', 'img'), ('img\\comp.png', 'img'), ('img\\copy-path.png', 'img'), ('img\\dlna.png', 'img'), ('img\\enter.png', 'img'), ('img\\filter.png', 'img'), ('img\\filter_clear.png', 'img'), ('img\\filter_down-2.png', 'img'), ('img\\filter_down.png', 'img'), ('img\\filter_off-2.png', 'img'), ('img\\filter_off.png', 'img'), ('img\\filter_up-2.png', 'img'), ('img\\filter_up.png', 'img'), ('img\\heart.png', 'img'), ('img\\heart_filled.png', 'img'), ('img\\hide.png', 'img'), ('img\\icon.ico', 'img'), ('img\\icon.png', 'img'), ('img\\import.png', 'img'), ('img\\next.png', 'img'), ('img\\nwo.png', 'img'), ('img\\open-path.png', 'img'), ('img\\open.png', 'img'), ('img\\pause.png', 'img'), ('img\\play-button.png', 'img'), ('img\\play.png', 'img'), ('img\\playing.gif', 'img'), ('img\\prev.png', 'img'), ('img\\refresh.png', 'img'), ('img\\repeat.png', 'img'), ('img\\resize.png', 'img'), ('img\\search.png', 'img'), ('img\\settings.png', 'img'), ('img\\shuffle.png', 'img'), ('img\\sort-alphabetical-a.png', 'img'), ('img\\sort-alphabetical-d.png', 'img'), ('img\\sort-latest-a.png', 'img'), ('img\\sort-latest-d.png', 'img'), ('img\\sort-most_played-a.png', 'img'), ('img\\sort-most_played-d.png', 'img'), ('img\\sort-num-asc.png', 'img'), ('img\\sort-num-desc.png', 'img'), ('img\\sort-random-a.png', 'img'), ('img\\sort-random-d.png', 'img'), ('img\\switch.png', 'img'), ('img\\trash.png', 'img'), ('img\\unhide.png', 'img'), ('img\\volume.png', 'img'), ('img\\volume_mute.png', 'img'), ('img\\yes.png', 'img'), ('.\\album_grid.qml', '.'), ('.\\artist_grid.qml', '.'), ('.\\artist_section_grid.qml', '.'), ('.\\home_row.qml', '.'), ('.\\playlist_grid.qml', '.'), ('.\\SkeletonCard.qml', '.')]
binaries = [('audio_core.dll', '.'), ('libs\\libbrotlicommon.dll', 'libs'), ('libs\\libbrotlidec.dll', 'libs'), ('libs\\libcrypto-3-x64.dll', 'libs'), ('libs\\libcurl-4.dll', 'libs'), ('libs\\libiconv-2.dll', 'libs'), ('libs\\libidn2-0.dll', 'libs'), ('libs\\libintl-8.dll', 'libs'), ('libs\\libnghttp2-14.dll', 'libs'), ('libs\\libnghttp3-9.dll', 'libs'), ('libs\\libngtcp2-16.dll', 'libs'), ('libs\\libngtcp2_crypto_ossl-0.dll', 'libs'), ('libs\\libpsl-5.dll', 'libs'), ('libs\\libssh2-1.dll', 'libs'), ('libs\\libssl-3-x64.dll', 'libs'), ('libs\\libunistring-5.dll', 'libs'), ('libs\\libzstd.dll', 'libs'), ('libs\\zlib1.dll', 'libs')]
hiddenimports = ['pychromecast', 'pychromecast.discovery', 'pychromecast.controllers', 'pychromecast.controllers.media', 'zeroconf', 'zeroconf._utils.ipaddress', 'zeroconf._dns', 'aiohttp', 'async_upnp_client', 'async_upnp_client.search']
tmp_ret = collect_all('psutil')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pychromecast')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('zeroconf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiohttp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('async_upnp_client')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ifaddr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Icosahedron',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['img\\icon.ico'],
)
