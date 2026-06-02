from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import xhs_poster.publish.session as session_mod
from xhs_poster.config import Settings
from xhs_poster.models import (
    PublishExecutionResult,
    PublishPlanItem,
    PublishPlanResult,
)
from xhs_poster.publish.session import _publish_interval_seconds, run_publish_plan


def _settings(*, low: float, high: float) -> Settings:
    settings = Settings()
    settings.publish_interval_min_seconds = low
    settings.publish_interval_max_seconds = high
    return settings


class PublishIntervalSecondsTests(unittest.TestCase):
    """_publish_interval_seconds：边界、关闭、clamp、随机区间。"""

    def test_disabled_when_both_non_positive(self) -> None:
        self.assertEqual(_publish_interval_seconds(_settings(low=0, high=0)), 0.0)
        self.assertEqual(_publish_interval_seconds(_settings(low=-5, high=-1)), 0.0)

    def test_random_within_range(self) -> None:
        settings = _settings(low=30, high=90)
        with mock.patch.object(session_mod.random, "uniform", return_value=42.0) as uniform:
            value = _publish_interval_seconds(settings)
        uniform.assert_called_once_with(30.0, 90.0)
        self.assertEqual(value, 42.0)

    def test_min_greater_than_max_clamps_to_min(self) -> None:
        settings = _settings(low=60, high=10)
        with mock.patch.object(session_mod.random, "uniform", return_value=60.0) as uniform:
            _publish_interval_seconds(settings)
        # high clamps up to low → 区间退化为 [60, 60]
        uniform.assert_called_once_with(60.0, 60.0)

    def test_positive_min_negative_max_clamps(self) -> None:
        settings = _settings(low=30, high=-1)
        with mock.patch.object(session_mod.random, "uniform", return_value=30.0) as uniform:
            _publish_interval_seconds(settings)
        uniform.assert_called_once_with(30.0, 30.0)


class _FakeSession:
    """替身 PublishSession：不开浏览器，publish_one 直接返回成功结果。"""

    def __init__(self, settings: Settings, *, headless: bool | None = None) -> None:
        self.settings = settings

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def maybe_recycle(self) -> None:
        return None

    def publish_one(self, *, product_id: str, angle: int) -> PublishExecutionResult:
        return PublishExecutionResult(
            product_id=product_id,
            product_name=product_id,
            title="t",
            content="c",
            title_selector="#title",
            content_selector="#content",
            publish_result={"success": True},
        )


def _plan(item_count: int) -> PublishPlanResult:
    today = datetime.now().date().isoformat()
    items = [
        PublishPlanItem(
            sequence=i,
            product_id=f"p{i}",
            product_name=f"p{i}",
            angle=0,
            angle_name="a",
            title="t",
            selection_reason="r",
            status="pending",
        )
        for i in range(item_count)
    ]
    return PublishPlanResult(
        date=today,
        mode="sequential",
        dedupe_scope="today",
        count_requested=item_count,
        count_selected=item_count,
        items=items,
    )


class PublishIntervalOrchestrationTests(unittest.TestCase):
    """run_publish_plan：N 篇 pending → 间隔被调用 N-1 次（首篇不等）。"""

    def _run(self, item_count: int) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings()
            settings.project_root = Path(tmp)
            plan = _plan(item_count)
            with (
                mock.patch.object(session_mod, "PublishSession", _FakeSession),
                mock.patch.object(session_mod, "load_publish_plan", return_value=plan),
                mock.patch.object(
                    session_mod, "reconcile_publish_plan_with_records", lambda _s, p: p
                ),
                mock.patch.object(session_mod, "save_publish_plan", lambda *_a, **_k: None),
                mock.patch.object(session_mod, "_sleep_interval") as sleep_interval,
            ):
                run_publish_plan(mode="sequential", count=item_count, settings=settings)
            return sleep_interval.call_count

    def test_three_pending_sleeps_twice(self) -> None:
        self.assertEqual(self._run(3), 2)

    def test_single_pending_never_sleeps(self) -> None:
        self.assertEqual(self._run(1), 0)


if __name__ == "__main__":
    unittest.main()
