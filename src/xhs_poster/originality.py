from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import ContentDraft

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


def check_draft_similarity(draft: ContentDraft, previous_drafts: list[ContentDraft]) -> str | None:
    """Return a reason string if draft is too similar to any previous draft, None otherwise."""
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
