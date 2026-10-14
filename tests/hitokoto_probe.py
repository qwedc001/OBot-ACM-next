"""Verify public Hitokoto commands through native QQ matchers and delivery."""

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
    separator = sys.argv[1] if len(sys.argv) > 1 else "."
    with tempfile.TemporaryDirectory(prefix="obot-hitokoto-") as temporary:
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
        from framework.clients import hitokoto as module
        from framework.clients.http import UpstreamError
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        catalog.configure_separator(separator)
        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        for plugin in ("plugins.maintenance", "plugins.help", "plugins.fun"):
            assert nonebot.load_plugin(plugin)
        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls, counter, clock = [], 0, [10.0]

        async def post(**kwargs):
            calls.append(kwargs)
            return {"id": "reply"}

        async def request(command, role, *, expect_reply=True):
            nonlocal counter
            counter += 1
            payload = dict(
                id=f"event-{counter}", content=command, to_me=True, timestamp="2026-10-12T00:00:00+08:00"
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
            if not expect_reply:
                assert len(calls) == before
                return None
            assert len(calls) == before + 1
            assert calls[-1]["msg_type"] == 0
            return calls[-1]["content"]

        upstream = AsyncMock(
            return_value={"hitokoto": "今天也要开心地 AC。", "from": "训练日记", "from_who": None}
        )
        await upgrade()
        await driver._lifespan.startup()
        try:
            with (
                patch.object(Bot, "post_c2c_messages", AsyncMock(side_effect=post)),
                patch.object(Bot, "post_group_messages", AsyncMock(side_effect=post)),
                patch.object(services.clients.http, "get_json", upstream),
                patch.object(module, "monotonic", lambda: clock[0]),
            ):
                for role in ("private", "member", "admin", "owner"):
                    for name in (
                        f"fun{separator}hitokoto",
                        "hitokoto",
                        "一言",
                        "来句",
                        "来一句",
                        "来句话",
                        "来一句话",
                    ):
                        reply = await request("/" + name, role)
                        assert "[Hitokoto]\n今天也要开心地 AC。\nBy 「训练日记」" in reply
                        assert ("<@user>" in reply) == (role != "private")
                    help_text = await request("/help text", role)
                    assert f"/fun{separator}hitokoto" in help_text and "/maintenance" not in help_text
                    directory = await request("/help text fun", role)
                    assert "趣味工具" in directory and "示例：/hitokoto" in directory
                    detail = await request("/help text 一言", role)
                    assert "来一句话" in detail
                    for entry in ("/fun", "!fun", "/maintenance", "/maintainence"):
                        await request(entry, role, expect_reply=False)
                assert upstream.await_count == 1
                clock[0] += 3
                upstream.side_effect = UpstreamError("private upstream detail")
                assert "一言暂时无法获取" in await request("/hitokoto", "private")
                upstream.side_effect = None
                assert "今天也要开心地 AC。" in await request("/hitokoto", "private")
                assert services.telemetry.counts.get("failed", 0) == 0
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "everyone": True,
                            "aliases": True,
                            "shared_help": True,
                            "failure_and_recovery": True,
                        }
                    )
                )
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
