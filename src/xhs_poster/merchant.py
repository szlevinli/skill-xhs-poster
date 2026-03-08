from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import Error, Page

from .config import Settings
from .models import DownloadedImage, ProductImages, ProductSummary


class ProductDetailPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def open(self, product_id: str) -> None:
        self.page.goto(
            self.settings.merchant_edit_url(product_id),
            wait_until="networkidle",
            timeout=30_000,
        )
        self.page.wait_for_load_state("networkidle")

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

    def extract_qimg_urls(self, limit: int = 5) -> tuple[list[str], str, int]:
        self.open_graphic_info_tab()
        self.page.evaluate("window.scrollTo(0, 600)")
        self.page.wait_for_timeout(1_500)

        img_urls = self.page.evaluate(
            """
            () => {
                const urls = [];
                const seen = new Set();
                for (const img of document.querySelectorAll('img[src]')) {
                    const src = img.src || '';
                    if (!src.includes('qimg.xiaohongshu.com') || !src.includes('material_space')) {
                        continue;
                    }
                    const normalized = src.split('?')[0];
                    if (seen.has(normalized)) {
                        continue;
                    }
                    seen.add(normalized);
                    urls.push(normalized);
                }
                return urls;
            }
            """
        )

        html = self.page.content()
        ci_domain_count = html.count("ci.xiaohongshu.com")

        html_urls = []
        seen = set()
        for uuid in re.findall(r"material_space/([a-f0-9-]{36})", html):
            if uuid in seen:
                continue
            seen.add(uuid)
            html_urls.append(f"https://qimg.xiaohongshu.com/material_space/{uuid}")

        if img_urls:
            return img_urls[:limit], "qimg_from_img", ci_domain_count
        return html_urls[:limit], "qimg_from_html", ci_domain_count

    def download_images(
        self,
        product: ProductSummary,
        *,
        limit: int = 3,
        force_download: bool = False,
    ) -> ProductImages:
        qimg_urls, strategy, ci_domain_count = self.extract_qimg_urls(limit=max(limit, 5))
        product_dir = self.settings.images_dir / product.id
        product_dir.mkdir(parents=True, exist_ok=True)

        if not force_download:
            existing_images = self._load_existing_images(product_dir, qimg_urls, limit=limit)
            if len(existing_images) >= limit:
                return ProductImages(
                    product_id=product.id,
                    product_name=product.name,
                    qimg_urls=qimg_urls,
                    download_strategy="existing_files",
                    ci_domain_count=ci_domain_count,
                    downloaded_images=existing_images[:limit],
                )

        downloaded_images: list[DownloadedImage] = []
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            for index, url in enumerate(qimg_urls[:limit], 1):
                response = client.get(url)
                response.raise_for_status()
                image_bytes = response.content

                with Image.open(BytesIO(image_bytes)) as image:
                    image_format = (image.format or "JPEG").lower()
                    width, height = image.size

                suffix = ".png" if image_format == "png" else ".jpg"
                path = product_dir / f"{index}{suffix}"
                path.write_bytes(image_bytes)

                downloaded_images.append(
                    DownloadedImage(
                        index=index,
                        source_url=url,
                        path=str(path),
                        bytes=len(image_bytes),
                        format=image_format,
                        width=width,
                        height=height,
                    )
                )

        if len(downloaded_images) < limit:
            raise RuntimeError(f"商品 {product.id} 仅下载到 {len(downloaded_images)} 张主图。")

        bundle = ProductImages(
            product_id=product.id,
            product_name=product.name,
            qimg_urls=qimg_urls,
            download_strategy=strategy,
            ci_domain_count=ci_domain_count,
            downloaded_images=downloaded_images,
        )
        return bundle

    def _load_existing_images(
        self,
        product_dir: Path,
        qimg_urls: list[str],
        *,
        limit: int,
    ) -> list[DownloadedImage]:
        existing: list[DownloadedImage] = []

        for index, url in enumerate(qimg_urls[:limit], 1):
            matched_path = None
            for suffix in (".jpg", ".png"):
                candidate = product_dir / f"{index}{suffix}"
                if candidate.exists():
                    matched_path = candidate
                    break

            if matched_path is None:
                break

            with Image.open(matched_path) as image:
                image_format = (image.format or "JPEG").lower()
                width, height = image.size

            existing.append(
                DownloadedImage(
                    index=index,
                    source_url=url,
                    path=str(matched_path),
                    bytes=matched_path.stat().st_size,
                    format=image_format,
                    width=width,
                    height=height,
                )
            )

        return existing


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
