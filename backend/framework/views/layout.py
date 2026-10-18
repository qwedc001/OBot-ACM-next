"""Measured desktop layouts shared by OBot's own renderers, never third-party images."""

from math import log

TARGET_RATIO = 4 / 3
MAX_EDGE = 8192
MAX_PIXELS = 24_000_000


def fits(width: int, height: int) -> bool:
    return 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and width * height <= MAX_PIXELS


def aspect_error(width: int, height: int) -> float:
    """Symmetric error: twice too wide and twice too tall have the same cost."""
    return abs(log(width / height / TARGET_RATIO))


def max_columns(cell: int, gap: int, margin: int) -> int:
    return (MAX_EDGE - margin * 2 + gap) // (cell + gap)
