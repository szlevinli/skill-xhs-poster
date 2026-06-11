from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

import httpx

from .logging import log_error

if TYPE_CHECKING:
    from .config import Settings
    from .models import PublishRunResult


NotifyKind = Literal["stage_done", "publish_summary", "error"]
NotifyLevel = Literal["success", "error", "info"]

_LEVEL_TEMPLATE: dict[NotifyLevel, str] = {
    "success": "green",
    "error": "red",
    "info": "grey",
}
_LEVEL_EMOJI: dict[NotifyLevel, str] = {
    "success": "✅",
    "error": "❌",
    "info": "ℹ️",
}


@dataclass(frozen=True)
class NotifyEvent:
    """一条待推送的通知。fields 是有序键值行，渲染进卡片正文。"""

    kind: NotifyKind
    level: NotifyLevel
    title: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    link: str | None = None  # 可选文本字段（records 相对路径），不渲染为跳转按钮


class Notifier(Protocol):
    def send(self, event: NotifyEvent) -> None: ...


class NullNotifier:
    """未配置 webhook 时的空实现：调用方无需写 if，直接 send。"""

    def send(self, event: NotifyEvent) -> None:
        return None


class FeishuNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        secret: str | None,
        label: str,
        timeout: float,
        enabled_kinds: frozenset[str],
    ) -> None:
        self._webhook_url = webhook_url
        self._secret = secret
        self._label = label
        self._timeout = timeout
        self._enabled_kinds = enabled_kinds

    _MAX_ATTEMPTS = 3

    def send(self, event: NotifyEvent) -> None:
        """构卡 → POST，带退避重试。任何异常只 log_error 到 stderr，绝不抛出（失败隔离）。

        飞书对 webhook 有频率限制（瞬时返回 code 11232 ``frequency limited``），单次失败就放弃会丢掉
        发布摘要/告警（线上实测：批量发布失败那次摘要正好撞上限流、用户什么都没收到）。故非 0 码或网络
        异常都退避重试（2s、4s），多数瞬时限流二次即过。``sign`` 含 timestamp，每次重试都重新生成。
        """
        if event.kind not in self._enabled_kinds:
            return
        card = _build_card(event, self._label)
        last: object = None
        for attempt in range(self._MAX_ATTEMPTS):
            try:
                payload: dict[str, object] = {"msg_type": "interactive", "card": card}
                if self._secret:
                    timestamp = str(int(time.time()))
                    payload["timestamp"] = timestamp
                    payload["sign"] = _sign(timestamp, self._secret)
                response = httpx.post(self._webhook_url, json=payload, timeout=self._timeout)
                data = response.json()
                code = data.get("code", data.get("StatusCode"))
                if code in (0, "0"):
                    return
                last = data
            except Exception as exc:  # noqa: BLE001 — 失败隔离：通知永不影响流水线
                last = exc
            if attempt < self._MAX_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
        log_error(f"[notify] 发送失败（重试 {self._MAX_ATTEMPTS} 次仍失败）：{last}")


def _build_card(event: NotifyEvent, label: str) -> dict[str, object]:
    rows: list[tuple[str, str]] = []
    if label:
        rows.append(("部署", label))
    rows.extend(event.fields)
    if event.link:
        rows.append(("records", event.link))

    elements: list[dict[str, object]] = []
    if rows:
        elements.append(
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": False,
                        "text": {"tag": "lark_md", "content": f"**{key}**：{value}"},
                    }
                    for key, value in rows
                ],
            }
        )

    emoji = _LEVEL_EMOJI[event.level]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _LEVEL_TEMPLATE[event.level],
            "title": {"tag": "plain_text", "content": f"{emoji} {event.title}"},
        },
        "elements": elements,
    }


def _sign(timestamp: str, secret: str) -> str:
    """飞书签名：key = f'{timestamp}\\n{secret}'，对空字节串做 HMAC-SHA256，base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_notifier(settings: Settings) -> Notifier:
    if not settings.feishu_webhook_url:
        return NullNotifier()
    enabled_kinds = frozenset(
        part.strip() for part in settings.feishu_notify_events.split(",") if part.strip()
    )
    return FeishuNotifier(
        settings.feishu_webhook_url,
        secret=settings.feishu_webhook_secret,
        label=settings.feishu_notify_label,
        timeout=settings.feishu_notify_timeout_seconds,
        enabled_kinds=enabled_kinds,
    )


def stage_done_event(cmd: str, summary: str, fields: list[tuple[str, str]]) -> NotifyEvent:
    return NotifyEvent(kind="stage_done", level="success", title=summary, fields=fields)


def publish_summary_event(result: PublishRunResult) -> NotifyEvent:
    level: NotifyLevel = (
        "error" if result.count_attempted > 0 and result.count_succeeded == 0 else "success"
    )
    fields = [
        ("日期", result.date),
        ("结果", f"成功 {result.count_succeeded} / 失败 {result.count_failed}"),
        ("尝试", str(result.count_attempted)),
    ]
    return NotifyEvent(
        kind="publish_summary",
        level=level,
        title="发布完成",
        fields=fields,
        link=f"publish/{result.date}/records.json",
    )


def error_event(cmd: str, message: str, exit_code: int) -> NotifyEvent:
    return NotifyEvent(
        kind="error",
        level="error",
        title=f"{cmd} 失败",
        fields=[("命令", cmd), ("退出码", str(exit_code)), ("原因", message)],
    )
