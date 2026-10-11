"""Official QQ event identities, native roles and message delivery."""

from nonebot.adapters import Bot, Event
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    GuildMessageEvent,
)
from nonebot.adapters.qq.permission import GROUP_ADMIN, GROUP_OWNER, GUILD_ADMIN, GUILD_OWNER

from ...contracts import ExternalIdentity, SceneKind, SceneRef
from ...messages import Image, Keyboard, Markdown, Message, SendTarget, Text
from .base import Incoming, Platform
from .qq_messages import build_qq_reply, send_qq_to
from .qq_transport import QQAdapter


class QQPlatform(Platform):
    adapter_type = QQAdapter
    supported_segments = frozenset({Text, Image, Markdown, Keyboard})

    def normalize(self, bot: Bot, event: Event) -> Incoming:
        app_id = bot.self_id
        if isinstance(event, DirectMessageCreateEvent):
            identity = ExternalIdentity("qq", app_id, "guild_user", event.author.id)
            scene = SceneRef("qq", app_id, SceneKind.DIRECT, event.guild_id, event.guild_id)
        elif isinstance(event, GuildMessageEvent):
            identity = ExternalIdentity("qq", app_id, "guild_user", event.author.id)
            scene = SceneRef("qq", app_id, SceneKind.CHANNEL, event.channel_id, event.guild_id)
        elif isinstance(event, GroupMessageCreateEvent):
            identity = ExternalIdentity("qq", app_id, "group_member", event.author.member_openid)
            scene = SceneRef("qq", app_id, SceneKind.GROUP, event.group_openid)
        elif isinstance(event, C2CMessageCreateEvent):
            identity = ExternalIdentity("qq", app_id, "c2c_user", event.author.user_openid)
            scene = SceneRef("qq", app_id, SceneKind.C2C, event.author.user_openid)
        else:
            raise ValueError("Unsupported QQ message event")
        return Incoming(identity, scene, event.id)

    async def group_admin(self, bot: Bot, event: Event) -> bool:
        return bool(await (GROUP_ADMIN | GROUP_OWNER | GUILD_ADMIN | GUILD_OWNER)(bot, event))

    async def build_message(self, recipient: Event | SendTarget, message: Message, at_sender: bool | None):
        return await build_qq_reply(recipient, message, at_sender)

    async def send_to(self, bot: Bot, target: SendTarget, message):
        return await send_qq_to(bot, target, message)
