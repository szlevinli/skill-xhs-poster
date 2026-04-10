from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xhs_poster.image_pipeline import (
    ImageCandidate,
    dedupe_candidates,
    dedupe_downloaded_images,
    normalize_image_url,
)
from xhs_poster.models import DownloadedImage


class TestImagePipeline(unittest.TestCase):
    def test_normalize_image_url_prefers_original_image(self) -> None:
        self.assertEqual(
            normalize_image_url("//ci.xiaohongshu.com/abc.jpg?imageView2=2&w=1080"),
            "https://ci.xiaohongshu.com/abc.jpg",
        )
        self.assertEqual(
            normalize_image_url("https://ci.xiaohongshu.com/abc.jpg@!small"),
            "https://ci.xiaohongshu.com/abc.jpg",
        )

    def test_dedupe_candidates_prefers_main_images(self) -> None:
        candidates = [
            ImageCandidate(
                source_url="https://ci.xiaohongshu.com/dup.jpg?imageView2=2",
                normalized_url="https://ci.xiaohongshu.com/dup.jpg",
                source_type="detail",
                source_priority=1,
                position=2,
            ),
            ImageCandidate(
                source_url="https://ci.xiaohongshu.com/dup.jpg",
                normalized_url="https://ci.xiaohongshu.com/dup.jpg",
                source_type="main",
                source_priority=0,
                position=1,
            ),
        ]

        deduped = dedupe_candidates(candidates)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_type, "main")

    def test_dedupe_downloaded_images_renumbers_and_keeps_higher_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            detail_path = tmp_path / "detail.jpg"
            main_path = tmp_path / "main.jpg"
            detail_path.write_bytes(b"same")
            main_path.write_bytes(b"same")

            images = [
                DownloadedImage(
                    index=2,
                    image_id="detail",
                    path=str(detail_path),
                    source_url="https://example.com/detail.jpg",
                    normalized_url="https://example.com/detail.jpg",
                    source_type="detail",
                    source_priority=1,
                    position=2,
                    bytes=10,
                    format="jpeg",
                    width=100,
                    height=100,
                    sha256="same-sha",
                ),
                DownloadedImage(
                    index=1,
                    image_id="main",
                    path=str(main_path),
                    source_url="https://example.com/main.jpg",
                    normalized_url="https://example.com/main.jpg",
                    source_type="main",
                    source_priority=0,
                    position=1,
                    bytes=10,
                    format="jpeg",
                    width=100,
                    height=100,
                    sha256="same-sha",
                ),
            ]

            deduped = dedupe_downloaded_images(images)

            self.assertEqual(len(deduped), 1)
            self.assertEqual(deduped[0].source_type, "main")
            self.assertEqual(deduped[0].index, 1)
            self.assertFalse(detail_path.exists())
            self.assertTrue(main_path.exists())
