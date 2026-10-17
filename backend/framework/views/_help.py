"""Native help syntax layout adapted from OBot-ACM render_help.py (AGPL-3.0).

Unlike the old parser, our metadata uses <required> and [optional]. Never infer
permission restrictions from prose: the catalog has already filtered commands.
"""

import re

from easy_pixie import Loc, StyledString, calculate_width, draw_mask_rect, draw_text

_PARAMETER = re.compile(r"(<[^<>\n]+>|\[[^\[\]\n]+\])")


class UsageBlock:
    def __init__(self, usage: str, width: int, accent: tuple):
        self.lines = [[]]
        line_width = 0
        for raw in _PARAMETER.split(usage):
            if not raw:
                continue
            parameter = bool(_PARAMETER.fullmatch(raw))
            required = raw.startswith("<") if parameter else None
            # Ordinary tokens can wrap between words; parameters remain a unit
            # unless wider than the whole column (StyledString then wraps it).
            tokens = [raw[1:-1]] if parameter else re.findall(r"\S+\s*|\s+", raw)
            for token in tokens:
                text = StyledString(
                    token,
                    "H" if parameter else "B",
                    28,
                    font_color=accent if parameter else (255, 255, 255, 180),
                    max_width=width - 16,
                    line_multiplier=1.25,
                )
                span_width = min(width, int(calculate_width(text)) + (16 if parameter else 0))
                if self.lines[-1] and line_width + span_width > width:
                    self.lines.append([])
                    line_width = 0
                self.lines[-1].append((text, span_width, required))
                line_width += span_width
        self.lines = [line for line in self.lines if line]
        self.height = sum(max(text.height for text, _, _ in line) + 14 for line in self.lines)
        self.accent = accent

    def draw(self, image, x, y):
        for line in self.lines:
            height = max(text.height for text, _, _ in line)
            current_x = x
            for text, width, required in line:
                draw_text(image, text, current_x + (8 if required is not None else 0), y)
                if required is not None:
                    length, gap = (width - 4, 0) if required else (8, 7)
                    offset = 0
                    while offset < width - 4:
                        draw_mask_rect(
                            image,
                            Loc(current_x + offset, y + height + 3, min(length, width - 4 - offset), 2),
                            self.accent,
                            1,
                        )
                        offset += length + gap
                current_x += width
            y += height + 14
        return y
