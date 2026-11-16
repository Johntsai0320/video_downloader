import os
import sys
import re
import json
import shutil
import tempfile
import urllib.request
import urllib.parse
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

def media_id_to_shortcode(media_id: int) -> str:
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    shortcode = []
    while media_id > 0:
        media_id, rem = divmod(media_id, 64)
        shortcode.append(alphabet[rem])
    return ''.join(reversed(shortcode))

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

    def resolve_meta_redirect(self, raw_url):
        try:
            req = urllib.request.Request(
                raw_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                content = resp.read(65536).decode('utf-8', errors='ignore')

            match_post = re.search(r'/(?:post|t)/([A-Za-z0-9_-]+)', final_url)
            if match_post:
                return f"https://www.threads.net/t/{match_post.group(1)}", match_post.group(1)

            match_media_id = re.search(r'injected_media_ids=%5B%22(\d+)%22%5D', final_url) or re.search(r'"media_id":"(\d+)"', content)
            if match_media_id:
                mid = int(match_media_id.group(1))
                code = media_id_to_shortcode(mid)
                return f"https://www.instagram.com/p/{code}/", code

            match_og = re.search(r'property="og:url"\s+content="([^"]+)"', content)
            if match_og:
                og_url = match_og.group(1)
                og_post = re.search(r'/(?:post|t|p)/([A-Za-z0-9_-]+)', og_url)
                if og_post:
                    return f"https://www.threads.net/t/{og_post.group(1)}", og_post.group(1)

        except Exception as e:
            self.log_signal.emit(f"【縮網址解析警告】{e}")
        return raw_url, None

    def fetch_meta_direct_video_url(self, post_id):
        """改進的嗅探器：精準提取真實 Progressive MP4，排除 DASH 片段"""
        self.log_signal.emit("【直連嗅探】嘗試透過公開嵌入通道取得真實 MP4 直連...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8',
            'Referer': 'https://www.instagram.com/'
        }

        # 方式 1：Instagram 公開 Embed 頁面 JSON 解析
        try:
            embed_url = f"https://www.instagram.com/p/{post_id}/embed/captioned/"
            req = urllib.request.Request(embed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            # 抓取 video_url 鍵值
            video_matches = re.findall(r'"video_url":\s*"([^"]+)"', content)
            for raw_u in video_matches:
                clean_url = raw_u.replace('\\u0026', '&').replace('&amp;', '&').replace('\\/', '/')
                if 'cdninstagram.com' in clean_url and not any(bad in clean_url for bad in ['BaseURL', 'Segment', 'mpd']):
                    return clean_url

            # 備用正則：直接匹配完整 https 連結
            url_matches = re.findall(r'https:[^"]+?cdninstagram\.com[^"]+?\.mp4[^"]*', content)
            for raw_u in url_matches:
                clean_url = raw_u.replace('\\u0026', '&').replace('&amp;', '&').replace('\\/', '/')
                if not any(bad in clean_url for bad in ['BaseURL', 'Segment', 'mpd', '\\u003C', '<']):
                    return clean_url
        except Exception:
            pass

        # 方式 2：Threads GraphQL 公開頁面嗅探
        try:
            th_url = f"https://www.threads.net/t/{post_id}"
            req = urllib.request.Request(th_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            url_matches = re.findall(r'https:[^"]+?cdninstagram\.com[^"]+?\.mp4[^"]*', content)
            for raw_u in url_matches:
                clean_url = raw_u.replace('\\u0026', '&').replace('&amp;', '&').replace('\\/', '/')
                if not any(bad in clean_url for bad in ['BaseURL', 'Segment', 'mpd', '\\u003C', '<']):
                    return clean_url
        except Exception:
            pass

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

            # 規範化 X / Twitter
            for proxy_domain in ["vxtwitter.com", "fxtwitter.com", "fixupx.com", "twittpr.com"]:
                if proxy_domain in target_url:
                    target_url = target_url.replace(proxy_domain, "x.com")
                    self.log_signal.emit(f"【自動規範】已將轉發網址轉換為官方網址：{target_url}")
                    break

            # 處理 Threads / IG 縮網址
            extracted_code = None
            if "threads.com/share/" in target_url or "threads.net/share/" in target_url or "injected_media_ids" in target_url:
                self.log_signal.emit("【縮網址解析】偵測到 Threads 分享短連結，正在追蹤原始貼文...")
                target_url, extracted_code = self.resolve_meta_redirect(target_url)
                self.log_signal.emit(f"【目標定位】已鎖定核心媒體網址：{target_url}")

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

            has_valid_cookie_file = bool(self.cookie_file and os.path.exists(self.cookie_file))
            prefer_cookie_domains = ["instagram.com", "threads.net", "facebook.com", "fb.watch"]
            is_meta_site = any(domain in target_url for domain in prefer_cookie_domains)

            active_cookie_file = self.cookie_file
            if is_meta_site and has_valid_cookie_file:
                sanitized = self.create_sanitized_meta_cookies(self.cookie_file)
                if sanitized:
                    active_cookie_file = sanitized
                    self.log_signal.emit("【自動優化】已純化 Meta 專屬 Session 憑證...")

            def build_opts(url_target, use_cookie_file_flag=False, force_mobile=False, is_direct_cdn=False):
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,en-US;q=0.7,en;q=0.6',
                }

                if force_mobile:
                    headers['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'

                if "bilibili.com" in url_target:
                    headers['Referer'] = 'https://www.bilibili.com/'
                    headers['Origin'] = 'https://www.bilibili.com'

                # 關鍵修復：下載 Meta CDN 時加上合法 Referer 避免 403 Forbidden
                if is_direct_cdn or "cdninstagram.com" in url_target:
                    headers['Referer'] = 'https://www.instagram.com/'
                    headers['Origin'] = 'https://www.instagram.com'

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

                if "instagram.com" in url_target and not is_direct_cdn:
                    opts['extractor_args'] = {'instagram': {'api': ['mobile']}}

                if self.is_playlist and self.playlist_range.strip():
                    opts['playlist_items'] = self.playlist_range.strip()

                if FFMPEG_PATH:
                    opts['ffmpeg_location'] = FFMPEG_PATH

                if use_cookie_file_flag and active_cookie_file and os.path.exists(active_cookie_file):
                    opts['cookiefile'] = active_cookie_file

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

            if has_valid_cookie_file:
                self.log_signal.emit("【智慧決策】啟用 Cookie 檔案認證模式...")
            else:
                self.log_signal.emit("【智慧決策】未指定有效 Cookie 檔案，啟用純淨訪客模式...")

            self.status_signal.emit("正在解析串流內容並下載...")

            # 第一次常規嘗試
            try:
                with yt_dlp.YoutubeDL(build_opts(target_url, use_cookie_file_flag=has_valid_cookie_file)) as ydl:
                    info = ydl.extract_info(target_url, download=True)
                    title = info.get('title', '下載任務')
                    self.finished_signal.emit(True, f"已成功儲存：{title}")
                    return
            except Exception as primary_error:
                error_str = str(primary_error)

                # B站 412 處理
                if "bilibili.com" in target_url and "412" in error_str:
                    self.log_signal.emit("【B站防護突破】偵測到 HTTP 412 限制，嘗試掛載憑證重試...")
                    try:
                        with yt_dlp.YoutubeDL(build_opts(target_url, use_cookie_file_flag=True)) as ydl:
                            info = ydl.extract_info(target_url, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存：{title}")
                            return
                    except Exception as bili_err:
                        self.log_signal.emit(f"【重試訊息】{bili_err}")

                # Meta 救援邏輯
                real_post_id = extracted_code
                if not real_post_id:
                    match_id = re.search(r'/(?:post|t|p)/([A-Za-z0-9_-]+)', error_str) or re.search(r'/(?:post|t|p)/([A-Za-z0-9_-]+)', target_url)
                    if match_id:
                        real_post_id = match_id.group(1)

                if real_post_id:
                    fallback_ig = f"https://www.instagram.com/p/{real_post_id}/"
                    self.log_signal.emit(f"【最後嘗試】以 Instagram 行動端 API 重試：{fallback_ig}")

                    # 嘗試 A：訪客模式
                    try:
                        with yt_dlp.YoutubeDL(build_opts(fallback_ig, use_cookie_file_flag=False, force_mobile=True)) as ydl:
                            info = ydl.extract_info(fallback_ig, download=True)
                            title = info.get('title', '下載任務')
                            self.finished_signal.emit(True, f"已成功儲存（訪客模式）：{title}")
                            return
                    except Exception as guest_err:
                        self.log_signal.emit(f"訪客模式受阻 ({guest_err})，啟動 CDN 直連嗅探...")

                    # 嘗試 B：CDN 直連嗅探（帶上專用 Referer 避免 403）
                    direct_video_url = self.fetch_meta_direct_video_url(real_post_id)
                    if direct_video_url:
                        self.log_signal.emit("【嗅探成功】取得真實 MP4 直連，正在下載封裝...")
                        cdn_out_template = os.path.join(self.save_path, f'Threads_{real_post_id}.%(ext)s')
                        cdn_opts = build_opts(direct_video_url, use_cookie_file_flag=False, is_direct_cdn=True)
                        cdn_opts['outtmpl'] = cdn_out_template
                        
                        with yt_dlp.YoutubeDL(cdn_opts) as ydl:
                            ydl.download([direct_video_url])
                            self.finished_signal.emit(True, f"已成功儲存（直連通道）：Threads_{real_post_id}.mp4")
                            return
                    else:
                        raise Exception("Meta 伺服器拒絕存取：此貼文受限。請更新 cookies.txt 後再試。")

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

        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        title = QLabel("Universal Video Hub")
        title.setObjectName("HeaderTitle")
        desc = QLabel("支援 YouTube、Bilibili (自動音訊合成)、Instagram、Threads、X (Twitter)、TikTok")
        desc.setObjectName("HeaderSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        root_layout.addLayout(header_layout)

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
        self.url_edit.setPlaceholderText("請貼上影片或播放清單網址 (支援短網址/分享連結)...")
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

        cookie_row = QHBoxLayout()
        cookie_label = QLabel("Cookie 檔案：")
        cookie_label.setFixedWidth(85)
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("可手動選擇 cookies.txt，留空則自動以訪客模式連線...")
        self.btn_browse_cookie = QPushButton("選取檔案")
        self.btn_browse_cookie.setProperty("class", "SecondaryBtn")
        self.btn_browse_cookie.clicked.connect(self.browse_cookie_file)
        cookie_row.addWidget(cookie_label)
        cookie_row.addWidget(self.cookie_file_edit, 1)
        cookie_row.addWidget(self.btn_browse_cookie)
        c2_layout.addLayout(cookie_row)

        root_layout.addWidget(card2)

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
            browser_name="",
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
