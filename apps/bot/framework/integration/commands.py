"""Register help metadata and return a native NoneBot matcher."""

from nonebot import get_driver, on_command, on_fullmatch
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.rule import Rule

from ..catalog import CommandSpec, MatchMode, catalog
from ..responses import command_failure
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


def template_command(spec: CommandSpec, *patterns, compact_aliases=(), **kwargs):
    """Use Alconna's declarative grammar, assignments and native injection."""
    from nonebot import require

    require("nonebot_plugin_alconna")
    from arclet.alconna import Alconna, CommandMeta, Namespace, config
    from nonebot_plugin_alconna import CommandResult, on_alconna

    from .command_context import Reply
    from .command_templates import TextCommandSource, suggest_subcommand

    if spec.mode != MatchMode.COMMAND:
        raise ValueError("Template commands require ordinary command mode")
    if not set(compact_aliases) <= set(spec.aliases):
        raise ValueError("Compact aliases must be declared in the command spec")
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
    # Only opted-in shortcuts accept attached arguments; canonical commands and
    # English aliases retain word boundaries. Register longer aliases first.
    for alias in sorted(spec.aliases, key=len, reverse=True):
        matcher.shortcut(alias, prefix=True, compact=alias in compact_aliases)

    @matcher.handle()
    async def invalid_arguments(event: Event, result: CommandResult, reply: Reply):
        if not result.result.matched:
            text = event.get_plaintext()
            suggestion = suggest_subcommand(matcher.command(), result.result, text, spec, catalog.separator)
            prefix = next(
                (
                    p
                    for p in sorted(get_driver().config.command_start, key=len, reverse=True)
                    if p and text.lstrip().startswith(p)
                ),
                "/",
            )
            await reply.finish(
                command_failure(
                    text,
                    usage=spec.expand_example(spec.display_usage(catalog.separator), catalog.separator),
                    suggestions=(suggestion,) if suggestion else (),
                    help_command=f"{prefix}help {spec.example_alias or spec.qualified_name(catalog.separator)}",
                )
            )

    return matcher
