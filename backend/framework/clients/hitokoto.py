"""Async Hitokoto API access with a brief shared cache."""

import asyncio
from dataclasses import dataclass
from time import monotonic

from .base import Client
from .http import HttpClient, UpstreamError


@dataclass(frozen=True)
class Quote:
    content: str
    source: str | None
    author: str | None


class HitokotoClient(Client):
    dependencies = ("clients.http",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http: HttpClient, *, timeout: float = 8):
        self.http = http
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._cached: Quote | None = None
        self._expires_at = 0.0

    async def random(self) -> Quote:
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    if self._cached is not None and monotonic() < self._expires_at:
                        return self._cached
                    data = await self.http.get_json(
                        "https://v1.hitokoto.cn/", params={"encode": "json"}, timeout=5
                    )
                    quote = self._parse(data)
                    # Share concurrent requests and avoid bursts against the public API.
                    self._cached, self._expires_at = quote, monotonic() + 2
                    return quote
        except TimeoutError:
            raise UpstreamError("Hitokoto: request timed out") from None

    @staticmethod
    def _parse(data) -> Quote:
        if not isinstance(data, dict):
            raise UpstreamError("Hitokoto: invalid response")
        content = data.get("hitokoto")
        source, author = data.get("from"), data.get("from_who")
        if (
            not isinstance(content, str)
            or not content.strip()
            or any(value is not None and not isinstance(value, str) for value in (source, author))
        ):
            raise UpstreamError("Hitokoto: invalid response")
        return Quote(
            content.strip(),
            source.strip() or None if source else None,
            author.strip() or None if author else None,
        )
