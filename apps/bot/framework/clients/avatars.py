"""Async public profile avatars; bounded download and off-thread normalization."""

import asyncio
import io
import warnings
from urllib.parse import urlsplit

from ._cache import AsyncCache
from .base import Client
from .http import UpstreamError

AVATAR_HOSTS = frozenset(
    {
        "codeforces.com",
        "userpic.codeforces.org",
        "img.atcoder.jp",
        "atcoder.jp",
        "images.nowcoder.com",
        "uploadfiles.nowcoder.com",
        "static.nowcoder.com",
    }
)


class AvatarsClient(Client):
    dependencies = ("clients.http",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http):
        self.http = http
        self.cache = AsyncCache[bytes](ttl=3600, capacity=128, timeout=8)

    async def image(self, url: str | None) -> bytes | None:
        if not url:
            return None
        url = "https:" + url if url.startswith("//") else url
        url = "https:" + url[5:] if url.startswith("http:") else url
        try:
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or parts.hostname not in AVATAR_HOSTS
                or parts.username
                or parts.password
                or parts.port not in {None, 443}
            ):
                return None
            return await self.cache.get(url, lambda: self._fetch(url))
        except (UpstreamError, ValueError, ImportError):
            return None

    async def _fetch(self, url):
        data = await self.http.download(url)
        return await asyncio.to_thread(self.normalize, data)

    @staticmethod
    def normalize(data: bytes) -> bytes:
        from PIL import Image, ImageOps, UnidentifiedImageError

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > 16_000_000:
                        raise ValueError("Avatar dimensions too large")
                    image.seek(0)
                    square = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"), (176, 176))
                    result = io.BytesIO()
                    square.save(result, "PNG")
                    return result.getvalue()
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise ValueError("Avatar image unavailable") from None

    async def close(self):
        await self.cache.close()
