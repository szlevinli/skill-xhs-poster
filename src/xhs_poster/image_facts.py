from __future__ import annotations

from collections import Counter
from pathlib import Path

from PIL import Image

from .models import ImageFact, ProductImageFacts, ProductSummary, TodayPool


KNOWN_KEYWORDS = [
    "抓夹",
    "发夹",
    "鲨鱼夹",
    "发饰",
    "头饰",
    "刘海夹",
]

STYLE_TOKENS = [
    "韩系",
    "复古",
    "清新",
    "高级感",
    "简约",
    "法式",
    "港风",
    "甜美",
    "温柔",
    "日系",
    "ins",
]

ELEMENT_TOKENS = [
    "碎花",
    "格纹",
    "珍珠",
    "蝴蝶结",
    "花朵",
    "爱心",
    "透明",
    "磨砂",
]


def _normalize_color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    if max(rgb) - min(rgb) < 18:
        if r >= 210:
            return "米白色"
        if r >= 150:
            return "灰棕色"
        return "深灰色"
    if r > 180 and g > 165 and b < 120:
        return "金色"
    if r > 190 and g > 120 and b > 120:
        return "粉色"
    if r > 165 and g > 85 and b < 90:
        return "红色"
    if r > 130 and 70 <= g <= 125 and b < 90:
        return "棕色"
    if r > 120 and g > 120 and b > 160:
        return "雾霾蓝"
    if b > 150 and r < 140:
        return "蓝色"
    if g > 140 and r < 150:
        return "绿色"
    if r > 160 and g > 160 and b > 160:
        return "浅色"
    return "综合色"


def _extract_palette(image: Image.Image, limit: int = 3) -> list[str]:
    reduced = image.convert("RGB").resize((96, 96)).quantize(colors=limit)
    palette = reduced.getpalette() or []
    colors = []
    counter = Counter(dict(reduced.getcolors() or []))
    for _, color_index in counter.most_common(limit):
        base = color_index * 3
        rgb = tuple(palette[base : base + 3])
        if len(rgb) == 3:
            colors.append(_normalize_color_name(rgb))

    unique = []
    for color in colors:
        if color not in unique:
            unique.append(color)
    return unique


def _brightness_label(image: Image.Image) -> str:
    rgb = image.convert("RGB").resize((64, 64))
    width, height = rgb.size
    total = 0.0
    for y in range(height):
        for x in range(width):
            pixel = rgb.getpixel((x, y))
            if not isinstance(pixel, tuple) or len(pixel) < 3:
                continue
            r, g, b = pixel[:3]
            total += (r + g + b) / 3
    avg = total / max(1, width * height)
    if avg >= 190:
        return "明亮"
    if avg >= 120:
        return "柔和"
    return "偏深"


def _extract_tokens(text: str, candidates: list[str]) -> list[str]:
    result = []
    for token in candidates:
        if token in text and token not in result:
            result.append(token)
    return result


def extract_product_image_facts(
    product: ProductSummary,
    image_paths: list[str],
) -> ProductImageFacts:
    colors: list[str] = []
    image_facts: list[ImageFact] = []

    for path_str in image_paths:
        path = Path(path_str)
        with Image.open(path) as image:
            palette = _extract_palette(image)
            brightness = _brightness_label(image)
            visual_elements = _extract_tokens(product.name, ELEMENT_TOKENS)
            image_facts.append(
                ImageFact(
                    path=str(path),
                    width=image.width,
                    height=image.height,
                    dominant_colors=palette,
                    brightness=brightness,
                    visual_elements=visual_elements,
                )
            )
            for color in palette:
                if color not in colors:
                    colors.append(color)

    return ProductImageFacts(
        product_id=product.id,
        product_name=product.name,
        keywords=_extract_tokens(product.name, KNOWN_KEYWORDS),
        colors=colors[:3],
        style_keywords=_extract_tokens(product.name, STYLE_TOKENS),
        confirmed_elements=_extract_tokens(product.name, ELEMENT_TOKENS),
        images=image_facts,
    )


def build_image_facts(today_pool: TodayPool) -> list[ProductImageFacts]:
    facts: list[ProductImageFacts] = []
    for product in today_pool.products:
        image_paths = [
            asset.path for asset in today_pool.image_assets.get(product.id, [])
        ] or today_pool.images.get(product.id, [])
        if not image_paths:
            continue
        facts.append(extract_product_image_facts(product, image_paths))
    return facts
