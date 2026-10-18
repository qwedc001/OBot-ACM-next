"""Validate external PNGs and only resize when transport limits require it.

Third-party images retain their own layout and credits. Within limits, preserve
the original bytes; do not add an OBot footer or re-encode the image.
"""

import math
import shutil
import sys
from pathlib import Path

MAX_RENDER_PIXELS = 64_000_000
MAX_OUTPUT_PIXELS = 24_000_000
MAX_OUTPUT_EDGE = 8192


def prepare_image(source: Path, destination: Path):
    from PIL import Image

    with Image.open(source) as png:
        width, height = png.size
        if png.format != "PNG" or width <= 0 or height <= 0 or width * height > MAX_RENDER_PIXELS:
            raise ValueError("Invalid or oversized image")
        png.verify()
    scale = min(1, MAX_OUTPUT_EDGE / max(width, height), math.sqrt(MAX_OUTPUT_PIXELS / (width * height)))
    if scale == 1:
        shutil.copyfile(source, destination)
    else:
        with Image.open(source) as png:
            result = png.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS
            )
            result.save(destination, format="PNG")


if __name__ == "__main__":
    prepare_image(Path(sys.argv[1]), Path(sys.argv[2]))
