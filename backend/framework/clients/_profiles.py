"""Shared profile request lifecycle, not a second HTTP transport.

Successful values are bounded and short lived. Shielded tasks belong to this
client, not to an individual matcher; one cancelled waiter cannot cancel peers.
The task's own deadline and close() bound its lifetime even with no waiters.
"""

import asyncio
import re

from lxml import html

from ..profiles import Profile, ProfileError, clean
from ._cache import AsyncCache
from .base import Client
from .http import HttpClient, UpstreamError


def elements(tree, class_name: str):
    return tree.xpath(f"//*[contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')]")


def content(nodes) -> str:
    return clean(" ".join(node.text_content() for node in nodes))


def document(source: str):
    try:
        return html.fromstring(source)
    except (ValueError, html.etree.ParserError):
        raise UpstreamError("Profile: invalid HTML") from None


class ProfileClient(Client):
    dependencies = ("clients.http",)
    pattern = ""
    hint = ""

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http: HttpClient, *, ttl=60, capacity=128, timeout=30):
        self.http = http
        self.cache = AsyncCache[Profile](ttl=ttl, capacity=capacity, timeout=timeout)

    async def profile(self, handle: str) -> Profile:
        handle = handle.strip()
        if not re.fullmatch(self.pattern, handle):
            raise ProfileError(self.hint)
        return await self.cache.get(handle.casefold(), lambda: self._fetch(handle))

    async def _fetch(self, handle: str) -> Profile:
        raise NotImplementedError

    async def close(self):
        await self.cache.close()


async def optional(fetch):
    """An unavailable enrichment must not hide a valid public profile."""
    try:
        async with asyncio.timeout(15):
            return await fetch()
    except (UpstreamError, TimeoutError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
