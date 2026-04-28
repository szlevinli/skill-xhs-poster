from __future__ import annotations

import json
import re
import time
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
from .originality import build_default_originality_check, coerce_originality_check


ANGLE_SPECS = [
    (1, "颜色颜值"),
    (2, "材质质感"),
    (3, "搭配场景"),
    (4, "风格情感"),
    (5, "使用体验"),
]
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL)
ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


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


def _looks_like_human_readable_cn_phrase(value: str) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    if len(text) > 24:
        return False
    if any(mark in text for mark in ("。", "；", ";", "!", "?", ":", "：")):
        return False
    if ASCII_LETTER_RE.search(text):
        return False
    return True


def _pick_clean_semantic_value(items: list[str], fallback: str) -> str:
    for item in items:
        if _looks_like_human_readable_cn_phrase(item):
            return _normalize_text(item)
    return fallback


def _build_template_semantic_line(style: str, semantic_facts: ProductSemanticFacts | None) -> str:
    if semantic_facts is None:
        return f"从图片里能看到整体细节很完整，{style}感拿捏得刚刚好"

    visible = _pick_clean_semantic_value(semantic_facts.product_elements, "")
    mood = _pick_clean_semantic_value(semantic_facts.style_moods, style)
    color = _pick_clean_semantic_value(semantic_facts.colors, "")

    phrases: list[str] = []
    if color:
        phrases.append(f"{color}调很显眼")
    if visible:
        phrases.append(f"{visible}细节比较抓眼")
    if mood:
        phrases.append(f"整体是偏{mood}的感觉")
    if not phrases:
        return f"从图片里能看到整体细节很完整，{style}感拿捏得刚刚好"
    return "，".join(phrases[:2])


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


def _extract_json_candidate(text: str) -> str:
    candidate = text.strip()
    match = JSON_BLOCK_RE.search(candidate)
    if match:
        return match.group(1).strip()

    first_object = candidate.find("{")
    first_array = candidate.find("[")
    starts = [index for index in (first_object, first_array) if index >= 0]
    if starts:
        start = min(starts)
        end_object = candidate.rfind("}")
        end_array = candidate.rfind("]")
        end = max(end_object, end_array)
        if end > start:
            return candidate[start : end + 1]
    return candidate


def _sanitize_json_candidate(candidate: str) -> str:
    fixed = candidate.strip()
    fixed = fixed.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)
    return fixed


def _repair_json_payload_locally(text: str) -> dict | list:
    candidate = _sanitize_json_candidate(_extract_json_candidate(text))
    return json.loads(candidate)


def _request_json_repair(client: httpx.Client, settings: Settings, raw_text: str) -> dict | list:
    repair_messages = [
        {
            "role": "system",
            "content": (
                "你是 JSON 修复助手。"
                "把用户给出的内容修复为严格合法的 JSON。"
                "不要解释，只返回 JSON 本身。"
            ),
        },
        {
            "role": "user",
            "content": raw_text,
        },
    ]
    repair_payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": repair_messages,
        "max_tokens": 4096,
    }
    if settings.llm_model.startswith("kimi-k2."):
        repair_payload["thinking"] = {"type": "disabled"}
    else:
        repair_payload["temperature"] = 0

    response = client.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        },
        json=repair_payload,
    )
    response.raise_for_status()
    repaired_text = _extract_message_text(response.json())
    return _repair_json_payload_locally(repaired_text)


def _extract_json_payload(text: str) -> dict | list:
    candidate = _extract_json_candidate(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        try:
            return _repair_json_payload_locally(candidate)
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
            "必须满足原创性闸门：每条 draft 都要有 1 个新核心输入 + 2 个支持性差异；没有新核心输入不得输出为可发布内容",
            "新核心输入只能从：新案例、新数据、新实测、新个人经验、新对比样本、新失败教训 中选择，并必须在正文里体现",
            "支持性差异至少 2 项，可从不同人群、不同场景、不同决策问题、不同观点/结论、不同结构组织、不同表达切口、不同素材组合方式中选择",
            "不要复刻 history_style_refs 的标题、段落骨架、核心结论或标签堆叠；它们只可作为反面查重参考",
            "先确定 product_fact_anchors（至少 2 个）再写文案；anchors 必须来自当前商品事实池，例如颜色、材质、元素、轮廓、夹齿/开合、场景、可见结构",
            "title 和 content 中必须实际写出这些 product_fact_anchors，不允许只在 originality 字段里声明而正文不落地",
            "如果写了实测、对比、个人经验，也必须把这些经验落在当前商品事实上，例如当前颜色、当前元素、当前夹齿结构、当前材质或当前场景",
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
                    "originality": {
                        "core_input_type": "新案例",
                        "core_input_evidence": "本篇成立的新素材/新案例/新实测依据",
                        "product_fact_anchors": ["当前商品事实1", "当前商品事实2"],
                        "supporting_differences": ["不同决策问题：...", "不同素材组合方式：..."],
                        "nearest_history_notes": ["历史参考文件或标题"],
                    },
                }
            ]
        },
        "default_tags": tags,
    }


def _build_core_input_evidence(
    product: ProductSummary,
    facts: ProductImageFacts,
    semantic_facts: ProductSemanticFacts | None,
    angle_name: str,
) -> str:
    semantic_bits: list[str] = []
    if semantic_facts is not None:
        semantic_bits.extend(semantic_facts.colors[:2])
        semantic_bits.extend(semantic_facts.product_elements[:2])
        semantic_bits.extend(semantic_facts.visible_elements[:2])
        semantic_bits.extend(semantic_facts.scene_guesses[:1])
    if not semantic_bits:
        semantic_bits.extend(facts.colors[:2])
        semantic_bits.extend(facts.confirmed_elements[:2] or facts.keywords[:2])
    detail = "、".join(bit for bit in semantic_bits if bit) or product.name
    return f"以当前商品 {product.name} 的图片事实作为新案例，围绕{angle_name}补充独立观察，明确写到这些商品锚点：{detail}"


def _supporting_differences_for_angle(angle_name: str, semantic_facts: ProductSemanticFacts | None) -> list[str]:
    scene = _pick_clean_semantic_value(
        semantic_facts.scene_guesses if semantic_facts and semantic_facts.scene_guesses else [],
        "当前商品图场景",
    )
    color = _pick_clean_semantic_value(
        semantic_facts.colors if semantic_facts and semantic_facts.colors else [],
        "当前商品色调",
    )
    element = _pick_clean_semantic_value(
        semantic_facts.product_elements if semantic_facts and semantic_facts.product_elements else [],
        "当前商品细节",
    )
    return [
        f"不同决策问题：本篇聚焦{angle_name}，并结合{element}这个具体商品细节，而不是复述历史笔记同一卖点",
        f"不同素材组合方式：结合{color}与{scene}这两个当前商品事实组织内容",
    ]


def _default_product_fact_anchors(
    product: ProductSummary,
    facts: ProductImageFacts,
    semantic_facts: ProductSemanticFacts | None,
) -> list[str]:
    terms = _build_grounding_terms(product, facts, semantic_facts)
    anchors: list[str] = []
    for term in terms:
        if len(term) >= 2 and term not in anchors:
            anchors.append(term)
        if len(anchors) >= 3:
            break
    return anchors[:3]


def _build_grounding_terms(
    product: ProductSummary,
    facts: ProductImageFacts,
    semantic_facts: ProductSemanticFacts | None,
) -> list[str]:
    terms: list[str] = [product.name]
    terms.extend(facts.keywords[:3])
    terms.extend(facts.colors[:3])
    terms.extend(facts.confirmed_elements[:4])
    if semantic_facts is not None:
        terms.extend(semantic_facts.colors[:3])
        terms.extend(semantic_facts.product_elements[:4])
        terms.extend(semantic_facts.visible_elements[:4])
        terms.extend(semantic_facts.categories[:2])
        terms.extend(semantic_facts.scene_guesses[:2])
    deduped: list[str] = []
    for term in terms:
        text = _normalize_text(term)
        if not text or text in deduped:
            continue
        deduped.append(text)
    return deduped


def _raw_originality_fields(raw_item: dict[str, Any]) -> tuple[str, str, list[str], list[str]]:
    raw_originality = coerce_originality_check(raw_item.get("originality"))
    if raw_originality is None:
        return "", "", [], []
    return (
        raw_originality.core_input_type,
        raw_originality.core_input_evidence,
        raw_originality.product_fact_anchors,
        raw_originality.supporting_differences,
    )


def _build_chat_request_payload(settings: Settings, prompt_payload: dict[str, Any]) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": settings.llm_model,
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
    if settings.llm_model.startswith("kimi-k2."):
        request_payload["thinking"] = {"type": "disabled"}
        request_payload["max_tokens"] = 4096
    else:
        request_payload["temperature"] = 0.8
    return request_payload


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
    request_payload = _build_chat_request_payload(settings, prompt_payload)
    base_url = settings.llm_base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for attempt in range(3):
            try:
                response = client.post(url, headers=headers, json=request_payload)
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code != 429 or attempt == 2:
                    raise
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(1.0, min(float(retry_after), 10.0))
                else:
                    delay = float(2 ** attempt)
                time.sleep(delay)
            except Exception as exc:
                last_error = exc
                raise

    if payload is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM 返回为空。")

    raw_text = _extract_message_text(payload)
    try:
        parsed = _extract_json_payload(raw_text)
    except RuntimeError as parse_error:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as repair_client:
            try:
                parsed = _request_json_repair(repair_client, settings, raw_text)
            except Exception:
                raise parse_error
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
        draft = ContentDraft(
            angle=angle,
            angle_name=angle_name,
            title=title,
            content=content,
            tags=_coerce_tags(raw_item.get("tags"), tags),
            reference_notes=reference_notes,
        )
        core_input_type, core_input_evidence, product_fact_anchors, supporting_differences = _raw_originality_fields(raw_item)
        if not core_input_type and not core_input_evidence:
            core_input_type = "新案例"
            core_input_evidence = _build_core_input_evidence(product, facts, semantic_facts, angle_name)
            product_fact_anchors = _default_product_fact_anchors(product, facts, semantic_facts)
            supporting_differences = _supporting_differences_for_angle(angle_name, semantic_facts)
        draft.originality_check = build_default_originality_check(
            draft,
            product_name=product.name,
            core_input_type=core_input_type,
            core_input_evidence=core_input_evidence,
            product_fact_anchors=product_fact_anchors,
            supporting_differences=supporting_differences,
            grounding_terms=_build_grounding_terms(product, facts, semantic_facts),
            history_style_refs=history_style_refs,
            previous_drafts=drafts,
        )
        drafts.append(draft)

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
    color = _pick_clean_semantic_value(
        semantic_facts.colors if semantic_facts and semantic_facts.colors else [],
        _pick_display_color(facts.colors),
    )
    style = _pick_clean_semantic_value(
        semantic_facts.style_moods if semantic_facts and semantic_facts.style_moods else [],
        _pick_first(facts.style_keywords, "温柔"),
    )
    element = _pick_clean_semantic_value(
        semantic_facts.product_elements if semantic_facts and semantic_facts.product_elements else [],
        _pick_first(facts.confirmed_elements, "细节"),
    )
    emoji = analysis.emoji_candidates[(len(product.id) + len(product.name)) % len(analysis.emoji_candidates)]
    scene_source = [
        item
        for item in (semantic_facts.scene_guesses if semantic_facts and semantic_facts.scene_guesses else [])
        if _looks_like_human_readable_cn_phrase(item)
    ] or analysis.scene_candidates
    scene = scene_source[len(product.name) % len(scene_source)]
    tags = _build_tag_string(keyword, product, analysis, history_style_refs)
    reference_notes = [
        ref.source_file.rsplit("/", maxsplit=1)[-1]
        for ref in (history_style_refs or [])[:2]
    ]
    drafts: list[ContentDraft] = []
    for angle, angle_name in ANGLE_SPECS[:contents_per_product]:
        if angle == 1:
            title = f"{emoji}这个{color}{keyword}也太{style}了叭！"
            content = (
                f"最近看到这款{product.name}，第一眼就被它的{color}调调吸引住了{emoji}\n\n"
                f"{_build_template_semantic_line(style, semantic_facts)}，随手一夹都很提气质。"
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

        draft = ContentDraft(
            angle=angle,
            angle_name=angle_name,
            title=title,
            content=content,
            tags=tags,
            reference_notes=reference_notes,
        )
        draft.originality_check = build_default_originality_check(
            draft,
            product_name=product.name,
            core_input_type="新案例",
            core_input_evidence=_build_core_input_evidence(product, facts, semantic_facts, angle_name),
            product_fact_anchors=_default_product_fact_anchors(product, facts, semantic_facts),
            supporting_differences=_supporting_differences_for_angle(angle_name, semantic_facts),
            grounding_terms=_build_grounding_terms(product, facts, semantic_facts),
            history_style_refs=history_style_refs or [],
            previous_drafts=drafts,
        )
        drafts.append(draft)

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
