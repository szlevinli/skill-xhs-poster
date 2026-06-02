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
    launch_merchant_persistent_context,
    launch_merchant_runtime_context,
    merchant_auth_state_path,
    merchant_profile_dir,
)
from .config import Settings
from .models import AuthSource, BrowserMode, SessionInfo


class LoginRequiredError(RuntimeError):
    def __init__(self, session: SessionInfo):
        super().__init__(session.message)
        self.session = session


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


def _is_authenticated_merchant_page(page: Page) -> bool:
    url = page.url.lower()
    parsed = urlparse(url)
    path = parsed.path.lower()
    if (
        "customer.xiaohongshu.com" in parsed.netloc
        or "/login" in path
        or "/website-login/error" in path
    ):
        return False
    return is_authenticated_ark_page(page)


def _build_session_info(
    *,
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
                message = "商家端登录有效，已通过 auth-state 验证，可直接在云服务器无头运行。"
            else:
                message = "商家端登录有效，当前通过本地 profile 验证。"
        elif auth_source == "auth_state":
            message = (
                "商家端 auth-state 无效或已过期，且 profile 兜底未通过；"
                "请回到 macOS 重新登录并重新导出。"
            )
        elif auth_source == "profile":
            message = "商家端需要登录，请启动有头浏览器完成登录。"
        else:
            message = (
                "商家端未找到可用 auth-state，且本地 profile 未登录；"
                "请先执行 login 或导入 auth-state。"
            )

    return SessionInfo(
        site="merchant",
        status="authenticated" if authenticated else "login_required",
        authenticated=authenticated,
        auth_source=auth_source,
        attempted_auth_sources=attempted_auth_sources or [auth_source],
        browser_mode=resolved_browser_mode,
        checked_url=checked_url,
        profile_dir=str(merchant_profile_dir(settings)),
        auth_state_path=auth_state_path or str(merchant_auth_state_path(settings)),
        home_url=settings.merchant_home_url,
        message=message,
    )


def _launch_merchant_context_headful(
    settings: Settings,
) -> tuple[BrowserContext, Playwright]:
    configure_playwright_browser_path(settings)
    playwright = sync_playwright().start()
    context = launch_merchant_persistent_context(playwright, settings, headless=False)
    return context, playwright


def _launch_runtime_context_for_source(
    settings: Settings,
    *,
    headless: bool,
    auth_source: AuthSource,
) -> tuple[BrowserContext, Playwright, AuthSource]:
    configure_playwright_browser_path(settings)
    playwright = sync_playwright().start()
    context, resolved_source = launch_merchant_runtime_context(
        playwright,
        settings,
        headless=headless,
        auth_source=auth_source,
    )
    return context, playwright, resolved_source


def _probe_context(
    context: BrowserContext,
    settings: Settings,
    *,
    timeout_ms: int,
) -> tuple[bool, str]:
    page = context.pages[0] if context.pages else context.new_page()
    page = get_alive_page(context, page)
    page.goto(settings.merchant_home_url, wait_until="domcontentloaded", timeout=30_000)

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page = get_alive_page(context, page)
        if _is_authenticated_merchant_page(page):
            verified, verified_url = _verify_merchant_session(page, settings)
            if verified:
                return True, verified_url

        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
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
    stage: str,
) -> str:
    settings.ensure_directories()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.auth_artifacts_dir / f"merchant-{stage}-{timestamp}"
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
        "site": "merchant",
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
    settings: Settings | None = None,
    *,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()

    attempted_sources = available_auth_sources(settings)
    last_checked_url = settings.merchant_home_url

    for auth_source in attempted_sources:
        context, playwright, resolved_source = _launch_runtime_context_for_source(
            settings,
            headless=True,
            auth_source=auth_source,
        )
        try:
            authenticated, checked_url = _probe_context(
                context,
                settings,
                timeout_ms=timeout_ms,
            )
            last_checked_url = checked_url
            if authenticated:
                return _build_session_info(
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
            "商家端 auth-state 校验失败，profile 兜底也未通过；"
            "请回到 macOS 重新登录并重新导出。"
        )
    return _build_session_info(
        authenticated=False,
        checked_url=last_checked_url,
        settings=settings,
        auth_source=preferred_source,
        attempted_auth_sources=attempted_sources,
        browser_mode="headful",
        message=message,
    )


def login_site(
    settings: Settings | None = None,
    *,
    timeout_ms: int = 0,
    debug_auth: bool = False,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()

    context, playwright = _launch_merchant_context_headful(settings)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(settings.merchant_home_url, wait_until="domcontentloaded", timeout=30_000)

        if timeout_ms <= 0:
            while True:
                page = get_alive_page(context, page)
                if _is_authenticated_merchant_page(page):
                    checked_url = page.url
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
                            stage="login-success",
                        )
                        message = f"商家端登录有效，诊断产物已写入：{artifact_dir}"
                    return _build_session_info(
                        authenticated=True,
                        checked_url=checked_url,
                        settings=settings,
                        auth_source="profile",
                        attempted_auth_sources=["profile"],
                        browser_mode="headful",
                        message=message,
                    )
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
                if _is_authenticated_merchant_page(page):
                    checked_url = page.url
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
                            stage="login-success",
                        )
                        message = f"商家端登录有效，诊断产物已写入：{artifact_dir}"
                    return _build_session_info(
                        authenticated=True,
                        checked_url=checked_url,
                        settings=settings,
                        auth_source="profile",
                        attempted_auth_sources=["profile"],
                        browser_mode="headful",
                        message=message,
                    )
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
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
                    stage="login-timeout",
                )
                message = f"商家端登录超时，诊断产物已写入：{artifact_dir}"
            raise LoginRequiredError(
                _build_session_info(
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
    settings: Settings | None = None,
    *,
    output_path: str | Path | None = None,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()
    resolved_output = Path(output_path).expanduser() if output_path else merchant_auth_state_path(settings)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    context, playwright = _launch_merchant_context_headful(settings)
    try:
        authenticated, checked_url = _probe_context(
            context,
            settings,
            timeout_ms=timeout_ms,
        )
        if not authenticated:
            raise LoginRequiredError(
                _build_session_info(
                    authenticated=False,
                    checked_url=checked_url,
                    settings=settings,
                    auth_source="profile",
                    attempted_auth_sources=["profile"],
                    browser_mode="headful",
                    message="商家端 profile 未登录，无法导出 auth-state，请先执行 login。",
                )
            )
        context.storage_state(path=str(resolved_output))
    finally:
        context.close()
        playwright.stop()

    return _build_session_info(
        authenticated=True,
        checked_url=checked_url,
        settings=settings,
        auth_source="auth_state",
        attempted_auth_sources=["profile", "auth_state"],
        browser_mode="headless",
        message=f"商家端 auth-state 已导出：{resolved_output}",
        auth_state_path=str(resolved_output),
    )


def import_site_auth_state(
    settings: Settings | None = None,
    *,
    input_path: str | Path | None = None,
    timeout_ms: int = 8_000,
) -> SessionInfo:
    settings = settings or Settings()
    settings.ensure_directories()
    source_path = Path(input_path).expanduser() if input_path else merchant_auth_state_path(settings)
    payload = _load_auth_state_payload(source_path)

    destination_path = merchant_auth_state_path(settings)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != destination_path.resolve():
        destination_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    session = probe_site_session(settings, timeout_ms=timeout_ms)
    if not session.authenticated or session.auth_source != "auth_state":
        raise LoginRequiredError(
            _build_session_info(
                authenticated=False,
                checked_url=session.checked_url,
                settings=settings,
                auth_source="auth_state",
                attempted_auth_sources=session.attempted_auth_sources,
                browser_mode="headful",
                message=(
                    f"商家端 auth-state 已导入到 {destination_path}，但无头校验未通过；"
                    "请回到 macOS 重新登录并重新导出。"
                ),
            )
        )

    session.message = f"商家端 auth-state 已导入并验证通过：{destination_path}"
    session.auth_state_path = str(destination_path)
    return session


def require_authenticated_session(
    settings: Settings | None = None,
) -> SessionInfo:
    session = probe_site_session(settings)
    if not session.authenticated:
        raise LoginRequiredError(session)
    return session
