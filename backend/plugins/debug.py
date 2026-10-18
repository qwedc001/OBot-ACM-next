"""Superuser diagnostic templates with separate Markdown and button handlers."""

from arclet.alconna import Subcommand
from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata

from framework.catalog import CommandSpec
from framework.clients.reply import DeliveryFailed
from framework.contracts import Role
from framework.integration.command_context import BotServices, CommandContext, Reply
from framework.integration.commands import template_command
from framework.messages import Keyboard, Markdown

__plugin_meta__ = PluginMetadata(
    name="发送能力调试",
    description="仅超级管理员可用的 Markdown 与交互按钮测试",
    usage="/debug [markdown|button]",
    type="application",
    supported_adapters=None,
)
debug = template_command(
    CommandSpec(
        id="core.debug",
        plugin_id="debug",
        name="debug",
        directory=(),
        summary="测试 Markdown 排版或按钮交互",
        menu_label="消息测试",
        permission=Role.SUPERUSER,
        usage="/debug [markdown|button]",
        examples=("/debug", "/debug markdown", "/debug button"),
    ),
    Subcommand("markdown|MARKDOWN"),
    Subcommand("button|BUTTON"),
    priority=10,
    block=True,
)


@debug.assign("$main")
async def handle_debug_help(context: CommandContext, services: BotServices, reply: Reply):
    await reply.finish(await services.help.reply(context, "debug", draw=False, image_available=False))


async def send_diagnostic(bot, reply, content, required, failure_hint):
    if not all(reply.supports(bot.adapter.get_name(), part) for part in required):
        await reply.finish("当前平台未实现该消息类型，无法进行这项发送测试。")
    try:
        await reply.send(content)
    except DeliveryFailed:
        await reply.finish(
            "测试消息发送失败，尚不能确认该能力可用。\n请检查 QQ 开放平台是否开通被动原生 Markdown 权限；"
            + failure_hint
        )
    await reply.finish()


@debug.assign("markdown")
async def handle_debug_markdown(bot: Bot, services: BotServices, reply: Reply):
    await send_diagnostic(
        bot, reply, await services.debug.markdown(), (Markdown,), "本 Markdown 样例不含外部链接或图片。"
    )


@debug.assign("button")
async def handle_debug_button(bot: Bot, services: BotServices, reply: Reply):
    await send_diagnostic(
        bot,
        reply,
        await services.debug.button(),
        (Markdown, Keyboard),
        "按钮测试还需要自定义按钮能力，链接域名 q.qq.com 需按平台要求配置。",
    )
