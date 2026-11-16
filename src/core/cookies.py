"""Cookie sanitization and local browser cookie extraction helpers."""

from __future__ import annotations

import os
import sys
import tempfile


class BrowserCookieError(Exception):
    """Raised when browser cookies cannot be extracted."""


class BrowserCookieLockedError(BrowserCookieError):
    """Raised when browser cookie DB is locked or protected by Windows App-Bound encryption."""


def browser_profile_exists(browser_name: str) -> bool:
    """Check whether a local profile directory exists for the given browser."""
    browser = (browser_name or "").strip().lower()
    if not browser:
        return False

    home = os.path.expanduser("~")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        candidates = {
            "firefox": [
                os.path.join(appdata, "Mozilla", "Firefox", "Profiles"),
                os.path.join(local, "Packages"),
            ],
            "chrome": [os.path.join(local, "Google", "Chrome", "User Data")],
            "edge": [os.path.join(local, "Microsoft", "Edge", "User Data")],
            "brave": [os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")],
            "opera": [os.path.join(appdata, "Opera Software", "Opera Stable")],
        }
    elif sys.platform == "darwin":
        app_support = os.path.join(home, "Library", "Application Support")
        candidates = {
            "firefox": [os.path.join(app_support, "Firefox", "Profiles")],
            "chrome": [os.path.join(app_support, "Google", "Chrome")],
            "edge": [os.path.join(app_support, "Microsoft Edge")],
            "brave": [os.path.join(app_support, "BraveSoftware", "Brave-Browser")],
            "opera": [os.path.join(app_support, "com.operasoftware.Opera")],
        }
    else:
        config = os.path.join(home, ".config")
        candidates = {
            "firefox": [os.path.join(home, ".mozilla", "firefox")],
            "chrome": [os.path.join(config, "google-chrome")],
            "edge": [os.path.join(config, "microsoft-edge")],
            "brave": [os.path.join(config, "BraveSoftware", "Brave-Browser")],
            "opera": [os.path.join(config, "opera")],
        }

    paths = candidates.get(browser, [])
    for path in paths:
        if browser == "firefox" and sys.platform == "win32" and path.endswith("Packages"):
            if os.path.isdir(path) and any(
                name.startswith("Mozilla.Firefox_") for name in os.listdir(path)
            ):
                return True
        elif os.path.exists(path):
            return True
    return False


def create_sanitized_meta_cookies(original_cookie_path: str) -> tuple[str | None, str | None]:
    """Filter cookies.txt down to Meta domains (instagram/threads) in a temp file.

    Returns (active_cookie_path, temp_file_path_to_cleanup).
    """
    if not original_cookie_path or not os.path.exists(original_cookie_path):
        return None, None

    try:
        with open(original_cookie_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        clean_lines: list[str] = []
        allowed_domains = ("instagram.com", "threads.net", "threads.com")
        has_cookie_entry = False

        for line in lines:
            if line.startswith("#") and not line.startswith("#HttpOnly_"):
                clean_lines.append(line)
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                domain = parts[0].lower()
                if any(ad in domain for ad in allowed_domains):
                    clean_lines.append(line)
                    has_cookie_entry = True

        if has_cookie_entry:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w",
                delete=False,
                suffix="_meta_cookies.txt",
                encoding="utf-8",
            )
            temp_file.writelines(clean_lines)
            temp_file.close()
            return temp_file.name, temp_file.name
    except Exception:
        pass

    return original_cookie_path, None


def extract_browser_cookies_to_file(browser_name: str, output_dir: str) -> str:
    """Extract cookies from a local browser and save them in Netscape cookies.txt format."""
    from yt_dlp.cookies import extract_cookies_from_browser

    browser = (browser_name or "").strip().lower()
    if not browser or browser == "none":
        raise BrowserCookieError("未指定有效的瀏覽器名稱。")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{browser}_cookies.txt")

    try:
        cj = extract_cookies_from_browser(browser)
        if not cj:
            raise BrowserCookieError(
                f"無法從 {browser.capitalize()} 讀取 Cookie，請確認該瀏覽器已登入帳號。"
            )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# Generated automatically by Universal Video Hub\n\n")
            for c in cj:
                domain = c.domain
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                path = c.path
                secure = "TRUE" if c.secure else "FALSE"
                expires = str(c.expires) if c.expires else "0"
                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{c.name}\t{c.value}\n")

        return output_path
    except BrowserCookieError:
        raise
    except Exception as exc:
        err_msg = str(exc)
        lowered = err_msg.lower()
        if any(
            token in lowered
            for token in ("database is locked", "permission denied", "cookie database", "dpapi")
        ):
            raise BrowserCookieLockedError(err_msg) from exc
        raise BrowserCookieError(err_msg) from exc
