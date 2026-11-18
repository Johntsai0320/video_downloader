"""Comprehensive test suite across major social media platforms.

Covers both deterministic offline tests (extractor matching, URL normalization,
platform headers, fallback cascades, Meta HTML/redirect parsing) and optional
live-network smoke tests gated behind `RUN_LIVE_TESTS=1`.
"""

from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yt_dlp
from yt_dlp.extractor import gen_extractors

from src.core.downloader import (
    UniversalDownloaderEngine,
    fetch_meta_direct_video_url,
    media_id_to_shortcode,
    normalize_url,
    resolve_meta_redirect,
)

_NON_GENERIC_EXTRACTORS = [e for e in gen_extractors() if e.IE_NAME != "generic"]


def _matching_extractors(url: str) -> list[str]:
    return [e.IE_NAME for e in _NON_GENERIC_EXTRACTORS if e.suitable(url)]


class _FakeHTTPResponse:
    def __init__(self, final_url: str, body: str):
        self._final_url = final_url
        self._buf = io.BytesIO(body.encode("utf-8"))

    def geturl(self) -> str:
        return self._final_url

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class SocialPlatformMatrixTests(unittest.TestCase):
    """Offline deterministic verification for YouTube, Bilibili, IG, Threads, X, TikTok, Facebook."""

    def test_yt_dlp_extractor_suitability_matrix(self) -> None:
        cases = {
            "youtube_watch": ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "youtube"),
            "youtube_shorts": ("https://www.youtube.com/shorts/aqz-KE-bpKQ", "youtube"),
            "youtube_live": ("https://www.youtube.com/live/aqz-KE-bpKQ", "youtube"),
            "youtube_short_domain": ("https://youtu.be/aqz-KE-bpKQ", "youtube"),
            "youtube_playlist": (
                "https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj",
                "youtube:tab",
            ),
            "bilibili_video": ("https://www.bilibili.com/video/BV1xx411c7mD", "BiliBili"),
            "instagram_post": ("https://www.instagram.com/p/BQ0eAlwhDrw/", "Instagram"),
            "instagram_reel": ("https://www.instagram.com/reel/Chunk8-jurw/", "Instagram"),
            "x_status": ("https://x.com/starwars/status/665052190608723968", "twitter"),
            "twitter_status": ("https://twitter.com/starwars/status/665052190608723968", "twitter"),
            "t_co_short": ("https://t.co/abc1234", "twitter:shortener"),
            "tiktok_video": ("https://www.tiktok.com/@tiktok/video/7106594312292453675", "TikTok"),
            "tiktok_vm": ("https://vm.tiktok.com/ZM8123456/", "vm.tiktok"),
            "tiktok_vt": ("https://vt.tiktok.com/ZS8123456/", "vm.tiktok"),
            "facebook_watch": ("https://www.facebook.com/watch/?v=10153231379946729", "facebook"),
            "facebook_reel": ("https://www.facebook.com/reel/1234567890", "facebook:reel"),
        }
        for label, (url, expected_ie) in cases.items():
            matched = _matching_extractors(url)
            self.assertIn(expected_ie, matched, f"{label} ({url}) got {matched}")

    def test_tiktok_curl_cffi_available(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("curl_cffi"),
            "curl_cffi must be installed for TikTok TLS impersonation",
        )

    def test_x_proxy_domain_rewriting(self) -> None:
        proxies = [
            "https://fxtwitter.com/NASA/status/12345",
            "https://d.fxtwitter.com/NASA/status/12345",
            "https://www.vxtwitter.com/NASA/status/12345?s=20",
            "https://fixupx.com/NASA/status/12345",
            "https://twittpr.com/NASA/status/12345",
            "https://girlcockx.com/NASA/status/12345",
        ]
        for raw in proxies:
            norm, rewritten = normalize_url(raw)
            self.assertTrue(rewritten, raw)
            self.assertTrue(norm.startswith("https://x.com/NASA/status/12345"), norm)
            self.assertIn("twitter", _matching_extractors(norm))

        # Native domains stay untouched
        for native in (
            "https://x.com/NASA/status/12345",
            "https://twitter.com/NASA/status/12345",
        ):
            norm, rewritten = normalize_url(native)
            self.assertFalse(rewritten)
            self.assertEqual(norm, native)

    def test_bilibili_headers_and_412_handling(self) -> None:
        engine = UniversalDownloaderEngine()
        for url in ("https://www.bilibili.com/video/BV1xx411c7mD", "https://b23.tv/abc1234"):
            opts = engine.build_ydl_opts(url, "/tmp/%(title)s.%(ext)s", "best")
            self.assertEqual(opts["http_headers"]["Referer"], "https://www.bilibili.com/")
            self.assertEqual(opts["http_headers"]["Origin"], "https://www.bilibili.com")

        # Without cookies, HTTP 412 produces actionable guidance
        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.core.downloader.yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError(
                    "HTTP Error 412: Precondition Failed"
                )
                with self.assertRaises(RuntimeError) as ctx:
                    engine.download("https://www.bilibili.com/video/BV1xx411c7mD", tmp)
                self.assertIn("412", str(ctx.exception))
                self.assertIn("SESSDATA", str(ctx.exception))

            # With cookies, retries once and succeeds
            cookie_file = Path(tmp) / "bili.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            out_file = Path(tmp) / "BiliVideo [BV1xx411c7mD].mp4"
            out_file.write_bytes(b"MP4")

            calls = {"count": 0}

            def side_effect(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RuntimeError("HTTP Error 412: Precondition Failed")
                return {
                    "title": "BiliVideo",
                    "requested_downloads": [{"filepath": str(out_file)}],
                }

            with patch("src.core.downloader.yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.extract_info.side_effect = side_effect
                res = engine.download(
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    tmp,
                    cookie_file=str(cookie_file),
                )
                self.assertEqual(res.title, "BiliVideo")
                self.assertEqual(calls["count"], 2)

    def test_threads_redirect_media_id_and_cdn_sniffing(self) -> None:
        # 1. injected_media_ids -> Instagram shortcode
        mid = 3456789012345678901
        expected_code = media_id_to_shortcode(mid)
        fake_redirect = f"https://www.threads.com/login?injected_media_ids=%5B%22{mid}%22%5D"
        with patch(
            "src.core.downloader.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(fake_redirect, "<html></html>"),
        ):
            resolved_url, code = resolve_meta_redirect("https://www.threads.com/share/XYZ")
            self.assertEqual(code, expected_code)
            self.assertEqual(resolved_url, f"https://www.instagram.com/p/{expected_code}/")

        # 2. og:url reverse attribute order
        html_og = '<meta content="https://www.threads.com/@user/post/AbCdEf123" property="og:url">'
        with patch(
            "src.core.downloader.urllib.request.urlopen",
            return_value=_FakeHTTPResponse("https://www.threads.com/", html_og),
        ):
            resolved_url, code = resolve_meta_redirect("https://www.threads.net/share/ABC")
            self.assertEqual(code, "AbCdEf123")
            self.assertEqual(resolved_url, "https://www.threads.com/t/AbCdEf123")

        # 3. CDN sniffing rejects DASH BaseURL/Segment/mpd and picks clean progressive MP4
        dirty_then_clean = (
            '"video_url":"https:\\/\\/scontent.cdninstagram.com\\/v\\/dash_BaseURL.mp4",'
            '"video_url":"https:\\/\\/scontent.cdninstagram.com\\/v\\/t50.2886-16\\/clean_prog.mp4\\u0026oe=123"'
        )
        with patch(
            "src.core.downloader.urllib.request.urlopen",
            return_value=_FakeHTTPResponse(
                "https://www.instagram.com/p/AbCdEf123/embed/captioned/", dirty_then_clean
            ),
        ):
            cdn_url = fetch_meta_direct_video_url("AbCdEf123")
            self.assertEqual(
                cdn_url,
                "https://scontent.cdninstagram.com/v/t50.2886-16/clean_prog.mp4&oe=123",
            )

    def test_threads_and_instagram_multi_stage_rescue_cascade(self) -> None:
        engine = UniversalDownloaderEngine()
        with tempfile.TemporaryDirectory() as tmp:
            cdn_file = Path(tmp) / "Threads_AbCd123.mp4"
            cdn_file.write_bytes(b"MP4")

            seen_urls: list[str] = []

            def fake_extract(url_target: str, download: bool = True):
                seen_urls.append(url_target)
                if "cdninstagram.com" in url_target:
                    return {
                        "title": "Threads_AbCd123",
                        "requested_downloads": [{"filepath": str(cdn_file)}],
                    }
                raise RuntimeError("Login required")

            with (
                patch("src.core.downloader.yt_dlp.YoutubeDL") as mock_ydl_cls,
                patch(
                    "src.core.downloader.fetch_meta_direct_video_url",
                    return_value="https://scontent.cdninstagram.com/v/video.mp4",
                ),
            ):
                mock_ydl_cls.return_value.__enter__.return_value.extract_info.side_effect = fake_extract
                res = engine.download("https://www.threads.net/@user/post/AbCd123?xmt=foo", tmp)
                self.assertEqual(res.mode_note, "直連通道")
                self.assertEqual(res.filepath, str(cdn_file))
                self.assertEqual(seen_urls[0], "https://www.threads.com/@user/post/AbCd123")
                self.assertEqual(seen_urls[1], "https://www.instagram.com/p/AbCd123/")
                self.assertIn("cdninstagram.com", seen_urls[2])

    def test_facebook_and_instagram_opts(self) -> None:
        engine = UniversalDownloaderEngine()
        ig_opts = engine.build_ydl_opts("https://www.instagram.com/reel/Chunk8-jurw/", "/tmp/x.mp4", "best")
        self.assertEqual(ig_opts["extractor_args"], {"instagram": {"api": ["mobile"]}})

        cdn_opts = engine.build_ydl_opts(
            "https://scontent.cdninstagram.com/v/clip.mp4",
            "/tmp/x.mp4",
            "best",
            is_direct_cdn=True,
        )
        self.assertEqual(cdn_opts["http_headers"]["Referer"], "https://www.instagram.com/")
        self.assertNotIn("extractor_args", cdn_opts)


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "Set RUN_LIVE_TESTS=1 to run live network tests against public social platform URLs.",
)
class LivePlatformTests(unittest.TestCase):
    """Live network smoke tests against public social platform endpoints."""

    def test_live_youtube_extract_and_real_download(self) -> None:
        engine = UniversalDownloaderEngine()
        with tempfile.TemporaryDirectory() as tmp:
            # Classic 19s public YouTube clip ("Me at the zoo") for fast real download + FFmpeg merge
            res = engine.download(
                "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                tmp,
                quality_option="MP4：720p 以下",
            )
            self.assertIsNotNone(res.filepath)
            self.assertTrue(os.path.exists(res.filepath), f"Downloaded file missing: {res.filepath}")
            self.assertGreater(os.path.getsize(res.filepath), 50_000)

    def test_live_youtube_shorts_extract(self) -> None:
        engine = UniversalDownloaderEngine()
        opts = engine.build_ydl_opts("https://www.youtube.com/shorts/aqz-KE-bpKQ", "/tmp/%(id)s.%(ext)s", "best")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/shorts/aqz-KE-bpKQ", download=False)
            self.assertTrue(info and len(info.get("formats") or []) > 0)

    def test_live_tiktok_extract(self) -> None:
        engine = UniversalDownloaderEngine()
        url = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
        opts = engine.build_ydl_opts(url, "/tmp/%(id)s.%(ext)s", "best")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            self.assertTrue(info and len(info.get("formats") or []) > 0)

    def test_live_x_twitter_extract(self) -> None:
        engine = UniversalDownloaderEngine()
        norm_url, rewritten = normalize_url("https://fxtwitter.com/captainamerica/status/719944021058060289")
        self.assertTrue(rewritten)
        opts = engine.build_ydl_opts(norm_url, "/tmp/%(id)s.%(ext)s", "best")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(norm_url, download=False)
            self.assertTrue(info and len(info.get("formats") or []) > 0)

    def test_live_facebook_watch_extract(self) -> None:
        engine = UniversalDownloaderEngine()
        url = "https://www.facebook.com/watch/?v=10153231379946729"
        opts = engine.build_ydl_opts(url, "/tmp/%(id)s.%(ext)s", "best")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            self.assertTrue(info and len(info.get("formats") or []) > 0)

    def test_live_instagram_reel_and_embed_sniff(self) -> None:
        engine = UniversalDownloaderEngine()
        url = "https://www.instagram.com/reel/Chunk8-jurw/"
        opts = engine.build_ydl_opts(url, "/tmp/%(id)s.%(ext)s", "best")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            self.assertTrue(info and len(info.get("formats") or []) > 0)
        cdn = fetch_meta_direct_video_url("Chunk8-jurw")
        self.assertTrue(cdn and "cdninstagram.com" in cdn)

    def test_live_bilibili_guest_or_412_guidance(self) -> None:
        engine = UniversalDownloaderEngine()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                opts = engine.build_ydl_opts(
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    os.path.join(tmp, "%(id)s.%(ext)s"),
                    "best",
                )
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info("https://www.bilibili.com/video/BV1xx411c7mD", download=False)
                self.assertTrue(info and info.get("title"))
            except Exception as exc:
                # Cloud datacenter IPs typically receive HTTP 412 from Bilibili without SESSDATA
                self.assertIn("412", str(exc))


if __name__ == "__main__":
    unittest.main()
