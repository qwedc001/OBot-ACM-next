"""Exercise public profile aliases through native NoneBot QQ dispatch."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.message import handle_event


async def main():
    separator = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="obot-profiles-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            command_sep={separator},
            command_start={"/", "!"},
            log_level="ERROR",
        )
        from framework.catalog import catalog
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        catalog.configure_separator(separator)
        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        for plugin in ("plugins.maintenance", "plugins.help", "plugins.board", "plugins.profiles"):
            assert nonebot.load_plugin(plugin)
        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls, counter = [], 0

        async def post(**kwargs):
            calls.append(kwargs)
            return {"id": "reply"}

        async def request(text, role):
            nonlocal counter
            counter += 1
            payload = dict(
                id=f"event-{counter}", content=text, to_me=True, timestamp="2026-10-18T00:00:00+08:00"
            )
            if role == "private":
                payload["author"] = dict(id="user", user_openid="user")
                event = C2CMessageCreateEvent.model_validate(payload)
            else:
                payload.update(
                    group_id="group",
                    group_openid="group",
                    author=dict(id="user", member_openid="user", member_role=role, bot=False),
                )
                event = GroupAtMessageCreateEvent.model_validate(payload)
            before = len(calls)
            await handle_event(bot, event)
            assert len(calls) == before + 1, (text, role, calls[before:])
            return calls[-1]["content"]

        async def profile(context, platform, handle, *, image_available):
            assert image_available
            return f"profile:{platform}:{handle}"

        await upgrade()
        await driver._lifespan.startup()
        try:
            with (
                patch.object(Bot, "post_c2c_messages", AsyncMock(side_effect=post)),
                patch.object(Bot, "post_group_messages", AsyncMock(side_effect=post)),
                patch.object(services.profiles, "reply", AsyncMock(side_effect=profile)) as query,
            ):
                for role in ("private", "member", "admin", "owner"):
                    for names, platform, handle in (
                        (("cf", "codeforces"), "codeforces", "example"),
                        (("atc", "atcoder"), "atcoder", "example"),
                        (("nk", "nc", "nowcoder"), "nowcoder", "123456"),
                    ):
                        for name in (f"cp{separator}{names[0]}", *names):
                            for action in ("user", "identity", "id", "card", "info"):
                                result = await request(f"/{name} {action} {handle}", role)
                                assert f"profile:{platform}:{handle}" in result
                        for suffix in ("", " user", " unknown example", " user example extra"):
                            before = query.await_count
                            assert "用法" in await request(f"/{names[0]}{suffix}", role)
                            assert query.await_count == before
                    help_text = await request("/help text cp", role)
                    for name in ("cf", "atc", "nk"):
                        assert f"/cp{separator}{name}" in help_text
                    detail = await request("/help text cf", role)
                    assert "示例：/cf user tourist" in detail
                    assert "board" not in detail
                assert services.telemetry.counts.get("failed", 0) == 0
                print(json.dumps({"ok": True, "everyone": True, "aliases": True, "help": True}))
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
