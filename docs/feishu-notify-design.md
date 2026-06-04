# 飞书通知通道 — 技术选型与架构设计（待审核）

状态：**设计稿，未实施**。本文档落盘供 levin 审核；定稿后再展开编码里程碑。

> ⚠️ 与 v2 重构决策的关系：`docs/refactor-plan.md` / 记忆 `refactor-v2` 的 M8 明确写过「可观测：默认失败才打包证据……**不做主动告警**」。本特性是对该条的**有意反转**——需求变化所致，不是疏漏。定稿合入后应同步更新 `refactor-v2` 记忆与 refactor-plan 决策记录，避免日后翻 git 困惑。

---

## 1. 需求

VPS 上 systemd 定时跑「准备批」(fetch-products → generate-content → plan-publish) 与「发布批」(publish)。当前只有 stderr 日志进 journal，无人值守时出了事不知道。要在以下时机主动推一条飞书消息：

1. **准备完成** — 准备链各阶段成功
2. **发布完成** — publish 批次结束，报成功/失败数
3. **发生错误** — 任意命令异常退出（含掉登录 exit 2）

## 2. 选型结论（已与 levin 确认）

| 维度 | 选定 | 理由 |
|------|------|------|
| 通道 | **自定义群机器人 Webhook** | 单向通知够用；只需 1 个 URL（+ 可选签名 secret），无 app_id/secret、无 OAuth、无 token 刷新、无 openid 解析。依赖仅 `httpx`（已在 deps）。自建应用 API 是重型方案，对单向通知不划算。 |
| 粒度 | **批次级 / CLI 边界** | 钩子只挂在 `cli.py` 每个命令收尾处；流水线核心 (fetch/generate/publish/session) 保持纯净。消息少、信噪比高。每篇级会让 20 篇一批刷 20+ 条，否决。 |
| 形式 | **交互卡片 interactive** | header 模板色按级别区分（成功绿 / 错误红 / 中性灰），字段排版，可挂跳转按钮。纯文本无法一眼区分成功失败。 |

> 关于可用的 `lark-*` skills：那是**本对话助手**的 MCP 能力，运行在我这侧；VPS 上的 CLI 是独立 Python 进程，必须自带实现，不能依赖 skill。故走 httpx 直连 webhook。

## 3. 架构

### 3.1 新增模块 `src/xhs_poster/notify.py`

定位与 `logging.py` 平级——一个横切的「出站告警 seam」。单文件即可（预计 ~180 行）；若后续卡片模板膨胀再拆 `notify/` 包（`feishu.py` 传输 + `events.py` 事件 + `__init__.py` re-export）。

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

NotifyKind = Literal["stage_done", "publish_summary", "error"]
NotifyLevel = Literal["success", "error", "info"]


@dataclass(frozen=True)
class NotifyEvent:
    """一条待推送的通知。fields 是有序键值行，渲染进卡片正文。"""
    kind: NotifyKind
    level: NotifyLevel
    title: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    link: str | None = None          # 可选跳转（records 路径 / journal 提示）


class Notifier(Protocol):
    def send(self, event: NotifyEvent) -> None: ...


class NullNotifier:
    """未配置 webhook 时的空实现：调用方无需写 if，直接 send。"""
    def send(self, event: NotifyEvent) -> None:
        return None


class FeishuNotifier:
    def __init__(self, webhook_url: str, *, secret: str | None,
                 label: str, timeout: float) -> None: ...

    def send(self, event: NotifyEvent) -> None:
        """构卡 → POST。任何异常只 log_error 到 stderr，绝不抛出。"""
        ...


def build_notifier(settings: Settings) -> Notifier:
    if not settings.feishu_webhook_url:
        return NullNotifier()
    return FeishuNotifier(
        settings.feishu_webhook_url,
        secret=settings.feishu_webhook_secret,
        label=settings.feishu_notify_label,
        timeout=settings.feishu_notify_timeout_seconds,
    )


# 语义构造器：cli.py 调用点用它们拼 NotifyEvent，卡片细节收敛在 notify.py
def stage_done_event(cmd: str, summary: str, fields: list[tuple[str, str]]) -> NotifyEvent: ...
def publish_summary_event(result: PublishRunResult) -> NotifyEvent: ...
def error_event(cmd: str, message: str, exit_code: int) -> NotifyEvent: ...
```

设计要点：

- **`NullNotifier` 让调用点无分支**——未配置就静默 no-op，配置了才发。
- **`send` 永不抛**：通知失败不能影响流水线结果或退出码（见 §3.4）。
- **事件是数据 (`NotifyEvent`)**，卡片 JSON 构造收敛在 `FeishuNotifier` 内一处，便于改版式和单测。

### 3.2 钩子挂在 `cli.py`（唯一接入点）

每个命令进程启动时 `notifier = build_notifier(Settings())`，收尾处发事件。流水线核心代码零改动。

```python
# 以 publish 为例（其余命令同构）
@app.command("publish", ...)
def publish_command(...):
    cmd = "publish"
    notifier = build_notifier(Settings())
    try:
        result = run_publish_plan(...)
    except LoginRequiredError as exc:
        log_error(f"[{cmd}] 登录态失效：{exc.session.message}")
        notifier.send(error_event(cmd, exc.session.message, exit_code=2))
        raise typer.Exit(code=2)
    except Exception as exc:
        log_error(f"[{cmd}] 失败：{exc}")
        notifier.send(error_event(cmd, str(exc), exit_code=1))
        raise typer.Exit(code=1)
    log_summary(...)
    notifier.send(publish_summary_event(result))   # 含部分失败：批次摘要总发
    raise typer.Exit(code=0 if ... else 1)
```

事件映射：

| 命令 | 成功收尾 | 异常/登录失效 |
|------|----------|----------------|
| `fetch-products` | `stage_done`（就绪 N / 失败 / 跳过） | `error`（exit 1 / 2） |
| `generate-content` | `stage_done`（N 商品 / M 草稿） | `error`（exit 1） |
| `plan-publish` | `stage_done`（计划 N 篇）= 「准备就绪」 | `error`（exit 1） |
| `publish` | `publish_summary`（成功/尝试/失败） | `error`（exit 1 / 2） |

> `login` / `auth` 子命令是人工交互场景，默认**不**接通知（人在现场）。如需可后补。

### 3.3 「准备完成」是 3 张卡，不是 1 张

`xhs-prepare.service` 顺序跑 fetch / generate / plan-publish 三条 `ExecStart`，是三个独立进程。CLI 边界无跨进程状态，无法天然合并成一条「准备就绪」。两种取舍：

- **默认**：三个准备阶段各发各的 `stage_done`（每阶段一卡）。语义清晰、能定位是哪一步，但一次准备批刷 3 条。
- **降噪**：用 `feishu_notify_events` 过滤（§4），只让 `plan-publish` 发（它是准备链终点，到了即「准备就绪」），fetch/generate 成功静默、仅失败发 error。

建议默认全发，觉得吵再用过滤收。**这是需要你拍板的点之一**（见 §7）。

### 3.4 失败隔离（最高优先级约束）

通知是副信道，**任何情况下不得改变流水线行为或退出码**：

- `FeishuNotifier.send` 整体 try/except，异常只 `log_error` 到 stderr。
- httpx 短超时（默认 5s），避免飞书侧抖动拖住 oneshot 进程。
- 网络不通 / 4xx / 5xx / 超时 → 吞掉，日志留痕，进程照常按原退出码退出。

> 反向风险：Python 的 except 抓不到硬崩溃（OOM、SIGKILL、解释器在发卡前就挂）。这类「进程暴毙」靠 systemd 兜底——在 unit 加 `OnFailure=` 指向一个发飞书的 oneshot，或用 `systemd-notify`。属部署侧增强，**不在本代码改动范围**，但建议部署文档补一节（§6）。两层互补：应用层报业务结果，systemd 层报进程暴毙。

## 4. 配置（`config.py` 新增）

```python
feishu_webhook_url: str | None = Field(
    default=None,
    validation_alias=AliasChoices("XHS_POSTER_FEISHU_WEBHOOK_URL", "FEISHU_WEBHOOK_URL"),
    description="飞书群机器人 webhook 完整 URL；未设置则全程不发通知（NullNotifier）。",
)
feishu_webhook_secret: str | None = Field(
    default=None,
    validation_alias=AliasChoices("XHS_POSTER_FEISHU_WEBHOOK_SECRET", "FEISHU_WEBHOOK_SECRET"),
    description="飞书机器人「签名校验」密钥；设置后按飞书算法在请求体带 timestamp+sign。机器人若开了签名校验则必填。",
)
feishu_notify_label: str = Field(
    default="",
    validation_alias=AliasChoices("XHS_POSTER_FEISHU_NOTIFY_LABEL", "FEISHU_NOTIFY_LABEL"),
    description="部署标识（如 xhs-prod / 主机名），进卡片副标题，区分多套部署。",
)
feishu_notify_events: str = Field(
    default="stage_done,publish_summary,error",
    validation_alias=AliasChoices("XHS_POSTER_FEISHU_NOTIFY_EVENTS", "FEISHU_NOTIFY_EVENTS"),
    description="逗号分隔的启用事件白名单；用于降噪（如只留 publish_summary,error）。",
)
feishu_notify_timeout_seconds: float = Field(
    default=5.0,
    validation_alias=AliasChoices("XHS_POSTER_FEISHU_NOTIFY_TIMEOUT_SECONDS", "FEISHU_NOTIFY_TIMEOUT_SECONDS"),
    description="发卡片 HTTP 超时（秒），防飞书抖动拖住进程。",
)
```

`.env` 最小新增（不配则零行为，完全向后兼容）：

```dotenv
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
# 机器人若开签名校验再加：
FEISHU_WEBHOOK_SECRET=xxxxxxxx
FEISHU_NOTIFY_LABEL=xhs-prod
```

> **安全**：webhook URL = 谁拿到谁能往群里发消息，是 secret。`.env` 已在 `.gitignore`（已确认），勿硬编码、勿提交。建议机器人开「签名校验」并配 `FEISHU_WEBHOOK_SECRET`，比仅靠 URL 保密更稳。

## 5. 飞书协议要点

### 5.1 请求

`POST <webhook_url>`，body：

```json
{ "msg_type": "interactive", "card": { "config": {...}, "header": {...}, "elements": [...] } }
```

开了签名校验时额外带：

```json
{ "timestamp": "<unix秒>", "sign": "<base64(HMAC-SHA256)>", "msg_type": "...", "card": {...} }
```

签名算法（飞书定义）：`key = f"{timestamp}\n{secret}"`，对**空字节串**做 HMAC-SHA256（key 即上式），结果 base64。实现时以官方文档为准（`hmac`/`base64` 标准库即可，无新依赖）。

### 5.2 卡片版式（按级别）

| level | header.template | 触发 |
|-------|-----------------|------|
| success | `green` | 阶段完成 / 发布全成或部分成 |
| error | `red` | 异常 / 掉登录 |
| info | `grey`(`blue`) | 预留 |

正文用 `div` + `fields` 渲染键值行；底部可挂 `action` 按钮跳转（如 records 路径提示 / journal 命令）。卡片 JSON 模板收敛在 `FeishuNotifier`，调用点只给 `NotifyEvent` 数据。

示例（发布完成，部分失败）：

```
┌──────────────────────────┐
│ ✅ 发布完成          [绿]   │   header
├──────────────────────────┤
│ 部署    xhs-prod          │
│ 日期    2026-06-04        │
│ 结果    成功 18 / 失败 2   │
│ records publish/2026-06-04/│
│         records.json       │
└──────────────────────────┘
```

### 5.3 响应

成功返回 `{"code":0,"msg":"success"}` 或 `StatusCode:0`。非 0 视为失败、记日志、不重试（批次级低频，重试无必要；漏一条不致命）。

## 6. 部署影响（写进 `deploy/README.md` 即可，无 unit 改动）

1. VPS 需能出网到 `open.feishu.cn`（443）。内网受限环境要放行。
2. `.env` 补 `FEISHU_WEBHOOK_URL`（已被 `EnvironmentFile=` 加载，无需改 service）。
3. **建议**补 systemd `OnFailure=` 兜底进程暴毙（§3.4），与应用层通知互补。可后续单独做。

## 7. 待你拍板 / 已知取舍

1. **准备阶段通知粒度**（§3.3）：默认 3 卡全发，还是只 plan-publish 发「准备就绪」+ 其余仅失败发？倾向**默认全发，留 `feishu_notify_events` 给你后调**。
2. **模块形态**：先单文件 `notify.py`，还是直接拆 `notify/` 包？倾向**先单文件**，膨胀再拆。
3. **卡片跳转按钮**：records 在 VPS 本地、群里点不开；按钮意义有限。倾向**不放跳转按钮，只把 records 相对路径作为文本字段**给运维 ssh 上去看。
4. **OnFailure systemd 兜底**：本轮一起做，还是先只做应用层？倾向**本轮先只做应用层**，systemd 兜底另开小任务。

## 8. 实施清单（定稿后展开为里程碑）

1. `config.py`：加 5 个 feishu 字段（§4）。
2. 新建 `notify.py`：`NotifyEvent` / `Notifier` / `NullNotifier` / `FeishuNotifier`（含签名 + 失败隔离）/ `build_notifier` / 3 个语义构造器（§3.1）。
3. `cli.py`：4 个命令的成功/异常收尾接 `notifier.send`（§3.2）；尊重 `feishu_notify_events` 过滤。
4. `tests/`：`NullNotifier` no-op；`FeishuNotifier.send` 在传输抛错时不冒泡（注入 fake transport）；卡片 JSON 结构与签名计算单测。**全程不打真网络**。
5. 文档：`deploy/README.md` 加飞书配置节；更新 `refactor-v2` 记忆 + refactor-plan，记录「主动告警」决策反转。
6. 验收：`uv run python -m compileall src` + `uv run pytest -q` 绿；配一个测试群 webhook 手动冒烟一条。
