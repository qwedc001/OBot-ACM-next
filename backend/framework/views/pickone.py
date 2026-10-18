"""Pass Dashboard snapshots/thumbnail bytes to original PickOne drawing code."""

from ..clients.renderer import PixieBackend
from .layout import fits


class PickOneBackend:
    @staticmethod
    def renderer(model):
        from .renderer.bindings import pickone_renderer

        return pickone_renderer(model)

    def draw(self, model):
        return self.renderer(model).render()

    def render_pages(self, model):
        pages, offset = [], 0
        while offset < len(model["items"]):
            size = len(model["items"]) - offset
            while True:
                page = {**model, "items": model["items"][offset : offset + size]}
                if fits(*self.renderer(page).measure()):
                    break
                if size == 1:
                    raise ValueError("PickOne sheet exceeds image size limit")
                size -= 1
            pages.append(PixieBackend(self.draw).render(page))
            offset += size
        return tuple(pages)

    def render(self, model):
        return self.render_pages(model)[0]
