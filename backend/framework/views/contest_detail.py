"""Single-contest detail view (no counterpart in original OBot)."""

from ..contests import Contest, format_duration, format_time
from .footer import CardFooter
from .palette import DECENT, INK, MUTED, paint_card

MARGIN, COLUMN, GAP = 80, 760, 56


class ContestDetailRenderer:
    @staticmethod
    def text(value, size=24, *, width=COLUMN, weight="M", color=INK):
        from easy_pixie import StyledString

        return StyledString(value, weight, size, max_width=width, font_color=color, line_multiplier=1.25)

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

        width, height, placements, bars, footer, bottom = self.detail_layout(model)
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
