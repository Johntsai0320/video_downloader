"""Background QThread workers for downloads and non-blocking yt-dlp core updates."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

import yt_dlp
from PyQt6.QtCore import QThread, pyqtSignal

from src.core.downloader import UniversalDownloaderEngine


class DownloadWorker(QThread):
    """Runs UniversalDownloaderEngine off the UI thread."""

    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        url: str,
        save_path: str,
        quality_option: str,
        browser_name: str,
        cookie_file: str,
        is_playlist: bool,
        playlist_range: str,
    ):
        super().__init__()
        self.url = url
        self.save_path = save_path
        self.quality_option = quality_option
        self.browser_name = browser_name
        self.cookie_file = cookie_file
        self.is_playlist = is_playlist
        self.playlist_range = playlist_range
        self.is_cancelled = False

    def run(self) -> None:
        engine = UniversalDownloaderEngine(
            on_progress=self.progress_signal.emit,
            on_status=self.status_signal.emit,
            on_log=self.log_signal.emit,
            is_cancelled=lambda: self.is_cancelled,
        )
        try:
            result = engine.download(
                url=self.url,
                save_path=self.save_path,
                quality_option=self.quality_option,
                cookie_file=self.cookie_file,
                browser_name=self.browser_name,
                is_playlist=self.is_playlist,
                playlist_range=self.playlist_range,
            )
            suffix = f"（{result.mode_note}）" if result.mode_note else ""
            self.finished_signal.emit(True, f"已成功儲存{suffix}：{result.title}")
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class CoreUpdateWorker(QThread):
    """Checks for or upgrades yt-dlp in a background thread without blocking the GUI."""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def run(self) -> None:
        current_ver = getattr(yt_dlp.version, "__version__", "unknown")

        # Inside a PyInstaller frozen .exe, sys.executable is the .exe itself, not python.exe.
        if getattr(sys, "frozen", False):
            try:
                req = urllib.request.Request(
                    "https://pypi.org/pypi/yt-dlp/json",
                    headers={"User-Agent": "UniversalVideoHub/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latest_ver = data.get("info", {}).get("version", "unknown")
                if latest_ver == current_ver:
                    self.finished_signal.emit(
                        True,
                        f"目前封裝版內建 yt-dlp ({current_ver}) 已是最新版本。",
                    )
                else:
                    self.finished_signal.emit(
                        True,
                        f"目前內建版本為 {current_ver}，PyPI 最新版本為 {latest_ver}。\n"
                        "獨立封裝執行檔 (.exe) 無法直接透過 pip 原地覆寫，請使用原始碼環境升級後重新打包。",
                    )
            except Exception as exc:
                self.finished_signal.emit(False, f"檢查版本失敗：{exc}")
            return

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default,curl-cffi,deno]"],
                capture_output=True,
                text=True,
                creationflags=creationflags,
                check=False,
            )
            if proc.stdout:
                for line in proc.stdout.strip().splitlines()[-5:]:
                    self.log_signal.emit(line)
            if proc.returncode != 0:
                err_text = (proc.stderr or proc.stdout or "pip 回傳非零狀態").strip()
                self.finished_signal.emit(False, err_text)
                return

            self.finished_signal.emit(
                True,
                f"yt-dlp 解析模組已檢查/更新完成（啟動時載入版本：{current_ver}，建議重啟程式套用新版）。",
            )
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))
