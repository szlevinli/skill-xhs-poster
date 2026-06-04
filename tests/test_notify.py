from __future__ import annotations

import unittest

import xhs_poster.notify as notify
from xhs_poster.config import Settings
from xhs_poster.models import PublishRunResult
from xhs_poster.notify import (
    FeishuNotifier,
    NullNotifier,
    build_notifier,
    error_event,
    publish_summary_event,
    stage_done_event,
)


class _PostRecorder:
    """假 httpx.post：记录调用，返回一个带 .json() 的响应替身。"""

    def __init__(self, code: int = 0) -> None:
        self.calls: list[dict[str, object]] = []
        self._code = code

    def __call__(self, url: str, *, json: dict[str, object], timeout: float) -> _PostRecorder:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self

    def json(self) -> dict[str, object]:
        return {"code": self._code, "msg": "ok"}


def _settings(**overrides: object) -> Settings:
    return Settings.model_construct(None, **overrides)


def _result(*, attempted: int, succeeded: int, failed: int) -> PublishRunResult:
    return PublishRunResult(
        date="2026-06-04",
        mode="sequential",
        dedupe_scope="today",
        count_requested=attempted,
        count_selected=attempted,
        count_attempted=attempted,
        count_succeeded=succeeded,
        count_failed=failed,
    )


class BuildNotifierTests(unittest.TestCase):
    def test_no_url_returns_null_notifier(self) -> None:
        notifier = build_notifier(_settings(feishu_webhook_url=None))
        self.assertIsInstance(notifier, NullNotifier)

    def test_with_url_returns_feishu_notifier(self) -> None:
        notifier = build_notifier(
            _settings(
                feishu_webhook_url="https://example.invalid/hook",
                feishu_webhook_secret=None,
                feishu_notify_label="",
                feishu_notify_events="stage_done,publish_summary,error",
                feishu_notify_timeout_seconds=5.0,
            )
        )
        self.assertIsInstance(notifier, FeishuNotifier)

    def test_null_notifier_send_is_noop(self) -> None:
        # 不抛、不发，返回 None
        result = NullNotifier().send(error_event("publish", "boom", 1))
        self.assertIsNone(result)


class FeishuSendTests(unittest.TestCase):
    def _notifier(self, recorder: _PostRecorder, **overrides: object) -> FeishuNotifier:
        defaults: dict[str, object] = {
            "secret": None,
            "label": "xhs-prod",
            "timeout": 5.0,
            "enabled_kinds": frozenset({"stage_done", "publish_summary", "error"}),
        }
        defaults.update(overrides)
        notifier = FeishuNotifier("https://example.invalid/hook", **defaults)  # type: ignore[arg-type]
        notify.httpx.post = recorder  # type: ignore[assignment]
        return notifier

    def setUp(self) -> None:
        self._orig_post = notify.httpx.post

    def tearDown(self) -> None:
        notify.httpx.post = self._orig_post  # type: ignore[assignment]

    def test_send_posts_payload(self) -> None:
        recorder = _PostRecorder()
        notifier = self._notifier(recorder)
        notifier.send(stage_done_event("plan-publish", "准备就绪", [("计划", "5 篇")]))
        self.assertEqual(len(recorder.calls), 1)
        payload = recorder.calls[0]["json"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["msg_type"], "interactive")

    def test_send_swallows_transport_error(self) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("network down")

        notifier = self._notifier(_PostRecorder())
        notify.httpx.post = boom  # type: ignore[assignment]
        # 不冒泡
        notifier.send(error_event("publish", "boom", 1))

    def test_event_filtering_skips_disabled_kind(self) -> None:
        recorder = _PostRecorder()
        notifier = self._notifier(recorder, enabled_kinds=frozenset({"publish_summary", "error"}))
        notifier.send(stage_done_event("fetch-products", "就绪", []))
        self.assertEqual(recorder.calls, [])

    def test_secret_adds_timestamp_and_sign(self) -> None:
        recorder = _PostRecorder()
        notifier = self._notifier(recorder, secret="test-secret")
        notifier.send(error_event("publish", "boom", 1))
        payload = recorder.calls[0]["json"]
        assert isinstance(payload, dict)
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)


class CardStructureTests(unittest.TestCase):
    def test_success_card_template_and_content(self) -> None:
        event = publish_summary_event(_result(attempted=20, succeeded=18, failed=2))
        card = notify._build_card(event, "xhs-prod")
        header = card["header"]
        assert isinstance(header, dict)
        self.assertEqual(header["template"], "green")
        rendered = repr(card)
        self.assertIn("xhs-prod", rendered)  # label
        self.assertIn("成功 18 / 失败 2", rendered)  # fields
        self.assertIn("publish/2026-06-04/records.json", rendered)  # link

    def test_error_card_template_red(self) -> None:
        card = notify._build_card(error_event("publish", "掉登录", 2), "")
        header = card["header"]
        assert isinstance(header, dict)
        self.assertEqual(header["template"], "red")

    def test_publish_all_failed_is_error_level(self) -> None:
        event = publish_summary_event(_result(attempted=3, succeeded=0, failed=3))
        self.assertEqual(event.level, "error")

    def test_publish_noop_is_success_level(self) -> None:
        event = publish_summary_event(_result(attempted=0, succeeded=0, failed=0))
        self.assertEqual(event.level, "success")


class SignTests(unittest.TestCase):
    def test_sign_is_deterministic(self) -> None:
        self.assertEqual(
            notify._sign("1700000000", "test-secret"),
            "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84=",
        )


if __name__ == "__main__":
    unittest.main()
