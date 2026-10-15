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


def template_command(spec: CommandSpec, *patterns, **kwargs):
    """Use Alconna's declarative grammar, assignments and native injection."""
    from nonebot import require

    require("nonebot_plugin_alconna")
    from arclet.alconna import Alconna, CommandMeta, Namespace, config
    from nonebot_plugin_alconna import CommandResult, on_alconna

    from .command_context import Reply
    from .command_templates import TextCommandSource

    if spec.mode != MatchMode.COMMAND:
        raise ValueError("Template commands require ordinary command mode")
    catalog.register(spec)
    namespace = Namespace(f"OBot_{spec.id}", disable_builtin_options={"help", "shortcut", "completion"})
    config.namespaces[namespace.name] = namespace
    matcher = on_alconna(
        Alconna(
            spec.qualified_name(catalog.separator),
            *patterns,
            meta=CommandMeta(description=spec.summary, compact=False),
            namespace=namespace,
        ),
        aliases=set(spec.aliases),
        extensions=[TextCommandSource],
        use_cmd_start=True,
        use_cmd_sep=False,
        skip_for_unmatch=False,
        auto_send_output=False,
        rule=Rule(supported_message) & kwargs.pop("rule", None),
        default_state={**(kwargs.pop("state", None) or {}), _SPEC: spec},
        _depth=1,
        **kwargs,
    )

    @matcher.handle()
    async def invalid_arguments(result: CommandResult, reply: Reply):
        if not result.result.matched:
            await reply.finish(
                "未知子指令或参数格式不正确，请使用以下用法。\n" + spec.display_usage(catalog.separator)
            )

    return matcher
