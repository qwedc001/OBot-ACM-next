from typing import Annotated

from nonebot.adapters import Bot, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import command
from framework.messages import Image
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="帮助",
    description="所有用户可使用的独立图形帮助",
    usage="/help [text] [模块或指令]",
    type="application",
    supported_adapters=None,
)


help_command = command(
    CommandSpec(
        id="core.help",
        plugin_id="help",
        name="help",
        directory=(),
        aliases=("帮助", "helps"),
        permission=Role.EVERYONE,
        summary="查看当前可用的指令与用法，默认图片",
        usage="/help [text] [模块或指令路径]",
        examples=("/help", "/help maintenance", "/help ping", "/help text"),
    ),
    priority=10,
    block=True,
)


@help_command.handle()
async def help_handler(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    args: Annotated[Message, CommandArg()],
) -> None:
    result = await services.help.reply(
        context,
        args.extract_plain_text().strip(),
        image_available=reply.supports(bot.adapter.get_name(), Image),
    )
    messages = text_chunks(result) if isinstance(result, str) else result
    for message in messages:
        await reply.send(message)
    await reply.finish()
