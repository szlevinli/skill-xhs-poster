from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import xhs_poster.publish.plan as plan_mod
import xhs_poster.publish.session as session_mod
from xhs_poster.auth import LoginRequiredError
from xhs_poster.config import Settings
from xhs_poster.models import (
    PublishDailyRecords,
    PublishExecutionResult,
    PublishPlanItem,
    PublishPlanResult,
    PublishRecord,
    SessionInfo,
)


def _session_info() -> SessionInfo:
    return SessionInfo(
        site="merchant",
        status="authenticated",
        authenticated=True,
        auth_source="auth_state",
        browser_mode="headless",
        checked_url="https://ark.xiaohongshu.com/app-item/list",
        profile_dir="/tmp/profile",
        home_url="https://ark.xiaohongshu.com",
        message="ok",
    )


class _FakeSession:
    """替身 PublishSession：不开浏览器。可配置某些 product_id 抛异常、是否掉登录、列表页是否健康。

    类级计数器记录关键自愈/发布动作的调用次数，供断言。
    """

    publish_calls: list[str] = []
    open_context_calls = 0

    def __init__(
        self,
        settings: Settings,
        *,
        headless: bool | None = None,
        verbose: bool = False,
    ) -> None:
        self.settings = settings
        self.session_info = _session_info()
        # 由测试在 patch 后覆盖以下钩子
        self.raise_on: set[str] = set()
        self.login_lost_after: str | None = None
        self.unhealthy_after: str | None = None
        self._logged_out = False
        self._unhealthy = False

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def ensure_ready_for_next(self) -> None:
        if self._unhealthy:
            type(self).open_context_calls += 1
            self._unhealthy = False

    def detect_login_lost_bounded(self) -> bool:
        return self._logged_out

    def login_lost_error(self) -> LoginRequiredError:
        return LoginRequiredError(self.session_info)

    def publish_one(self, *, product_id: str, angle: int) -> PublishExecutionResult:
        type(self).publish_calls.append(product_id)
        if product_id in self.raise_on:
            if self.login_lost_after == product_id:
                self._logged_out = True
            if self.unhealthy_after == product_id:
                self._unhealthy = True
            # 真实的 publish_one 失败时已自行记账+留证，统一抛 PublishItemError。
            raise session_mod.PublishItemError(f"boom {product_id}")
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


def _make_fake_factory(**config):
    def factory(settings, *, headless=None, verbose=False):
        session = _FakeSession(settings, headless=headless, verbose=verbose)
        for key, value in config.items():
            setattr(session, key, value)
        return session

    return factory


class RunPublishPlanRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSession.publish_calls = []
        _FakeSession.open_context_calls = 0

    def _run(self, plan: PublishPlanResult, factory, *, count: int):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings()
            settings.project_root = Path(tmp)
            with (
                mock.patch.object(session_mod, "PublishSession", factory),
                mock.patch.object(session_mod, "load_publish_plan", return_value=plan),
                mock.patch.object(
                    session_mod, "reconcile_publish_plan_with_records", lambda _s, p: p
                ),
                mock.patch.object(session_mod, "save_publish_plan", lambda *_a, **_k: None),
                mock.patch.object(session_mod, "append_record", lambda *_a, **_k: "log"),
                mock.patch.object(session_mod, "_sleep_interval", lambda *_a, **_k: None),
            ):
                return session_mod.run_publish_plan(
                    mode="sequential", count=count, settings=settings
                )

    def test_login_lost_aborts_whole_batch(self) -> None:
        """第 2 篇触发掉登录 → 整批中止：第 3 篇不再尝试，冒泡 LoginRequiredError。"""
        plan = _plan(3)
        factory = _make_fake_factory(raise_on={"p1"}, login_lost_after="p1")
        with self.assertRaises(LoginRequiredError):
            self._run(plan, factory, count=3)
        # p0 成功、p1 触发掉登录、p2 不再尝试
        self.assertEqual(_FakeSession.publish_calls, ["p0", "p1"])
        # 已成功篇保持 published（续传可恢复）
        self.assertEqual(plan.items[0].status, "published")

    def test_normal_failure_does_not_abort(self) -> None:
        """第 2 篇普通失败（非掉登录）→ 第 3 篇照常尝试，退出码语义按 ≥1 成功。"""
        plan = _plan(3)
        factory = _make_fake_factory(raise_on={"p1"})
        result = self._run(plan, factory, count=3)
        self.assertEqual(_FakeSession.publish_calls, ["p0", "p1", "p2"])
        self.assertEqual(result.count_attempted, 3)
        self.assertEqual(result.count_succeeded, 2)
        self.assertEqual(result.count_failed, 1)

    def test_page_self_heal_rebuilds_context(self) -> None:
        """一篇失败使列表页不健康 → 进下一篇前触发一次 context 重建（open_context+1）。"""
        plan = _plan(3)
        factory = _make_fake_factory(raise_on={"p1"}, unhealthy_after="p1")
        self._run(plan, factory, count=3)
        self.assertEqual(_FakeSession.publish_calls, ["p0", "p1", "p2"])
        self.assertEqual(_FakeSession.open_context_calls, 1)


class ReconcileRobustnessTests(unittest.TestCase):
    """reconcile_publish_plan_with_records 续传对账：成功优先、不重发已成功篇。"""

    def _reconcile(self, plan: PublishPlanResult, records: list[PublishRecord]):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings()
            settings.project_root = Path(tmp)
            daily = PublishDailyRecords(date=plan.date, records=records)
            with (
                mock.patch.object(plan_mod, "load_daily_records", return_value=daily),
                mock.patch.object(plan_mod, "save_publish_plan", lambda *_a, **_k: None),
            ):
                return plan_mod.reconcile_publish_plan_with_records(settings, plan)

    def _record(self, product_id: str, *, status: str, attempted_at: str, error=None):
        return PublishRecord(
            attempted_at=attempted_at,
            product_id=product_id,
            product_name=product_id,
            angle=0,
            title="t",
            status=status,
            dedupe_key=f"{product_id}:0",
            error=error,
        )

    def test_pending_minus_succeeded(self) -> None:
        """plan 有 3 篇、records 已成功 2 篇 → reconcile 后 pending = 1，已成功篇不重发。"""
        plan = _plan_n(3)
        records = [
            self._record("p0", status="success", attempted_at="2026-06-02T10:00:00"),
            self._record("p1", status="success", attempted_at="2026-06-02T10:05:00"),
        ]
        reconciled = self._reconcile(plan, records)
        pending = [item for item in reconciled.items if item.status == "pending"]
        self.assertEqual([item.product_id for item in pending], ["p2"])
        self.assertEqual(reconciled.items[0].status, "published")
        self.assertEqual(reconciled.items[1].status, "published")

    def test_success_record_not_demoted_by_later_failed(self) -> None:
        """同 dedupe_key 先成功后又有一条 failed → 仍判 published，不降级（成功优先）。"""
        plan = _plan_n(1)
        records = [
            self._record("p0", status="success", attempted_at="2026-06-02T10:00:00"),
            self._record(
                "p0", status="failed", attempted_at="2026-06-02T10:10:00", error="late"
            ),
        ]
        reconciled = self._reconcile(plan, records)
        self.assertEqual(reconciled.items[0].status, "published")
        self.assertIsNone(reconciled.items[0].error)


def _plan_n(item_count: int) -> PublishPlanResult:
    return _plan(item_count)


if __name__ == "__main__":
    unittest.main()
