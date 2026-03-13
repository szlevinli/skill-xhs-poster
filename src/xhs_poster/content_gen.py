from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .models import (
    ContentDraft,
    ContentGenerationMeta,
    HistoryStyleReference,
    HotNotesAnalysis,
    ProductImageFacts,
    ProductSemanticFacts,
    ProductSummary,
)


ANGLE_SPECS = [
    (1, "颜色颜值"),
    (2, "材质质感"),
    (3, "搭配场景"),
    (4, "风格情感"),
    (5, "使用体验"),
]
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)


@dataclass(slots=True)
class ProductContentGenerationResult:
    drafts: list[ContentDraft]
    meta: ContentGenerationMeta


def _pick_first(items: list[str], fallback: str) -> str:
    return items[0] if items else fallback


def _pick_display_color(colors: list[str]) -> str:
    for color in colors:
        if color not in {"综合色", "浅色"}:
            return color
    return _pick_first(colors, "这款")


def _build_tag_string(
    keyword: str,
    product: ProductSummary,
    analysis: HotNotesAnalysis,
    history_style_refs: list[HistoryStyleReference] | None = None,
) -> str:
    tags: list[str] = []
    candidates = [f"#{keyword}", *analysis.tag_candidates]
    for ref in history_style_refs or []:
        candidates.extend(ref.cleaned_hashtags or ref.hashtags)
    if "鲨鱼夹" in product.name:
        candidates.append("#鲨鱼夹")
    if "刘海夹" in product.name:
        candidates.append("#刘海夹")
    if "头饰" in product.name or "发饰" in product.name:
        candidates.append("#头饰发饰")

    for tag in candidates:
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break
    return " ".join(tags)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_multiline_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n")
    lines = [line.strip() for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_message_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM 返回为空，未找到 choices。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(texts).strip()
    raise RuntimeError("LLM 返回格式异常，无法读取 message.content。")


def _extract_json_payload(text: str) -> dict | list:
    candidate = text.strip()
    match = JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    else:
        first_object = candidate.find("{")
        first_array = candidate.find("[")
        starts = [index for index in (first_object, first_array) if index >= 0]
        if starts:
            start = min(starts)
            end_object = candidate.rfind("}")
            end_array = candidate.rfind("]")
            end = max(end_object, end_array)
            if end > start:
                candidate = candidate[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM 返回的 JSON 无法解析：{exc}") from exc


def _coerce_tags(value: Any, fallback_tags: str) -> str:
    if isinstance(value, str):
        tags = _normalize_text(value)
        return tags or fallback_tags
    if isinstance(value, list):
        tags = []
        for item in value:
            tag = _normalize_text(item)
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = f"#{tag}"
            if tag not in tags:
                tags.append(tag)
            if len(tags) >= 5:
                break
        return " ".join(tags) or fallback_tags
    return fallback_tags


def _build_prompt_payload(
    product: ProductSummary,
    facts: ProductImageFacts,
    analysis: HotNotesAnalysis,
    *,
    semantic_facts: ProductSemanticFacts | None,
    history_style_refs: list[HistoryStyleReference],
    keyword: str,
    color: str,
    style: str,
    element: str,
    tags: str,
    contents_per_product: int,
) -> dict[str, Any]:
    note_refs = [
        {
            "title": note.title,
            "author": note.author,
            "tags": note.tags,
            "content": note.content[:280],
        }
        for note in analysis.notes[:3]
    ]
    history_refs = [
        {
            "title": ref.cleaned_title or ref.title,
            "content": (ref.cleaned_content or ref.content)[:280],
            "hashtags": ref.cleaned_hashtags or ref.hashtags,
            "quality_flags": ref.quality_flags,
            "quality_score": ref.quality_score,
            "normalization_notes": ref.normalization_notes,
            "source_file": ref.source_file,
        }
        for ref in history_style_refs[:3]
    ]
    return {
        "task": "为小红书商品生成可直接发布的种草文案",
        "rules": [
            "严格输出 JSON，不要输出 markdown 代码块之外的解释",
            "返回 drafts 数组，数量必须与 angles 数组一致",
            "标题口语化、自然，不要夸大承诺，不要出现虚假数据",
            "正文保持第一人称分享口吻，2 到 3 段短句，避免生硬广告腔",
            "tags 使用 3 到 5 个话题，保留 # 前缀",
            "不要杜撰商品没有出现过的功能参数",
            "标题和正文优先依据图片语义事实，不要只根据商品名泛化描述",
            "不要把 background_elements 里的背景道具写成商品属性或卖点",
            "当图片语义与商品名冲突时，优先保守描述，只写能从图里确认的内容",
        ],
        "angles": [
            {"angle": angle, "angle_name": angle_name}
            for angle, angle_name in ANGLE_SPECS[:contents_per_product]
        ],
        "product": {
            "id": product.id,
            "name": product.name,
            "keyword": keyword,
            "display_color": color,
            "style": style,
            "element": element,
            "image_keywords": facts.keywords,
            "colors": facts.colors,
            "style_keywords": facts.style_keywords,
            "confirmed_elements": facts.confirmed_elements,
            "semantic_summary": semantic_facts.summary if semantic_facts is not None else "",
            "semantic_categories": semantic_facts.categories if semantic_facts is not None else [],
            "semantic_colors": semantic_facts.colors if semantic_facts is not None else [],
            "semantic_material_guesses": semantic_facts.material_guesses if semantic_facts is not None else [],
            "semantic_visible_elements": semantic_facts.visible_elements if semantic_facts is not None else [],
            "semantic_product_elements": semantic_facts.product_elements if semantic_facts is not None else [],
            "semantic_background_elements": semantic_facts.background_elements if semantic_facts is not None else [],
            "semantic_style_moods": semantic_facts.style_moods if semantic_facts is not None else [],
            "semantic_scene_guesses": semantic_facts.scene_guesses if semantic_facts is not None else [],
            "semantic_confidence_notes": semantic_facts.confidence_notes if semantic_facts is not None else [],
        },
        "hot_notes_analysis": {
            "source": analysis.source,
            "title_patterns": analysis.title_patterns,
            "content_patterns": analysis.content_patterns,
            "tag_candidates": analysis.tag_candidates,
            "scene_candidates": analysis.scene_candidates,
            "tone_keywords": analysis.tone_keywords,
            "reference_notes": note_refs,
        },
        "history_style_refs": history_refs,
        "output_schema": {
            "drafts": [
                {
                    "angle": 1,
                    "angle_name": "颜色颜值",
                    "title": "字符串",
                    "content": "字符串",
                    "tags": ["#标签1", "#标签2"],
                }
            ]
        },
        "default_tags": tags,
    }


def _request_llm_drafts(
    settings: Settings,
    product: ProductSummary,
    facts: ProductImageFacts,
    analysis: HotNotesAnalysis,
    *,
    semantic_facts: ProductSemanticFacts | None,
    history_style_refs: list[HistoryStyleReference],
    contents_per_product: int,
    keyword: str,
    color: str,
    style: str,
    element: str,
    tags: str,
    reference_notes: list[str],
) -> ProductContentGenerationResult:
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM API Key。")

    prompt_payload = _build_prompt_payload(
        product,
        facts,
        analysis,
        semantic_facts=semantic_facts,
        history_style_refs=history_style_refs,
        keyword=keyword,
        color=color,
        style=style,
        element=element,
        tags=tags,
        contents_per_product=contents_per_product,
    )
    request_payload = {
        "model": settings.llm_model,
        "temperature": 0.8,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是资深小红书内容策划。"
                    "请根据提供的商品信息、图片事实和热门笔记分析，"
                    "输出可直接解析的 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            },
        ],
    }
    base_url = settings.llm_base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = client.post(url, headers=headers, json=request_payload)
        response.raise_for_status()
        payload = response.json()

    raw_text = _extract_message_text(payload)
    parsed = _extract_json_payload(raw_text)
    drafts_payload = parsed.get("drafts") if isinstance(parsed, dict) else parsed
    if not isinstance(drafts_payload, list):
        raise RuntimeError("LLM 返回中缺少 drafts 数组。")

    drafts: list[ContentDraft] = []
    for index, (angle, angle_name) in enumerate(ANGLE_SPECS[:contents_per_product]):
        raw_item = drafts_payload[index] if index < len(drafts_payload) else {}
        if not isinstance(raw_item, dict):
            raw_item = {}
        title = _normalize_text(raw_item.get("title")) or f"{product.name}灵感分享 {index + 1}"
        content = _normalize_multiline_text(raw_item.get("content"))
        if not content:
            raise RuntimeError(f"LLM 返回的第 {index + 1} 条正文为空。")
        drafts.append(
            ContentDraft(
                angle=angle,
                angle_name=angle_name,
                title=title,
                content=content,
                tags=_coerce_tags(raw_item.get("tags"), tags),
                reference_notes=reference_notes,
            )
        )

    return ProductContentGenerationResult(
        drafts=drafts,
        meta=ContentGenerationMeta(
            source="llm",
            provider="moonshot",
            model=settings.llm_model,
        ),
    )


def _generate_template_contents(
    product: ProductSummary,
    facts: ProductImageFacts,
    analysis: HotNotesAnalysis,
    *,
    semantic_facts: ProductSemanticFacts | None = None,
    history_style_refs: list[HistoryStyleReference] | None = None,
    contents_per_product: int = 5,
) -> list[ContentDraft]:
    keyword = _pick_first(facts.keywords, analysis.keyword)
    color = _pick_display_color(semantic_facts.colors if semantic_facts and semantic_facts.colors else facts.colors)
    style = _pick_first(semantic_facts.style_moods if semantic_facts and semantic_facts.style_moods else facts.style_keywords, "温柔")
    element = _pick_first(semantic_facts.product_elements if semantic_facts and semantic_facts.product_elements else facts.confirmed_elements, "细节")
    emoji = analysis.emoji_candidates[(len(product.id) + len(product.name)) % len(analysis.emoji_candidates)]
    scene_source = semantic_facts.scene_guesses if semantic_facts and semantic_facts.scene_guesses else analysis.scene_candidates
    scene = scene_source[len(product.name) % len(scene_source)]
    tags = _build_tag_string(keyword, product, analysis, history_style_refs)
    reference_notes = [
        ref.source_file.rsplit("/", maxsplit=1)[-1]
        for ref in (history_style_refs or [])[:2]
    ]
    semantic_summary = semantic_facts.summary if semantic_facts is not None else ""

    drafts: list[ContentDraft] = []
    for angle, angle_name in ANGLE_SPECS[:contents_per_product]:
        if angle == 1:
            title = f"{emoji}这个{color}{keyword}也太{style}了叭！"
            content = (
                f"最近看到这款{product.name}，第一眼就被它的{color}调调吸引住了{emoji}\n\n"
                f"{semantic_summary or f'从图片里能看到整体细节很完整，{style}感拿捏得刚刚好'}，随手一夹都很提气质。"
            )
        elif angle == 2:
            title = f"{emoji}这款{keyword}的细节质感真的很加分"
            content = (
                f"我会特别在意发饰的表面纹理和细节处理，这款{product.name}看起来就很耐看{emoji}\n\n"
                f"从主图能看到{element}细节比较明显，整体光泽和层次感都在线，属于越看越顺眼的类型。"
            )
        elif angle == 3:
            title = f"{emoji}出门前一分钟就能用上的{style}{keyword}"
            content = (
                f"这种{keyword}真的很适合{scene}前快速整理发型{emoji}\n\n"
                f"不用太复杂的步骤，夹上以后整体造型就会更完整，和日常穿搭也比较好搭。"
            )
        elif angle == 4:
            title = f"{emoji}这款{keyword}有种很自然的{style}氛围"
            content = (
                f"我很喜欢这种不需要太多修饰就能带出氛围感的小配饰{emoji}\n\n"
                f"这款从配色到{element}细节都偏{style}路线，看着就让人想到轻松又舒服的日常时刻。"
            )
        else:
            title = f"{emoji}最近很想反复拿出来戴的{keyword}"
            content = (
                f"有些发饰是看一眼就过去了，但这款会让我想一直反复搭配{emoji}\n\n"
                f"主要是它不挑日常场景，视觉上也足够显眼，出门前顺手拿它就会觉得今天状态不错。"
            )

        drafts.append(
            ContentDraft(
                angle=angle,
                angle_name=angle_name,
                title=title,
                content=content,
                tags=tags,
                reference_notes=reference_notes,
            )
        )

    return drafts


def generate_product_contents(
    product: ProductSummary,
    facts: ProductImageFacts,
    analysis: HotNotesAnalysis,
    *,
    semantic_facts: ProductSemanticFacts | None = None,
    history_style_refs: list[HistoryStyleReference] | None = None,
    contents_per_product: int = 5,
    settings: Settings | None = None,
) -> ProductContentGenerationResult:
    history_style_refs = history_style_refs or []
    keyword = _pick_first(facts.keywords, analysis.keyword)
    color = _pick_display_color(facts.colors)
    style = _pick_first(facts.style_keywords, "温柔")
    element = _pick_first(facts.confirmed_elements, "细节")
    tags = _build_tag_string(keyword, product, analysis, history_style_refs)
    reference_notes = [
        ref.source_file.rsplit("/", maxsplit=1)[-1]
        for ref in history_style_refs[:2]
    ]

    if settings and settings.llm_api_key:
        try:
            return _request_llm_drafts(
                settings,
                product,
                facts,
                analysis,
                semantic_facts=semantic_facts,
                history_style_refs=history_style_refs,
                contents_per_product=contents_per_product,
                keyword=keyword,
                color=color,
                style=style,
                element=element,
                tags=tags,
                reference_notes=reference_notes,
            )
        except Exception as exc:
            return ProductContentGenerationResult(
                drafts=_generate_template_contents(
                    product,
                    facts,
                    analysis,
                    semantic_facts=semantic_facts,
                    history_style_refs=history_style_refs,
                    contents_per_product=contents_per_product,
                ),
                meta=ContentGenerationMeta(
                    source="llm_fallback",
                    provider="moonshot",
                    model=settings.llm_model,
                    error=str(exc),
                ),
            )

    return ProductContentGenerationResult(
        drafts=_generate_template_contents(
            product,
            facts,
            analysis,
            semantic_facts=semantic_facts,
            history_style_refs=history_style_refs,
            contents_per_product=contents_per_product,
        ),
        meta=ContentGenerationMeta(
            source="template",
            provider="moonshot" if settings else None,
            model=settings.llm_model if settings else None,
            error="未配置 LLM API Key。" if settings else None,
        ),
    )
