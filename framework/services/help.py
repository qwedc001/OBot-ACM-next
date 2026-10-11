"""Help defaults to images and uses the command's native permission checks."""

import logging

from framework.catalog import CommandCatalog
from framework.contracts import RequestContext
from framework.views.help import HelpPixieBackend

from ..clients.renderer import RenderClient
from ..messages import Image, Message, Text
from .base import Service

logger = logging.getLogger(__name__)
RENDER_TOKENS = frozenset({"render", "draw", "r", "d"})
TEXT_TOKENS = frozenset({"text", "t", "文字"})


def parse_help_request(args: str) -> tuple[bool, str]:
    parts = args.strip().split(maxsplit=1)
    if parts and parts[0].casefold() in RENDER_TOKENS | TEXT_TOKENS:
        return parts[0].casefold() in RENDER_TOKENS, parts[1].strip() if len(parts) > 1 else ""
    return True, args.strip()


class HelpService(Service):
    dependencies = ("clients.renderer",)

    @classmethod
    def build(cls, container):
        return cls(container.catalog, container.clients.renderer)

    def __init__(self, catalog: CommandCatalog, renderer: RenderClient):
        self.catalog, self.renderer = catalog, renderer
        renderer.register("help", HelpPixieBackend())

    async def reply(
        self, context: RequestContext, args: str, *, image_available: bool = True
    ) -> str | tuple[Message, ...]:
        if context.command_allowed is None:
            raise ValueError("Help requires the same permission checker as command execution")
        draw, query = parse_help_request(args)
        model = await self.catalog.help_model(context.command_allowed, query)
        text = self.catalog.help_model_text(model)
        if not draw or not image_available or not model["found"] or not model["count"]:
            return text
        try:
            artifacts = await self.renderer.render_pages(context.actor, "help", model)
        except (ImportError, OSError, ValueError) as error:
            logger.warning("Help rendering unavailable: %s", type(error).__name__)
            latest = await self.catalog.help_model(context.command_allowed, query)
            return self.catalog.help_model_text(latest) + "\n\n图片暂不可用，已返回文字帮助。"
        latest = await self.catalog.help_model(context.command_allowed, query)
        if latest != model:
            return self.catalog.help_model_text(latest) + "\n\n权限发生变化，已返回最新文字帮助。"
        messages = []
        for index, artifact in enumerate(artifacts, 1):
            caption = f"帮助手册 · 当前可用 {model['count']} 条指令"
            if len(artifacts) > 1:
                caption += f"\n第 {index}/{len(artifacts)} 页"
            data = await self.renderer.artifacts.read(context.actor, artifact.id)
            messages.append(Message(Text(caption), Image(data)))
        latest = await self.catalog.help_model(context.command_allowed, query)
        if latest != model:
            return self.catalog.help_model_text(latest) + "\n\n权限发生变化，已返回最新文字帮助。"
        return tuple(messages)
