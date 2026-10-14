"""Derive two-level QQ discovery menus from the shared command catalogue."""

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from nonebot.adapters import Bot

from ..catalog import CommandCatalog, CommandSpec, MatchMode
from ..clients.qq_menu import QQMenuClient
from ..contracts import Role, SceneKind
from .base import Service


def _short(text: str, limit: int) -> str:
    result, used = "", 0
    for character in text:
        cost = 1 if character.isascii() else 2
        if used + cost > limit:
            break
        result += character
        used += cost
    return result


def _unique(text: str, used: set[str], limit: int) -> str:
    candidate = _short(text, limit)
    number = 2
    while candidate in used:
        suffix = str(number)
        candidate = _short(text, limit - len(suffix)) + suffix
        number += 1
    used.add(candidate)
    return candidate


def _without_icons(value):
    # The live QQ API adds default icons that are absent from the documented payload.
    if isinstance(value, dict):
        return {key: _without_icons(item) for key, item in value.items() if key != "icon"}
    if isinstance(value, list):
        return [_without_icons(item) for item in value]
    return value


class MenuService(Service):
    dependencies = ("clients.qq_menu",)

    @classmethod
    def build(cls, container):
        return cls(container.catalog, container.clients.qq_menu)

    def __init__(self, catalog: CommandCatalog, client: QQMenuClient):
        self.catalog, self.client = catalog, client

    def preview(self, *, include_admin: bool = False, command_start: str = "/") -> dict:
        commands = tuple(
            spec
            for spec in self.catalog.commands
            if not spec.hidden
            and (SceneKind.C2C in spec.scopes or include_admin)
            and (spec.permission == Role.EVERYONE or include_admin)
        )
        descriptions = {
            tuple(item["path"]): item["summary"] for item in self.catalog.directory_index(commands)
        }
        grouped: dict[tuple[str, ...], list[CommandSpec]] = defaultdict(list)
        for spec in commands:
            grouped[spec.path[:-1]].append(spec)
        roots, used_roots = [], set()
        for path, entries in sorted(grouped.items()):
            title = descriptions.get(path, "通用功能")
            # Reserve one of five child slots for the complete permission-filtered help.
            for offset in range(0, len(entries), 4):
                used_labels, children = set(), []
                for spec in entries[offset : offset + 4]:
                    marker = (
                        "·超管"
                        if spec.permission == Role.SUPERUSER
                        else ("·群管" if spec.permission == Role.GROUP_ADMIN else "")
                    )
                    marker_length = sum(1 if char.isascii() else 2 for char in marker)
                    label = _short(spec.menu_label or spec.name, 14 - marker_length) + marker
                    name = spec.example_alias or spec.qualified_name(self.catalog.separator)
                    invocation = name if spec.mode == MatchMode.KEYWORD else command_start + name
                    children.append(
                        {
                            "type": "send_message",
                            "name": _unique(label, used_labels, 14),
                            "send_message": invocation,
                        }
                    )
                query = self.catalog.separator.join(path)
                children.append(
                    {
                        "type": "send_message",
                        "name": _unique("指令说明", used_labels, 14),
                        "send_message": command_start + "help text" + (" " + query if query else ""),
                    }
                )
                roots.append(
                    {"type": "menu", "name": _unique(title, used_roots, 10), "sub_menu_items": children}
                )
        if len(roots) > 10:
            raise ValueError("QQ global menus allow at most ten root items; reorganize command modules")
        return {"items": roots}

    async def sync(
        self, bot: Bot, *, backup: Path, include_admin: bool = False, command_start: str = "/"
    ) -> dict:
        desired = self.preview(include_admin=include_admin, command_start=command_start)
        current = await self.client.get(bot)
        existing = current.get("menu") or {}
        if _without_icons(existing) == desired:
            return {"changed": False, "verified": True, "version": current.get("version")}

        def save():
            backup.parent.mkdir(parents=True, exist_ok=True)
            # Never overwrite a previous snapshot during a retry.
            with backup.open("x", encoding="utf-8") as file:
                json.dump(current, file, ensure_ascii=False, indent=2)
            backup.chmod(0o600)

        await asyncio.to_thread(save)
        updated = await self.client.put(bot, desired)
        readback = await self.client.get(bot)
        if _without_icons(readback.get("menu")) != desired:
            raise RuntimeError("QQ menu update accepted but readback differs; inspect the saved snapshot")
        return {"changed": True, "verified": True, "version": updated["version"]}
