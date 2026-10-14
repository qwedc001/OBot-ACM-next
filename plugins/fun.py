"""Small public tools for everyday chat."""

from nonebot.plugin import PluginMetadata

from framework.catalog import CommandDirectory, CommandSpec, catalog
from framework.contracts import Role
from framework.integration.command_context import BotServices, Reply
from framework.integration.commands import template_command as command
from framework.responses import text_chunks

__plugin_meta__ = PluginMetadata(
    name="趣味工具",
    description="一言与日常趣味工具",
    usage="/hitokoto /一言 /来一句",
    type="application",
    supported_adapters=None,
)

catalog.register_directory(CommandDirectory(("fun",), "趣味工具"))

hitokoto = command(
    CommandSpec(
        id="fun.hitokoto",
        plugin_id="fun",
        name="hitokoto",
        summary="随机获取一句一言，附作者与出处",
        menu_label="随机一言",
        permission=Role.EVERYONE,
        aliases=("hitokoto", "一言", "来句", "来一句", "来句话", "来一句话"),
        example_alias="hitokoto",
        examples=("/hitokoto",),
    ),
    priority=10,
    block=True,
)


@hitokoto.assign("$main")
async def hitokoto_handler(services: BotServices, reply: Reply) -> None:
    for chunk in text_chunks(await services.hitokoto.random()):
        await reply.send(chunk)
    await reply.finish()
