from __future__ import annotations

import json
from datetime import date

from .config import Settings
from .history_notes import load_history_style_refs
from .hot_notes import analyze_hot_notes, build_fallback_hot_notes_analysis
from .models import HistoryStyleReference, HotNote, HotNotesAnalysis


def _history_ref_to_hot_note(ref: HistoryStyleReference) -> HotNote:
    return HotNote(
        note_id=ref.source_file.rsplit("/", maxsplit=1)[-1],
        title=ref.cleaned_title or ref.title,
        url=ref.source_file,
        author="history_yaml",
        tags=ref.cleaned_hashtags or ref.hashtags,
        content=ref.cleaned_content or ref.content,
    )


def build_trend_signals_from_history_refs(
    *,
    keyword: str,
    refs: list[HistoryStyleReference],
) -> HotNotesAnalysis:
    refs = [ref for ref in refs if ref.use_for_trend]
    matched_refs = [
        ref
        for ref in refs
        if (
            keyword in (ref.cleaned_title or ref.title)
            or keyword in (ref.cleaned_content or ref.content)
            or any(keyword in tag for tag in (ref.cleaned_hashtags or ref.hashtags))
        )
    ]
    if not matched_refs:
        matched_refs = refs

    notes = [_history_ref_to_hot_note(ref) for ref in matched_refs[:30]]
    if not notes:
        analysis = build_fallback_hot_notes_analysis(keyword)
        analysis.source = "local_fallback"
        return analysis

    analysis = analyze_hot_notes(keyword, notes)
    analysis.source = "history_style_refs"
    return analysis


def save_trend_signals(settings: Settings, analysis: HotNotesAnalysis) -> None:
    payload = {
        "date": str(date.today()),
        "data": analysis.model_dump(mode="json"),
    }
    temp_path = settings.trend_signals_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(settings.trend_signals_path)


def build_trend_signals_payload(
    *,
    keyword: str | None = None,
    settings: Settings | None = None,
) -> tuple[dict, int]:
    settings = settings or Settings()
    settings.ensure_directories()
    refs = load_history_style_refs(settings)
    trend_refs = [ref for ref in refs if ref.use_for_trend]
    final_keyword = keyword or "发饰"
    analysis = build_trend_signals_from_history_refs(keyword=final_keyword, refs=refs)
    save_trend_signals(settings, analysis)
    payload = {
        "status": "ok",
        "data": {
            "date": str(date.today()),
            **analysis.model_dump(mode="json"),
            "trend_signals_path": str(settings.trend_signals_path),
            "history_refs_count": len(refs),
            "trend_usable_refs_count": len(trend_refs),
        },
    }
    return payload, 0
