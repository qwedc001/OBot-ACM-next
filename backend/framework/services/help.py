"""Help defaults to images and uses the command's native permission checks."""

import difflib
import logging

from framework.catalog import CommandCatalog, MatchMode
from framework.contracts import RequestContext
from framework.views.help import HelpPixieBackend

from ..clients.renderer import RenderClient
from ..messages import Image, Message, Text
from ..responses import command_failure
from .base import Service

logger = logging.getLogger(__name__)


class HelpService(Service):
    dependencies = ("clients.renderer",)

    @classmethod
    def build(cls, container):
        return cls(container.catalog, container.clients.renderer)

    def __init__(self, catalog: CommandCatalog, renderer: RenderClient):
        self.catalog, self.renderer = catalog, renderer
        renderer.register("help", HelpPixieBackend())

    async def unmatched(self, check, prefix, query):
        # Match registered names/aliases only, never module names or commands the
        # current actor cannot execute. Multiple aliases yield one suggestion.
        commands = await self.catalog.visible(check)
        choices = {
            name.casefold(): (name, spec)
            for spec in commands
            if spec.mode != MatchMode.KEYWORD
            for name in spec.invocation_names(self.catalog.separator)
        }
        seen, suggestions = set(), []
        for name in difflib.get_close_matches(query.casefold(), choices, n=max(1, len(choices)), cutoff=0.5):
            display, spec = choices[name]
            if spec.id in seen:
                continue
            seen.add(spec.id)
            suggestions.append(f"{prefix}{display} — {spec.summary}")
            if len(suggestions) == 3:
                break
        return command_failure(prefix + query, suggestions=suggestions, help_command=prefix + "help")

    async def reply(
        self, context: RequestContext, query: str = "", *, draw: bool = True, image_available: bool = True
    ) -> str | tuple[Message, ...]:
        if context.command_allowed is None:
            raise ValueError("Help requires the same permission checker as command execution")
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
