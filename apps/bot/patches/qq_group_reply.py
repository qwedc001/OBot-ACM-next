"""Fix OBot native mention segments at QQ's *group-only* send boundaries.

Upstream targets: nonebot.adapters.qq.bot.Bot.send and Bot.send_to_group, 1.7.3.
The OBot ReplyClient prepends a native mention_user segment for at_sender=True.
In QQ group text/media replies the platform already labels the sender; upstream
serializes that extra segment as literal <@id>. Conversely, group msg_type=2
ignores the native content field, so a separate mention_user never reaches the
Markdown body. Both behaviors were reproduced on a real Android QQ client.

We remove ONLY a redundant leading mention of the authenticated passive sender
from non-Markdown group replies. For raw Markdown, materialize explicit native
mention segments into a copied Markdown body. Never change Guild/C2C methods,
user identities, msg_id/msg_seq, media upload logic or template parameters.
Remove/recheck this patch when upstream separates QQ group mention serialization
from guild serialization or when QQ changes its observed reply behavior.

Related upstream research (not a wholesale backport): adapter-qq PR #223,
head 570d61703174e0a38f105d741a97e8679d02806e, changes tag serialization
globally; issues #220/#225 discuss plain-text and Markdown limitations. We use
the documented XML tag only inside group Markdown, retaining the installed SDK's
Guild/C2C parsing and never implicitly upgrading ordinary text to Markdown.
https://github.com/nonebot/adapter-qq/pull/223
"""

from functools import wraps
from html import escape
from importlib.metadata import version

from nonebot.adapters.qq import Bot, Message, MessageSegment
from nonebot.adapters.qq.event import GroupMessageCreateEvent


def group_passive_message(event, message):
    if not isinstance(event, GroupMessageCreateEvent):
        return message
    native = Bot._prepare_message(message)
    if native["markdown"] or not native or native[0].type != "mention_user":
        return message
    sender_ids = {event.author.id, event.author.member_openid}
    if native[0].data["user_id"] not in sender_ids or not any(
        seg.type != "text" or seg.data["text"].strip() for seg in native[1:]
    ):
        return message
    # Make a new list and text segment: caller-owned messages remain unchanged.
    result = Message(native[1:])
    if result and result[0].type == "text":
        text = result[0].data["text"].removeprefix(" ")
        if text:
            result[0] = MessageSegment.text(text)
        else:
            del result[0]
    return result


def group_markdown_message(message):
    native = Bot._prepare_message(message)
    mentions, markdowns = native["mention_user"], native["markdown"]
    if not mentions or len(markdowns) != 1:
        return message
    if any(segment.type == "text" and segment.data["text"].strip() for segment in native):
        # Text+Markdown is not a supported mixed layout (upstream issue #225).
        # Do not silently reinterpret the caller's text as Markdown.
        return message
    markdown = markdowns[0].data["markdown"]
    if markdown.content is None:
        # A custom template has no universal insertion slot. Do not guess one.
        return message
    prefix = " ".join(
        f'<qqbot-at-user id="{escape(str(segment.data["user_id"]), quote=True)}" />' for segment in mentions
    )
    updated = markdown.model_copy(update={"content": prefix + "\n" + markdown.content})
    return Message(
        [
            MessageSegment.markdown(updated) if segment is markdowns[0] else segment
            for segment in native
            if segment.type not in {"mention_user", "text"}
        ]
    )


def install_group_reply():
    # Native private methods/serialization are version-sensitive. Never silently
    # apply an untested monkeypatch after a dependency upgrade.
    if version("nonebot-adapter-qq") != "1.7.3":
        from nonebot.log import logger

        logger.warning("OBot QQ group reply patch skipped: recheck adapter version")
        return
    if getattr(Bot.send, "__obot_group_reply__", False):
        return
    original_send, original_group = Bot.send, Bot.send_to_group

    @wraps(original_send)
    async def send(bot, event, message, **kwargs):
        return await original_send(bot, event, group_passive_message(event, message), **kwargs)

    @wraps(original_group)
    async def send_to_group(
        bot, group_openid, message, msg_id=None, msg_seq=None, event_id=None, msg_ref_id=None
    ):
        return await original_group(
            bot,
            group_openid=group_openid,
            message=group_markdown_message(message),
            msg_id=msg_id,
            msg_seq=msg_seq,
            event_id=event_id,
            msg_ref_id=msg_ref_id,
        )

    send.__obot_group_reply__ = True
    Bot.send = send
    Bot.send_to_group = send_to_group
