from __future__ import annotations

import json
import random
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..auth import (
    LoginRequiredError,
    is_session_page_authenticated,
    require_authenticated_session,
)
from ..browser import (
    close_context_safely,
    get_alive_page,
    is_ready_list_page,
    launch_merchant_context,
    open_product_list_page,
)
from ..config import Settings
from ..logging import log_step, log_summary
from ..merchant import ProductListPage
from ..models import (
    AuthSource,
    PublishExecutionResult,
    PublishDedupScope,
    PublishPlanMode,
    PublishRecord,
    PublishRunItemResult,
    PublishRunResult,
)
from .page import PublishPage
from .plan import (
    build_publish_plan,
    extract_skipped_topics,
    load_publish_plan,
    load_today_pool,
    reconcile_publish_plan_with_records,
    resolve_image_paths,
    resolve_product,
    resolve_publish_inputs,
    save_publish_plan,
)
from .records import append_record, build_evidence_dir, capture_page_evidence, save_steps_evidence


@contextmanager
def _step(name: str, steps: list[dict], *, verbose: bool) -> Iterator[None]:
    """给单步发布操作计时并记录 ``{step, status, elapsed_ms}``，verbose 时实时打日志。"""
    start = time.monotonic()
    status = "success"
    try:
        yield
    except Exception:
        status = "failed"
        raise
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        steps.append({"step": name, "status": status, "elapsed_ms": elapsed_ms})
        log_step(f"[publish] {name} {status} {elapsed_ms}ms", verbose=verbose)


class PublishItemTimeout(Exception):
    """某段发布操作触发硬超时（看门狗强制中止），区别于普通失败。"""


class PublishItemError(Exception):
    """单篇发布已失败**且已记账+留证**：编排层据此只更新计划/结果、不重复记账。

    ``artifacts`` 携带本篇证据目录信息（供日志/排查）；``recorded`` 恒为真，提醒编排层不要再 append。
    """

    def __init__(self, message: str, *, artifacts: dict | None = None) -> None:
        super().__init__(message)
        self.artifacts = artifacts
        self.recorded = True


def _format_exc(exc: BaseException) -> str:
    """异常压成单行原因串（带类型名），用于记账 error 字段与 meta.reason。"""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _can_use_alarm() -> bool:
    return hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()


@contextmanager
def _watchdog(seconds: float, message: str) -> Iterator[None]:
    """给一段操作套**单次触发**的硬超时（SIGALRM）：超过 ``seconds`` 抛 ``PublishItemTimeout``。

    存在的意义：Playwright sync 调用各自有默认超时，但浏览器被 OOM 杀掉后，下一次 sync 调用会卡在
    死掉的 CDP 管道上**永久阻塞**（线上实测可达数小时），任何 per-call timeout 都救不了。SIGALRM 在
    C 层 read 上以 EINTR 打断阻塞调用，是唯一可靠的兜底。

    **单次触发（无重复间隔）**：早先用重复触发是为了打断回溯路径上的清理，但那会把「失败后采集证据」
    也一并打断、导致超时失败留不下现场。改为单次后，调用方必须把「证据采集 / 清理 / 重建」等各步**各自**
    用独立的 ``_watchdog`` 串行包裹（彼此不嵌套），从而每步都有界、又互不打断。仅主线程可用；
    非主线程或无 SIGALRM 的平台退化为无超时。
    """
    if seconds <= 0 or not _can_use_alarm():
        yield
        return

    def _handler(_signum: int, _frame: object) -> None:
        raise PublishItemTimeout(message)

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


class PublishSession:
    """整批发布共享的一个浏览器会话：浏览器/列表页只初始化一次，每篇在独立 tab 中发布。

    通过 ``settings.publish_session_recycle_every`` 控制每发 N 篇后重建会话，
    平衡提速与风控（很大=整批一会话，1=每篇一会话）。
    """

    def __init__(self, settings: Settings, *, headless: bool | None = None, verbose: bool = False):
        self.settings = settings
        self.verbose = verbose
        session = require_authenticated_session(settings)
        self.session_info = session
        self.headless = session.browser_mode == "headless" if headless is None else headless
        self.auth_source: AuthSource = session.auth_source
        self._playwright = None
        self._context = None
        self._list_page: ProductListPage | None = None
        self._today_pool = None
        self._since_recycle = 0

    def __enter__(self) -> "PublishSession":
        self._playwright = sync_playwright().start()
        self._today_pool = load_today_pool(self.settings)
        self._open_context()
        return self

    def __exit__(self, *_exc) -> None:
        self._close_context()
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _open_context(self) -> None:
        assert self._playwright is not None
        context, _ = launch_merchant_context(
            self._playwright,
            self.settings,
            headless=self.headless,
            auth_source=self.auth_source,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page = open_product_list_page(context, page, self.settings)
        self._context = context
        self._list_page = ProductListPage(page, self.settings)
        self._since_recycle = 0

    def _close_context(self) -> None:
        if self._context is not None:
            try:
                close_context_safely(self._context)
            finally:
                self._context = None
                self._list_page = None

    def detect_login_lost(self) -> bool:
        """判断是否**确凿掉登录**（区别于普通单篇失败 / 页面崩溃）。

        只有列表页仍存活、却已被重定向到登录态以外（customer/login/非 ark 鉴权页）时返回 True。
        页面已关闭/读取异常时返回 False——那是页面健康问题，交给 ``ensure_ready_for_next`` 自愈，
        不能误判成掉登录把整批中止。正常单篇失败时列表页仍停在 app-item/list，也返回 False。
        """
        if self._list_page is None:
            return False
        page = self._list_page.page
        if page.is_closed():
            return False
        return not is_session_page_authenticated(page)

    def login_lost_error(self) -> LoginRequiredError:
        """构造一个携带"批中途掉登录"信息的 LoginRequiredError，供编排层冒泡到 cli → exit 2。"""
        session = self.session_info.model_copy(
            update={
                "status": "login_required",
                "authenticated": False,
                "message": "整批发布中途检测到商家端登录态失效，已中止后续发布；请重新登录或导入 auth-state 后续传。",
            }
        )
        return LoginRequiredError(session)

    def list_page_is_healthy(self) -> bool:
        """常驻列表页是否仍可用于下一篇 ``open_publish_popup``（存活且停在就绪的列表页）。"""
        if self._context is None or self._list_page is None:
            return False
        page = self._list_page.page
        if page.is_closed():
            return False
        try:
            return is_ready_list_page(page)
        except Exception:
            return False

    def detect_login_lost_bounded(self) -> bool:
        """带超时的掉登录探测：读列表页可能在卡死浏览器上阻塞，故套短看门狗；
        探测超时/异常一律返回 False（不确定就**不**中止整批，宁可继续发到自然结束）。
        """
        recovery = self.settings.publish_recovery_timeout_seconds
        try:
            with _watchdog(recovery, "掉登录探测超时"):
                return self.detect_login_lost()
        except Exception:
            return False

    def force_rebuild_bounded(self) -> bool:
        """无条件重建会话，关闭与重开各套短看门狗，**绝不抛**。返回是否重建出可用列表页。

        单篇失败后浏览器状态未知（可能已 OOM/卡死），不能依赖健康探测（探测本身会在死浏览器上卡住）。
        即便重建失败也不抛——交给下一篇在自己的看门狗下快速失败并记账，绝不因重建失败中止整批。
        """
        recovery = self.settings.publish_recovery_timeout_seconds
        try:
            with _watchdog(recovery, "关闭会话超时"):
                self._close_context()
        except Exception as exc:
            # 关闭卡住：丢弃引用，让 playwright 资源随进程回收，别拦住重开
            log_summary(f"关闭会话异常（已跳过）：{_format_exc(exc)}")
            self._context = None
            self._list_page = None
        try:
            with _watchdog(recovery, "重建会话超时"):
                self._open_context()
            return True
        except Exception as exc:
            log_summary(f"重建会话失败（下一篇将重试）：{_format_exc(exc)}")
            return False

    def ensure_ready_for_next(self) -> None:
        """进下一篇前确保会话可用：到点回收 / 列表页不健康都重建，全程有界、**绝不抛**。

        与旧的 ``maybe_recycle`` + ``ensure_list_page_healthy`` 合一，且把健康探测也套了看门狗——
        探测在死浏览器上会卡住，不能让"自愈"动作本身把整批拖死。
        """
        recycle_every = self.settings.publish_session_recycle_every
        if recycle_every > 0 and self._since_recycle >= recycle_every:
            self.force_rebuild_bounded()
            return
        recovery = self.settings.publish_recovery_timeout_seconds
        try:
            with _watchdog(recovery, "列表页健康探测超时"):
                healthy = self.list_page_is_healthy()
        except Exception:
            healthy = False
        if not healthy:
            log_summary("列表页异常，重建会话以自愈后继续发布")
            self.force_rebuild_bounded()

    def publish_one(
        self,
        *,
        product_id: str | None = None,
        angle: int | None = None,
        title: str | None = None,
        content: str | None = None,
        topic_keywords: list[str] | None = None,
        image_paths: list[str] | None = None,
    ) -> PublishExecutionResult:
        settings = self.settings
        assert self._list_page is not None and self._today_pool is not None
        publish_date = datetime.now().date().isoformat()
        product = resolve_product(self._today_pool, product_id)
        final_title, final_content, final_topics, draft = resolve_publish_inputs(
            settings,
            product.id,
            publish_date=publish_date,
            title=title,
            content=content,
            topic_keywords=topic_keywords,
            angle=angle,
        )
        final_image_paths = resolve_image_paths(
            settings,
            self._today_pool,
            product.id,
            image_paths=image_paths or (draft.selected_image_paths if draft else None),
        )

        record_angle = draft.angle if draft else angle
        attempted_at = datetime.now().isoformat()
        steps: list[dict] = []
        evidence_dir: Path | None = None
        popup = None
        publish_page: PublishPage | None = None
        publish_result: dict = {}
        title_selector: str | None = None
        content_selector: str | None = None
        topic_results: list[dict] = []
        product_binding: dict = {}
        failure: Exception | None = None
        recovery = settings.publish_recovery_timeout_seconds

        # trace 是 context 级，每篇 start/stop 一份；默认仅失败保留、verbose 全留，成功非 verbose 丢弃。
        tracing = self._context.tracing if self._context is not None else None
        if tracing is not None:
            tracing.start(screenshots=True, snapshots=True, sources=True)

        # 单篇硬超时只圈住「真正发布」这段（含打开弹窗——线上正是卡在这里）。证据采集/清理另起独立的
        # 短看门狗串行兜底，这样即便发布段卡死，后续仍能在有界时间内留下现场并清理，不会被同一看门狗反复打断。
        item_timeout = settings.publish_item_timeout_seconds
        try:
            with _watchdog(item_timeout, f"单篇发布超过 {item_timeout:.0f}s 硬超时，已强制中止该篇"):
                with _step("open_publish_popup", steps, verbose=self.verbose):
                    popup = self._list_page.open_publish_popup(product.id)
                publish_page = PublishPage(popup, settings)
                self._since_recycle += 1
                with _step("upload_images", steps, verbose=self.verbose):
                    publish_page.upload_images(final_image_paths)
                with _step("fill_title", steps, verbose=self.verbose):
                    title_selector = publish_page.fill_title(final_title)
                with _step("fill_content", steps, verbose=self.verbose):
                    content_selector = publish_page.fill_content(final_content)
                with _step("add_topic", steps, verbose=self.verbose):
                    topic_results = [publish_page.add_topic(topic_keyword) for topic_keyword in final_topics]
                with _step("add_product", steps, verbose=self.verbose):
                    product_binding = publish_page.add_product(product.id)
                with _step("click_publish", steps, verbose=self.verbose):
                    publish_page.click_publish()
                with _step("verify_success", steps, verbose=self.verbose):
                    publish_result = publish_page.verify_success()
        except Exception as exc:
            failure = exc

        succeeded = failure is None and bool(publish_result.get("success"))
        reason = (
            None
            if succeeded
            else _format_exc(failure)
            if failure is not None
            else f"verify_success 未通过：{json.dumps(publish_result, ensure_ascii=False)}"
        )

        # 现场采集（失败 / 未成功 / verbose）：内存现场（steps + meta）**一定**落盘；
        # 页面现场（截图 / HTML）尽力而为，套短看门狗，卡死也只记一行 error 不再阻塞。
        artifacts: dict | None = None
        if not succeeded or self.verbose:
            evidence_dir = build_evidence_dir(
                settings, record_date=publish_date, product_id=product.id, angle=record_angle
            )
            artifacts = save_steps_evidence(
                evidence_dir,
                steps=steps,
                meta={
                    "attempted_at": attempted_at,
                    "product_id": product.id,
                    "angle": record_angle,
                    "succeeded": succeeded,
                    "reason": reason,
                    "failed_step": steps[-1]["step"] if (not succeeded and steps) else None,
                },
            )
            if publish_page is None:
                artifacts["page_evidence_error"] = "发布弹窗未打开，无页面可截图"
            else:
                try:
                    with _watchdog(recovery, "页面证据采集超时"):
                        artifacts.update(capture_page_evidence(publish_page, evidence_dir))
                except Exception as cap_exc:
                    artifacts["page_evidence_error"] = _format_exc(cap_exc)

        # 清理：停 trace（落证据目录）、关弹窗，各自短看门狗、绝不抛。
        if tracing is not None:
            try:
                with _watchdog(recovery, "停 trace 超时"):
                    if evidence_dir is not None:
                        tracing.stop(path=str(evidence_dir / "trace.zip"))
                    else:
                        tracing.stop()
            except Exception:
                pass
        if popup is not None:
            try:
                with _watchdog(recovery, "关闭弹窗超时"):
                    if not popup.is_closed():
                        popup.close(run_before_unload=False)
            except Exception:
                pass

        # 记账：成败都写一条（失败带 reason + 证据）。这是唯一记账点，编排层不再重复 append。
        log_path = append_record(
            settings,
            record_date=publish_date,
            record=PublishRecord(
                attempted_at=attempted_at,
                product_id=product.id,
                product_name=product.name,
                angle=record_angle or 0,
                angle_name=draft.angle_name if draft else None,
                title=final_title,
                topic_keywords=final_topics,
                skipped_topics=extract_skipped_topics(topic_results),
                status="success" if succeeded else "failed",
                dedupe_key=f"{publish_date}:{product.id}:{record_angle or 0}",
                error=reason,
                publish_result=publish_result,
                artifacts=artifacts,
            ),
        )

        if not succeeded:
            # 已记账 + 留证：抛 PublishItemError，编排层只更新计划/结果、**不**重复记账、**不**中止整批。
            raise PublishItemError(reason or "发布失败", artifacts=artifacts)

        assert title_selector is not None and content_selector is not None
        result = PublishExecutionResult(
            product_id=product.id,
            product_name=product.name,
            title=final_title,
            content=final_content,
            topic_keywords=final_topics,
            angle=record_angle,
            angle_name=draft.angle_name if draft else None,
            image_paths=final_image_paths,
            title_selector=title_selector,
            content_selector=content_selector,
            topic_results=topic_results,
            skipped_topics=extract_skipped_topics(topic_results),
            product_binding=product_binding,
            publish_result=publish_result,
            artifacts=artifacts,
        )
        result.log_path = log_path
        return result


def _publish_interval_seconds(settings: Settings) -> float:
    """每篇之间的随机反检测间隔（秒）。

    min 与 max 同时 <=0 → 关闭间隔，返回 0。否则在 [min, max] 内取随机值；
    若 min>max（含 min>0 而 max<=0 的写法）则 clamp 到 min，避免 random.uniform 行为反直觉。
    """
    low = settings.publish_interval_min_seconds
    high = settings.publish_interval_max_seconds
    if low <= 0 and high <= 0:
        return 0.0
    low = max(low, 0.0)
    high = max(high, low)
    return random.uniform(low, high)


def _sleep_interval(settings: Settings) -> None:
    """在两篇发布之间插入随机间隔（反检测），并打一行日志便于 journal 观察。"""
    seconds = _publish_interval_seconds(settings)
    if seconds <= 0:
        return
    log_summary(f"下一篇前等待 {seconds:.1f}s（反检测间隔）")
    time.sleep(seconds)


def run_publish_plan(
    *,
    mode: PublishPlanMode,
    count: int,
    settings: Settings | None = None,
    date: str | None = None,
    dedupe_scope: PublishDedupScope = "today",
    seed: int | None = None,
    headless: bool | None = None,
    verbose: bool = False,
) -> PublishRunResult:
    settings = settings or Settings()
    settings.ensure_directories()
    plan = load_publish_plan(settings)
    current_date = date or datetime.now().date().isoformat()
    if plan is None or plan.date != current_date:
        plan = build_publish_plan(
            mode=mode,
            count=count,
            settings=settings,
            date=current_date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
    plan = reconcile_publish_plan_with_records(settings, plan)

    pending_items = [item for item in plan.items if item.status == "pending"][:count]
    results: list[PublishRunItemResult] = []

    if pending_items:
        # 整批共享一个浏览器会话：浏览器与列表页只在 PublishSession 内初始化一次。
        with PublishSession(settings, headless=headless, verbose=verbose) as session:
            for index, item in enumerate(pending_items):
                if index > 0:
                    # 进下一篇前确保会话可用（到点回收 / 坏页重建，全程有界、绝不抛），再插反检测间隔。
                    session.ensure_ready_for_next()
                    _sleep_interval(settings)
                try:
                    execution_result = session.publish_one(
                        product_id=item.product_id,
                        angle=item.angle,
                    )
                    # publish_one 只在成功时返回；失败一律抛 PublishItemError（已自行记账+留证）。
                    item.status = "published"
                    item.published_at = datetime.now().isoformat()
                    item.error = None
                    save_publish_plan(settings, plan)
                    results.append(
                        PublishRunItemResult(
                            product_id=item.product_id,
                            product_name=item.product_name,
                            angle=item.angle,
                            angle_name=item.angle_name,
                            status="success",
                            execution_result=execution_result,
                            error=None,
                        )
                    )
                    continue
                except PublishItemError as exc:
                    # 已在 publish_one 内记账 + 留证（含超时/崩溃/未通过）：这里只更新计划与结果，不重复 append。
                    error = str(exc)
                except Exception as exc:
                    # 记账前就崩了（极少数，如 resolve 阶段 / 会话已不可用）：兜底补一条失败记录。
                    error = _format_exc(exc)
                    log_summary(f"[publish] 单篇异常（记账前，已兜底记录）：{error}")
                    append_record(
                        settings,
                        record_date=current_date,
                        record=PublishRecord(
                            attempted_at=datetime.now().isoformat(),
                            product_id=item.product_id,
                            product_name=item.product_name,
                            angle=item.angle,
                            angle_name=item.angle_name,
                            title=item.title,
                            topic_keywords=item.topic_keywords,
                            status="failed",
                            dedupe_key=f"{current_date}:{item.product_id}:{item.angle}",
                            error=error,
                        ),
                    )

                # —— 失败收尾（两类失败共用）——
                item.status = "failed"
                item.error = error
                save_publish_plan(settings, plan)
                results.append(
                    PublishRunItemResult(
                        product_id=item.product_id,
                        product_name=item.product_name,
                        angle=item.angle,
                        angle_name=item.angle_name,
                        status="failed",
                        error=error,
                    )
                )
                # 唯一允许中止整批的情形：**确凿**掉登录（带超时探测，测不出就当没掉、继续）。掉登录后
                # 继续发只会篇篇失败、且在小红书侧刷异常行为。其余任何单篇失败都继续——绝不因一篇拖垮整批；
                # 坏掉的会话由下一轮开头的 ensure_ready_for_next 重建。
                if session.detect_login_lost_bounded():
                    log_summary("检测到商家端登录态失效，中止整批发布")
                    raise session.login_lost_error()

    success_count = sum(1 for result in results if result.status == "success")
    failed_count = len(results) - success_count
    return PublishRunResult(
        date=plan.date,
        mode=plan.mode,
        dedupe_scope=plan.dedupe_scope,
        count_requested=plan.count_requested,
        count_selected=len(pending_items),
        count_attempted=len(results),
        count_succeeded=success_count,
        count_failed=failed_count,
        seed=plan.seed,
        results=results,
    )
