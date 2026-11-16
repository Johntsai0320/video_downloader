import os
import sys
import re
import json
import tempfile
import urllib.request
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
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, url, save_path, quality_option, browser_name, cookie_file, is_playlist, playlist_range):
        super().__init__()
        self.url = url
        self.save_path = save_path
        self.quality_option = quality_option
        self.browser_name = browser_name.lower()
        self.cookie_file = cookie_file.strip()
        self.is_playlist = is_playlist
        self.playlist_range = playlist_range
        self.is_cancelled = False
        self.temp_cookie_file = None

    def fetch_meta_direct_video_url(self, post_id):
        """利用公開 OEmbed 與靜態解析取得原始 MP4 CDN 位址"""
        self.log_signal.emit("【直連嗅探】嘗試透過 Meta 公開嵌入通道取得影片直連...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
        }
        test_urls = [
            f"https://www.threads.net/t/{post_id}",
            f"https://www.instagram.com/p/{post_id}/embed/captioned/"
        ]
        for u in test_urls:
            try:
                req = urllib.request.Request(u, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content = resp.read().decode('utf-8', errors='ignore')
                matches = re.findall(r'https:[^"]+?cdninstagram\.com[^"]+?\.mp4[^"]*', content)
                if matches:
                    clean_url = matches[0].replace('\\u0026', '&').replace('&amp;', '&').replace('\\/', '/')
                    return clean_url
            except Exception:
                continue
        return None

    def create_sanitized_meta_cookies(self, original_cookie_path):
        if not original_cookie_path or not os.path.exists(original_cookie_path):
            return None
        try:
            with open(original_cookie_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            clean_lines = []
            allowed_domains = ["instagram.com", "threads.net", "threads.com"]
            for line in lines:
                if line.startswith('#') and not line.startswith('#HttpOnly_'):
                    clean_lines.append(line)
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    domain = parts[0].lower()
                    if any(ad in domain for ad in allowed_domains):
                        clean_lines.append(line)

            if clean_lines:
                temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_meta_cookies.txt', encoding='utf-8')
                temp_file.writelines(clean_lines)
                temp_file.close()
                self.temp_cookie_file = temp_file.name
                return self.temp_cookie_file
        except Exception:
            pass
        return original_cookie_path

    def run(self):
        try:
            target_url = self.url.strip()

            # 1. 規範化 X / Twitter 轉發網址
            for proxy_domain in ["vxtwitter.com", "fxtwitter.com", "fixupx.com", "twittpr.com"]:
                if proxy_domain in target_url:
                    target_url = target_url.replace(proxy_domain, "x.com")
                    self.log_signal.emit(f"【自動規範】已將轉發網址轉換為官方網址：{target_url}")
                    break

            # 2. 規範化 Threads 網址
            if "threads.com" in target_url:
                target_url = target_url.replace("threads.com", "threads.net")
            if "threads.net" in target_url and "?" in target_url:
                target_url = target_url.split("?")[0]

            # 3. 命名範本
            if self.is_playlist:
                out_template = os.path.join(
                    self.save_path, 
                    '%(playlist_title|播放清單)s', 
                    '%(playlist_index)02d - %(title).80s [%(id)s].%(ext)s'
                )
            else:
                out_template = os.path.join(self.save_path, '%(title).80s [%(id)s].%(ext)s')

            has_cookie_source = bool(
                (self.cookie_file and os.path.exists(self.cookie_file)) or self.browser_name
            )

            prefer_cookie_domains = ["instagram.com", "threads.net", "facebook.com", "fb.watch"]
            is_meta_site = any(domain in target_url for domain in prefer_cookie_domains)
            initial_use_cookie = has_cookie_source and is_meta_site

            active_cookie_file = self.cookie_file
            if is_meta_site and self.cookie_file and os.path.exists(self.cookie_file):
                sanitized = self.create_sanitized_meta_cookies(self.cookie_file)
                if sanitized:
                    active_cookie_file = sanitized
                    self.log_signal.emit("【自動優化】已純化 Meta 專屬 Session 憑證...")

            def build_opts(url_target, use_cookie_flag):
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Sec-Fetch-Mode': 'navigate',
                }

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

                chosen_cookie = active_cookie_file
                if use_cookie_flag:
                    if chosen_cookie and os.path.exists(chosen_cookie):
                        opts['cookiefile'] = chosen_cookie
                    elif self.browser_name:
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

            if initial_use_cookie:
                self.log_signal.emit("【智慧決策】社群受限網域，自動啟用 Cookie 認證...")
            else:
                self.log_signal.emit("【智慧決策】啟用純淨訪客模式連線...")

            self.status_signal.emit("正在解析串流內容並下載...")

            # 第一次下載嘗試
            try:
                with yt_dlp.YoutubeDL(build_opts(target_url, initial_use_cookie)) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    title = info.get('title', '下載任務')
                    self.finished_signal.emit(True, f"已成功儲存：{title}")
                    return
            except Exception as primary_error:
                error_str = str(primary_error)

                # 關鍵救援：從網址或錯誤中抓取 Post ID
                match_id = re.search(r'/post/([A-Za-z0-9_-]+)', error_str) or re.search(r'/post/([A-Za-z0-9_-]+)', target_url)
                if match_id:
                    real_post_id = match_id.group(1)
                    fallback_ig = f"https://www.instagram.com/p/{real_post_id}/"
                    self.log_signal.emit(f"【最後嘗試】以 Instagram Post 格式再次重試：{fallback_ig}")

                    # 嘗試 A：帶 Cookie 下載
                    try:
                        with yt_dlp.YoutubeDL(build_opts(fallback_ig, has_cookie_source)) as ydl:
                            info = ydl.extract_info(fallback_ig, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存：{title}")
                            return
                    except Exception as cookie_err:
                        self.log_signal.emit(f"帶 Cookie 失敗 ({cookie_err})，改用純淨訪客重試...")

                    # 嘗試 B：訪客模式下載
                    try:
                        with yt_dlp.YoutubeDL(build_opts(fallback_ig, False)) as ydl:
                            info = ydl.extract_info(fallback_ig, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存（訪客模式）：{title}")
                            return
                    except Exception as guest_err:
                        self.log_signal.emit(f"訪客模式受阻 ({guest_err})，啟動 CDN 直連嗅探...")

                    # 嘗試 C：CDN 直連嗅探（繞過所有 API 400 限制）
                    direct_video_url = self.fetch_meta_direct_video_url(real_post_id)
                    if direct_video_url:
                        self.log_signal.emit("【嗅探成功】直接以 CDN 影音串流儲存...")
                        with yt_dlp.YoutubeDL(build_opts(direct_video_url, False)) as ydl:
                            info = ydl.extract_info(direct_video_url, download=True)
                            self.finished_signal.emit(True, f"已成功儲存（直連通道）：Threads_{real_post_id}")
                            return
                    else:
                        raise Exception("Meta 伺服器拒絕存取：請重新由瀏覽器匯出包含有效登入狀態的 cookies.txt。")

                # 若帶 Cookie 遇到 403/400 且為其他網站，切換至純淨模式重試
                if initial_use_cookie and any(code in error_str for code in ["403", "400", "DPAPI"]):
                    self.log_signal.emit("【自動修復】偵測到 Cookie 衝突，自動切換至免登入模式重試...")
                    with yt_dlp.YoutubeDL(build_opts(target_url, False)) as ydl:
                        info = ydl.extract_info(target_url, download=True)
                        title = info.get('title', '下載任務')
                        self.finished_signal.emit(True, f"已成功儲存（免登入模式）：{title}")
                        return

                raise primary_error

        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if self.temp_cookie_file and os.path.exists(self.temp_cookie_file):
                try:
                    os.remove(self.temp_cookie_file)
                except Exception:
                    pass

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
                percent = int((downloaded / total) * 100)
                self.progress_signal.emit(percent)
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                self.status_signal.emit(f"{prefix}下載中: {percent}% | 速度: {speed} | 剩餘時間: {eta}")
        elif d['status'] == 'finished':
            self.progress_signal.emit(100)
            self.status_signal.emit("正在處理影音合併...")


class VideoDownloaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("全網影音 / 播放清單通用智慧下載器")
        self.resize(720, 600)
        self.thread = None

        self.default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "VideoDownloads")
        os.makedirs(self.default_dir, exist_ok=True)

        self.setup_ui()

    def setup_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        title = QLabel("全網通用智慧下載器")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        desc = QLabel("具備各網站 Cookie 自適應決策，支援 YouTube、Threads、FB、IG、X、TikTok、B站")
        desc.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(desc)

        # 網址輸入列
        url_layout = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("請貼上影片網址...")
        btn_paste = QPushButton("貼上")
        btn_paste.clicked.connect(lambda: self.url_edit.setText(QApplication.clipboard().text().strip()))
        url_layout.addWidget(self.url_edit)
        url_layout.addWidget(btn_paste)
        layout.addLayout(url_layout)

        # 播放清單控制項
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

        # 智慧憑證庫配置區
        cert_group = QVBoxLayout()
        cert_title = QLabel("智慧憑證庫 (程式會自動過濾並判斷網站是否取用，無需手動切換)：")
        cert_title.setStyleSheet("font-weight: bold; color: #2563eb;")
        cert_group.addWidget(cert_title)

        file_bar = QHBoxLayout()
        file_bar.addWidget(QLabel("全域 cookies.txt："))
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("可留空。若有提供，遇到需登入的網站會自動調用並純化...")
        self.btn_browse_cookie = QPushButton("選取檔案")
        self.btn_browse_cookie.clicked.connect(self.browse_cookie_file)
        file_bar.addWidget(self.cookie_file_edit, 1)
        file_bar.addWidget(self.btn_browse_cookie)
        cert_group.addLayout(file_bar)

        browser_bar = QHBoxLayout()
        browser_bar.addWidget(QLabel("備用讀取瀏覽器："))
        self.combo_browser = QComboBox()
        self.combo_browser.addItems(["Firefox", "Chrome", "Edge", "Brave", "Opera"])
        browser_bar.addWidget(self.combo_browser)
        browser_bar.addWidget(QLabel("(當未指定 cookies.txt 時，由該瀏覽器自動讀取 Session)"))
        browser_bar.addStretch()
        cert_group.addLayout(browser_bar)

        layout.addLayout(cert_group)

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

        # 狀態進度顯示
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

    def browse_cookie_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "選取 cookies.txt", "", "Text Files (*.txt);;All Files (*)")
        if file_path:
            self.cookie_file_edit.setText(file_path)

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
            browser_name=self.combo_browser.currentText(),
            cookie_file=self.cookie_file_edit.text(),
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
        self.log_view.append("正在向 PyPI 檢查並升級 yt-dlp 引擎...")
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
