from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import Error, Page

from .config import Settings
from .models import DownloadedImage, ProductImages, ProductSummary


def locator_is_visible(locator) -> bool:
    try:
        return locator.is_visible()
    except Error:
        return False


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

    def open_publish_page(self, product_id: str) -> "PublishPage":
        self.wait_until_ready()
        row = self.page.locator("table tbody tr").filter(has_text=product_id).first
        if row.count() == 0:
            raise RuntimeError(f"未在商品列表中找到商品 {product_id}。")

        publish_trigger = row.get_by_text("去发布", exact=True).first
        with self.page.expect_popup() as popup_info:
            publish_trigger.click()
        publish_page = popup_info.value
        publish_page.wait_for_load_state("domcontentloaded")
        publish_page.wait_for_timeout(2_000)
        return PublishPage(publish_page, self.settings)


class PublishPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def wait_until_ready(self) -> None:
        self.page.wait_for_url("**/app-note/publish**", timeout=20_000)
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(2_000)

    def open_upload_material_step(self) -> None:
        self.wait_until_ready()
        for text in ("上传图文", "上传笔记素材"):
            tab = self.page.get_by_text(text, exact=True).first
            if locator_is_visible(tab):
                tab.click()
                self.page.wait_for_timeout(2_000)

    def upload_images(self, paths: list[str]) -> None:
        self.open_upload_material_step()
        file_inputs = self.page.locator("input[type='file']")
        if file_inputs.count() <= 0:
            raise RuntimeError("发布页未找到文件上传控件。")
        file_inputs.first.set_input_files(paths)
        self.page.wait_for_timeout(5_000)

    def open_note_info_step(self) -> None:
        note_info = self.page.get_by_text("填写笔记信息", exact=True).first
        if locator_is_visible(note_info):
            note_info.click()
            self.page.wait_for_timeout(2_000)

    def fill_title(self, title: str) -> str:
        self.open_note_info_step()
        selectors = [
            "input[placeholder*='填写标题']",
            "input[placeholder*='标题']",
        ]
        for selector in selectors:
            locator = self.page.locator(selector).first
            if locator_is_visible(locator):
                locator.fill(title)
                return selector
        raise RuntimeError("未找到标题输入框。")

    def _get_editor_locator(self):
        self.open_note_info_step()
        for selector in (".ql-editor", "[contenteditable='true']", "textarea"):
            locator = self.page.locator(selector).first
            if locator_is_visible(locator):
                return locator, selector
        raise RuntimeError("未找到正文编辑器。")

    def fill_content(self, content: str) -> str:
        editor, selector = self._get_editor_locator()
        editor.click()
        editor.fill(content)
        self.page.wait_for_timeout(500)
        return selector

    def add_topic(self, topic_keyword: str) -> dict:
        editor, selector = self._get_editor_locator()
        editor.click()
        self.page.keyboard.press("End")
        self.page.keyboard.type(f" #{topic_keyword}")
        self.page.wait_for_timeout(1_500)

        selected_text = self.page.evaluate(
            """
            (topicKeyword) => {
                const candidates = Array.from(
                  document.querySelectorAll('li, div, span, a, button')
                ).filter((node) => {
                    const text = (node.textContent || '').trim().replace(/\\s+/g, ' ');
                    if (!text || !text.includes('#' + topicKeyword)) return false;
                    if (node.closest('.ql-editor') || node.closest('[contenteditable="true"]')) {
                        return false;
                    }
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    if (rect.width <= 0 || rect.height <= 0) return false;
                    if (style.visibility === 'hidden' || style.display === 'none') return false;
                    return true;
                });

                const preferred = candidates.find((node) => {
                    const text = (node.textContent || '').trim().replace(/\\s+/g, ' ');
                    return text.startsWith('#' + topicKeyword) && text.includes('浏览');
                }) || candidates.find((node) => {
                    const text = (node.textContent || '').trim().replace(/\\s+/g, ' ');
                    return text.startsWith('#' + topicKeyword);
                });

                if (preferred) {
                    preferred.click();
                    return (preferred.textContent || '').trim().replace(/\\s+/g, ' ');
                }
                return null;
            }
            """,
            topic_keyword,
        )
        self.page.wait_for_timeout(1_000)
        return {
            "editor_selector": selector,
            "topic_keyword": topic_keyword,
            "topic_candidate_selected": selected_text,
        }

    def add_product(self, product_id: str) -> dict:
        result = {
            "add_product_button_clicked": False,
            "search_box_found": False,
            "checkbox_clicked": False,
            "save_clicked": False,
        }

        add_button = self.page.get_by_text("添加商品").first
        if not locator_is_visible(add_button):
            raise RuntimeError("发布页未找到“添加商品”按钮。")

        add_button.click()
        result["add_product_button_clicked"] = True
        self.page.wait_for_timeout(2_000)

        search_input = self.page.get_by_placeholder("搜索商品ID 或 商品名称").first
        if not locator_is_visible(search_input):
            raise RuntimeError("添加商品弹层未找到搜索框。")

        result["search_box_found"] = True
        search_input.fill(product_id)
        self.page.wait_for_timeout(2_000)

        checkbox = self.page.locator(".d-checkbox-indicator").first
        if not locator_is_visible(checkbox):
            raise RuntimeError("添加商品弹层未找到商品勾选框。")

        checkbox.click()
        result["checkbox_clicked"] = True
        self.page.wait_for_timeout(800)

        save_button = self.page.get_by_text("保存", exact=True).first
        if not locator_is_visible(save_button):
            raise RuntimeError("添加商品弹层未找到保存按钮。")

        save_button.click()
        result["save_clicked"] = True
        self.page.wait_for_timeout(2_000)
        return result

    def click_publish(self) -> None:
        publish_button = self.page.get_by_text("发布", exact=True).first
        if not locator_is_visible(publish_button):
            raise RuntimeError("发布页未找到“发布”按钮。")
        publish_button.click()

    def verify_success(self, timeout_ms: int = 15_000) -> dict:
        url_before = self.page.url
        try:
            self.page.wait_for_url("**/app-note/note-list**", timeout=timeout_ms)
        except Error:
            pass

        self.page.wait_for_timeout(2_000)
        body_text = self.page.locator("body").inner_text(timeout=3_000)
        success_markers = ["发布成功", "笔记管理", "笔记列表", "发布完成"]
        success_signals = [
            marker for marker in success_markers if marker in body_text or marker in self.page.url
        ]
        return {
            "url_before": url_before,
            "url_after": self.page.url,
            "title_after": self.page.title(),
            "success_signals": success_signals,
            "success": "/app-note/note-list" in self.page.url or bool(success_signals),
        }

    def screenshot_on_failure(self, path: str) -> None:
        self.page.screenshot(path=path, full_page=True)
