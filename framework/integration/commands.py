"""Register help metadata and return a native NoneBot matcher."""

from nonebot import on_command, on_fullmatch
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.rule import Rule

from ..catalog import CommandSpec, MatchMode, catalog
from .command_context import _SPEC
from .platforms import supports_bot


async def supported_message(bot: Bot, event: Event) -> bool:
    return event.get_type() == "message" and supports_bot(bot)


def command(spec: CommandSpec, **kwargs) -> type[Matcher]:
    """Register help once and return a native matcher with unchanged options."""
    catalog.register(spec)
    kwargs["rule"] = Rule(supported_message) & kwargs.get("rule")
    kwargs["state"] = {**(kwargs.get("state") or {}), _SPEC: spec}
    if spec.mode == MatchMode.KEYWORD:
        matcher = on_fullmatch(spec.invocation_names(catalog.separator), _depth=1, **kwargs)
    else:
        kwargs.setdefault("force_whitespace", spec.mode != MatchMode.PREFIX)
        matcher = on_command(spec.path, aliases=set(spec.aliases), _depth=1, **kwargs)

    return matcher
