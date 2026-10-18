"""Feed the filtered catalog into original OBot help sections.

Only safety overflow splits categories at whole-command boundaries. The actual
layout, typography, chips and underlines live in the ported Pixie renderer.
"""

from types import SimpleNamespace

from ..clients.renderer import PixieBackend
from .layout import MAX_EDGE, MAX_PIXELS, fits

MAX_HEIGHT = MAX_EDGE


class HelpPixieBackend:
    def layout(self, model):
        from copy import copy

        from .palette import DECENT
        from .renderer.bindings import HelpPageRenderer as HelpRenderer
        from .renderer.pixie.render_help import _HelpCategory

        categories = []
        for group in model["groups"]:
            helps = []
            for command in group["commands"]:
                prefix = "" if command["mode"] == "keyword" else "/"
                description = [command["summary"]]
                if command["aliases"]:
                    description.append("别名：" + "、".join(prefix + a for a in command["aliases"]))
                if command["examples"]:
                    description.append("示例：" + "；".join(command["examples"]))
                helps.append(
                    SimpleNamespace(
                        id=command["id"],
                        command=command["usage"] or prefix + command["name"],
                        help="\n".join(description),
                        restriction={"superuser": "超管", "group_admin": "群管"}.get(
                            command.get("permission")
                        ),
                    )
                )
            if helps:
                categories.append(
                    _HelpCategory(f"{group['name']} · {group['summary']}", helps, DECENT.colors[-1])
                )
        if not categories:
            return []

        width, overhead = HelpRenderer([]).measure()
        capacity = min(MAX_HEIGHT, MAX_PIXELS // width) - overhead
        chunks = []
        for category in categories:
            chunk = copy(category)
            chunk.section_items = []
            for item in category.section_items:
                trial = copy(chunk)
                trial.section_items = [*chunk.section_items, item]
                if trial.get_height() > capacity:
                    if chunk.section_items:
                        chunks.append(chunk)
                    chunk = copy(category)
                    chunk.section_items = [item]
                    if chunk.get_height() > capacity:
                        raise ValueError("Help entry exceeds image size limit; text help remains available")
                else:
                    chunk = trial
            chunks.append(chunk)

        groups, pending = [], []
        for chunk in chunks:
            width, height = HelpRenderer([*pending, chunk]).measure()
            if pending and (height > MAX_HEIGHT or not fits(width, height)):
                groups.append(pending)
                pending = []
            pending.append(chunk)
        if pending:
            groups.append(pending)
        return [HelpRenderer(group, page=i, total=len(groups)) for i, group in enumerate(groups, 1)]

    def render_pages(self, model):
        return tuple(
            PixieBackend(lambda _, page=page: page.render()).render(model) for page in self.layout(model)
        )

    def render(self, model):
        pages = self.render_pages(model)
        if len(pages) != 1:
            raise ValueError("Use render_pages for a help book with multiple pages")
        return pages[0]
