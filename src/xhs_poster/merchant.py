from __future__ import annotations

import hashlib
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image
from playwright.sync_api import Error, Page

from .config import Settings
from .models import DownloadedImage, ProductImages, ProductSummary
from .image_pipeline import (
    ImageCandidate,
    build_image_id,
    dedupe_candidates,
    dedupe_downloaded_images,
    normalize_image_url,
)


def locator_is_visible(locator) -> bool:
    try:
        return locator.is_visible()
    except Error:
        return False


def _dismiss_blocking_modal(page: Page) -> bool:
    for locator in (
        page.locator(".d-modal-close").first,
        page.locator(".ant-modal-close").first,
        page.locator("[aria-label='Close']").first,
        page.get_by_text("关闭", exact=True).first,
        page.get_by_text("我知道了", exact=True).first,
        page.get_by_text("知道了", exact=True).first,
        page.get_by_text("取消", exact=True).first,
    ):
        if locator_is_visible(locator):
            try:
                locator.click(timeout=2_000)
                page.wait_for_timeout(500)
                return True
            except Error:
                continue

    dismissed = page.evaluate(
        """
        () => {
            const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const isVisible = (node) => {
                if (!node) return false;
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const buttons = Array.from(document.querySelectorAll('button, span, div, a'))
                .filter((node) => isVisible(node))
                .filter((node) => ['关闭', '我知道了', '知道了', '取消'].includes(normalize(node.textContent)));
            const target = buttons[0];
            if (!target) return false;
            target.click();
            return true;
        }
        """
    )
    if dismissed:
        page.wait_for_timeout(500)
    return bool(dismissed)


def _wait_for_modal_mask_to_clear(page: Page, timeout_ms: int = 5_000) -> bool:
    try:
        page.wait_for_function(
            """
            () => {
                const isVisible = (node) => {
                    if (!node) return false;
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                return !Array.from(document.querySelectorAll('.d-modal-mask, .ant-modal-mask, .semi-modal-mask'))
                    .some((node) => isVisible(node));
            }
            """,
            timeout=timeout_ms,
        )
        return True
    except Error:
        return False


class ProductDetailPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def open(self, product_id: str) -> None:
        self.page.goto(
            self.settings.merchant_edit_url(product_id),
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        self.page.wait_for_timeout(1_500)

    def open_graphic_info_tab(self) -> None:
        tab = self.page.get_by_text("图文信息", exact=True).first
        try:
            if tab.is_visible():
                tab.click()
                self.page.wait_for_timeout(2_000)
                return
        except Error:
            pass

        self.page.evaluate(
            """
            () => {
                const el = Array.from(document.querySelectorAll('*'))
                  .find((node) => node.textContent?.trim() === '图文信息');
                if (el) el.click();
            }
            """
        )
        self.page.wait_for_timeout(2_000)

    def _extract_section_candidates(
        self,
        labels: tuple[str, ...],
        *,
        source_type: Literal["main", "detail", "unknown"],
        source_priority: int,
    ) -> list[ImageCandidate]:
        self.open_graphic_info_tab()
        self.page.evaluate("window.scrollTo(0, 600)")
        self.page.wait_for_timeout(1_500)

        section_urls = self.page.evaluate(
            """
            (labels) => {
                const normalizeText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const extractUrl = (node) => {
                    const candidates = [
                        node.getAttribute?.('data-origin'),
                        node.getAttribute?.('data-origin-src'),
                        node.getAttribute?.('data-original'),
                        node.getAttribute?.('data-url'),
                        node.getAttribute?.('data-src'),
                        node.getAttribute?.('src'),
                        node.getAttribute?.('href'),
                    ];
                    for (const value of candidates) {
                        const url = normalizeText(value);
                        if (url.startsWith('http')) return url;
                    }
                    const style = node.getAttribute?.('style') || '';
                    const match = style.match(/url\\(["']?([^"')]+)["']?\\)/);
                    return match ? match[1] : '';
                };
                const collectUrls = (root) => {
                    const urls = [];
                    const seen = new Set();
                    for (const node of root.querySelectorAll('img, a, div, span, li, picture source')) {
                        const url = extractUrl(node);
                        if (!url || !url.startsWith('http')) {
                            continue;
                        }
                        if (!/xiaohongshu\\.com/.test(url)) {
                            continue;
                        }
                        if (seen.has(url)) {
                            continue;
                        }
                        seen.add(url);
                        urls.push(url);
                    }
                    return urls;
                };
                const roots = [];
                for (const node of document.querySelectorAll('div, section, span, p, label, h2, h3, h4, li')) {
                    if (!labels.includes(normalizeText(node.textContent))) {
                        continue;
                    }
                    roots.push(
                        node.closest('[class*="form"], [class*="item"], [class*="section"], [class*="block"], [class*="card"], .ant-form-item, .form-item, .d-form-item')
                        || node.parentElement
                        || node
                    );
                }
                const urls = [];
                const seen = new Set();
                for (const root of roots) {
                    for (const url of collectUrls(root)) {
                        if (seen.has(url)) {
                            continue;
                        }
                        seen.add(url);
                        urls.push(url);
                    }
                }
                return urls;
            }
            """,
            labels,
        )

        candidates: list[ImageCandidate] = []
        for position, url in enumerate(section_urls, start=1):
            candidates.append(
                ImageCandidate(
                    source_url=url,
                    normalized_url=normalize_image_url(url),
                    source_type=source_type,
                    source_priority=source_priority,
                    position=position,
                )
            )
        return candidates

    def extract_image_candidates(self) -> tuple[list[ImageCandidate], str, int]:
        main_candidates = self._extract_section_candidates(
            ("商品主图",),
            source_type="main",
            source_priority=0,
        )
        detail_candidates = self._extract_section_candidates(
            ("详情页图片", "详情图片"),
            source_type="detail",
            source_priority=1,
        )

        strategy = "sectioned"
        candidates = dedupe_candidates([*main_candidates, *detail_candidates])
        html = self.page.content()
        ci_domain_count = html.count("ci.xiaohongshu.com")

        if candidates:
            return candidates, strategy, ci_domain_count

        fallback_urls = []
        seen = set()
        for uuid in re.findall(r"material_space/([a-f0-9-]{36})", html):
            url = f"https://qimg.xiaohongshu.com/material_space/{uuid}"
            if url in seen:
                continue
            seen.add(url)
            fallback_urls.append(
                ImageCandidate(
                    source_url=url,
                    normalized_url=normalize_image_url(url),
                    source_type="unknown",
                    source_priority=9,
                    position=len(fallback_urls) + 1,
                )
            )
        return fallback_urls, "html_fallback", ci_domain_count

    def download_images(
        self,
        product: ProductSummary,
        *,
        limit: int = 3,
        force_download: bool = False,
    ) -> ProductImages:
        del limit
        candidates, strategy, ci_domain_count = self.extract_image_candidates()
        required_count = len(candidates)
        if required_count == 0:
            raise RuntimeError(f"商品 {product.id} 未提取到可用图片。")
        product_dir = self.settings.images_dir / product.id
        product_dir.mkdir(parents=True, exist_ok=True)

        if not force_download:
            existing_images = self._load_existing_images(product_dir, candidates)
            if len(existing_images) >= required_count:
                return ProductImages(
                    product_id=product.id,
                    product_name=product.name,
                    qimg_urls=[candidate.normalized_url for candidate in candidates],
                    download_strategy="existing_files",
                    ci_domain_count=ci_domain_count,
                    downloaded_images=existing_images[:required_count],
                )

        for child in product_dir.iterdir():
            if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                child.unlink()

        downloaded_images: list[DownloadedImage] = []
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            for index, candidate in enumerate(candidates, 1):
                download_url = candidate.normalized_url or candidate.source_url
                response = client.get(download_url)
                response.raise_for_status()
                image_bytes = response.content

                with Image.open(BytesIO(image_bytes)) as image:
                    image_format = (image.format or "JPEG").lower()
                    width, height = image.size

                sha256 = hashlib.sha256(image_bytes).hexdigest()
                suffix = ".png" if image_format == "png" else ".jpg"
                path = product_dir / f"{index:03d}{suffix}"
                path.write_bytes(image_bytes)

                downloaded_images.append(
                    DownloadedImage(
                        index=index,
                        image_id=build_image_id(product.id, candidate.normalized_url, candidate.source_type, candidate.position),
                        path=str(path),
                        source_url=candidate.source_url,
                        normalized_url=candidate.normalized_url,
                        source_type=candidate.source_type,
                        source_priority=candidate.source_priority,
                        position=candidate.position,
                        bytes=len(image_bytes),
                        format=image_format,
                        width=width,
                        height=height,
                        sha256=sha256,
                    )
                )

        downloaded_images = self._finalize_downloaded_images(
            product_dir,
            dedupe_downloaded_images(downloaded_images),
        )
        if not downloaded_images:
            raise RuntimeError(f"商品 {product.id} 未下载到任何图片。")

        bundle = ProductImages(
            product_id=product.id,
            product_name=product.name,
            qimg_urls=[candidate.normalized_url for candidate in candidates],
            download_strategy=strategy,
            ci_domain_count=ci_domain_count,
            downloaded_images=downloaded_images,
        )
        return bundle

    def _load_existing_images(
        self,
        product_dir: Path,
        candidates: list[ImageCandidate],
    ) -> list[DownloadedImage]:
        existing: list[DownloadedImage] = []

        for index, candidate in enumerate(candidates, 1):
            matched_path = None
            for suffix in (".jpg", ".png", ".jpeg", ".webp"):
                existing_candidate = product_dir / f"{index:03d}{suffix}"
                if existing_candidate.exists():
                    matched_path = existing_candidate
                    break

            if matched_path is None:
                break

            with Image.open(matched_path) as image:
                image_format = (image.format or "JPEG").lower()
                width, height = image.size
            sha256 = hashlib.sha256(matched_path.read_bytes()).hexdigest()

            existing.append(
                DownloadedImage(
                    index=index,
                    image_id=build_image_id(
                        product_dir.name,
                        candidate.normalized_url,
                        candidate.source_type,
                        candidate.position,
                    ),
                    path=str(matched_path),
                    source_url=candidate.source_url,
                    normalized_url=candidate.normalized_url,
                    source_type=candidate.source_type,
                    source_priority=candidate.source_priority,
                    position=candidate.position,
                    bytes=matched_path.stat().st_size,
                    format=image_format,
                    width=width,
                    height=height,
                    sha256=sha256,
                )
            )

        return self._finalize_downloaded_images(product_dir, dedupe_downloaded_images(existing))

    def _finalize_downloaded_images(
        self,
        product_dir: Path,
        downloaded_images: list[DownloadedImage],
    ) -> list[DownloadedImage]:
        finalized: list[DownloadedImage] = []
        keep_paths = {image.path for image in downloaded_images}
        for child in product_dir.iterdir():
            if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and str(child) not in keep_paths:
                child.unlink()

        for index, image in enumerate(downloaded_images, start=1):
            current_path = Path(image.path)
            suffix = current_path.suffix or (".png" if image.format == "png" else ".jpg")
            target_path = product_dir / f"{index:03d}{suffix}"
            if current_path != target_path:
                current_path.replace(target_path)
            finalized.append(
                image.model_copy(
                    update={
                        "index": index,
                        "path": str(target_path),
                    }
                )
            )
        return finalized


class ProductListPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def wait_until_ready(self) -> None:
        self.page.wait_for_url("**/app-item/list/**", timeout=15_000)
        self.page.wait_for_selector("table tbody tr", timeout=15_000)
        self.page.wait_for_timeout(2_000)

    def get_products(self, limit: int = 10) -> list[ProductSummary]:
        self.wait_until_ready()
        raw_products = self.page.evaluate(
            """
            () => {
                const rows = document.querySelectorAll('table tbody tr, [class*="table"] tr');
                const result = [];
                const seen = new Set();
                for (const row of rows) {
                    const text = row.innerText || '';
                    const idMatch = text.match(/商品ID[：:]\\s*([a-f0-9]{24})/);
                    if (!idMatch) {
                        continue;
                    }
                    const id = idMatch[1];
                    if (seen.has(id)) {
                        continue;
                    }
                    seen.add(id);
                    const nameMatch = text.match(/^([^商品ID]+)/);
                    const name = nameMatch
                        ? nameMatch[1].trim().replace(/\\s+/g, ' ').slice(0, 120)
                        : '';
                    result.push({ id, name });
                }
                return result;
            }
            """
        )
        return [ProductSummary(**item) for item in raw_products[:limit]]

    def get_product_images(
        self,
        product: ProductSummary,
        *,
        limit: int = 3,
        force_download: bool = False,
    ) -> ProductImages:
        detail_page = ProductDetailPage(self.page, self.settings)
        detail_page.open(product.id)
        return detail_page.download_images(
            product,
            limit=limit,
            force_download=force_download,
        )

    def _dismiss_blocking_modal(self) -> bool:
        return _dismiss_blocking_modal(self.page)

    def _wait_for_modal_mask_to_clear(self, timeout_ms: int = 5_000) -> bool:
        return _wait_for_modal_mask_to_clear(self.page, timeout_ms)

    def _clear_blocking_mask(self, timeout_s: float = 8.0) -> bool:
        """尽力清掉商品列表页的运营弹窗遮罩（``d-modal-mask``），返回遮罩是否已消失。**不抛**。

        遮罩会拦截一切 pointer 事件，导致「去发布」「搜索」等按钮点击卡满默认 30s 超时。
        关弹窗 → 等遮罩消失 → 仍在则按 Escape，循环至清掉或超时。``_prepare_publish_click``
        与 ``_search_product`` 共用此逻辑。
        """
        if self._wait_for_modal_mask_to_clear(timeout_ms=1_500):
            return True
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            dismissed = self._dismiss_blocking_modal()
            if self._wait_for_modal_mask_to_clear(timeout_ms=1_500):
                return True
            if not dismissed:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(500)
                if self._wait_for_modal_mask_to_clear(timeout_ms=1_500):
                    return True
        return False

    def _prepare_publish_click(self) -> None:
        if not self._clear_blocking_mask():
            raise RuntimeError("商品列表页存在未关闭的弹窗遮罩，无法点击“去发布”。")

    def _search_product(self, product_id: str) -> bool:
        """用列表页顶部搜索框按商品 ID 精准过滤，等目标行出现。返回是否真正执行了搜索。

        默认列表页只渲染第 1 页（每页 20 条、最新在前）；fetch 之后商家若上新，计划里较老的商品会被
        挤到后页，``filter(has_text=...)`` 在当前 DOM 里就找不到。搜索框支持「商品名称/ID/货号/编码」，
        按 24 位 ID 搜必得唯一结果，彻底摆脱分页与排序。搜索框不存在时返回 False，调用方退回旧的当页定位。
        """
        search = self.page.locator("input.d-text[placeholder*='商品货号']").first
        if search.count() == 0:
            return False
        # 运营弹窗遮罩(d-modal-mask)会拦截搜索框/按钮的点击，导致 click 卡满 30s 超时（线上批量发布尾部
        # 实测翻车）。与「去发布」同源：交互前先尽力清遮罩，点击套有界超时 + 拦截重试一次。
        self._clear_blocking_mask()
        search.fill(product_id)
        button = self.page.get_by_role("button", name="搜索", exact=True).first
        clicked = False
        last_error: Error | None = None
        for _ in range(2):
            self._clear_blocking_mask()
            try:
                if button.count() > 0:
                    button.click(timeout=5_000)
                else:
                    search.press("Enter")
                clicked = True
                break
            except Error as exc:
                last_error = exc
                if "intercepts pointer events" not in str(exc):
                    raise
                self._dismiss_blocking_modal()
                self.page.wait_for_timeout(800)
        if not clicked and last_error is not None:
            raise last_error
        # 等列表刷新到目标行出现（轮询，避开 f-string 选择器转义）；超时不抛，交由调用方按 count==0 报错。
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.page.locator("table tbody tr").filter(has_text=product_id).count() > 0:
                break
            self.page.wait_for_timeout(300)
        self.page.wait_for_timeout(500)
        return True

    def _locate_product_row(self, product_id: str):
        """定位目标商品行：当前页已渲染则直接用（快路径），否则用搜索框精准过滤后重新定位。"""
        row = self.page.locator("table tbody tr").filter(has_text=product_id).first
        if row.count() > 0:
            return row
        if self._search_product(product_id):
            row = self.page.locator("table tbody tr").filter(has_text=product_id).first
        return row

    def open_publish_popup(self, product_id: str) -> Page:
        """点击“去发布”并返回弹出的发布页 Page 句柄；由调用方包装为 PublishPage（解开循环依赖）。"""
        self.wait_until_ready()
        row = self._locate_product_row(product_id)
        if row.count() == 0:
            raise RuntimeError(f"未在商品列表中找到商品 {product_id}。")

        publish_trigger = row.get_by_text("去发布", exact=True).first
        last_error: Error | None = None
        for _ in range(2):
            self._prepare_publish_click()
            try:
                with self.page.expect_popup() as popup_info:
                    publish_trigger.click(timeout=5_000)
                popup = popup_info.value
                popup.wait_for_load_state("domcontentloaded")
                popup.wait_for_timeout(4_000)
                return popup
            except Error as exc:
                last_error = exc
                if "intercepts pointer events" not in str(exc):
                    raise
                self._dismiss_blocking_modal()
                self.page.wait_for_timeout(800)

        raise RuntimeError(f"点击商品 {product_id} 的“去发布”失败：{last_error}")

