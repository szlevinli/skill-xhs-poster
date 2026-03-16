from __future__ import annotations

import json
import re
import time
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
        required_count = min(limit, len(qimg_urls))
        if required_count == 0:
            raise RuntimeError(f"商品 {product.id} 未提取到可用主图。")
        product_dir = self.settings.images_dir / product.id
        product_dir.mkdir(parents=True, exist_ok=True)

        if not force_download:
            existing_images = self._load_existing_images(product_dir, qimg_urls, limit=limit)
            if len(existing_images) >= required_count:
                return ProductImages(
                    product_id=product.id,
                    product_name=product.name,
                    qimg_urls=qimg_urls,
                    download_strategy="existing_files",
                    ci_domain_count=ci_domain_count,
                    downloaded_images=existing_images[:required_count],
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

        if not downloaded_images:
            raise RuntimeError(f"商品 {product.id} 未下载到任何主图。")

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
        publish_page.wait_for_timeout(4_000)
        return PublishPage(publish_page, self.settings)


class PublishPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def wait_until_ready(self) -> None:
        self.page.wait_for_url("**/app-note/publish**", timeout=20_000)
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(2_000)

    def _click_text_action(self, text: str) -> bool:
        locator = self.page.get_by_text(text, exact=True).first
        if locator_is_visible(locator):
            locator.click()
            self.page.wait_for_timeout(2_000)
            return True
        clicked = self.page.evaluate(
            """
            (targetText) => {
                const el = Array.from(document.querySelectorAll('*'))
                  .find((node) => (node.textContent || '').trim() === targetText);
                if (!el) return false;
                el.click();
                return true;
            }
            """,
            text,
        )
        if clicked:
            self.page.wait_for_timeout(2_000)
            return True
        return False

    def open_upload_material_step(self) -> None:
        self.wait_until_ready()
        # 某些发布页会先停在入口态，需先切到手动创作，再进入图文上传。
        self._click_text_action("手动创作")
        for text in ("上传图文", "上传笔记素材"):
            self._click_text_action(text)

    def inspect_upload_state(self) -> dict:
        return self.page.evaluate(
            """
            () => {
                const normalize = (text) => (text || '').trim().replace(/\\s+/g, ' ');
                const isVisible = (node) => {
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const tabs = Array.from(document.querySelectorAll('span, div, button, a'))
                  .map((node) => normalize(node.textContent))
                  .filter((text) => ['手动创作', '智能创作', '上传图文', '上传笔记素材', '填写笔记信息'].includes(text));
                const fileInputs = Array.from(document.querySelectorAll("input[type='file']")).map((node) => ({
                    accept: node.getAttribute('accept') || '',
                    multiple: node.hasAttribute('multiple'),
                    class_name: node.className || '',
                    visible: isVisible(node),
                }));
                return {
                    url: window.location.href,
                    tabs,
                    file_inputs: fileInputs,
                    body_excerpt: normalize(document.body?.innerText || '').slice(0, 1000),
                };
            }
            """
        )

    def upload_images(self, paths: list[str]) -> None:
        diagnostics: list[dict] = []
        for _ in range(3):
            self.open_upload_material_step()
            try:
                self.page.wait_for_selector("input[type='file']", state="attached", timeout=8_000)
            except Exception:
                diagnostics.append(self.inspect_upload_state())
                continue

            file_inputs = self.page.locator("input[type='file']")
            input_count = file_inputs.count()
            target_index = None
            target_multiple = False

            for index in range(input_count):
                locator = file_inputs.nth(index)
                accept = (locator.get_attribute("accept") or "").lower()
                is_multiple = locator.get_attribute("multiple") is not None
                if any(video_ext in accept for video_ext in (".mp4", ".mov", ".flv", ".mkv", ".rm", ".rmvb", ".m4v", ".mpg", ".mpeg", ".ts")):
                    continue
                if any(image_hint in accept for image_hint in ("image", ".png", ".jpg", ".jpeg", ".webp")):
                    target_index = index
                    target_multiple = is_multiple
                    break

            if target_index is None:
                for index in range(input_count):
                    locator = file_inputs.nth(index)
                    accept = (locator.get_attribute("accept") or "").lower()
                    if any(video_ext in accept for video_ext in (".mp4", ".mov", ".flv", ".mkv", ".rm", ".rmvb", ".m4v", ".mpg", ".mpeg", ".ts")):
                        continue
                    target_index = index
                    target_multiple = locator.get_attribute("multiple") is not None
                    break

            if target_index is None:
                diagnostics.append(self.inspect_upload_state())
                self.page.wait_for_timeout(2_000)
                continue

            file_input = file_inputs.nth(target_index)
            if target_multiple:
                file_input.set_input_files(paths)
                self.page.wait_for_timeout(8_000)
                return

            for path in paths:
                file_input.set_input_files(path)
                self.page.wait_for_timeout(3_000)
            return

        raise RuntimeError(
            f"发布页未找到图片上传控件。diagnostics={json.dumps(diagnostics, ensure_ascii=False)}"
        )

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

    def _get_first_topic_candidate(self, topic_keyword: str) -> dict | None:
        try:
            self.page.wait_for_function(
                """
                (topicKeyword) => {
                    const isVisible = (node) => {
                        if (!node) return false;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const normalize = (text) => (text || '').trim().replace(/\\s+/g, ' ');
                    const keyword = normalize(topicKeyword).replace(/^#/, '');
                    const list = document.querySelector('#quill-mention-list');
                    const container = list?.closest('.ql-mention-list-container');
                    if (!list || (container && !isVisible(container))) return false;
                    const items = Array.from(list.querySelectorAll('.ql-mention-list-item')).filter((node) => isVisible(node));
                    return items.some((node) => {
                        const name = normalize(node.getAttribute('data-name') || node.getAttribute('data-value') || node.textContent).replace(/^#/, '');
                        return name === keyword || name.includes(keyword);
                    });
                }
                """,
                arg=topic_keyword,
                timeout=4_000,
            )
        except Error:
            self.page.wait_for_timeout(1_000)
        return self.page.evaluate(
            """
            (topicKeyword) => {
                const normalize = (text) => (text || '').trim().replace(/\\s+/g, ' ');
                const isVisible = (node) => {
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const list = document.querySelector('#quill-mention-list');
                const container = list?.closest('.ql-mention-list-container');
                if (!list || (container && !isVisible(container))) return null;
                const items = Array.from(list.querySelectorAll('.ql-mention-list-item'))
                  .filter((node) => isVisible(node));
                if (!items.length) return null;
                const keyword = normalize(topicKeyword).replace(/^#/, '');
                const scored = items.map((node, index) => {
                    const rawName = normalize(node.getAttribute('data-name') || node.getAttribute('data-value') || node.textContent);
                    const normalizedName = rawName.replace(/^#/, '');
                    let score = 0;
                    if (normalizedName === keyword) {
                        score = 3;
                    } else if (normalizedName.startsWith(keyword)) {
                        score = 2;
                    } else if (normalizedName.includes(keyword)) {
                        score = 1;
                    }
                    return { node, index, rawName, normalizedName, score };
                });
                scored.sort((a, b) => b.score - a.score || a.index - b.index);
                const best = scored[0];
                if (!best || best.score <= 0) return null;
                const first = best.node;
                return {
                  selected_text: normalize(first.textContent),
                  candidate_count: items.length,
                  id: first.id || '',
                  data_id: first.getAttribute('data-id') || '',
                  data_name: first.getAttribute('data-name') || '',
                  data_value: first.getAttribute('data-value') || '',
                  data_link: first.getAttribute('data-link') || '',
                };
            }
            """,
            topic_keyword,
        )

    def _click_first_topic_candidate(self, topic_keyword: str) -> dict:
        candidate = self._get_first_topic_candidate(topic_keyword)
        if not candidate:
            raise RuntimeError(f"未找到话题“{topic_keyword}”的候选项。")

        clicked = self.page.evaluate(
            """
            ({ id, dataId, topicKeyword }) => {
                const normalize = (text) => (text || '').trim().replace(/\\s+/g, ' ');
                const keyword = normalize(topicKeyword).replace(/^#/, '');
                const items = Array.from(document.querySelectorAll('#quill-mention-list .ql-mention-list-item'));
                const item = items.find((node) => node.id === id)
                  || items.find((node) => (node.getAttribute('data-id') || '') === dataId)
                  || items.find((node) => {
                      const name = normalize(node.getAttribute('data-name') || node.getAttribute('data-value') || node.textContent).replace(/^#/, '');
                      return name === keyword;
                  })
                  || items.find((node) => {
                      const name = normalize(node.getAttribute('data-name') || node.getAttribute('data-value') || node.textContent).replace(/^#/, '');
                      return name.includes(keyword);
                  });
                if (!item) return false;
                item.scrollIntoView({ block: 'nearest' });
                item.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true }));
                item.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                item.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                item.click();
                return true;
            }
            """,
            {
                "id": candidate["id"],
                "dataId": candidate["data_id"],
                "topicKeyword": topic_keyword,
            },
        )
        if not clicked:
            self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(800)
        return candidate

    def _verify_topic_applied(self, topic_keyword: str) -> dict:
        self.page.wait_for_timeout(1_000)
        editor, selector = self._get_editor_locator()
        verification = self.page.evaluate(
            """
            ({ topicKeyword, selector }) => {
                const editor = document.querySelector(selector) || document.querySelector('.ql-editor') || document.querySelector('[contenteditable="true"]');
                if (!editor) return { applied: false, reason: 'editor_not_found' };

                const topicText = '#' + topicKeyword;
                const html = editor.innerHTML || '';
                const text = (editor.innerText || editor.textContent || '').replace(/\\s+/g, ' ').trim();
                const linkedNode = Array.from(editor.querySelectorAll('*')).find((node) => {
                    const nodeText = (node.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!nodeText.includes(topicText)) return false;
                    const tag = node.tagName.toLowerCase();
                    const cls = node.className || '';
                    return tag === 'a'
                      || tag === 'span'
                      || /mention/i.test(String(cls))
                      || /topic|tag|link|mention|editor/i.test(String(cls))
                      || node.hasAttribute('href')
                      || node.hasAttribute('data-topic-id')
                      || node.hasAttribute('data-type')
                      || node.hasAttribute('data-id')
                      || node.hasAttribute('data-value');
                });
                const mentionList = document.querySelector('#quill-mention-list');
                const mentionListVisible = !!mentionList && (() => {
                    const rect = mentionList.getBoundingClientRect();
                    const style = window.getComputedStyle(mentionList);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                })();
                return {
                    applied: Boolean(linkedNode),
                    reason: linkedNode ? 'linked_node_found' : (mentionListVisible ? 'mention_list_still_visible' : 'linked_node_missing'),
                    editor_html: html,
                    editor_text: text,
                    mention_list_visible: mentionListVisible,
                };
            }
            """,
            {"topicKeyword": topic_keyword, "selector": selector},
        )
        if not verification.get("applied"):
            raise RuntimeError(
                f"话题“{topic_keyword}”未成功转为可点击节点：{verification.get('reason')}"
            )
        return verification

    def add_topic(self, topic_keyword: str) -> dict:
        editor, selector = self._get_editor_locator()
        editor.click()
        self.page.keyboard.press("End")
        self.page.keyboard.type("#")
        self.page.wait_for_timeout(800)
        self.page.keyboard.type(topic_keyword)
        candidate = self._click_first_topic_candidate(topic_keyword)
        try:
            verification = self._verify_topic_applied(topic_keyword)
        except RuntimeError:
            # 部分页面中首次点击不会提交 mention，再回退为 Enter 提交当前高亮项。
            self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(800)
            verification = self._verify_topic_applied(topic_keyword)
        return {
            "editor_selector": selector,
            "topic_keyword": topic_keyword,
            "topic_candidate_selected": candidate["selected_text"],
            "topic_candidate_name": candidate.get("data_name") or candidate.get("data_value") or "",
            "topic_candidate_count": candidate["candidate_count"],
            "topic_applied": verification["applied"],
        }

    def _open_add_product_dialog(self) -> None:
        add_button = self.page.get_by_text("添加商品").first
        if not locator_is_visible(add_button):
            raise RuntimeError("发布页未找到“添加商品”按钮。")
        add_button.click()
        search_input = self.page.get_by_placeholder("搜索商品ID 或 商品名称").first
        search_input.wait_for(state="visible", timeout=10_000)
        self.page.wait_for_timeout(500)

    def _dismiss_add_product_dialog(self) -> None:
        for locator in (
            self.page.get_by_text("取消", exact=True).first,
            self.page.get_by_text("关闭", exact=True).first,
            self.page.locator(".d-modal-close").first,
            self.page.locator(".ant-modal-close").first,
            self.page.locator("[aria-label='Close']").first,
        ):
            if locator_is_visible(locator):
                locator.click()
                self.page.wait_for_timeout(500)
                return
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)

    def _click_add_product_dialog_save(self) -> bool:
        clicked = self.page.evaluate(
            """
            () => {
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (node) => {
                    if (!node) return false;
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .d-modal, .ant-modal, .semi-modal, .modal'))
                    .filter((node) => isVisible(node));
                const dialog = dialogs.find((node) => normalize(node.textContent).includes('选择商品')) || dialogs[dialogs.length - 1];
                if (!dialog) return false;
                const buttons = Array.from(dialog.querySelectorAll('button, span, div, a'))
                    .filter((node) => isVisible(node) && normalize(node.textContent) === '保存');
                const button = buttons[buttons.length - 1];
                if (!button) return false;
                button.click();
                return true;
            }
            """
        )
        if clicked:
            self.page.wait_for_timeout(800)
        return bool(clicked)

    def _click_product_candidate(self, product_id: str, timeout_ms: int = 12_000) -> dict:
        deadline = time.monotonic() + timeout_ms / 1000
        last_state: dict = {"found": False, "reason": "waiting_for_candidate"}
        while time.monotonic() < deadline:
            candidate = self.page.evaluate(
                """
                (productId) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (node) => {
                        if (!node) return false;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .d-modal, .ant-modal, .semi-modal, .modal'))
                        .filter((node) => isVisible(node));
                    const scope = dialogs[dialogs.length - 1] || document.body;
                    const checkboxSelector = 'input[type="checkbox"], .d-checkbox-indicator, .ant-checkbox-input, .ant-checkbox, .semi-checkbox, [role="checkbox"]';
                    const rowSelectors = [
                        'tr',
                        '.d-table-row',
                        '.ant-table-row',
                        '.semi-table-row',
                        '.table-row',
                        'li',
                        '.list-item',
                        '.d-list-item',
                        '.ant-list-item',
                        '.semi-list-item',
                        '.d-grid-item',
                        '.ant-card',
                        '.semi-card',
                        '[data-row-key]',
                    ];
                    const rows = Array.from(scope.querySelectorAll(rowSelectors.join(','))).filter((node) => {
                        const text = normalize(node.textContent);
                        return text.includes(productId);
                    });
                    const resolveCandidateRow = () => {
                        if (rows.length) return rows[0];
                        const textNodes = Array.from(scope.querySelectorAll('*')).filter((node) => {
                            if (!isVisible(node)) return false;
                            const text = normalize(node.textContent);
                            if (!text.includes(productId)) return false;
                            return text.length <= 300;
                        });
                        for (const node of textNodes) {
                            let current = node;
                            while (current && current !== scope) {
                                if (typeof current.querySelector === 'function' && current.querySelector(checkboxSelector)) {
                                    return current;
                                }
                                current = current.parentElement;
                            }
                        }
                        return null;
                    };
                    const row = resolveCandidateRow();
                    if (!row) {
                        return {
                            found: false,
                            reason: normalize(scope.textContent).includes(productId) ? 'text_found_without_row' : 'row_not_found',
                            row_count: rows.length,
                        };
                    }
                    const checkbox = row.querySelector(checkboxSelector);
                    if (!checkbox) {
                        return {
                            found: false,
                            reason: 'checkbox_not_found',
                            row_count: rows.length,
                            row_text: normalize(row.textContent).slice(0, 200),
                        };
                    }
                    row.scrollIntoView({ block: 'center' });
                    const target = checkbox.closest('label') || checkbox;
                    target.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true }));
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    target.click();
                    return {
                        found: true,
                        reason: 'clicked',
                        row_count: rows.length,
                        row_text: normalize(row.textContent).slice(0, 200),
                    };
                }
                """,
                product_id,
            )
            last_state = candidate
            if candidate.get("found"):
                self.page.wait_for_timeout(500)
                return candidate
            self.page.wait_for_timeout(400)
        raise RuntimeError(
            f"添加商品弹层未找到商品 {product_id} 的可勾选结果：{last_state.get('reason')}"
        )

    def _verify_product_bound(self, product_id: str, timeout_ms: int = 10_000) -> dict:
        deadline = time.monotonic() + timeout_ms / 1000
        last_state: dict = {"bound": False, "reason": "waiting_for_binding"}
        while time.monotonic() < deadline:
            verification = self.page.evaluate(
                """
                (productId) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (node) => {
                        if (!node) return false;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                    };
                    const visibleDialogs = Array.from(document.querySelectorAll('[role="dialog"], .d-modal, .ant-modal, .semi-modal, .modal'))
                        .filter((node) => isVisible(node));
                    const modalStillVisible = visibleDialogs.some((node) => {
                        const text = normalize(node.textContent);
                        return text.includes('选择商品') || text.includes('搜索商品ID') || text.includes('普通商品');
                    });
                    const bodyCandidates = Array.from(document.querySelectorAll('body *')).filter((node) => {
                        if (!isVisible(node)) return false;
                        if (visibleDialogs.some((dialog) => dialog.contains(node))) return false;
                        const text = normalize(node.textContent);
                        if (!text.includes(productId)) return false;
                        if (text.length > 300) return false;
                        return (
                            text.includes('商品ID') ||
                            text.includes('删除') ||
                            text.includes('改规格') ||
                            text.includes('商业推广') ||
                            text.includes('¥')
                        );
                    });
                    const matched = bodyCandidates.sort((a, b) => normalize(a.textContent).length - normalize(b.textContent).length)[0];
                    return {
                        bound: Boolean(matched),
                        reason: matched ? (modalStillVisible ? 'binding_marker_found_modal_still_visible' : 'binding_marker_found') : 'binding_marker_missing',
                        modal_still_visible: modalStillVisible,
                        matched_text: matched ? normalize(matched.textContent).slice(0, 200) : '',
                    };
                }
                """,
                product_id,
            )
            last_state = verification
            if verification.get("bound"):
                return verification
            self.page.wait_for_timeout(400)
        raise RuntimeError(f"商品 {product_id} 保存后未确认绑定成功：{last_state.get('reason')}")

    def add_product(self, product_id: str) -> dict:
        result = {
            "attempts": 0,
            "add_product_button_clicked": False,
            "search_box_found": False,
            "checkbox_clicked": False,
            "save_clicked": False,
            "candidate": {},
            "verification": {},
            "errors": [],
        }

        last_error: RuntimeError | None = None
        for attempt in range(1, 3):
            result["attempts"] = attempt
            try:
                self._open_add_product_dialog()
                result["add_product_button_clicked"] = True

                search_input = self.page.get_by_placeholder("搜索商品ID 或 商品名称").first
                if not locator_is_visible(search_input):
                    raise RuntimeError("添加商品弹层未找到搜索框。")
                result["search_box_found"] = True

                search_input.click()
                search_input.fill("")
                self.page.wait_for_timeout(200)
                search_input.fill(product_id)
                self.page.wait_for_timeout(1_000)

                candidate = self._click_product_candidate(product_id)
                result["candidate"] = candidate
                result["checkbox_clicked"] = True

                if not self._click_add_product_dialog_save():
                    raise RuntimeError("添加商品弹层未找到保存按钮。")
                result["save_clicked"] = True

                verification = self._verify_product_bound(product_id)
                if verification.get("modal_still_visible"):
                    self._dismiss_add_product_dialog()
                    self.page.wait_for_timeout(500)
                    verification = self._verify_product_bound(product_id)
                result["verification"] = verification
                return result
            except RuntimeError as exc:
                last_error = exc
                result["errors"].append(f"attempt_{attempt}: {exc}")
                self._dismiss_add_product_dialog()
                self.page.wait_for_timeout(800)

        raise RuntimeError(str(last_error) if last_error else f"商品 {product_id} 绑定失败")

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
