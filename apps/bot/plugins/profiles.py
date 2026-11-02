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
    description="Codeforces、AtCoder 与牛客公开资料卡、题目检索及账号绑定",
    usage="/cf user <账号标识>；/cf prob [tag] [rating] [new]；/cf recent <handle> [count]；/cf tags；/cf bind [handle]；/cf unbind；/atc user <账号标识>；/nk user <账号标识>",
    type="application",
    supported_adapters=None,
)


def is_rating_range(val: str) -> bool:
    """Check if string is a numeric rating or rating range like 1500 or 1400-1600."""
    val = val.strip()
    if val.isdigit():
        return True
    if "-" in val:
        parts = val.split("-", 1)
        return parts[0].isdigit() and parts[1].isdigit()
    return False


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
        "prob",
        Args["arg1?", str]["arg2?", str]["arg3?", str],
        alias=("pick", "problem"),
    ),
    Subcommand("recent", Args["handle", str]["count?", int]),
    Subcommand("tags", alias=("tag",)),
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


@cf.assign("prob")
async def handle_codeforces_prob(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    arg1: str | None = None,
    arg2: str | None = None,
    arg3: str | None = None,
):
    args = [a for a in (arg1, arg2, arg3) if a is not None]
    tag, rating, newer = None, None, False
    for a in args:
        if a.lower() == "new":
            newer = True
        elif is_rating_range(a):
            rating = a
        else:
            tag = a

    success, msg, _ = await services.cf_problem.pick_problem(tag=tag, rating_range=rating, newer_only=newer)
    await reply.finish(msg)


@cf.assign("recent")
async def handle_codeforces_recent(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    handle: str,
    count: int | None = None,
):
    actual_count = count if count is not None else 5
    success, msg = await services.cf_problem.get_recent_submissions(handle, actual_count)
    await reply.finish(msg)


@cf.assign("tags")
async def handle_codeforces_tags(services: BotServices, reply: Reply):
    try:
        tags = await services.cf_problem.get_all_tags()
    except Exception:
        await reply.finish("暂无法获取标签列表，请稍后重试。")
    if not tags:
        await reply.finish("暂无法获取标签列表，请稍后重试。")
    msg = "[Codeforces] 题目标签:\n" + ", ".join(tags)
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
