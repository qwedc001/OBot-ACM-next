"""Narrow adapters around the verbatim renderer tree, loaded in the draw worker.

Drawing classes stay in their original files. These bindings translate current
snapshots and replace only the old version information, retaining original Tips.
"""

from types import SimpleNamespace

from easy_pixie import StyledString, calculate_width, draw_text

from ...contests import PHASES, Contest
from ..footer import CardFooter
from .pixie.model import RenderableSection
from .pixie.render_contest_list import ContestListRenderer
from .pixie.render_help import HelpRenderer
from .pixie.render_pick_one import PickOnePreviewRenderer, PickOneRenderer
from .runtime import PickOne, PickOneImgStat


class InformationSection(RenderableSection):
    def __init__(self, *, detail, width=916, dark=False, tips=None):
        self.footer = CardFooter(width, detail=detail, dark=dark)
        self.tips = tips
        self.tip_height = max(tips.str_tips_title.height, 8 + tips.str_tips_detail.height) if tips else 0

    def get_height(self):
        return self.tip_height + self.footer.height

    def render(self, img, x, y):
        if self.tips:
            # Original Tips label, body, font, 12px horizontal / 8px vertical
            # offsets and 64px bottom padding; only generation info is replaced.
            draw_text(img, self.tips.str_tips_title, x, y)
            draw_text(
                img, self.tips.str_tips_detail, x + calculate_width(self.tips.str_tips_title) + 12, y + 8
            )
        return self.footer.draw(img, x, y + self.tip_height)


class HelpPageRenderer(HelpRenderer):
    def __init__(self, categories, *, gradient=None, page=1, total=1):
        super().__init__({}, gradient)
        self.categories = categories
        self.page, self.total = page, total

    def _get_render_sections(self):
        sections = super()._get_render_sections()
        sections[1].section_categories = self.categories
        detail = "配色 " + self._gradient_color.name
        if self.total > 1:
            detail += f" · 第 {self.page}/{self.total} 页"
        sections[-1] = InformationSection(detail=detail, dark=True, tips=sections[-1])
        return sections


class NoticeSection(RenderableSection):
    def __init__(self, text):
        self.text = StyledString(text, "M", 28, max_width=916, line_multiplier=1.32)

    def get_height(self):
        return self.text.height

    def render(self, img, x, y):
        return draw_text(img, self.text, x, y)


def contest_renderer(model):
    now = model["now"]
    groups = {phase: [] for phase in PHASES}
    for row in model["items"]:
        contest = Contest(**row)
        groups[contest.phase(now)].append(
            SimpleNamespace(
                platform="NowCoder" if contest.platform == "牛客" else contest.platform,
                abbr=contest.abbr,
                name=contest.name,
                start_time=contest.start,
                duration=contest.duration,
                supplement=contest.supplement,
                phase=contest.status(now),
                now=now,
            )
        )
    renderer = ContestListRenderer(
        *groups.values(), is_today=model["title"].startswith("今日"), gradient=model.get("_gradient")
    )
    renderer.sections[0].str_title.content = model["title"]
    renderer.sections[-1] = InformationSection(
        detail="配色 " + renderer._gradient_color.name, tips=renderer.sections[-1]
    )
    renderer.sections[2:2] = [NoticeSection(text) for text in model.get("warnings", ())]
    return renderer


def pickone_renderer(model):
    categories = model.get("categories", model["items"] if not model.get("preview") else [])
    if not categories:
        categories = [
            dict(
                label=model["title"].removesuffix(" · 预览"),
                command_key="preview",
                keys=[],
                image_count=len(model["items"]),
            )
        ]
    # Original collection sorting/palette/summary are all handled by PickOneRenderer.
    data = PickOne(
        ids=[(item["label"], item["image_count"]) for item in categories],
        conf={
            item["command_key"]: SimpleNamespace(id=item["label"], key=[item["command_key"], *item["keys"]])
            for item in categories
        },
    )
    if model.get("preview"):
        stats = [
            PickOneImgStat(
                hash_id=item["hash_id"],
                likes=item["likes"],
                comments=len(item["comments"]),
                pickup_times=item["pickup_times"],
                add_time=item.get("add_time", 0),
                thumbnail=item.get("thumbnail"),
            )
            for item in model["items"]
        ]
        renderer = PickOnePreviewRenderer(data, model.get("category_key", next(iter(data.conf))), stats)
    else:
        renderer = PickOneRenderer(data)
    renderer.sections[-1] = InformationSection(
        width=3136, detail="Rainbow spectrum: red to purple", tips=renderer.sections[-1]
    )
    return renderer
