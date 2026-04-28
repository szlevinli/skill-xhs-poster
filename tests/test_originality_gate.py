from __future__ import annotations

import json
import tempfile
import unittest
from typing import Any, cast
from datetime import date
from pathlib import Path

from xhs_poster.content_gen import _build_chat_request_payload, _extract_json_payload, generate_product_contents
from xhs_poster.config import Settings
from xhs_poster.models import (
    ContentDraft,
    HistoryStyleReference,
    HotNotesAnalysis,
    ProductImageFacts,
    ProductSummary,
)
from xhs_poster.originality import assert_publishable_originality, build_default_originality_check
from xhs_poster.phase3 import resolve_publish_inputs


class OriginalityGateTests(unittest.TestCase):
    def test_generated_template_drafts_include_publishable_originality_checks(self) -> None:
        result = generate_product_contents(
            ProductSummary(id="p1", name="珍珠发夹"),
            ProductImageFacts(
                product_id="p1",
                product_name="珍珠发夹",
                keywords=["发夹"],
                colors=["珍珠白"],
                style_keywords=["温柔"],
                confirmed_elements=["珍珠装饰"],
            ),
            HotNotesAnalysis(
                keyword="发夹",
                source="test",
                emoji_candidates=["✨"],
                scene_candidates=["通勤"],
            ),
            contents_per_product=2,
        )

        self.assertEqual(len(result.drafts), 2)
        for draft in result.drafts:
            self.assertIsNotNone(draft.originality_check)
            assert draft.originality_check is not None
            self.assertTrue(draft.originality_check.passed, draft.originality_check.rejection_reasons)
            self.assertEqual(draft.originality_check.core_input_type, "新案例")
            self.assertGreaterEqual(len(draft.originality_check.supporting_differences), 2)

    def test_originality_gate_rejects_draft_without_core_input(self) -> None:
        draft = ContentDraft(angle=1, angle_name="颜色颜值", title="标题", content="正文")
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="商品",
            core_input_type="",
            core_input_evidence="",
            product_fact_anchors=["奶油黄", "南瓜抓夹"],
            supporting_differences=["不同场景：测试", "不同观点/结论：测试"],
            grounding_terms=["奶油黄", "南瓜抓夹"],
        )

        with self.assertRaisesRegex(RuntimeError, "原创性闸门未通过"):
            assert_publishable_originality(draft)

    def test_originality_gate_rejects_high_similarity_to_history(self) -> None:
        draft = ContentDraft(
            angle=1,
            angle_name="颜色颜值",
            title="珍珠发夹很温柔",
            content="珍珠发夹很温柔，通勤随手一夹很提气质。",
        )
        ref = HistoryStyleReference(
            product_search_key="发夹",
            title="珍珠发夹很温柔",
            content="珍珠发夹很温柔，通勤随手一夹很提气质。",
            source_file="history.md",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="珍珠发夹",
            core_input_type="新案例",
            core_input_evidence="当前商品珍珠发夹的新案例",
            product_fact_anchors=["珍珠", "发夹"],
            supporting_differences=["不同场景：测试", "不同观点/结论：测试"],
            grounding_terms=["珍珠", "发夹"],
            history_style_refs=[ref],
        )

        self.assertFalse(draft.originality_check.passed)
        self.assertTrue(
            any(reason.startswith("high_similarity_to_history") for reason in draft.originality_check.rejection_reasons)
        )

    def test_grounding_terms_allow_publish_without_exact_product_name(self) -> None:
        draft = ContentDraft(
            angle=1,
            angle_name="颜色颜值",
            title="奶油黄南瓜夹让通勤发型更轻松",
            content="这次重点写奶油黄配色和南瓜夹轮廓，通勤时一夹就能完成造型。",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="新夏季奶油黄南瓜抓夹",
            core_input_type="新案例",
            core_input_evidence="这次围绕奶油黄和南瓜夹轮廓做新的通勤案例。",
            product_fact_anchors=["奶油黄", "南瓜夹"],
            supporting_differences=["不同场景：通勤", "不同决策问题：快速出门整理发型"],
            grounding_terms=["奶油黄", "南瓜夹", "通勤"],
        )

        assert draft.originality_check is not None
        self.assertTrue(draft.originality_check.passed, draft.originality_check.rejection_reasons)


    def test_kimi_request_payload_omits_temperature_and_disables_thinking(self) -> None:
        class DummySettings:
            llm_model = "kimi-k2.6"

        payload = _build_chat_request_payload(cast(Any, DummySettings()), {"task": "demo"})
        self.assertEqual(payload["model"], "kimi-k2.6")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("temperature", payload)

    def test_moonshot_request_payload_keeps_temperature(self) -> None:
        class DummySettings:
            llm_model = "moonshot-v1-8k"

        payload = _build_chat_request_payload(cast(Any, DummySettings()), {"task": "demo"})
        self.assertEqual(payload["model"], "moonshot-v1-8k")
        self.assertEqual(payload["temperature"], 0.8)
        self.assertNotIn("thinking", payload)


    def test_extract_json_payload_tolerates_trailing_commas(self) -> None:
        payload = _extract_json_payload("""```json
{"drafts": [{"title": "a", "content": "b",},],}
```""")
        self.assertIsInstance(payload, dict)
        payload_dict = cast(dict[str, Any], payload)
        self.assertEqual(payload_dict["drafts"][0]["title"], "a")


    def test_grounding_requires_two_fact_anchors_for_generic_copy(self) -> None:
        draft = ContentDraft(
            angle=2,
            angle_name="材质质感",
            title="这个抓夹夹得很稳",
            content="我发量偏多，戴一下午也不会往下掉，整体挺实用。",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="奶油黄南瓜抓夹",
            core_input_type="新实测",
            core_input_evidence="只写了发量和实用，没有写到当前商品锚点。",
            product_fact_anchors=["奶油黄"],
            supporting_differences=["不同场景：逛街"],
            grounding_terms=["奶油黄", "南瓜夹", "波浪夹齿"],
        )
        assert draft.originality_check is not None
        self.assertFalse(draft.originality_check.passed)
        self.assertIn("missing_two_supporting_differences", draft.originality_check.rejection_reasons)


    def test_history_template_reuse_is_primary_rejection(self) -> None:
        draft = ContentDraft(
            angle=1,
            angle_name="颜色颜值",
            title="灰棕色抓夹真的比黑色耐看",
            content="灰棕色抓夹配燕麦色毛衣很好看，通勤戴一整天都不突兀。",
        )
        ref = HistoryStyleReference(
            product_search_key="抓夹",
            title="灰棕色抓夹真的比黑色耐看",
            content="灰棕色抓夹配燕麦色毛衣很好看，通勤戴一整天都不突兀。",
            source_file="history.md",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="灰棕色南瓜抓夹",
            core_input_type="新个人经验",
            core_input_evidence="结合灰棕色和通勤场景的个人经验。",
            product_fact_anchors=["灰棕色", "南瓜夹"],
            supporting_differences=["不同场景：通勤", "不同决策问题：替代黑色发夹"],
            grounding_terms=["灰棕色", "南瓜夹"],
            history_style_refs=[ref],
        )
        assert draft.originality_check is not None
        self.assertFalse(draft.originality_check.passed)
        self.assertTrue(any(reason.startswith("high_similarity_to_history") or reason.startswith("template_like_history") for reason in draft.originality_check.rejection_reasons))


    def test_grounding_allows_dense_product_fact_copy(self) -> None:
        draft = ContentDraft(
            angle=2,
            angle_name="材质质感",
            title="醋酸南瓜夹的厚度比我预期更扎实",
            content="这只南瓜夹是半透明醋酸感，夹齿间距偏密，弹簧回弹很稳。",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="奶油黄南瓜抓夹",
            core_input_type="新实测",
            core_input_evidence="实测里重点记录了南瓜夹轮廓、醋酸感和夹齿间距。",
            product_fact_anchors=["南瓜夹", "醋酸", "夹齿间距"],
            supporting_differences=["不同场景：通勤", "不同观点/结论：强调材质和结构而非泛好看"],
            grounding_terms=["南瓜夹", "醋酸", "夹齿间距", "半透明"],
        )
        assert draft.originality_check is not None
        self.assertTrue(draft.originality_check.passed, draft.originality_check.rejection_reasons)


    def test_anchors_are_recorded_but_not_blocking_when_structure_is_good(self) -> None:
        draft = ContentDraft(
            angle=1,
            angle_name="颜色颜值",
            title="灰棕色抓夹真的比黑色耐看",
            content="灰棕色抓夹配燕麦色毛衣很好看，通勤戴一整天都不突兀。",
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name="灰棕色南瓜抓夹",
            core_input_type="新个人经验",
            core_input_evidence="结合灰棕色和通勤场景的个人经验。",
            product_fact_anchors=["灰棕色"],
            supporting_differences=["不同场景：通勤", "不同决策问题：替代黑色发夹"],
            grounding_terms=["灰棕色", "南瓜夹"],
        )
        assert draft.originality_check is not None
        self.assertTrue(draft.originality_check.passed, draft.originality_check.rejection_reasons)


    def test_phase3_rejects_contents_without_originality_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp)
            data_dir = project_root / "xiaohongshu-data"
            product_dir = data_dir / "images" / "product-1"
            product_dir.mkdir(parents=True)
            image_path = product_dir / "001.jpg"
            image_path.write_bytes(b"image")
            today = date.today().isoformat()
            (data_dir / "today-pool.json").write_text(
                json.dumps(
                    {
                        "date": today,
                        "products": [{"id": "product-1", "name": "测试商品"}],
                        "images": {"product-1": [str(image_path)]},
                        "failed_products": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (data_dir / "contents.json").write_text(
                json.dumps(
                    {
                        "date": today,
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
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "原创性闸门未填写"):
                resolve_publish_inputs(
                    Settings(project_root=project_root),
                    "product-1",
                    publish_date=today,
                    title=None,
                    content=None,
                    topic_keywords=None,
                    angle=1,
                )


if __name__ == "__main__":
    unittest.main()
