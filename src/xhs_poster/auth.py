from __future__ import annotations

import time

from playwright.sync_api import BrowserContext, Error, Page, sync_playwright

from .browser import configure_playwright_browser_path, get_alive_page
from .config import Settings
from .models import SessionInfo, SiteName


class LoginRequiredError(RuntimeError):
    def __init__(self, session: SessionInfo):
        super().__init__(session.message)
        self.session = session


def _site_home_url(settings: Settings, site: SiteName) -> str:
    if site == "merchant":
        return settings.merchant_home_url
    return settings.consumer_home_url


def _site_profile_dir(settings: Settings, site: SiteName) -> str:
    if site == "merchant":
        return str(settings.merchant_profile_dir)
    return str(settings.consumer_profile_dir)


def _has_consumer_auth_cookies(context: BrowserContext) -> bool:
    cookie_names = {cookie.get("name", "") for cookie in context.cookies()}
    return "web_session" in cookie_names or "id_token" in cookie_names


def _consumer_has_logged_in_markers(page: Page) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=2_000)
    except Error:
        return False
    markers = ("创作中心", "发布", "通知")
    return all(marker in body_text for marker in markers)


def _is_authenticated_page(page: Page, site: SiteName, context: BrowserContext) -> bool:
    url = page.url.lower()
    if "customer.xiaohongshu.com" in url or "login" in url or "/website-login/error" in url:
        return False
    if site == "merchant":
        return "ark.xiaohongshu.com" in url
    if "www.xiaohongshu.com" not in url:
        return False
    return _has_consumer_auth_cookies(context) and _consumer_has_logged_in_markers(page)


def _build_session_info(
    *,
    site: SiteName,
    authenticated: bool,
    checked_url: str,
    settings: Settings,
) -> SessionInfo:
    return SessionInfo(
        site=site,
        status="authenticated" if authenticated else "login_required",
        authenticated=authenticated,
        browser_mode="headless" if authenticated else "headful",
        checked_url=checked_url,
        profile_dir=_site_profile_dir(settings, site),
        home_url=_site_home_url(settings, site),
        message=(
            f"{site} 站点登录有效，可直接使用无头浏览器运行。"
            if authenticated
            else f"{site} 站点需要登录，请启动有头浏览器完成登录。"
        ),
    )


def _launch_site_context(
    settings: Settings,
    site: SiteName,
    *,
    headless: bool,
) -> tuple[BrowserContext, object]:
    configure_playwright_browser_path(settings)
    playwright = sync_playwright().start()
    profile_dir = _site_profile_dir(settings, site)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=headless,
        accept_downloads=True,
    )
    return context, playwright


def probe_site_session(
    site: SiteName,
    settings: Settings | None = None,
    *,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()

    context, playwright = _launch_site_context(settings, site, headless=True)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        page.goto(_site_home_url(settings, site), wait_until="domcontentloaded", timeout=30_000)

        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            page = get_alive_page(context, page)
            if _is_authenticated_page(page, site, context):
                return _build_session_info(
                    site=site,
                    authenticated=True,
                    checked_url=page.url,
                    settings=settings,
                )

            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if site == "consumer":
                page.wait_for_timeout(min(500, remaining_ms))
                continue
            try:
                page.wait_for_function(
                    """
                    () => {
                        const url = window.location.href.toLowerCase();
                        return !url.includes('customer.xiaohongshu.com')
                          && !url.includes('login')
                          && url.includes('ark.xiaohongshu.com');
                    }
                    """,
                    timeout=min(2_000, remaining_ms),
                )
            except Error:
                continue

        return _build_session_info(
            site=site,
            authenticated=False,
            checked_url=page.url,
            settings=settings,
        )
    finally:
        context.close()
        playwright.stop()


def login_site(
    site: SiteName,
    settings: Settings | None = None,
    *,
    timeout_ms: int = 0,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()

    context, playwright = _launch_site_context(settings, site, headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(_site_home_url(settings, site), wait_until="domcontentloaded", timeout=30_000)

        if timeout_ms <= 0:
            while True:
                page = get_alive_page(context, page)
                if _is_authenticated_page(page, site, context):
                    return _build_session_info(
                        site=site,
                        authenticated=True,
                        checked_url=page.url,
                        settings=settings,
                    )
                if site == "consumer":
                    page.wait_for_timeout(500)
                    continue
                try:
                    page.wait_for_function(
                        """
                        () => {
                            const url = window.location.href.toLowerCase();
                            return !url.includes('customer.xiaohongshu.com')
                              && !url.includes('login')
                              && url.includes('ark.xiaohongshu.com');
                        }
                        """,
                        timeout=0,
                    )
                except Error:
                    continue
        else:
            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                page = get_alive_page(context, page)
                if _is_authenticated_page(page, site, context):
                    return _build_session_info(
                        site=site,
                        authenticated=True,
                        checked_url=page.url,
                        settings=settings,
                    )
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                if site == "consumer":
                    page.wait_for_timeout(min(500, remaining_ms))
                    continue
                try:
                    page.wait_for_function(
                        """
                        () => {
                            const url = window.location.href.toLowerCase();
                            return !url.includes('customer.xiaohongshu.com')
                              && !url.includes('login')
                              && url.includes('ark.xiaohongshu.com');
                        }
                        """,
                        timeout=min(2_000, remaining_ms),
                    )
                except Error:
                    continue

            raise LoginRequiredError(
                _build_session_info(
                    site=site,
                    authenticated=False,
                    checked_url=page.url,
                    settings=settings,
                )
            )
    finally:
        context.close()
        playwright.stop()


def require_authenticated_session(
    site: SiteName,
    settings: Settings | None = None,
) -> SessionInfo:
    session = probe_site_session(site, settings)
    if not session.authenticated:
        raise LoginRequiredError(session)
    return session
