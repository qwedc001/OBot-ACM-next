from nonebot.plugin import PluginMetadata

from framework.catalog import CommandDirectory, CommandSpec, catalog
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import command

__plugin_meta__ = PluginMetadata(
    name="维护与诊断",
    description="仅机器人管理员可用的连通检查、身份与运行状态",
    usage="/help maintenance /maintenance.ping /maintenance.whoami /maintenance.status",
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
        permission=Role.SUPERUSER,
        examples=("/ping",),
    ),
    priority=10,
    block=True,
)


@ping.handle()
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
        permission=Role.SUPERUSER,
        examples=("/whoami",),
    ),
    priority=10,
    block=True,
)


@identity.handle()
async def identity_handler(context: CommandContext, services: BotServices, reply: Reply) -> None:
    await reply.finish(await services.maintenance.whoami(context))


status = command(
    CommandSpec(
        id="core.status",
        plugin_id="maintenance",
        name="status",
        aliases=("status",),
        example_alias="status",
        summary="查看机器人运行状态",
        permission=Role.SUPERUSER,
        examples=("/status",),
    ),
    priority=10,
    block=True,
)


@status.handle()
async def status_handler(services: BotServices, reply: Reply) -> None:
    await reply.finish(await services.maintenance.status())
