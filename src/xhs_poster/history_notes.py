from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .config import Settings
from .models import HistoryStyleReference, ProductSummary

HASHTAG_RE = re.compile(r"#([^\s#]+)")
TRAILING_HASHTAGS_RE = re.compile(r"(?:\s*#[^\s#]+)+\s*$")
REFERENCE_TERMS = [
    "鸡蛋花",
    "山茶花",
    "蝴蝶结",
    "蝴蝶",
    "云朵",
    "千鸟格",
    "格子",
    "碎花",
    "玳瑁",
    "琉璃",
    "磨砂",
    "珠光",
    "粉色",
    "方夹",
    "圆角夹",
    "圆环夹",
    "交叉夹",
    "鸭嘴夹",
    "刘海夹",
    "抓夹",
    "发夹",
    "鲨鱼夹",
    "发饰",
    "头饰",
    "韩系",
    "复古",
    "清新",
    "高级感",
    "温柔",
]
CORE_REFERENCE_TERMS = [
    "鸡蛋花",
    "山茶花",
    "蝴蝶结",
    "蝴蝶",
    "云朵",
    "千鸟格",
    "格子",
    "碎花",
    "玳瑁",
    "琉璃",
    "磨砂",
    "珠光",
    "方夹",
    "圆角夹",
    "圆环夹",
    "交叉夹",
    "鸭嘴夹",
    "刘海夹",
]
GENERIC_STYLE_TERMS = {
    "抓夹",
    "发夹",
    "鲨鱼夹",
    "发饰",
    "头饰",
    "头饰发饰",
    "发饰分享",
    "我的平价好物",
    "平价发饰",
    "高级感",
    "复古",
    "百搭",
    "韩系",
    "清新",
    "温柔",
    "氛围感",
    "少女感",
    "可爱",
    "精致",
    "日常出门",
    "通勤",
    "约会",
    "粉色系",
}
BLOCKED_TAG_TERMS = {
    "1年1度购物狂欢",
    "生日",
    "宝藏饰品大公开",
    "植物系穿搭",
    "显瘦",
    "美式复古",
    "彩色",
}


def _filename_label(path: str) -> str:
    name = Path(path).stem
    name = re.sub(r"_+publish_note(?:_copy)*$", "", name)
    name = re.sub(r"\d+$", "", name)
    return name.strip("_")


def _extract_reference_terms(*texts: str) -> list[str]:
    result: list[str] = []
    combined = " ".join(texts)
    for term in REFERENCE_TERMS:
        if term in combined and term not in result:
            result.append(term)
    return result


def _normalize_sentence_text(text: str) -> str:
    text = text.replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_value(lines: list[str], key: str) -> str:
    for index, line in enumerate(lines):
        if line.strip() != f"{key}:":
            continue
        for next_line in lines[index + 1 :]:
            stripped = next_line.strip()
            if not stripped:
                continue
            if stripped.startswith("content:"):
                return stripped.removeprefix("content:").strip().strip('"')
            break
    return ""


def _extract_search_key(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("search_key:"):
            return stripped.removeprefix("search_key:").strip().strip('"')
    return ""


def _extract_hashtags(text: str) -> list[str]:
    hashtags: list[str] = []
    for tag in HASHTAG_RE.findall(text or ""):
        normalized = f"#{tag.strip()}"
        if normalized == "#" or normalized in hashtags:
            continue
        hashtags.append(normalized)
    return hashtags


def _clean_content_and_tags(
    title: str,
    content: str,
    hashtags: list[str],
    filename_label: str,
) -> tuple[str, list[str], list[str], list[str]]:
    normalization_notes: list[str] = []
    cleaned_title = _normalize_sentence_text(title)
    cleaned_content = _normalize_sentence_text(TRAILING_HASHTAGS_RE.sub("", content or ""))
    if cleaned_content != _normalize_sentence_text(content):
        normalization_notes.append("removed_trailing_hashtags")

    anchor_terms = set(_extract_reference_terms(filename_label, cleaned_title, cleaned_content))
    cleaned_hashtags: list[str] = []
    removed_count = 0
    for tag in hashtags:
        tag_text = tag.lstrip("#").strip()
        if not tag_text:
            removed_count += 1
            continue
        if tag_text in BLOCKED_TAG_TERMS:
            removed_count += 1
            continue
        if (
            tag_text in GENERIC_STYLE_TERMS
            or any(term in tag_text for term in anchor_terms)
            or any(term in cleaned_content for term in _extract_reference_terms(tag_text))
        ):
            if tag not in cleaned_hashtags:
                cleaned_hashtags.append(tag)
            continue
        removed_count += 1

    if removed_count:
        normalization_notes.append(f"filtered_{removed_count}_hashtags")
    if len(cleaned_hashtags) > 8:
        cleaned_hashtags = cleaned_hashtags[:8]
        normalization_notes.append("trimmed_hashtags_to_8")

    cleaned_reference_terms = _extract_reference_terms(
        filename_label,
        cleaned_title,
        cleaned_content,
        " ".join(cleaned_hashtags),
    )
    return cleaned_content, cleaned_hashtags, cleaned_reference_terms, normalization_notes


def _quality_flags(title: str, content: str, hashtags: list[str]) -> list[str]:
    flags: list[str] = []
    if len(hashtags) >= 12:
        flags.append("hashtag_overload")

    repeated = len(set(hashtags)) != len(hashtags)
    if repeated:
        flags.append("duplicate_hashtags")

    content_text = f"{title} {content}"
    floral_terms = [term for term in ("鸡蛋花", "山茶花", "玫瑰") if term in content_text]
    if len(floral_terms) >= 2:
        flags.append("cross_product_terms")

    if len(content.strip()) <= 24:
        flags.append("content_too_short")
    return flags


def _quality_score(title: str, content: str, hashtags: list[str], flags: list[str]) -> int:
    score = 100
    penalties = {
        "hashtag_overload": 18,
        "duplicate_hashtags": 12,
        "cross_product_terms": 30,
        "content_too_short": 15,
    }
    for flag in flags:
        score -= penalties.get(flag, 0)

    if 3 <= len(hashtags) <= 8:
        score += 5
    elif len(hashtags) == 0:
        score -= 10

    title_length = len(title.strip())
    if 6 <= title_length <= 20:
        score += 5

    content_length = len(content.strip())
    if 35 <= content_length <= 180:
        score += 5

    return max(0, min(100, score))


def _usage_flags(
    score: int,
    flags: list[str],
    *,
    raw_hashtag_count: int,
    cleaned_hashtag_count: int,
) -> tuple[bool, bool]:
    removed_hashtag_count = max(0, raw_hashtag_count - cleaned_hashtag_count)
    aggressive_cleanup = removed_hashtag_count >= 5
    low_retention = raw_hashtag_count >= 6 and cleaned_hashtag_count / max(1, raw_hashtag_count) < 0.4
    use_for_style = (
        score >= 75
        and "cross_product_terms" not in flags
        and "hashtag_overload" not in flags
        and "duplicate_hashtags" not in flags
        and not aggressive_cleanup
        and not low_retention
    )
    use_for_trend = score >= 45 and "content_too_short" not in flags
    return use_for_style, use_for_trend


def _score_history_ref(product: ProductSummary, ref: HistoryStyleReference) -> int:
    if not ref.use_for_style:
        return 0

    product_terms = _extract_reference_terms(product.name)
    if not product_terms:
        return 0

    product_core_terms = [term for term in product_terms if term in CORE_REFERENCE_TERMS]
    filename_terms = _extract_reference_terms(ref.filename_label or _filename_label(ref.source_file))
    title_terms = _extract_reference_terms(ref.cleaned_title or ref.title)
    hashtag_terms = _extract_reference_terms(" ".join(ref.cleaned_hashtags or ref.hashtags))
    content_terms = ref.cleaned_reference_terms or _extract_reference_terms(ref.cleaned_content or ref.content)
    ref_core_terms = {
        *[term for term in filename_terms if term in CORE_REFERENCE_TERMS],
        *[term for term in title_terms if term in CORE_REFERENCE_TERMS],
    }

    if product_core_terms and not any(term in ref_core_terms for term in product_core_terms):
        return 0

    score = 0
    for term in product_terms:
        if term in filename_terms:
            score += 8
        if term in title_terms:
            score += 5
        if term in hashtag_terms:
            score += 2
        if term in content_terms:
            score += 1

    if "cross_product_terms" in ref.quality_flags:
        score -= 3
    if "hashtag_overload" in ref.quality_flags:
        score -= 1
    score += min(10, ref.quality_score // 10)
    return score


def parse_history_note(path: Path) -> HistoryStyleReference | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = _extract_value(lines, "title")
    content = _extract_value(lines, "describe")
    product_search_key = _extract_search_key(lines)
    if not title or not content or not product_search_key:
        return None

    hashtags = _extract_hashtags(content)
    filename_label = _filename_label(str(path))
    cleaned_content, cleaned_hashtags, cleaned_reference_terms, normalization_notes = _clean_content_and_tags(
        title,
        content,
        hashtags,
        filename_label,
    )
    cleaned_title = _normalize_sentence_text(title)
    flags = _quality_flags(cleaned_title, cleaned_content, cleaned_hashtags)
    score = _quality_score(cleaned_title, cleaned_content, cleaned_hashtags, flags)
    use_for_style, use_for_trend = _usage_flags(
        score,
        flags,
        raw_hashtag_count=len(hashtags),
        cleaned_hashtag_count=len(cleaned_hashtags),
    )
    reference_terms = _extract_reference_terms(filename_label, title, content, " ".join(hashtags))
    return HistoryStyleReference(
        product_search_key=product_search_key,
        title=title,
        content=content,
        hashtags=hashtags,
        cleaned_title=cleaned_title,
        cleaned_content=cleaned_content,
        cleaned_hashtags=cleaned_hashtags,
        quality_flags=flags,
        quality_score=score,
        use_for_style=use_for_style,
        use_for_trend=use_for_trend,
        filename_label=filename_label,
        reference_terms=reference_terms,
        cleaned_reference_terms=cleaned_reference_terms,
        normalization_notes=normalization_notes,
        source_file=str(path),
    )


def load_history_style_refs(settings: Settings) -> list[HistoryStyleReference]:
    refs: list[HistoryStyleReference] = []
    if not settings.history_notes_dir.exists():
        return refs

    for path in sorted(settings.history_notes_dir.glob("*.yaml")):
        ref = parse_history_note(path)
        if ref is None:
            continue
        refs.append(ref)
    return refs


def save_history_style_refs(settings: Settings, refs: list[HistoryStyleReference]) -> None:
    payload = {
        "items": [ref.model_dump(mode="json") for ref in refs],
        "total": len(refs),
        "usable_for_style": sum(1 for ref in refs if ref.use_for_style),
        "usable_for_trend": sum(1 for ref in refs if ref.use_for_trend),
    }
    temp_path = settings.history_style_refs_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(settings.history_style_refs_path)


def group_history_style_refs(
    refs: list[HistoryStyleReference],
) -> dict[str, list[HistoryStyleReference]]:
    grouped: dict[str, list[HistoryStyleReference]] = defaultdict(list)
    seen_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for ref in refs:
        dedupe_key = (ref.title.strip(), ref.content.strip())
        if dedupe_key in seen_pairs[ref.product_search_key]:
            continue
        seen_pairs[ref.product_search_key].add(dedupe_key)
        grouped[ref.product_search_key].append(ref)
    return dict(grouped)


def select_history_style_refs(
    product: ProductSummary,
    grouped_refs: dict[str, list[HistoryStyleReference]],
    *,
    limit: int = 3,
) -> list[HistoryStyleReference]:
    direct_refs = grouped_refs.get(product.id, [])
    direct_style_refs = [ref for ref in direct_refs if ref.use_for_style]
    if direct_style_refs:
        direct_style_refs.sort(key=lambda ref: ref.quality_score, reverse=True)
        return direct_style_refs[:limit]

    if not _extract_reference_terms(product.name):
        return []

    scored: list[tuple[int, HistoryStyleReference]] = []
    for refs in grouped_refs.values():
        for ref in refs:
            score = _score_history_ref(product, ref)
            if score > 0:
                scored.append((score, ref))

    scored.sort(
        key=lambda item: (
            item[0],
            -len(item[1].quality_flags),
            len(item[1].hashtags),
        ),
        reverse=True,
    )
    selected: list[HistoryStyleReference] = []
    seen_files: set[str] = set()
    for _, ref in scored:
        if ref.source_file in seen_files:
            continue
        selected.append(ref)
        seen_files.add(ref.source_file)
        if len(selected) >= limit:
            break
    return selected
