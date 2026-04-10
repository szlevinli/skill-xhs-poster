from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from .config import Settings
from .content_gen import generate_product_contents
from .facts_builder import build_content_input_refs, build_product_facts_snapshot
from .history_notes import (
    group_history_style_refs,
    load_history_style_refs,
    save_history_style_refs,
    select_history_style_refs,
)
from .hot_notes import build_fallback_hot_notes_analysis, infer_search_keyword
from .image_facts import extract_product_image_facts
from .image_allocation import allocate_image_paths
from .image_semantics import analyze_product_image_semantics, load_image_semantic_facts, save_image_semantic_facts
from .models import (
    ContentDraft,
    ContentGenerationMeta,
    ContentsBundle,
    HotNotesAnalysis,
    Phase2ExecutionResult,
    Phase2Success,
    ProductFailure,
    ProductImageFacts,
    ProductSemanticFacts,
    SkillError,
    TodayPool,
)
from .phase2_report import build_phase2_report
from .trend_signals import build_trend_signals_from_history_refs


def load_today_pool(settings: Settings) -> TodayPool:
    if not settings.today_pool_path.exists():
        raise RuntimeError(
            f"未找到 today-pool.json，请先执行 prepare-products：{settings.today_pool_path}"
        )
    return TodayPool.model_validate_json(settings.today_pool_path.read_text(encoding="utf-8"))


def save_json_atomic(path, payload: dict) -> None:
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def load_trend_analysis(
    settings: Settings,
    keyword: str,
    history_style_refs=None,
) -> tuple[HotNotesAnalysis, str]:
    if settings.trend_signals_path.exists():
        try:
            payload = json.loads(settings.trend_signals_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            analysis = HotNotesAnalysis.model_validate(payload)
            if not analysis.keyword:
                analysis.keyword = keyword
            return analysis, "trend_file"
        except Exception:
            pass

    if history_style_refs:
        analysis = build_trend_signals_from_history_refs(
            keyword=keyword,
            refs=history_style_refs,
        )
        return analysis, "history_refs"

    return build_fallback_hot_notes_analysis(keyword), "default_fallback"


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


def build_phase2_outputs(
    *,
    keyword: str | None = None,
    contents_per_product: int = 5,
    search_limit: int = 20,
    detail_limit: int = 8,
    settings: Settings | None = None,
) -> Phase2ExecutionResult:
    del search_limit
    del detail_limit
    settings = settings or Settings()
    settings.ensure_directories()
    today_pool = load_today_pool(settings)
    if not today_pool.products:
        raise RuntimeError("today-pool.json 中没有可用商品。")

    final_keyword = keyword or infer_search_keyword(today_pool.products)
    history_style_refs = load_history_style_refs(settings)
    grouped_history_refs = group_history_style_refs(history_style_refs)
    save_history_style_refs(settings, history_style_refs)
    analysis, trend_source_kind = load_trend_analysis(settings, final_keyword, history_style_refs)
    if trend_source_kind == "default_fallback":
        analysis.source = "local_fallback"

    image_facts: list[ProductImageFacts] = []
    facts_map: dict[str, ProductImageFacts] = {}
    semantic_facts_map: dict[str, ProductSemanticFacts] = {}
    product_fact_snapshots = []
    semantic_bundle = load_image_semantic_facts(settings)
    for product in today_pool.products:
        image_paths = resolve_image_paths(settings, today_pool, product.id, limit=None)
        if not image_paths:
            continue
        facts = extract_product_image_facts(product, image_paths)
        image_facts.append(facts)
        facts_map[product.id] = facts
        semantic_facts_map[product.id] = analyze_product_image_semantics(
            settings,
            product_id=product.id,
            product_name=product.name,
            image_paths=image_paths,
            cache_bundle=semantic_bundle,
        )
    save_image_semantic_facts(settings, semantic_bundle)

    contents: dict[str, list[ContentDraft]] = {}
    generation: dict[str, ContentGenerationMeta] = {}
    statuses: dict[str, str] = {}
    warnings_map: dict[str, list[str]] = {}
    input_refs: dict[str, dict] = {}
    failed_products: list[ProductFailure] = []
    generation_sources: dict[str, str] = {}

    started_at = time.perf_counter()
    for product in today_pool.products:
        product_warnings: list[str] = []
        image_paths = resolve_image_paths(settings, today_pool, product.id, limit=None)
        if not image_paths:
            statuses[product.id] = "failed"
            product_warnings.append("缺少可用商品图片，无法生成文案。")
            warnings_map[product.id] = product_warnings
            failed_products.append(
                ProductFailure(
                    product_id=product.id,
                    product_name=product.name,
                    reason="缺少可用商品图片，无法生成文案。",
                )
            )
            continue

        facts = facts_map.get(product.id)
        if facts is None:
            statuses[product.id] = "failed"
            product_warnings.append("图片事实提取失败，无法生成文案。")
            warnings_map[product.id] = product_warnings
            failed_products.append(
                ProductFailure(
                    product_id=product.id,
                    product_name=product.name,
                    reason="图片事实提取失败，无法生成文案。",
                )
            )
            continue

        selected_history_refs = select_history_style_refs(product, grouped_history_refs)
        semantic_facts = semantic_facts_map.get(product.id)
        if semantic_facts and not any(item.status == "success" for item in semantic_facts.images):
            product_warnings.append("semantic_analysis_unavailable")

        snapshot = build_product_facts_snapshot(
            product,
            image_paths,
            facts,
            semantic_facts=semantic_facts,
            history_style_refs=selected_history_refs,
            trend_analysis=analysis,
            warnings=product_warnings,
        )
        product_fact_snapshots.append(snapshot)
        input_refs[product.id] = build_content_input_refs(snapshot)

        generated = generate_product_contents(
            product,
            facts,
            analysis,
            semantic_facts=semantic_facts,
            history_style_refs=selected_history_refs,
            contents_per_product=contents_per_product,
            settings=settings,
        )
        allocations = allocate_image_paths(
            image_paths,
            draft_count=len(generated.drafts),
        )
        for draft, selected_image_paths in zip(generated.drafts, allocations, strict=False):
            draft.selected_image_paths = selected_image_paths
            draft.selected_image_count = len(selected_image_paths)
        contents[product.id] = generated.drafts
        generation[product.id] = generated.meta
        generation_sources[product.id] = generated.meta.source
        if generated.meta.source != "llm":
            error_text = (generated.meta.error or "").strip()
            if error_text:
                product_warnings.append(f"正文生成回退为模板：{error_text}")
            else:
                product_warnings.append("正文生成回退为模板。")
        warnings_map[product.id] = product_warnings

        if generated.meta.source == "llm":
            statuses[product.id] = "ok"
        else:
            statuses[product.id] = "partial"

    if not contents:
        raise RuntimeError("未能基于 today-pool.json 生成任何商品内容。")

    image_facts_payload = {
        "date": str(date.today()),
        "source": "local_image_analysis",
        "items": [item.model_dump(mode="json") for item in image_facts],
    }
    save_json_atomic(settings.image_facts_path, image_facts_payload)

    hot_notes_payload = {
        "date": str(date.today()),
        **analysis.model_dump(mode="json"),
    }
    product_facts_payload = {
        "date": str(date.today()),
        "keyword": final_keyword,
        "items": [item.model_dump(mode="json") for item in product_fact_snapshots],
    }
    save_json_atomic(settings.hot_notes_analysis_path, hot_notes_payload)
    save_json_atomic(settings.product_facts_path, product_facts_payload)

    phase2_report = build_phase2_report(
        total_products=len(today_pool.products),
        statuses=statuses,
        warnings=warnings_map,
        failed_products=failed_products,
        generation_sources=generation_sources,
    )
    phase2_report_payload = phase2_report.model_dump(mode="json")
    phase2_report_payload["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    save_json_atomic(settings.phase2_report_path, phase2_report_payload)

    contents_bundle = ContentsBundle(
        date=str(date.today()),
        total_products=len(today_pool.products),
        contents_per_product=contents_per_product,
        analysis_ref=settings.hot_notes_analysis_path.name,
        product_facts_ref=settings.product_facts_path.name,
        phase2_report_ref=settings.phase2_report_path.name,
        contents=contents,
        generation=generation,
        statuses=statuses,
        warnings=warnings_map,
        input_refs=input_refs,
    )
    save_json_atomic(
        settings.contents_path,
        contents_bundle.model_dump(mode="json"),
    )

    return Phase2ExecutionResult(
        date=str(date.today()),
        keyword=final_keyword,
        source=analysis.source,
        total_products=len(today_pool.products),
        contents_per_product=contents_per_product,
        raw_hot_notes_path=None,
        hot_notes_analysis_path=str(settings.hot_notes_analysis_path),
        image_facts_path=str(settings.image_facts_path),
        image_semantic_facts_path=str(settings.image_semantic_facts_path),
        product_facts_path=str(settings.product_facts_path),
        phase2_report_path=str(settings.phase2_report_path),
        contents_path=str(settings.contents_path),
        contents=contents,
        generation=generation,
        statuses=statuses,
        warnings=warnings_map,
    )


def build_phase2_payload(
    *,
    keyword: str | None = None,
    contents_per_product: int = 5,
    search_limit: int = 20,
    detail_limit: int = 8,
) -> tuple[dict, int]:
    try:
        result = build_phase2_outputs(
            keyword=keyword,
            contents_per_product=contents_per_product,
            search_limit=search_limit,
            detail_limit=detail_limit,
        )
        payload = Phase2Success(data=result)
        return payload.model_dump(mode="json"), 0
    except Exception as exc:
        payload = SkillError(
            error="PHASE2_FAILED",
            message=str(exc),
        )
        return payload.model_dump(mode="json"), 1


def main() -> None:
    payload, exit_code = build_phase2_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
