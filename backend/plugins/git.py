"""Native superuser command templates for the local supervisor."""

from dataclasses import replace

from arclet.alconna import Args, Subcommand
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.clients.reply import DeliveryFailed
from framework.clients.updater import SupervisorUnavailable
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.integration.platforms import get_platform
from framework.messages import Markdown
from framework.services.updates import report_pages

__plugin_meta__ = PluginMetadata(
    name="代码更新",
    description="超管通过独立守护进程更新和重启机器人",
    usage="/git [status|fetch|log|pull|restart|plog]；/update [子指令] [分支名]",
    type="application",
)

git = template_command(
    CommandSpec(
        "git.manage",
        "git",
        "git",
        "查看代码与运行状态，更新或重启机器人",
        directory=(),
        permission=Role.SUPERUSER,
        usage="/git [status|fetch|log|pull|restart|plog]",
        examples=("/git status", "/git pull", "/git plog"),
    ),
    *(Subcommand(name) for name in ("status", "fetch", "log", "pull", "restart", "plog")),
    priority=5,
    block=True,
)
update = template_command(
    CommandSpec(
        "git.update",
        "git",
        "update",
        "更新稳定版、测试分支或恢复测试前备份",
        directory=(),
        permission=Role.SUPERUSER,
        usage="/update [子指令] [分支名]",
        examples=("/update", "/update test codex/feature", "/update restore"),
    ),
    Subcommand("test", Args["branch", str]),
    Subcommand("restore"),
    priority=5,
    block=True,
)


async def inspect(context, services, reply, action):
    try:
        result = await services.updates.execute(context, action)
    except SupervisorUnavailable as error:
        await reply.finish(str(error))
    return result


@git.assign("$main")
async def handle_git_help(reply: Reply):
    await reply.finish(
        "代码更新\n"
        "参数：<必填>，[可选]，a|b 表示任选其一\n"
        "/git status — 查看运行版本\n"
        "/git fetch — 检查远端版本\n"
        "/git log — 查看提交记录\n"
        "/git pull — 更新稳定版，与 /update 相同\n"
        "/update test <分支名> — 部署测试分支\n"
        "/update restore — 恢复测试前的代码与数据\n"
        "/git restart — 重启机器人\n"
        "/git plog — 查看上次更新报告"
    )


@git.assign("status")
async def handle_git_status(context: CommandContext, services: BotServices, reply: Reply):
    result = await inspect(context, services, reply, "status")
    operation = result.get("operation") or {}
    baseline = result.get("baseline") or {}
    await reply.finish(
        f"运行：{'正常' if result['running'] else '停止'}\n分支：{result.get('branch', '未知')}\n版本：{str(result['commit'])[:12]}\n稳定版备份：{str(baseline.get('commit', '无'))[:12]}\n更新：{operation.get('phase', '无')} / {operation.get('outcome', '无')}"
    )


@git.assign("fetch")
async def handle_git_fetch(context: CommandContext, services: BotServices, reply: Reply):
    result = await inspect(context, services, reply, "fetch")
    await reply.finish(f"远端版本：{result[:12]}")


@git.assign("log")
async def handle_git_log(context: CommandContext, services: BotServices, reply: Reply):
    await reply.finish(await inspect(context, services, reply, "log"))


@git.assign("plog")
async def handle_git_plog(context: CommandContext, services: BotServices, reply: Reply):
    result = await inspect(context, services, reply, "status")
    if not result.get("operation"):
        await reply.finish("暂无更新记录。")
    for page in report_pages(result["operation"]):
        try:
            await reply.send(Markdown(content=page))
        except DeliveryFailed:
            await reply.send(page)
    await reply.finish()


async def request_update(bot, event, context, services, reply, action, *, branch=None):
    await services.updates.authorize(context)
    platform = get_platform(bot)
    incoming = platform.normalize(bot, event)
    target = replace(platform.send_target(bot, context.scene), reply_id=incoming.event_id, reply_sequence=2)
    # Await delivery before the daemon is allowed to stop this process.
    message = {
        "test": "准备更新测试分支；保留测试前稳定版及完整数据，启动失败恢复该备份。",
        "restore": "准备更新为测试前稳定版并恢复当时的数据；测试期间的数据会另外备份。",
    }.get(action, "准备更新并重启；启动失败会自动恢复。")
    await reply.send(message + "结果将发回本会话，也可使用 /git plog 查看。")
    try:
        await services.updates.execute(context, action, target, branch=branch)
    except SupervisorUnavailable as error:
        await reply.finish(str(error))
    await reply.finish()


@git.assign("pull")
async def handle_git_pull(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply
):
    await request_update(bot, event, context, services, reply, "update")


@update.assign("$main")
async def handle_update(bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply):
    await request_update(bot, event, context, services, reply, "update")


@update.assign("test")
async def handle_update_test(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply, branch: str
):
    await request_update(bot, event, context, services, reply, "test", branch=branch)


@update.assign("restore")
async def handle_update_restore(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply
):
    await request_update(bot, event, context, services, reply, "restore")


@git.assign("restart")
async def handle_restart(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply
):
    await request_update(bot, event, context, services, reply, "restart")
