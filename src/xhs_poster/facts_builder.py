from __future__ import annotations

from .models import (
    HistoryStyleReference,
    HotNotesAnalysis,
    ProductFactsSnapshot,
    ProductImageFacts,
    ProductSemanticFacts,
    ProductSummary,
)


def build_product_facts_snapshot(
    product: ProductSummary,
    image_paths: list[str],
    facts: ProductImageFacts,
    *,
    semantic_facts: ProductSemanticFacts | None = None,
    history_style_refs: list[HistoryStyleReference] | None = None,
    trend_analysis: HotNotesAnalysis | None = None,
    warnings: list[str] | None = None,
) -> ProductFactsSnapshot:
    history_style_refs = history_style_refs or []
    warnings = warnings or []

    brightness_labels: list[str] = []
    for image in facts.images:
        if image.brightness and image.brightness not in brightness_labels:
            brightness_labels.append(image.brightness)

    trend_keywords: list[str] = []
    if trend_analysis is not None:
        for item in [*trend_analysis.scene_candidates, *trend_analysis.tone_keywords, *trend_analysis.tag_candidates]:
            if item and item not in trend_keywords:
                trend_keywords.append(item)

    return ProductFactsSnapshot(
        product_id=product.id,
        product_name=product.name,
        image_paths=image_paths,
        visual_colors=facts.colors,
        brightness_labels=brightness_labels,
        keyword_candidates=facts.keywords,
        style_candidates=facts.style_keywords,
        element_candidates=facts.confirmed_elements,
        history_style_refs=history_style_refs,
        trend_source=trend_analysis.source if trend_analysis is not None else None,
        trend_keywords=trend_keywords[:10],
        semantic_summary=semantic_facts.summary if semantic_facts is not None else "",
        semantic_categories=semantic_facts.categories if semantic_facts is not None else [],
        semantic_colors=semantic_facts.colors if semantic_facts is not None else [],
        semantic_material_guesses=semantic_facts.material_guesses if semantic_facts is not None else [],
        semantic_visible_elements=semantic_facts.visible_elements if semantic_facts is not None else [],
        semantic_product_elements=semantic_facts.product_elements if semantic_facts is not None else [],
        semantic_background_elements=semantic_facts.background_elements if semantic_facts is not None else [],
        semantic_style_moods=semantic_facts.style_moods if semantic_facts is not None else [],
        semantic_scene_guesses=semantic_facts.scene_guesses if semantic_facts is not None else [],
        semantic_confidence_notes=semantic_facts.confidence_notes if semantic_facts is not None else [],
        warnings=warnings,
    )


def build_content_input_refs(
    snapshot: ProductFactsSnapshot,
) -> dict:
    return {
        "image_paths": snapshot.image_paths,
        "history_files": [ref.source_file for ref in snapshot.history_style_refs],
        "trend_source": snapshot.trend_source,
        "trend_keywords": snapshot.trend_keywords,
        "semantic_summary": snapshot.semantic_summary,
        "semantic_categories": snapshot.semantic_categories,
        "semantic_colors": snapshot.semantic_colors,
    }
