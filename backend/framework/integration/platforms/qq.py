"""Official QQ event identities, native roles and message delivery."""

from nonebot.adapters import Bot, Event
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    GuildMessageEvent,
)
from nonebot.adapters.qq.models import GroupMentionUser
from nonebot.adapters.qq.permission import GROUP_ADMIN, GROUP_OWNER, GUILD_ADMIN, GUILD_OWNER

from ...contracts import ExternalIdentity, MentionedUser, SceneKind, SceneRef
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

    def mentioned_users(self, bot: Bot, event: Event) -> tuple[MentionedUser, ...]:
        own_ids = {bot.self_id}
        try:
            own_ids.add(bot.self_info.id)
        except RuntimeError:
            pass
        if isinstance(event, GroupMessageCreateEvent):
            kind = "group_member"
            if event.mentions:
                return tuple(
                    MentionedUser(ExternalIdentity("qq", bot.self_id, kind, user.member_openid), True)
                    for user in event.mentions
                    if isinstance(user, GroupMentionUser)
                    and not user.bot
                    and not user.is_you
                    and user.member_openid.strip()
                )
        elif isinstance(event, GuildMessageEvent) and not isinstance(event, DirectMessageCreateEvent):
            kind = "guild_user"
            if event.mentions:
                return tuple(
                    MentionedUser(ExternalIdentity("qq", bot.self_id, kind, user.id), True)
                    for user in event.mentions
                    if user.id not in own_ids and user.id.strip() and not getattr(user, "bot", False)
                )
        else:
            return ()
        # Older payloads expose only a display ID in the mention segment. It can
        # look up an observed identity, but must not invent a new member_openid.
        return tuple(
            MentionedUser(ExternalIdentity("qq", bot.self_id, kind, segment.data["user_id"]))
            for segment in event.get_message()
            if segment.type == "mention_user"
            and segment.data.get("user_id")
            and segment.data["user_id"] not in own_ids
            and not segment.data.get("is_bot", False)
        )

    async def build_message(self, recipient: Event | SendTarget, message: Message, at_sender: bool | None):
        return await build_qq_reply(recipient, message, at_sender)

    def reply_author(self, bot: Bot, event: Event) -> MentionedUser | None:
        if isinstance(event, DirectMessageCreateEvent):
            return None
        if not isinstance(event, (GroupMessageCreateEvent, GuildMessageEvent)):
            return None
        # The native adapter resolves the reference index before dispatch. Do not
        # guess an author from arbitrary msg_elements or a display-name match.
        reply = event.reply
        author = reply.author if reply else None
        if not author or getattr(author, "bot", False):
            return None
        group = isinstance(event, GroupMessageCreateEvent)
        user_id = author.member_openid if group else author.id
        if not user_id or not user_id.strip() or user_id == bot.self_id:
            return None
        try:
            if user_id == bot.self_info.id:
                return None
        except RuntimeError:
            pass
        return MentionedUser(
            ExternalIdentity("qq", bot.self_id, "group_member" if group else "guild_user", user_id), True
        )

    async def send_to(self, bot: Bot, target: SendTarget, message):
        return await send_qq_to(bot, target, message)

    def send_target(self, bot: Bot, scene: SceneRef) -> SendTarget:
        return SendTarget(
            bot.adapter.get_name(),
            "dms" if scene.kind == SceneKind.DIRECT else scene.kind.value,
            scene.external_id,
            bot_id=bot.self_id,
        )
