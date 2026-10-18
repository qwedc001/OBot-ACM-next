"""Native Alconna message source and grammar-based error suggestions."""

import difflib

from arclet.alconna import Subcommand
from arclet.alconna.exceptions import ParamsUnmatched
from nonebot_plugin_alconna import Extension, UniMessage
from tarina import split


def suggest_subcommand(command, result, text, spec, separator):
    """Suggest a registered name at a failed grammar node; never parse/execute anew.

    Traversal stops at argument-bearing nodes so account names and other values
    cannot be mistaken for commands. Alconna's failed token must agree as well.
    """
    if not isinstance(result.error_info, ParamsUnmatched) or not result.error_data:
        return None
    tokens = split(text.strip(), " ")
    if not tokens:
        return None
    node, path, canonical = command, [], []
    for token in tokens[1:]:
        if node.args.argument:
            return None
        aliases = {
            alias: child for child in node.options if isinstance(child, Subcommand) for alias in child.aliases
        }
        if token in aliases:
            node = aliases[token]
            path.append(token)
            canonical.append(node.name)
            continue
        if token != result.error_data[0]:
            return None
        closest = difflib.get_close_matches(token, aliases, n=1, cutoff=0.6)
        if not closest:
            return None
        alias = closest[0]
        child = aliases[alias]
        canonical_path = " ".join([*canonical, child.name])
        declared = spec.display_usage(separator).partition(" ")[2]
        # Reuse the curated parameter labels when the declared usage describes
        # this path; otherwise let Alconna format its own argument metadata.
        if declared == canonical_path or declared.startswith(canonical_path + " "):
            tail = declared[len(canonical_path) :].strip()
        else:
            tail = command.formatter.parameters(child.args).strip()
            if any(isinstance(option, Subcommand) for option in child.options):
                tail = (tail + " <子指令>").strip()
        return " ".join([tokens[0][:80], *path, alias, tail]).rstrip()
    return None


class TextCommandSource(Extension):
    id = "obot.text_commands"
    priority = -100

    async def message_provider(self, event, state, bot, use_origin=False):
        return UniMessage(event.get_plaintext())
