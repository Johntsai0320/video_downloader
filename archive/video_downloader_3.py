import os
import sys
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QFileDialog,
    QProgressBar, QTextEdit, QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# 嘗試自動讀取由 imageio-ffmpeg 提供的 ffmpeg 執行檔
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_PATH = None

import yt_dlp

class UniversalDownloadThread(QThread):
    progress_signal = pyqtSignal(float)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, url, save_path, quality_option, use_cookie, browser_name, is_playlist, playlist_range):
        super().__init__()
        self.url = url
        self.save_path = save_path
        self.quality_option = quality_option
        self.use_cookie = use_cookie
        self.browser_name = browser_name.lower()
        self.is_playlist = is_playlist
        self.playlist_range = playlist_range
        self.is_cancelled = False

    def run(self):
        try:
            # 處理 Threads / Instagram 等 Meta 平台常見的追蹤參數與網址規範
            clean_url = self.url.strip()
            if "threads.net" in clean_url or "threads.com" in clean_url:
                clean_url = clean_url.split("?")[0]

            # 命名範本：若是播放清單，加上序號並自動歸類到專屬資料夾
            if self.is_playlist:
                out_template = os.path.join(
                    self.save_path, 
                    '%(playlist_title|播放清單)s', 
                    '%(playlist_index)02d - %(title).80s [%(id)s].%(ext)s'
                )
            else:
                out_template = os.path.join(self.save_path, '%(title).80s [%(id)s].%(ext)s')

            def create_ydl_opts(enable_cookie):
                opts = {
                    'outtmpl': out_template,
                    'progress_hooks': [self.hook],
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'noplaylist': not self.is_playlist,
                    'socket_timeout': 30,
                    # 模擬現代桌面瀏覽器，防止被 Meta/YouTube 等平台導向錯誤網址
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Sec-Fetch-Mode': 'navigate',
                    }
                }

                # 限制播放清單集數範圍
                if self.is_playlist and self.playlist_range.strip():
                    opts['playlist_items'] = self.playlist_range.strip()

                # 自動掛載 FFmpeg
                if FFMPEG_PATH:
                    opts['ffmpeg_location'] = FFMPEG_PATH

                # 讀取瀏覽器 Cookie
                if enable_cookie:
                    opts['cookiesfrombrowser'] = (self.browser_name,)

                # 格式回退規則
                if self.quality_option == "MP4：最佳畫質 (通用)":
                    opts['format'] = 'bestvideo*+bestaudio/best'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP4：1080p 以下":
                    opts['format'] = 'bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP4：720p 以下":
                    opts['format'] = 'bestvideo*[height<=720]+bestaudio/best[height<=720]/best'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP3 音訊擷取":
                    opts['format'] = 'bestaudio/best'
                    opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                return opts

            self.status_signal.emit("正在解析串流並下載內容...")

            # 具備 DPAPI 錯誤自動容錯回退機制
            try:
                with yt_dlp.YoutubeDL(create_ydl_opts(self.use_cookie)) as ydl:
                    info = ydl.extract_info(clean_url, download=True)
                    title = info.get('title', '下載任務')
                    self.finished_signal.emit(True, f"已完成：{title}")
            except Exception as first_error:
                error_msg = str(first_error)
                # 若因 Windows DPAPI 加密報錯，自動改用訪客模式（無 Cookie）重試
                if self.use_cookie and "DPAPI" in error_msg:
                    self.log_signal.emit("【提示】瀏覽器 Cookie 受到系統 DPAPI 加密保護，自動切換至免登入模式重試...")
                    with yt_dlp.YoutubeDL(create_ydl_opts(False)) as ydl:
                        info = ydl.extract_info(clean_url, download=True)
                        title = info.get('title', '下載任務')
                        self.finished_signal.emit(True, f"已完成：{title}")
                else:
                    raise first_error

        except Exception as e:
            self.finished_signal.emit(False, str(e))

    def hook(self, d):
        if self.is_cancelled:
            raise Exception("任務已手動取消。")

        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)

            # 播放清單進度提示
            info_dict = d.get('info_dict', {})
            p_index = info_dict.get('playlist_index')
            p_count = info_dict.get('n_entries')
            prefix = f"[第 {p_index}/{p_count} 部] " if (p_index and p_count) else ""

            if total > 0:
                percent = (downloaded / total) * 100
                self.progress_signal.emit(percent)
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                self.status_signal.emit(f"{prefix}下載中: {percent:.1f}% | 速度: {speed} | 預計剩餘: {eta}")
        elif d['status'] == 'finished':
            self.progress_signal.emit(100.0)
            self.status_signal.emit("正在進行後續轉檔或影音合併...")


class VideoDownloaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("全網影音 / 播放清單萬能下載器")
        self.resize(700, 560)
        self.thread = None

        self.default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "VideoDownloads")
        os.makedirs(self.default_dir, exist_ok=True)

        self.setup_ui()

    def setup_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        # 標題
        title = QLabel("全網通用影音下載器")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        desc = QLabel("支援 YouTube、Threads、FB、IG、X、TikTok、Bilibili 及播放清單（Playlist）下載")
        desc.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(desc)

        # 網址輸入列
        url_layout = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("請貼上影片網址、串流位址或播放清單連結...")
        btn_paste = QPushButton("貼上")
        btn_paste.clicked.connect(self.paste_from_clipboard)
        url_layout.addWidget(self.url_edit)
        url_layout.addWidget(btn_paste)
        layout.addLayout(url_layout)

        # 播放清單選項
        playlist_layout = QHBoxLayout()
        self.chk_playlist = QCheckBox("下載整份播放清單 (Playlist)")
        self.chk_playlist.stateChanged.connect(self.toggle_playlist_fields)

        self.lbl_range = QLabel("集數範圍 (例: 1-5 或 1,3,7):")
        self.lbl_range.setEnabled(False)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("留空則全部下載")
        self.range_edit.setEnabled(False)
        self.range_edit.setFixedWidth(140)

        playlist_layout.addWidget(self.chk_playlist)
        playlist_layout.addWidget(self.lbl_range)
        playlist_layout.addWidget(self.range_edit)
        playlist_layout.addStretch()
        layout.addLayout(playlist_layout)

        # 格式選擇
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("下載規格："))
        self.combo_format = QComboBox()
        self.combo_format.addItems([
            "MP4：最佳畫質 (通用)",
            "MP4：1080p 以下",
            "MP4：720p 以下",
            "MP3 音訊擷取"
        ])
        format_layout.addWidget(self.combo_format, 1)
        layout.addLayout(format_layout)

        # 瀏覽器 Cookie
        cookie_layout = QHBoxLayout()
        self.chk_cookie = QCheckBox("讀取瀏覽器登入狀態 (遇受限/會員內容再勾選)：")
        self.combo_browser = QComboBox()
        self.combo_browser.addItems(["Chrome", "Edge", "Firefox", "Brave", "Opera"])
        cookie_layout.addWidget(self.chk_cookie)
        cookie_layout.addWidget(self.combo_browser)
        cookie_layout.addStretch()
        layout.addLayout(cookie_layout)

        # 儲存目錄
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("儲存位置："))
        self.path_edit = QLineEdit(self.default_dir)
        btn_browse = QPushButton("變更")
        btn_browse.clicked.connect(self.browse_folder)
        btn_open = QPushButton("開啟資料夾")
        btn_open.clicked.connect(self.open_folder)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(btn_browse)
        path_layout.addWidget(btn_open)
        layout.addLayout(path_layout)

        # 控制按鈕
        btn_bar = QHBoxLayout()
        self.btn_start = QPushButton("開始下載")
        self.btn_start.setStyleSheet("background-color: #0d6efd; color: white; padding: 8px 16px; font-weight: bold;")
        self.btn_start.clicked.connect(self.start_download)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_download)

        self.btn_update = QPushButton("更新解析核心")
        self.btn_update.clicked.connect(self.update_core)

        btn_bar.addWidget(self.btn_start)
        btn_bar.addWidget(self.btn_cancel)
        btn_bar.addWidget(self.btn_update)
        layout.addLayout(btn_bar)

        # 進度與狀態提示
        self.lbl_status = QLabel("就緒")
        self.lbl_status.setStyleSheet("color: #444; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # 日誌輸出框
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #fafafa; border: 1px solid #ddd; font-size: 12px;")
        self.log_view.setFixedHeight(100)
        layout.addWidget(self.log_view)

        self.setCentralWidget(container)

    def paste_from_clipboard(self):
        self.url_edit.setText(QApplication.clipboard().text().strip())

    def toggle_playlist_fields(self):
        enabled = self.chk_playlist.isChecked()
        self.lbl_range.setEnabled(enabled)
        self.range_edit.setEnabled(enabled)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇儲存資料夾", self.path_edit.text())
        if folder:
            self.path_edit.setText(folder)

    def open_folder(self):
        target = self.path_edit.text()
        if os.path.exists(target):
            if sys.platform == "win32":
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])

    def start_download(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提醒", "請先輸入影片或清單網址！")
            return

        # 自動修正 Threads 網域名稱 (.com 改回官方 API 的 .net)
        if "threads.com" in url:
            url = url.replace("threads.com", "threads.net")
            self.url_edit.setText(url)
            self.log_view.append("【自動修正】已將 threads.com 修正為官方標準網址 threads.net")

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_status.setText("正在分析網址資料...")
        self.log_view.append(f"開始任務：{url}")

        self.thread = UniversalDownloadThread(
            url=url,
            save_path=self.path_edit.text(),
            quality_option=self.combo_format.currentText(),
            use_cookie=self.chk_cookie.isChecked(),
            browser_name=self.combo_browser.currentText(),
            is_playlist=self.chk_playlist.isChecked(),
            playlist_range=self.range_edit.text()
        )
        self.thread.progress_signal.connect(self.progress.setValue)
        self.thread.status_signal.connect(self.lbl_status.setText)
        self.thread.log_signal.connect(self.log_view.append)
        self.thread.finished_signal.connect(self.on_complete)
        self.thread.start()

    def cancel_download(self):
        if self.thread and self.thread.isRunning():
            self.thread.is_cancelled = True
            self.lbl_status.setText("正在終止連線...")

    def on_complete(self, success, message):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if success:
            self.lbl_status.setText("下載完成！")
            self.log_view.append(f"【成功】{message}")
        else:
            self.lbl_status.setText("下載失敗")
            self.log_view.append(f"【錯誤】{message}")

    def update_core(self):
        self.log_view.append("正在向 PyPI 檢查並升級 yt-dlp 引擎...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
            self.log_view.append("解析核心已更新至最新版！")
            QMessageBox.information(self, "更新完畢", "下載核心已更新到最新版本，相容各大社群平台的最新更新。")
        except Exception as e:
            self.log_view.append(f"更新失敗：{e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoDownloaderGUI()
    window.show()
    sys.exit(app.exec())
