"""FFmpeg and FFprobe discovery across source, PyInstaller bundles, and Windows paths."""

from __future__ import annotations

import os
import shutil
import sys


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs.append(exe_dir)
        dirs.append(os.path.join(exe_dir, "_internal"))
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        dirs.append(project_root)

    if sys.platform == "win32":
        dirs.append(r"C:\ffmpeg\bin")

    return dirs


def _find_binary(name: str) -> str | None:
    exe_names = [f"{name}.exe", f"{name}.EXE", name] if sys.platform == "win32" else [name]

    # 1. PyInstaller bundle / project root / standard Windows folder
    for directory in _candidate_dirs():
        for exe_name in exe_names:
            candidate = os.path.join(directory, exe_name)
            if os.path.isfile(candidate):
                return candidate

    # 2. System PATH
    found = shutil.which(name)
    if found:
        return found

    return None


def find_ffmpeg() -> str | None:
    """Locate the ffmpeg executable, falling back to imageio-ffmpeg if installed."""
    found = _find_binary("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    return None


def find_ffprobe() -> str | None:
    """Locate the ffprobe executable if available."""
    return _find_binary("ffprobe")


def get_ffmpeg_location() -> str | None:
    """Return the directory containing ffmpeg (preferred when ffprobe is alongside it) or exe path."""
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        return None
    ffprobe_exe = find_ffprobe()
    if ffprobe_exe and os.path.dirname(os.path.abspath(ffmpeg_exe)) == os.path.dirname(os.path.abspath(ffprobe_exe)):
        return os.path.dirname(os.path.abspath(ffmpeg_exe))
    return ffmpeg_exe
