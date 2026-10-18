"""Exercise contest templates through real QQ events with isolated storage."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.message import handle_event


async def main():
    with tempfile.TemporaryDirectory(prefix="obot-contests-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            command_start={"/", "!"},
            log_level="ERROR",
        )
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        for plugin in ("plugins.board", "plugins.help", "plugins.contests"):
            assert nonebot.load_plugin(plugin)
        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls, counter = [], 0

        async def post(**kwargs):
            calls.append(kwargs)
            return {"id": "reply"}

        async def request(text, role="private"):
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
            return [item["content"].removeprefix("<@user> ") for item in calls[before:]]

        async def listing(context, *, today, source, image_available):
            assert image_available
            return (f"list:{today}:{source}:1", "page:2")

        await upgrade()
        await driver._lifespan.startup()
        try:
            with (
                patch.object(Bot, "post_c2c_messages", AsyncMock(side_effect=post)),
                patch.object(Bot, "post_group_messages", AsyncMock(side_effect=post)),
                patch.object(services.contests, "listing", AsyncMock(side_effect=listing)) as query,
                patch.object(
                    services.contests, "detail", AsyncMock(return_value=("detail result",))
                ) as detail,
            ):
                for role in ("private", "member", "admin"):
                    for name in ("contest", "contests", "比赛", "近日比赛", "cp.contest"):
                        replies = await request("/" + name, role)
                        assert replies == ["list:False:all:1", "page:2"], (name, role, replies)
                    for invocation in ("/contest today", "/今日比赛", "!今天比赛", "/cp.today_contests"):
                        assert await request(invocation + " cf", role) == ["list:True:cf:1", "page:2"]
                    assert await request("/contest recent manual", role) == ["list:False:manual:1", "page:2"]
                    assert await request('/contest detail "Example Round"', role) == ["detail result"]
                    detail.assert_awaited_with(ANY, "Example Round", image_available=True)
                    assert await request("/contest detail Example Round", role) == ["detail result"]
                    detail.assert_awaited_with(ANY, "Example Round", image_available=True)
                    for suffix in (" todya", " today other", " recent cf extra", " detail"):
                        before = query.await_count, detail.await_count
                        replies = await request("/contest" + suffix, role)
                        assert len(replies) == 1 and "指令匹配失败" in replies[0]
                        assert before == (query.await_count, detail.await_count)
                    help_text = await request("/help text contest", role)
                    assert "detail" in "".join(help_text) and "today" in "".join(help_text)
                assert services.telemetry.counts.get("failed", 0) == 0
                print(json.dumps({"ok": True, "everyone": True, "ordered_pages": True}))
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
