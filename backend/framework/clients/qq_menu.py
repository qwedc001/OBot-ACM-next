"""Explicit QQ menu/panel APIs, without a separate token or HTTP stack."""

from nonebot.adapters import Bot

from .base import Client


class QQMenuClient(Client):
    @classmethod
    def build(cls, container):
        return cls()

    async def get(self, bot: Bot) -> dict:
        from ..integration.platforms.qq_menu import request_menu

        return await request_menu(bot)

    async def put(self, bot: Bot, menu: dict) -> dict:
        from ..integration.platforms.qq_menu import request_menu

        return await request_menu(bot, menu=menu)

    async def panels(self, bot: Bot, scope: str) -> list[dict]:
        from ..integration.platforms.qq_menu import QQMenuError, request_discovery

        result, cursor, seen = [], "", set()
        while True:
            page = await request_discovery(
                bot,
                "GET",
                ("v2", "panels"),
                params={"scope": scope, "limit": 50, **({"cursor": cursor} if cursor else {})},
            )
            if not any(key in page for key in ("records", "is_end", "next_cursor")):
                raise QQMenuError()
            records = page.get("records", [])
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise QQMenuError()
            result.extend(records)
            next_cursor = page.get("next_cursor")
            if page.get("is_end") is False and not next_cursor:
                raise QQMenuError()
            if page.get("is_end") is True or not next_cursor:
                return result
            if not isinstance(next_cursor, str) or next_cursor in seen:
                raise QQMenuError()
            cursor = next_cursor
            seen.add(cursor)

    async def create_panel(self, bot: Bot, scope: str, panel: dict) -> str:
        from ..integration.platforms.qq_menu import QQMenuError, request_discovery

        result = await request_discovery(
            bot, "POST", ("v2", "panels"), payload={"scope": scope, "target_type": "all", "panel": panel}
        )
        panel_id = result.get("panel_id")
        if not isinstance(panel_id, str) or not panel_id:
            raise QQMenuError()
        return panel_id

    async def get_panel(self, bot: Bot, panel_id: str) -> dict:
        from ..integration.platforms.qq_menu import QQMenuError, request_discovery

        result = await request_discovery(bot, "GET", ("v2", "panels", panel_id))
        if not isinstance(result.get("panel"), dict):
            raise QQMenuError()
        return result

    async def update_panel(self, bot: Bot, panel_id: str, panel: dict) -> dict:
        from ..integration.platforms.qq_menu import QQMenuError, request_discovery

        result = await request_discovery(bot, "PUT", ("v2", "panels", panel_id), payload={"panel": panel})
        if type(result.get("version")) is not int:
            raise QQMenuError()
        return result

    async def delete_panel(self, bot: Bot, panel_id: str) -> None:
        from ..integration.platforms.qq_menu import request_discovery

        await request_discovery(bot, "DELETE", ("v2", "panels", panel_id))
