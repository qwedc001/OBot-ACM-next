"""Raster allocation limits shared by OBot-owned views."""

MAX_EDGE = 8192
MAX_PIXELS = 24_000_000


def fits(width: int, height: int) -> bool:
    return 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE and width * height <= MAX_PIXELS
