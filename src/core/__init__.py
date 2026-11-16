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
    media_id_to_shortcode,
    normalize_url,
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
    "extract_browser_cookies_to_file",
    "find_ffmpeg",
    "find_ffprobe",
    "get_ffmpeg_location",
    "media_id_to_shortcode",
    "normalize_url",
]
