"""Translate portable content to the official QQ adapter's native segments."""

import asyncio
from pathlib import Path

from nonebot.adapters import Bot, Event
from nonebot.adapters.qq import Message as QQMessage
from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.event import (
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    GuildMessageEvent,
    MessageEvent,
)
from nonebot.adapters.qq.models import (
    Action,
    InlineKeyboard,
    InlineKeyboardRow,
    MessageKeyboard,
    MessageMarkdown,
    MessageMarkdownParams,
    Modal,
    Permission,
    RenderData,
)
from nonebot.adapters.qq.models import (
    Button as QQButton,
)

from ...messages import Image, Keyboard, Markdown, Message, SendTarget, Text


async def build_qq_reply(event: Event | SendTarget, message: Message, at_sender: bool | None) -> QQMessage:
    if at_sender is None:
        at_sender = isinstance(event, (GroupMessageCreateEvent, GuildMessageEvent)) and not isinstance(
            event, DirectMessageCreateEvent
        )
    if isinstance(event, SendTarget):
        if event.kind not in {"c2c", "group", "channel", "dms"}:
            raise ValueError("Unsupported QQ target kind")
        if at_sender:
            raise ValueError("Active delivery has no sender to mention")
    elif not isinstance(event, MessageEvent):
        raise ValueError("QQ replies require a message event")
    images = sum(isinstance(part, Image) for part in message.parts)
    markdowns = sum(isinstance(part, Markdown) for part in message.parts)
    keyboards = sum(isinstance(part, Keyboard) for part in message.parts)
    if images > 1 or markdowns > 1 or keyboards > 1 or images and (markdowns or keyboards):
        raise ValueError(
            "QQ requires separate messages for multiple media or image/Markdown/keyboard combinations"
        )
    if keyboards and not markdowns:
        raise ValueError("QQ keyboards must accompany Markdown")
    segments = []
    if at_sender:
        segments.extend((MessageSegment.mention_user(event.author.id), MessageSegment.text(" ")))
    for part in message.parts:
        if isinstance(part, Text):
            segments.append(MessageSegment.text(part.content))
        elif isinstance(part, Image):
            if isinstance(part.source, str):
                segments.append(MessageSegment.image(part.source))
            else:
                data = (
                    await asyncio.to_thread(part.source.read_bytes)
                    if isinstance(part.source, Path)
                    else part.source
                )
                filename = part.filename or (part.source.name if isinstance(part.source, Path) else None)
                segments.append(MessageSegment.file_image(data, file_name=filename))
        elif isinstance(part, Markdown):
            segments.append(
                MessageSegment.markdown(
                    MessageMarkdown(
                        content=part.content,
                        custom_template_id=part.template_id,
                        params=[
                            MessageMarkdownParams(key=param.key, values=list(param.values))
                            for param in part.params
                        ]
                        or None,
                    )
                )
            )
        elif isinstance(part, Keyboard):
            if len(part.rows) > 5 or any(len(row) > 5 for row in part.rows):
                raise ValueError("QQ keyboards allow at most five rows and five buttons per row")
            rows = [
                InlineKeyboardRow(
                    buttons=[
                        QQButton(
                            id=button.id,
                            group_id=button.group_id,
                            render_data=RenderData(
                                label=button.label,
                                visited_label=button.visited_label or button.label,
                                style=button.style,
                            ),
                            action=Action(
                                type=int(button.action),
                                data=button.data,
                                enter=button.enter,
                                reply=button.reply,
                                anchor=button.anchor,
                                unsupport_tips=button.unsupported_tips,
                                modal=Modal(
                                    content=button.modal.content,
                                    confirm_text=button.modal.confirm_text,
                                    cancel_text=button.modal.cancel_text,
                                )
                                if button.modal
                                else None,
                                permission=Permission(
                                    type=int(button.permission.access),
                                    specify_user_ids=list(button.permission.user_ids) or None,
                                    specify_role_ids=list(button.permission.role_ids) or None,
                                ),
                            ),
                        )
                        for button in row
                    ]
                )
                for row in part.rows
            ]
            segments.append(
                MessageSegment.keyboard(
                    MessageKeyboard(
                        id=part.template_id,
                        content=InlineKeyboard(rows=rows) if rows else None,
                    )
                )
            )
    return QQMessage(segments)


async def send_qq_to(bot: Bot, target: SendTarget, message: QQMessage):
    # Route through high-level SDK methods so uploads and msg_type remain SDK-owned.
    if target.kind == "c2c":
        return await bot.send_to_c2c(openid=target.id, message=message)
    if target.kind == "group":
        return await bot.send_to_group(group_openid=target.id, message=message)
    if target.kind == "channel":
        return await bot.send_to_channel(channel_id=target.id, message=message)
    if target.kind == "dms":
        return await bot.send_to_dms(guild_id=target.id, message=message)
    raise ValueError("Unsupported QQ target kind")
