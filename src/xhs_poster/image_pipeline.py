from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from .models import DownloadedImage


@dataclass(slots=True)
class ImageCandidate:
    source_url: str
    normalized_url: str
    source_type: Literal["main", "detail", "unknown"]
    source_priority: int
    position: int


_DROP_QUERY_KEYS = {
    "imageView2",
    "imageMogr2",
    "x-oss-process",
    "x-image-process",
    "x-khb-process",
    "x-kh-process",
}
_DROP_QUERY_MARKERS = ("image", "x-oss-", "x-image-", "x-kh-")


def normalize_image_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"

    split = urlsplit(raw)
    path = re.sub(r"(@!|!)[^/?#]+$", "", split.path)
    query = split.query
    if query and (
        any(key in query for key in _DROP_QUERY_KEYS)
        or any(marker in query for marker in _DROP_QUERY_MARKERS)
    ):
        query = ""
    return urlunsplit((split.scheme, split.netloc, path, query, ""))


def build_image_id(product_id: str, normalized_url: str, source_type: str, position: int) -> str:
    raw = f"{product_id}|{source_type}|{position}|{normalized_url}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def dedupe_candidates(candidates: Iterable[ImageCandidate]) -> list[ImageCandidate]:
    best_by_url: dict[str, ImageCandidate] = {}
    ordered_urls: list[str] = []

    for candidate in candidates:
        key = candidate.normalized_url or candidate.source_url
        if not key:
            continue
        existing = best_by_url.get(key)
        if existing is None:
            best_by_url[key] = candidate
            ordered_urls.append(key)
            continue
        if (candidate.source_priority, candidate.position) < (existing.source_priority, existing.position):
            best_by_url[key] = candidate

    return [best_by_url[key] for key in ordered_urls]


def dedupe_downloaded_images(downloaded_images: Iterable[DownloadedImage]) -> list[DownloadedImage]:
    best_by_sha: dict[str, DownloadedImage] = {}
    ordered_shas: list[str] = []
    discarded_paths: set[str] = set()

    for image in downloaded_images:
        key = image.sha256 or image.normalized_url or image.path
        existing = best_by_sha.get(key)
        if existing is None:
            best_by_sha[key] = image
            ordered_shas.append(key)
            continue
        if (image.source_priority, image.position, image.index) < (
            existing.source_priority,
            existing.position,
            existing.index,
        ):
            if existing.path and existing.path != image.path:
                discarded_paths.add(existing.path)
            best_by_sha[key] = image
        elif image.path and image.path != existing.path:
            discarded_paths.add(image.path)

    deduped: list[DownloadedImage] = []
    for index, key in enumerate(ordered_shas, start=1):
        item = best_by_sha[key]
        if item.index != index:
            item = item.model_copy(update={"index": index})
        deduped.append(item)

    for discarded_path in discarded_paths:
        path = Path(discarded_path)
        if path.exists():
            path.unlink()
    return deduped
