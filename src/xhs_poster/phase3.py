from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .auth import LoginRequiredError, require_authenticated_session
from .browser import get_alive_page, merchant_context, open_product_list_page
from .config import Settings
from .merchant import ProductListPage
from .models import (
    ContentDraft,
    ContentsBundle,
    Phase3ExecutionResult,
    Phase3Success,
    ProductSummary,
    SkillError,
    TodayPool,
)


def load_today_pool(settings: Settings) -> TodayPool:
    if not settings.today_pool_path.exists():
        raise RuntimeError(
            f"未找到 today-pool.json，请先执行 phase1：{settings.today_pool_path}"
        )
    return TodayPool.model_validate_json(settings.today_pool_path.read_text(encoding="utf-8"))


def load_contents_bundle(settings: Settings) -> ContentsBundle:
    if not settings.contents_path.exists():
        raise RuntimeError(
            f"未找到 contents.json，且本次也未显式传入标题/正文：{settings.contents_path}"
        )
    return ContentsBundle.model_validate_json(settings.contents_path.read_text(encoding="utf-8"))


def resolve_product(today_pool: TodayPool, product_id: str | None) -> ProductSummary:
    if product_id is None:
        if not today_pool.products:
            raise RuntimeError("today-pool.json 中没有可用商品。")
        return today_pool.products[0]

    for product in today_pool.products:
        if product.id == product_id:
            return product
    raise RuntimeError(f"today-pool.json 中不存在商品 {product_id}。")


def resolve_image_paths(
    settings: Settings,
    today_pool: TodayPool,
    product_id: str,
    *,
    image_paths: list[str] | None = None,
    limit: int = 3,
) -> list[str]:
    if image_paths:
        resolved = [str(Path(path)) for path in image_paths if Path(path).exists()]
    else:
        resolved = [
            path
            for path in today_pool.images.get(product_id, [])
            if Path(path).exists()
        ]

    if len(resolved) < limit:
        product_dir = settings.images_dir / product_id
        if product_dir.exists():
            local_files = sorted(
                [
                    str(path)
                    for path in product_dir.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ]
            )
            seen = set(resolved)
            for path in local_files:
                if path in seen:
                    continue
                resolved.append(path)
                seen.add(path)

    if len(resolved) < limit:
        raise RuntimeError(
            f"商品 {product_id} 可用图片不足 {limit} 张，当前仅找到 {len(resolved)} 张。"
        )
    return resolved[:limit]


def pick_content_draft(
    bundle: ContentsBundle,
    product_id: str,
    *,
    angle: int | None = None,
) -> ContentDraft:
    drafts = bundle.contents.get(product_id, [])
    if not drafts:
        raise RuntimeError(f"contents.json 中不存在商品 {product_id} 的内容草稿。")

    if angle is None:
        return drafts[0]

    for draft in drafts:
        if draft.angle == angle:
            return draft
    raise RuntimeError(f"商品 {product_id} 在 contents.json 中不存在 angle={angle} 的内容草稿。")


def extract_topic_keyword(tags: str) -> str | None:
    match = re.search(r"#([^\s#]+)", tags or "")
    if not match:
        return None
    return match.group(1).strip()


def merge_content_and_tags(content: str, tags: str) -> str:
    normalized_content = (content or "").strip()
    normalized_tags = re.sub(r"\s+", " ", (tags or "")).strip()
    if not normalized_tags:
        return normalized_content
    if normalized_tags in normalized_content:
        return normalized_content
    if not normalized_content:
        return normalized_tags
    return f"{normalized_content}\n\n{normalized_tags}"


def resolve_publish_inputs(
    settings: Settings,
    product_id: str,
    *,
    title: str | None,
    content: str | None,
    topic_keyword: str | None,
    angle: int | None,
) -> tuple[str, str, str | None, ContentDraft | None]:
    if title or content:
        if not title or not content:
            raise RuntimeError("显式传参发布时，`title` 和 `content` 必须同时提供。")
        return title, content, topic_keyword, None

    draft = pick_content_draft(load_contents_bundle(settings), product_id, angle=angle)
    resolved_topic = topic_keyword or extract_topic_keyword(draft.tags)
    final_content = merge_content_and_tags(draft.content, draft.tags)
    return draft.title, final_content, resolved_topic, draft


def save_phase3_artifacts(page, settings: Settings, product_id: str) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = settings.phase3_artifacts_dir / f"phase3-{product_id}-{stamp}.png"
    html_path = settings.phase3_artifacts_dir / f"phase3-{product_id}-{stamp}.html"
    page.screenshot_on_failure(str(screenshot_path))
    html_path.write_text(page.page.content(), encoding="utf-8")
    return {
        "screenshot": str(screenshot_path),
        "html": str(html_path),
    }


def append_publish_log(settings: Settings, record: dict) -> str:
    log_path = settings.publish_log_path
    payload = {"records": []}
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("records"), list):
                payload = existing
        except json.JSONDecodeError:
            payload = {"records": []}

    payload["records"].append(record)
    temp_path = log_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(log_path)
    return str(log_path)


def run_phase3(
    *,
    product_id: str | None = None,
    angle: int | None = None,
    title: str | None = None,
    content: str | None = None,
    topic_keyword: str | None = None,
    image_paths: list[str] | None = None,
    headless: bool | None = None,
    settings: Settings | None = None,
) -> Phase3ExecutionResult:
    settings = settings or Settings()
    settings.ensure_directories()
    session = require_authenticated_session("merchant", settings)
    run_headless = session.browser_mode == "headless" if headless is None else headless

    today_pool = load_today_pool(settings)
    product = resolve_product(today_pool, product_id)
    final_title, final_content, final_topic, draft = resolve_publish_inputs(
        settings,
        product.id,
        title=title,
        content=content,
        topic_keyword=topic_keyword,
        angle=angle,
    )
    final_image_paths = resolve_image_paths(
        settings,
        today_pool,
        product.id,
        image_paths=image_paths,
    )

    with merchant_context(settings, headless=run_headless) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page = open_product_list_page(context, page, settings)
        list_page = ProductListPage(page, settings)
        publish_page = list_page.open_publish_page(product.id)

        publish_page.upload_images(final_image_paths)
        title_selector = publish_page.fill_title(final_title)
        content_selector = publish_page.fill_content(final_content)
        topic_result = publish_page.add_topic(final_topic) if final_topic else None
        product_binding = publish_page.add_product(product.id)
        publish_page.click_publish()
        publish_result = publish_page.verify_success()
        artifacts = None
        if not publish_result.get("success"):
            artifacts = save_phase3_artifacts(publish_page, settings, product.id)

    result = Phase3ExecutionResult(
        product_id=product.id,
        product_name=product.name,
        title=final_title,
        content=final_content,
        topic_keyword=final_topic,
        angle=draft.angle if draft else angle,
        angle_name=draft.angle_name if draft else None,
        image_paths=final_image_paths,
        title_selector=title_selector,
        content_selector=content_selector,
        topic_result=topic_result,
        product_binding=product_binding,
        publish_result=publish_result,
        artifacts=artifacts,
    )
    result.log_path = str(settings.publish_log_path)

    append_publish_log(
        settings,
        {
            "timestamp": datetime.now().isoformat(),
            "status": "ok" if publish_result.get("success") else "error",
            **result.model_dump(mode="json"),
        },
    )
    return result


def build_phase3_payload(
    *,
    product_id: str | None = None,
    angle: int | None = None,
    title: str | None = None,
    content: str | None = None,
    topic_keyword: str | None = None,
    image_paths: list[str] | None = None,
) -> tuple[dict, int]:
    try:
        result = run_phase3(
            product_id=product_id,
            angle=angle,
            title=title,
            content=content,
            topic_keyword=topic_keyword,
            image_paths=image_paths,
        )
        if result.publish_result.get("success"):
            payload = Phase3Success(data=result)
            return payload.model_dump(mode="json"), 0

        payload = SkillError(
            error="PHASE3_PUBLISH_FAILED",
            message="笔记发布失败或成功信号不明确，已保留结构化结果与失败证据。",
            site="merchant",
            details=result.model_dump(mode="json"),
        )
        return payload.model_dump(mode="json"), 1
    except LoginRequiredError as exc:
        payload = SkillError(
            error="LOGIN_REQUIRED",
            message=exc.session.message,
            site=exc.session.site,
            login=exc.session,
        )
        return payload.model_dump(mode="json"), 2
    except Exception as exc:
        payload = SkillError(
            error="PHASE3_FAILED",
            message=str(exc),
            site="merchant",
        )
        return payload.model_dump(mode="json"), 1


def main() -> None:
    payload, exit_code = build_phase3_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
