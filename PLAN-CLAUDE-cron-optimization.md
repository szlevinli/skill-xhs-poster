# Cron 无人值守优化计划

**目标**：优化 xhs-poster skill 在 Openclaw + Kimi2.5 + cron 定时执行场景下的稳定性和可靠性。  
**执行方式**：每个 Task 独立，可单独提交。按优先级顺序执行。  
**验证方式**：每个 Task 完成后运行 `uv run python -m compileall src` 确认无语法错误。  
**来源说明**：Task 1-7 来自原始分析，Task 8-14 为评估 GEMINI/OPENAI 计划后补充。

---

## Task 1 — 移除 run_phase3_plan 的隐式补计划行为（P0）

**文件**：`src/xhs_poster/phase3.py`  
**位置**：`run_phase3_plan` 函数，第 552～562 行附近

**当前代码**：
```python
plan = load_publish_plan(settings)
current_date = date or datetime.now().date().isoformat()
if plan is None or plan.date != current_date:
    plan = build_phase3_plan(
        mode=mode,
        count=count,
        settings=settings,
        date=current_date,
        dedupe_scope=dedupe_scope,
        seed=seed,
    )
```

**改为**：
```python
plan = load_publish_plan(settings)
current_date = date or datetime.now().date().isoformat()
if plan is None or plan.date != current_date:
    raise RuntimeError(
        f"当天（{current_date}）发布计划不存在或已过期，请先执行 plan-publish 生成今日计划。"
    )
```

**验证**：
```bash
uv run python -m compileall src
# 手动测试：不存在 publish-plan.json 时执行 run-publish-plan，
# 应返回 exit code 1 且 error 字段包含"请先执行 plan-publish"
```

---

## Task 2 — 每日 50 条发布上限代码层硬限制（P0）

**文件**：`src/xhs_poster/phase3.py`  
**位置**：`run_phase3_plan` 函数内，`reconcile_publish_plan_with_records` 行之后、`pending_items` 行之前

**插入代码**：
```python
DAILY_PUBLISH_LIMIT = 50
today_records = load_phase3_daily_records(settings, current_date)
success_today = sum(1 for r in today_records.records if r.status == "success")
if success_today >= DAILY_PUBLISH_LIMIT:
    return Phase3RunPlanResult(
        date=current_date,
        mode=plan.mode,
        dedupe_scope=plan.dedupe_scope,
        count_requested=count,
        count_selected=0,
        count_attempted=0,
        count_succeeded=0,
        count_failed=0,
        seed=plan.seed,
        results=[],
        blocked_reason=f"今日已成功发布 {success_today} 篇，已达每日上限 {DAILY_PUBLISH_LIMIT} 篇，停止发布。",
    )
```

**文件**：`src/xhs_poster/models.py`  
**位置**：`Phase3RunPlanResult` 类末尾，`results` 字段之后追加：
```python
blocked_reason: str | None = None
```

**验证**：
```bash
uv run python -m compileall src
```

---

## Task 3 — 新增 `status` 子命令，输出今日全局状态（P1）

**目标**：Agent 一次调用即可获取所有阶段状态和推荐下一步动作，也是 Task 8 `run-daily` 内部状态检查的基础。

### 3a — 在 models.py 末尾追加状态模型

**文件**：`src/xhs_poster/models.py`

```python
class Phase1StatusSummary(BaseModel):
    done: bool
    status: str  # "complete" | "partial" | "not_started"
    product_count: int = 0
    date: str | None = None


class Phase2StatusSummary(BaseModel):
    done: bool
    contents_count: int = 0
    date: str | None = None


class Phase3StatusSummary(BaseModel):
    plan_exists: bool
    plan_date: str | None = None
    pending: int = 0
    published_today: int = 0
    daily_limit_reached: bool = False


class DailyStatusResult(BaseModel):
    date: str
    phase1: Phase1StatusSummary
    phase2: Phase2StatusSummary
    phase3: Phase3StatusSummary
    next_recommended_action: str
    login_required: bool = False


class DailyStatusSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    data: DailyStatusResult
```

### 3b — 新建 src/xhs_poster/status.py

```python
from __future__ import annotations

from datetime import date as date_type

from .config import Settings
from .models import (
    ContentsBundle,
    DailyStatusResult,
    DailyStatusSuccess,
    Phase1StatusSummary,
    Phase2StatusSummary,
    Phase3StatusSummary,
    SkillError,
    TodayPool,
)
from .phase3 import load_phase3_daily_records, load_publish_plan

DAILY_PUBLISH_LIMIT = 50


def get_daily_status(settings: Settings | None = None) -> DailyStatusResult:
    settings = settings or Settings()
    today = str(date_type.today())

    # Phase1
    phase1 = Phase1StatusSummary(done=False, status="not_started")
    if settings.today_pool_path.exists():
        try:
            pool = TodayPool.model_validate_json(
                settings.today_pool_path.read_text(encoding="utf-8")
            )
            if pool.date == today:
                phase1 = Phase1StatusSummary(
                    done=True,
                    status=pool.status,
                    product_count=len(pool.products),
                    date=pool.date,
                )
        except Exception:
            pass

    # Phase2
    phase2 = Phase2StatusSummary(done=False)
    if settings.contents_path.exists():
        try:
            bundle = ContentsBundle.model_validate_json(
                settings.contents_path.read_text(encoding="utf-8")
            )
            if bundle.date == today:
                total = sum(len(v) for v in bundle.contents.values())
                phase2 = Phase2StatusSummary(done=True, contents_count=total, date=bundle.date)
        except Exception:
            pass

    # Phase3
    plan = None
    try:
        plan = load_publish_plan(settings)
    except Exception:
        pass

    today_records = load_phase3_daily_records(settings, today)
    published_today = sum(1 for r in today_records.records if r.status == "success")
    daily_limit_reached = published_today >= DAILY_PUBLISH_LIMIT

    if plan and plan.date == today:
        pending = sum(1 for item in plan.items if item.status == "pending")
        phase3 = Phase3StatusSummary(
            plan_exists=True,
            plan_date=plan.date,
            pending=pending,
            published_today=published_today,
            daily_limit_reached=daily_limit_reached,
        )
    else:
        phase3 = Phase3StatusSummary(
            plan_exists=False,
            published_today=published_today,
            daily_limit_reached=daily_limit_reached,
        )

    # 检查 phase1-state 是否有 LOGIN_REQUIRED 阻塞
    login_required = False
    if settings.phase1_state_path.exists():
        try:
            from .models import Phase1State
            p1_state = Phase1State.model_validate_json(
                settings.phase1_state_path.read_text(encoding="utf-8")
            )
            if p1_state.blocked_reason == "LOGIN_REQUIRED":
                login_required = True
        except Exception:
            pass

    # 推荐下一步动作
    if login_required:
        next_action = "login_required_manual_intervention"
    elif daily_limit_reached:
        next_action = "daily_limit_reached"
    elif not phase1.done:
        next_action = "prepare-products"
    elif not phase2.done:
        next_action = "generate-content"
    elif not phase3.plan_exists:
        next_action = "plan-publish"
    elif phase3.pending > 0:
        next_action = "run-publish-plan"
    else:
        next_action = "all_done"

    return DailyStatusResult(
        date=today,
        phase1=phase1,
        phase2=phase2,
        phase3=phase3,
        next_recommended_action=next_action,
        login_required=login_required,
    )


def build_status_payload() -> tuple[dict, int]:
    try:
        result = get_daily_status()
        return DailyStatusSuccess(data=result).model_dump(mode="json"), 0
    except Exception as exc:
        payload = SkillError(error="STATUS_FAILED", message=str(exc))
        return payload.model_dump(mode="json"), 1
```

### 3c — 在 cli.py 注册 status 子命令

**文件**：`src/xhs_poster/cli.py`

顶部 import 区域增加：
```python
from .status import build_status_payload
```

在 `app.add_typer(auth_app, name="auth")` 之前新增：
```python
@app.command("status", help="输出今日各阶段完成状态与推荐下一步动作；cron 编排层可据此决定执行哪条命令。")
def status_command() -> None:
    payload, exit_code = build_status_payload()
    emit_json(payload)
    raise typer.Exit(code=exit_code)
```

**验证**：
```bash
uv run python -m compileall src
uv run xhs-poster status
# 应输出含 phase1/phase2/phase3/next_recommended_action 的 JSON
```

---

## Task 4 — auth-state 过期时写入 phase1-state 的 blocked_reason 字段（P2）

**目标**：LOGIN_REQUIRED 时，在 `phase1-state.json` 写入可观测的告警字段，供 `status` 命令和外部轮询感知。

**文件**：`src/xhs_poster/models.py`  
**位置**：`Phase1State` 类末尾，`products` 字段之后追加：
```python
blocked_reason: str | None = None
```

**文件**：`src/xhs_poster/phase1.py`  
**位置**：`build_phase1_payload` 的 `LoginRequiredError` 分支，`return` 之前插入：
```python
try:
    _settings = Settings()
    _settings.ensure_directories()
    _state = load_phase1_state(_settings)
    _state.blocked_reason = "LOGIN_REQUIRED"
    _state.run_status = "failed"
    save_phase1_state(_settings, _state)
except Exception:
    pass
```

**验证**：
```bash
uv run python -m compileall src
```

---

## Task 5 — 修复 content_gen.py 中 provider 字段硬编码（P2）

**文件**：`src/xhs_poster/content_gen.py`

**步骤 1**：在 `_request_llm_drafts` 函数上方新增工具函数：
```python
def _infer_provider(base_url: str) -> str:
    url = base_url.lower()
    if "moonshot" in url:
        return "moonshot"
    if "openai" in url:
        return "openai"
    if "deepseek" in url:
        return "deepseek"
    if "kimi" in url:
        return "kimi"
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return host.split(".")[0] or "unknown"
    except Exception:
        return "unknown"
```

**步骤 2**：`_request_llm_drafts` 返回处，将 `provider="moonshot"` 改为：
```python
provider=_infer_provider(settings.llm_base_url),
```

**步骤 3**：`generate_product_contents` 中 `llm_fallback` 的 meta，同样将 `provider="moonshot"` 改为：
```python
provider=_infer_provider(settings.llm_base_url),
```

**验证**：
```bash
uv run python -m compileall src
```

---

## Task 6 — phase2 图片语义分析并发化（P3）

**前置检查**：先阅读 `src/xhs_poster/image_semantics.py`，确认 `analyze_product_image_semantics` 是否对 `cache_bundle` 有写操作。若有，并发访问需要加 `threading.Lock()`。

**文件**：`src/xhs_poster/phase2.py`

将串行的语义分析循环替换为：
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

semantic_bundle = load_image_semantic_facts(settings)

# 串行做本地图片事实提取（无网络 IO）
products_with_paths: list[tuple] = []
for product in today_pool.products:
    image_paths = resolve_image_paths(settings, today_pool, product.id)
    if not image_paths:
        continue
    facts = extract_product_image_facts(product, image_paths)
    image_facts.append(facts)
    facts_map[product.id] = facts
    products_with_paths.append((product, image_paths))

# 并发做视觉 LLM 语义分析（max_workers=3 控制 API 并发）
def _analyze_one(args):
    product, image_paths = args
    return product.id, analyze_product_image_semantics(
        settings,
        product_id=product.id,
        product_name=product.name,
        image_paths=image_paths,
        cache_bundle=semantic_bundle,
    )

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {executor.submit(_analyze_one, args): args[0].id for args in products_with_paths}
    for future in as_completed(futures):
        product_id, semantic_facts = future.result()
        semantic_facts_map[product_id] = semantic_facts

save_image_semantic_facts(settings, semantic_bundle)
```

**验证**：
```bash
uv run python -m compileall src
uv run xhs-poster generate-content --contents-per-product 1
```

---

## Task 7 — SKILL.md 新增 cron 无人值守模式描述（P1）

**文件**：`SKILL.md`  
**位置**：在 `## 用户意图默认动作` 小节之后插入新小节

```markdown
## Cron 无人值守模式

当本 skill 由定时任务（如 Openclaw cron）自动触发、没有用户实时交互时，优先使用
`run-daily` 单命令入口；若需要分步控制，按以下序列执行。

### run-daily 单命令入口（推荐）

```bash
uv run xhs-poster run-daily --publish-count 3
# --dry-run 可预览将执行哪些阶段而不真实操作
uv run xhs-poster run-daily --publish-count 3 --dry-run
```

### 分步序列（调试或异常处理时使用）

```bash
uv run xhs-poster status   # 查看今日状态，读取 next_recommended_action

# 根据 next_recommended_action 决定：
# login_required_manual_intervention → 停止，等待人工重新登录
# prepare-products  → uv run xhs-poster prepare-products --limit 10
# generate-content  → uv run xhs-poster generate-content --contents-per-product 5
# plan-publish      → uv run xhs-poster plan-publish
# run-publish-plan  → uv run xhs-poster run-publish-plan --count N
# all_done / daily_limit_reached → 停止
```

### exit code 处理规范

| exit code | 含义 | cron 应对策略 |
|-----------|------|---------------|
| 0 | 成功 | 继续下一步 |
| 1 | 业务错误（商品为空、LLM 失败等） | 记录日志，停止当轮，下次 cron 重试 |
| 2 | LOGIN_REQUIRED（auth-state 过期） | 停止所有后续阶段，触发告警，等待人工重新登录导出 auth-state |

### 注意事项

- cron 触发时不要根据"上次成功与否"自动重跑 phase1 或 phase2
- exit code 2 出现后当天所有后续阶段都应跳过
- `status.phase3.daily_limit_reached == true` 时直接停止，不执行任何发布命令
```

---

## Task 8 — 新增 `run-daily` 统一编排入口（P0）【来自 OPENAI】

**目标**：cron 只需调用一个命令完成完整日常流程，不再依赖 Agent 从 SKILL.md 推理阶段顺序。

### 8a — 新建 src/xhs_poster/orchestrator.py

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type

from .config import Settings
from .models import SkillError
from .phase1 import build_phase1_payload
from .phase2 import build_phase2_payload
from .phase3 import build_phase3_plan_payload, build_phase3_run_plan_payload
from .status import DAILY_PUBLISH_LIMIT, get_daily_status


@dataclass
class OrchestratorResult:
    date: str
    dry_run: bool
    phases_executed: list[str] = field(default_factory=list)
    phases_skipped: list[str] = field(default_factory=list)
    publish_count_requested: int = 0
    publish_count_succeeded: int = 0
    blocked_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    status: str = "ok"  # "ok" | "partial" | "blocked" | "error"


def run_daily(
    *,
    limit: int = 10,
    images_per_product: int = 3,
    contents_per_product: int = 5,
    keyword: str | None = None,
    publish_count: int = 3,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> OrchestratorResult:
    settings = settings or Settings()
    today = str(date_type.today())
    result = OrchestratorResult(date=today, dry_run=dry_run, publish_count_requested=publish_count)

    status = get_daily_status(settings)

    # LOGIN_REQUIRED 直接阻断
    if status.login_required:
        result.status = "blocked"
        result.blocked_reason = "LOGIN_REQUIRED：auth-state 已过期，需要人工重新登录并导出。"
        return result

    # 每日上限已达
    if status.phase3.daily_limit_reached:
        result.status = "blocked"
        result.blocked_reason = f"今日已达每日发布上限 {DAILY_PUBLISH_LIMIT} 篇。"
        result.phases_skipped = ["prepare-products", "generate-content", "plan-publish", "run-publish-plan"]
        return result

    # Phase1
    if not status.phase1.done:
        if dry_run:
            result.phases_skipped.append("prepare-products[dry-run]")
        else:
            payload, exit_code = build_phase1_payload(
                limit=limit,
                images_per_product=images_per_product,
            )
            result.phases_executed.append("prepare-products")
            if exit_code != 0:
                result.errors.append(f"prepare-products failed: {payload.get('message', '')}")
                result.status = "error"
                return result
            # 刷新状态
            status = get_daily_status(settings)
    else:
        result.phases_skipped.append("prepare-products")

    # Phase2
    if not status.phase2.done:
        if dry_run:
            result.phases_skipped.append("generate-content[dry-run]")
        else:
            payload, exit_code = build_phase2_payload(
                keyword=keyword,
                contents_per_product=contents_per_product,
            )
            result.phases_executed.append("generate-content")
            if exit_code != 0:
                result.errors.append(f"generate-content failed: {payload.get('message', '')}")
                result.status = "error"
                return result
            status = get_daily_status(settings)
    else:
        result.phases_skipped.append("generate-content")

    # Phase3 plan
    if not status.phase3.plan_exists:
        if dry_run:
            result.phases_skipped.append("plan-publish[dry-run]")
        else:
            payload, exit_code = build_phase3_plan_payload(
                mode="sequential",
                count=None,
                dedupe_scope="today",
            )
            result.phases_executed.append("plan-publish")
            if exit_code != 0:
                result.errors.append(f"plan-publish failed: {payload.get('message', '')}")
                result.status = "error"
                return result
    else:
        result.phases_skipped.append("plan-publish")

    # Phase3 run
    if dry_run:
        result.phases_skipped.append(f"run-publish-plan[dry-run, count={publish_count}]")
        result.status = "ok"
        return result

    payload, exit_code = build_phase3_run_plan_payload(
        mode="sequential",
        count=publish_count,
        dedupe_scope="today",
    )
    result.phases_executed.append("run-publish-plan")
    if isinstance(payload.get("data"), dict):
        result.publish_count_succeeded = payload["data"].get("count_succeeded", 0)
    result.status = "ok" if exit_code == 0 else "partial"
    return result


def build_run_daily_payload(
    *,
    limit: int = 10,
    images_per_product: int = 3,
    contents_per_product: int = 5,
    keyword: str | None = None,
    publish_count: int = 3,
    dry_run: bool = False,
) -> tuple[dict, int]:
    try:
        result = run_daily(
            limit=limit,
            images_per_product=images_per_product,
            contents_per_product=contents_per_product,
            keyword=keyword,
            publish_count=publish_count,
            dry_run=dry_run,
        )
        from dataclasses import asdict
        exit_code = 0 if result.status in ("ok", "blocked") else 1
        return {"status": result.status, "data": asdict(result)}, exit_code
    except Exception as exc:
        payload = SkillError(error="RUN_DAILY_FAILED", message=str(exc))
        return payload.model_dump(mode="json"), 1
```

### 8b — 在 cli.py 注册 run-daily 子命令

**文件**：`src/xhs_poster/cli.py`

顶部 import 区域增加：
```python
from .orchestrator import build_run_daily_payload
```

新增命令（在 `app.add_typer` 之前）：
```python
@app.command("run-daily", help="自动检查并补跑今日缺失阶段，按顺序完成 prepare-products → generate-content → plan-publish → run-publish-plan；cron 推荐入口。")
def run_daily_command(
    limit: Annotated[int, typer.Option("--limit", help="目标商品数量")] = 10,
    images_per_product: Annotated[int, typer.Option("--images-per-product")] = 3,
    contents_per_product: Annotated[int, typer.Option("--contents-per-product")] = 5,
    keyword: Annotated[str | None, typer.Option("--keyword")] = None,
    publish_count: Annotated[int, typer.Option("--publish-count", help="本次发布篇数")] = 3,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="预览将执行的阶段，不做真实操作")] = False,
) -> None:
    payload, exit_code = build_run_daily_payload(
        limit=limit,
        images_per_product=images_per_product,
        contents_per_product=contents_per_product,
        keyword=keyword,
        publish_count=publish_count,
        dry_run=dry_run,
    )
    emit_json(payload)
    raise typer.Exit(code=exit_code)
```

**验证**：
```bash
uv run python -m compileall src
uv run xhs-poster run-daily --dry-run
# 应输出今日将执行/跳过的阶段列表，不做真实操作
```

---

## Task 9 — 消除 .env 对 cwd 的强依赖（P1）【来自 OPENAI】

**问题**：`config.py` 中 `env_file=".env"` 是相对路径，依赖进程 cwd 为仓库根目录。cron 的工作目录通常不是仓库根目录，导致 `.env` 找不到、API Key 静默为 `None`。

**文件**：`src/xhs_poster/config.py`

**步骤 1**：在 `PROJECT_ROOT = Path(__file__).resolve().parents[2]` 之后，增加：
```python
import os as _os
_ENV_FILE = _os.environ.get("XHS_POSTER_ENV_FILE", str(PROJECT_ROOT / ".env"))
```

**步骤 2**：将 `SettingsConfigDict` 中的 `env_file=".env"` 改为：
```python
env_file=_ENV_FILE,
```

这样，cron 中可以通过环境变量显式指定 `.env` 路径：
```bash
XHS_POSTER_ENV_FILE=/path/to/repo/.env uv run xhs-poster run-daily --publish-count 3
```

也可以不设置任何变量，默认自动使用仓库根目录的 `.env`，与现有行为一致（但不再依赖 cwd）。

**验证**：
```bash
uv run python -m compileall src
# 在非仓库根目录下测试：
cd /tmp && XHS_POSTER_ENV_FILE=/path/to/repo/.env uv run --project /path/to/repo xhs-poster status
```

---

## Task 10 — 引入运行锁，防止 cron 并发执行（P1）【来自 OPENAI + GEMINI】

**问题**：两次 cron 触发时间相近时，可能出现并发执行，导致 `today-pool.json`、`contents.json` 等文件被并发写坏。

**方案**：实现基于 PID 文件的轻量级进程锁，不引入外部依赖。

### 10a — 新建 src/xhs_poster/runlock.py

```python
from __future__ import annotations

import os
from pathlib import Path


class RunLockError(Exception):
    """另一个 xhs-poster 进程正在运行。"""


class RunLock:
    """基于 PID 文件的轻量级进程锁。"""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._acquired = False

    def acquire(self) -> None:
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text().strip())
                # 检查 PID 是否仍在运行
                os.kill(pid, 0)
                raise RunLockError(
                    f"另一个 xhs-poster 进程（PID {pid}）正在运行，"
                    f"锁文件：{self.lock_path}。"
                    "若确认进程已结束，请手动删除锁文件后重试。"
                )
            except (ValueError, ProcessLookupError):
                # PID 文件内容异常或进程已结束，视为过期锁，覆盖
                pass

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(os.getpid()))
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                pass
            self._acquired = False

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *_) -> None:
        self.release()
```

### 10b — 在 config.py 新增 lock_path 属性

**文件**：`src/xhs_poster/config.py`，在 `Settings` 类的属性方法中追加：
```python
@property
def run_lock_path(self) -> Path:
    return self.data_dir / ".xhs-poster.lock"
```

### 10c — 在 orchestrator.py 的 run_daily 中应用锁

**文件**：`src/xhs_poster/orchestrator.py`

在 `run_daily` 函数开始处加锁：
```python
from .runlock import RunLock, RunLockError

# run_daily 函数开头，settings 初始化之后
with RunLock(settings.run_lock_path):
    # 将现有函数体全部缩进到此 with 块内
    ...
```

**验证**：
```bash
uv run python -m compileall src
```

---

## Task 11 — 清理 generate-content 的未实现参数（P2）【来自 OPENAI】

**问题**：`generate-content` 有 `--search-limit` 和 `--detail-limit` 两个参数，在 `phase2.py` 的 `build_phase2_outputs` 中被 `del` 掉，实际没有任何效果。这对 Agent 来说是误导性噪音。

**文件**：`src/xhs_poster/cli.py`  
**位置**：`generate_content_command` 函数

删除以下两个参数定义：
```python
search_limit: Annotated[int, typer.Option("--search-limit", help="（预留，当前未使用）")] = 20,
detail_limit: Annotated[int, typer.Option("--detail-limit", help="（预留，当前未使用）")] = 8,
```

同时删除调用处的对应参数：
```python
search_limit=search_limit,
detail_limit=detail_limit,
```

**文件**：`src/xhs_poster/phase2.py`  
**位置**：`build_phase2_outputs` 和 `build_phase2_payload` 函数签名

删除 `search_limit` 和 `detail_limit` 参数及其函数体内的 `del search_limit` / `del detail_limit` 语句。

**验证**：
```bash
uv run python -m compileall src
uv run xhs-poster generate-content --help
# 确认 --search-limit 和 --detail-limit 已消失
```

---

## Task 12 — 新增 skill-contract.md 契约文档（P2）【来自 OPENAI】

**目标**：建立一份明确的 CLI 契约，列出每个命令的参数状态、适用场景和 exit code，让 Agent 无需阅读源码即可理解工具边界。

**新建文件**：`docs/skill-contract.md`（如 `docs/` 目录不存在则创建）

文件内容包含以下几个表格：

**命令能力矩阵**：

| 命令 | 适用场景 | 需要登录态 | cron 可用 | exit code 0 | exit code 1 | exit code 2 |
|------|----------|-----------|-----------|-------------|-------------|-------------|
| `status` | 查询 | 否 | 是 | 状态已获取 | 读取失败 | - |
| `run-daily` | 编排 | 是（phase1/3） | 是 | 全部成功 | 部分失败 | LOGIN_REQUIRED |
| `prepare-products` | phase1 | 是 | 是（headless） | 有成功商品 | 0个成功 | LOGIN_REQUIRED |
| `generate-content` | phase2 | 否 | 是 | 成功 | 失败 | - |
| `prepare-trends` | 可选 | 否 | 是 | 成功 | 失败 | - |
| `plan-publish` | phase3 | 否 | 是 | 成功 | 失败 | - |
| `run-publish-plan` | phase3 | 是 | 是 | 全部成功 | 有失败 | LOGIN_REQUIRED |
| `list-publish-candidates` | 查询 | 否 | 是 | 成功 | 失败 | - |
| `publish-note` | 调试 | 是 | 否 | 成功 | 失败 | LOGIN_REQUIRED |
| `login merchant` | 登录 | - | 否（需前台） | 成功 | - | 未完成 |
| `auth probe` | 登录检查 | - | 是 | 已登录 | - | 未登录 |
| `auth export` | 迁移 | 是 | 否（需前台） | 成功 | - | 未登录 |
| `auth import` | 迁移 | - | 是 | 成功 | - | 校验失败 |

**参数状态表**（标注已实现 / 废弃）根据清理后的实际参数填写。

**验证**：文件创建后人工审查内容是否与实际代码一致。

---

## Task 13 — Webhook 告警（auth 过期时主动通知）（P3）【来自 GEMINI】

**目标**：auth-state 过期导致 LOGIN_REQUIRED 时，主动推送告警到钉钉/企微，而不是仅写文件等待人工发现。

**方案**：通过环境变量配置 Webhook URL，在 `build_phase1_payload` 的 `LoginRequiredError` 分支中异步触发。

**文件**：`src/xhs_poster/config.py`  
在 `Settings` 类增加字段：
```python
alert_webhook_url: str | None = Field(
    default=None,
    validation_alias=AliasChoices("XHS_POSTER_ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_URL"),
    description="告警 Webhook URL（钉钉/企微格式），auth 过期时自动推送。",
)
```

**文件**：`src/xhs_poster/phase1.py`  
在 `LoginRequiredError` 分支写入 `phase1-state` 之后，新增告警发送：
```python
try:
    _settings_for_alert = Settings()
    if _settings_for_alert.alert_webhook_url:
        import httpx as _httpx
        _httpx.post(
            _settings_for_alert.alert_webhook_url,
            json={"msgtype": "text", "text": {"content": "[xhs-poster] 商家端登录态已过期，请重新登录并导出 auth-state。"}},
            timeout=5.0,
        )
except Exception:
    pass
```

**验证**：
```bash
uv run python -m compileall src
# 配置测试 Webhook URL 后触发 LOGIN_REQUIRED 验证推送
```

---

## Task 14 — Dockerfile 与 crontab.example（P3）【来自 GEMINI】

**目标**：标准化云服务器部署，降低环境配置门槛。

### 14a — 新建 Dockerfile

```dockerfile
FROM python:3.13-slim

# 安装 Playwright 系统依赖
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

RUN pip install uv && uv sync --frozen
RUN uv run playwright install chromium --with-deps

COPY . .

ENV XHS_POSTER_ENV_FILE=/app/.env

CMD ["uv", "run", "xhs-poster", "--help"]
```

### 14b — 新建 crontab.example

```cron
# xhs-poster 推荐 cron 配置示例
# 编辑方式：crontab -e

# 环境变量（按实际路径修改）
REPO=/path/to/skill-xhs-poster
LOG_DIR=/var/log/xhs-poster

# 每天 09:00 执行完整日常流程（发布 3 篇）
0 9 * * * cd $REPO && XHS_POSTER_ENV_FILE=$REPO/.env uv run xhs-poster run-daily --publish-count 3 >> $LOG_DIR/run-daily.log 2>&1

# 每天 15:00 补充发布（发布 2 篇，幂等：今日已发满则跳过）
0 15 * * * cd $REPO && XHS_POSTER_ENV_FILE=$REPO/.env uv run xhs-poster run-daily --publish-count 2 >> $LOG_DIR/run-daily-pm.log 2>&1

# 每天 08:50 探测登录态（exit code 2 时发告警）
50 8 * * * cd $REPO && XHS_POSTER_ENV_FILE=$REPO/.env uv run xhs-poster auth probe merchant || echo "[$(date)] auth probe failed" >> $LOG_DIR/auth-alert.log
```

---

## 执行顺序总结

| 顺序 | Task | 优先级 | 来源 | 影响范围 |
|------|------|--------|------|----------|
| 1 | Task 1：移除隐式补计划 | P0 | CLAUDE | `phase3.py` |
| 2 | Task 2：50 条发布上限硬限制 | P0 | CLAUDE | `phase3.py`, `models.py` |
| 3 | Task 8：run-daily 统一编排入口 | P0 | OPENAI | 新增 `orchestrator.py`, 改 `cli.py` |
| 4 | Task 9：消除 .env cwd 依赖 | P1 | OPENAI | `config.py` |
| 5 | Task 10：运行锁防并发 | P1 | OPENAI+GEMINI | 新增 `runlock.py`, 改 `config.py`, `orchestrator.py` |
| 6 | Task 3：新增 status 命令 | P1 | CLAUDE | 新增 `status.py`, 改 `cli.py`, `models.py` |
| 7 | Task 7：SKILL.md cron 描述 | P1 | CLAUDE | `SKILL.md` 文档 |
| 8 | Task 4：LOGIN_REQUIRED 写 blocked_reason | P2 | CLAUDE | `phase1.py`, `models.py` |
| 9 | Task 5：provider 动态化 | P2 | CLAUDE | `content_gen.py` |
| 10 | Task 11：清理未实现参数 | P2 | OPENAI | `cli.py`, `phase2.py` |
| 11 | Task 12：skill-contract.md | P2 | OPENAI | 新增文档 |
| 12 | Task 6：语义分析并发化 | P3 | CLAUDE | `phase2.py`（需先确认线程安全） |
| 13 | Task 13：Webhook 告警 | P3 | GEMINI | `config.py`, `phase1.py` |
| 14 | Task 14：Dockerfile + crontab | P3 | GEMINI | 新增部署文件 |

每个 Task 独立，完成一个即可提交，不需要等待全部完成。
