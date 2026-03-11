from __future__ import annotations

import json
from datetime import date

from .auth import LoginRequiredError, require_authenticated_session
from .browser import get_alive_page, merchant_context, open_product_list_page
from .config import Settings
from .merchant import ProductListPage
from .models import Phase1Success, SkillError, TodayPool


def build_today_pool(products, product_images) -> TodayPool:
    images = {
        bundle.product_id: [image.path for image in bundle.downloaded_images]
        for bundle in product_images
    }
    return TodayPool(
        date=str(date.today()),
        products=products,
        images=images,
    )


def save_today_pool(settings: Settings, today_pool: TodayPool) -> None:
    settings.ensure_directories()
    temp_path = settings.today_pool_path.with_suffix(".json.tmp")
    temp_path.write_text(today_pool.model_dump_json(indent=2), encoding="utf-8")
    temp_path.replace(settings.today_pool_path)


def ensure_clean_image_dir(
    settings: Settings,
    products,
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


def run_phase1(
    *,
    limit: int = 10,
    images_per_product: int = 3,
    headless: bool | None = None,
    force_download: bool = False,
    settings: Settings | None = None,
) -> TodayPool:
    settings = settings or Settings()
    settings.ensure_directories()
    session = require_authenticated_session("merchant", settings)
    run_headless = session.browser_mode == "headless" if headless is None else headless

    with merchant_context(settings, headless=run_headless, auth_source=session.auth_source) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page = open_product_list_page(context, page, settings)
        list_page = ProductListPage(page, settings)

        products = list_page.get_products(limit=limit)
        if not products:
            raise RuntimeError("未从商品管理页提取到商品。")

        ensure_clean_image_dir(settings, products, force_download=force_download)
        product_images = [
            list_page.get_product_images(
                product,
                limit=images_per_product,
                force_download=force_download,
            )
            for product in products
        ]

    today_pool = build_today_pool(products, product_images)
    save_today_pool(settings, today_pool)
    return today_pool


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
        payload = Phase1Success(data=result)
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
