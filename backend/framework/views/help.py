"""OBot help cards with measured columns and overflow-only pagination.

Visual design: OBot-ACM src/render/pixie/render_help.py (AGPL-3.0).
Uses easy-pixie's existing OPPOSans fonts; no old Constants/import side effects.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.clients.renderer import PixieBackend, RenderedImage

from .footer import CardFooter
from .layout import MAX_PIXELS, split_columns
from .palette import DECENT, paint_card

WIDTH = 1108
MAX_HEIGHT = 5120
MARGIN = 96
CONTENT_WIDTH = WIDTH - MARGIN * 2
HEADER_HEIGHT = 300
FOOTER_HEIGHT = 300
COLUMN_GAP = 96
MAX_COLUMNS = 4
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
    column_width: int = CONTENT_WIDTH

    @property
    def width(self) -> int:
        return MARGIN * 2 + len(self.columns) * self.column_width + (len(self.columns) - 1) * COLUMN_GAP

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
        return self._layout(model, CONTENT_WIDTH)

    def _layout(self, model: dict, column_width: int) -> list[HelpPage]:
        from functools import partial

        from ._help import UsageBlock

        text = partial(self._text, width=column_width - 48)
        modules: list[HelpModule] = []
        capacity = MAX_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT
        for group in model["groups"]:
            category = f"{group['name']} · {group['summary']}"
            heading = text(category, 32, weight="H", color=ACCENT, width=column_width - 68)
            cards = []
            for command in group["commands"]:
                prefix = "" if command["mode"] == "keyword" else "/"
                blocks = [
                    text(prefix + command["name"], 46, weight="H", color=INK),
                    text(command["summary"], 28),
                    UsageBlock(command["usage"], column_width - 48, ACCENT),
                ]
                if command["aliases"]:
                    blocks.append(
                        text(
                            "别名：" + "、".join(prefix + alias for alias in command["aliases"]),
                            24,
                            color=MUTED,
                        )
                    )
                if command["examples"]:
                    blocks.append(text("示例：" + "；".join(command["examples"]), 24, color=MUTED))
                card_height = sum(block.height for block in blocks) + 12 * (len(blocks) - 1) + 32
                if heading.height + 64 + card_height + 32 > capacity:
                    raise ValueError("Help entry exceeds a page; text help remains available")
                cards.append(HelpCard(command["id"], blocks, card_height + 32))
            if cards:
                modules.append(HelpModule(category, heading, cards))
        if not modules:
            return []

        # Original help: at most four columns of whole categories, in catalog
        # order. Split a category only when it exceeds the image safety budget.
        width = MARGIN * 2 + MAX_COLUMNS * column_width + (MAX_COLUMNS - 1) * COLUMN_GAP
        capacity = min(MAX_HEIGHT, MAX_PIXELS // width) - HEADER_HEIGHT - FOOTER_HEIGHT
        chunks = []
        for module in modules:
            chunk = HelpModule(module.category, module.heading, [])
            for card in module.cards:
                if chunk.height + card.height > capacity:
                    if not chunk.cards:
                        raise ValueError("Help entry exceeds image size limit; text help remains available")
                    chunks.append(chunk)
                    chunk = HelpModule(module.category, module.heading, [])
                chunk.cards.append(card)
            chunks.append(chunk)

        pages, pending = [], []
        for chunk in chunks:
            columns = split_columns([*pending, chunk], MAX_COLUMNS, lambda module: module.height)
            if pending and max(sum(m.height for m in column) for column in columns) > capacity:
                pages.append(self._page(pending, column_width))
                pending = []
            pending.append(chunk)
        if pending:
            pages.append(self._page(pending, column_width))
        return pages

    @staticmethod
    def _page(modules, column_width):
        columns = split_columns(modules, MAX_COLUMNS, lambda module: module.height)
        return HelpPage([HelpColumn(column) for column in columns], column_width)

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
            x = MARGIN + index * (layout.column_width + COLUMN_GAP)
            y = HEADER_HEIGHT
            for module in column.modules:
                chip_width = min(layout.column_width, int(calculate_width(module.heading)) + 68)
                chip_height = module.heading.height + 18
                draw_mask_rect(image, Loc(x, y, chip_width, chip_height), (*ACCENT, 30), chip_height // 2)
                draw_text(image, module.heading, x + 34, y + 9)
                remaining = layout.column_width - chip_width - 20
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
