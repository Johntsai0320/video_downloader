"""FastAPI + WebSocket backend for mobile and cloud video downloading."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.websockets import WebSocketState

from src.core.downloader import (
    QUALITY_PRESETS,
    DownloadCancelledError,
    UniversalDownloaderEngine,
    validate_http_url,
)
from src.core.ffmpeg_helper import find_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_INDEX = PROJECT_ROOT / "docs" / "index.html"

# Use temp directory in cloud containers (e.g. Render/Koyeb), Downloads locally
_IS_CLOUD = bool(
    os.environ.get("RENDER") or os.environ.get("KOYEB_APP_NAME") or os.environ.get("CLOUD_ENV")
)
DOWNLOAD_DIR = Path(
    os.environ.get(
        "DOWNLOAD_DIR",
        os.path.join(tempfile.gettempdir(), "WebVideoDownloads")
        if _IS_CLOUD
        else os.path.join(os.path.expanduser("~"), "Downloads", "WebVideoDownloads"),
    )
)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

AUTO_CLEANUP = os.environ.get("AUTO_CLEANUP", "1" if _IS_CLOUD else "0").lower() in (
    "1",
    "true",
    "yes",
)
FILE_TTL_SECONDS = max(30, int(os.environ.get("FILE_TTL_SECONDS", "600")))
MAX_CONCURRENT_DOWNLOADS = max(
    1, int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2" if _IS_CLOUD else "4"))
)

_RAW_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").strip()
ALLOWED_ORIGINS = (
    ["*"]
    if not _RAW_ORIGINS or _RAW_ORIGINS == "*"
    else [o.strip() for o in _RAW_ORIGINS.split(",") if o.strip()]
)

_active_downloads_lock = threading.Lock()
_active_downloads = 0


def _get_access_token() -> str:
    return os.environ.get("API_ACCESS_TOKEN", "").strip()


def _is_authorized(provided_token: str) -> bool:
    expected = _get_access_token()
    if not expected:
        return True
    return bool(provided_token and provided_token.strip() == expected)


def _create_cookie_context() -> tuple[str, str | None]:
    """Resolve cookie file from COOKIES_DATA env var (per-task temp file) or COOKIE_FILE."""
    cookies_data = os.environ.get("COOKIES_DATA", "").strip()
    if cookies_data:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            suffix="_cloud_cookies.txt",
            encoding="utf-8",
        ) as tmp:
            with contextlib.suppress(OSError):
                os.chmod(tmp.name, 0o600)
            tmp.write(cookies_data)
            return tmp.name, tmp.name

    env_cookie_file = os.environ.get("COOKIE_FILE", "").strip()
    if env_cookie_file and os.path.exists(env_cookie_file):
        return env_cookie_file, None
    return "", None


def _safe_delete(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.exists():
            path.unlink()


def cleanup_expired_downloads(
    download_dir: Path = DOWNLOAD_DIR,
    ttl_seconds: int = FILE_TTL_SECONDS,
) -> int:
    """Remove expired media files and stale .part artifacts older than ttl_seconds."""
    if not AUTO_CLEANUP or not download_dir.exists():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for entry in download_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _cleanup_partial_artifacts(download_dir: Path, started_after: float) -> None:
    """Remove unfinished .part/.ytdl temp artifacts created during a cancelled task."""
    if not download_dir.exists():
        return
    for entry in download_dir.iterdir():
        if entry.is_file() and entry.name.endswith((".part", ".ytdl")):
            with contextlib.suppress(OSError):
                if entry.stat().st_mtime >= started_after - 1.0:
                    entry.unlink()


app = FastAPI(title="Universal Video Hub Web API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def get_index() -> HTMLResponse:
    if DOCS_INDEX.exists():
        return HTMLResponse(DOCS_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Universal Video Hub</h1><p>Frontend not found.</p>", status_code=404)


@app.get("/api/health")
async def health_check() -> JSONResponse:
    cleanup_expired_downloads()
    return JSONResponse(
        {
            "status": "ok",
            "ffmpeg": bool(find_ffmpeg()),
            "yt_dlp_version": getattr(yt_dlp.version, "__version__", "unknown"),
            "auth_required": bool(_get_access_token()),
            "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
            "presets": [{"key": k, "label": v["label"]} for k, v in QUALITY_PRESETS.items()],
        }
    )


@app.get("/download/{filename}")
async def serve_file(
    filename: str,
    background_tasks: BackgroundTasks,
    token: str = "",
):
    if not _is_authorized(token):
        return HTMLResponse("Unauthorized", status_code=401)

    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename:
        return HTMLResponse("Invalid filename", status_code=400)

    base = DOWNLOAD_DIR.resolve()
    target = (base / safe_name).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return HTMLResponse("File Not Found", status_code=404)

    if AUTO_CLEANUP and background_tasks is not None:
        # Touch mtime so active multi-request downloads (e.g. iOS Safari) aren't swept mid-transfer,
        # and sweep only files older than FILE_TTL_SECONDS.
        with contextlib.suppress(OSError):
            os.utime(target, None)
        background_tasks.add_task(cleanup_expired_downloads, DOWNLOAD_DIR, FILE_TTL_SECONDS)

    return FileResponse(path=str(target), filename=safe_name)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _active_downloads

    query_token = ws.query_params.get("token", "")
    auth_header = ws.headers.get("authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    if not _is_authorized(query_token or bearer_token):
        await ws.close(code=4401, reason="Unauthorized")
        return

    await ws.accept()
    loop = asyncio.get_running_loop()
    ws_closed = False

    async def safe_send(payload: dict) -> None:
        if ws_closed or ws.client_state != WebSocketState.CONNECTED:
            return
        with contextlib.suppress(Exception):
            await ws.send_json(payload)

    def send_async(payload: dict) -> None:
        if ws_closed:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(safe_send(payload), loop)

    try:
        while True:
            data = await ws.receive_json()
            if data.get("action") == "cancel":
                continue

            raw_url = (data.get("url") or "").strip()
            fmt = (data.get("format") or "best").strip()
            browser = (data.get("browser") or "").strip().lower()
            if browser == "none":
                browser = ""

            try:
                url = validate_http_url(raw_url)
            except ValueError as val_err:
                await safe_send({"type": "error", "data": str(val_err)})
                continue

            with _active_downloads_lock:
                if _active_downloads >= MAX_CONCURRENT_DOWNLOADS:
                    await safe_send(
                        {
                            "type": "finished",
                            "success": False,
                            "data": f"伺服器目前忙碌中（同時上限 {MAX_CONCURRENT_DOWNLOADS} 個任務），請稍後再試。",
                        }
                    )
                    continue
                _active_downloads += 1

            cleanup_expired_downloads()
            cancel_event = threading.Event()
            started_at = time.time()
            cookie_file, temp_cloud_cookie = _create_cookie_context()

            try:
                await safe_send({"type": "log", "data": f">> 開始任務：{url}"})
                if browser:
                    await safe_send(
                        {
                            "type": "log",
                            "data": f">> 嘗試自動調用本機 [{browser.capitalize()}] Cookie...",
                        }
                    )

                engine = UniversalDownloaderEngine(
                    on_progress=lambda pct: send_async(
                        {"type": "progress", "percent": pct, "desc": f"下載進度：{pct}%"}
                    ),
                    on_status=lambda desc: send_async({"type": "status", "desc": desc}),
                    on_log=lambda msg: send_async({"type": "log", "data": msg}),
                    is_cancelled=cancel_event.is_set,
                )

                download_call = functools.partial(
                    engine.download,
                    url=url,
                    save_path=str(DOWNLOAD_DIR),
                    quality_option=fmt,
                    cookie_file=cookie_file,
                    browser_name=browser,
                    filename_template="%(title).60s [%(id)s].%(ext)s",
                )
                download_future = loop.run_in_executor(None, download_call)

                while not download_future.done():
                    recv_task = asyncio.create_task(ws.receive_json())
                    done, _ = await asyncio.wait(
                        {download_future, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if recv_task in done:
                        try:
                            ctrl_msg = recv_task.result()
                            if ctrl_msg.get("action") == "cancel":
                                cancel_event.set()
                                await safe_send({"type": "log", "data": ">> 收到中止指令，正在停止任務..."})
                        except WebSocketDisconnect:
                            ws_closed = True
                            cancel_event.set()
                            break
                        except Exception:
                            ws_closed = True
                            cancel_event.set()
                            break
                    else:
                        recv_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await recv_task

                if ws_closed:
                    with contextlib.suppress(Exception):
                        await download_future
                    _cleanup_partial_artifacts(DOWNLOAD_DIR, started_at)
                    return

                result = await download_future
                if not result.filepath or not os.path.exists(result.filepath):
                    raise RuntimeError("下載已完成，但找不到輸出檔案。")

                filename = os.path.basename(result.filepath)
                encoded_name = urllib.parse.quote(filename, safe="")
                await safe_send(
                    {
                        "type": "finished",
                        "success": True,
                        "title": result.title,
                        "download_url": f"/download/{encoded_name}",
                    }
                )
            except DownloadCancelledError:
                _cleanup_partial_artifacts(DOWNLOAD_DIR, started_at)
                await safe_send(
                    {"type": "finished", "success": False, "data": "任務已依使用者要求中止。"}
                )
            except Exception as exc:
                await safe_send({"type": "finished", "success": False, "data": str(exc)})
            finally:
                if temp_cloud_cookie:
                    _safe_delete(Path(temp_cloud_cookie))
                with _active_downloads_lock:
                    _active_downloads = max(0, _active_downloads - 1)
    except WebSocketDisconnect:
        ws_closed = True
