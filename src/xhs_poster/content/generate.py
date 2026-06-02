from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..config import Settings
from .llm import generate_product_contents
from .images import allocate_image_paths
from .vision import analyze_product_image_semantics, load_image_semantic_facts, save_image_semantic_facts
from ..models import (
    ContentDraft,
    ContentGenerationMeta,
    ContentsBundle,
    GenerateContentExecutionResult,
    ProductFailure,
    TodayPool,
)
from ..originality import check_draft_similarity


def load_today_pool(settings: Settings) -> TodayPool:
    if not settings.products_path.exists():
        raise RuntimeError(
            f"未找到 products.json，请先执行 fetch-products：{settings.products_path}"
        )
    return TodayPool.model_validate_json(settings.products_path.read_text(encoding="utf-8"))


def save_json_atomic(path, payload: dict) -> None:
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def resolve_image_paths(
    settings: Settings,
    today_pool: TodayPool,
    product_id: str,
    *,
    limit: int | None = None,
) -> list[str]:
    existing = [
        asset.path
        for asset in today_pool.image_assets.get(product_id, [])
        if Path(asset.path).exists()
    ]
    seen = set(existing)
    for path in today_pool.images.get(product_id, []):
        if not Path(path).exists() or path in seen:
            continue
        existing.append(path)
        seen.add(path)
    if limit is not None and len(existing) >= limit:
        return existing[:limit]

    product_dir = settings.images_dir / product_id
    if product_dir.exists():
        candidates = sorted(
            str(path)
            for path in product_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        for path in candidates:
            if path in seen:
                continue
            existing.append(path)
            seen.add(path)
            if limit is not None and len(existing) >= limit:
                break
    return existing[:limit] if limit is not None else existing


def build_generate_content_outputs(
    *,
    contents_per_product: int = 5,
    settings: Settings | None = None,
) -> GenerateContentExecutionResult:
    settings = settings or Settings()
    settings.ensure_directories()
    today_pool = load_today_pool(settings)
    if not today_pool.products:
        raise RuntimeError("products.json 中没有可用商品。")

    semantic_bundle = load_image_semantic_facts(settings)

    contents: dict[str, list[ContentDraft]] = {}
    generation: dict[str, ContentGenerationMeta] = {}
    statuses: dict[str, str] = {}
    warnings_map: dict[str, list[str]] = {}
    failed_products: list[ProductFailure] = []

    for product in today_pool.products:
        product_warnings: list[str] = []
        image_paths = resolve_image_paths(settings, today_pool, product.id)
        if not image_paths:
            statuses[product.id] = "failed"
            failed_products.append(
                ProductFailure(
                    product_id=product.id,
                    product_name=product.name,
                    reason="缺少可用商品图片，无法生成文案。",
                )
            )
            warnings_map[product.id] = ["缺少可用商品图片，无法生成文案。"]
            continue

        semantic_facts = analyze_product_image_semantics(
            settings,
            product_id=product.id,
            product_name=product.name,
            image_paths=image_paths,
            cache_bundle=semantic_bundle,
        )

        if not any(item.status == "success" for item in semantic_facts.images):
            product_warnings.append("semantic_analysis_unavailable")

        generated = generate_product_contents(
            product,
            semantic_facts,
            contents_per_product=contents_per_product,
            settings=settings,
        )

        all_previous = [d for drafts_list in contents.values() for d in drafts_list]
        for draft in generated.drafts:
            reason = check_draft_similarity(draft, all_previous)
            if reason:
                product_warnings.append(f"angle_{draft.angle}_similar:{reason}")

        allocations = allocate_image_paths(image_paths, draft_count=len(generated.drafts))
        for draft, selected_paths in zip(generated.drafts, allocations, strict=False):
            draft.selected_image_paths = selected_paths
            draft.selected_image_count = len(selected_paths)

        contents[product.id] = generated.drafts
        generation[product.id] = generated.meta
        statuses[product.id] = "ok" if generated.meta.source == "llm" else "partial"

        if generated.meta.source != "llm":
            error_text = (generated.meta.error or "").strip()
            product_warnings.append(f"正文生成回退为模板：{error_text}" if error_text else "正文生成回退为模板。")

        warnings_map[product.id] = product_warnings

    save_image_semantic_facts(settings, semantic_bundle)

    if not contents:
        raise RuntimeError("未能基于 products.json 生成任何商品内容。")

    bundle = ContentsBundle(
        date=str(date.today()),
        total_products=len(today_pool.products),
        contents_per_product=contents_per_product,
        contents=contents,
        generation=generation,
        statuses=statuses,
        warnings=warnings_map,
    )
    save_json_atomic(settings.contents_path, bundle.model_dump(mode="json"))

    return GenerateContentExecutionResult(
        date=str(date.today()),
        image_analysis_path=str(settings.image_analysis_path),
        contents_path=str(settings.contents_path),
        contents=contents,
        generation=generation,
        statuses=statuses,
        warnings=warnings_map,
        total_products=len(today_pool.products),
        contents_per_product=contents_per_product,
    )
