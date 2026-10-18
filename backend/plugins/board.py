"""Declarative board templates, each assigned to a single-purpose handler."""

from dataclasses import asdict
from functools import wraps
from typing import Literal

from arclet.alconna import Args, Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.board import BoardError
from framework.catalog import CommandDirectory, CommandSpec, catalog
from framework.clients.jobs import QueueClosed, QueueFull
from framework.contracts import SceneKind
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.integration.platforms import get_platform
from framework.messages import Image, Message, Text
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="训练榜单",
    description="HydroOJ 训练榜单和一次性网页绑定",
    usage="/board bind <配置名>；/board today [配置名]",
    type="application",
    supported_adapters=None,
)
catalog.register_directory(CommandDirectory(("cp",), "算法竞赛"))
board = template_command(
    CommandSpec(
        id="cp.board",
        plugin_id="cp",
        name="board",
        aliases=("board",),
        example_alias="board",
        summary="管理 HydroOJ 绑定并生成训练榜单；不带参数查看子指令",
        menu_label="训练榜单",
        usage="/board [子指令] [参数]",
        examples=("/board", "/board today xxxOJ", "/board verdict WA xxxOJ"),
    ),
    Subcommand("bind", Args["name", str]),
    Subcommand("confirm", Args["name", str]),
    Subcommand("unbind", Args["name", str]),
    Subcommand("list"),
    Subcommand("version"),
    Subcommand(
        "push", *(Subcommand(action, Args["name", str, None]) for action in ("on", "off", "status", "test"))
    ),
    Subcommand("starred", Args["setting", Literal["on", "off", "toggle"], "toggle"]["name", str, None]),
    Subcommand("today", Args["name", str, None]),
    Subcommand("yesterday", Args["name", str, None]),
    Subcommand("history", Args["day", str]["name", str, None]),
    Subcommand("verdict", Args["verdict", str]["name", str, None]),
    Subcommand(
        "user",
        Subcommand("id", Args["uid", str]["name", str, None]),
        Subcommand("name", Args["username", str]["name", str, None]),
    ),
    priority=10,
    block=True,
)


def board_errors(handler):
    """Share the domain error boundary; parsing and dispatch stay in Alconna."""

    @wraps(handler)
    async def guarded(*args, **kwargs):
        reply = kwargs["reply"]
        try:
            await handler(*args, **kwargs)
        except BoardError as error:
            await reply.finish(str(error))
        except (TimeoutError, QueueFull, QueueClosed):
            await reply.finish("榜单服务繁忙或查询超时，请稍后重试。")
        except NotImplementedError:
            await reply.finish("当前平台不支持榜单定时推送。")

    return guarded


@board.assign("$main")
async def handle_board_help(context: CommandContext, services: BotServices, reply: Reply):
    lines = [
        "训练榜单",
        "参数：<必填>，[可选]，a|b 表示任选其一",
        "/board list — 查看当前对话的榜单配置",
        "/board today [配置名] — 生成今日榜单",
        "/board yesterday [配置名] — 生成昨日榜单",
        "/board history <日期> [配置名] — 生成往期榜单，日期格式为 YYYY-MM-DD",
        "/board verdict <评测结果> [配置名] — 生成评测榜单，如 WA",
        "/board version — 查看生成器版本",
    ]
    if context.scene.kind in {SceneKind.C2C, SceneKind.DIRECT}:
        lines.extend(
            (
                "/board user id <用户ID> [配置名] — 按 ID 查询用户",
                "/board user name <用户名> [配置名] — 按用户名查询用户",
            )
        )
    if await services.boards.can_manage(context):
        lines.extend(
            (
                "/board bind <配置名> — 获取一次性绑定链接",
                "/board confirm <配置名> — 确认群绑定，需由发起人执行",
                "/board unbind <配置名> — 解除当前对话的榜单绑定",
                "/board push <on|off> [配置名] — 开启或关闭每日推送",
                "/board push status [配置名] — 查看推送与调度状态",
                "/board push test [配置名] — 测试主动发送昨日榜单",
                "/board starred [on|off|toggle] [配置名] — 设置打星用户显示，默认切换",
            )
        )
    await reply.finish("\n".join(lines))


@board.assign("bind")
@board_errors
async def handle_board_bind(context: CommandContext, services: BotServices, reply: Reply, name: str):
    link = await services.boards.issue(context, name)
    minutes = (services.settings.obot_board_bind_ttl + 59) // 60
    await reply.finish(
        f"请在网页填写 HydroOJ 地址与管理员账号。链接约 {minutes} 分钟内有效，成功提交后失效；重新获取会使旧链接失效。\n"
        + link
    )


@board.assign("confirm")
@board_errors
async def handle_board_confirm(context: CommandContext, services: BotServices, reply: Reply, name: str):
    await reply.finish(await services.boards.confirm(context, name))


@board.assign("unbind")
@board_errors
async def handle_board_unbind(context: CommandContext, services: BotServices, reply: Reply, name: str):
    await reply.finish(await services.boards.unbind(context, name))


@board.assign("list")
async def handle_board_list(context: CommandContext, services: BotServices, reply: Reply):
    entries = await services.boards.configs(context)
    await reply.finish(
        "当前可用榜单：\n" + "\n".join(config.name for config in entries)
        if entries
        else "当前对话尚未绑定榜单。"
    )


@board.assign("version")
async def handle_board_version(services: BotServices, reply: Reply):
    await reply.finish("Peeper-Board-Generator " + await services.clients.peeper.version())


@board.assign("push.on")
@board_errors
async def handle_board_push_on(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    target = asdict(get_platform(bot).send_target(bot, context.scene))
    await reply.finish(await services.boards.push(context, True, name, target))


@board.assign("push.off")
@board_errors
async def handle_board_push_off(
    context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    await reply.finish(await services.boards.push(context, False, name, None))


@board.assign("push.status")
@board_errors
async def handle_board_push_status(
    context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    await reply.finish(await services.boards.push_status(context, name))


@board.assign("push.test")
@board_errors
async def handle_board_push_test(
    context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    await services.boards.require_manage(context)
    await reply.send("正在测试主动推送，这会向已开启推送的当前对话发送昨日榜单。")
    await reply.finish(await services.boards.test_push(context, name))


@board.assign("starred")
@board_errors
async def handle_board_starred(
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    setting: str = "toggle",
    name: str | None = None,
):
    enabled = {"on": True, "off": False, "toggle": None}[setting]
    await reply.finish(await services.boards.starred(context, enabled, name))


async def send_board(bot, context, services, reply, mode, name=None, *, day=None, verdict="AC"):
    if not reply.supports(bot.adapter.get_name(), Image):
        raise BoardError("当前平台不支持图片榜单。")
    await reply.send("正在获取榜单并生成图片，请稍候。")
    artifact = await services.boards.image(context, mode, name, day=day, verdict=verdict)
    await reply.finish(
        Message(Text("训练榜单"), Image(await services.clients.artifacts.read(context.actor, artifact.id)))
    )


@board.assign("today")
@board_errors
async def handle_board_today(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    await send_board(bot, context, services, reply, "now", name)


@board.assign("yesterday")
@board_errors
async def handle_board_yesterday(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, name: str | None = None
):
    await send_board(bot, context, services, reply, "full", name)


@board.assign("history")
@board_errors
async def handle_board_history(
    bot: Bot, context: CommandContext, services: BotServices, reply: Reply, day: str, name: str | None = None
):
    await send_board(bot, context, services, reply, "full", name, day=day)


@board.assign("verdict")
@board_errors
async def handle_board_verdict(
    bot: Bot,
    context: CommandContext,
    services: BotServices,
    reply: Reply,
    verdict: str,
    name: str | None = None,
):
    await send_board(bot, context, services, reply, "now", name, verdict=verdict)


async def send_user(context, services, reply, kind, value, name):
    for chunk in text_chunks(await services.boards.user(context, kind, value, name)):
        await reply.send(chunk)
    await reply.finish()


@board.assign("user.id")
@board_errors
async def handle_board_user_id(
    context: CommandContext, services: BotServices, reply: Reply, uid: str, name: str | None = None
):
    await send_user(context, services, reply, "id", uid, name)


@board.assign("user.name")
@board_errors
async def handle_board_user_name(
    context: CommandContext, services: BotServices, reply: Reply, username: str, name: str | None = None
):
    await send_user(context, services, reply, "name", username, name)
