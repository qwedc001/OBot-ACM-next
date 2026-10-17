"""Native matcher dispatch: superuser-only operations and acknowledgement ordering."""

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
    with tempfile.TemporaryDirectory(prefix="obot-git-test-") as directory:
        root = Path(directory)
        env = root / "empty.env"
        env.write_text("")
        nonebot.init(
            _env_file=env,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            superusers={"operator"},
            log_level="ERROR",
        )
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.git")
        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls, actions = [], []
        counter = 0

        async def post(**values):
            calls.append(values)
            return {"id": "reply"}

        async def updater(action, **values):
            actions.append((action, values))
            if action in {"update", "restart"}:
                assert "准备更新" in calls[-1]["content"], "ACK must precede process stop"
                assert values["target"]["reply_id"].startswith("event-")
                assert values["target"]["reply_sequence"] == 2
                return "operation"
            if action == "status":
                return {
                    "running": True,
                    "commit": "a" * 40,
                    "operation": {"id": "old", "outcome": "rolled_back", "log": "synthetic failure"},
                }
            return "commit or log"

        async def send(command, *, group=False, user="operator", role="member"):
            nonlocal counter
            counter += 1
            payload = dict(
                id=f"event-{counter}", content=command, to_me=True, timestamp="2026-10-17T00:00:00+08:00"
            )
            if group:
                payload.update(
                    group_id="group",
                    group_openid="group",
                    author=dict(id=user, member_openid=user, member_role=role, bot=False),
                )
                event = GroupAtMessageCreateEvent.model_validate(payload)
            else:
                payload["author"] = dict(id=user, user_openid=user)
                event = C2CMessageCreateEvent.model_validate(payload)
            before = len(calls)
            await handle_event(bot, event)
            return calls[before:]

        await upgrade()
        await driver._lifespan.startup()
        try:
            with (
                patch.object(Bot, "post_c2c_messages", AsyncMock(side_effect=post)),
                patch.object(Bot, "post_group_messages", AsyncMock(side_effect=post)),
                patch.object(services.clients.updater, "request", AsyncMock(side_effect=updater)),
            ):
                for group, role in ((False, "member"), (True, "admin"), (True, "owner")):
                    for command in ("/git", "/git pull", "/git status", "/update", "/git restart"):
                        responses = await send(command, group=group, role=role, user="ordinary")
                        assert len(responses) == 1 and "权限不足" in responses[0]["content"]
                assert not actions
                for group in (False, True):
                    for command in (
                        "/git",
                        "/git status",
                        "/git fetch",
                        "/git log",
                        "/git plog",
                        "/git pull",
                        "/update",
                        "/git restart",
                    ):
                        responses = await send(command, group=group)
                        assert responses, command
                        assert all("处理失败" not in (value.get("content") or "") for value in responses), (
                            command
                        )
                    assert await send("/git pull unexpected", group=group)
                assert [action for action, _ in actions].count("update") == 4
                assert [action for action, _ in actions].count("restart") == 2
                assert services.telemetry.counts.get("failed", 0) == 0
            print(json.dumps({"ok": True}))
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
