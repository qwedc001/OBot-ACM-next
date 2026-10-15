"""OBot help cards adapted for permission models and bounded mobile pages.

Visual design: OBot-ACM src/render/pixie/render_help.py (AGPL-3.0).
Uses easy-pixie's existing OPPOSans fonts; no old Constants/import side effects.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.clients.renderer import PixieBackend, RenderedImage

from .. import __version__

WIDTH = 1040
MAX_HEIGHT = 4096
MARGIN = 60
CONTENT_WIDTH = WIDTH - MARGIN * 2
HEADER_HEIGHT = 282
FOOTER_HEIGHT = 110
MAX_COLUMNS = 3
COLUMN_GAP = 32
ACCENT = (146, 220, 237)


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
        return self.heading.height + 38 + sum(card.height for card in self.cards)


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
        color: tuple = (238, 243, 249),
        width: int = CONTENT_WIDTH - 48,
    ):
        from easy_pixie import StyledString

        return StyledString(text, weight, size, font_color=color, max_width=width, line_multiplier=1.25)

    def layout(self, model: dict) -> list[HelpPage]:
        modules: list[HelpModule] = []
        capacity = MAX_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT
        for group in model["groups"]:
            category = f"{group['name']} · {group['summary']}"
            heading = self._text(category, 24, weight="B", color=ACCENT, width=CONTENT_WIDTH - 32)
            cards = []
            for command in group["commands"]:
                prefix = "" if command["mode"] == "keyword" else "/"
                blocks = [
                    self._text(prefix + command["name"], 34, weight="H", color=ACCENT),
                    self._text(command["summary"], 26),
                    self._text("用法：" + command["usage"], 24, color=(201, 212, 228)),
                ]
                if command["aliases"]:
                    blocks.append(
                        self._text(
                            "别名：" + "、".join(prefix + alias for alias in command["aliases"]),
                            22,
                            color=(173, 191, 211),
                        )
                    )
                if command["examples"]:
                    blocks.append(self._text("示例：" + "；".join(command["examples"]), 24, color=ACCENT))
                card_height = sum(block.height for block in blocks) + 14 * (len(blocks) - 1) + 48
                if heading.height + 38 + card_height + 24 > capacity:
                    raise ValueError("Help entry exceeds a page; text help remains available")
                cards.append(HelpCard(command["id"], blocks, card_height + 24))
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
            GradientColor,
            GradientDirection,
            Loc,
            apply_tint,
            draw_gradient_rect,
            draw_img,
            draw_mask_rect,
            draw_text,
        )

        width, height = layout.width, layout.height
        image = pixie.Image(width, height)
        image.fill(pixie.parse_color("#0D1220"))
        gradient = GradientColor(color_list=["#274B77", "#297065"], pos_list=[0.0, 1.0], name="Lowlight")
        draw_gradient_rect(
            image,
            Loc(18, 18, width - 36, height - 36),
            gradient,
            GradientDirection.DIAGONAL_LEFT_TO_RIGHT,
            34,
        )
        draw_mask_rect(image, Loc(24, 24, width - 48, height - 48), (9, 13, 23, 231), 30)
        icon = pixie.read_image(str(Path(__file__).with_name("assets") / "Help.png"))
        draw_img(image, apply_tint(icon, ACCENT), Loc(MARGIN, 64, 62, 62))
        draw_text(image, self._text("指令帮助", 54, weight="H", color=ACCENT), MARGIN + 82, 66)
        draw_text(
            image,
            self._text(f"{model['title']} · {model['count']} 条可用指令", 24, color=(173, 191, 211)),
            MARGIN,
            147,
        )
        draw_text(
            image,
            self._text("按模块查找功能 · 示例中的短别名可直接使用", 22, color=(173, 191, 211)),
            MARGIN,
            195,
        )
        for index, column in enumerate(layout.columns):
            x = MARGIN + index * (CONTENT_WIDTH + COLUMN_GAP)
            y = HEADER_HEIGHT
            for module in column.modules:
                draw_mask_rect(
                    image, Loc(x, y, CONTENT_WIDTH, module.heading.height + 18), (146, 220, 237, 28), 12
                )
                draw_text(image, module.heading, x + 16, y + 9)
                y += module.heading.height + 38
                for card in module.cards:
                    draw_mask_rect(image, Loc(x, y, CONTENT_WIDTH, card.height - 24), (255, 255, 255, 8), 16)
                    current = y + 24
                    for block in card.blocks:
                        current = draw_text(image, block, x + 24, current) + 14
                    y += card.height
        draw_text(
            image,
            self._text(f"Lowlight · OBot {__version__}      {page}/{total}", 21, color=(144, 161, 185)),
            MARGIN,
            height - 82,
        )
        draw_text(
            image,
            self._text("依据当前用户与对话场景的权限实时生成", 21, color=(144, 161, 185)),
            MARGIN,
            height - 51,
        )
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
