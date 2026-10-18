"""OBot-style contest typography, phase sections and bounded column pages.

Adapted from OBot-ACM's ContestListRenderer: numbered rows, actual contest
progress and a light gradient sheet. Reuses the shared palette/footer; no I/O
except Pixie's output, which runs under the existing serialized render worker.
"""

from ..clients.renderer import PixieBackend
from ..contests import PHASES, Contest, format_duration, format_time
from .footer import CardFooter
from .layout import fits, split_columns
from .palette import DECENT, INK, MUTED, paint_card

MARGIN, COLUMN, GAP, MAX_HEIGHT = 80, 760, 56, 3600


class ContestBackend:
    @staticmethod
    def text(value, size=24, *, width=COLUMN, weight="M", color=INK):
        from easy_pixie import StyledString

        return StyledString(value, weight, size, max_width=width, font_color=color, line_multiplier=1.25)

    def layout(self, model):
        if model.get("detail"):
            return self.detail_layout(model)
        items = [Contest(**item) for item in model["items"]]
        largest = max(sum(c.phase(model["now"]) == phase for c in items) for phase in PHASES)
        # Original ContestListRenderer chooses columns by the largest phase.
        count = 3 if largest > 12 else 2 if largest > 5 else 1
        return self.list_layout(model, count)

    def list_layout(self, model, count, column_width=COLUMN):
        items = [Contest(**item) for item in model["items"]]
        placements = []

        def place(block, x, y):
            placements.append((block, x, y))
            return y + block.height

        # Phase headings span the sheet. Each phase balances its own rows,
        # preserving source order, before the following phase starts below it.
        rows = []
        headings = {phase: self.text(label, 30, weight="H") for phase, label in PHASES.items()}
        for phase in PHASES:
            for contest in (c for c in items if c.phase(model["now"]) == phase):
                index = model.get("offset", 0) + items.index(contest) + 1
                number = self.text(f"{index:02d}", 34, color=MUTED, width=62, weight="H")
                blocks = (
                    self.text(
                        f"{contest.platform} · {contest.abbr}", 21, color=MUTED, width=column_width - 80
                    ),
                    self.text(contest.name, 36, weight="H", width=column_width - 80),
                    self.text(
                        f"{format_time(contest.start)} · {contest.status(model['now'])}",
                        23,
                        width=column_width - 80,
                    ),
                    self.text("持续 " + format_duration(contest.duration), 23, width=column_width - 80),
                    self.text(contest.supplement, 23, color=MUTED, width=column_width - 80),
                    self.text("查询 " + contest.key, 20, color=MUTED, width=column_width - 80),
                )
                ratio = (
                    min(1, max(0, (model["now"] - contest.start) / contest.duration))
                    if phase == "running"
                    else None
                )
                height = sum(block.height + 10 for block in blocks) + (20 if ratio is not None else 0) + 32
                rows.append((phase, number, blocks, ratio, height))

        width = 2 * MARGIN + count * column_width + (count - 1) * GAP
        top = place(self.text(model["title"], 52, weight="H", width=width - 2 * MARGIN), MARGIN, 72)
        top = (
            place(self.text(f"{format_time(model['now'])} · 北京时间", 23, color=MUTED), MARGIN, top + 14)
            + 44
        )
        bars = []
        for phase in PHASES:
            entries = [row for row in rows if row[0] == phase]
            if not entries:
                continue
            top = place(headings[phase], MARGIN, top) + 28
            columns = split_columns(entries, count, lambda row: row[-1])
            bottoms = []
            for column, entries in enumerate(columns):
                x, y = MARGIN + column * (column_width + GAP), top
                for _, number, blocks, ratio, _ in entries:
                    place(number, x, y)
                    for block in blocks:
                        y = place(block, x + 80, y) + 10
                    if ratio is not None:
                        bars.append((x + 80, y, column_width - 80, ratio))
                        y += 20
                    y += 32
                bottoms.append(y)
            top = max(bottoms) + 30
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

    def detail_layout(self, model):
        # A detail card is a single sheet, like the original profile cards.
        return self._detail_layout(model, 1472)

    def _detail_layout(self, model, width):
        contest = Contest(**model["items"][0])
        content_width = width - 2 * MARGIN
        placements, bars = [], []

        def place(value, x, y, size=28, *, block_width=content_width, weight="M", color=INK):
            block = self.text(value, size, width=block_width, weight=weight, color=color)
            placements.append((block, x, y))
            return y + block.height

        top = place(f"{contest.platform} · {contest.abbr} · {model['title']}", MARGIN, 72, color=MUTED)
        top = place(contest.name, MARGIN, top + 24, 52, weight="H") + 46
        field_columns = 1 if content_width < 960 else 2
        cell_width = (content_width - GAP * (field_columns - 1)) // field_columns
        fields = (
            ("开始时间 · 北京时间", format_time(contest.start)),
            ("结束时间 · 北京时间", format_time(contest.end)),
            ("当前状态", contest.status(model["now"])),
            ("比赛时长", format_duration(contest.duration)),
        )
        for offset in range(0, len(fields), field_columns):
            bottoms = []
            for column, (label, value) in enumerate(fields[offset : offset + field_columns]):
                x = MARGIN + column * (cell_width + GAP)
                y = place(label, x, top, 23, color=MUTED, block_width=cell_width)
                bottoms.append(place(value, x, y + 12, 34, weight="H", block_width=cell_width))
            top = max(bottoms) + 34
        if contest.phase(model["now"]) == "running":
            ratio = min(1, max(0, (model["now"] - contest.start) / contest.duration))
            bars.append((MARGIN, top, content_width, ratio))
            top += 32
        if contest.supplement:
            top = place(contest.supplement, MARGIN, top, color=MUTED) + 28
        top = place("查询标识：" + contest.key, MARGIN, top, 23, color=MUTED) + 12
        if contest.url:
            top = place(contest.url, MARGIN, top, 23, color=MUTED) + 12
        top = place("更新于 " + format_time(model["now"]) + " · 北京时间", MARGIN, top, 23, color=MUTED) + 24
        for warning in model.get("warnings", ()):
            top = place(warning, MARGIN, top, 23, color=MUTED) + 12
        footer = CardFooter(content_width, detail="配色 " + DECENT.label)
        return width, top + footer.height + 70, placements, bars, footer, top

    def draw(self, model):
        import pixie
        from easy_pixie import Loc, draw_mask_rect, draw_text

        width, height, placements, bars, footer, bottom = self.layout(model)
        image = pixie.Image(width, height)
        paint_card(image, DECENT)
        for block, x, y in placements:
            draw_text(image, block, x, y)
        for x, y, bar_width, ratio in bars:
            draw_mask_rect(image, Loc(x, y, bar_width, 4), (0, 0, 0, 24))
            if ratio:
                draw_mask_rect(image, Loc(x, y, round(bar_width * ratio), 4), (49, 99, 108, 160))
        footer.draw(image, MARGIN, bottom)
        return image

    def render_pages(self, model):
        pages, offset = [], 0
        while offset < len(model["items"]):
            # Keep the complete list on one sheet when it fits. Pagination is
            # only the last resort at the measured height limit, never at 12 rows.
            size = len(model["items"]) - offset
            while True:
                page = {**model, "offset": offset, "items": model["items"][offset : offset + size]}
                width, height, *_ = self.layout(page)
                if height <= MAX_HEIGHT and fits(width, height):
                    break
                if size == 1:
                    raise ValueError("Contest exceeds image size limit")
                size -= 1
            pages.append(PixieBackend(self.draw).render(page))
            offset += size
        return tuple(pages)

    def render(self, model):
        return self.render_pages(model)[0]
