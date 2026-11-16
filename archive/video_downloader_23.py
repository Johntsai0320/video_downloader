import os
import sys
import re
import json
import shutil
import tempfile
import urllib.request
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QFileDialog,
    QProgressBar, QTextEdit, QMessageBox, QCheckBox, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# 1. 優先偵測系統全域 PATH 的 ffmpeg，若無才嘗試 imageio-ffmpeg
FFMPEG_PATH = shutil.which("ffmpeg")
if not FFMPEG_PATH:
    try:
        import imageio_ffmpeg
        FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        FFMPEG_PATH = None

import yt_dlp
from yt_dlp.cookies import extract_cookies_from_browser

# ============================
# 全局現代化樣式表 (QSS)
# ============================
MODERN_STYLE = """
QMainWindow, QWidget#MainContainer {
    background-color: #f1f5f9;
    font-family: "Segoe UI", "Microsoft JhengHei", "PingFang TC", sans-serif;
    color: #1e293b;
}
QFrame.CardFrame {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
}
QLabel {
    font-size: 13px;
    color: #475569;
}
QLabel#HeaderTitle {
    font-size: 22px;
    font-weight: 700;
    color: #0f172a;
}
QLabel#HeaderSubtitle {
    font-size: 12px;
    color: #64748b;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #2563eb;
}
QLineEdit {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    color: #0f172a;
}
QLineEdit:focus {
    background-color: #ffffff;
    border: 1.5px solid #3b82f6;
}
QLineEdit:disabled {
    background-color: #e2e8f0;
    color: #94a3b8;
}
QComboBox {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    color: #0f172a;
}
QComboBox:hover {
    border-color: #94a3b8;
}
QCheckBox {
    font-size: 13px;
    color: #334155;
    font-weight: 500;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #cbd5e1;
    background-color: #f8fafc;
}
QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
}
QPushButton {
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton.SecondaryBtn {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    color: #334155;
}
QPushButton.SecondaryBtn:hover {
    background-color: #f8fafc;
    border-color: #94a3b8;
}
QPushButton.SecondaryBtn:pressed {
    background-color: #e2e8f0;
}
QPushButton.AccentBtn {
    background-color: #0284c7;
    color: #ffffff;
    border: none;
}
QPushButton.AccentBtn:hover {
    background-color: #0369a1;
}
QPushButton#PrimaryActionBtn {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    padding: 10px 24px;
    font-size: 14px;
}
QPushButton#PrimaryActionBtn:hover {
    background-color: #1d4ed8;
}
QPushButton#PrimaryActionBtn:disabled {
    background-color: #94a3b8;
}
QPushButton#CancelActionBtn {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    padding: 10px 20px;
}
QPushButton#CancelActionBtn:hover {
    background-color: #dc2626;
}
QPushButton#CancelActionBtn:disabled {
    background-color: #fca5a5;
}
QProgressBar {
    background-color: #e2e8f0;
    border-radius: 6px;
    text-align: center;
    height: 12px;
}
QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #06b6d4);
    border-radius: 6px;
}
QTextEdit#TerminalLog {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 10px;
    color: #cbd5e1;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    padding: 8px;
}
"""

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
        self.log_signal.emit("【直連嗅探】嘗試透過 Meta 公開嵌入通道取得影片直連...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
        }
        test_urls = [
            f"https://www.instagram.com/p/{post_id}/embed/captioned/",
            f"https://www.threads.net/t/{post_id}"
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

            for proxy_domain in ["vxtwitter.com", "fxtwitter.com", "fixupx.com", "twittpr.com"]:
                if proxy_domain in target_url:
                    target_url = target_url.replace(proxy_domain, "x.com")
                    self.log_signal.emit(f"【自動規範】已將轉發網址轉換為官方網址：{target_url}")
                    break

            if "threads.com" in target_url:
                target_url = target_url.replace("threads.com", "threads.net")
            if "threads.net" in target_url and "?" in target_url:
                target_url = target_url.split("?")[0]

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

            def build_opts(url_target, use_cookie_flag, force_mobile=False):
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,en-US;q=0.7,en;q=0.6',
                }

                if force_mobile:
                    headers['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'

                if "bilibili.com" in url_target:
                    headers['Referer'] = 'https://www.bilibili.com/'
                    headers['Origin'] = 'https://www.bilibili.com'

                opts = {
                    'outtmpl': out_template,
                    'progress_hooks': [self.hook],
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'noplaylist': not self.is_playlist,
                    'socket_timeout': 30,
                    'http_headers': headers,
                }

                if "instagram.com" in url_target:
                    opts['extractor_args'] = {
                        'instagram': {'api': ['mobile']}
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
                    opts['format'] = 'bv*+ba/b'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP4：1080p 以下":
                    opts['format'] = 'bv*[height<=1080]+ba/b[height<=1080]/b'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP4：720p 以下":
                    opts['format'] = 'bv*[height<=720]+ba/b[height<=720]/b'
                    opts['merge_output_format'] = 'mp4'
                elif self.quality_option == "MP3 音訊擷取":
                    opts['format'] = 'ba/b'
                    opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                return opts

            if FFMPEG_PATH:
                self.log_signal.emit(f"【FFmpeg 偵測】已啟用多媒體合併引擎：{FFMPEG_PATH}")

            if initial_use_cookie:
                self.log_signal.emit("【智慧決策】社群受限網域，自動啟用 Cookie 認證...")
            else:
                self.log_signal.emit("【智慧決策】啟用純淨訪客模式連線...")

            self.status_signal.emit("正在解析串流內容並下載...")

            try:
                with yt_dlp.YoutubeDL(build_opts(target_url, initial_use_cookie)) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    title = info.get('title', '下載任務')
                    self.finished_signal.emit(True, f"已成功儲存：{title}")
                    return
            except Exception as primary_error:
                error_str = str(primary_error)

                if "bilibili.com" in target_url and "412" in error_str:
                    self.log_signal.emit("【B站防護突破】偵測到 HTTP 412 限制，嘗試掛載憑證重試...")
                    try:
                        with yt_dlp.YoutubeDL(build_opts(target_url, has_cookie_source)) as ydl:
                            info = ydl.extract_info(target_url, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存：{title}")
                            return
                    except Exception as bili_err:
                        self.log_signal.emit(f"【重試訊息】{bili_err}")

                match_id = re.search(r'/post/([A-Za-z0-9_-]+)', error_str) or re.search(r'/post/([A-Za-z0-9_-]+)', target_url)
                if match_id and ("instagram" in target_url or "threads" in target_url or "threads" in error_str):
                    real_post_id = match_id.group(1)
                    fallback_ig = f"https://www.instagram.com/p/{real_post_id}/"
                    self.log_signal.emit(f"【最後嘗試】以 Instagram 行動端 API 重試：{fallback_ig}")

                    try:
                        with yt_dlp.YoutubeDL(build_opts(fallback_ig, has_cookie_source, force_mobile=True)) as ydl:
                            info = ydl.extract_info(fallback_ig, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存：{title}")
                            return
                    except Exception as cookie_err:
                        self.log_signal.emit(f"行動端帶 Cookie 受阻 ({cookie_err})，嘗試純淨訪客重試...")

                    try:
                        with yt_dlp.YoutubeDL(build_opts(fallback_ig, False, force_mobile=True)) as ydl:
                            info = ydl.extract_info(fallback_ig, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存（訪客模式）：{title}")
                            return
                    except Exception as guest_err:
                        self.log_signal.emit(f"訪客模式受阻 ({guest_err})，啟動 CDN 直連嗅探...")

                    direct_video_url = self.fetch_meta_direct_video_url(real_post_id)
                    if direct_video_url:
                        self.log_signal.emit("【嗅探成功】直接以 CDN 影音串流儲存...")
                        with yt_dlp.YoutubeDL(build_opts(direct_video_url, False)) as ydl:
                            info = ydl.extract_info(direct_video_url, download=True)
                            self.finished_signal.emit(True, f"已成功儲存（直連通道）：Threads_{real_post_id}")
                            return
                    else:
                        raise Exception("Meta 伺服器拒絕存取：此貼文受限且提供的 cookies.txt 已失效。請使用下方【一鍵抓取瀏覽器 Cookie】按鈕更新憑證。")

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
            prefix = f"[{p_index}/{p_count}] " if (p_index and p_count) else ""

            if total > 0:
                percent = int((downloaded / total) * 100)
                self.progress_signal.emit(percent)
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                self.status_signal.emit(f"{prefix}下載進度: {percent}% | 速度: {speed} | 剩餘時間: {eta}")
        elif d['status'] == 'finished':
            self.progress_signal.emit(100)
            self.status_signal.emit("音訊與視訊下載完成，FFmpeg 封裝處理中...")


class VideoDownloaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("全網影音下載器 · Universal Media Hub")
        self.resize(760, 740)
        self.thread = None

        self.default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "VideoDownloads")
        os.makedirs(self.default_dir, exist_ok=True)

        self.setup_ui()

    def setup_ui(self):
        container = QWidget()
        container.setObjectName("MainContainer")
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(14)

        # 頂部標頭區域
        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        title = QLabel("Universal Video Hub")
        title.setObjectName("HeaderTitle")
        desc = QLabel("支援 YouTube、Bilibili (自動音訊合成)、Instagram、Threads、X (Twitter)、TikTok")
        desc.setObjectName("HeaderSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        root_layout.addLayout(header_layout)

        # 卡片 1: 網址輸入與播放清單配置
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
        self.url_edit.setPlaceholderText("請貼上影片或播放清單網址 (支援短網址/代理網址)...")
        btn_paste = QPushButton("貼上剪貼簿")
        btn_paste.setProperty("class", "SecondaryBtn")
        btn_paste.clicked.connect(lambda: self.url_edit.setText(QApplication.clipboard().text().strip()))
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

        # 卡片 2: 格式與憑證設置
        card2 = QFrame()
        card2.setProperty("class", "CardFrame")
        c2_layout = QVBoxLayout(card2)
        c2_layout.setContentsMargins(16, 14, 16, 14)
        c2_layout.setSpacing(10)

        c2_title = QLabel("下載規格與憑證決策")
        c2_title.setObjectName("SectionTitle")
        c2_layout.addWidget(c2_title)

        fmt_row = QHBoxLayout()
        fmt_label = QLabel("輸出規格：")
        fmt_label.setFixedWidth(85)
        self.combo_format = QComboBox()
        self.combo_format.addItems([
            "MP4：最佳畫質 (通用)",
            "MP4：1080p 以下",
            "MP4：720p 以下",
            "MP3 音訊擷取"
        ])
        fmt_row.addWidget(fmt_label)
        fmt_row.addWidget(self.combo_format, 1)
        c2_layout.addLayout(fmt_row)

        browser_row = QHBoxLayout()
        b_label = QLabel("目標瀏覽器：")
        b_label.setFixedWidth(85)
        self.combo_browser = QComboBox()
        self.combo_browser.addItems(["Firefox", "Chrome", "Edge", "Brave", "Opera"])
        
        # 新增：一鍵抓取按鈕
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
        self.cookie_file_edit.setPlaceholderText("可手動選擇或點選上方「一鍵抓取」自動代入...")
        self.btn_browse_cookie = QPushButton("選取檔案")
        self.btn_browse_cookie.setProperty("class", "SecondaryBtn")
        self.btn_browse_cookie.clicked.connect(self.browse_cookie_file)
        cookie_row.addWidget(cookie_label)
        cookie_row.addWidget(self.cookie_file_edit, 1)
        cookie_row.addWidget(self.btn_browse_cookie)
        c2_layout.addLayout(cookie_row)

        root_layout.addWidget(card2)

        # 卡片 3: 儲存目錄設定
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

        # 操作主控按鈕群
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

        # 進度指示與日誌
        status_box = QVBoxLayout()
        status_box.setSpacing(6)

        status_info_layout = QHBoxLayout()
        self.lbl_status = QLabel("狀態：就緒")
        self.lbl_status.setStyleSheet("font-weight: 600; color: #334155;")
        status_info_layout.addWidget(self.lbl_status)
        status_box.addLayout(status_info_layout)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status_box.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("TerminalLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(120)
        status_box.addWidget(self.log_view)

        root_layout.addLayout(status_box)
        self.setCentralWidget(container)

    def extract_browser_cookies(self):
        """核心新增：直接讀取指定瀏覽器 Cookie 並儲存為 cookies.txt"""
        browser = self.combo_browser.currentText().lower()
        self.log_view.append(f">> 正在嘗試讀取本機 {browser.capitalize()} 的登入憑證...")
        output_path = os.path.join(self.default_dir, f"{browser}_cookies.txt")

        try:
            # 呼叫 yt-dlp 內部解密器
            cj = extract_cookies_from_browser(browser)
            if not cj:
                raise Exception(f"無法從 {browser.capitalize()} 讀取 Cookie。請確認該瀏覽器已登入帳號。")

            # 轉存為標準 Netscape cookies.txt 格式
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# Generated automatically by Universal Video Hub\n\n")
                for c in cj:
                    domain = c.domain
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.path
                    secure = "TRUE" if c.secure else "FALSE"
                    expires = str(c.expires) if c.expires else "0"
                    name = c.name
                    value = c.value
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

            self.cookie_file_edit.setText(output_path)
            self.log_view.append(f"[SUCCESS] 成功匯出 Cookie 至：{output_path}")
            QMessageBox.information(
                self, 
                "抓取成功", 
                f"已成功從 {browser.capitalize()} 抓取憑證並自動代入！\n路徑：{output_path}"
            )
        except Exception as e:
            err_msg = str(e)
            self.log_view.append(f"[ERROR] 抓取失敗：{err_msg}")
            if "database is locked" in err_msg.lower() or "permission denied" in err_msg.lower():
                QMessageBox.warning(
                    self, 
                    "讀取受限", 
                    f"讀取 {browser.capitalize()} 失敗！\n原因：瀏覽器正在執行並鎖定 Cookie 資料庫。\n\n請【完全關閉該瀏覽器】後再試一次，或改用 Firefox（支援免關閉讀取）。"
                )
            else:
                QMessageBox.warning(self, "抓取失敗", f"未能成功提取憑證：\n{err_msg}")

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
            QMessageBox.warning(self, "提示", "請先輸入有效的影片網址！")
            return

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_status.setText("狀態：連線並分析串流資料中...")
        self.log_view.append(f">> 開始任務：{url}")

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
            self.lbl_status.setText("狀態：正在安全中止任務...")

    def on_complete(self, success, message):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if success:
            self.lbl_status.setText("狀態：任務順利完成！")
            self.log_view.append(f"[SUCCESS] {message}\n")
        else:
            self.lbl_status.setText("狀態：下載發生異常")
            self.log_view.append(f"[ERROR] {message}\n")

    def update_core(self):
        self.log_view.append(">> 正在向 PyPI 檢查並升級核心引擎 (yt-dlp)...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
            self.log_view.append("[SUCCESS] 解析核心已更新至最新版本！")
            QMessageBox.information(self, "更新完成", "yt-dlp 解析模組已更新完成。")
        except Exception as e:
            self.log_view.append(f"[ERROR] 更新失敗：{e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(MODERN_STYLE)
    window = VideoDownloaderGUI()
    window.show()
    sys.exit(app.exec())
