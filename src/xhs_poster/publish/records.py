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


def build_evidence_dir(
    settings: Settings,
    *,
    record_date: str,
    product_id: str,
    angle: int | None,
) -> Path:
    """为单篇懒建证据子目录 ``publish/<date>/evidence/<product_id>-<angle>-<HHMMSS>/``。"""
    stamp = datetime.now().strftime("%H%M%S")
    evidence_dir = settings.publish_evidence_dir(record_date) / f"{product_id}-{angle or 0}-{stamp}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return evidence_dir


def save_steps_evidence(
    evidence_dir: Path,
    *,
    steps: list[dict] | None,
    meta: dict,
) -> dict:
    """落盘**内存现场**：逐步明细 steps.jsonl + 概要 meta.json。

    只写内存里已有的数据，完全不碰浏览器——所以无论页面是否卡死/崩溃都能成功落盘，
    是「任何失败都必须留现场」的底线保证。页面截图/HTML 由 ``capture_page_evidence`` 另行尽力采集。
    """
    artifacts: dict = {"dir": str(evidence_dir)}
    steps_path = evidence_dir / "steps.jsonl"
    steps_path.write_text(
        "".join(json.dumps(step, ensure_ascii=False) + "\n" for step in (steps or [])),
        encoding="utf-8",
    )
    artifacts["steps"] = str(steps_path)
    meta_path = evidence_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["meta"] = str(meta_path)
    return artifacts


def capture_page_evidence(page, evidence_dir: Path) -> dict:
    """采集**页面现场**：截图 + HTML。尽力而为——页面卡死/崩溃时可能抛错或被外层看门狗打断，
    调用方需用短超时包裹并隔离异常。trace.zip 由会话侧 ``context.tracing.stop(path=...)`` 另写同目录。
    """
    screenshot_path = evidence_dir / "screenshot.png"
    html_path = evidence_dir / "page.html"
    page.screenshot_on_failure(str(screenshot_path))
    html_path.write_text(page.page.content(), encoding="utf-8")
    return {"screenshot": str(screenshot_path), "html": str(html_path)}
