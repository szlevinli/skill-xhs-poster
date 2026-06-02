from __future__ import annotations

import json
from datetime import datetime

from playwright.sync_api import sync_playwright

from ..auth import require_authenticated_session
from ..browser import (
    close_context_safely,
    get_alive_page,
    launch_merchant_context,
    open_product_list_page,
)
from ..config import Settings
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
from .records import append_record, save_publish_evidence


class PublishSession:
    """整批发布共享的一个浏览器会话：浏览器/列表页只初始化一次，每篇在独立 tab 中发布。

    通过 ``settings.publish_session_recycle_every`` 控制每发 N 篇后重建会话，
    平衡提速与风控（很大=整批一会话，1=每篇一会话）。
    """

    def __init__(self, settings: Settings, *, headless: bool | None = None):
        self.settings = settings
        session = require_authenticated_session(settings)
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

        popup = self._list_page.open_publish_popup(product.id)
        publish_page = PublishPage(popup, settings)
        self._since_recycle += 1
        try:
            try:
                publish_page.upload_images(final_image_paths)
                title_selector = publish_page.fill_title(final_title)
                content_selector = publish_page.fill_content(final_content)
                topic_results = [publish_page.add_topic(topic_keyword) for topic_keyword in final_topics]
                product_binding = publish_page.add_product(product.id)
                publish_page.click_publish()
                publish_result = publish_page.verify_success()
                artifacts = None
                if not publish_result.get("success"):
                    artifacts = save_publish_evidence(
                        publish_page, settings, record_date=publish_date, product_id=product.id
                    )
            except Exception as exc:
                artifacts = save_publish_evidence(
                    publish_page, settings, record_date=publish_date, product_id=product.id
                )
                raise RuntimeError(f"{exc} artifacts={json.dumps(artifacts, ensure_ascii=False)}") from exc
        finally:
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


def run_publish_plan(
    *,
    mode: PublishPlanMode,
    count: int,
    settings: Settings | None = None,
    date: str | None = None,
    dedupe_scope: PublishDedupScope = "today",
    seed: int | None = None,
    headless: bool | None = None,
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
        with PublishSession(settings, headless=headless) as session:
            for index, item in enumerate(pending_items):
                if index > 0:
                    session.maybe_recycle()
                try:
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
                except Exception as exc:
                    item.status = "failed"
                    item.error = str(exc)
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
                            error=str(exc),
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
                            error=str(exc),
                        )
                    )

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
