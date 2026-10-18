"""Public private login and group-admin login, with links delivered only in private."""

from arclet.alconna import Args
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.clients.reply import DeliveryFailed
from framework.contracts import AccessDenied, Role, SceneKind
from framework.dashboard import DashboardError
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.integration.platforms import get_platform

__plugin_meta__ = PluginMetadata(
    name="网页入口",
    description="使用 QQ 身份登录 OBot 网页",
    usage="私聊发送 /dashboard；群管理员也可在群内发送，由机器人私聊返回本群入口",
    type="application",
    supported_adapters=None,
)
login = template_command(
    CommandSpec(
        id="dashboard",
        plugin_id="dashboard",
        name="dashboard",
        directory=(),
        scopes=frozenset({SceneKind.C2C, SceneKind.DIRECT, SceneKind.GROUP}),
        scene_permissions=((SceneKind.GROUP, Role.GROUP_ADMIN),),
        summary="私聊登录网页；群管可在群内获取本群榜单入口",
        usage="/dashboard",
        examples=("/dashboard",),
        aliases=("面板",),
    ),
    priority=10,
    block=True,
)


@login.assign("$main")
async def handle_dashboard(bot: Bot, context: CommandContext, services: BotServices, reply: Reply):
    try:
        target = None
        if context.scene.kind == SceneKind.GROUP:
            platform = get_platform(bot)
            recipient = await services.dashboard.private_recipient(context, platform.private_identity_kinds)
            target = platform.private_target(bot, recipient)
        link = await services.dashboard.issue(context)
    except DashboardError as error:
        await reply.finish(str(error))
    text = (
        f"点击链接登录{'本群管理工作台' if target else '网页'}（{services.settings.obot_web_login_ttl} 秒内有效，仅可使用一次，请勿转发）：\n"
        + link
    )
    if target:
        try:
            await reply.send_to(text, target, bot=bot)
        except DeliveryFailed as error:
            await services.dashboard.revoke_link(link)
            detail = f"（平台错误码 {error.code}）" if error.code is not None else ""
            await reply.finish(
                f"私聊发送失败{detail}。请先添加机器人并允许私聊主动消息，再重试；登录链接不会发送到群内。"
            )
        await reply.finish("本群管理链接已私聊发送，请查看与机器人的私聊。")
    await reply.finish(text)


verify = template_command(
    CommandSpec(
        id="dashboard.verify",
        plugin_id="dashboard",
        name="dashboard-verify",
        directory=(),
        permission=Role.GROUP_ADMIN,
        scopes=frozenset({SceneKind.GROUP}),
        summary="在群内验证网页的群榜单管理权限",
        usage="/dashboard-verify 验证码",
        examples=("/dashboard-verify <网页提供的验证码>",),
    ),
    Args["code", str],
    priority=9,
    block=True,
)


@verify.assign("$main")
async def handle_group_verify(code: str, context: CommandContext, services: BotServices, reply: Reply):
    try:
        result = await services.scene_access.verify(context, code)
    except DashboardError as error:
        await reply.finish(str(error))
    except AccessDenied:
        await reply.finish("无法验证本群管理身份，请由群主或群管理员在群内执行。")
    await reply.finish(result)
