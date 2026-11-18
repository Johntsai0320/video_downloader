"""Shared core modules for desktop and web downloaders."""

from .cookies import (
    BrowserCookieError,
    BrowserCookieLockedError,
    browser_profile_exists,
    create_sanitized_meta_cookies,
    extract_browser_cookies_to_file,
)
from .downloader import (
    QUALITY_PRESETS,
    DownloadCancelledError,
    DownloadResult,
    UniversalDownloaderEngine,
    detect_js_runtimes,
    media_id_to_shortcode,
    normalize_url,
    validate_http_url,
)
from .ffmpeg_helper import find_ffmpeg, find_ffprobe, get_ffmpeg_location

__all__ = [
    "BrowserCookieError",
    "BrowserCookieLockedError",
    "DownloadCancelledError",
    "DownloadResult",
    "QUALITY_PRESETS",
    "UniversalDownloaderEngine",
    "browser_profile_exists",
    "create_sanitized_meta_cookies",
    "detect_js_runtimes",
    "extract_browser_cookies_to_file",
    "find_ffmpeg",
    "find_ffprobe",
    "get_ffmpeg_location",
    "media_id_to_shortcode",
    "normalize_url",
    "validate_http_url",
]
