from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandDirectory, CommandSpec, catalog
from framework.contracts import Role, SceneKind
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command as command
from framework.integration.platforms import get_platform

__plugin_meta__ = PluginMetadata(
    name="维护与诊断",
    description="仅机器人管理员可用的连通检查、身份与运行状态",
    usage="/help maintenance /ping /whoami /whoisthis @某人 /status",
    type="application",
    supported_adapters=None,
)

catalog.register_directory(CommandDirectory(("maintenance",), "维护与诊断", aliases=("maintainence",)))


ping = command(
    CommandSpec(
        id="core.ping",
        plugin_id="maintenance",
        name="ping",
        aliases=("ping",),
        example_alias="ping",
        summary="检查机器人是否能够回复",
        menu_label="连通检查",
        permission=Role.SUPERUSER,
        examples=("/ping",),
    ),
    priority=10,
    block=True,
)


@ping.assign("$main")
async def ping_handler(services: BotServices, reply: Reply) -> None:
    await reply.finish(await services.maintenance.ping())


identity = command(
    CommandSpec(
        id="core.identity",
        plugin_id="maintenance",
        name="whoami",
        aliases=("whoami", "我的身份"),
        example_alias="whoami",
        summary="查看自己的机器人身份与当前对话场景",
        menu_label="我的身份",
        permission=Role.SUPERUSER,
        examples=("/whoami",),
    ),
    priority=10,
    block=True,
)


@identity.assign("$main")
async def identity_handler(context: CommandContext, services: BotServices, reply: Reply) -> None:
    await reply.finish(await services.maintenance.whoami(context))


lookup_identity = command(
    CommandSpec(
        id="core.whoisthis",
        plugin_id="maintenance",
        name="whoisthis",
        aliases=("whoisthis",),
        example_alias="whoisthis",
        summary="查看被提及用户的主体，便于登记管理员身份",
        permission=Role.SUPERUSER,
        scopes=frozenset({SceneKind.GROUP, SceneKind.CHANNEL}),
        usage="/whoisthis @某人",
        examples=("/whoisthis @某人",),
    ),
    priority=10,
    block=True,
)


@lookup_identity.assign("$main")
async def lookup_identity_handler(
    bot: Bot, event: Event, context: CommandContext, services: BotServices, reply: Reply
) -> None:
    targets = get_platform(bot).mentioned_users(bot, event)
    await reply.finish(await services.maintenance.whoisthis(context, targets))


status = command(
    CommandSpec(
        id="core.status",
        plugin_id="maintenance",
        name="status",
        aliases=("status",),
        example_alias="status",
        summary="查看机器人运行状态",
        menu_label="运行状态",
        permission=Role.SUPERUSER,
        examples=("/status",),
    ),
    priority=10,
    block=True,
)


@status.assign("$main")
async def status_handler(services: BotServices, reply: Reply) -> None:
    await reply.finish(await services.maintenance.status())
