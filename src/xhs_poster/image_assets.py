from __future__ import annotations

from pathlib import Path

from PIL import Image

from .models import ProductImageAsset


IMAGE_FILE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def build_local_asset(path_str: str, *, index: int) -> ProductImageAsset:
    path = Path(path_str)
    width = 0
    height = 0
    image_format = path.suffix.lower().lstrip(".")
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = (image.format or image_format or "jpeg").lower()
    except Exception:
        pass
    return ProductImageAsset(
        image_id=f"local:{path.name}:{index}",
        path=str(path),
        format=image_format,
        width=width,
        height=height,
        bytes=path.stat().st_size if path.exists() else 0,
        position=index,
        source_type="unknown",
        source_priority=99,
    )


def build_local_assets(paths: list[str]) -> list[ProductImageAsset]:
    return [build_local_asset(path, index=index) for index, path in enumerate(paths, start=1)]
