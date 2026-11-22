# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT_DIR = os.path.dirname(SPEC_DIR)

datas = []
binaries = []
for tool in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = os.path.join(r"C:\ffmpeg\bin", tool)
    if os.path.isfile(candidate):
        binaries.append((candidate, "."))

hiddenimports = [
    "src.core.cookies",
    "src.core.downloader",
    "src.core.ffmpeg_helper",
    "src.desktop.styles",
    "src.desktop.window",
    "src.desktop.workers",
]

for pkg_name in ("yt_dlp", "yt_dlp_ejs", "curl_cffi"):
    try:
        tmp_ret = collect_all(pkg_name)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception:
        pass

try:
    import deno as _deno_pkg

    deno_exe = _deno_pkg.find_deno_bin()
    if deno_exe and os.path.isfile(deno_exe):
        binaries.append((deno_exe, "."))
except Exception:
    pass

a = Analysis(
    [os.path.join(ROOT_DIR, "main.py")],
    pathex=[ROOT_DIR],
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
    [],
    exclude_binaries=True,
    name="UniversalVideoHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="UniversalVideoHub",
)
