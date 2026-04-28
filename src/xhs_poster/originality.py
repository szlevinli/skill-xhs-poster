from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from .models import ContentDraft, HistoryStyleReference, OriginalityCheck

CORE_INPUT_TYPES = (
    "新案例",
    "新数据",
    "新实测",
    "新个人经验",
    "新对比样本",
    "新失败教训",
)
SUPPORTING_DIFFERENCE_TYPES = (
    "不同人群",
    "不同场景",
    "不同决策问题",
    "不同观点/结论",
    "不同结构组织",
    "不同表达切口",
    "不同素材组合方式",
)
_HIGH_SIMILARITY_THRESHOLD = 0.72
_TITLE_SIMILARITY_THRESHOLD = 0.80
_CONTENT_SIMILARITY_THRESHOLD = 0.76
_TITLE_CONTENT_COMPOUND_THRESHOLD = 0.62


def normalize_for_similarity(text: str) -> str:
    normalized = re.sub(r"#[^\s#]+", "", text or "")
    normalized = re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)
    return normalized.lower()


def similarity_ratio(left: str, right: str) -> float:
    left_norm = normalize_for_similarity(left)
    right_norm = normalize_for_similarity(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _history_title(ref: HistoryStyleReference) -> str:
    return (ref.cleaned_title or ref.title).strip()


def _history_content(ref: HistoryStyleReference) -> str:
    return (ref.cleaned_content or ref.content).strip()


def _history_text(ref: HistoryStyleReference) -> str:
    return f"{_history_title(ref)}\n{_history_content(ref)}".strip()


def _clean_terms(terms: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for term in terms:
        normalized = re.sub(r"\s+", "", str(term or "")).strip()
        if not normalized or len(normalized) < 2:
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def nearest_history_notes(
    draft: ContentDraft,
    history_style_refs: Iterable[HistoryStyleReference],
    *,
    limit: int = 3,
) -> list[str]:
    draft_text = f"{draft.title}\n{draft.content}"
    scored: list[tuple[float, str]] = []
    for ref in history_style_refs:
        label = ref.source_file.rsplit("/", maxsplit=1)[-1] or ref.title
        score = similarity_ratio(draft_text, _history_text(ref))
        if score > 0:
            scored.append((score, f"{label}:{score:.2f}"))
    scored.sort(reverse=True, key=lambda item: item[0])
    return [label for _, label in scored[:limit]]


def _check_history_template_reuse(draft: ContentDraft, history_style_refs: list[HistoryStyleReference]) -> str | None:
    draft_text = f"{draft.title}\n{draft.content}"
    for ref in history_style_refs:
        full_score = similarity_ratio(draft_text, _history_text(ref))
        if full_score >= _HIGH_SIMILARITY_THRESHOLD:
            return f"high_similarity_to_history:{full_score:.2f}"
        title_score = similarity_ratio(draft.title, _history_title(ref))
        content_score = similarity_ratio(draft.content, _history_content(ref))
        if title_score >= _TITLE_SIMILARITY_THRESHOLD and content_score >= _TITLE_CONTENT_COMPOUND_THRESHOLD:
            return f"template_like_history_title_content:{title_score:.2f}/{content_score:.2f}"
        if content_score >= _CONTENT_SIMILARITY_THRESHOLD:
            return f"template_like_history_content:{content_score:.2f}"
    return None


def _check_generated_draft_reuse(draft: ContentDraft, previous_drafts: list[ContentDraft]) -> str | None:
    draft_text = f"{draft.title}\n{draft.content}"
    for previous in previous_drafts:
        full_score = similarity_ratio(draft_text, f"{previous.title}\n{previous.content}")
        if full_score >= _HIGH_SIMILARITY_THRESHOLD:
            return f"high_similarity_to_generated_draft:{full_score:.2f}"
        title_score = similarity_ratio(draft.title, previous.title)
        content_score = similarity_ratio(draft.content, previous.content)
        if title_score >= _TITLE_SIMILARITY_THRESHOLD and content_score >= _TITLE_CONTENT_COMPOUND_THRESHOLD:
            return f"template_like_generated_title_content:{title_score:.2f}/{content_score:.2f}"
        if content_score >= _CONTENT_SIMILARITY_THRESHOLD:
            return f"template_like_generated_content:{content_score:.2f}"
    return None


def build_default_originality_check(
    draft: ContentDraft,
    *,
    product_name: str,
    core_input_type: str,
    core_input_evidence: str,
    product_fact_anchors: list[str],
    supporting_differences: list[str],
    grounding_terms: list[str] | None = None,
    history_style_refs: list[HistoryStyleReference] | None = None,
    previous_drafts: list[ContentDraft] | None = None,
) -> OriginalityCheck:
    del product_name
    del grounding_terms
    history_style_refs = history_style_refs or []
    previous_drafts = previous_drafts or []
    rejection_reasons: list[str] = []

    if core_input_type not in CORE_INPUT_TYPES:
        rejection_reasons.append("missing_valid_core_input_type")
    if not core_input_evidence.strip():
        rejection_reasons.append("missing_core_input_evidence")
    if len([item for item in supporting_differences if item.strip()]) < 2:
        rejection_reasons.append("missing_two_supporting_differences")

    nearest = nearest_history_notes(draft, history_style_refs)
    history_reuse_reason = _check_history_template_reuse(draft, history_style_refs)
    if history_reuse_reason is not None:
        rejection_reasons.append(history_reuse_reason)
    generated_reuse_reason = _check_generated_draft_reuse(draft, previous_drafts)
    if generated_reuse_reason is not None:
        rejection_reasons.append(generated_reuse_reason)

    cleaned_anchors = _clean_terms(product_fact_anchors)

    deduped_reasons: list[str] = []
    for reason in rejection_reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return OriginalityCheck(
        passed=not deduped_reasons,
        core_input_type=core_input_type if core_input_type in CORE_INPUT_TYPES else "",
        core_input_evidence=core_input_evidence.strip(),
        product_fact_anchors=cleaned_anchors[:4],
        supporting_differences=supporting_differences[:3],
        nearest_history_notes=nearest,
        rejection_reasons=deduped_reasons,
    )


def coerce_originality_check(value: object) -> OriginalityCheck | None:
    if isinstance(value, OriginalityCheck):
        return value
    if isinstance(value, dict):
        return OriginalityCheck.model_validate(value)
    return None


def assert_publishable_originality(draft: ContentDraft) -> None:
    check = draft.originality_check
    if check is None:
        raise RuntimeError("原创性闸门未填写：缺少 1 个新核心输入 + 2 个支持性差异。")
    if not check.passed:
        reasons = ", ".join(check.rejection_reasons) or "unknown"
        raise RuntimeError(f"原创性闸门未通过，禁止发布：{reasons}")
    if not check.core_input_type or not check.core_input_evidence.strip():
        raise RuntimeError("原创性闸门未通过：没有新核心输入，禁止发布。")
    if len([item for item in check.supporting_differences if item.strip()]) < 2:
        raise RuntimeError("原创性闸门未通过：不足 2 个支持性差异，禁止发布。")
