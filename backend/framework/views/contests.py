"""Adapt contest snapshots to the original OBot list renderer."""

from ..clients.renderer import PixieBackend
from .layout import MAX_EDGE, fits

MAX_HEIGHT = MAX_EDGE


class ContestBackend:
    def renderer(self, model):
        from .renderer.bindings import contest_renderer

        return contest_renderer(model)

    def measure(self, model):
        if model.get("detail"):
            from .contest_detail import ContestDetailRenderer

            return ContestDetailRenderer().detail_layout(model)[:2]
        return self.renderer(model).measure()

    def draw(self, model):
        if model.get("detail"):
            from .contest_detail import ContestDetailRenderer

            return ContestDetailRenderer().draw(model)
        return self.renderer(model).render()

    def render_pages(self, model):
        pages, offset = [], 0
        while offset < len(model["items"]):
            size = len(model["items"]) - offset
            while True:
                page = {**model, "items": model["items"][offset : offset + size]}
                width, height = self.measure(page)
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
