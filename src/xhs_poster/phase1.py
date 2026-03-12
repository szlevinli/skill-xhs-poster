from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .auth import LoginRequiredError, require_authenticated_session
from .browser import get_alive_page, merchant_context, open_product_list_page
from .config import Settings
from .merchant import ProductListPage
from .models import (
    Phase1ExecutionResult,
    Phase1ProductState,
    Phase1State,
    Phase1Success,
    ProductFailure,
    ProductSummary,
    SkillError,
    TodayPool,
)


def now_iso() -> str:
    return datetime.now().isoformat()


def save_json_atomic(path: Path, payload: str) -> None:
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(path)


def load_phase1_state(settings: Settings) -> Phase1State:
    if not settings.phase1_state_path.exists():
        return Phase1State(date=str(date.today()))

    try:
        state = Phase1State.model_validate_json(settings.phase1_state_path.read_text(encoding="utf-8"))
    except Exception:
        return Phase1State(date=str(date.today()))

    state.date = str(date.today())
    return state


def save_phase1_state(settings: Settings, state: Phase1State) -> None:
    settings.ensure_directories()
    save_json_atomic(settings.phase1_state_path, state.model_dump_json(indent=2))


def save_today_pool(settings: Settings, today_pool: TodayPool) -> None:
    settings.ensure_directories()
    save_json_atomic(settings.today_pool_path, today_pool.model_dump_json(indent=2))


def ensure_clean_image_dir(
    settings: Settings,
    products: list[ProductSummary],
    *,
    force_download: bool,
) -> None:
    if not force_download:
        return

    valid_product_ids = {product.id for product in products}
    if not settings.images_dir.exists():
        return

    for child in settings.images_dir.iterdir():
        if not child.is_dir() or child.name in valid_product_ids:
            continue
        for nested in child.iterdir():
            nested.unlink()
        child.rmdir()


def discover_local_image_paths(settings: Settings, product_id: str, *, limit: int) -> list[str]:
    product_dir = settings.images_dir / product_id
    if not product_dir.exists():
        return []

    candidates: list[str] = []
    for index in range(1, limit + 1):
        matched_path = None
        for suffix in (".jpg", ".png", ".jpeg", ".webp"):
            candidate = product_dir / f"{index}{suffix}"
            if candidate.exists():
                matched_path = candidate
                break
        if matched_path is None:
            break
        candidates.append(str(matched_path))

    if len(candidates) >= limit:
        return candidates[:limit]

    fallback = sorted(
        str(path)
        for path in product_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    seen = set(candidates)
    for path in fallback:
        if path in seen:
            continue
        candidates.append(path)
        seen.add(path)
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def mark_images_complete(
    state: Phase1ProductState,
    *,
    image_paths: list[str],
    source: str,
    timestamp: str,
) -> None:
    state.fetch_status = "complete"
    state.last_error = None
    state.updated_at = timestamp
    state.artifacts.images.status = "complete"
    state.artifacts.images.paths = image_paths
    state.artifacts.images.count = len(image_paths)
    state.artifacts.images.source = source


def mark_images_failed(
    state: Phase1ProductState,
    *,
    reason: str,
    timestamp: str,
) -> None:
    state.fetch_status = "failed"
    state.last_error = reason
    state.updated_at = timestamp
    current_count = len([path for path in state.artifacts.images.paths if Path(path).exists()])
    state.artifacts.images.count = current_count
    state.artifacts.images.status = "partial" if current_count else "missing"


def refresh_state_summary(
    state: Phase1State,
    products: list[ProductSummary],
    *,
    target_total: int,
    skipped_count: int,
) -> None:
    current_ids = {product.id for product in products}
    states = [state.products[product_id] for product_id in current_ids if product_id in state.products]
    state.target_total = target_total
    state.success_count = sum(item.fetch_status == "complete" for item in states)
    state.failed_count = sum(item.fetch_status == "failed" for item in states)
    state.processed_count = sum(item.fetch_status in {"complete", "failed"} for item in states)
    state.skipped_count = skipped_count
    state.updated_at = now_iso()


def build_today_pool_from_state(
    products: list[ProductSummary],
    state: Phase1State,
    *,
    limit: int,
    target_count: int,
) -> TodayPool:
    success_products: list[ProductSummary] = []
    images: dict[str, list[str]] = {}
    failed_products: list[ProductFailure] = []

    for product in products:
        product_state = state.products.get(product.id)
        if product_state is None:
            continue

        image_paths = [path for path in product_state.artifacts.images.paths if Path(path).exists()]
        if product_state.fetch_status == "complete" and image_paths:
            success_products.append(product)
            images[product.id] = image_paths[:limit]
            if len(success_products) >= target_count:
                break
            continue

        if product_state.last_error:
            failed_products.append(
                ProductFailure(
                    product_id=product.id,
                    product_name=product.name,
                    reason=product_state.last_error,
                )
            )

    status = "complete" if len(success_products) >= target_count else "partial"
    return TodayPool(
        date=str(date.today()),
        status=status,
        generated_at=now_iso(),
        products=success_products,
        images=images,
        failed_products=failed_products,
    )


def sync_product_states(
    state: Phase1State,
    products: list[ProductSummary],
) -> None:
    discovered_ids = {product.id for product in products}
    timestamp = now_iso()

    for product in products:
        product_state = state.products.get(product.id)
        if product_state is None:
            product_state = Phase1ProductState(
                product_id=product.id,
                product_name=product.name,
            )
            state.products[product.id] = product_state

        product_state.product_name = product.name
        product_state.list_discovered = True
        product_state.updated_at = timestamp

    for product_id, product_state in state.products.items():
        if product_id in discovered_ids:
            continue
        product_state.list_discovered = False


def run_phase1(
    *,
    limit: int = 10,
    images_per_product: int = 3,
    headless: bool | None = None,
    force_download: bool = False,
    settings: Settings | None = None,
) -> Phase1ExecutionResult:
    settings = settings or Settings()
    settings.ensure_directories()
    state = load_phase1_state(settings)
    state.started_at = now_iso()
    state.completed_at = None
    state.run_status = "running"

    session = require_authenticated_session("merchant", settings)
    run_headless = session.browser_mode == "headless" if headless is None else headless
    candidate_limit = max(limit * 3, limit + 10)

    skipped_count = 0
    with merchant_context(settings, headless=run_headless, auth_source=session.auth_source) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page = open_product_list_page(context, page, settings)
        list_page = ProductListPage(page, settings)

        candidate_products = list_page.get_products(limit=candidate_limit)
        if not candidate_products:
            raise RuntimeError("未从商品管理页提取到商品。")

        ensure_clean_image_dir(settings, candidate_products, force_download=force_download)
        sync_product_states(state, candidate_products)
        refresh_state_summary(state, candidate_products, target_total=limit, skipped_count=skipped_count)
        save_phase1_state(settings, state)

        for product in candidate_products:
            product_state = state.products[product.id]
            local_paths = [] if force_download else discover_local_image_paths(
                settings,
                product.id,
                limit=images_per_product,
            )

            if local_paths and product_state.fetch_status != "complete":
                mark_images_complete(
                    product_state,
                    image_paths=local_paths,
                    source="existing_files",
                    timestamp=now_iso(),
                )

            if not force_download and product_state.fetch_status == "complete":
                complete_paths = [path for path in product_state.artifacts.images.paths if Path(path).exists()]
                if complete_paths:
                    mark_images_complete(
                        product_state,
                        image_paths=complete_paths[:images_per_product],
                        source=product_state.artifacts.images.source or "existing_files",
                        timestamp=now_iso(),
                    )
                    skipped_count += 1
                    refresh_state_summary(
                        state,
                        candidate_products,
                        target_total=limit,
                        skipped_count=skipped_count,
                    )
                    save_phase1_state(settings, state)
                    current_today_pool = build_today_pool_from_state(
                        candidate_products,
                        state,
                        limit=images_per_product,
                        target_count=limit,
                    )
                    save_today_pool(
                        settings,
                        current_today_pool,
                    )
                    if len(current_today_pool.products) >= limit:
                        break
                    continue

            product_state.fetch_status = "in_progress"
            product_state.attempt_count += 1
            product_state.updated_at = now_iso()
            save_phase1_state(settings, state)

            try:
                bundle = list_page.get_product_images(
                    product,
                    limit=images_per_product,
                    force_download=force_download,
                )
                mark_images_complete(
                    product_state,
                    image_paths=[image.path for image in bundle.downloaded_images],
                    source=bundle.download_strategy or "downloaded",
                    timestamp=now_iso(),
                )
            except Exception as exc:
                mark_images_failed(product_state, reason=str(exc), timestamp=now_iso())

            refresh_state_summary(
                state,
                candidate_products,
                target_total=limit,
                skipped_count=skipped_count,
            )
            save_phase1_state(settings, state)
            current_today_pool = build_today_pool_from_state(
                candidate_products,
                state,
                limit=images_per_product,
                target_count=limit,
            )
            save_today_pool(
                settings,
                current_today_pool,
            )
            if len(current_today_pool.products) >= limit:
                break

    today_pool = build_today_pool_from_state(
        candidate_products,
        state,
        limit=images_per_product,
        target_count=limit,
    )
    refresh_state_summary(
        state,
        candidate_products,
        target_total=limit,
        skipped_count=skipped_count,
    )
    state.completed_at = now_iso()
    state.run_status = "complete" if len(today_pool.products) >= limit else "partial"
    save_phase1_state(settings, state)
    save_today_pool(settings, today_pool)

    return Phase1ExecutionResult(
        date=str(date.today()),
        run_status="complete" if state.run_status == "complete" else "partial",
        progress_ref=str(settings.phase1_state_path),
        today_pool_path=str(settings.today_pool_path),
        total_products=limit,
        success_count=len(today_pool.products),
        failed_count=state.failed_count,
        skipped_count=state.skipped_count,
        failed_products=today_pool.failed_products,
        today_pool=today_pool,
    )


def build_phase1_payload(
    *,
    limit: int = 10,
    images_per_product: int = 3,
    force_download: bool = False,
) -> tuple[dict, int]:
    try:
        result = run_phase1(
            limit=limit,
            images_per_product=images_per_product,
            force_download=force_download,
        )
        if result.success_count == 0:
            payload = SkillError(
                error="PHASE1_EMPTY",
                message="prepare-products 未成功准备任何商品，请检查 phase1-state.json 中的失败详情。",
                site="merchant",
                details={
                    "progress_ref": result.progress_ref,
                    "total_products": result.total_products,
                    "failed_count": result.failed_count,
                    "skipped_count": result.skipped_count,
                },
            )
            return payload.model_dump(mode="json"), 1

        payload = Phase1Success(
            status="ok" if result.run_status == "complete" else "partial",
            data=result,
        )
        return payload.model_dump(mode="json"), 0
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
            error="PHASE1_FAILED",
            message=str(exc),
            site="merchant",
        )
        return payload.model_dump(mode="json"), 1


def main() -> None:
    payload, exit_code = build_phase1_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
