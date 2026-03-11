from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Error, Page, Playwright, sync_playwright

from .browser import (
    available_auth_sources,
    configure_playwright_browser_path,
    get_alive_page,
    is_authenticated_ark_page,
    is_ready_list_page,
    launch_site_persistent_context,
    launch_site_runtime_context,
    site_auth_state_path,
    site_profile_dir,
)
from .config import Settings
from .models import AuthSource, BrowserMode, SessionInfo, SiteName


class LoginRequiredError(RuntimeError):
    def __init__(self, session: SessionInfo):
        super().__init__(session.message)
        self.session = session


def _site_home_url(settings: Settings, site: SiteName) -> str:
    if site == "merchant":
        return settings.merchant_home_url
    return settings.consumer_home_url


def _site_profile_dir(settings: Settings, site: SiteName) -> str:
    return str(site_profile_dir(settings, site))


def _site_auth_state_path(settings: Settings, site: SiteName) -> str:
    return str(site_auth_state_path(settings, site))


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
    parsed = urlparse(url)
    path = parsed.path.lower()
    if (
        "customer.xiaohongshu.com" in parsed.netloc
        or "/login" in path
        or "/website-login/error" in path
    ):
        return False
    if site == "merchant":
        return is_authenticated_ark_page(page)
    if "www.xiaohongshu.com" not in url:
        return False
    return _has_consumer_auth_cookies(context) and _consumer_has_logged_in_markers(page)


def _verify_merchant_session(page: Page, settings: Settings) -> tuple[bool, str]:
    try:
        page.goto(settings.merchant_list_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1_500)
    except Error:
        return False, page.url

    if not is_authenticated_ark_page(page):
        return False, page.url
    url = page.url.lower()
    if "ark.xiaohongshu.com/app-item/list" in url:
        return True, page.url
    if is_ready_list_page(page):
        return True, page.url
    return False, page.url


def _build_session_info(
    *,
    site: SiteName,
    authenticated: bool,
    checked_url: str,
    settings: Settings,
    auth_source: AuthSource,
    attempted_auth_sources: list[AuthSource] | None = None,
    browser_mode: BrowserMode | None = None,
    auth_state_path: str | None = None,
    message: str | None = None,
) -> SessionInfo:
    resolved_browser_mode = browser_mode or ("headless" if authenticated else "headful")
    if message is None:
        if authenticated:
            if auth_source == "auth_state":
                message = f"{site} 站点登录有效，已通过 auth-state 验证，可直接在云服务器无头运行。"
            else:
                message = f"{site} 站点登录有效，当前通过本地 profile 验证。"
        elif auth_source == "auth_state":
            message = (
                f"{site} 站点 auth-state 无效或已过期，且 profile 兜底未通过；"
                "请回到 macOS 重新登录并重新导出。"
            )
        elif auth_source == "profile":
            message = f"{site} 站点需要登录，请启动有头浏览器完成登录。"
        else:
            message = (
                f"{site} 站点未找到可用 auth-state，且本地 profile 未登录；"
                "请先执行 login 或导入 auth-state。"
            )

    return SessionInfo(
        site=site,
        status="authenticated" if authenticated else "login_required",
        authenticated=authenticated,
        auth_source=auth_source,
        attempted_auth_sources=attempted_auth_sources or [auth_source],
        browser_mode=resolved_browser_mode,
        checked_url=checked_url,
        profile_dir=_site_profile_dir(settings, site),
        auth_state_path=auth_state_path or _site_auth_state_path(settings, site),
        home_url=_site_home_url(settings, site),
        message=message,
    )


def _launch_site_context(
    settings: Settings,
    site: SiteName,
    *,
    headless: bool,
) -> tuple[BrowserContext, Playwright]:
    configure_playwright_browser_path(settings)
    playwright = sync_playwright().start()
    context = launch_site_persistent_context(playwright, settings, site, headless=headless)
    return context, playwright


def _launch_runtime_context_for_source(
    settings: Settings,
    site: SiteName,
    *,
    headless: bool,
    auth_source: AuthSource,
) -> tuple[BrowserContext, Playwright, AuthSource]:
    configure_playwright_browser_path(settings)
    playwright = sync_playwright().start()
    context, resolved_source = launch_site_runtime_context(
        playwright,
        settings,
        site,
        headless=headless,
        auth_source=auth_source,
    )
    return context, playwright, resolved_source


def _probe_context(
    context: BrowserContext,
    settings: Settings,
    site: SiteName,
    *,
    timeout_ms: int,
) -> tuple[bool, str]:
    page = context.pages[0] if context.pages else context.new_page()
    page = get_alive_page(context, page)
    page.goto(_site_home_url(settings, site), wait_until="domcontentloaded", timeout=30_000)

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page = get_alive_page(context, page)
        if _is_authenticated_page(page, site, context):
            if site == "merchant":
                verified, verified_url = _verify_merchant_session(page, settings)
                if verified:
                    return True, verified_url
            else:
                return True, page.url

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

    return False, page.url


def _load_auth_state_payload(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"未找到 auth-state 文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"auth-state 文件不是合法 JSON：{path}，{exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"auth-state 文件结构异常：{path}")
    cookies = payload.get("cookies")
    origins = payload.get("origins")
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise RuntimeError(f"auth-state 文件缺少 cookies/origins：{path}")
    return payload


def _capture_auth_debug_artifacts(
    *,
    page: Page,
    context: BrowserContext,
    settings: Settings,
    site: SiteName,
    stage: str,
) -> str:
    settings.ensure_directories()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.auth_artifacts_dir / f"{site}-{stage}-{timestamp}"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = artifact_dir / "page.png"
    html_path = artifact_dir / "page.html"
    summary_path = artifact_dir / "summary.json"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Error:
        pass

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Error:
        pass

    try:
        storage = page.evaluate(
            """
            () => {
                const entries = [];
                for (let i = 0; i < window.localStorage.length; i += 1) {
                    const key = window.localStorage.key(i);
                    if (!key) continue;
                    entries.push({
                        key,
                        value_preview: String(window.localStorage.getItem(key) || '').slice(0, 200),
                    });
                }
                return entries;
            }
            """
        )
    except Error:
        storage = []

    cookie_summaries = [
        {
            "name": cookie.get("name"),
            "domain": cookie.get("domain"),
            "path": cookie.get("path"),
            "expires": cookie.get("expires"),
            "httpOnly": cookie.get("httpOnly"),
            "secure": cookie.get("secure"),
        }
        for cookie in context.cookies()
    ]
    summary = {
        "site": site,
        "stage": stage,
        "url": page.url,
        "title": page.title(),
        "cookie_count": len(cookie_summaries),
        "cookies": cookie_summaries,
        "local_storage": storage,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(artifact_dir)


def probe_site_session(
    site: SiteName,
    settings: Settings | None = None,
    *,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()

    attempted_sources = available_auth_sources(settings, site)
    last_checked_url = _site_home_url(settings, site)

    for auth_source in attempted_sources:
        context, playwright, resolved_source = _launch_runtime_context_for_source(
            settings,
            site,
            headless=True,
            auth_source=auth_source,
        )
        try:
            authenticated, checked_url = _probe_context(
                context,
                settings,
                site,
                timeout_ms=timeout_ms,
            )
            last_checked_url = checked_url
            if authenticated:
                return _build_session_info(
                    site=site,
                    authenticated=True,
                    checked_url=checked_url,
                    settings=settings,
                    auth_source=resolved_source,
                    attempted_auth_sources=attempted_sources,
                    browser_mode="headless",
                )
        finally:
            context.close()
            playwright.stop()

    preferred_source = "auth_state" if "auth_state" in attempted_sources else attempted_sources[-1]
    message = None
    if attempted_sources == ["auth_state", "profile"]:
        message = (
            f"{site} 站点 auth-state 校验失败，profile 兜底也未通过；"
            "请回到 macOS 重新登录并重新导出。"
        )
    return _build_session_info(
        site=site,
        authenticated=False,
        checked_url=last_checked_url,
        settings=settings,
        auth_source=preferred_source,
        attempted_auth_sources=attempted_sources,
        browser_mode="headful",
        message=message,
    )


def login_site(
    site: SiteName,
    settings: Settings | None = None,
    *,
    timeout_ms: int = 0,
    debug_auth: bool = False,
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
                    checked_url = page.url
                    if site == "merchant":
                        verified, checked_url = _verify_merchant_session(page, settings)
                        if not verified:
                            page.wait_for_timeout(500)
                            continue
                    message = None
                    if debug_auth:
                        artifact_dir = _capture_auth_debug_artifacts(
                            page=page,
                            context=context,
                            settings=settings,
                            site=site,
                            stage="login-success",
                        )
                        message = f"{site} 站点登录有效，诊断产物已写入：{artifact_dir}"
                    return _build_session_info(
                        site=site,
                        authenticated=True,
                        checked_url=checked_url,
                        settings=settings,
                        auth_source="profile",
                        attempted_auth_sources=["profile"],
                        browser_mode="headful",
                        message=message,
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
                    checked_url = page.url
                    if site == "merchant":
                        verified, checked_url = _verify_merchant_session(page, settings)
                        if not verified:
                            page.wait_for_timeout(500)
                            continue
                    message = None
                    if debug_auth:
                        artifact_dir = _capture_auth_debug_artifacts(
                            page=page,
                            context=context,
                            settings=settings,
                            site=site,
                            stage="login-success",
                        )
                        message = f"{site} 站点登录有效，诊断产物已写入：{artifact_dir}"
                    return _build_session_info(
                        site=site,
                        authenticated=True,
                        checked_url=checked_url,
                        settings=settings,
                        auth_source="profile",
                        attempted_auth_sources=["profile"],
                        browser_mode="headful",
                        message=message,
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

            message = None
            if debug_auth:
                artifact_dir = _capture_auth_debug_artifacts(
                    page=page,
                    context=context,
                    settings=settings,
                    site=site,
                    stage="login-timeout",
                )
                message = f"{site} 站点登录超时，诊断产物已写入：{artifact_dir}"
            raise LoginRequiredError(
                _build_session_info(
                    site=site,
                    authenticated=False,
                    checked_url=page.url,
                    settings=settings,
                    auth_source="profile",
                    attempted_auth_sources=["profile"],
                    browser_mode="headful",
                    message=message,
                )
            )
    finally:
        context.close()
        playwright.stop()


def export_site_auth_state(
    site: SiteName,
    settings: Settings | None = None,
    *,
    output_path: str | Path | None = None,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()
    resolved_output = Path(output_path).expanduser() if output_path else site_auth_state_path(settings, site)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    context, playwright = _launch_site_context(settings, site, headless=False)
    try:
        authenticated, checked_url = _probe_context(
            context,
            settings,
            site,
            timeout_ms=timeout_ms,
        )
        if not authenticated:
            raise LoginRequiredError(
                _build_session_info(
                    site=site,
                    authenticated=False,
                    checked_url=checked_url,
                    settings=settings,
                    auth_source="profile",
                    attempted_auth_sources=["profile"],
                    browser_mode="headful",
                    message=f"{site} 站点 profile 未登录，无法导出 auth-state，请先执行 login。",
                )
            )
        context.storage_state(path=str(resolved_output))
    finally:
        context.close()
        playwright.stop()

    return _build_session_info(
        site=site,
        authenticated=True,
        checked_url=checked_url,
        settings=settings,
        auth_source="auth_state",
        attempted_auth_sources=["profile", "auth_state"],
        browser_mode="headless",
        message=f"{site} 站点 auth-state 已导出：{resolved_output}",
        auth_state_path=str(resolved_output),
    )


def import_site_auth_state(
    site: SiteName,
    settings: Settings | None = None,
    *,
    input_path: str | Path | None = None,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()
    source_path = Path(input_path).expanduser() if input_path else site_auth_state_path(settings, site)
    payload = _load_auth_state_payload(source_path)

    destination_path = site_auth_state_path(settings, site)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != destination_path.resolve():
        destination_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    session = probe_site_session(site, settings, timeout_ms=timeout_ms)
    if not session.authenticated or session.auth_source != "auth_state":
        raise LoginRequiredError(
            _build_session_info(
                site=site,
                authenticated=False,
                checked_url=session.checked_url,
                settings=settings,
                auth_source="auth_state",
                attempted_auth_sources=session.attempted_auth_sources,
                browser_mode="headful",
                message=(
                    f"{site} 站点 auth-state 已导入到 {destination_path}，但无头校验未通过；"
                    "请回到 macOS 重新登录并重新导出。"
                ),
            )
        )

    session.message = f"{site} 站点 auth-state 已导入并验证通过：{destination_path}"
    session.auth_state_path = str(destination_path)
    return session


def require_authenticated_session(
    site: SiteName,
    settings: Settings | None = None,
) -> SessionInfo:
    session = probe_site_session(site, settings)
    if not session.authenticated:
        raise LoginRequiredError(session)
    return session
