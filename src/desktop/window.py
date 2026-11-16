"""Main PyQt6 application window for Universal Video Hub."""

from __future__ import annotations

import os
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.cookies import (
    BrowserCookieError,
    BrowserCookieLockedError,
    extract_browser_cookies_to_file,
)
from src.core.downloader import QUALITY_PRESETS
from src.core.ffmpeg_helper import find_ffmpeg
from src.desktop.workers import CoreUpdateWorker, DownloadWorker


class VideoDownloaderGUI(QMainWindow):
    """Desktop GUI window for multi-platform media downloading."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("全網影音下載器 · Universal Video Hub")
        self.resize(780, 770)
        self.download_worker: DownloadWorker | None = None
        self.update_worker: CoreUpdateWorker | None = None

        self.default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "VideoDownloads")
        os.makedirs(self.default_dir, exist_ok=True)

        self._setup_ui()
        self._log_startup_info()

    def _setup_ui(self) -> None:
        container = QWidget()
        container.setObjectName("MainContainer")
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(14)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        title = QLabel("Universal Video Hub")
        title.setObjectName("HeaderTitle")
        desc = QLabel(
            "支援 YouTube / Shorts、Bilibili (自動影音合併)、Instagram、Threads、X (Twitter)、TikTok"
        )
        desc.setObjectName("HeaderSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        root_layout.addLayout(header_layout)

        # Card 1: URL & Playlist
        card1 = QFrame()
        card1.setProperty("class", "CardFrame")
        c1_layout = QVBoxLayout(card1)
        c1_layout.setContentsMargins(16, 14, 16, 14)
        c1_layout.setSpacing(10)

        c1_title = QLabel("目標網址與清單選項")
        c1_title.setObjectName("SectionTitle")
        c1_layout.addWidget(c1_title)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("請貼上影片或播放清單網址 (支援短網址 / Threads / X 代理連結)...")
        btn_paste = QPushButton("貼上剪貼簿")
        btn_paste.setProperty("class", "SecondaryBtn")
        btn_paste.clicked.connect(
            lambda: self.url_edit.setText(QApplication.clipboard().text().strip())
        )
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(btn_paste)
        c1_layout.addLayout(url_row)

        playlist_row = QHBoxLayout()
        self.chk_playlist = QCheckBox("啟用播放清單下載 (Playlist)")
        self.chk_playlist.stateChanged.connect(self.toggle_playlist_fields)
        self.lbl_range = QLabel("範圍指定:")
        self.lbl_range.setEnabled(False)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("例: 1-5 (留空抓取全部)")
        self.range_edit.setEnabled(False)
        self.range_edit.setFixedWidth(160)
        playlist_row.addWidget(self.chk_playlist)
        playlist_row.addSpacing(16)
        playlist_row.addWidget(self.lbl_range)
        playlist_row.addWidget(self.range_edit)
        playlist_row.addStretch()
        c1_layout.addLayout(playlist_row)
        root_layout.addWidget(card1)

        # Card 2: Quality & Cookies
        card2 = QFrame()
        card2.setProperty("class", "CardFrame")
        c2_layout = QVBoxLayout(card2)
        c2_layout.setContentsMargins(16, 14, 16, 14)
        c2_layout.setSpacing(10)

        c2_title = QLabel("下載規格與憑證設定")
        c2_title.setObjectName("SectionTitle")
        c2_layout.addWidget(c2_title)

        fmt_row = QHBoxLayout()
        fmt_label = QLabel("輸出規格：")
        fmt_label.setFixedWidth(85)
        self.combo_format = QComboBox()
        self.combo_format.addItems([preset["label"] for preset in QUALITY_PRESETS.values()])
        fmt_row.addWidget(fmt_label)
        fmt_row.addWidget(self.combo_format, 1)
        c2_layout.addLayout(fmt_row)

        browser_row = QHBoxLayout()
        b_label = QLabel("目標瀏覽器：")
        b_label.setFixedWidth(85)
        self.combo_browser = QComboBox()
        self.combo_browser.addItems(["不使用 (訪客)", "Firefox", "Chrome", "Edge", "Brave", "Opera"])

        self.btn_extract_browser = QPushButton("🔍 一鍵抓取瀏覽器 Cookie")
        self.btn_extract_browser.setProperty("class", "AccentBtn")
        self.btn_extract_browser.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_extract_browser.clicked.connect(self.extract_browser_cookies)

        browser_row.addWidget(b_label)
        browser_row.addWidget(self.combo_browser)
        browser_row.addWidget(self.btn_extract_browser)
        browser_row.addStretch()
        c2_layout.addLayout(browser_row)

        cookie_row = QHBoxLayout()
        cookie_label = QLabel("Cookie 檔案：")
        cookie_label.setFixedWidth(85)
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText(
            "可手動選取 cookies.txt 或使用上方一鍵抓取，留空則以訪客模式連線..."
        )
        btn_browse_cookie = QPushButton("選取檔案")
        btn_browse_cookie.setProperty("class", "SecondaryBtn")
        btn_browse_cookie.clicked.connect(self.browse_cookie_file)

        btn_clear_cookie = QPushButton("清除")
        btn_clear_cookie.setProperty("class", "SecondaryBtn")
        btn_clear_cookie.clicked.connect(self.cookie_file_edit.clear)

        cookie_row.addWidget(cookie_label)
        cookie_row.addWidget(self.cookie_file_edit, 1)
        cookie_row.addWidget(btn_browse_cookie)
        cookie_row.addWidget(btn_clear_cookie)
        c2_layout.addLayout(cookie_row)
        root_layout.addWidget(card2)

        # Card 3: Save Directory
        card3 = QFrame()
        card3.setProperty("class", "CardFrame")
        c3_layout = QHBoxLayout(card3)
        c3_layout.setContentsMargins(16, 12, 16, 12)
        c3_layout.setSpacing(8)

        path_label = QLabel("儲存路徑：")
        path_label.setFixedWidth(85)
        self.path_edit = QLineEdit(self.default_dir)
        btn_browse = QPushButton("變更位置")
        btn_browse.setProperty("class", "SecondaryBtn")
        btn_browse.clicked.connect(self.browse_folder)
        btn_open = QPushButton("開啟資料夾")
        btn_open.setProperty("class", "SecondaryBtn")
        btn_open.clicked.connect(self.open_folder)

        c3_layout.addWidget(path_label)
        c3_layout.addWidget(self.path_edit, 1)
        c3_layout.addWidget(btn_browse)
        c3_layout.addWidget(btn_open)
        root_layout.addWidget(card3)

        # Action Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(12)
        self.btn_start = QPushButton("開始極速下載")
        self.btn_start.setObjectName("PrimaryActionBtn")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_download)

        self.btn_cancel = QPushButton("中止任務")
        self.btn_cancel.setObjectName("CancelActionBtn")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_download)

        self.btn_update = QPushButton("升級核心 (yt-dlp)")
        self.btn_update.setProperty("class", "SecondaryBtn")
        self.btn_update.clicked.connect(self.update_core)

        btn_bar.addWidget(self.btn_start, 2)
        btn_bar.addWidget(self.btn_cancel, 1)
        btn_bar.addWidget(self.btn_update, 1)
        root_layout.addLayout(btn_bar)

        # Progress & Log
        status_box = QVBoxLayout()
        status_box.setSpacing(6)

        self.lbl_status = QLabel("狀態：就緒")
        self.lbl_status.setStyleSheet("font-weight: 600; color: #334155;")
        status_box.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status_box.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("TerminalLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(130)
        status_box.addWidget(self.log_view)

        root_layout.addLayout(status_box)
        self.setCentralWidget(container)

    def _log_startup_info(self) -> None:
        ffmpeg_exe = find_ffmpeg()
        if ffmpeg_exe:
            self.log_view.append(f"[SYSTEM] FFmpeg 已就緒：{ffmpeg_exe}")
        else:
            self.log_view.append("[WARN] 未偵測到 FFmpeg，高畫質影像與音訊可能無法自動合併。")

    def _selected_browser(self) -> str:
        choice = self.combo_browser.currentText()
        if choice.startswith("不使用"):
            return ""
        return choice.lower()

    def toggle_playlist_fields(self) -> None:
        enabled = self.chk_playlist.isChecked()
        self.lbl_range.setEnabled(enabled)
        self.range_edit.setEnabled(enabled)

    def extract_browser_cookies(self) -> None:
        browser = self._selected_browser()
        if not browser:
            QMessageBox.information(
                self,
                "請選擇瀏覽器",
                "請先在「目標瀏覽器」下拉選單中選擇 Firefox、Chrome、Edge 等瀏覽器。",
            )
            return

        self.log_view.append(f">> 正在嘗試讀取本機 {browser.capitalize()} 的登入憑證...")
        try:
            output_path = extract_browser_cookies_to_file(browser, self.default_dir)
            self.cookie_file_edit.setText(output_path)
            self.log_view.append(f"[SUCCESS] 成功匯出 Cookie 至：{output_path}")
            QMessageBox.information(
                self,
                "抓取成功",
                f"已成功從 {browser.capitalize()} 抓取憑證並自動代入！\n路徑：{output_path}",
            )
        except BrowserCookieLockedError as exc:
            self.log_view.append(f"[ERROR] 抓取受限：{exc}")
            QMessageBox.warning(
                self,
                "讀取受限 (資料庫鎖定 / App-Bound 加密)",
                f"{browser.capitalize()} Cookie 資料庫目前被系統或背景程序保護，無法直接讀取。\n\n"
                "建議解決方式：\n"
                "1. 若使用 Chrome / Edge，請至工作管理員完全關閉瀏覽器後再試。\n"
                "2. 若仍受 Chrome 127+ App-Bound 加密阻擋，建議改用【Firefox】。\n"
                "3. 或透過瀏覽器擴充套件「Get cookies.txt LOCALLY」匯出後，點擊下方【選取檔案】載入。",
            )
        except BrowserCookieError as exc:
            self.log_view.append(f"[ERROR] 抓取失敗：{exc}")
            QMessageBox.warning(self, "抓取失敗", f"未能成功提取憑證：\n{exc}")

    def browse_cookie_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "選取 cookies.txt", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self.cookie_file_edit.setText(file_path)

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "選擇儲存資料夾", self.path_edit.text())
        if folder:
            self.path_edit.setText(folder)

    def open_folder(self) -> None:
        target = self.path_edit.text()
        if os.path.exists(target):
            if sys.platform == "win32":
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])

    def start_download(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "請先輸入有效的影片網址！")
            return

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_status.setText("狀態：連線並分析串流資料中...")
        self.log_view.append(f">> 開始任務：{url}")

        self.download_worker = DownloadWorker(
            url=url,
            save_path=self.path_edit.text().strip() or self.default_dir,
            quality_option=self.combo_format.currentText(),
            browser_name=self._selected_browser(),
            cookie_file=self.cookie_file_edit.text(),
            is_playlist=self.chk_playlist.isChecked(),
            playlist_range=self.range_edit.text(),
        )
        self.download_worker.progress_signal.connect(self.progress.setValue)
        self.download_worker.status_signal.connect(self.lbl_status.setText)
        self.download_worker.log_signal.connect(self.log_view.append)
        self.download_worker.finished_signal.connect(self.on_download_complete)
        self.download_worker.start()

    def cancel_download(self) -> None:
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.is_cancelled = True
            self.lbl_status.setText("狀態：正在安全中止任務...")

    def on_download_complete(self, success: bool, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if success:
            self.lbl_status.setText("狀態：任務順利完成！")
            self.log_view.append(f"[SUCCESS] {message}\n")
        else:
            self.lbl_status.setText("狀態：下載發生異常")
            self.log_view.append(f"[ERROR] {message}\n")

    def update_core(self) -> None:
        if self.update_worker and self.update_worker.isRunning():
            return

        self.btn_update.setEnabled(False)
        self.log_view.append(">> 正在背景檢查並更新 yt-dlp 核心模組...")

        self.update_worker = CoreUpdateWorker()
        self.update_worker.log_signal.connect(self.log_view.append)
        self.update_worker.finished_signal.connect(self.on_update_complete)
        self.update_worker.start()

    def on_update_complete(self, success: bool, message: str) -> None:
        self.btn_update.setEnabled(True)
        if success:
            self.log_view.append(f"[SUCCESS] {message}\n")
            QMessageBox.information(self, "核心檢查/更新", message)
        else:
            self.log_view.append(f"[ERROR] 更新失敗：{message}\n")
            QMessageBox.warning(self, "更新失敗", message)

    def closeEvent(self, event) -> None:
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.is_cancelled = True
            self.download_worker.wait(1500)
        if self.update_worker and self.update_worker.isRunning():
            self.update_worker.wait(1000)
        super().closeEvent(event)
