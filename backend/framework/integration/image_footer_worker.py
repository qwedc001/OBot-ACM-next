"""Append OBot information to a completed PNG in an isolated native process.

Only the PNG boundary is used: upstream layout, credits and pixels stay intact.
No third-party drawing classes, private fields, fonts or clocks are patched.
"""

import math
import sys
from pathlib import Path

MAX_RENDER_PIXELS = 64_000_000
MAX_OUTPUT_PIXELS = 24_000_000
MAX_OUTPUT_EDGE = 8192


def append_footer(source: Path, destination: Path):
    from PIL import Image

    from ..views.footer import CardFooter

    with Image.open(source) as png:
        width, height = png.size
        if png.format != "PNG" or width < 128 or width * height > MAX_RENDER_PIXELS:
            raise ValueError("Invalid or oversized image")
        png.verify()
    import pixie

    footer = CardFooter(width - 128)
    if width * (height + footer.height + 96) > MAX_RENDER_PIXELS:
        raise ValueError("Oversized image")
    result = pixie.Image(width, height + footer.height + 96)
    result.fill(pixie.parse_color("#f4f6f8"))
    result.draw(pixie.read_image(str(source)))
    footer.draw(result, 64, height + 32)
    scale = min(
        1,
        MAX_OUTPUT_EDGE / max(result.width, result.height),
        math.sqrt(MAX_OUTPUT_PIXELS / (result.width * result.height)),
    )
    if scale < 1:
        result = result.resize(max(1, int(result.width * scale)), max(1, int(result.height * scale)))
    result.write_file(str(destination))


if __name__ == "__main__":
    append_footer(Path(sys.argv[1]), Path(sys.argv[2]))
