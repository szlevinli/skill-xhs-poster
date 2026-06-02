from __future__ import annotations

import random
import re
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..models import (
    ContentDraft,
    ContentsBundle,
    PublishCandidate,
    PublishCandidatesResult,
    PublishDailyRecords,
    PublishDedupScope,
    PublishPlanItem,
    PublishPlanMode,
    PublishPlanResult,
    PublishRecord,
    ProductSummary,
    TodayPool,
)
from .records import _save_json_atomic, load_daily_records


def load_today_pool(settings: Settings) -> TodayPool:
    if not settings.products_path.exists():
        raise RuntimeError(
            f"未找到 products.json，请先执行 fetch-products：{settings.products_path}"
        )
    return TodayPool.model_validate_json(settings.products_path.read_text(encoding="utf-8"))


def load_contents_bundle(
    settings: Settings,
    *,
    expected_date: str | None = None,
) -> ContentsBundle:
    if not settings.contents_path.exists():
        raise RuntimeError(
            f"未找到 contents.json，且本次也未显式传入标题/正文：{settings.contents_path}"
        )
    bundle = ContentsBundle.model_validate_json(settings.contents_path.read_text(encoding="utf-8"))
    if expected_date is not None and bundle.date != expected_date:
        raise RuntimeError(
            "contents.json 日期不是目标发布日，"
            f"当前为 {bundle.date}，目标日期为 {expected_date}；"
            "请先重新执行 generate-content。"
        )
    return bundle


def load_publish_plan(settings: Settings) -> PublishPlanResult | None:
    path = settings.publish_plan_path
    if not path.exists():
        return None
    try:
        plan = PublishPlanResult.model_validate_json(path.read_text(encoding="utf-8"))
        plan.plan_path = str(path)
        return plan
    except Exception as exc:
        raise RuntimeError(f"publish-plan.json 结构损坏：{path}，{exc}") from exc


def save_publish_plan(settings: Settings, plan: PublishPlanResult) -> str:
    path = settings.publish_plan_path
    plan.plan_path = str(path)
    return _save_json_atomic(path, plan.model_dump(mode="json"))


def resolve_product(today_pool: TodayPool, product_id: str | None) -> ProductSummary:
    if product_id is None:
        if not today_pool.products:
            raise RuntimeError("products.json 中没有可用商品。")
        return today_pool.products[0]

    for product in today_pool.products:
        if product.id == product_id:
            return product
    raise RuntimeError(f"products.json 中不存在商品 {product_id}。")


def resolve_image_paths(
    settings: Settings,
    today_pool: TodayPool,
    product_id: str,
    *,
    image_paths: list[str] | None = None,
    limit: int = 9,
    min_count: int = 1,
) -> list[str]:
    using_explicit_paths = bool(image_paths)
    if image_paths:
        resolved = [str(Path(path)) for path in image_paths if Path(path).exists()]
    else:
        resolved = [
            asset.path
            for asset in today_pool.image_assets.get(product_id, [])
            if Path(asset.path).exists()
        ] or [
            path
            for path in today_pool.images.get(product_id, [])
            if Path(path).exists()
        ]

    if not using_explicit_paths and not resolved:
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
    publish_date: str | None,
    title: str | None,
    content: str | None,
    topic_keywords: list[str] | None,
    angle: int | None,
) -> tuple[str, str, list[str], ContentDraft | None]:
    if title or content:
        if not title or not content:
            raise RuntimeError("显式传参发布时，`title` 和 `content` 必须同时提供。")
        return title, content, topic_keywords or [], None

    draft = pick_content_draft(
        load_contents_bundle(settings, expected_date=publish_date),
        product_id,
        angle=angle,
    )
    resolved_topics = topic_keywords or extract_topic_keywords(draft.tags)
    return draft.title, draft.content.strip(), resolved_topics, draft


def extract_skipped_topics(topic_results: list[dict]) -> list[dict]:
    skipped_topics: list[dict] = []
    for result in topic_results:
        if not result.get("topic_skipped"):
            continue
        skipped_topics.append(
            {
                "topic_keyword": result.get("topic_keyword", ""),
                "skip_reason": result.get("skip_reason", ""),
            }
        )
    return skipped_topics


def _load_success_dedupe_sets(
    settings: Settings,
    *,
    date: str,
) -> tuple[set[str], set[str]]:
    today_keys: set[str] = set()
    ever_keys: set[str] = set()
    today_records = load_daily_records(settings, date)
    for record in today_records.records:
        if record.status != "success":
            continue
        key = f"{record.product_id}:{record.angle}"
        today_keys.add(key)
        ever_keys.add(key)

    records_dir = settings.publish_records_dir
    if records_dir.exists():
        for child in records_dir.iterdir():
            if not child.is_dir() or child.name == date:
                continue
            path = child / "records.json"
            if not path.exists():
                continue
            try:
                records = PublishDailyRecords.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for record in records.records:
                if record.status != "success":
                    continue
                ever_keys.add(f"{record.product_id}:{record.angle}")
    return today_keys, ever_keys


def list_publish_candidates(
    *,
    settings: Settings | None = None,
    date: str | None = None,
    exclude_published: PublishDedupScope = "today",
) -> PublishCandidatesResult:
    settings = settings or Settings()
    settings.ensure_directories()
    current_date = date or datetime.now().date().isoformat()
    today_pool = load_today_pool(settings)
    contents_bundle = load_contents_bundle(settings, expected_date=current_date)
    published_today, published_ever = _load_success_dedupe_sets(settings, date=current_date)
    product_names = {product.id: product.name for product in today_pool.products}

    candidates: list[PublishCandidate] = []
    for product_id in sorted(contents_bundle.contents):
        if product_id not in product_names:
            continue
        drafts = sorted(contents_bundle.contents[product_id], key=lambda draft: draft.angle)
        for draft in drafts:
            dedupe_key = f"{product_id}:{draft.angle}"
            candidate = PublishCandidate(
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
                    resolve_image_paths(
                        settings,
                        today_pool,
                        product_id,
                        image_paths=draft.selected_image_paths,
                        limit=9,
                    )
                )
            except RuntimeError as exc:
                candidate.image_count = 0
                candidate.eligible = False
                candidate.ineligible_reason = str(exc)
            else:
                if candidate.eligible and exclude_published == "today" and candidate.published_today:
                    candidate.eligible = False
                    candidate.ineligible_reason = "该商品 angle 今日已发布"
                elif candidate.eligible and exclude_published == "ever" and candidate.published_ever:
                    candidate.eligible = False
                    candidate.ineligible_reason = "该商品 angle 历史已发布"
            candidates.append(candidate)

    return PublishCandidatesResult(
        date=current_date,
        exclude_published=exclude_published,
        candidates=candidates,
    )


def _candidate_sequential_sort_key(
    candidate: PublishCandidate,
    *,
    product_order: dict[str, int],
) -> tuple[int, int, str]:
    return (
        candidate.angle,
        product_order.get(candidate.product_id, 10**9),
        candidate.product_id,
    )


def build_publish_plan(
    *,
    mode: PublishPlanMode,
    count: int | None,
    settings: Settings | None = None,
    date: str | None = None,
    dedupe_scope: PublishDedupScope = "today",
    seed: int | None = None,
) -> PublishPlanResult:
    if count is not None and count <= 0:
        raise RuntimeError("`count` 必须大于 0。")
    settings = settings or Settings()
    today_pool = load_today_pool(settings)
    product_order = {
        product.id: index
        for index, product in enumerate(today_pool.products)
    }
    candidates_result = list_publish_candidates(
        settings=settings,
        date=date,
        exclude_published=dedupe_scope,
    )
    eligible = [candidate for candidate in candidates_result.candidates if candidate.eligible]
    resolved_count = len(eligible) if count is None else count
    if mode == "random":
        rng = random.Random(seed)
        rng.shuffle(eligible)
    else:
        eligible.sort(
            key=lambda candidate: _candidate_sequential_sort_key(
                candidate,
                product_order=product_order,
            )
        )
    selected = eligible[:resolved_count]
    items = [
        PublishPlanItem(
            sequence=index + 1,
            product_id=item.product_id,
            product_name=item.product_name,
            angle=item.angle,
            angle_name=item.angle_name,
            title=item.title,
            topic_keywords=item.topic_keywords,
            selection_reason="random" if mode == "random" else "sequential",
        )
        for index, item in enumerate(selected)
    ]
    result = PublishPlanResult(
        date=candidates_result.date,
        mode=mode,
        dedupe_scope=dedupe_scope,
        count_requested=resolved_count,
        count_selected=len(items),
        seed=seed,
        items=items,
    )
    result.plan_path = save_publish_plan(settings, result)
    return result


def reconcile_publish_plan_with_records(
    settings: Settings,
    plan: PublishPlanResult,
) -> PublishPlanResult:
    daily_records = load_daily_records(settings, plan.date)
    record_by_key: dict[str, PublishRecord] = {}
    for record in daily_records.records:
        key = f"{record.product_id}:{record.angle}"
        existing = record_by_key.get(key)
        # 同一 dedupe_key 可能有多条记录（失败后续传重发、或重跑）。续传对账规则：
        # 成功记录恒优先（已发成功的篇不能被随后一条 spurious failed 记录降级回 pending/failed）；
        # 同状态时取更晚的尝试（attempted_at 较大者）。保证 reconcile 幂等且不重发已成功篇。
        if existing is None:
            record_by_key[key] = record
            continue
        if existing.status == "success" and record.status != "success":
            continue
        if record.status == "success" and existing.status != "success":
            record_by_key[key] = record
            continue
        if record.attempted_at >= existing.attempted_at:
            record_by_key[key] = record

    changed = False
    for item in plan.items:
        record = record_by_key.get(f"{item.product_id}:{item.angle}")
        if record is None:
            continue
        if record.status == "success":
            if item.status != "published" or item.published_at != record.attempted_at or item.error is not None:
                item.status = "published"
                item.published_at = record.attempted_at
                item.error = None
                changed = True
            continue
        if record.status == "failed" and item.status == "pending":
            item.status = "failed"
            item.error = record.error
            changed = True

    if changed:
        save_publish_plan(settings, plan)
    return plan
