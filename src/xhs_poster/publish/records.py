from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..models import PublishDailyRecords, PublishRecord


def _save_json_atomic(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return str(path)


def load_daily_records(settings: Settings, record_date: str) -> PublishDailyRecords:
    path = settings.publish_records_path(record_date)
    if not path.exists():
        return PublishDailyRecords(date=record_date)
    try:
        return PublishDailyRecords.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"publish records.json 结构损坏：{path}，{exc}") from exc


def save_daily_records(settings: Settings, records: PublishDailyRecords) -> str:
    path = settings.publish_records_path(records.date)
    return _save_json_atomic(path, records.model_dump(mode="json"))


def append_record(
    settings: Settings,
    *,
    record_date: str,
    record: PublishRecord,
) -> str:
    daily_records = load_daily_records(settings, record_date)
    daily_records.records.append(record)
    return save_daily_records(settings, daily_records)


def save_publish_evidence(page, settings: Settings, *, record_date: str, product_id: str) -> dict:
    """失败时落盘截图与 HTML（M8 再扩 trace/steps）。"""
    evidence_dir = settings.publish_evidence_dir(record_date)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = evidence_dir / f"{product_id}-{stamp}.png"
    html_path = evidence_dir / f"{product_id}-{stamp}.html"
    page.screenshot_on_failure(str(screenshot_path))
    html_path.write_text(page.page.content(), encoding="utf-8")
    return {
        "screenshot": str(screenshot_path),
        "html": str(html_path),
    }
