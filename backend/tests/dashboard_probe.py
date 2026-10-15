"""Real QQ command dispatch to the shared dashboard login service, in an empty test environment."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.message import handle_event


async def main():
    with tempfile.TemporaryDirectory(prefix="obot-dashboard-command-") as directory:
        root = Path(directory)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(_env_file=empty, driver="~fastapi+~httpx+~websockets", qq_bots=[], log_level="ERROR")
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(
            Settings(obot_data_dir=root / "data", obot_public_base_url="https://bot.example.com")
        )
        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.dashboard")
        assert nonebot.load_plugin("plugins.help")
        await upgrade()
        driver = nonebot.get_driver()
        await driver._lifespan.startup()
        bot = Bot(nonebot.get_adapter("QQ"), "app", BotInfo(id="app", secret="synthetic"))
        sent = []
        try:
            with (
                patch.object(
                    Bot,
                    "post_c2c_messages",
                    AsyncMock(side_effect=lambda **kw: sent.append(kw) or {"id": "reply"}),
                ),
                patch.object(
                    Bot,
                    "post_group_messages",
                    AsyncMock(side_effect=lambda **kw: sent.append(kw) or {"id": "reply"}),
                ),
            ):
                private = C2CMessageCreateEvent.model_validate(
                    {
                        "id": "private",
                        "content": "/dashboard",
                        "to_me": True,
                        "timestamp": "2026-10-15T00:00:00+08:00",
                        "author": {"id": "member", "user_openid": "member"},
                    }
                )
                await handle_event(bot, private)
                text = sent[-1]["content"]
                assert "https://bot.example.com/login#" in text
                raw = text.split("#")[-1]
                session_token, account = await services.dashboard.exchange(raw)
                assert not account.superuser and await services.dashboard.authenticate(session_token)
                group = GroupAtMessageCreateEvent.model_validate(
                    {
                        "id": "group",
                        "content": "/dashboard",
                        "to_me": True,
                        "timestamp": "2026-10-15T00:00:00+08:00",
                        "group_id": "room",
                        "group_openid": "room",
                        "author": {
                            "id": "member",
                            "member_openid": "member",
                            "member_role": "member",
                            "bot": False,
                        },
                    }
                )
                await handle_event(bot, group)
                assert "https://" not in sent[-1]["content"]
                check = (await services.scene_access.issue_check(account.actor))["command"]
                raw = group.model_dump(exclude={"message"})
                raw.update(id="member-verify", content=check)
                await handle_event(bot, GroupAtMessageCreateEvent.model_validate(raw))
                from framework.contracts import SceneKind, SceneRef

                scene = SceneRef("qq", "app", SceneKind.GROUP, "room")
                assert not await services.scene_access.allowed(account.actor, scene)
                raw["author"]["member_role"] = "admin"
                raw["id"] = "admin-verify"
                await handle_event(bot, GroupAtMessageCreateEvent.model_validate(raw))
                assert "已验证" in sent[-1]["content"]
                assert await services.scene_access.allowed(account.actor, scene)
                raw["author"]["member_role"] = "member"
                raw.update(id="demoted", content="hello")
                await handle_event(bot, GroupAtMessageCreateEvent.model_validate(raw))
                assert not await services.scene_access.allowed(account.actor, scene)
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "private_login": True,
                            "group_link_denied": True,
                            "group_admin_verification": True,
                        }
                    )
                )
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
