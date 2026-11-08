"""Read-only OI contestant lookup, including original OBot aliases."""

from arclet.alconna import Args, MultiVar
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec, catalog
from framework.integration.command_context import BotServices, Reply
from framework.integration.commands import template_command
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="OI 选手查询",
    description="通过 CPCFinder 查询 OI 选手与获奖记录",
    usage="/oier <姓名> [姓名2 姓名3]",
    type="application",
    supported_adapters=None,
)
spec = CommandSpec(
    id="cp.oier",
    plugin_id="cp",
    name="oier",
    aliases=("oier", "oierdb", "oidb", "oi", "oifinder", "oifind", "oifd", "OI选手", "信息学奥赛"),
    example_alias="oier",
    summary="查询 OI 选手档案与获奖；最多三人",
    usage="/oier <姓名> [姓名2 姓名3]（多词姓名请加双引号）",
    examples=("/oier 张三", "/oier 张三 李四"),
)
oier = template_command(spec, Args["names", MultiVar(str), ()], priority=10, block=True)


@oier.assign("$main")
async def handle_oier(services: BotServices, reply: Reply, names: tuple[str, ...] = ()):
    if not names:
        await reply.finish(catalog.command_help(spec))
    for chunk in text_chunks(await services.oier.reply(names)):
        await reply.send(chunk)
    await reply.finish()
