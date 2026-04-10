from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from xhs_poster.config import Settings
from xhs_poster.phase3 import load_contents_bundle, resolve_image_paths, resolve_publish_inputs


class Phase3ImageFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.data_dir = self.project_root / "xiaohongshu-data"
        self.images_dir = self.data_dir / "images" / "product-1"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.today = date.today().isoformat()

        self.asset_path = self.images_dir / "001.jpg"
        self.asset_path.write_bytes(b"asset")
        self.fallback_path = self.images_dir / "002.jpg"
        self.fallback_path.write_bytes(b"fallback")

        (self.data_dir / "today-pool.json").write_text(
            json.dumps(
                {
                    "date": self.today,
                    "status": "complete",
                    "generated_at": self.today,
                    "products": [{"id": "product-1", "name": "测试商品"}],
                    "images": {"product-1": [str(self.fallback_path)]},
                    "image_assets": {
                        "product-1": [
                            {
                                "image_id": "asset-1",
                                "path": str(self.asset_path),
                                "source_url": "https://img/main.jpg",
                                "normalized_url": "https://img/main.jpg",
                                "source_type": "main",
                                "source_priority": 0,
                                "position": 1,
                                "bytes": 5,
                                "format": "jpg",
                                "width": 10,
                                "height": 10,
                                "sha256": "hash-1",
                            }
                        ]
                    },
                    "failed_products": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _settings(self) -> Settings:
        return Settings(project_root=self.project_root)

    def test_resolve_image_paths_prefers_image_assets_over_legacy_images(self) -> None:
        settings = self._settings()
        today_pool = json.loads((self.data_dir / "today-pool.json").read_text(encoding="utf-8"))
        from xhs_poster.models import TodayPool

        resolved = resolve_image_paths(settings, TodayPool.model_validate(today_pool), "product-1")
        self.assertEqual(resolved, [str(self.asset_path)])

    def test_run_inputs_fall_back_to_today_pool_when_draft_has_no_selected_images(self) -> None:
        settings = self._settings()
        (self.data_dir / "contents.json").write_text(
            json.dumps(
                {
                    "date": self.today,
                    "total_products": 1,
                    "contents_per_product": 1,
                    "contents": {
                        "product-1": [
                            {
                                "angle": 1,
                                "angle_name": "颜色颜值",
                                "title": "标题",
                                "content": "正文",
                                "tags": "#测试",
                                "reference_notes": [],
                            }
                        ]
                    },
                    "generation": {},
                    "statuses": {},
                    "warnings": {},
                    "input_refs": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        from xhs_poster.models import TodayPool

        bundle = load_contents_bundle(settings, expected_date=self.today)
        title, content, topics, draft = resolve_publish_inputs(
            settings,
            "product-1",
            publish_date=self.today,
            title=None,
            content=None,
            topic_keywords=None,
            angle=1,
        )
        self.assertEqual(title, "标题")
        self.assertEqual(content, "正文")
        self.assertEqual(topics, ["测试"])
        self.assertIsNotNone(draft)
        assert draft is not None
        self.assertEqual(draft.selected_image_paths, [])

        today_pool = TodayPool.model_validate_json((self.data_dir / "today-pool.json").read_text(encoding="utf-8"))
        resolved = resolve_image_paths(
            settings,
            today_pool,
            "product-1",
            image_paths=draft.selected_image_paths or None,
        )
        self.assertEqual(resolved, [str(self.asset_path)])
        self.assertEqual(bundle.contents["product-1"][0].title, "标题")


if __name__ == "__main__":
    unittest.main()
