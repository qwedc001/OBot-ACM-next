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
    description="Codeforces、AtCoder 与牛客公开资料卡及账号绑定",
    usage="/cf user <账号标识>；/cf bind [handle]；/cf unbind；/atc user <账号标识>；/nk user <账号标识>",
    type="application",
    supported_adapters=None,
)


def profile_command(name, label, aliases, example, *subcommands):
    spec = CommandSpec(
        id=f"cp.{name}",
        plugin_id="cp",
        name=name,
        aliases=aliases,
        example_alias=aliases[0],
        summary=f"查询公开账号资料卡（{label}）",
        usage=f"/{name} user <账号标识>",
        examples=(f"/{name} user {example}",),
    )
    matcher = template_command(
        spec,
        Subcommand("user", Args["handle", str], alias=("identity", "id", "card", "info")),
        *subcommands,
        priority=10,
        block=True,
    )

    @matcher.assign("$main")
    async def handle_profile_help(reply: Reply):
        await reply.finish(catalog.command_help(spec))

    return matcher


cf = profile_command(
    "cf",
    "Codeforces",
    ("cf", "codeforces"),
    "tourist",
    Subcommand("bind", Args["handle?", str]),
    Subcommand("unbind"),
    Subcommand(
        "duel",
        Subcommand("challenge", Args["rating_range?", str]["tag?", str], alias=("start",)),
        Subcommand("accept", Args["pair_code", str]),
        Subcommand("finish"),
        Subcommand("giveup", alias=("cancel",)),
    ),
)
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


@cf.assign("bind")
async def handle_codeforces_bind(
    context: CommandContext, services: BotServices, reply: Reply, handle: str | None = None
):
    if handle:
        success, msg, _ = await services.cf_binding.start_binding(context, handle)
    else:
        success, msg = await services.cf_binding.confirm_binding(context)
    await reply.finish(msg)


@cf.assign("unbind")
async def handle_codeforces_unbind(context: CommandContext, services: BotServices, reply: Reply):
    success, msg = await services.cf_binding.unbind(context)
    await reply.finish(msg)


@cf.assign("duel.challenge")
async def handle_codeforces_duel_challenge(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    rating_range: str | None = None,
    tag: str | None = None,
):
    success, msg = await services.cf_duel.challenge(context, rating_range, tag)
    await reply.finish(msg)


@cf.assign("duel.accept")
async def handle_codeforces_duel_accept(
    context: CommandContext, services: BotServices, reply: Reply, pair_code: str
):
    success, msg = await services.cf_duel.accept(context, pair_code)
    await reply.finish(msg)


@cf.assign("duel.finish")
async def handle_codeforces_duel_finish(context: CommandContext, services: BotServices, reply: Reply):
    success, msg = await services.cf_duel.finish(context)
    await reply.finish(msg)


@cf.assign("duel.giveup")
async def handle_codeforces_duel_giveup(context: CommandContext, services: BotServices, reply: Reply):
    success, msg = await services.cf_duel.giveup(context)
    await reply.finish(msg)


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
