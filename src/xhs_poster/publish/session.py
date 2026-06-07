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
from .records import append_record, build_evidence_dir, save_publish_evidence


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
    """单篇发布触发硬超时（看门狗强制中止），区别于普通单篇失败。"""


@contextmanager
def _item_deadline(seconds: float) -> Iterator[None]:
    """给一段发布操作套硬超时（SIGALRM 看门狗）：超过 ``seconds`` 抛 ``PublishItemTimeout``。

    存在的意义：Playwright sync 调用各自有默认超时，但浏览器被 OOM 杀掉后，下一次 sync 调用会卡在
    死掉的 CDP 管道上**永久阻塞**（线上实测可达数小时），任何 per-call timeout 都救不了。SIGALRM 在
    C 层 read 上以 EINTR 打断阻塞调用，是唯一可靠的兜底。

    用 ``setitimer`` 设初始 + 5s 重复触发：首次打断后异常会沿 ``publish_one`` 的 finally（``popup.close`` /
    ``tracing.stop``）回溯，而这些清理在死浏览器上同样会再次永久阻塞——重复触发确保每次阻塞都被打断，
    直到异常冒出函数体。仅在主线程可用（CLI 发布走主线程）；非主线程或无 SIGALRM 的平台退化为无超时。
    """
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _handler(_signum: int, _frame: object) -> None:
        raise PublishItemTimeout(f"单篇发布超过 {seconds:.0f}s 硬超时，已强制中止该篇")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds, 5.0)
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

    def maybe_recycle(self) -> None:
        recycle_every = self.settings.publish_session_recycle_every
        if recycle_every > 0 and self._since_recycle >= recycle_every:
            self._close_context()
            self._open_context()

    def detect_login_lost(self) -> bool:
        """判断是否**确凿掉登录**（区别于普通单篇失败 / 页面崩溃）。

        只有列表页仍存活、却已被重定向到登录态以外（customer/login/非 ark 鉴权页）时返回 True。
        页面已关闭/读取异常时返回 False——那是页面健康问题，交给 ``ensure_list_page_healthy`` 自愈，
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

    def ensure_list_page_healthy(self) -> None:
        """进下一篇前自愈：列表页不健康（页面崩/跳走）就重建会话，避免一篇坏页拖垮整批。

        与 ``maybe_recycle`` 正交——recycle 是"到点重建"，本方法是"坏了才重建"，两者幂等可共存。
        """
        if self.list_page_is_healthy():
            return
        log_summary("列表页异常，重建会话以自愈后继续发布")
        self._close_context()
        self._open_context()

    def force_rebuild(self) -> None:
        """无条件重建会话：单篇硬超时后浏览器状态未知（可能已被 OOM 杀掉/卡死），
        不能依赖 ``ensure_list_page_healthy`` 的健康探测（探测本身会在死浏览器上再次卡住）。
        """
        self._close_context()
        self._open_context()

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
        steps: list[dict] = []
        evidence_dir: Path | None = None

        def capture_evidence() -> dict:
            nonlocal evidence_dir
            evidence_dir = build_evidence_dir(
                settings, record_date=publish_date, product_id=product.id, angle=record_angle
            )
            return save_publish_evidence(publish_page, evidence_dir, steps=steps)

        # trace 是 context 级，每篇 start/stop 一份；默认仅失败保留、verbose 全留，成功非 verbose 丢弃。
        tracing = self._context.tracing if self._context is not None else None
        if tracing is not None:
            tracing.start(screenshots=True, snapshots=True, sources=True)

        popup = self._list_page.open_publish_popup(product.id)
        publish_page = PublishPage(popup, settings)
        self._since_recycle += 1
        try:
            try:
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
                artifacts = None
                if not publish_result.get("success") or self.verbose:
                    artifacts = capture_evidence()
            except Exception as exc:
                artifacts = capture_evidence()
                raise RuntimeError(f"{exc} artifacts={json.dumps(artifacts, ensure_ascii=False)}") from exc
        finally:
            if tracing is not None:
                try:
                    if evidence_dir is not None:
                        tracing.stop(path=str(evidence_dir / "trace.zip"))
                    else:
                        tracing.stop()
                except Exception:
                    pass
            if not popup.is_closed():
                try:
                    popup.close(run_before_unload=False)
                except Exception:
                    pass

        skipped_topics = extract_skipped_topics(topic_results)
        result = PublishExecutionResult(
            product_id=product.id,
            product_name=product.name,
            title=final_title,
            content=final_content,
            topic_keywords=final_topics,
            angle=draft.angle if draft else angle,
            angle_name=draft.angle_name if draft else None,
            image_paths=final_image_paths,
            title_selector=title_selector,
            content_selector=content_selector,
            topic_results=topic_results,
            skipped_topics=skipped_topics,
            product_binding=product_binding,
            publish_result=publish_result,
            artifacts=artifacts,
        )
        result.log_path = append_record(
            settings,
            record_date=publish_date,
            record=PublishRecord(
                attempted_at=datetime.now().isoformat(),
                product_id=result.product_id,
                product_name=result.product_name,
                angle=result.angle or 0,
                angle_name=result.angle_name,
                title=result.title,
                topic_keywords=result.topic_keywords,
                skipped_topics=result.skipped_topics,
                status="success" if publish_result.get("success") else "failed",
                dedupe_key=f"{publish_date}:{result.product_id}:{result.angle or 0}",
                publish_result=result.publish_result,
                artifacts=result.artifacts,
            ),
        )
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
                    session.maybe_recycle()
                    session.ensure_list_page_healthy()
                    _sleep_interval(settings)
                def _record_failure(error: str) -> None:
                    item.status = "failed"
                    item.error = error
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

                try:
                    # 单篇硬超时看门狗：超时即抛 PublishItemTimeout，避免单篇卡死（典型为浏览器被 OOM
                    # 杀掉后 sync 调用永久阻塞）把整批拖成数小时僵尸。
                    with _item_deadline(settings.publish_item_timeout_seconds):
                        execution_result = session.publish_one(
                            product_id=item.product_id,
                            angle=item.angle,
                        )
                    publish_succeeded = bool(execution_result.publish_result.get("success"))
                    if publish_succeeded:
                        item.status = "published"
                        item.published_at = datetime.now().isoformat()
                        item.error = None
                    else:
                        item.status = "failed"
                        item.error = json.dumps(execution_result.publish_result, ensure_ascii=False)
                    save_publish_plan(settings, plan)
                    results.append(
                        PublishRunItemResult(
                            product_id=item.product_id,
                            product_name=item.product_name,
                            angle=item.angle,
                            angle_name=item.angle_name,
                            status="success" if publish_succeeded else "failed",
                            execution_result=execution_result,
                            error=None if publish_succeeded else item.error,
                        )
                    )
                except PublishItemTimeout as exc:
                    # 超时后浏览器状态未知（可能已 OOM/卡死），强制重建会话再继续下一篇；
                    # 不走 detect_login_lost（它会读列表页，在死浏览器上同样会卡住）。
                    log_summary(str(exc))
                    _record_failure(str(exc))
                    try:
                        with _item_deadline(settings.publish_item_timeout_seconds):
                            session.force_rebuild()
                    except PublishItemTimeout:
                        # 连重建都卡死说明环境已不可用，整批中止（冒泡到 cli → exit 1）；
                        # systemd 的 TimeoutStartSec + OnFailure 是最后兜底。
                        log_summary("单篇超时后重建会话仍卡住，中止整批发布")
                        raise
                except Exception as exc:
                    _record_failure(str(exc))
                    # 区分"掉登录"与"普通单篇失败"：掉登录是全局前提崩了，继续发只会篇篇失败
                    # 且在小红书侧刷异常行为——立刻整批中止（冒泡到 cli → exit 2）。已发成功篇不回滚，
                    # plan/records 保持续传可恢复态。普通失败则不中止，下一篇照常尝试。
                    if session.detect_login_lost():
                        log_summary("检测到商家端登录态失效，中止整批发布")
                        raise session.login_lost_error() from exc

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
