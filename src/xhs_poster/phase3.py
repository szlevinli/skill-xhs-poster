from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path

from .auth import LoginRequiredError, require_authenticated_session
from .browser import get_alive_page, merchant_context, open_product_list_page
from .config import Settings
from .merchant import ProductListPage
from .models import (
    ContentDraft,
    ContentsBundle,
    Phase3Candidate,
    Phase3CandidatesResult,
    Phase3CandidatesSuccess,
    Phase3DedupScope,
    Phase3ExecutionResult,
    Phase3PlanItem,
    Phase3PlanMode,
    Phase3PlanResult,
    Phase3PlanSuccess,
    Phase3PublishedLedger,
    Phase3PublishedRecord,
    Phase3RunPlanItemResult,
    Phase3RunPlanResult,
    Phase3RunPlanSuccess,
    Phase3Success,
    ProductSummary,
    SkillError,
    TodayPool,
)


def load_today_pool(settings: Settings) -> TodayPool:
    if not settings.today_pool_path.exists():
        raise RuntimeError(
            f"未找到 today-pool.json，请先执行 prepare-products：{settings.today_pool_path}"
        )
    return TodayPool.model_validate_json(settings.today_pool_path.read_text(encoding="utf-8"))


def load_contents_bundle(settings: Settings) -> ContentsBundle:
    if not settings.contents_path.exists():
        raise RuntimeError(
            f"未找到 contents.json，且本次也未显式传入标题/正文：{settings.contents_path}"
        )
    return ContentsBundle.model_validate_json(settings.contents_path.read_text(encoding="utf-8"))


def load_phase3_published_ledger(settings: Settings) -> Phase3PublishedLedger:
    path = settings.phase3_published_path
    if not path.exists():
        return Phase3PublishedLedger()
    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            records: list[Phase3PublishedRecord] = []
            for entry in payload.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                product_id = str(entry.get("product_id", "")).strip()
                angle = entry.get("angle")
                published_at = str(entry.get("published_at", "")).strip()
                if not product_id or not isinstance(angle, int):
                    continue
                try:
                    record_date = datetime.fromisoformat(published_at).date().isoformat()
                except ValueError:
                    record_date = datetime.now().date().isoformat()
                records.append(
                    Phase3PublishedRecord(
                        date=record_date,
                        published_at=published_at or datetime.now().isoformat(),
                        product_id=product_id,
                        product_name=str(entry.get("product_name", "")),
                        angle=angle,
                        angle_name=entry.get("angle_name"),
                        title=str(entry.get("title", "")),
                        topic_keywords=list(entry.get("topic_keywords", [])),
                        publish_log_path=entry.get("publish_log_path"),
                        dedupe_key=f"{record_date}:{product_id}:{angle}",
                    )
                )
            return Phase3PublishedLedger(records=records)
        return Phase3PublishedLedger.model_validate(payload)
    except Exception as exc:
        raise RuntimeError(f"phase3-published.json 结构损坏：{path}，{exc}") from exc


def _write_phase3_published_ledger(settings: Settings, ledger: Phase3PublishedLedger) -> str:
    path = settings.phase3_published_path
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return str(path)


def append_phase3_published_record(
    settings: Settings,
    result: Phase3ExecutionResult,
    *,
    date: str | None = None,
) -> str | None:
    if result.angle is None:
        return None
    if not result.publish_result.get("success"):
        return None

    published_date = date or datetime.now().date().isoformat()
    dedupe_key = f"{published_date}:{result.product_id}:{result.angle}"
    ledger = load_phase3_published_ledger(settings)
    if any(record.dedupe_key == dedupe_key for record in ledger.records):
        return str(settings.phase3_published_path)

    record = Phase3PublishedRecord(
        date=published_date,
        published_at=datetime.now().isoformat(),
        product_id=result.product_id,
        product_name=result.product_name,
        angle=result.angle,
        angle_name=result.angle_name,
        title=result.title,
        topic_keywords=result.topic_keywords,
        publish_log_path=result.log_path,
        dedupe_key=dedupe_key,
    )
    ledger.records.append(record)
    return _write_phase3_published_ledger(settings, ledger)


def resolve_product(today_pool: TodayPool, product_id: str | None) -> ProductSummary:
    if product_id is None:
        if not today_pool.products:
            raise RuntimeError("today-pool.json 中没有可用商品。")
        return today_pool.products[0]

    for product in today_pool.products:
        if product.id == product_id:
            return product
    raise RuntimeError(f"today-pool.json 中不存在商品 {product_id}。")


def resolve_image_paths(
    settings: Settings,
    today_pool: TodayPool,
    product_id: str,
    *,
    image_paths: list[str] | None = None,
    limit: int = 3,
    min_count: int = 1,
) -> list[str]:
    if image_paths:
        resolved = [str(Path(path)) for path in image_paths if Path(path).exists()]
    else:
        resolved = [
            path
            for path in today_pool.images.get(product_id, [])
            if Path(path).exists()
        ]

    if len(resolved) < limit:
        product_dir = settings.images_dir / product_id
        if product_dir.exists():
            local_files = sorted(
                [
                    str(path)
                    for path in product_dir.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ]
            )
            seen = set(resolved)
            for path in local_files:
                if path in seen:
                    continue
                resolved.append(path)
                seen.add(path)
                if len(resolved) >= limit:
                    break

    if len(resolved) < min_count:
        raise RuntimeError(
            f"商品 {product_id} 缺少可用主图，当前仅找到 {len(resolved)} 张。"
        )
    return resolved[:limit]


def pick_content_draft(
    bundle: ContentsBundle,
    product_id: str,
    *,
    angle: int | None = None,
) -> ContentDraft:
    drafts = bundle.contents.get(product_id, [])
    if not drafts:
        raise RuntimeError(f"contents.json 中不存在商品 {product_id} 的内容草稿。")

    if angle is None:
        return drafts[0]

    for draft in drafts:
        if draft.angle == angle:
            return draft
    raise RuntimeError(f"商品 {product_id} 在 contents.json 中不存在 angle={angle} 的内容草稿。")


def extract_topic_keywords(tags: str) -> list[str]:
    keywords: list[str] = []
    for match in re.findall(r"#([^\s#]+)", tags or ""):
        normalized = match.strip()
        if normalized and normalized not in keywords:
            keywords.append(normalized)
    return keywords


def resolve_publish_inputs(
    settings: Settings,
    product_id: str,
    *,
    title: str | None,
    content: str | None,
    topic_keywords: list[str] | None,
    angle: int | None,
) -> tuple[str, str, list[str], ContentDraft | None]:
    if title or content:
        if not title or not content:
            raise RuntimeError("显式传参发布时，`title` 和 `content` 必须同时提供。")
        return title, content, topic_keywords or [], None

    draft = pick_content_draft(load_contents_bundle(settings), product_id, angle=angle)
    resolved_topics = topic_keywords or extract_topic_keywords(draft.tags)
    return draft.title, draft.content.strip(), resolved_topics, draft


def _build_success_dedupe_sets(
    ledger: Phase3PublishedLedger,
    *,
    date: str,
) -> tuple[set[str], set[str]]:
    today_keys: set[str] = set()
    ever_keys: set[str] = set()
    for record in ledger.records:
        if record.status != "success":
            continue
        key = f"{record.product_id}:{record.angle}"
        ever_keys.add(key)
        if record.date == date:
            today_keys.add(key)
    return today_keys, ever_keys


def list_phase3_candidates(
    *,
    settings: Settings | None = None,
    date: str | None = None,
    exclude_published: Phase3DedupScope = "today",
) -> Phase3CandidatesResult:
    settings = settings or Settings()
    settings.ensure_directories()
    current_date = date or datetime.now().date().isoformat()
    today_pool = load_today_pool(settings)
    contents_bundle = load_contents_bundle(settings)
    ledger = load_phase3_published_ledger(settings)
    published_today, published_ever = _build_success_dedupe_sets(ledger, date=current_date)
    product_names = {product.id: product.name for product in today_pool.products}

    candidates: list[Phase3Candidate] = []
    for product_id in sorted(contents_bundle.contents):
        if product_id not in product_names:
            continue
        drafts = sorted(contents_bundle.contents[product_id], key=lambda draft: draft.angle)
        for draft in drafts:
            dedupe_key = f"{product_id}:{draft.angle}"
            candidate = Phase3Candidate(
                date=current_date,
                product_id=product_id,
                product_name=product_names[product_id],
                angle=draft.angle,
                angle_name=draft.angle_name,
                title=draft.title,
                topic_keywords=extract_topic_keywords(draft.tags),
                published_today=dedupe_key in published_today,
                published_ever=dedupe_key in published_ever,
            )
            try:
                candidate.image_count = len(
                    resolve_image_paths(settings, today_pool, product_id, limit=3)
                )
            except RuntimeError as exc:
                candidate.image_count = 0
                candidate.eligible = False
                candidate.ineligible_reason = str(exc)
            else:
                if exclude_published == "today" and candidate.published_today:
                    candidate.eligible = False
                    candidate.ineligible_reason = "该商品 angle 今日已发布"
                elif exclude_published == "ever" and candidate.published_ever:
                    candidate.eligible = False
                    candidate.ineligible_reason = "该商品 angle 历史已发布"
            candidates.append(candidate)

    return Phase3CandidatesResult(
        date=current_date,
        exclude_published=exclude_published,
        candidates=candidates,
    )


def build_phase3_plan(
    *,
    mode: Phase3PlanMode,
    count: int,
    settings: Settings | None = None,
    date: str | None = None,
    dedupe_scope: Phase3DedupScope = "today",
    seed: int | None = None,
) -> Phase3PlanResult:
    if count <= 0:
        raise RuntimeError("`count` 必须大于 0。")
    settings = settings or Settings()
    candidates_result = list_phase3_candidates(
        settings=settings,
        date=date,
        exclude_published=dedupe_scope,
    )
    eligible = [candidate for candidate in candidates_result.candidates if candidate.eligible]
    if mode == "random":
        rng = random.Random(seed)
        rng.shuffle(eligible)
    selected = eligible[:count]
    items = [
        Phase3PlanItem(
            product_id=item.product_id,
            product_name=item.product_name,
            angle=item.angle,
            angle_name=item.angle_name,
            title=item.title,
            topic_keywords=item.topic_keywords,
            selection_reason="random" if mode == "random" else "sequential",
        )
        for item in selected
    ]
    return Phase3PlanResult(
        date=candidates_result.date,
        mode=mode,
        dedupe_scope=dedupe_scope,
        count_requested=count,
        count_selected=len(items),
        seed=seed,
        items=items,
    )


def save_phase3_artifacts(page, settings: Settings, product_id: str) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = settings.phase3_artifacts_dir / f"phase3-{product_id}-{stamp}.png"
    html_path = settings.phase3_artifacts_dir / f"phase3-{product_id}-{stamp}.html"
    page.screenshot_on_failure(str(screenshot_path))
    html_path.write_text(page.page.content(), encoding="utf-8")
    return {
        "screenshot": str(screenshot_path),
        "html": str(html_path),
    }


def append_publish_log(settings: Settings, record: dict) -> str:
    log_path = settings.publish_log_path
    payload = {"records": []}
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("records"), list):
                payload = existing
        except json.JSONDecodeError:
            payload = {"records": []}

    payload["records"].append(record)
    temp_path = log_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(log_path)
    return str(log_path)


def run_phase3(
    *,
    product_id: str | None = None,
    angle: int | None = None,
    title: str | None = None,
    content: str | None = None,
    topic_keywords: list[str] | None = None,
    image_paths: list[str] | None = None,
    headless: bool | None = None,
    settings: Settings | None = None,
) -> Phase3ExecutionResult:
    settings = settings or Settings()
    settings.ensure_directories()
    session = require_authenticated_session("merchant", settings)
    run_headless = session.browser_mode == "headless" if headless is None else headless

    today_pool = load_today_pool(settings)
    product = resolve_product(today_pool, product_id)
    final_title, final_content, final_topics, draft = resolve_publish_inputs(
        settings,
        product.id,
        title=title,
        content=content,
        topic_keywords=topic_keywords,
        angle=angle,
    )
    final_image_paths = resolve_image_paths(
        settings,
        today_pool,
        product.id,
        image_paths=image_paths,
    )

    with merchant_context(settings, headless=run_headless, auth_source=session.auth_source) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page = open_product_list_page(context, page, settings)
        list_page = ProductListPage(page, settings)
        publish_page = list_page.open_publish_page(product.id)
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
                artifacts = save_phase3_artifacts(publish_page, settings, product.id)
        except Exception as exc:
            artifacts = save_phase3_artifacts(publish_page, settings, product.id)
            raise RuntimeError(f"{exc} artifacts={json.dumps(artifacts, ensure_ascii=False)}") from exc

    result = Phase3ExecutionResult(
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
        product_binding=product_binding,
        publish_result=publish_result,
        artifacts=artifacts,
    )
    result.log_path = str(settings.publish_log_path)

    append_publish_log(
        settings,
        {
            "timestamp": datetime.now().isoformat(),
            "status": "ok" if publish_result.get("success") else "error",
            **result.model_dump(mode="json"),
        },
    )
    append_phase3_published_record(settings, result)
    return result


def run_phase3_plan(
    *,
    mode: Phase3PlanMode,
    count: int,
    settings: Settings | None = None,
    date: str | None = None,
    dedupe_scope: Phase3DedupScope = "today",
    seed: int | None = None,
    headless: bool | None = None,
) -> Phase3RunPlanResult:
    settings = settings or Settings()
    settings.ensure_directories()
    plan = build_phase3_plan(
        mode=mode,
        count=count,
        settings=settings,
        date=date,
        dedupe_scope=dedupe_scope,
        seed=seed,
    )

    results: list[Phase3RunPlanItemResult] = []
    for item in plan.items:
        try:
            phase3_result = run_phase3(
                product_id=item.product_id,
                angle=item.angle,
                settings=settings,
                headless=headless,
            )
            results.append(
                Phase3RunPlanItemResult(
                    product_id=item.product_id,
                    product_name=item.product_name,
                    angle=item.angle,
                    angle_name=item.angle_name,
                    status="success",
                    phase3_result=phase3_result,
                )
            )
        except Exception as exc:
            results.append(
                Phase3RunPlanItemResult(
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
    return Phase3RunPlanResult(
        date=plan.date,
        mode=plan.mode,
        dedupe_scope=plan.dedupe_scope,
        count_requested=plan.count_requested,
        count_selected=plan.count_selected,
        count_attempted=len(results),
        count_succeeded=success_count,
        count_failed=failed_count,
        seed=plan.seed,
        results=results,
    )


def build_phase3_payload(
    *,
    product_id: str | None = None,
    angle: int | None = None,
    title: str | None = None,
    content: str | None = None,
    topic_keywords: list[str] | None = None,
    image_paths: list[str] | None = None,
) -> tuple[dict, int]:
    try:
        result = run_phase3(
            product_id=product_id,
            angle=angle,
            title=title,
            content=content,
            topic_keywords=topic_keywords,
            image_paths=image_paths,
        )
        if result.publish_result.get("success"):
            payload = Phase3Success(data=result)
            return payload.model_dump(mode="json"), 0

        payload = SkillError(
            error="PHASE3_PUBLISH_FAILED",
            message="笔记发布失败或成功信号不明确，已保留结构化结果与失败证据。",
            site="merchant",
            details=result.model_dump(mode="json"),
        )
        return payload.model_dump(mode="json"), 1
    except LoginRequiredError as exc:
        payload = SkillError(
            error="LOGIN_REQUIRED",
            message=exc.session.message,
            site=exc.session.site,
            login=exc.session,
        )
        return payload.model_dump(mode="json"), 2
    except Exception as exc:
        payload = SkillError(
            error="PHASE3_FAILED",
            message=str(exc),
            site="merchant",
        )
        return payload.model_dump(mode="json"), 1


def build_phase3_candidates_payload(
    *,
    date: str | None = None,
    exclude_published: Phase3DedupScope = "today",
) -> tuple[dict, int]:
    try:
        result = list_phase3_candidates(
            date=date,
            exclude_published=exclude_published,
        )
        return Phase3CandidatesSuccess(data=result).model_dump(mode="json"), 0
    except Exception as exc:
        payload = SkillError(
            error="PHASE3_CANDIDATES_FAILED",
            message=str(exc),
        )
        return payload.model_dump(mode="json"), 1


def build_phase3_plan_payload(
    *,
    mode: Phase3PlanMode,
    count: int,
    date: str | None = None,
    dedupe_scope: Phase3DedupScope = "today",
    seed: int | None = None,
) -> tuple[dict, int]:
    try:
        result = build_phase3_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
        return Phase3PlanSuccess(data=result).model_dump(mode="json"), 0
    except Exception as exc:
        payload = SkillError(
            error="PHASE3_PLAN_FAILED",
            message=str(exc),
        )
        return payload.model_dump(mode="json"), 1


def build_phase3_run_plan_payload(
    *,
    mode: Phase3PlanMode,
    count: int,
    date: str | None = None,
    dedupe_scope: Phase3DedupScope = "today",
    seed: int | None = None,
) -> tuple[dict, int]:
    try:
        result = run_phase3_plan(
            mode=mode,
            count=count,
            date=date,
            dedupe_scope=dedupe_scope,
            seed=seed,
        )
        exit_code = 0 if result.count_failed == 0 else 1
        return Phase3RunPlanSuccess(data=result).model_dump(mode="json"), exit_code
    except LoginRequiredError as exc:
        payload = SkillError(
            error="LOGIN_REQUIRED",
            message=exc.session.message,
            site=exc.session.site,
            login=exc.session,
        )
        return payload.model_dump(mode="json"), 2
    except Exception as exc:
        payload = SkillError(
            error="PHASE3_RUN_PLAN_FAILED",
            message=str(exc),
            site="merchant",
        )
        return payload.model_dump(mode="json"), 1


def main() -> None:
    payload, exit_code = build_phase3_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
