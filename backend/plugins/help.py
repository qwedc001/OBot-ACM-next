"""Independent, permission-filtered help with explicit display templates."""

from arclet.alconna import Args, Subcommand
from nonebot import get_driver, on_message
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State

from framework.catalog import CommandSpec
from framework.contracts import Role
from framework.integration.command_context import COMMAND_MATCHES, BotServices, CommandContext, Reply
from framework.integration.commands import supported_message, template_command
from framework.integration.permissions import command_check
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
        summary="查看当前可用指令及用法，默认返回图片",
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


async def unmatched_command(bot: Bot, event: Event, state: T_State) -> bool:
    if not await supported_message(bot, event) or not event.is_tome() or state.get(COMMAND_MATCHES):
        return False
    text = event.get_plaintext().lstrip()
    for prefix in sorted(get_driver().config.command_start, key=len, reverse=True):
        # An empty configured prefix must not turn normal chat into errors.
        if prefix and text.startswith(prefix):
            # Echo only a bounded command token, not arbitrary arguments.
            head = text[len(prefix) :].split(maxsplit=1)
            query = "".join(char for char in (head[0] if head else "") if char.isprintable())[:80]
            state["unmatched_command"] = (prefix, query)
            return True
    return False


# Existing command errors/permission denials block propagation. This runs last
# and never auto-executes a fuzzy suggestion or publishes another help command.
fallback = on_message(rule=unmatched_command, priority=1000, block=True)


@fallback.handle()
async def handle_unmatched(bot: Bot, event: Event, state: T_State, services: BotServices, reply: Reply):
    await reply.finish(await services.help.unmatched(command_check(bot, event), *state["unmatched_command"]))
