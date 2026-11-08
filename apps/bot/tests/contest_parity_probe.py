"""Real NoneBot/Alconna dispatch using synthetic events and temporary storage."""

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
    with tempfile.TemporaryDirectory(prefix="obot-parity-probe-") as directory:
        root = Path(directory)
        env = root / "empty.env"
        env.write_text("")
        nonebot.init(_env_file=env, driver="~fastapi+~httpx+~websockets", qq_bots=[], log_level="ERROR")
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        for plugin in ("plugins.help", "plugins.board", "plugins.oier"):
            assert nonebot.load_plugin(plugin)
        await upgrade()
        await nonebot.get_driver()._lifespan.startup()
        bot = Bot(nonebot.get_adapter("QQ"), "app", BotInfo(id="app", secret="synthetic"))
        deliveries, counter = [], 0

        async def send(**kwargs):
            deliveries.append(kwargs["message"])
            return {"id": "reply"}

        async def request(text, group=False, to_me=True):
            nonlocal counter
            counter += 1
            data = {
                "id": str(counter),
                "content": text,
                "to_me": to_me,
                "timestamp": "2026-10-13T00:00:00+08:00",
            }
            if group:
                data.update(
                    group_id="room",
                    group_openid="room",
                    author={"id": "user", "member_openid": "user", "member_role": "member", "bot": False},
                )
                event = GroupAtMessageCreateEvent.model_validate(data)
            else:
                data["author"] = {"id": "user", "user_openid": "user"}
                event = C2CMessageCreateEvent.model_validate(data)
            before = len(deliveries)
            await handle_event(bot, event)
            return deliveries[before:]

        try:
            with (
                patch.object(Bot, "send_to_c2c", AsyncMock(side_effect=send)),
                patch.object(Bot, "send_to_group", AsyncMock(side_effect=send)),
                patch.object(services.oier, "reply", AsyncMock(return_value="OI lookup ok")) as lookup,
                patch.object(services.boards, "rank_image", AsyncMock()) as rank,
            ):
                for alias in (
                    "oier",
                    "oierdb",
                    "oidb",
                    "oi",
                    "oifinder",
                    "oifind",
                    "oifd",
                    "OI选手",
                    "信息学奥赛",
                ):
                    assert "OI lookup ok" in str(await request(f"/{alias} 测试选手"))
                    assert lookup.await_args.args == (("测试选手",),)
                await request('/oier "First Last" 测试选手', group=True)
                assert lookup.await_args.args == (("First Last", "测试选手"),), lookup.await_args
                before = lookup.await_count
                assert not await request("/oier 测试选手", group=True, to_me=False)
                assert lookup.await_count == before
                assert "用法" in str(await request("/oier"))
                assert "oier" in str(await request("/help text cp.oier"))
                from framework.contracts import ExternalIdentity

                actor = await services.identities.resolve(ExternalIdentity("qq", "app", "c2c_user", "user"))
                group_actor = await services.identities.resolve(
                    ExternalIdentity("qq", "app", "group_member", "user")
                )
                for invocation, limit, name, group in (
                    ("/board rank", 20, None, False),
                    ("/board rank 5 testOJ", 5, "testOJ", True),
                    ("/题数排名", 20, None, False),
                    ("/题数排名 100 testOJ", 100, "testOJ", True),
                ):
                    artifact = await services.clients.artifacts.create(
                        group_actor if group else actor, b"\x89PNG\r\n\x1a\nsynthetic", "image/png"
                    )
                    rank.return_value = artifact
                    messages = await request(invocation, group=group)
                    assert len(messages) == 2 and any(part.type == "file_image" for part in messages[-1]), (
                        messages
                    )
                    assert rank.await_args.args[1:] == (limit, name)
                before = rank.await_count
                for malformed in (
                    "/board rank 4",
                    "/board rank 101",
                    "/题数排名 -1",
                    "/题数排名 20 a b",
                    "/board rank abc",
                ):
                    assert await request(malformed)
                assert rank.await_count == before
                assert not await request("/题数排名", group=True, to_me=False)
                assert "rank" in str(await request("/board"))
                assert "过题数" in str(await request("/help text 题数排名"))
                print(json.dumps({"ok": True, "oi_aliases": 9, "rank_routes": 4, "native_events": counter}))
        finally:
            await nonebot.get_driver()._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
