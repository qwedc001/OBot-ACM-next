import asyncio
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import Client


class UpstreamError(Exception):
    """Messages deliberately omit query strings, headers and response bodies."""


class HttpClient(Client):
    dependencies = ()

    @classmethod
    def build(cls, container):
        return cls(container.settings.obot_http_timeout, container.settings.obot_http_concurrency)

    def __init__(
        self,
        timeout: float = 15,
        concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 0.25,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        self._limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(concurrency)
        )
        self._retry_delay = retry_delay

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("HTTP URL must have a host and no embedded credentials")
        method = method.upper()
        attempts = 3 if method in {"GET", "HEAD"} else 1
        # Include port: independent upstreams sharing a host need separate budgets.
        origin = f"{parts.scheme}://{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}"
        async with self._limits[origin]:
            for attempt in range(attempts):
                try:
                    response = await self._client.request(method, url, **kwargs)
                except httpx.TransportError:
                    if attempt + 1 == attempts:
                        raise UpstreamError(f"{method} {parts.hostname}: transport failure") from None
                else:
                    if response.is_success:
                        return response
                    retryable = response.status_code in {429, 502, 503, 504}
                    if not retryable or attempt + 1 == attempts:
                        raise UpstreamError(f"{method} {parts.hostname}: HTTP {response.status_code}")
                await asyncio.sleep(self._retry_delay * (2**attempt))
        raise AssertionError("Unreachable retry state")

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.request("GET", url, **kwargs)
        try:
            return await asyncio.to_thread(response.json)
        except ValueError:
            raise UpstreamError(f"GET {urlsplit(url).hostname}: invalid JSON") from None

    async def close(self) -> None:
        await self._client.aclose()
