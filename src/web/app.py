"""FastAPI + WebSocket backend for mobile and cloud video downloading."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from src.core.downloader import QUALITY_PRESETS, UniversalDownloaderEngine
from src.core.ffmpeg_helper import find_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_INDEX = PROJECT_ROOT / "docs" / "index.html"

# Use temp directory in cloud containers (e.g. Render/Koyeb), Downloads locally
_IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("KOYEB_APP_NAME") or os.environ.get("CLOUD_ENV"))
DOWNLOAD_DIR = Path(
    os.environ.get(
        "DOWNLOAD_DIR",
        os.path.join(tempfile.gettempdir(), "WebVideoDownloads")
        if _IS_CLOUD
        else os.path.join(os.path.expanduser("~"), "Downloads", "WebVideoDownloads"),
    )
)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

AUTO_CLEANUP = os.environ.get("AUTO_CLEANUP", "1" if _IS_CLOUD else "0").lower() in ("1", "true", "yes")


def _resolve_cookie_file() -> str:
    """Resolve cookie file from COOKIES_DATA env var (cloud) or local cookies.txt if configured."""
    cookies_data = os.environ.get("COOKIES_DATA", "").strip()
    if cookies_data:
        cookie_path = Path(tempfile.gettempdir()) / "cloud_cookies.txt"
        cookie_path.write_text(cookies_data, encoding="utf-8")
        return str(cookie_path)
    env_cookie_file = os.environ.get("COOKIE_FILE", "").strip()
    if env_cookie_file and os.path.exists(env_cookie_file):
        return env_cookie_file
    return ""


def _safe_delete(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


app = FastAPI(title="Universal Video Hub Web API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return JSONResponse(
        {
            "status": "ok",
            "ffmpeg": bool(find_ffmpeg()),
            "yt_dlp_version": getattr(yt_dlp.version, "__version__", "unknown"),
            "presets": [{"key": k, "label": v["label"]} for k, v in QUALITY_PRESETS.items()],
        }
    )


@app.get("/download/{filename}")
async def serve_file(filename: str, background_tasks: BackgroundTasks):
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename:
        return HTMLResponse("Invalid filename", status_code=400)

    base = DOWNLOAD_DIR.resolve()
    target = (base / safe_name).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        return HTMLResponse("File Not Found", status_code=404)

    if AUTO_CLEANUP:
        background_tasks.add_task(_safe_delete, target)

    return FileResponse(path=str(target), filename=safe_name)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    loop = asyncio.get_running_loop()

    def send_async(payload: dict) -> None:
        asyncio.run_coroutine_threadsafe(ws.send_json(payload), loop)

    try:
        while True:
            data = await ws.receive_json()
            url = (data.get("url") or "").strip()
            fmt = (data.get("format") or "best").strip()
            browser = (data.get("browser") or "").strip().lower()
            if browser == "none":
                browser = ""

            if not url:
                await ws.send_json({"type": "error", "data": "請輸入有效網址"})
                continue

            await ws.send_json({"type": "log", "data": f">> 開始任務：{url}"})
            if browser:
                await ws.send_json(
                    {"type": "log", "data": f">> 嘗試自動調用本機 [{browser.capitalize()}] Cookie..."}
                )

            cookie_file = _resolve_cookie_file()

            engine = UniversalDownloaderEngine(
                on_progress=lambda pct: send_async(
                    {"type": "progress", "percent": pct, "desc": f"下載進度：{pct}%"}
                ),
                on_status=lambda desc: send_async({"type": "status", "desc": desc}),
                on_log=lambda msg: send_async({"type": "log", "data": msg}),
            )

            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: engine.download(
                        url=url,
                        save_path=str(DOWNLOAD_DIR),
                        quality_option=fmt,
                        cookie_file=cookie_file,
                        browser_name=browser,
                        filename_template="%(title).60s [%(id)s].%(ext)s",
                    ),
                )
                if not result.filepath or not os.path.exists(result.filepath):
                    raise RuntimeError("下載已完成，但找不到輸出檔案。")

                filename = os.path.basename(result.filepath)
                await ws.send_json(
                    {
                        "type": "finished",
                        "success": True,
                        "title": result.title,
                        "download_url": f"/download/{filename}",
                    }
                )
            except Exception as exc:
                await ws.send_json({"type": "finished", "success": False, "data": str(exc)})
    except WebSocketDisconnect:
        pass
