import os
import sys
import re
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QFileDialog,
    QProgressBar, QTextEdit, QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

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
            target_url = self.url.strip()

            # 命名規則
            if self.is_playlist:
                out_template = os.path.join(
                    self.save_path, 
                    '%(playlist_title|播放清單)s', 
                    '%(playlist_index)02d - %(title).80s [%(id)s].%(ext)s'
                )
            else:
                out_template = os.path.join(self.save_path, '%(title).80s [%(id)s].%(ext)s')

            def build_opts(url_target, enable_cookie):
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Sec-Fetch-Mode': 'navigate',
                }

                # 針對 Threads 注入 Meta 專用標頭，防止跳轉至 error=invalid_post
                if "threads" in url_target:
                    headers.update({
                        'Sec-Fetch-Site': 'same-origin',
                        'X-IG-App-ID': '238260118697367',  # Threads 官方 Web App ID
                    })

                opts = {
                    'outtmpl': out_template,
                    'progress_hooks': [self.hook],
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'noplaylist': not self.is_playlist,
                    'socket_timeout': 30,
                    'http_headers': headers
                }

                if self.is_playlist and self.playlist_range.strip():
                    opts['playlist_items'] = self.playlist_range.strip()

                if FFMPEG_PATH:
                    opts['ffmpeg_location'] = FFMPEG_PATH

                if enable_cookie:
                    opts['cookiesfrombrowser'] = (self.browser_name,)

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

            self.status_signal.emit("正在解析串流內容...")

            # 第一次嘗試：以原網址下載
            try:
                with yt_dlp.YoutubeDL(build_opts(target_url, self.use_cookie)) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    title = info.get('title', '下載任務')
                    self.finished_signal.emit(True, f"已完成：{title}")
                    return
            except Exception as primary_error:
                error_str = str(primary_error)
                
                # 若因 Threads 遭 Meta 導向至 error 頁面，自動抽取 Post ID 改走 Instagram 引擎
                if "threads" in target_url and ("invalid_post" in error_str or "Unsupported URL" in error_str):
                    match = re.search(r'post/([A-Za-z0-9_-]+)', target_url)
                    if match:
                        post_id = match.group(1)
                        ig_fallback_url = f"https://www.instagram.com/p/{post_id}/"
                        self.log_signal.emit(f"【備援觸發】Threads 伺服器拒絕直接存取，自動切換至 Instagram CDN 解析：{ig_fallback_url}")
                        
                        with yt_dlp.YoutubeDL(build_opts(ig_fallback_url, self.use_cookie)) as ydl:
                            info = ydl.extract_info(ig_fallback_url, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已完成（經由備援通道）：{title}")
                            return

                # 若是 Cookie DPAPI 加密問題，自動改用訪客模式
                if self.use_cookie and "DPAPI" in error_str:
                    self.log_signal.emit("【提示】Cookie 解密受阻，自動切換至免登入模式重試...")
                    with yt_dlp.YoutubeDL(build_opts(target_url, False)) as ydl:
                        info = ydl.extract_info(target_url, download=True)
                        title = info.get('title', '下載任務')
                        self.finished_signal.emit(True, f"已完成：{title}")
                        return

                raise primary_error

        except Exception as e:
            self.finished_signal.emit(False, str(e))

    def hook(self, d):
        if self.is_cancelled:
            raise Exception("任務已取消。")

        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            info_dict = d.get('info_dict', {})
            p_index = info_dict.get('playlist_index')
            p_count = info_dict.get('n_entries')
            prefix = f"[第 {p_index}/{p_count} 部] " if (p_index and p_count) else ""

            if total > 0:
                percent = (downloaded / total) * 100
                self.progress_signal.emit(percent)
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                self.status_signal.emit(f"{prefix}下載中: {percent:.1f}% | 速度: {speed} | 剩餘: {eta}")
        elif d['status'] == 'finished':
            self.progress_signal.emit(100.0)
            self.status_signal.emit("正在處理影音合併...")


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

        title = QLabel("全網通用影音下載器")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        desc = QLabel("支援 YouTube、Threads、FB、IG、X、TikTok、Bilibili 及播放清單下載")
        desc.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(desc)

        # 網址輸入
        url_layout = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("請貼上影片網址...")
        btn_paste = QPushButton("貼上")
        btn_paste.clicked.connect(lambda: self.url_edit.setText(QApplication.clipboard().text().strip()))
        url_layout.addWidget(self.url_edit)
        url_layout.addWidget(btn_paste)
        layout.addLayout(url_layout)

        # 播放清單設定
        playlist_layout = QHBoxLayout()
        self.chk_playlist = QCheckBox("下載整份播放清單 (Playlist)")
        self.chk_playlist.stateChanged.connect(self.toggle_playlist_fields)
        self.lbl_range = QLabel("集數範圍 (例: 1-5):")
        self.lbl_range.setEnabled(False)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("留空則全部")
        self.range_edit.setEnabled(False)
        self.range_edit.setFixedWidth(120)
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
        self.chk_cookie = QCheckBox("讀取瀏覽器登入狀態 (受限/私人影片再開)：")
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

        # 操作按鈕
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

        # 狀態與日誌
        self.lbl_status = QLabel("就緒")
        self.lbl_status.setStyleSheet("color: #444; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("background-color: #fafafa; border: 1px solid #ddd; font-size: 12px;")
        self.log_view.setFixedHeight(100)
        layout.addWidget(self.log_view)

        self.setCentralWidget(container)

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
            QMessageBox.warning(self, "提醒", "請先輸入影片網址！")
            return

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
            self.lbl_status.setText("正在取消任務...")

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
        self.log_view.append("正在升級 yt-dlp 引擎...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
            self.log_view.append("解析核心已升級到最新版！")
            QMessageBox.information(self, "更新完畢", "下載核心已更新完成。")
        except Exception as e:
            self.log_view.append(f"更新失敗：{e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoDownloaderGUI()
    window.show()
    sys.exit(app.exec())
