"""Superuser-only native Markdown and keyboard diagnostics."""

from typing import Annotated

from nonebot.adapters import Bot, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.clients.reply import DeliveryFailed
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import command
from framework.messages import Keyboard, Markdown

__plugin_meta__ = PluginMetadata(
    name="发送能力调试",
    description="仅超级管理员可用的 Markdown 与交互按钮测试",
    usage="/debug [markdown|button]",
    type="application",
    supported_adapters=None,
)

debug = command(
    CommandSpec(
        id="core.debug",
        plugin_id="debug",
        name="debug",
        directory=(),
        summary="测试 Markdown 与按钮；markdown 测排版，button 测交互",
        permission=Role.SUPERUSER,
        usage="/debug [markdown|button]",
        examples=("/debug", "/debug markdown", "/debug button"),
    ),
    priority=10,
    block=True,
)


@debug.handle()
async def debug_handler(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    args: Annotated[Message, CommandArg()],
) -> None:
    choice = args.extract_plain_text().strip().casefold()
    if choice not in {"markdown", "button"}:
        menu = await services.help.reply(context, "text debug", image_available=False)
        prefix = "" if not choice else "未知 debug 子指令，请使用以下用法。\n\n"
        await reply.finish(prefix + menu)
    required = (Markdown, Keyboard) if choice == "button" else (Markdown,)
    if not all(reply.supports(bot.adapter.get_name(), part) for part in required):
        await reply.finish("当前平台未实现该消息类型，无法进行这项发送测试。")
    content = await (services.debug.button() if choice == "button" else services.debug.markdown())
    try:
        await reply.send(content)
    except DeliveryFailed:
        await reply.finish(
            "测试消息发送失败，尚不能确认该能力可用。\n"
            "请检查 QQ 开放平台是否开通被动原生 Markdown 权限；"
            + (
                "按钮测试还需要自定义按钮能力，链接域名 q.qq.com 需按平台要求配置。"
                if choice == "button"
                else "本 Markdown 样例不含外部链接或图片。"
            )
        )
    await reply.finish()
