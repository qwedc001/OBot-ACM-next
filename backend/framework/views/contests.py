"""OBot-style contest typography, phase sections and bounded column pages.

Adapted from OBot-ACM's ContestListRenderer: numbered rows, actual contest
progress and a light gradient sheet. Reuses the shared palette/footer; no I/O
except Pixie's output, which runs under the existing serialized render worker.
"""

from ..clients.renderer import PixieBackend
from ..contests import PHASES, Contest, format_duration, format_time
from .footer import CardFooter
from .palette import DECENT, INK, MUTED, paint_card

MARGIN, COLUMN, GAP, MAX_HEIGHT = 80, 760, 56, 3600


class ContestBackend:
    @staticmethod
    def text(value, size=24, *, width=COLUMN, weight="M", color=INK):
        from easy_pixie import StyledString

        return StyledString(value, weight, size, max_width=width, font_color=color, line_multiplier=1.25)

    def layout(self, model):
        items = [Contest(**item) for item in model["items"]]
        count = 1 if len(items) <= 4 else 2 if len(items) <= 10 else 3
        width = 2 * MARGIN + count * COLUMN + (count - 1) * GAP
        placements = []

        def place(block, x, y):
            placements.append((block, x, y))
            return y + block.height

        top = place(self.text(model["title"], 52, weight="H", width=width - 2 * MARGIN), MARGIN, 72)
        top = (
            place(self.text(f"{format_time(model['now'])} · 北京时间", 23, color=MUTED), MARGIN, top + 14)
            + 44
        )
        bars = []
        for phase, label in PHASES.items():
            group = [c for c in items if c.phase(model["now"]) == phase]
            if not group:
                continue
            top = place(self.text(label, 30, weight="H"), MARGIN, top) + 28
            heights = [top] * count
            for contest in group:
                column = min(range(count), key=heights.__getitem__)
                x, y = MARGIN + column * (COLUMN + GAP), heights[column]
                index = model.get("offset", 0) + items.index(contest) + 1
                place(self.text(f"{index:02d}", 34, color=MUTED, width=62, weight="H"), x, y)
                blocks = (
                    self.text(f"{contest.platform} · {contest.abbr}", 21, color=MUTED, width=COLUMN - 80),
                    self.text(contest.name, 36, weight="H", width=COLUMN - 80),
                    self.text(
                        f"{format_time(contest.start)} · {contest.status(model['now'])}",
                        23,
                        width=COLUMN - 80,
                    ),
                    self.text("持续 " + format_duration(contest.duration), 23, width=COLUMN - 80),
                    self.text(contest.supplement, 23, color=MUTED, width=COLUMN - 80),
                    self.text("查询 " + contest.key, 20, color=MUTED, width=COLUMN - 80),
                )
                for block in blocks:
                    y = place(block, x + 80, y) + 10
                if phase == "running":
                    ratio = min(1, max(0, (model["now"] - contest.start) / contest.duration))
                    bars.append((x + 80, y, ratio))
                    y += 20
                heights[column] = y + 32
            top = max(heights) + 30
        for warning in model.get("warnings", ()):
            top = place(self.text(warning, 23, color=MUTED, width=width - 2 * MARGIN), MARGIN, top) + 10
        top = (
            place(
                self.text("详情：/contest detail 查询标识或比赛名称", 23, width=width - 2 * MARGIN),
                MARGIN,
                top,
            )
            + 32
        )
        footer = CardFooter(width - 2 * MARGIN, detail="配色 " + DECENT.label)
        return width, top + footer.height + 70, placements, bars, footer, top

    def draw(self, model):
        import pixie
        from easy_pixie import Loc, draw_mask_rect, draw_text

        width, height, placements, bars, footer, bottom = self.layout(model)
        image = pixie.Image(width, height)
        paint_card(image, DECENT)
        for block, x, y in placements:
            draw_text(image, block, x, y)
        for x, y, ratio in bars:
            draw_mask_rect(image, Loc(x, y, COLUMN - 80, 4), (0, 0, 0, 24))
            if ratio:
                draw_mask_rect(image, Loc(x, y, round((COLUMN - 80) * ratio), 4), (49, 99, 108, 160))
        footer.draw(image, MARGIN, bottom)
        return image

    def render_pages(self, model):
        pages, offset = [], 0
        while offset < len(model["items"]):
            size = min(12, len(model["items"]) - offset)
            while True:
                page = {**model, "offset": offset, "items": model["items"][offset : offset + size]}
                if self.layout(page)[1] <= MAX_HEIGHT:
                    break
                if size == 1:
                    raise ValueError("Contest exceeds image size limit")
                size -= 1
            pages.append(PixieBackend(self.draw).render(page))
            offset += size
        return tuple(pages)

    def render(self, model):
        return self.render_pages(model)[0]
