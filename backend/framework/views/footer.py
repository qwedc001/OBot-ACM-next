"""Shared information area for every generated OBot image.

Drawing imports stay inside the render worker. Content is application metadata,
never deployment URLs, user identifiers, timestamps or runtime configuration.
"""

from framework import __version__

PROJECT_URL = "https://github.com/qwedc001/OBot-ACM-next"
COPYRIGHT = "© OBot-Next-Dev-Team"


def information_lines(detail: str = "") -> tuple[str, ...]:
    return (
        f"OBot's Next ACM Bot · v{__version__}",
        PROJECT_URL,
        COPYRIGHT + (" · " + detail if detail else ""),
    )


class CardFooter:
    """Measured three-line footer with a divider; light and dark ink variants.

    Width is the renderer's content width, not the whole canvas. Size and gaps
    are shared across renderers; height includes wrapping at narrow widths.
    """

    def __init__(self, width: int, *, detail: str = "", dark: bool = False):
        from easy_pixie import StyledString

        self.width = width
        self.ink = (255, 255, 255, 156) if dark else (0, 0, 0, 136)
        self.line = (255, 255, 255, 52) if dark else (0, 0, 0, 36)
        self.blocks = [
            StyledString(
                text,
                "B" if index == 0 else "M",
                26 if index == 0 else 22,
                font_color=self.ink,
                max_width=width,
                line_multiplier=1.25,
            )
            for index, text in enumerate(information_lines(detail))
        ]
        self.height = 28 + sum(block.height for block in self.blocks) + 10 * (len(self.blocks) - 1)

    def draw(self, image, x: int, y: int) -> int:
        from easy_pixie import Loc, draw_mask_rect, draw_text

        draw_mask_rect(image, Loc(x, y, self.width, 1), self.line)
        current = y + 28
        for block in self.blocks:
            current = draw_text(image, block, x, current) + 10
        return y + self.height
