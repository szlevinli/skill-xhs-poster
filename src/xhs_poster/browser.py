from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright

from .config import Settings
from .models import AuthSource, SiteName


class SessionExpiredError(RuntimeError):
    """登录态失效，需要人工重新登录。"""


def configure_playwright_browser_path(settings: Settings) -> None:
    """优先使用本机已安装的 Playwright 浏览器缓存。"""
    fallback_path = settings.playwright_browsers_path
    if fallback_path.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(fallback_path)


def site_profile_dir(settings: Settings, site: SiteName) -> Path:
    return settings.merchant_profile_dir if site == "merchant" else settings.consumer_profile_dir


def site_auth_state_path(settings: Settings, site: SiteName) -> Path:
    return settings.merchant_auth_state_path if site == "merchant" else settings.consumer_auth_state_path


def profile_has_state(settings: Settings, site: SiteName) -> bool:
    profile_dir = site_profile_dir(settings, site)
    try:
        next(profile_dir.iterdir())
    except (FileNotFoundError, StopIteration):
        return False
    return True


def available_auth_sources(settings: Settings, site: SiteName) -> list[AuthSource]:
    sources: list[AuthSource] = []
    if site_auth_state_path(settings, site).exists():
        sources.append("auth_state")
    if profile_has_state(settings, site):
        sources.append("profile")
    if not sources:
        sources.append("missing")
    return sources


def get_alive_page(context: BrowserContext, current_page: Page | None = None) -> Page:
    if current_page and not current_page.is_closed():
        return current_page

    alive_pages = [page for page in context.pages if not page.is_closed()]
    if not alive_pages:
        raise RuntimeError("浏览器页面已全部关闭。")
    return alive_pages[-1]


def is_authenticated_ark_page(page: Page) -> bool:
    url = page.url.lower()
    parsed = urlparse(url)
    path = parsed.path.lower()
    if "customer.xiaohongshu.com" in parsed.netloc or "/login" in path or "/website-login/" in path:
        return False
    if "ark.xiaohongshu.com" not in url:
        return False

    authenticated_prefixes = (
        "/app-system/",
        "/app-item/",
        "/notes/",
        "/note/",
        "/data/",
        "/trade/",
        "/store/",
    )
    if path.startswith(authenticated_prefixes):
        return True

    try:
        body_text = page.locator("body").inner_text(timeout=2_000)
    except Error:
        return False
    markers = ("商品管理", "商品", "订单", "数据中心", "店铺", "笔记")
    return any(marker in body_text for marker in markers)


def is_ready_list_page(page: Page) -> bool:
    url = page.url.lower()
    if "app-item/list" not in url:
        return False

    try:
        if page.locator("table tbody tr").count() > 0:
            return True
    except Error:
        return False

    try:
        body_text = page.locator("body").inner_text(timeout=2_000)
    except Error:
        return False

    markers = ["商品管理", "商品ID", "去发布", "编辑", "暂无商品"]
    return any(marker in body_text for marker in markers)


def wait_for_authenticated_page(
    context: BrowserContext,
    page: Page,
    timeout_ms: int = 15_000,
) -> Page:
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        page = get_alive_page(context, page)
        if is_authenticated_ark_page(page):
            return page
        try:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            page.wait_for_function(
                """
                () => {
                    const url = window.location.href.toLowerCase();
                    return !url.includes('customer.xiaohongshu.com') && !url.includes('login')
                      && url.includes('ark.xiaohongshu.com');
                }
                """,
                timeout=min(2_000, remaining_ms),
            )
        except Error:
            continue

    raise SessionExpiredError("商家端登录已过期，请先重新导入 auth-state 或重新登录 merchant profile。")


def open_product_list_page(
    context: BrowserContext,
    page: Page,
    settings: Settings,
    timeout_ms: int = 15_000,
) -> Page:
    page = get_alive_page(context, page)
    page.goto(settings.merchant_home_url, wait_until="domcontentloaded", timeout=30_000)
    page = wait_for_authenticated_page(context, page, timeout_ms=timeout_ms)

    if is_ready_list_page(page):
        return page

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page = get_alive_page(context, page)
        try:
            nav_item = page.locator("span").filter(has_text="商品管理").first
            nav_item.wait_for(state="visible", timeout=2_000)
            nav_item.click()
        except Error:
            try:
                page.evaluate(
                    """
                    () => {
                        const el = Array.from(document.querySelectorAll('span, div, a'))
                          .find((node) => node.textContent?.trim() === '商品管理');
                        if (el) el.click();
                    }
                    """
                )
            except Error:
                pass

        try:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            page.wait_for_url("**/app-item/list/**", timeout=min(3_000, remaining_ms))
        except Error:
            continue

        if is_ready_list_page(page):
            return page

    raise RuntimeError("未能从首页进入商品管理列表页。")


def launch_site_persistent_context(
    playwright: Playwright,
    settings: Settings,
    site: SiteName,
    *,
    headless: bool = False,
) -> BrowserContext:
    settings.ensure_directories()
    configure_playwright_browser_path(settings)
    profile_dir = site_profile_dir(settings, site)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        accept_downloads=True,
    )


def launch_site_runtime_context(
    playwright: Playwright,
    settings: Settings,
    site: SiteName,
    *,
    headless: bool = False,
    auth_source: AuthSource | None = None,
) -> tuple[BrowserContext, AuthSource]:
    settings.ensure_directories()
    configure_playwright_browser_path(settings)

    resolved_source = auth_source
    if resolved_source is None:
        resolved_source = available_auth_sources(settings, site)[0]

    if resolved_source == "auth_state":
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=str(site_auth_state_path(settings, site)),
            accept_downloads=True,
        )
        return context, resolved_source

    context = launch_site_persistent_context(playwright, settings, site, headless=headless)
    return context, resolved_source


def launch_merchant_context(
    playwright: Playwright,
    settings: Settings,
    *,
    headless: bool = False,
    auth_source: AuthSource | None = None,
) -> tuple[BrowserContext, AuthSource]:
    return launch_site_runtime_context(
        playwright,
        settings,
        "merchant",
        headless=headless,
        auth_source=auth_source,
    )


def launch_consumer_context(
    playwright: Playwright,
    settings: Settings,
    *,
    headless: bool = False,
    auth_source: AuthSource | None = None,
) -> tuple[BrowserContext, AuthSource]:
    return launch_site_runtime_context(
        playwright,
        settings,
        "consumer",
        headless=headless,
        auth_source=auth_source,
    )


def close_context_safely(context: BrowserContext) -> None:
    errors: list[str] = []

    for page in list(context.pages):
        if page.is_closed():
            continue
        try:
            page.close(run_before_unload=False)
        except Error as exc:
            errors.append(f"page.close: {exc}")

    browser = context.browser
    if browser is not None:
        try:
            browser.close()
        except Error as exc:
            errors.append(f"browser.close: {exc}")
    else:
        try:
            context.close()
        except Error as exc:
            errors.append(f"context.close: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors))


@contextmanager
def merchant_context(
    settings: Settings,
    *,
    headless: bool = False,
    auth_source: AuthSource | None = None,
):
    configure_playwright_browser_path(settings)
    with sync_playwright() as playwright:
        context, _ = launch_merchant_context(
            playwright,
            settings,
            headless=headless,
            auth_source=auth_source,
        )
        try:
            yield context
        finally:
            close_context_safely(context)


@contextmanager
def consumer_context(
    settings: Settings,
    *,
    headless: bool = False,
    auth_source: AuthSource | None = None,
):
    configure_playwright_browser_path(settings)
    with sync_playwright() as playwright:
        context, _ = launch_consumer_context(
            playwright,
            settings,
            headless=headless,
            auth_source=auth_source,
        )
        try:
            yield context
        finally:
            close_context_safely(context)
