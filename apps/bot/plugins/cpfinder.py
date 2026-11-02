"""CPCFinder plugin: query XCPC contestants, schools, awards and team records."""

from arclet.alconna import Args, StrMulti, Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec, catalog
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.messages import Image

__plugin_meta__ = PluginMetadata(
    name="XCPC 选手与战绩查询",
    description="CPCFinder XCPC 选手档案、获奖历史与学校/队伍战绩查询",
    usage="/cpc <姓名> [学校]；/cpc school <学校>；/cpc team <队伍名>",
    type="application",
    supported_adapters=None,
)

cpc_spec = CommandSpec(
    id="cp.cpc",
    plugin_id="cp",
    name="cpc",
    aliases=("cpc", "cpcfinder", "xcpc", "xcpcfinder"),
    example_alias="cpc",
    summary="查询 XCPC 选手档案、奖牌与高校战绩",
    usage="/cpc <姓名> [学校] 或 /cpc school <学校名> /cpc team <队伍名>",
    examples=(
        "/cpc 李欣鹏",
        "/cpc 蒋凌宇 北京大学",
        "/cpc school 北京大学",
        "/cpc team 茉莉雨",
    ),
)

cpc_cmd = template_command(
    cpc_spec,
    Subcommand("school", Args["school_name", StrMulti], alias=("sch", "univ")),
    Subcommand("team", Args["team_name", StrMulti], alias=("tm",)),
    Args["query?", str]["school?", StrMulti],
    priority=10,
    block=True,
)


@cpc_cmd.assign("$main")
async def handle_cpc_main(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    query: str | None = None,
    school: str | None = None,
):
    if not query:
        await reply.finish(catalog.command_help(cpc_spec))

    image_available = reply.supports(bot.adapter.get_name(), Image)
    res = await services.cpcfinder.reply(context, query, school, mode="auto", image_available=image_available)
    await reply.finish(res)


@cpc_cmd.assign("school")
async def handle_cpc_school(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    school_name: str,
):
    image_available = reply.supports(bot.adapter.get_name(), Image)
    res = await services.cpcfinder.reply(context, school_name, mode="school", image_available=image_available)
    await reply.finish(res)


@cpc_cmd.assign("team")
async def handle_cpc_team(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    team_name: str,
):
    image_available = reply.supports(bot.adapter.get_name(), Image)
    res = await services.cpcfinder.reply(context, team_name, mode="team", image_available=image_available)
    await reply.finish(res)
