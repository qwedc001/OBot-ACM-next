"""Format a random sentence and its attribution for chat."""

import logging

from ..clients.hitokoto import HitokotoClient
from ..clients.http import UpstreamError
from .base import Service

logger = logging.getLogger(__name__)


class HitokotoService(Service):
    dependencies = ("clients.hitokoto",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.hitokoto)

    def __init__(self, client: HitokotoClient):
        self.client = client

    async def random(self) -> str:
        try:
            quote = await self.client.random()
        except UpstreamError:
            logger.warning("Hitokoto request unavailable")
            return "一言暂时无法获取，请稍后再试。"
        return f"[Hitokoto]\n{quote.content}\nBy {quote.author or ''}「{quote.source or '未知出处'}」"
