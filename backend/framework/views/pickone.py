"""Measured PickOne category and media contact sheets, inspired by OBot-ACM.

Uses existing render workers, Dashboard's category palette and the shared footer.
Original media are sent unchanged; only these generated sheets receive a footer.
"""

import io
import math
import tempfile
from pathlib import Path

from ..clients.renderer import PixieBackend
from ..curation.pickone.palette import accent_of
from .footer import CardFooter
from .layout import fits
from .palette import DECENT, paint_card


class PickOneBackend:
    @staticmethod
    def layout(model):
        count = len(model["items"])
        cell_height = 450 if model.get("preview") else 170
        # Original PickOne uses a four-column, row-major contact sheet.
        columns = 4
        width = 128 + columns * 524 + (columns - 1) * 24
        footer = CardFooter(width - 128, detail="配色 " + DECENT.label)
        bottom = 215 + math.ceil(count / columns) * (cell_height + 24)
        height = bottom + footer.height + 80
        if not fits(width, height):
            raise ValueError("PickOne sheet exceeds image size limit")
        return width, height, columns, cell_height, footer, bottom

    def draw(self, model):
        import pixie
        from easy_pixie import Loc, StyledString, draw_img, draw_mask_rect, draw_text
        from PIL import Image

        preview = model.get("preview", False)
        items = model["items"]
        width, height, columns, cell_height, footer, bottom = self.layout(model)
        image = pixie.Image(width, height)
        paint_card(image, DECENT)

        def text(value, x, y, size=26, color=(30, 35, 40), width=484):
            # Fixed cells use one measured line; long user-defined labels cannot overlap statistics.
            value = value.replace("\n", " ")
            block = StyledString(value, "B", size, font_color=color, max_width=width)
            while block.height > size * 1.6 and len(value) > 1:
                value = value[:-1]
                block = StyledString(value + "…", "B", size, font_color=color, max_width=width)
            return draw_text(image, block, x, y)

        text(model["title"][:32], 64, 64, 48, width=width - 128)
        text(model["hint"], 64, 135, 23, width=width - 128)
        with tempfile.TemporaryDirectory(prefix="obot-pickone-render-") as directory:
            for index, item in enumerate(items):
                x, y = 64 + (index % columns) * 548, 210 + (index // columns) * (cell_height + 24)
                draw_mask_rect(image, Loc(x, y, 524, cell_height), (255, 255, 255, 180), 18)
                if preview:
                    if item["thumbnail"]:
                        path = Path(directory) / f"{index}.png"
                        with Image.open(io.BytesIO(item["thumbnail"])) as source:
                            source.save(path, "PNG")
                        media = pixie.read_image(str(path))
                        ratio = min(484 / media.width, 320 / media.height, 2.5)
                        width, height = round(media.width * ratio), round(media.height * ratio)
                        draw_img(
                            image,
                            media,
                            Loc(x + (524 - width) // 2, y + 20 + (320 - height) // 2, width, height),
                        )
                    else:
                        text("预览不可用", x + 24, y + 160)
                    text("ID " + item["hash_id"], x + 20, y + 355, 22)
                    text(
                        f"点赞 {item['likes']} · 评论 {len(item['comments'])} · 提起 {item['pickup_times']}",
                        x + 20,
                        y + 395,
                        22,
                    )
                else:
                    ink = pixie.parse_color(accent_of(item["img_key"])["ink"])
                    color = tuple(round(channel * 255) for channel in (ink.r, ink.g, ink.b))
                    text(item["label"], x + 20, y + 16, 30, color)
                    text(f"{item['image_count']} 张 · /来只 {item['command_key']}", x + 20, y + 65, 23)
                    text("别名：" + "、".join(item["keys"])[:42], x + 20, y + 105, 21)
        footer.draw(image, 64, bottom + 10)
        return image

    def render_pages(self, model):
        items = model["items"]
        pages, offset = [], 0
        while offset < len(items):
            size = len(items) - offset
            while True:
                page = {**model, "items": items[offset : offset + size]}
                try:
                    self.layout(page)
                    break
                except ValueError:
                    if size == 1:
                        raise
                    size -= 1
            pages.append(PixieBackend(self.draw).render(page))
            offset += size
        return tuple(pages)

    def render(self, model):
        return self.render_pages(model)[0]
