from __future__ import annotations

import re
from collections import Counter

from .browser import consumer_context, get_alive_page
from .config import Settings
from .consumer import ConsumerNotePage, ConsumerSearchPage
from .models import HotNote, HotNotesAnalysis, ProductSummary

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]",
    re.UNICODE,
)
SCENE_KEYWORDS = ["通勤", "上课", "约会", "出游", "逛街", "通勤日常", "日常出门"]
TONE_KEYWORDS = ["温柔", "精致", "氛围感", "轻复古", "可爱", "高级感", "韩系", "百搭"]


def infer_search_keyword(products: list[ProductSummary]) -> str:
    corpus = " ".join(product.name for product in products)
    for keyword in ("抓夹", "发夹", "鲨鱼夹", "发饰", "头饰"):
        if keyword in corpus:
            return keyword
    return "发饰"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _extract_emojis(texts: list[str], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for emoji in EMOJI_RE.findall(text):
            counter[emoji] += 1
    return [item for item, _ in counter.most_common(limit)]


def _pick_tag_candidates(notes: list[HotNote], keyword: str, limit: int = 6) -> list[str]:
    counter: Counter[str] = Counter()
    for note in notes:
        counter.update(tag for tag in note.tags if 2 <= len(tag) <= 18)

    tags = [tag for tag, _ in counter.most_common(limit)]
    if f"#{keyword}" not in tags:
        tags.insert(0, f"#{keyword}")
    return tags[:limit]


def _pick_scene_candidates(notes: list[HotNote], limit: int = 5) -> list[str]:
    text = " ".join(f"{note.title} {note.content}" for note in notes)
    scenes = [scene for scene in SCENE_KEYWORDS if scene in text]
    return scenes[:limit] or ["通勤", "约会", "日常出门"]


def _pick_tone_keywords(notes: list[HotNote], limit: int = 5) -> list[str]:
    text = " ".join(f"{note.title} {note.content}" for note in notes)
    tones = [tone for tone in TONE_KEYWORDS if tone in text]
    return tones[:limit] or ["温柔", "精致", "氛围感"]


def _infer_title_patterns(notes: list[HotNote], keyword: str) -> list[str]:
    counter: Counter[str] = Counter()
    for note in notes:
        title = note.title
        if not title:
            continue
        if EMOJI_RE.match(title):
            counter["emoji 开头 + 情绪表达"] += 1
        if "也太" in title or "绝了" in title:
            counter["夸张感叹式标题"] += 1
        if "终于" in title or "找到" in title:
            counter["发现/种草式标题"] += 1
        if "被问爆" in title or "问爆" in title:
            counter["被问爆/高频安利式"] += 1
        if keyword in title:
            counter[f"关键词直出：{keyword}"] += 1
        if len(title) <= 14:
            counter["短标题 + 强情绪词"] += 1

    patterns = [item for item, _ in counter.most_common(5)]
    return patterns or [
        "emoji 开头 + 情绪表达",
        f"关键词直出：{keyword}",
        "短标题 + 强情绪词",
    ]


def _infer_content_patterns(notes: list[HotNote]) -> list[str]:
    counter: Counter[str] = Counter()
    for note in notes:
        content = note.content
        if not content:
            continue
        paragraph_count = len([part for part in content.split("\n") if part.strip()])
        if paragraph_count >= 3:
            counter["多段短句 + 留白换行"] += 1
        if any(scene in content for scene in SCENE_KEYWORDS):
            counter["场景代入 + 日常口吻"] += 1
        if any(tag in content for tag in note.tags):
            counter["正文结尾带标签或话题"] += 1
        if any(word in content for word in ("我觉得", "我最近", "我发现", "我真的")):
            counter["第一人称体验式表达"] += 1

    patterns = [item for item, _ in counter.most_common(4)]
    return patterns or [
        "第一人称体验式表达",
        "多段短句 + 留白换行",
        "场景代入 + 日常口吻",
    ]


def collect_hot_notes(
    keyword: str,
    settings: Settings,
    *,
    headless: bool,
    search_limit: int = 20,
    detail_limit: int = 8,
) -> list[HotNote]:
    with consumer_context(settings, headless=headless) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page = get_alive_page(context, page)
        search_page = ConsumerSearchPage(page, settings)
        search_page.open_search(keyword)
        cards = search_page.collect_note_cards(limit=search_limit)
        if not cards:
            raise RuntimeError(f"未在用户端搜索页采集到“{keyword}”相关笔记卡片。")

        note_page = ConsumerNotePage(page, settings)
        notes: list[HotNote] = []
        valid_count = 0
        for card in cards[:search_limit]:
            note_id = card.get("note_id")
            if not note_id:
                continue
            detail = note_page.extract_detail(note_id, url=card.get("url"))
            note = HotNote(
                note_id=note_id,
                title=_normalize_text(detail.get("title") or card.get("text") or ""),
                url=detail.get("url") or card.get("url") or "",
                author=_normalize_text(detail.get("author") or ""),
                cover_url=card.get("cover_url") or "",
                like_count=detail.get("like_count"),
                collect_count=detail.get("collect_count"),
                comment_count=detail.get("comment_count"),
                tags=detail.get("tags") or [],
                content=_normalize_text(detail.get("content") or card.get("text") or ""),
            )
            notes.append(note)
            if is_valid_hot_note(note):
                valid_count += 1
                if valid_count >= detail_limit:
                    break
            page.wait_for_timeout(800)

        if not notes:
            raise RuntimeError(f"搜索到了“{keyword}”卡片，但未能成功解析任何热门笔记详情。")
        return notes


def is_valid_hot_note(note: HotNote) -> bool:
    url = (note.url or "").lower()
    if "/website-login/error" in url or "/404?" in url:
        return False
    text = f"{note.title} {note.content}".strip()
    blocked_markers = (
        "安全限制",
        "IP存在风险",
        "当前笔记暂时无法浏览",
        "请打开小红书App扫码查看",
        "小红书 - 你访问的页面不见了",
        "返回首页",
    )
    return bool(text) and not any(marker in text for marker in blocked_markers)


def filter_valid_hot_notes(notes: list[HotNote]) -> list[HotNote]:
    return [note for note in notes if is_valid_hot_note(note)]


def analyze_hot_notes(keyword: str, notes: list[HotNote]) -> HotNotesAnalysis:
    texts = [note.title for note in notes] + [note.content for note in notes]
    emojis = _extract_emojis(texts)
    return HotNotesAnalysis(
        keyword=keyword,
        source="consumer_search",
        total_collected=len(notes),
        title_patterns=_infer_title_patterns(notes, keyword),
        content_patterns=_infer_content_patterns(notes),
        tag_candidates=_pick_tag_candidates(notes, keyword),
        emoji_candidates=emojis or ["🎀", "✨", "💖"],
        scene_candidates=_pick_scene_candidates(notes),
        tone_keywords=_pick_tone_keywords(notes),
        notes=notes,
    )


def collect_and_analyze_hot_notes(
    keyword: str,
    settings: Settings,
    *,
    headless: bool,
    search_limit: int = 20,
    detail_limit: int = 8,
) -> HotNotesAnalysis:
    notes = collect_hot_notes(
        keyword,
        settings,
        headless=headless,
        search_limit=search_limit,
        detail_limit=detail_limit,
    )
    valid_notes = filter_valid_hot_notes(notes)
    if not valid_notes:
        fallback = build_fallback_hot_notes_analysis(keyword)
        fallback.source = "local_fallback_risk"
        return fallback
    return analyze_hot_notes(keyword, valid_notes)


def build_fallback_hot_notes_analysis(keyword: str) -> HotNotesAnalysis:
    return HotNotesAnalysis(
        keyword=keyword,
        source="local_fallback",
        total_collected=0,
        title_patterns=[
            "emoji + 这个{keyword}也太温柔了叭",
            "emoji + 一分钟搞定氛围感发型",
            "emoji + 终于找到顺手又好看的{keyword}了",
        ],
        content_patterns=[
            "第一眼感受 -> 颜色/细节 -> 日常场景",
            "个人体验 -> 风格氛围 -> 搭配建议",
            "轻情绪表达 -> 图片事实 -> 自然收尾",
        ],
        tag_candidates=[
            f"#{keyword}",
            "#发夹推荐",
            "#发饰分享",
            "#头饰发饰",
            "#日常发型",
            "#鲨鱼夹",
        ],
        emoji_candidates=["🎀", "✨", "💖", "🤍"],
        scene_candidates=["通勤", "上课", "约会", "逛街", "日常出门"],
        tone_keywords=["温柔", "精致", "轻复古", "氛围感", "日常好搭"],
    )
