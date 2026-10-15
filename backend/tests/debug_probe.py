"""Run administrator debug commands through native QQ dispatch/API conversion."""

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
    with tempfile.TemporaryDirectory(prefix="obot-debug-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
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
        assert nonebot.load_plugin("plugins.maintenance")
        assert nonebot.load_plugin("plugins.help")
        assert nonebot.load_plugin("plugins.debug")
        driver = nonebot.get_driver()
        bot = Bot(driver._adapters["QQ"], "app", BotInfo(id="app", secret="synthetic"))
        calls = []
        counter = 0
        reject_rich = False

        async def post(**kwargs):
            calls.append(kwargs)
            if reject_rich and kwargs["msg_type"] == 2:
                raise RuntimeError("synthetic SDK response must not be exposed")
            return {"id": "reply"}

        async def request(text, *, group=False, user="operator", role="member", failure=False):
            nonlocal counter, reject_rich
            counter += 1
            reject_rich = failure
            payload = dict(
                id=f"event-{counter}", content=text, to_me=True, timestamp="2026-10-12T00:00:00+08:00"
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
            ):
                for group, role in ((False, "member"), (True, "member"), (True, "admin"), (True, "owner")):
                    for command in ("/debug", "/debug markdown", "/debug button"):
                        responses = await request(command, group=group, role=role, user="ordinary")
                        assert len(responses) == 1
                        assert responses[0]["msg_type"] == 0 and "权限不足" in responses[0]["content"]
                    responses = await request("/help text", group=group, role=role, user="ordinary")
                    assert "/debug" not in responses[0]["content"]
                    responses = await request("/help debug", group=group, role=role, user="ordinary")
                    assert "没有找到" in responses[0]["content"]

                for group in (False, True):
                    for command in ("/debug", "/help text debug"):
                        responses = await request(command, group=group)
                        assert len(responses) == 1
                        assert "/debug markdown" in responses[0]["content"]
                        assert "/debug button" in responses[0]["content"]
                    responses = await request("/debug markdown", group=group)
                    assert len(responses) == 1
                    markdown = responses[0]
                    assert markdown["msg_type"] == 2 and markdown.get("keyboard") is None
                    assert markdown["markdown"].content.startswith("# Lowlight")
                    assert "**加粗**" in markdown["markdown"].content
                    assert "http" not in markdown["markdown"].content
                    responses = await request("/debug button", group=group)
                    assert len(responses) == 1
                    card = responses[0]
                    assert card["msg_type"] == 2 and card["markdown"] is not None
                    rows = card["keyboard"].content.rows
                    assert len(rows) <= 5 and all(len(row.buttons) <= 5 for row in rows)
                    buttons = {button.id: button for row in rows for button in row.buttons}
                    link = buttons["debug_link"].action
                    assert link.type == 0 and link.data == "https://q.qq.com/"
                    for key in ("debug_fill_ping", "debug_fill_help", "debug_draft"):
                        assert buttons[key].action.type == 2 and buttons[key].action.enter is False
                    ping = buttons["debug_send_ping"].action
                    assert ping.data == "/ping" and ping.enter is True
                    status = buttons["debug_confirm_status"].action
                    assert status.data == "/status" and status.modal.confirm_text == "发送"
                    assert status.modal.cancel_text == "取消"
                    assert buttons["debug_markdown"].action.data == "/debug markdown"
                    for subcommand in ("markdown", "button"):
                        responses = await request(f"/debug {subcommand}", group=group, failure=True)
                        assert len(responses) == 2
                        assert responses[0]["msg_type"] == 2 and responses[1]["msg_type"] == 0
                        assert [value["msg_seq"] for value in responses] == [1, 2]
                        assert "测试消息发送失败" in responses[1]["content"]
                        assert "synthetic" not in responses[1]["content"]
                    responses = await request("/debug markdown extra", group=group)
                    assert len(responses) == 1 and "未知" in responses[0]["content"]
                    assert await request("/debugging", group=group) == []
            assert services.telemetry.counts["delivery_failed"] == 4
            assert services.telemetry.counts.get("failed", 0) == 0
            print(
                json.dumps(
                    {
                        "ok": True,
                        "superuser_only": True,
                        "private_and_group": True,
                        "native_rich_payloads": True,
                        "failed_delivery_explicit": True,
                    }
                )
            )
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
