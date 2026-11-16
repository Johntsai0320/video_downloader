"""Unified yt-dlp + FFmpeg download engine shared by Desktop GUI and Web API."""

from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

import yt_dlp

from .cookies import browser_profile_exists, create_sanitized_meta_cookies
from .ffmpeg_helper import get_ffmpeg_location

# Canonical quality preset keys and their human-readable labels
QUALITY_PRESETS: dict[str, dict[str, str]] = {
    "best": {
        "label": "MP4：最佳畫質 (通用)",
        "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
        "mode": "video",
    },
    "1080p": {
        "label": "MP4：1080p 以下",
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "mode": "video",
    },
    "720p": {
        "label": "MP4：720p 以下",
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/b",
        "mode": "video",
    },
    "audio": {
        "label": "MP3 音訊擷取",
        "format": "ba/b",
        "mode": "audio",
    },
}

_LABEL_TO_KEY = {v["label"]: k for k, v in QUALITY_PRESETS.items()}
_LABEL_TO_KEY["video"] = "best"
_LABEL_TO_KEY["mp4"] = "best"
_LABEL_TO_KEY["mp3"] = "audio"

_X_PROXY_DOMAINS = ("vxtwitter.com", "fxtwitter.com", "fixupx.com", "twittpr.com")
_META_DOMAINS = ("instagram.com", "threads.net", "facebook.com", "fb.watch")

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


class DownloadCancelledError(Exception):
    """Raised when a download task is cancelled by the user."""


@dataclass
class DownloadResult:
    """Result metadata returned after a successful download."""

    title: str
    filepath: str | None
    mode_note: str = ""


def resolve_quality_key(quality_option: str) -> str:
    """Map either a preset key ('best', '1080p', ...) or GUI label to a canonical key."""
    if quality_option in QUALITY_PRESETS:
        return quality_option
    return _LABEL_TO_KEY.get(quality_option, "best")


def media_id_to_shortcode(media_id: int) -> str:
    """Convert a 64-bit Meta numeric media_id to an Instagram/Threads base64url shortcode."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if media_id <= 0:
        return ""
    shortcode: list[str] = []
    while media_id > 0:
        media_id, rem = divmod(media_id, 64)
        shortcode.append(alphabet[rem])
    return "".join(reversed(shortcode))


def normalize_url(raw_url: str) -> tuple[str, bool]:
    """Normalize known proxy domains and Threads URLs.

    Returns (normalized_url, proxy_rewritten).
    """
    target_url = raw_url.strip()
    rewritten = False

    for proxy_domain in _X_PROXY_DOMAINS:
        if proxy_domain in target_url:
            target_url = target_url.replace(proxy_domain, "x.com")
            rewritten = True
            break

    if "threads.com" in target_url:
        target_url = target_url.replace("threads.com", "threads.net")
    if "threads.net" in target_url and "?" in target_url and "injected_media_ids" not in target_url:
        target_url = target_url.split("?")[0]

    return target_url, rewritten


def resolve_meta_redirect(
    raw_url: str, log_cb: Callable[[str], None] | None = None
) -> tuple[str, str | None]:
    """Follow Threads /share/ redirects and decode shortcode or injected_media_ids."""
    try:
        req = urllib.request.Request(
            raw_url,
            headers={
                "User-Agent": _DESKTOP_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
            content = resp.read(65536).decode("utf-8", errors="ignore")

        match_post = re.search(r"/(?:post|t)/([A-Za-z0-9_-]+)", final_url)
        if match_post:
            code = match_post.group(1)
            return f"https://www.threads.net/t/{code}", code

        match_media_id = (
            re.search(r"injected_media_ids=%5B%22(\d+)%22%5D", final_url)
            or re.search(r'injected_media_ids=\["(\d+)"\]', urllib.parse.unquote(final_url))
            or re.search(r'"media_id":"(\d+)"', content)
        )
        if match_media_id:
            mid = int(match_media_id.group(1))
            code = media_id_to_shortcode(mid)
            if code:
                return f"https://www.instagram.com/p/{code}/", code

        match_og = re.search(r'property="og:url"\s+content="([^"]+)"', content)
        if match_og:
            og_post = re.search(r"/(?:post|t|p)/([A-Za-z0-9_-]+)", match_og.group(1))
            if og_post:
                code = og_post.group(1)
                return f"https://www.threads.net/t/{code}", code
    except Exception as exc:
        if log_cb:
            log_cb(f"【縮網址解析警告】{exc}")

    return raw_url, None


def fetch_meta_direct_video_url(
    post_id: str, log_cb: Callable[[str], None] | None = None
) -> str | None:
    """Sniff progressive MP4 CDN URL from public Meta embed/Threads HTML, excluding DASH fragments."""
    if log_cb:
        log_cb("【直連嗅探】嘗試透過公開嵌入通道取得真實 MP4 直連...")

    headers = {
        "User-Agent": _MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    bad_tokens = ("BaseURL", "Segment", "mpd", "\\u003C", "<")

    def _pick_clean_mp4(text: str) -> str | None:
        for raw_u in re.findall(r'"video_url":\s*"([^"]+)"', text):
            clean = raw_u.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
            if "cdninstagram.com" in clean and not any(tok in clean for tok in bad_tokens):
                return clean
        for raw_u in re.findall(r'https:[^"]+?cdninstagram\.com[^"]+?\.mp4[^"]*', text):
            clean = raw_u.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
            if not any(tok in clean for tok in bad_tokens):
                return clean
        return None

    for probe_url in (
        f"https://www.instagram.com/p/{post_id}/embed/captioned/",
        f"https://www.threads.net/t/{post_id}",
    ):
        try:
            req = urllib.request.Request(probe_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
            found = _pick_clean_mp4(content)
            if found:
                return found
        except Exception:
            continue

    return None


class UniversalDownloaderEngine:
    """Core yt-dlp download orchestrator with Meta/Bilibili/FFmpeg fallbacks."""

    def __init__(
        self,
        on_progress: Callable[[int], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        self.on_progress = on_progress
        self.on_status = on_status
        self.on_log = on_log
        self.is_cancelled = is_cancelled

    def _emit_log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def _emit_progress(self, pct: int) -> None:
        if self.on_progress:
            self.on_progress(pct)

    def _progress_hook(self, d: dict) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise DownloadCancelledError("任務已取消。")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            info_dict = d.get("info_dict") or {}
            p_index = info_dict.get("playlist_index")
            p_count = info_dict.get("n_entries")
            prefix = f"[{p_index}/{p_count}] " if (p_index and p_count) else ""

            if total > 0:
                percent = min(100, int((downloaded / total) * 100))
                self._emit_progress(percent)
                speed = d.get("_speed_str", "N/A")
                eta = d.get("_eta_str", "N/A")
                self._emit_status(f"{prefix}下載進度: {percent}% | 速度: {speed} | 剩餘時間: {eta}")
        elif status == "finished":
            self._emit_progress(100)
            self._emit_status("音訊與視訊下載完成，FFmpeg 封裝處理中...")

    def build_ydl_opts(
        self,
        url_target: str,
        out_template: str,
        quality_option: str,
        *,
        is_playlist: bool = False,
        playlist_range: str = "",
        cookie_file: str | None = None,
        browser_name: str | None = None,
        use_cookie_flag: bool = False,
        force_mobile: bool = False,
        is_direct_cdn: bool = False,
        ffmpeg_location: str | None = None,
    ) -> dict:
        """Build a yt-dlp options dictionary for a given target and mode."""
        headers = {
            "User-Agent": _MOBILE_UA if force_mobile else _DESKTOP_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,en-US;q=0.7,en;q=0.6",
        }

        if "bilibili.com" in url_target:
            headers["Referer"] = "https://www.bilibili.com/"
            headers["Origin"] = "https://www.bilibili.com"

        if is_direct_cdn or "cdninstagram.com" in url_target:
            headers["Referer"] = "https://www.instagram.com/"
            headers["Origin"] = "https://www.instagram.com"

        opts: dict = {
            "outtmpl": out_template,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "noplaylist": not is_playlist,
            "socket_timeout": 30,
            "http_headers": headers,
        }

        if "instagram.com" in url_target and not is_direct_cdn:
            opts["extractor_args"] = {"instagram": {"api": ["mobile"]}}

        if is_playlist and playlist_range.strip():
            opts["playlist_items"] = playlist_range.strip()

        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location

        if use_cookie_flag:
            if cookie_file and os.path.exists(cookie_file):
                opts["cookiefile"] = cookie_file
            elif browser_name and browser_name.lower() != "none" and browser_profile_exists(browser_name):
                opts["cookiesfrombrowser"] = (browser_name.lower(),)

        preset_key = resolve_quality_key(quality_option)
        preset = QUALITY_PRESETS[preset_key]
        opts["format"] = preset["format"]

        if preset["mode"] == "audio":
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            opts["merge_output_format"] = "mp4"

        return opts

    @staticmethod
    def _extract_final_filepath(ydl: yt_dlp.YoutubeDL, info: dict | None, preset_key: str) -> str | None:
        if not info:
            return None

        req_downloads = info.get("requested_downloads")
        if isinstance(req_downloads, list) and req_downloads:
            fp = req_downloads[0].get("filepath")
            if fp:
                return fp

        try:
            raw_path = ydl.prepare_filename(info)
        except Exception:
            return None

        stem, _ = os.path.splitext(raw_path)
        target_ext = ".mp3" if QUALITY_PRESETS[preset_key]["mode"] == "audio" else ".mp4"
        candidate = stem + target_ext
        if os.path.exists(candidate):
            return candidate
        return raw_path

    def download(
        self,
        url: str,
        save_path: str,
        quality_option: str = "best",
        *,
        cookie_file: str = "",
        browser_name: str = "",
        is_playlist: bool = False,
        playlist_range: str = "",
        filename_template: str | None = None,
    ) -> DownloadResult:
        """Execute a full download task with automatic fallback and cleanup."""
        os.makedirs(save_path, exist_ok=True)
        preset_key = resolve_quality_key(quality_option)
        ffmpeg_location = get_ffmpeg_location()

        target_url, proxy_rewritten = normalize_url(url)
        if proxy_rewritten:
            self._emit_log(f"【自動規範】已將轉發網址轉換為官方網址：{target_url}")

        extracted_code: str | None = None
        if (
            "threads.com/share/" in url
            or "threads.net/share/" in url
            or "injected_media_ids" in url
        ):
            self._emit_log("【縮網址解析】偵測到 Threads 分享短連結，正在追蹤原始貼文...")
            target_url, extracted_code = resolve_meta_redirect(url, self._emit_log)
            target_url, _ = normalize_url(target_url)
            self._emit_log(f"【目標定位】已鎖定核心媒體網址：{target_url}")

        if filename_template:
            out_template = os.path.join(save_path, filename_template)
        elif is_playlist:
            out_template = os.path.join(
                save_path,
                "%(playlist_title|播放清單)s",
                "%(playlist_index)02d - %(title).80s [%(id)s].%(ext)s",
            )
        else:
            out_template = os.path.join(save_path, "%(title).80s [%(id)s].%(ext)s")

        cookie_file_clean = cookie_file.strip()
        has_valid_cookie_file = bool(cookie_file_clean and os.path.exists(cookie_file_clean))
        browser_clean = (browser_name or "").strip().lower()
        has_valid_browser = bool(
            browser_clean and browser_clean != "none" and browser_profile_exists(browser_clean)
        )
        has_cookie_source = has_valid_cookie_file or has_valid_browser

        is_meta_site = any(domain in target_url for domain in _META_DOMAINS)
        active_cookie_file = cookie_file_clean
        temp_cookie_file: str | None = None

        if is_meta_site and has_valid_cookie_file:
            active_cookie_file, temp_cookie_file = create_sanitized_meta_cookies(cookie_file_clean)
            if temp_cookie_file:
                self._emit_log("【自動優化】已純化 Meta 專屬 Session 憑證...")

        if ffmpeg_location:
            self._emit_log(f"【FFmpeg 偵測】已啟用多媒體合併引擎：{ffmpeg_location}")
        else:
            self._emit_log("【FFmpeg 警告】未偵測到 FFmpeg，高畫質分離串流可能無法合併音軌。")

        if has_valid_cookie_file:
            self._emit_log("【智慧決策】啟用 Cookie 檔案認證模式...")
        elif has_valid_browser:
            self._emit_log(f"【智慧決策】嘗試調用本機 {browser_clean.capitalize()} Cookie 認證...")
        else:
            self._emit_log("【智慧決策】未指定有效 Cookie，啟用純淨訪客模式...")

        self._emit_status("正在解析串流內容並下載...")

        def _run_once(
            url_target: str,
            use_cookie: bool,
            force_mobile: bool = False,
            is_direct_cdn: bool = False,
            override_outtmpl: str | None = None,
        ) -> tuple[dict | None, str | None]:
            opts = self.build_ydl_opts(
                url_target,
                override_outtmpl or out_template,
                preset_key,
                is_playlist=is_playlist,
                playlist_range=playlist_range,
                cookie_file=active_cookie_file,
                browser_name=browser_clean,
                use_cookie_flag=use_cookie,
                force_mobile=force_mobile,
                is_direct_cdn=is_direct_cdn,
                ffmpeg_location=ffmpeg_location,
            )
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url_target, download=True)
                fpath = self._extract_final_filepath(ydl, info, preset_key)
                return info, fpath

        try:
            try:
                info, fpath = _run_once(target_url, use_cookie=has_cookie_source)
                title = (info or {}).get("title", "下載任務")
                return DownloadResult(title=title, filepath=fpath)
            except DownloadCancelledError:
                raise
            except Exception as primary_error:
                error_str = str(primary_error)

                # Bilibili HTTP 412 fallback with cookies/Referer
                if "bilibili.com" in target_url and "412" in error_str and has_cookie_source:
                    self._emit_log("【B站防護突破】偵測到 HTTP 412 限制，嘗試掛載憑證重試...")
                    try:
                        info, fpath = _run_once(target_url, use_cookie=True)
                        title = (info or {}).get("title", "下載任務")
                        return DownloadResult(title=title, filepath=fpath)
                    except Exception as bili_err:
                        self._emit_log(f"【重試訊息】{bili_err}")

                # Cookie conflict / DPAPI fallback to guest mode
                if has_cookie_source and any(
                    tok in error_str for tok in ("403", "400", "DPAPI", "cookie database", "database is locked")
                ):
                    self._emit_log("【自動修復】偵測到 Cookie 讀取或權限衝突，切換至訪客模式重試...")
                    try:
                        info, fpath = _run_once(target_url, use_cookie=False)
                        title = (info or {}).get("title", "下載任務")
                        return DownloadResult(title=title, filepath=fpath, mode_note="免登入模式")
                    except Exception as guest_retry_err:
                        self._emit_log(f"訪客重試未果：{guest_retry_err}")

                # Meta (Instagram / Threads) multi-stage rescue
                real_post_id = extracted_code
                if not real_post_id:
                    match_id = (
                        re.search(r"/(?:post|t|p)/([A-Za-z0-9_-]+)", error_str)
                        or re.search(r"/(?:post|t|p)/([A-Za-z0-9_-]+)", target_url)
                    )
                    if match_id and any(k in target_url or k in error_str for k in ("instagram", "threads")):
                        real_post_id = match_id.group(1)

                if real_post_id:
                    fallback_ig = f"https://www.instagram.com/p/{real_post_id}/"
                    self._emit_log(f"【最後嘗試】以 Instagram 行動端 API 重試：{fallback_ig}")

                    try:
                        info, fpath = _run_once(fallback_ig, use_cookie=False, force_mobile=True)
                        title = (info or {}).get("title", "下載任務")
                        return DownloadResult(title=title, filepath=fpath, mode_note="訪客模式")
                    except Exception as guest_err:
                        self._emit_log(f"訪客模式受阻 ({guest_err})，啟動 CDN 直連嗅探...")

                    direct_video_url = fetch_meta_direct_video_url(real_post_id, self._emit_log)
                    if direct_video_url:
                        self._emit_log("【嗅探成功】取得真實 MP4 直連，正在下載封裝...")
                        cdn_outtmpl = os.path.join(save_path, f"Threads_{real_post_id}.%(ext)s")
                        _, fpath = _run_once(
                            direct_video_url,
                            use_cookie=False,
                            is_direct_cdn=True,
                            override_outtmpl=cdn_outtmpl,
                        )
                        expected_mp4 = os.path.join(save_path, f"Threads_{real_post_id}.mp4")
                        if not fpath or not os.path.exists(fpath):
                            fpath = expected_mp4
                        return DownloadResult(
                            title=f"Threads_{real_post_id}.mp4",
                            filepath=fpath,
                            mode_note="直連通道",
                        )

                    raise RuntimeError(
                        "Meta 伺服器拒絕存取：此貼文受限。請更新 cookies.txt 後再試。"
                    ) from primary_error

                raise primary_error
        finally:
            if temp_cookie_file and os.path.exists(temp_cookie_file):
                try:
                    os.remove(temp_cookie_file)
                except Exception:
                    pass
