"""OBot help cards adapted for permission models and bounded mobile pages.

Visual design: OBot-ACM src/render/pixie/render_help.py (AGPL-3.0).
Uses easy-pixie's existing OPPOSans fonts; no old Constants/import side effects.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.clients.renderer import PixieBackend, RenderedImage

from .footer import CardFooter
from .palette import DECENT, paint_card

WIDTH = 1108
MAX_HEIGHT = 4096
MARGIN = 96
CONTENT_WIDTH = WIDTH - MARGIN * 2
HEADER_HEIGHT = 300
FOOTER_HEIGHT = 300
MAX_COLUMNS = 3
COLUMN_GAP = 96
# Original help uses a near-black veil and a lightened gradient accent.
ACCENT = (221, 244, 248)
INK = (255, 255, 255, 228)
MUTED = (255, 255, 255, 156)


@dataclass
class HelpCard:
    command_id: str
    blocks: list[Any]
    height: int


@dataclass
class HelpModule:
    category: str
    heading: Any
    cards: list[HelpCard]

    @property
    def height(self) -> int:
        return self.heading.height + 64 + sum(card.height for card in self.cards)


@dataclass
class HelpColumn:
    modules: list[HelpModule]

    @property
    def height(self) -> int:
        return sum(module.height for module in self.modules)


@dataclass
class HelpPage:
    columns: list[HelpColumn]

    @property
    def width(self) -> int:
        return MARGIN * 2 + len(self.columns) * CONTENT_WIDTH + (len(self.columns) - 1) * COLUMN_GAP

    @property
    def height(self) -> int:
        return HEADER_HEIGHT + FOOTER_HEIGHT + max(column.height for column in self.columns)


class HelpPixieBackend:
    """Optional native imports happen inside the serialized render worker."""

    @staticmethod
    def _text(
        text: str,
        size: int,
        *,
        weight: str = "R",
        color: tuple = INK,
        width: int = CONTENT_WIDTH - 48,
    ):
        from easy_pixie import StyledString

        return StyledString(text, weight, size, font_color=color, max_width=width, line_multiplier=1.25)

    def layout(self, model: dict) -> list[HelpPage]:
        from ._help import UsageBlock

        modules: list[HelpModule] = []
        capacity = MAX_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT
        for group in model["groups"]:
            category = f"{group['name']} · {group['summary']}"
            heading = self._text(category, 32, weight="H", color=ACCENT, width=CONTENT_WIDTH - 68)
            cards = []
            for command in group["commands"]:
                prefix = "" if command["mode"] == "keyword" else "/"
                blocks = [
                    self._text(prefix + command["name"], 46, weight="H", color=INK),
                    self._text(command["summary"], 28),
                    UsageBlock(command["usage"], CONTENT_WIDTH - 48, ACCENT),
                ]
                if command["aliases"]:
                    blocks.append(
                        self._text(
                            "别名：" + "、".join(prefix + alias for alias in command["aliases"]),
                            24,
                            color=MUTED,
                        )
                    )
                if command["examples"]:
                    blocks.append(self._text("示例：" + "；".join(command["examples"]), 24, color=MUTED))
                card_height = sum(block.height for block in blocks) + 12 * (len(blocks) - 1) + 32
                if heading.height + 64 + card_height + 32 > capacity:
                    raise ValueError("Help entry exceeds a page; text help remains available")
                cards.append(HelpCard(command["id"], blocks, card_height + 32))
            if cards:
                modules.append(HelpModule(category, heading, cards))
        if not modules:
            return []

        column_count = min(MAX_COLUMNS, len(modules))

        def new_page():
            return HelpPage([HelpColumn([]) for _ in range(column_count)])

        pages = [new_page()]
        # Longest-first packing avoids leaving a tall module until all columns
        # already contain short ones. Ties retain the catalog's module order.
        for module in sorted(modules, key=lambda module: module.height, reverse=True):
            # Keep whole modules together; only over-height modules are split,
            # at card boundaries, with the same measured heading on each part.
            chunks = [HelpModule(module.category, module.heading, [])]
            for card in module.cards:
                if chunks[-1].height + card.height > capacity:
                    chunks.append(HelpModule(module.category, module.heading, []))
                chunks[-1].cards.append(card)
            for chunk in chunks:
                column = min(pages[-1].columns, key=lambda column: column.height)
                if column.height + chunk.height > capacity:
                    pages.append(new_page())
                    column = pages[-1].columns[0]
                column.modules.append(chunk)
        return pages

    def _draw_page(self, model: dict, layout: HelpPage, page: int, total: int):
        import pixie
        from easy_pixie import (
            Loc,
            apply_tint,
            calculate_width,
            draw_img,
            draw_mask_rect,
            draw_text,
        )

        width, height = layout.width, layout.height
        image = pixie.Image(width, height)
        paint_card(image, DECENT, dark=True)
        icon = pixie.read_image(str(Path(__file__).with_name("assets") / "Help.png"))
        draw_img(image, apply_tint(icon, ACCENT), Loc(MARGIN, 100, 86, 86))
        draw_text(image, self._text("指令帮助", 78, weight="H", color=ACCENT), MARGIN + 112, 90)
        draw_text(
            image,
            self._text(f"{model['title']} · {model['count']} 条可用指令", 26, color=MUTED),
            MARGIN,
            203,
        )
        for index, column in enumerate(layout.columns):
            x = MARGIN + index * (CONTENT_WIDTH + COLUMN_GAP)
            y = HEADER_HEIGHT
            for module in column.modules:
                chip_width = min(CONTENT_WIDTH, int(calculate_width(module.heading)) + 68)
                chip_height = module.heading.height + 18
                draw_mask_rect(image, Loc(x, y, chip_width, chip_height), (*ACCENT, 30), chip_height // 2)
                draw_text(image, module.heading, x + 34, y + 9)
                remaining = CONTENT_WIDTH - chip_width - 20
                if remaining > 0:
                    draw_mask_rect(
                        image, Loc(x + chip_width + 20, y + chip_height // 2, remaining, 1), (*ACCENT, 80)
                    )
                y += module.heading.height + 64
                for card in module.cards:
                    current = y + 16
                    for block in card.blocks:
                        draw = getattr(block, "draw", None)
                        current = (
                            draw(image, x, current) if draw else draw_text(image, block, x, current)
                        ) + 12
                    y += card.height
        footer_y = height - FOOTER_HEIGHT + 12
        draw_text(image, self._text("参数：<必填> 实线 · [选填] 虚线", 24, color=MUTED), MARGIN, footer_y)
        footer = CardFooter(
            width - MARGIN * 2,
            dark=True,
            detail="配色 " + DECENT.label + (f" · 第 {page}/{total} 页" if total > 1 else ""),
        )
        footer.draw(image, MARGIN, footer_y + 64)
        return image

    def render_pages(self, model: dict) -> tuple[RenderedImage, ...]:
        pages = self.layout(model)
        return tuple(
            PixieBackend(
                lambda _model, layout=layout, index=index: self._draw_page(model, layout, index, len(pages))
            ).render(model)
            for index, layout in enumerate(pages, 1)
        )

    def render(self, model: dict) -> RenderedImage:
        pages = self.render_pages(model)
        if len(pages) != 1:
            raise ValueError("Use render_pages for a help book with multiple pages")
        return pages[0]
