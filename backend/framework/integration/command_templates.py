"""Provide native event text to Alconna without adapter-specific ID conversion."""

from nonebot_plugin_alconna import Extension, UniMessage


class TextCommandSource(Extension):
    id = "obot.text_commands"
    priority = -100

    async def message_provider(self, event, state, bot, use_origin=False):
        return UniMessage(event.get_plaintext())
