"""Public profile templates: native parsing, one handler for each platform."""

from arclet.alconna import Args, Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec, catalog
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.messages import Image

__plugin_meta__ = PluginMetadata(
    name="竞赛账号",
    description="Codeforces、AtCoder 与牛客公开资料卡",
    usage="/cf user handle；/atc user handle；/nk user uid",
    type="application",
    supported_adapters=None,
)


def profile_command(name, label, aliases, example):
    spec = CommandSpec(
        id=f"cp.{name}",
        plugin_id="cp",
        name=name,
        aliases=aliases,
        example_alias=aliases[0],
        summary=f"查询 {label} 公开账号资料卡",
        usage=f"/{name} user 账号标识",
        examples=(f"/{name} user {example}",),
    )
    matcher = template_command(
        spec,
        Subcommand("user", Args["handle", str], alias=("identity", "id", "card", "info")),
        priority=10,
        block=True,
    )

    @matcher.assign("$main")
    async def handle_profile_help(reply: Reply):
        await reply.finish(catalog.command_help(spec))

    return matcher


cf = profile_command("cf", "Codeforces", ("cf", "codeforces"), "tourist")
atc = profile_command("atc", "AtCoder", ("atc", "atcoder"), "tourist")
nk = profile_command("nk", "牛客", ("nk", "nc", "nowcoder"), "123456")


async def send_profile(bot, context, services, reply, platform, handle):
    await reply.finish(
        await services.profiles.reply(
            context, platform, handle, image_available=reply.supports(bot.adapter.get_name(), Image)
        )
    )


@cf.assign("user")
async def handle_codeforces_user(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, handle: str
):
    await send_profile(bot, context, services, reply, "codeforces", handle)


@atc.assign("user")
async def handle_atcoder_user(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, handle: str
):
    await send_profile(bot, context, services, reply, "atcoder", handle)


@nk.assign("user")
async def handle_nowcoder_user(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, handle: str
):
    await send_profile(bot, context, services, reply, "nowcoder", handle)
