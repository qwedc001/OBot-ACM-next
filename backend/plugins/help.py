"""Independent, permission-filtered help with explicit display templates."""

from arclet.alconna import Args, Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.messages import Image
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="帮助",
    description="所有用户可使用的独立图形帮助",
    usage="/help [text] [模块或指令]",
    type="application",
    supported_adapters=None,
)
help_command = template_command(
    CommandSpec(
        id="core.help",
        plugin_id="help",
        name="help",
        directory=(),
        aliases=("帮助", "helps"),
        permission=Role.EVERYONE,
        summary="查看当前可用的指令与用法，默认图片",
        menu_label="查看帮助",
        usage="/help [text] [模块或指令路径]",
        examples=("/help", "/help maintenance", "/help ping", "/help text"),
    ),
    Args["query", str, ""],
    Subcommand("text|t|文字|TEXT|T", Args["query", str, ""]),
    Subcommand("render|draw|r|d|RENDER|DRAW|R|D", Args["query", str, ""]),
    priority=10,
    block=True,
)


async def send_help(bot, context, services, reply, query, draw):
    result = await services.help.reply(
        context, query, draw=draw, image_available=reply.supports(bot.adapter.get_name(), Image)
    )
    for message in text_chunks(result) if isinstance(result, str) else result:
        await reply.send(message)
    await reply.finish()


@help_command.assign("$main")
async def handle_help(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, query: str = ""
):
    await send_help(bot, context, services, reply, query, True)


@help_command.assign("text")
async def handle_help_text(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, query: str = ""
):
    await send_help(bot, context, services, reply, query, False)


@help_command.assign("render")
async def handle_help_render(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, query: str = ""
):
    await send_help(bot, context, services, reply, query, True)
