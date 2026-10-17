"""Profile workflows shared by plugins and future web endpoints."""

import logging
from dataclasses import asdict

from ..clients.http import UpstreamError
from ..clients.jobs import QueueClosed, QueueFull
from ..contracts import RequestContext
from ..messages import Image, Message, Text
from ..profiles import Profile, ProfileError
from ..views.profile import ProfilePixieBackend
from .base import Service

logger = logging.getLogger(__name__)


class ProfilesService(Service):
    dependencies = (
        "clients.codeforces",
        "clients.atcoder",
        "clients.nowcoder",
        "clients.renderer",
        "clients.avatars",
    )

    @classmethod
    def build(cls, container):
        return cls(
            {name: getattr(container.clients, name) for name in ("codeforces", "atcoder", "nowcoder")},
            container.clients.renderer,
            container.clients.avatars,
        )

    def __init__(self, clients, renderer, avatars):
        self.clients, self.renderer, self.avatars = clients, renderer, avatars
        renderer.register("profile", ProfilePixieBackend())

    async def query(self, platform: str, handle: str) -> Profile:
        return await self.clients[platform].profile(handle)

    async def reply(
        self, context: RequestContext, platform: str, handle: str, *, image_available=True
    ) -> Message | str:
        try:
            profile = await self.query(platform, handle)
        except ProfileError as error:
            return str(error)
        except UpstreamError:
            logger.warning("Profile query unavailable: %s", platform)
            return "账号资料暂时无法获取，请稍后重试。"
        if not image_available:
            return profile.text()
        try:
            model = asdict(profile)
            model["avatar"] = await self.avatars.image(profile.avatar_url)
            artifact = await self.renderer.render(context.actor, "profile", model)
            data = await self.renderer.artifacts.read(context.actor, artifact.id)
        except (ImportError, OSError, ValueError, TimeoutError, QueueFull, QueueClosed):
            logger.warning("Profile image unavailable: %s", platform)
            return profile.text() + "\n图片暂不可用，已返回文字资料。"
        return Message(Text(f"{profile.platform} · {profile.handle}\n{profile.url}"), Image(data))
