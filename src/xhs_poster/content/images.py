from __future__ import annotations


def _unique_paths(image_paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for path in image_paths:
        if not path or path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def allocate_image_paths(
    image_paths: list[str],
    draft_count: int | None = None,
    *,
    contents_per_product: int | None = None,
    preferred_min_per_draft: int = 3,
    preferred_max_per_draft: int = 5,
    hard_max_per_draft: int = 9,
) -> list[list[str]]:
    draft_count = draft_count if draft_count is not None else (contents_per_product or 0)
    if draft_count <= 0:
        return []

    unique_paths = _unique_paths(image_paths)
    allocations: list[list[str]] = [[] for _ in range(draft_count)]
    if not unique_paths:
        return allocations

    base = max(1, min(preferred_max_per_draft, len(unique_paths) // draft_count))
    cursor = 0

    for _ in range(base):
        progressed = False
        for bucket in allocations:
            if cursor >= len(unique_paths):
                break
            bucket.append(unique_paths[cursor])
            cursor += 1
            progressed = True
        if not progressed:
            break

    def distribute_until(cap: int) -> None:
        nonlocal cursor
        while cursor < len(unique_paths):
            progressed = False
            for bucket in allocations:
                if len(bucket) >= cap or cursor >= len(unique_paths):
                    continue
                bucket.append(unique_paths[cursor])
                cursor += 1
                progressed = True
            if not progressed:
                break

    distribute_until(max(preferred_min_per_draft, preferred_max_per_draft))
    distribute_until(hard_max_per_draft)

    if any(not bucket for bucket in allocations):
        for index, bucket in enumerate(allocations):
            if bucket:
                continue
            bucket.append(unique_paths[index % len(unique_paths)])

    return allocations
