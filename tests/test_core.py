"""Unit and integration tests for Universal Video Hub (compatible with unittest & pytest)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import BackgroundTasks

from src.core.cookies import create_sanitized_meta_cookies
from src.core.downloader import (
    UniversalDownloaderEngine,
    media_id_to_shortcode,
    normalize_url,
)
from src.core.ffmpeg_helper import find_ffmpeg
from src.web.app import DOWNLOAD_DIR, get_index, health_check, serve_file


class CoreTests(unittest.TestCase):
    def test_media_id_to_shortcode(self) -> None:
        self.assertEqual(media_id_to_shortcode(0), "")
        code = media_id_to_shortcode(3986797102625621582)
        self.assertTrue(len(code) > 0)
        self.assertTrue(all(ch.isalnum() or ch in "-_" for ch in code))

    def test_normalize_url(self) -> None:
        u1, rewritten1 = normalize_url("https://fxtwitter.com/user/status/123")
        self.assertEqual(u1, "https://x.com/user/status/123")
        self.assertTrue(rewritten1)

        u2, rewritten2 = normalize_url("https://www.threads.com/@user/post/AbCd123?xmt=foo")
        self.assertEqual(u2, "https://www.threads.net/@user/post/AbCd123")
        self.assertFalse(rewritten2)

    def test_create_sanitized_meta_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            src = Path(tmp_dir) / "mixed_cookies.txt"
            src.write_text(
                "\n".join(
                    [
                        "# Netscape HTTP Cookie File",
                        ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR\tabc",
                        ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\txyz",
                        ".threads.net\tTRUE\t/\tTRUE\t0\tcsrftoken\t123",
                    ]
                ),
                encoding="utf-8",
            )

            active_path, temp_path = create_sanitized_meta_cookies(str(src))
            try:
                self.assertIsNotNone(temp_path)
                self.assertEqual(active_path, temp_path)
                content = Path(active_path).read_text(encoding="utf-8")
                self.assertIn("instagram.com", content)
                self.assertIn("threads.net", content)
                self.assertNotIn("youtube.com", content)
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    def test_build_ydl_opts_presets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = UniversalDownloaderEngine()
            out = os.path.join(tmp_dir, "%(title)s.%(ext)s")

            best_opts = engine.build_ydl_opts("https://www.youtube.com/shorts/abc", out, "best")
            self.assertEqual(best_opts["merge_output_format"], "mp4")
            self.assertTrue(best_opts["format"].startswith("bv*[ext=mp4]+ba[ext=m4a]"))
            self.assertTrue(best_opts["noplaylist"])

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

            audio_opts = engine.build_ydl_opts("https://www.youtube.com/watch?v=1", out, "audio")
            self.assertEqual(audio_opts["postprocessors"][0]["preferredcodec"], "mp3")

    def test_ffmpeg_detected(self) -> None:
        ffmpeg_path = find_ffmpeg()
        self.assertIsNotNone(ffmpeg_path)
        self.assertTrue(os.path.isfile(ffmpeg_path))

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


if __name__ == "__main__":
    unittest.main()
