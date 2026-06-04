from __future__ import annotations

import unittest
from types import SimpleNamespace

from typer.testing import CliRunner

import xhs_poster.cli as cli
from xhs_poster.models import PublishRunResult
from xhs_poster.notify import NotifyEvent


class _Recorder:
    """记录被发送的事件，断言 cli 接线挂在了正确的收尾点。"""

    def __init__(self) -> None:
        self.events: list[NotifyEvent] = []

    def send(self, event: NotifyEvent) -> None:
        self.events.append(event)


class CliNotifyTests(unittest.TestCase):
    """FN2：cli 四命令收尾接 notifier；通知不得改变退出码。"""

    def setUp(self) -> None:
        self.runner = CliRunner()
        self.recorder = _Recorder()

    def _patch(self, name: str, value: object) -> None:
        original = getattr(cli, name)
        setattr(cli, name, value)
        self.addCleanup(setattr, cli, name, original)

    def _invoke(self, args: list[str]) -> int:
        self._patch("build_notifier", lambda _settings: self.recorder)
        return self.runner.invoke(cli.app, args).exit_code

    def _kinds(self) -> list[str]:
        return [event.kind for event in self.recorder.events]

    # --- 成功路径 ---

    def test_fetch_products_success_sends_stage_done(self) -> None:
        result = SimpleNamespace(
            success_count=3,
            failed_count=1,
            skipped_count=0,
            run_status="completed",
            progress_ref="products-state.json",
        )
        self._patch("run_fetch_products", lambda **_kwargs: result)
        self.assertEqual(self._invoke(["fetch-products"]), 0)
        self.assertEqual(self._kinds(), ["stage_done"])

    def test_generate_content_success_sends_stage_done(self) -> None:
        result = SimpleNamespace(
            contents={"p1": [object(), object()]},
            total_products=1,
            contents_path="contents.json",
        )
        self._patch("build_generate_content_outputs", lambda **_kwargs: result)
        self.assertEqual(self._invoke(["generate-content"]), 0)
        self.assertEqual(self._kinds(), ["stage_done"])

    def test_plan_publish_success_sends_stage_done(self) -> None:
        result = SimpleNamespace(count_selected=5, plan_path="publish-plan.json")
        self._patch("build_publish_plan", lambda **_kwargs: result)
        self.assertEqual(self._invoke(["plan-publish"]), 0)
        self.assertEqual(self._kinds(), ["stage_done"])

    def test_publish_success_sends_publish_summary(self) -> None:
        result = PublishRunResult(
            date="2026-06-04",
            mode="sequential",
            dedupe_scope="today",
            count_requested=3,
            count_selected=3,
            count_attempted=3,
            count_succeeded=2,
            count_failed=1,
        )
        self._patch("run_publish_plan", lambda **_kwargs: result)
        self.assertEqual(self._invoke(["publish"]), 0)
        self.assertEqual(self._kinds(), ["publish_summary"])

    # --- 失败路径：发 error，且退出码与未接通知时一致 ---

    def test_fetch_products_zero_success_sends_error_exit_one(self) -> None:
        result = SimpleNamespace(
            success_count=0,
            failed_count=2,
            skipped_count=1,
            run_status="failed",
            progress_ref="products-state.json",
        )
        self._patch("run_fetch_products", lambda **_kwargs: result)
        self.assertEqual(self._invoke(["fetch-products"]), 1)
        self.assertEqual(self._kinds(), ["error"])

    def test_generate_content_exception_sends_error_exit_one(self) -> None:
        def _boom(**_kwargs: object) -> object:
            raise RuntimeError("缺少 products.json")

        self._patch("build_generate_content_outputs", _boom)
        self.assertEqual(self._invoke(["generate-content"]), 1)
        self.assertEqual(self._kinds(), ["error"])

    def test_publish_all_failed_sends_summary_exit_one(self) -> None:
        result = PublishRunResult(
            date="2026-06-04",
            mode="sequential",
            dedupe_scope="today",
            count_requested=2,
            count_selected=2,
            count_attempted=2,
            count_succeeded=0,
            count_failed=2,
        )
        self._patch("run_publish_plan", lambda **_kwargs: result)
        # 全失败仍发批次摘要（卡片内计成败），退出码 1 不变。
        self.assertEqual(self._invoke(["publish"]), 1)
        self.assertEqual(self._kinds(), ["publish_summary"])


if __name__ == "__main__":
    unittest.main()
