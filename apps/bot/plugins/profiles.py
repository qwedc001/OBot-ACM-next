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
    description="Codeforces、AtCoder 与牛客公开资料卡、题目检索、抽题、绑定及榜单查询",
    usage="/cf user <账号标识>；/cf prob [tag] [rating] [new]；/cf recent <handle> [count]；/cf tags；/cf bind [handle]；/cf unbind；/atc user <账号标识>；/atc prob [abc|arc|agc] [diff]；/nk user <账号标识>；/nk standing <ID> [handle]",
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


def profile_command(
    name,
    label,
    aliases,
    example,
    *subcommands,
    extra_usage: tuple[str, ...] = (),
    extra_examples: tuple[str, ...] = (),
):
    usage_lines = [f"/{name} user <账号标识>", *extra_usage]
    example_lines = [f"/{name} user {example}", *extra_examples]
    spec = CommandSpec(
        id=f"cp.{name}",
        plugin_id="cp",
        name=name,
        aliases=aliases,
        example_alias=aliases[0],
        summary=f"查询公开账号资料卡（{label}）",
        usage="\n".join(usage_lines),
        examples=tuple(example_lines),
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
    Subcommand(
        "duel",
        Subcommand("challenge", Args["rating_range?", str]["tag?", str], alias=("start",)),
        Subcommand("accept", Args["pair_code", str]),
        Subcommand("finish"),
        Subcommand("giveup", alias=("cancel",)),
    ),
    extra_usage=(
        "/cf bind [handle]",
        "/cf unbind",
        "/cf prob [tag] [rating] [new]",
        "/cf recent <handle> [count]",
        "/cf tags",
        "/cf duel challenge [rating] [tag]",
        "/cf duel accept <pair_code>",
        "/cf duel finish",
        "/cf duel giveup",
    ),
    extra_examples=(
        "/cf bind tourist",
        "/cf prob dp 1800",
        "/cf recent tourist 5",
        "/cf tags",
        "/cf duel challenge 1600-1800 math",
        "/cf duel accept 1234",
    ),
)
atc = profile_command(
    "atc",
    "AtCoder",
    ("atc", "atcoder"),
    "tourist",
    Subcommand("prob", Args["contest_type?", str]["diff?", str], alias=("problem", "pick")),
    extra_usage=("/atc prob [比赛类型] [难度/范围]",),
    extra_examples=("/atc prob abc 800-1200",),
)
nk = profile_command(
    "nk",
    "牛客",
    ("nk", "nc", "nowcoder"),
    "123456",
    Subcommand("standing", Args["contest_id", str]["handle?", str], alias=("standings", "stand", "status")),
    extra_usage=("/nk standing <比赛ID> [用户标识]",),
    extra_examples=("/nk standing 98765", "/nk standing 98765 tourist"),
)


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
            if newer:
                await reply.finish("重复的参数: new")
            newer = True
        elif is_rating_range(a):
            if rating is not None:
                await reply.finish(f"冲突的难度参数: {rating} 与 {a}")
            rating = a
        else:
            if tag is not None:
                await reply.finish(f"冲突的标签参数: {tag} 与 {a}")
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


@atc.assign("prob")
async def handle_atcoder_prob(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    contest_type: str | None = None,
    diff: str | None = None,
):
    # Support either order: /atc prob abc 1200 or /atc prob 1200 abc or /atc prob 1200
    ctype = contest_type
    diff_str = diff
    # Difficulty string can contain digits, e.g. 1200, -800, -800--500, 1200-1600
    # Contest type is one of abc, arc, agc, all
    if ctype and ctype.lower() not in {"abc", "arc", "agc", "all"}:
        if not diff_str:
            diff_str = ctype
            ctype = None
        else:
            # swap
            ctype, diff_str = diff_str, ctype

    try:
        prob = await services.clients.atcoder.pick_problem(ctype, diff_str)
    except ValueError as e:
        await reply.finish(f"参数错误：{e}")
        return
    except Exception:
        await reply.finish("获取 AtCoder 题库数据失败，请稍后重试。")
        return

    if not prob:
        await reply.finish("未找到满足条件的题目，请尝试放宽条件。")
        return

    diff_display = f"\n难度: *{prob['difficulty']}" if prob["difficulty"] is not None else "\n难度: 未评定"
    idx = prob["index"]
    title = prob["title"]
    if idx and title.lower().startswith(f"{idx.lower()}."):
        # title already has prefix like 'A. Problem Name'
        prob_header = title
    elif idx:
        prob_header = f"{idx}. {title}"
    else:
        prob_header = title

    msg = (
        f"[AtCoder] 随机选题\n\n"
        f"{prob_header}\n"
        f"比赛: {prob['contest_id'].upper()}"
        f"{diff_display}\n"
        f"链接: {prob['url']}"
    )
    await reply.finish(msg)


@nk.assign("user")
async def handle_nowcoder_user(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, handle: str
):
    await send_profile(bot, context, services, reply, "nowcoder", handle)


@nk.assign("standing")
async def handle_nowcoder_standing(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    contest_id: str,
    handle: str | None = None,
):
    cid = contest_id.strip()
    if not cid.isdigit():
        await reply.finish("比赛 ID 必须为数字，例如 /nk standing 81596")
        return

    try:
        data = await services.clients.nowcoder.standings(cid, search_user_name=handle or "")
    except Exception:
        await reply.finish("获取牛客比赛榜单失败，请检查比赛 ID 是否正确或稍后重试。")
        return

    rank_data = data.get("rank_data", [])
    rank_type = data.get("rank_type", "ICPC")

    header = f"[NowCoder] 比赛 {cid} 榜单查询"
    if handle:
        header = f"[NowCoder] 比赛 {cid} 选手 {handle} 榜单查询"

    if not rank_data:
        if handle:
            await reply.finish(f"{header}\n\n未找到选手 {handle} 的榜单数据。")
        else:
            await reply.finish(f"{header}\n\n暂无榜单数据。")
        return

    entries = [
        services.clients.nowcoder.format_standing_entry(item, rank_type=rank_type)
        for item in rank_data[:10]
    ]
    content = f"{header}\n\n" + "\n\n".join(entries)
    if len(rank_data) > 10:
        content += "\n\n最多展示 10 条榜单信息"

    await reply.finish(content)
