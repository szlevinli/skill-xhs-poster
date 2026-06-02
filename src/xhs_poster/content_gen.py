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

_DEFAULT_EMOJIS = ["✨", "🌸", "💫", "🌟", "🍀", "🎀"]
_DEFAULT_SCENES = ["日常出门", "通勤", "约会", "逛街"]


@dataclass(slots=True)
class ProductContentGenerationResult:
    drafts: list[ContentDraft]
    meta: ContentGenerationMeta


def _infer_keyword(product_name: str) -> str:
    for kw in ("抓夹", "发夹", "鲨鱼夹", "发饰", "头饰"):
        if kw in product_name:
            return kw
    return "发饰"


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
    if re.search(r"[A-Za-z]", text):
        return False
    return True


def _pick_clean_semantic_value(items: list[str], fallback: str) -> str:
    for item in items:
        if _looks_like_human_readable_cn_phrase(item):
            return _normalize_text(item)
    return fallback


def _build_tags(product_name: str, semantic_facts: ProductSemanticFacts | None) -> str:
    tags: list[str] = []
    for kw in ("抓夹", "发夹", "鲨鱼夹", "发饰", "头饰"):
        if kw in product_name:
            tags.append(f"#{kw}")
            break
    if semantic_facts:
        for mood in semantic_facts.style_moods[:2]:
            if _looks_like_human_readable_cn_phrase(mood) and f"#{mood}" not in tags:
                tags.append(f"#{mood}")
    if not any(t in tags for t in ("#发饰", "#头饰", "#抓夹", "#发夹", "#鲨鱼夹")):
        tags.append("#发饰")
    if len(tags) < 3:
        tags.append("#好物分享")
    if len(tags) < 4:
        tags.append("#穿搭")
    return " ".join(tags[:5])


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
            "content": "你是 JSON 修复助手。把用户给出的内容修复为严格合法的 JSON。不要解释，只返回 JSON 本身。",
        },
        {"role": "user", "content": raw_text},
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
        headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
        json=repair_payload,
    )
    response.raise_for_status()
    return _repair_json_payload_locally(_extract_message_text(response.json()))


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
    semantic_facts: ProductSemanticFacts | None,
    *,
    contents_per_product: int,
) -> dict[str, Any]:
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
            "每条 draft 在角度上要有差异，避免多条内容雷同",
        ],
        "angles": [
            {"angle": angle, "angle_name": angle_name}
            for angle, angle_name in ANGLE_SPECS[:contents_per_product]
        ],
        "product": {
            "id": product.id,
            "name": product.name,
            "semantic_summary": semantic_facts.summary if semantic_facts else "",
            "semantic_categories": semantic_facts.categories if semantic_facts else [],
            "semantic_colors": semantic_facts.colors if semantic_facts else [],
            "semantic_material_guesses": semantic_facts.material_guesses if semantic_facts else [],
            "semantic_visible_elements": semantic_facts.visible_elements if semantic_facts else [],
            "semantic_product_elements": semantic_facts.product_elements if semantic_facts else [],
            "semantic_background_elements": semantic_facts.background_elements if semantic_facts else [],
            "semantic_style_moods": semantic_facts.style_moods if semantic_facts else [],
            "semantic_scene_guesses": semantic_facts.scene_guesses if semantic_facts else [],
            "semantic_confidence_notes": semantic_facts.confidence_notes if semantic_facts else [],
        },
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
    }


def _build_chat_request_payload(settings: Settings, prompt_payload: dict[str, Any]) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是资深小红书内容策划。"
                    "请根据提供的商品信息和图片语义分析，"
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
    *,
    semantic_facts: ProductSemanticFacts | None,
    contents_per_product: int,
    fallback_tags: str,
) -> ProductContentGenerationResult:
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM API Key。")

    prompt_payload = _build_prompt_payload(product, semantic_facts, contents_per_product=contents_per_product)
    request_payload = _build_chat_request_payload(settings, prompt_payload)
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}

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
                delay = (
                    max(1.0, min(float(retry_after), 10.0))
                    if retry_after and retry_after.isdigit()
                    else float(2**attempt)
                )
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
        drafts.append(
            ContentDraft(
                angle=angle,
                angle_name=angle_name,
                title=title,
                content=content,
                tags=_coerce_tags(raw_item.get("tags"), fallback_tags),
            )
        )

    return ProductContentGenerationResult(
        drafts=drafts,
        meta=ContentGenerationMeta(source="llm", provider="moonshot", model=settings.llm_model),
    )


def _generate_template_contents(
    product: ProductSummary,
    semantic_facts: ProductSemanticFacts | None = None,
    *,
    contents_per_product: int = 5,
    fallback_tags: str = "",
) -> list[ContentDraft]:
    keyword = _infer_keyword(product.name)
    color = _pick_clean_semantic_value(
        semantic_facts.colors if semantic_facts and semantic_facts.colors else [], "这款"
    )
    style = _pick_clean_semantic_value(
        semantic_facts.style_moods if semantic_facts and semantic_facts.style_moods else [], "温柔"
    )
    element = _pick_clean_semantic_value(
        semantic_facts.product_elements if semantic_facts and semantic_facts.product_elements else [], "细节"
    )
    scene_list = [
        item
        for item in (semantic_facts.scene_guesses if semantic_facts and semantic_facts.scene_guesses else [])
        if _looks_like_human_readable_cn_phrase(item)
    ] or _DEFAULT_SCENES
    scene = scene_list[len(product.name) % len(scene_list)]
    emoji = _DEFAULT_EMOJIS[(len(product.id) + len(product.name)) % len(_DEFAULT_EMOJIS)]
    tags = fallback_tags or _build_tags(product.name, semantic_facts)

    drafts: list[ContentDraft] = []
    for angle, angle_name in ANGLE_SPECS[:contents_per_product]:
        if angle == 1:
            title = f"{emoji}这个{color}{keyword}也太{style}了叭！"
            content = (
                f"最近看到这款{product.name}，第一眼就被它的{color}调调吸引住了{emoji}\n\n"
                f"整体细节很完整，{style}感拿捏得刚刚好，随手一夹都很提气质。"
            )
        elif angle == 2:
            title = f"{emoji}这款{keyword}的细节质感真的很加分"
            content = (
                f"我会特别在意发饰的表面纹理和细节处理，这款{product.name}看起来就很耐看{emoji}\n\n"
                f"从图片能看到{element}细节比较明显，整体光泽和层次感都在线，属于越看越顺眼的类型。"
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
        drafts.append(ContentDraft(angle=angle, angle_name=angle_name, title=title, content=content, tags=tags))

    return drafts


def generate_product_contents(
    product: ProductSummary,
    semantic_facts: ProductSemanticFacts | None = None,
    *,
    contents_per_product: int = 5,
    settings: Settings | None = None,
) -> ProductContentGenerationResult:
    fallback_tags = _build_tags(product.name, semantic_facts)

    if settings and settings.llm_api_key:
        try:
            return _request_llm_drafts(
                settings,
                product,
                semantic_facts=semantic_facts,
                contents_per_product=contents_per_product,
                fallback_tags=fallback_tags,
            )
        except Exception as exc:
            return ProductContentGenerationResult(
                drafts=_generate_template_contents(
                    product,
                    semantic_facts=semantic_facts,
                    contents_per_product=contents_per_product,
                    fallback_tags=fallback_tags,
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
            semantic_facts=semantic_facts,
            contents_per_product=contents_per_product,
            fallback_tags=fallback_tags,
        ),
        meta=ContentGenerationMeta(
            source="template",
            provider="moonshot" if settings else None,
            model=settings.llm_model if settings else None,
            error="未配置 LLM API Key。" if settings else None,
        ),
    )
