"""The web login entry is an independent public command; links are sent only in private."""

from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.contracts import SceneKind
from framework.dashboard import DashboardError
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command

__plugin_meta__ = PluginMetadata(
    name="网页入口",
    description="使用 QQ 身份登录 OBot 网页",
    usage="私聊发送 /dashboard",
    type="application",
    supported_adapters=None,
)
login = template_command(
    CommandSpec(
        id="dashboard",
        plugin_id="dashboard",
        name="dashboard",
        directory=(),
        scopes=frozenset({SceneKind.C2C, SceneKind.DIRECT}),
        summary="私聊获取一次性网页登录链接",
        usage="/dashboard",
        examples=("/dashboard",),
        aliases=("面板",),
    ),
    priority=10,
    block=True,
)


@login.assign("$main")
async def handle_dashboard(context: CommandContext, services: BotServices, reply: Reply):
    try:
        link = await services.dashboard.issue(context)
    except DashboardError as error:
        await reply.finish(str(error))
    await reply.finish(
        f"点击链接登录网页（{services.settings.obot_web_login_ttl} 秒内有效，仅可使用一次，请勿转发）：\n"
        + link
    )
