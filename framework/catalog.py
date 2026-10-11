"""Command declarations are the source for matching, help and web descriptions."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from .contracts import Role, SceneKind


class MatchMode(StrEnum):
    COMMAND = "command"
    PREFIX = "prefix"
    KEYWORD = "keyword"


@dataclass(frozen=True)
class CommandSpec:
    id: str
    plugin_id: str
    name: str
    summary: str
    permission: Role = Role.EVERYONE
    aliases: tuple[str, ...] = ()
    usage: str = ""
    examples: tuple[str, ...] = ()
    scopes: frozenset[SceneKind] = frozenset(SceneKind)
    mode: MatchMode = MatchMode.COMMAND
    hidden: bool = False
    directory: tuple[str, ...] | None = None
    example_alias: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.id):
            raise ValueError("Command ID must be stable and qualified")
        object.__setattr__(self, "permission", Role(self.permission))
        if not self.plugin_id or not self.summary or not self.scopes:
            raise ValueError("Command metadata is incomplete")
        if any(
            not part or "." in part or "/" in part or any(c.isspace() for c in part) for part in self.path
        ):
            raise ValueError("Command path segments cannot be empty or contain dots, slashes or whitespace")
        names = (self.qualified_name(), *self.aliases)
        if any(not name or any(c.isspace() for c in name) or name.startswith("/") for name in names):
            raise ValueError("Command names cannot contain whitespace or a leading slash")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate command alias")
        if self.example_alias is not None and self.example_alias not in self.aliases:
            raise ValueError("Example alias must be a registered command alias")

    @property
    def path(self) -> tuple[str, ...]:
        directory = (self.plugin_id,) if self.directory is None else self.directory
        return (*directory, *self.name.split("."))

    def qualified_name(self, separator: str = ".") -> str:
        return separator.join(self.path)

    def invocation_names(self, separator: str = ".") -> tuple[str, ...]:
        return (self.qualified_name(separator), *self.aliases)

    def expand_example(self, text: str, separator: str = ".") -> str:
        return self._format_invocation(text, self.example_alias or self.qualified_name(separator), separator)

    def _format_invocation(self, text: str, name: str, separator: str) -> str:
        start = "" if self.mode == MatchMode.KEYWORD else "/"
        # Declarations may use a local name or a dot-qualified path even when
        # the runtime separator changes; this does not register extra aliases.
        names = {*self.invocation_names(separator), self.name, self.qualified_name()}
        for source in sorted(names, key=len, reverse=True):
            invocation = start + source
            if text[: len(invocation)].casefold() == invocation.casefold():
                tail = text[len(invocation) :]
                if self.mode == MatchMode.PREFIX or not tail or tail[0].isspace():
                    return start + name + tail
        return text

    def display_usage(self, separator: str = ".") -> str:
        start = "" if self.mode == MatchMode.KEYWORD else "/"
        return (
            self._format_invocation(self.usage, self.qualified_name(separator), separator)
            if self.usage
            else start + self.qualified_name(separator)
        )

    def to_dict(self, separator: str = ".") -> dict:
        return {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "name": self.qualified_name(separator),
            "path": list(self.path),
            "directory": list(self.path[:-1]),
            "short_name": self.name,
            "summary": self.summary,
            "permission": self.permission.value,
            "aliases": list(self.aliases),
            "example_alias": self.example_alias,
            "usage": self.display_usage(separator),
            "examples": [self.expand_example(example, separator) for example in self.examples],
            "scopes": sorted(self.scopes),
            "mode": self.mode.value,
        }


@dataclass(frozen=True)
class CommandDirectory:
    path: tuple[str, ...]
    summary: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.path
            or not self.summary
            or any(
                not part or any(c.isspace() or c in "/." for c in part)
                for part in (*self.path, *self.aliases)
            )
        ):
            raise ValueError("Invalid command directory metadata")


class CommandCatalog:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._directories: dict[tuple[str, ...], CommandDirectory] = {}
        self._frozen = False
        self.separator = "."

    def configure_separator(self, separator: str) -> None:
        if self._frozen or self._commands or self._directories:
            raise RuntimeError("Configure command separator before registering directories and commands")
        if not separator or any(c.isspace() or c.isalnum() or c == "/" for c in separator):
            raise ValueError("Command separator must be nonempty punctuation")
        self.separator = separator

    def register_directory(self, directory: CommandDirectory) -> None:
        if self._frozen:
            raise RuntimeError("Command catalog is frozen")
        path = tuple(part.casefold() for part in directory.path)
        names = {path, *(path[:-1] + (alias.casefold(),) for alias in directory.aliases)}
        if len(names) != 1 + len(directory.aliases):
            raise ValueError("Duplicate directory alias")
        for prior in self._directories.values():
            prior_path = tuple(part.casefold() for part in prior.path)
            prior_names = {prior_path, *(prior_path[:-1] + (alias.casefold(),) for alias in prior.aliases)}
            if names & prior_names:
                raise ValueError("Conflicting command directory")
        self._validate_tree(self.commands, (*self._directories.values(), directory))
        self._directories[path] = directory

    def register(self, spec: CommandSpec) -> None:
        if self._frozen:
            raise RuntimeError("Command catalog is frozen")
        if spec.id in self._commands:
            raise ValueError(f"Duplicate command ID: {spec.id}")
        names = spec.invocation_names(self.separator)
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate command alias")
        for other in self._commands.values():
            if (spec.mode == MatchMode.KEYWORD) == (other.mode == MatchMode.KEYWORD):
                for name in spec.invocation_names(self.separator):
                    for prior in other.invocation_names(self.separator):
                        same = name.casefold() == prior.casefold()
                        prefix = (
                            spec.mode == MatchMode.PREFIX and prior.casefold().startswith(name.casefold())
                        ) or (other.mode == MatchMode.PREFIX and name.casefold().startswith(prior.casefold()))
                        if same or prefix:
                            raise ValueError(f"Conflicting command alias: {name}")
        self._validate_tree((*self.commands, spec), tuple(self._directories.values()))
        self._commands[spec.id] = spec

    def _validate_tree(
        self, commands: tuple[CommandSpec, ...], directories: tuple[CommandDirectory, ...]
    ) -> None:
        parents = {
            tuple(part.casefold() for part in spec.path[:length])
            for spec in commands
            for length in range(1, len(spec.path))
        }
        parents.update(tuple(part.casefold() for part in directory.path) for directory in directories)
        for spec in commands:
            if any(self.separator in part for part in spec.path):
                raise ValueError("Command path segment contains the configured separator")
        for directory in directories:
            if any(self.separator in part for part in (*directory.path, *directory.aliases)):
                raise ValueError("Directory segment contains the configured separator")
            primary = tuple(part.casefold() for part in directory.path)
            for alias in directory.aliases:
                alias_path = primary[:-1] + (alias.casefold(),)
                if alias_path in parents or any(
                    parent[: len(alias_path)] == alias_path for parent in parents
                ):
                    raise ValueError("Directory alias shadows a command directory")
                parents.add(alias_path)
        directory_names = {self.separator.join(path) for path in parents}
        for spec in commands:
            if directory_names & {name.casefold() for name in spec.invocation_names(self.separator)}:
                raise ValueError("Command invocation conflicts with a directory")

    def freeze(self) -> None:
        self._frozen = True

    @property
    def commands(self) -> tuple[CommandSpec, ...]:
        return tuple(sorted(self._commands.values(), key=lambda spec: spec.id))

    async def visible(
        self, check: Callable[[CommandSpec], Awaitable[bool]], *, include_hidden: bool = False
    ) -> tuple[CommandSpec, ...]:
        result = []
        for spec in self.commands:
            if (include_hidden or not spec.hidden) and await check(spec):
                result.append(spec)
        return tuple(result)

    async def help_model(self, check: Callable[[CommandSpec], Awaitable[bool]], query: str = "") -> dict:
        # Help is an exhaustive list of executable commands. Hidden metadata
        # still affects discovery consumers, but never substitutes for policy.
        commands = await self.visible(check, include_hidden=True)
        query = query.strip().lstrip("/").casefold()
        path = tuple(query.split(self.separator)) if query else ()
        for length in range(1, len(path) + 1):
            for directory in self._directories.values():
                primary = tuple(part.casefold() for part in directory.path)
                if (
                    len(primary) == length
                    and path[: length - 1] == primary[:-1]
                    and path[length - 1] in {alias.casefold() for alias in directory.aliases}
                ):
                    path = primary + path[length:]
        directories = self.directory_index(commands)
        is_directory = not query or any(tuple(item["path"]) == path for item in directories)
        if is_directory:
            selected = tuple(
                spec for spec in commands if tuple(part.casefold() for part in spec.path[: len(path)]) == path
            )
        else:
            selected = tuple(
                spec
                for spec in commands
                if self.separator.join(path) == spec.qualified_name(self.separator).casefold()
                or query in {name.casefold() for name in spec.invocation_names(self.separator)}
            )
        grouped: dict[tuple[str, ...], list[dict]] = {}
        for spec in selected:
            parent = tuple(part.casefold() for part in spec.path[:-1])
            grouped.setdefault(parent, []).append(spec.to_dict(self.separator))
        descriptions = {tuple(item["path"]): item["summary"] for item in directories}
        return {
            "title": "Lowlight 帮助手册"
            if not query
            else (
                selected[0].qualified_name(self.separator)
                if selected and not is_directory
                else self.separator.join(path)
            ),
            "separator": self.separator,
            "count": len(selected),
            "found": is_directory or bool(selected),
            "groups": [
                {
                    "path": list(parent),
                    "name": self.separator.join(parent) or "通用功能",
                    "summary": descriptions.get(parent, "独立指令" if not parent else "指令"),
                    "commands": entries,
                }
                for parent, entries in sorted(grouped.items())
            ],
        }

    async def help_text(self, check: Callable[[CommandSpec], Awaitable[bool]], query: str = "") -> str:
        return self.help_model_text(await self.help_model(check, query))

    @classmethod
    def help_model_text(cls, model: dict) -> str:
        if not model["found"]:
            return "没有找到当前场景可用的目录或指令。"
        if not model["count"]:
            return "当前场景没有可用指令。"
        sections = [
            model["title"],
            f"当前可用 {model['count']} 条指令。使用 /help 模块名 或 /help 指令 查看详情，/help text 查看文字。",
        ]
        for group in model["groups"]:
            sections.append(f"{group['name']} — {group['summary']}")
            sections.extend(cls.command_description(spec) for spec in group["commands"])
        return "\n\n".join(sections)

    def command_help(self, spec: CommandSpec) -> str:
        return self.command_description(spec.to_dict(self.separator))

    @staticmethod
    def command_description(spec: dict) -> str:
        start = "" if spec["mode"] == MatchMode.KEYWORD else "/"
        return "\n".join(
            filter(
                None,
                (
                    f"{start}{spec['name']} — {spec['summary']}",
                    f"用法：{spec['usage']}",
                    f"别名：{'、'.join(start + alias for alias in spec['aliases'])}"
                    if spec["aliases"]
                    else "",
                    f"示例：{'；'.join(spec['examples'])}" if spec["examples"] else "",
                ),
            )
        )

    def directory_index(self, commands: tuple[CommandSpec, ...]) -> list[dict]:
        # Infer every parent from visible leaves, so private/denied directories
        # cannot leak through either help or the dashboard catalogue.
        paths = {
            tuple(part.casefold() for part in spec.path[:length])
            for spec in commands
            for length in range(1, len(spec.path))
        }
        return [
            {
                "path": list(path),
                "name": self.separator.join(path),
                "summary": self._directories[path].summary if path in self._directories else path[-1],
            }
            for path in sorted(paths)
        ]


catalog = CommandCatalog()
