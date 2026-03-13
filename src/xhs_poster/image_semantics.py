from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .config import Settings
from .models import ImageSemanticFact, ImageSemanticFactsBundle, ProductSemanticFacts


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _save_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_list(value: Any, *, limit: int = 10) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，/、;\n]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        return []

    result: list[str] = []
    for part in parts:
        item = _normalize_text(part)
        if not item or item in result:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _extract_json_payload(text: str) -> dict[str, Any]:
    candidate = text.strip()
    match = JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise RuntimeError("视觉模型返回格式异常，未得到 JSON 对象。")
    return payload


def _image_sha256(image_path: Path) -> str:
    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def _guess_mime_type(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(image_path.name)
    return mime_type or "image/jpeg"


def load_image_semantic_facts(settings: Settings) -> ImageSemanticFactsBundle:
    path = settings.image_semantic_facts_path
    if not path.exists():
        return ImageSemanticFactsBundle(date=str(date.today()))
    payload = ImageSemanticFactsBundle.model_validate_json(path.read_text(encoding="utf-8"))
    return payload


def save_image_semantic_facts(settings: Settings, bundle: ImageSemanticFactsBundle) -> None:
    _save_json_atomic(settings.image_semantic_facts_path, bundle.model_dump(mode="json"))


def _build_cache_index(bundle: ImageSemanticFactsBundle) -> dict[str, ImageSemanticFact]:
    cache: dict[str, ImageSemanticFact] = {}
    for item in bundle.items:
        cache[item.image_sha256] = item
    return cache


def _request_image_semantics(
    settings: Settings,
    image_path: Path,
) -> tuple[dict[str, Any], str]:
    api_key = settings.resolved_vision_llm_api_key
    if not api_key:
        raise RuntimeError("未配置视觉模型 API Key。")

    mime_type = _guess_mime_type(image_path)
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    request_payload = {
        "model": settings.resolved_vision_llm_model,
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是电商商品图片分析助手。"
                    "请仅基于图片可见内容输出 JSON。"
                    "不要把背景道具误判为商品卖点。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请分析这张商品图，只返回一个 JSON 对象。"
                            "字段必须包含：summary, category, colors, material_guesses, "
                            "visible_elements, product_elements, background_elements, "
                            "style_moods, scene_guesses, confidence_notes。"
                            "如果无法确认，请保守表达。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}",
                        },
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.resolved_vision_llm_base_url.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = client.post(url, headers=headers, json=request_payload)
        response.raise_for_status()
        payload = response.json()

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("视觉模型返回为空，未找到 choices。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        raw_text = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    else:
        raw_text = _normalize_text(content)
    if not raw_text:
        raise RuntimeError("视觉模型返回为空，未找到文本内容。")
    return _extract_json_payload(raw_text), raw_text


def _normalize_semantic_fact(
    *,
    image_path: Path,
    image_sha256: str,
    width: int,
    height: int,
    raw_payload: dict[str, Any] | None,
    raw_text: str | None,
    model: str,
    error: str | None = None,
) -> ImageSemanticFact:
    payload = raw_payload or {}
    status = "failed" if error else "success"
    return ImageSemanticFact(
        image_sha256=image_sha256,
        path=str(image_path),
        width=width,
        height=height,
        model=model,
        analyzed_at=datetime.now().isoformat(),
        status=status,
        summary=_normalize_text(payload.get("summary")),
        category=_normalize_text(payload.get("category")),
        colors=_normalize_list(payload.get("colors"), limit=5),
        material_guesses=_normalize_list(payload.get("material_guesses") or payload.get("material_guess"), limit=3),
        visible_elements=_normalize_list(payload.get("visible_elements"), limit=10),
        product_elements=_normalize_list(payload.get("product_elements"), limit=10),
        background_elements=_normalize_list(payload.get("background_elements"), limit=10),
        style_moods=_normalize_list(payload.get("style_moods") or payload.get("style_mood"), limit=5),
        scene_guesses=_normalize_list(payload.get("scene_guesses") or payload.get("scene_guess"), limit=5),
        confidence_notes=_normalize_list(payload.get("confidence_notes"), limit=5),
        error=error,
        raw_text=raw_text,
    )


def analyze_image_semantics(
    settings: Settings,
    image_path: str,
    *,
    cache_bundle: ImageSemanticFactsBundle | None = None,
) -> ImageSemanticFact:
    bundle = cache_bundle or load_image_semantic_facts(settings)
    image = Path(image_path)
    image_sha256 = _image_sha256(image)
    cache_index = _build_cache_index(bundle)
    cached = cache_index.get(image_sha256)
    if cached:
        cached_date = cached.analyzed_at[:10] if cached.analyzed_at else ""
        if cached.path != str(image):
            cached.path = str(image)
            save_image_semantic_facts(settings, bundle)
        if cached.status == "success" or cached_date == str(date.today()):
            return cached

    with Image.open(image) as img:
        width, height = img.width, img.height

    try:
        raw_payload, raw_text = _request_image_semantics(settings, image)
        item = _normalize_semantic_fact(
            image_path=image,
            image_sha256=image_sha256,
            width=width,
            height=height,
            raw_payload=raw_payload,
            raw_text=raw_text,
            model=settings.resolved_vision_llm_model,
        )
    except Exception as exc:
        item = _normalize_semantic_fact(
            image_path=image,
            image_sha256=image_sha256,
            width=width,
            height=height,
            raw_payload=None,
            raw_text=None,
            model=settings.resolved_vision_llm_model,
            error=str(exc),
        )

    bundle.items.append(item)
    bundle.date = str(date.today())
    save_image_semantic_facts(settings, bundle)
    return item


def analyze_product_image_semantics(
    settings: Settings,
    *,
    product_id: str,
    product_name: str,
    image_paths: list[str],
    cache_bundle: ImageSemanticFactsBundle | None = None,
) -> ProductSemanticFacts:
    bundle = cache_bundle or load_image_semantic_facts(settings)
    image_results = [analyze_image_semantics(settings, path, cache_bundle=bundle) for path in image_paths]
    successful = [item for item in image_results if item.status == "success"]
    if not successful:
        return ProductSemanticFacts(
            product_id=product_id,
            product_name=product_name,
            image_count=len(image_results),
            images=image_results,
            confidence_notes=["semantic_analysis_unavailable"],
        )

    def merge_unique(values: list[str], result: list[str], *, limit: int) -> list[str]:
        for value in values:
            if value and value not in result:
                result.append(value)
            if len(result) >= limit:
                break
        return result

    color_values: list[str] = []
    visible_elements: list[str] = []
    product_elements: list[str] = []
    background_elements: list[str] = []
    style_moods: list[str] = []
    scene_guesses: list[str] = []
    confidence_notes: list[str] = []
    categories: list[str] = []
    material_counter: Counter[str] = Counter()
    summaries: list[str] = []

    for item in successful:
        merge_unique(item.colors, color_values, limit=5)
        merge_unique(item.visible_elements, visible_elements, limit=12)
        merge_unique(item.product_elements, product_elements, limit=12)
        merge_unique(item.background_elements, background_elements, limit=12)
        merge_unique(item.style_moods, style_moods, limit=5)
        merge_unique(item.scene_guesses, scene_guesses, limit=5)
        merge_unique(item.confidence_notes, confidence_notes, limit=5)
        merge_unique([item.category], categories, limit=3)
        summaries = merge_unique([item.summary], summaries, limit=3)
        material_counter.update(item.material_guesses)

    material_guesses = [value for value, _ in material_counter.most_common(2)]
    summary = "；".join(summaries[:2]) or f"{product_name}商品图语义分析结果"
    return ProductSemanticFacts(
        product_id=product_id,
        product_name=product_name,
        image_count=len(image_results),
        summary=summary,
        categories=categories,
        colors=color_values,
        material_guesses=material_guesses,
        visible_elements=visible_elements,
        product_elements=product_elements,
        background_elements=background_elements,
        style_moods=style_moods,
        scene_guesses=scene_guesses,
        confidence_notes=confidence_notes,
        images=image_results,
    )
