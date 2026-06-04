# 飞书通知 — 实施计划（里程碑级）

> 配套设计稿：`docs/feishu-notify-design.md`（定稿）。本文件是可执行的逐里程碑计划。
> 工作方式：**一个 session 只做一个里程碑**，做完 `/clear` 再开下一个，与 `docs/impl-plan.md`（v2 重构）同规矩。
> 本特性独立于 v2 重构（M0–M10 已全部 ✅），是在已稳定系统上的**纯附加**能力。

---

## 给每个新 session 的使用说明（务必先读）

`/clear` 之后你没有上一轮对话记忆。按以下步骤冷启动，**不要依赖任何对话历史**：

1. 读本文件「进度跟踪」表，找第一个未完成（⬜）的里程碑 = 你这次要做的。
2. 读该里程碑完整小节（目标 / 前置状态 / 涉及文件 / 改动点 / 验收 / 回滚 / 收尾清单）。
3. 读设计稿 `docs/feishu-notify-design.md`（尤其 §3 架构、§4 配置、§5 协议、§7 已拍板取舍）。
4. 核对「前置状态」与当前代码是否一致（前序里程碑应已落 git）。不一致就停下来问，别硬上。
5. 编码 → 跑「验收」命令 → 全绿后执行「收尾清单」。
6. 收尾必须做：① git 提交（中文信息）② 进度表该行改 ✅ ③ 必要时更新 `memory/`。提示用户 `/clear` 继续下一个。

**铁律**：每个里程碑结束，`uv run python -m compileall src` + `uv run ruff check src tests` + `uv run pytest -q` 必须绿，不留半坏中间态。只改与当前里程碑相关的代码。

---

## 编码前需确认的设计取舍（来自设计稿 §7，本计划已采用以下默认）

| # | 取舍 | 本计划采用 |
|---|------|-----------|
| 1 | 准备阶段通知粒度 | **3 个准备命令各发各的 `stage_done`**（CLI 边界无跨进程状态，合不了）；用 `feishu_notify_events` 留降噪余地 |
| 2 | 模块形态 | **单文件 `notify.py`**，膨胀再拆包 |
| 3 | 卡片跳转按钮 | **不放**（records 在 VPS 本地点不开）；records 相对路径作文本字段 |
| 4 | systemd `OnFailure=` 兜底 | **本轮不做代码**，FN2 仅在部署文档提一节 |

> 若对以上有异议，**先改设计稿 §7 再编码**，不要在实施时临时改主意。

---

## 进度跟踪

| FN | 名称 | 状态 | 提交 |
|----|------|------|------|
| FN1 | 通知基建（config + notify.py + 单测） | ✅ | 6e3ea0a |
| FN2 | CLI 接线 + 文档/记忆收尾 | ✅ | （见本次提交） |

---

## FN1 — 通知基建（config + notify.py + 单测）

**目标**：新增飞书通知模块与配置，**纯附加、不接线、不改任何现有行为**。FN1 完成后系统行为与 FN1 前完全一致（没人调 notifier），但 `notify.py` 已可被单测覆盖。compileall + ruff + pyright + pytest 全绿。

**前置状态**：v2 重构全部完成（最新提交 `1f034c2` 之后，工作树干净）。无 `notify.py`；`config.py` 无 feishu 字段；`httpx` 已在 `pyproject.toml` deps（无需加依赖）。设计稿 `docs/feishu-notify-design.md` 已定稿。

**涉及文件**：
- **新增** `src/xhs_poster/notify.py`
- **改** `src/xhs_poster/config.py`（加 5 个字段）
- **新增** `tests/test_notify.py`

**改动点**：

1. **`config.py`** — 在 `publish_interval_max_seconds` 字段之后、`@property data_dir` 之前，按设计稿 §4 加 5 个字段（`Field` + `AliasChoices` + 中文 description）：
   - `feishu_webhook_url: str | None = None`（别名 `XHS_POSTER_FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_URL`）
   - `feishu_webhook_secret: str | None = None`（`..._FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_SECRET`）
   - `feishu_notify_label: str = ""`（`..._FEISHU_NOTIFY_LABEL` / `FEISHU_NOTIFY_LABEL`）
   - `feishu_notify_events: str = "stage_done,publish_summary,error"`（`..._FEISHU_NOTIFY_EVENTS` / `FEISHU_NOTIFY_EVENTS`）
   - `feishu_notify_timeout_seconds: float = 5.0`（`..._FEISHU_NOTIFY_TIMEOUT_SECONDS` / `FEISHU_NOTIFY_TIMEOUT_SECONDS`）

2. **`notify.py`**（单文件，`from __future__ import annotations`，无新依赖——`httpx` 已有，`hmac`/`hashlib`/`base64`/`time`/`json` 均标准库）：
   - `NotifyKind = Literal["stage_done", "publish_summary", "error"]`、`NotifyLevel = Literal["success", "error", "info"]`
   - `@dataclass(frozen=True) class NotifyEvent`：`kind / level / title / fields: list[tuple[str,str]] / link: str | None`（见设计稿 §3.1）
   - `class Notifier(Protocol)`：`def send(self, event: NotifyEvent) -> None`
   - `class NullNotifier`：`send` 空 no-op
   - `class FeishuNotifier`：
     - `__init__(webhook_url, *, secret, label, timeout, enabled_kinds: frozenset[str])`
     - `send(event)`：① `event.kind not in self.enabled_kinds` → return（事件过滤）② `_build_card` ③ secret 非空则 `_sign` 加 `timestamp`+`sign` ④ `httpx.post(url, json=payload, timeout=...)` ⑤ 校验响应 `code==0`/`StatusCode==0`，非 0 `log_error`。**整个 send 体 try/except Exception → `log_error`，永不抛**（失败隔离，设计稿 §3.4）。
   - `_build_card(event, label) -> dict`：按 `level` 选 header `template`（success→green / error→red / info→grey），标题加 emoji，`fields`+`label`+`link` 渲染进 `elements`（设计稿 §5.2）。
   - `_sign(timestamp: str, secret: str) -> str`：飞书算法 `key=f"{timestamp}\n{secret}"`，对空字节串做 HMAC-SHA256，base64（以官方文档为准）。
   - `build_notifier(settings) -> Notifier`：无 `feishu_webhook_url` → `NullNotifier`；否则解析 `feishu_notify_events` 成 `frozenset` 传入 `FeishuNotifier`。
   - 语义构造器：`stage_done_event(cmd, summary, fields)` / `publish_summary_event(result: PublishRunResult)` / `error_event(cmd, message, exit_code)` → 各返回 `NotifyEvent`。
   - 失败隔离依赖 `from .logging import log_error`。

3. **`tests/test_notify.py`**（仿 `tests/test_run_publish_plan_exit_code.py` 的 monkeypatch 风格，**全程不打真网络**）：
   - `build_notifier`：无 url → `NullNotifier`；有 url → `FeishuNotifier`。
   - `NullNotifier.send` no-op（不抛、不发）。
   - **失败隔离**：注入一个抛异常的 fake `httpx.post`（monkeypatch），断言 `FeishuNotifier.send` 不冒泡。
   - 卡片结构：`_build_card` 对 success/error 的 `header.template` 正确；含 `label`、`fields`、`link`。
   - 签名确定性：给定固定 `timestamp`+`secret`，`_sign` 输出等于预先算好的常量。
   - 事件过滤：`enabled_kinds` 不含某 kind 时，`send` 不调用 `httpx.post`（用 recorder fake 断言未被调用）。

**验收**（新 session 可独立执行）：
- `uv run python -m compileall src` 绿；`uv run ruff check src tests` 过；`uv run pyright src/xhs_poster/notify.py src/xhs_poster/config.py` 零新增；`uv run pytest -q` 绿（新增用例通过）。
- `uv run xhs-poster --help` 正常；**现有命令行为/退出码零变化**（notifier 尚未接入 cli）。
- `grep -rn "httpx" tests/` 确认测试无真实网络出口（post 被 monkeypatch）。

**回滚**：`git revert` 本里程碑提交（纯新增，无副作用）。

**收尾清单**：
- [ ] compileall / ruff / pyright / pytest 全绿
- [ ] git 提交：`通知基建：notify.py + config 飞书字段 + 单测`
- [ ] 进度表 FN1 → ✅
- [ ] 若 `notify.py` 比预期大（卡片模板膨胀），在 `memory/` 记一笔是否该拆包
- [ ] 提示用户 `/clear` 继续 FN2

---

## FN2 — CLI 接线 + 文档/记忆收尾

**目标**：把 notifier 接到 `cli.py` 四个命令（fetch-products / generate-content / plan-publish / publish）的成功/异常收尾，告警正式上线。更新部署文档与项目记忆，明确记录这是对 v2「不做主动告警」决策的**有意反转**。配测试群 webhook 手动冒烟成功/失败各一条。

**前置状态**：FN1 已完成（`notify.py` 存在、单测绿，但无人调用）。`cli.py` 四个流水线命令现状：命令体不显式建 `Settings`（核心函数内部自建）；成功走 `log_summary`+`Exit(0/1)`，异常分 `LoginRequiredError`(exit 2) 与 `Exception`(exit 1) 两个 except 块。

**涉及文件**：
- **改** `src/xhs_poster/cli.py`
- **改** `deploy/README.md`
- **改** `.env.example`（补飞书可选配置注释块，与 deploy 文档同步）
- **改** `docs/refactor-plan.md`（决策记录补一条反转说明）
- **改** `memory/refactor-v2.md`（记录告警决策反转 + 本特性完成）
- **可选新增** `tests/test_cli_notify.py`

**改动点**：

1. **`cli.py`**：
   - 顶部 `from .notify import build_notifier, stage_done_event, publish_summary_event, error_event`。
   - 每个目标命令体开头：`notifier = build_notifier(Settings())`（多建一个 `Settings` 读 env，便宜；未配 webhook 时是 `NullNotifier`，零行为）。需 `from .config import Settings`（确认是否已 import）。
   - 接入点（设计稿 §3.2 映射表）：
     - **fetch-products**：成功 → `notifier.send(stage_done_event("fetch-products", 汇总文案, [("就绪",...),("失败",...),("跳过",...)]))`；`LoginRequiredError` 块 → `error_event(cmd, msg, 2)`；`Exception` 块 → `error_event(cmd, str(exc), 1)`。注意现有「success_count==0 也 exit 1」分支也要发 error（准备失败）。
     - **generate-content**：成功 → `stage_done_event`；`Exception` 块 → `error_event(cmd, str(exc), 1)`。
     - **plan-publish**：成功 → `stage_done_event`（= 准备就绪）；`Exception` 块 → `error_event(cmd, str(exc), 1)`。
     - **publish**：成功收尾 → `publish_summary_event(result)`（**部分失败也发**，卡片里成功/失败计数）；`LoginRequiredError` → `error_event(cmd, msg, 2)`；`Exception` → `error_event(cmd, str(exc), 1)`。
   - **`auth` / `login` 子命令不接**（人工在场，设计稿 §3.2）。
   - `notifier.send` 调用放在 `log_error`/`log_summary` 之后、`raise typer.Exit` 之前。因 `send` 永不抛，不影响退出码。

2. **`deploy/README.md`**：加「飞书通知（可选）」一节——`.env` 配 `FEISHU_WEBHOOK_URL`（被现有 `EnvironmentFile=` 自动加载，**无需改 service unit**）+ 可选 `FEISHU_WEBHOOK_SECRET`/`FEISHU_NOTIFY_LABEL`；VPS 需出网到 `open.feishu.cn:443`；附「进阶：systemd `OnFailure=` 兜底进程暴毙」一段（标为后续可选，本轮不做代码）。

3. **`.env.example`**：在 LLM 配置块之后补一段注释化的「飞书通知（可选）」——`FEISHU_WEBHOOK_URL`（必填才启用）、可选 `FEISHU_WEBHOOK_SECRET` / `FEISHU_NOTIFY_LABEL` / `FEISHU_NOTIFY_EVENTS` / `FEISHU_NOTIFY_TIMEOUT_SECONDS`，全部以 `#` 注释、不影响默认（不配=NullNotifier）。与 `deploy/README.md` 那节保持口径一致。

4. **`docs/refactor-plan.md`**：决策记录处补一行——「飞书主动告警（2026-06）反转 M8『不做主动告警』，详见 `docs/feishu-notify-design.md`」。

5. **`memory/refactor-v2.md`**：补一句记录该反转 + 飞书特性已落地，并 `[[link]]` 到设计稿/本计划（保持记忆与现状一致）。

6. **可选 `tests/test_cli_notify.py`**：monkeypatch `cli.build_notifier` 返回一个 recorder notifier，用 Typer `CliRunner` 跑命令，断言成功路径发 `publish_summary`/`stage_done`、失败路径发 `error`，且**退出码与未接通知时一致**（仿 `test_run_publish_plan_exit_code.py`，不真发笔记、不打真网络）。

**验收**（新 session 可独立执行）：
- `uv run python -m compileall src` 绿；`uv run ruff check src tests` 过；`uv run pyright src/xhs_poster/cli.py` 零新增；`uv run pytest -q` 绿。
- **未配 webhook 时**：所有命令行为/退出码与 FN2 前完全一致（`NullNotifier`，无网络、无日志噪音）。
- **手动冒烟**（配测试群 `FEISHU_WEBHOOK_URL`）：
  - 跑一个会成功的命令（如 `plan-publish`）→ 群里收到绿卡。
  - 制造一次失败（如缺 `products.json` 跑 `generate-content`）→ 群里收到红卡，且命令退出码仍是 `1`。
  - **失败隔离**：把 `FEISHU_WEBHOOK_URL` 改成错误地址 / 断网，重跑上面命令 → 命令仍按原退出码退出，仅 stderr 多一行通知失败日志，发布/准备结果不受影响。

**回滚**：`git revert` 本里程碑提交（cli 接线移除，notify.py 保留无害）。

**收尾清单**：
- [ ] compileall / ruff / pyright / pytest 全绿
- [ ] 未配 webhook 时退出码/行为零变化 验证通过
- [ ] 测试群手动冒烟：成功绿卡 + 失败红卡 + 断网失败隔离 三项均通过
- [ ] git 提交：`飞书通知接线：CLI 四命令收尾接 notifier + 部署文档 + 决策反转记录`
- [ ] 进度表 FN2 → ✅
- [ ] 更新 `memory/refactor-v2.md`：记录主动告警决策反转 + 飞书特性完成
- [ ] 本特性完成，无需 `/clear` 后续里程碑
