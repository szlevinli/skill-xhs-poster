from __future__ import annotations

import os
import time
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright

from .config import Settings


class SessionExpiredError(RuntimeError):
    """登录态失效，需要人工重新登录。"""


def configure_playwright_browser_path(settings: Settings) -> None:
    """优先使用本机已安装的 Playwright 浏览器缓存。"""
    fallback_path = settings.playwright_browsers_path
    if fallback_path.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(fallback_path)


def get_alive_page(context: BrowserContext, current_page: Page | None = None) -> Page:
    if current_page and not current_page.is_closed():
        return current_page

    alive_pages = [page for page in context.pages if not page.is_closed()]
    if not alive_pages:
        raise RuntimeError("浏览器页面已全部关闭。")
    return alive_pages[-1]


def is_authenticated_ark_page(page: Page) -> bool:
    url = page.url.lower()
    if "customer.xiaohongshu.com" in url or "login" in url:
        return False
    return "ark.xiaohongshu.com" in url


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

    raise SessionExpiredError("商家端登录已过期，请先重新登录 merchant profile。")


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


def launch_merchant_context(
    playwright: Playwright,
    settings: Settings,
    *,
    headless: bool = False,
) -> BrowserContext:
    settings.ensure_directories()
    configure_playwright_browser_path(settings)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(settings.merchant_profile_dir),
        headless=headless,
        accept_downloads=True,
    )


@contextmanager
def merchant_context(
    settings: Settings,
    *,
    headless: bool = False,
):
    configure_playwright_browser_path(settings)
    with sync_playwright() as playwright:
        context = launch_merchant_context(playwright, settings, headless=headless)
        try:
            yield context
        finally:
            context.close()
