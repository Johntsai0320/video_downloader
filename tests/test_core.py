"""Core unit and WebSocket/HTTP integration tests for Universal Video Hub."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import src.web.app as web_app
from src.core.cookies import create_sanitized_meta_cookies
from src.core.downloader import (
    DownloadCancelledError,
    DownloadResult,
    UniversalDownloaderEngine,
    detect_js_runtimes,
    media_id_to_shortcode,
    normalize_url,
    validate_http_url,
)
from src.core.ffmpeg_helper import find_ffmpeg
from src.web.app import (
    DOWNLOAD_DIR,
    cleanup_expired_downloads,
    get_index,
    health_check,
    serve_file,
)


class CoreTests(unittest.TestCase):
    def test_validate_http_url(self) -> None:
        self.assertEqual(
            validate_http_url("  https://www.youtube.com/watch?v=123  "),
            "https://www.youtube.com/watch?v=123",
        )
        for bad in ("", "   ", "file:///etc/passwd", "javascript:alert(1)", "not-a-url"):
            with self.assertRaises(ValueError):
                validate_http_url(bad)

    def test_media_id_to_shortcode(self) -> None:
        self.assertEqual(media_id_to_shortcode(0), "")
        code = media_id_to_shortcode(3986797102625621582)
        self.assertTrue(len(code) > 0)
        self.assertTrue(all(ch.isalnum() or ch in "-_" for ch in code))

    def test_normalize_url(self) -> None:
        u1, rewritten1 = normalize_url("https://fxtwitter.com/user/status/123")
        self.assertEqual(u1, "https://x.com/user/status/123")
        self.assertTrue(rewritten1)

        u_sub, rewritten_sub = normalize_url("https://d.fxtwitter.com/user/status/123?s=20")
        self.assertEqual(u_sub, "https://x.com/user/status/123?s=20")
        self.assertTrue(rewritten_sub)

        u2, rewritten2 = normalize_url("https://www.threads.net/@user/post/AbCd123?xmt=foo")
        self.assertEqual(u2, "https://www.threads.com/@user/post/AbCd123")
        self.assertFalse(rewritten2)

        u3, rewritten3 = normalize_url(
            "https://www.threads.com/share/123?injected_media_ids=%5B%22999%22%5D"
        )
        self.assertIn("injected_media_ids", u3)
        self.assertFalse(rewritten3)

    def test_create_sanitized_meta_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "mixed_cookies.txt"
            src.write_text(
                "# Netscape HTTP Cookie File\n"
                ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR\tabc\n"
                ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\txyz\n"
                ".threads.com\tTRUE\t/\tTRUE\t0\tcsrftoken\t123\n",
                encoding="utf-8",
            )

            active_path, temp_path = create_sanitized_meta_cookies(str(src))
            try:
                self.assertIsNotNone(temp_path)
                self.assertEqual(active_path, temp_path)
                content = Path(active_path).read_text(encoding="utf-8")
                self.assertIn("instagram.com", content)
                self.assertIn("threads.com", content)
                self.assertNotIn("youtube.com", content)
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    def test_build_ydl_opts_presets_and_tls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = UniversalDownloaderEngine()
            out = os.path.join(tmp_dir, "%(title)s.%(ext)s")

            best_opts = engine.build_ydl_opts("https://www.youtube.com/shorts/abc", out, "best")
            self.assertEqual(best_opts["merge_output_format"], "mp4")
            self.assertIn("bv*[ext=mp4]", best_opts["format"])
            self.assertIn("protocol~='^https?$'", best_opts["format"])
            self.assertTrue(best_opts["noprogress"])
            self.assertTrue(best_opts["noplaylist"])
            self.assertFalse(best_opts["nocheckcertificate"])

            insecure_opts = engine.build_ydl_opts(
                "https://www.youtube.com/watch?v=abc",
                out,
                "best",
                allow_insecure_ssl=True,
            )
            self.assertTrue(insecure_opts["nocheckcertificate"])

            opts_1080 = engine.build_ydl_opts(
                "https://www.bilibili.com/video/BV123",
                out,
                "MP4：1080p 以下",
                is_playlist=True,
                playlist_range="1-3",
            )
            self.assertEqual(opts_1080["http_headers"]["Referer"], "https://www.bilibili.com/")
            self.assertFalse(opts_1080["noplaylist"])
            self.assertEqual(opts_1080["playlist_items"], "1-3")
            self.assertIn("height<=1080", opts_1080["format"])

            b23_opts = engine.build_ydl_opts("https://b23.tv/abc123", out, "best")
            self.assertEqual(b23_opts["http_headers"]["Referer"], "https://www.bilibili.com/")

            audio_opts = engine.build_ydl_opts("https://www.youtube.com/watch?v=1", out, "audio")
            self.assertEqual(audio_opts["postprocessors"][0]["preferredcodec"], "mp3")

    def test_detect_js_runtimes_and_ffmpeg(self) -> None:
        runtimes = detect_js_runtimes()
        self.assertTrue(len(runtimes) > 0, "Expected at least deno or node runtime in test environment")

        ffmpeg_path = find_ffmpeg()
        self.assertIsNotNone(ffmpeg_path)
        self.assertTrue(os.path.isfile(ffmpeg_path))

    def test_extract_final_filepath_alternate_containers(self) -> None:
        class _FakeYDL:
            def __init__(self, base: str):
                self._base = base

            def prepare_filename(self, info: dict) -> str:
                return self._base

        with tempfile.TemporaryDirectory() as tmp_dir:
            mkv_path = Path(tmp_dir) / "sample.mkv"
            mkv_path.write_bytes(b"MKV")
            fake_ydl = _FakeYDL(str(Path(tmp_dir) / "sample.webm"))
            resolved = UniversalDownloaderEngine._extract_final_filepath(
                fake_ydl, {"id": "1"}, "best"
            )
            self.assertEqual(resolved, str(mkv_path))

    def test_web_app_handlers(self) -> None:
        async def _run() -> None:
            index_resp = await get_index()
            self.assertEqual(index_resp.status_code, 200)
            self.assertIn("Universal Video Hub", index_resp.body.decode("utf-8"))

            health_resp = await health_check()
            self.assertEqual(health_resp.status_code, 200)
            payload = json.loads(health_resp.body.decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["ffmpeg"])

            bad_dl = await serve_file("../../secret.txt", BackgroundTasks())
            self.assertEqual(bad_dl.status_code, 400)

            missing_dl = await serve_file("nonexistent_video_file.mp4", BackgroundTasks())
            self.assertEqual(missing_dl.status_code, 404)
            self.assertTrue(DOWNLOAD_DIR.exists())

        asyncio.run(_run())

    def test_web_websocket_encoding_ttl_cancel_and_auth(self) -> None:
        orig_dir = web_app.DOWNLOAD_DIR
        orig_cleanup = web_app.AUTO_CLEANUP
        orig_ttl = web_app.FILE_TTL_SECONDS
        orig_token = os.environ.get("API_ACCESS_TOKEN")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            web_app.DOWNLOAD_DIR = tmp_dir
            web_app.AUTO_CLEANUP = True
            web_app.FILE_TTL_SECONDS = 600
            os.environ.pop("API_ACCESS_TOKEN", None)

            try:
                special_file = tmp_dir / "測試影片 #1 ? 100% [abc123].mp4"
                special_file.write_bytes(b"MP4DATA")

                expired_file = tmp_dir / "old_file.mp4"
                expired_file.write_bytes(b"OLD")
                old_ts = time.time() - 1200
                os.utime(expired_file, (old_ts, old_ts))

                def fake_download(*args, **kwargs):
                    return DownloadResult(title="測試影片 #1 ? 100%", filepath=str(special_file))

                client = TestClient(web_app.app)
                with (
                    patch.object(web_app.UniversalDownloaderEngine, "download", side_effect=fake_download),
                    client.websocket_connect("/ws") as ws,
                ):
                    ws.send_json({"url": "https://www.youtube.com/watch?v=abc123", "format": "best"})
                    msgs = []
                    while True:
                        m = ws.receive_json()
                        msgs.append(m)
                        if m["type"] == "finished":
                            break
                    dl_url = msgs[-1]["download_url"]
                    self.assertIn("%23", dl_url)
                    self.assertIn("%3F", dl_url)
                    self.assertIn("%25", dl_url)

                    # Two sequential GETs succeed under TTL cleanup (e.g. iOS Safari Range prefetch)
                    self.assertEqual(client.get(dl_url).status_code, 200)
                    self.assertEqual(client.get(dl_url).status_code, 200)
                    self.assertFalse(expired_file.exists())

                # Invalid URL rejected
                with client.websocket_connect("/ws") as ws:
                    ws.send_json({"url": "file:///etc/passwd", "format": "best"})
                    err = ws.receive_json()
                    self.assertEqual(err["type"], "error")

                # Cancel mid-download
                def slow_cancel(self, *args, **kwargs):
                    for _ in range(40):
                        if self.is_cancelled and self.is_cancelled():
                            raise DownloadCancelledError("任務已取消。")
                        time.sleep(0.02)
                    raise AssertionError("Download should have been cancelled")

                with (
                    patch.object(web_app.UniversalDownloaderEngine, "download", new=slow_cancel),
                    client.websocket_connect("/ws") as ws,
                ):
                    ws.send_json({"url": "https://www.youtube.com/watch?v=abc123", "format": "best"})
                    time.sleep(0.05)
                    ws.send_json({"action": "cancel"})
                    msgs = []
                    while True:
                        m = ws.receive_json()
                        msgs.append(m)
                        if m["type"] == "finished":
                            break
                    self.assertFalse(msgs[-1]["success"])
                    self.assertIn("中止", msgs[-1]["data"])

                # Auth enforcement
                os.environ["API_ACCESS_TOKEN"] = "token-123"
                self.assertEqual(client.get(dl_url).status_code, 401)
                self.assertEqual(client.get(dl_url + "?token=token-123").status_code, 200)

                removed = cleanup_expired_downloads(tmp_dir, ttl_seconds=600)
                self.assertEqual(removed, 0)
            finally:
                web_app.DOWNLOAD_DIR = orig_dir
                web_app.AUTO_CLEANUP = orig_cleanup
                web_app.FILE_TTL_SECONDS = orig_ttl
                if orig_token is None:
                    os.environ.pop("API_ACCESS_TOKEN", None)
                else:
                    os.environ["API_ACCESS_TOKEN"] = orig_token


if __name__ == "__main__":
    unittest.main()
