"""Public contest queries: native templates, one handler for each operation."""

from typing import Literal

from arclet.alconna import Args, MultiVar, Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.messages import Image

__plugin_meta__ = PluginMetadata(
    name="比赛查询",
    description="今日、近日比赛聚合与详情",
    usage="/contest [recent|today] [all|cf|atc|nk|manual]；/contest detail <关键词>",
    type="application",
    supported_adapters=None,
)

Source = Literal["all", "cf", "atc", "nk", "manual"]
contests = template_command(
    CommandSpec(
        id="cp.contest",
        plugin_id="cp",
        name="contest",
        summary="查询今日、近日比赛或单场详情，默认返回图片",
        aliases=("contest", "contests", "比赛", "近日比赛", "最近的比赛"),
        example_alias="contest",
        usage="/contest [recent|today|detail] [参数]",
        examples=("/contest", "/contest today", "/contest recent cf", "/contest detail ABC"),
    ),
    Subcommand("recent", Args["source", Source, "all"]),
    Subcommand("today", Args["source", Source, "all"]),
    Subcommand("detail", Args["query", MultiVar(str)]),
    priority=10,
    block=True,
)
today = template_command(
    CommandSpec(
        id="cp.today_contests",
        plugin_id="cp",
        name="today_contests",
        summary="查询今日进行的比赛，包含跨日比赛",
        aliases=("今日比赛", "今天比赛", "今天的比赛", "今日的比赛"),
        example_alias="今日比赛",
        usage="/today_contests [all|cf|atc|nk|manual]",
        examples=("/today_contests",),
    ),
    Args["source", Source, "all"],
    priority=9,
    block=True,
)


async def send_list(bot, context, services, reply, *, today=False, source="all"):
    messages = await services.contests.listing(
        context,
        today=today,
        source=source,
        image_available=reply.supports(bot.adapter.get_name(), Image),
    )
    for message in messages:
        await reply.send(message)
    await reply.finish()


@contests.assign("$main")
async def handle_contests(bot: Bot, context: CommandContext, services: BotServices, reply: Reply):
    await send_list(bot, context, services, reply)


@contests.assign("recent")
async def handle_recent(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, source: str = "all"
):
    await send_list(bot, context, services, reply, source=source)


@contests.assign("today")
async def handle_today(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, source: str = "all"
):
    await send_list(bot, context, services, reply, today=True, source=source)


@today.assign("$main")
async def handle_today_alias(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, source: str = "all"
):
    await send_list(bot, context, services, reply, today=True, source=source)


@contests.assign("detail")
async def handle_detail(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, query: tuple[str, ...]
):
    messages = await services.contests.detail(
        context, " ".join(query), image_available=reply.supports(bot.adapter.get_name(), Image)
    )
    for message in messages:
        await reply.send(message)
    await reply.finish()
