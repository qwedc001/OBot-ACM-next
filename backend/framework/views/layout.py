"""Ordered column balancing from OBot-ACM's RenderableSection._split_columns.

Views choose the column count; balancing preserves order without a ratio target.
"""

from collections.abc import Callable, Sequence

MAX_EDGE = 8192
MAX_PIXELS = 24_000_000


def fits(width: int, height: int) -> bool:
    return 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and width * height <= MAX_PIXELS


def split_columns[T](
    items: Sequence[T], count: int, height: Callable[[T], int], gap: int = 0
) -> list[list[T]]:
    """Partition into at most count contiguous columns with minimum max height."""
    if not items:
        return []
    count = max(1, min(count, len(items)))
    heights = [height(item) for item in items]

    def pack(limit):
        columns, used = [[]], 0
        for item, size in zip(items, heights, strict=True):
            needed = size + (gap if columns[-1] else 0)
            if columns[-1] and used + needed > limit:
                columns.append([])
                used, needed = 0, size
            columns[-1].append(item)
            used += needed
        return columns

    low, high = max(heights), sum(heights) + gap * (len(items) - 1)
    while low < high:
        middle = (low + high) // 2
        if len(pack(middle)) <= count:
            high = middle
        else:
            low = middle + 1
    return pack(high)
