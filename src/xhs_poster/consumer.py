from __future__ import annotations

import re
from urllib.parse import quote

from playwright.sync_api import Error, Page

from .config import Settings


def parse_count_text(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("w"):
        multiplier = 10_000
        text = text[:-1]

    try:
        return int(float(text) * multiplier)
    except ValueError:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None


def parse_metric_from_text(text: str, label: str) -> int | None:
    match = re.search(rf"(\d+(?:\.\d+)?(?:万|w)?)\s*{label}", text, re.I)
    if not match:
        return None
    return parse_count_text(match.group(1))


class ConsumerSearchPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def open_search(self, keyword: str) -> None:
        search_url = (
            "https://www.xiaohongshu.com/search_result"
            f"?keyword={quote(keyword)}&source=web_explore_feed"
        )
        try:
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        except Error as exc:
            if "ERR_ABORTED" not in str(exc):
                raise

    def wait_until_ready(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8_000)
            self.page.wait_for_timeout(800)
            self.page.wait_for_function(
                """
                () => {
                    return Boolean(
                        document.querySelector('a[href*="/explore/"]')
                        || document.body?.innerText?.includes('搜索')
                    );
                }
                """,
                timeout=10_000,
            )
        except Error as exc:
            raise RuntimeError("消费者端搜索页未能正常加载。") from exc

    def _extract_note_cards(self, limit: int) -> list[dict]:
        for _ in range(3):
            try:
                return self.page.evaluate(
                    """
                    (limit) => {
                        const cards = [];
                        const seen = new Set();
                        const anchors = Array.from(document.querySelectorAll('a[href*="/explore/"]'));
                        for (const anchor of anchors) {
                            const href = anchor.getAttribute('href') || '';
                            const match = href.match(/\\/explore\\/([a-zA-Z0-9]+)/);
                            if (!match) continue;
                            const noteId = match[1];
                            if (seen.has(noteId)) continue;
                            seen.add(noteId);

                            const card = anchor.closest('section, article, div') || anchor;
                            const text = (card.innerText || anchor.innerText || '').trim().replace(/\\s+/g, ' ');
                            const image = card.querySelector('img') || anchor.querySelector('img');
                            cards.push({
                                note_id: noteId,
                                url: new URL(href, location.origin).href,
                                text,
                                cover_url: image?.src || '',
                            });
                            if (cards.length >= limit) break;
                        }
                        return cards;
                    }
                    """,
                    limit,
                )
            except Error as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                self.page.wait_for_timeout(500)
        raise RuntimeError("搜索页在提取热门笔记卡片时发生连续导航，未能稳定读取 DOM。")

    def collect_note_cards(self, limit: int = 20) -> list[dict]:
        self.wait_until_ready()
        cards: list[dict] = []
        stable_rounds = 0
        for _ in range(8):
            cards = self._extract_note_cards(limit)
            if len(cards) >= limit:
                return cards[:limit]
            self.page.mouse.wheel(0, 1800)
            self.page.wait_for_timeout(800)
            updated = self._extract_note_cards(limit)
            if len(updated) <= len(cards):
                stable_rounds += 1
            else:
                stable_rounds = 0
                cards = updated
            if stable_rounds >= 2:
                break
        return cards[:limit]


class ConsumerNotePage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def open_note(self, note_id: str, url: str | None = None) -> None:
        target_url = url or f"{self.settings.consumer_home_url}/explore/{note_id}"
        try:
            self.page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        except Error as exc:
            if "ERR_ABORTED" not in str(exc):
                raise

    def wait_until_ready(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8_000)
            self.page.wait_for_timeout(800)
            self.page.wait_for_selector("body", timeout=8_000)
        except Error as exc:
            raise RuntimeError("热门笔记详情页未能正常加载。") from exc

    def extract_detail(self, note_id: str, url: str | None = None) -> dict:
        self.open_note(note_id, url=url)
        self.wait_until_ready()
        detail = None
        for _ in range(3):
            try:
                detail = self.page.evaluate(
                    """
                    () => {
                        const bodyText = document.body?.innerText || '';
                        const normalize = (text) => (text || '').trim().replace(/\\s+/g, ' ');
                        const title =
                            document.querySelector('meta[property="og:title"]')?.content
                            || document.querySelector('h1')?.textContent
                            || document.querySelector('title')?.textContent
                            || '';
                        const article = Array.from(document.querySelectorAll('section, article, div'))
                            .map((node) => normalize(node.innerText))
                            .filter((text) => text.length >= 30)
                            .sort((a, b) => b.length - a.length)[0] || normalize(bodyText);
                        const author =
                            document.querySelector('meta[name="author"]')?.content
                            || document.querySelector('meta[property="og:site_name"]')?.content
                            || '';
                        const tags = Array.from(new Set((article.match(/#[^\\s#]+/g) || []).slice(0, 12)));
                        return {
                            url: window.location.href,
                            title: normalize(title),
                            author: normalize(author),
                            content: article,
                            tags,
                            body_text: normalize(bodyText),
                        };
                    }
                    """
                )
                break
            except Error as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                self.page.wait_for_timeout(500)
        if detail is None:
            raise RuntimeError(f"热门笔记 {note_id} 详情页连续跳转，未能稳定提取内容。")
        body_text = detail.get("body_text", "")
        detail["like_count"] = parse_metric_from_text(body_text, "赞")
        detail["collect_count"] = parse_metric_from_text(body_text, "收藏")
        detail["comment_count"] = parse_metric_from_text(body_text, "评论")
        return detail
