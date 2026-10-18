"""Compact PickOne category and eight-image contact sheets, inspired by OBot-ACM.

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
from .palette import DECENT, paint_card


class PickOneBackend:
    def draw(self, model):
        import pixie
        from easy_pixie import Loc, StyledString, draw_img, draw_mask_rect, draw_text
        from PIL import Image

        preview = model.get("preview", False)
        items = model["items"]
        cell_height = 450 if preview else 170
        footer = CardFooter(1072, detail="配色 " + DECENT.label)
        bottom = 215 + math.ceil(len(items) / 2) * (cell_height + 24)
        image = pixie.Image(1200, bottom + footer.height + 80)
        paint_card(image, DECENT)

        def text(value, x, y, size=26, color=(30, 35, 40), width=484):
            # Fixed cells use one measured line; long user-defined labels cannot overlap statistics.
            value = value.replace("\n", " ")
            block = StyledString(value, "B", size, font_color=color, max_width=width)
            while block.height > size * 1.6 and len(value) > 1:
                value = value[:-1]
                block = StyledString(value + "…", "B", size, font_color=color, max_width=width)
            return draw_text(image, block, x, y)

        text(model["title"][:32], 64, 64, 48, width=1072)
        text(model["hint"], 64, 135, 23, width=1072)
        with tempfile.TemporaryDirectory(prefix="obot-pickone-render-") as directory:
            for index, item in enumerate(items):
                x, y = 64 + (index % 2) * 548, 210 + (index // 2) * (cell_height + 24)
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
                    text(item["id"][:24], x + 20, y + 16, 30, color)
                    text(f"{item['image_count']} 张 · {item['img_key'][:32]}", x + 20, y + 65, 23)
                    text("别名：" + "、".join(item["keys"])[:42], x + 20, y + 105, 21)
        footer.draw(image, 64, bottom + 10)
        return image

    def render_pages(self, model):
        items = model["items"]
        size = 8 if model.get("preview") else 12
        return tuple(
            PixieBackend(self.draw).render({**model, "items": items[start : start + size]})
            for start in range(0, len(items), size)
        )

    def render(self, model):
        return self.render_pages(model)[0]
