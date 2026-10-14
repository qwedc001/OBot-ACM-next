"""Exercise real QQ native matchers, scoped binding web and board delivery."""

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
    with tempfile.TemporaryDirectory(prefix="obot-board-probe-") as directory:
        root = Path(directory)
        empty = root / "empty.env"
        empty.write_text("")
        nonebot.init(_env_file=empty, driver="~fastapi+~httpx+~websockets", qq_bots=[], log_level="ERROR")
        from framework.config import Settings
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        services = configure_services(
            Settings(obot_data_dir=root / "data", obot_public_base_url="https://bot.example.com")
        )
        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.help")
        assert nonebot.load_plugin("plugins.board")
        from plugins.board import board
        from plugins.help import help_command

        help_cases = [
            ("", True, ""),
            ("maintenance", True, "maintenance"),
            ("render", True, ""),
            ("R ping", True, "ping"),
            ("draw tools.nested", True, "tools.nested"),
            ("d status", True, "status"),
            ("renderer", True, "renderer"),
            ("text", False, ""),
            ("text maintenance", False, "maintenance"),
            ("TEXT ping", False, "ping"),
        ]
        for arguments, draw, query in help_cases:
            result = help_command.command().parse("/help " + arguments)
            assert result.matched, result.error_info
            assert (result.query("text") is None) == draw
            assert result.all_matched_args.get("query", "") == query
        handlers = {handler.call.__name__ for handler in board.handlers}
        assert {
            "handle_board_bind",
            "handle_board_today",
            "handle_board_user_id",
            "handle_board_starred",
        } <= handlers

        await upgrade()
        await nonebot.get_driver()._lifespan.startup()
        bot = Bot(nonebot.get_adapter("QQ"), "app", BotInfo(id="app", secret="synthetic"))
        deliveries, counter = [], 0

        async def send(**kwargs):
            deliveries.append(kwargs["message"])
            return {"id": "reply"}

        async def request(text, group=False, role="member"):
            nonlocal counter
            counter += 1
            payload = {
                "id": str(counter),
                "content": text,
                "to_me": True,
                "timestamp": "2026-10-13T00:00:00+08:00",
            }
            if group:
                payload.update(
                    group_id="room",
                    group_openid="room",
                    author={"id": "user", "member_openid": "user", "member_role": role, "bot": False},
                )
                event = GroupAtMessageCreateEvent.model_validate(payload)
            else:
                payload["author"] = {"id": "user", "user_openid": "user"}
                event = C2CMessageCreateEvent.model_validate(payload)
            before = len(deliveries)
            await handle_event(bot, event)
            assert len(deliveries) > before
            return deliveries[before:]

        try:
            with (
                patch.object(Bot, "send_to_c2c", AsyncMock(side_effect=send)),
                patch.object(Bot, "send_to_group", AsyncMock(side_effect=send)),
                patch.object(services.clients.hydro, "validate", AsyncMock()),
            ):
                assert "bind" in str((await request("/board"))[0])
                assert "bind" not in str((await request("/board", group=True))[0])
                assert "starred" in str((await request("/board"))[0])
                assert "starred" not in str((await request("/board", group=True))[0])
                assert "权限不足" in str((await request("/board bind groupOJ", group=True))[0])
                private = str((await request("/board bind privateOJ"))[0]).split("#")[-1]
                await services.boards.submit(private, "https://oj.example.com", "admin", "password")
                assert "privateoj" in str((await request("/board list"))[0])
                assert "已关闭" in str((await request("/board starred off privateOJ"))[0])
                assert "已开启" in str((await request("/board starred"))[0])
                assert "已关闭" in str((await request("/board starred toggle"))[0])
                assert "已开启" in str((await request("/board starred on"))[0])
                assert "尚未绑定" in str((await request("/board list", group=True))[0])
                group_token = str((await request("/board bind groupOJ", group=True, role="admin"))[0]).split(
                    "#"
                )[-1]
                await services.boards.submit(group_token, "https://oj.example.com", "admin", "password")
                assert "没有" in str((await request("/board confirm groupOJ"))[0])
                assert "已绑定" in str((await request("/board confirm groupOJ", group=True, role="admin"))[0])
                assert "groupoj" in str((await request("/board list", group=True))[0])
                assert "权限不足" in str((await request("/board starred off", group=True))[0])
                assert "已关闭" in str((await request("/board starred off", group=True, role="admin"))[0])
                assert "已开启" in str((await request("/board starred", group=True, role="admin"))[0])
                assert "用法" in str((await request("/board starred on extra extra"))[0])
                assert "私聊" in str((await request("/board user id 2", group=True))[0])
                assert "v1.2.5" in str((await request("/board version"))[0])
                assert "已开启" in str((await request("/board push on", group=True, role="admin"))[0])
                assert "权限不足" in str((await request("/board push off", group=True))[0])
                assert "已关闭" in str((await request("/board push off", group=True, role="admin"))[0])
                assert "尚未" not in str((await request("/help text cp"))[0])
                from framework.contracts import ExternalIdentity

                actor = await services.identities.resolve(ExternalIdentity("qq", "app", "c2c_user", "user"))
                artifact = await services.clients.artifacts.create(
                    actor, b"\x89PNG\r\n\x1a\nsynthetic", "image/png"
                )
                with patch.object(services.boards, "image", AsyncMock(return_value=artifact)):
                    messages = await request("/board today")
                    assert len(messages) == 2 and "正在" in str(messages[0])
                    assert any(part.type == "file_image" for part in messages[1])
                with patch.object(services.boards, "image", AsyncMock(return_value=artifact)) as image:
                    for invocation, mode, day, verdict in [
                        ("/board today privateOJ", "now", None, "AC"),
                        ("/cp.board yesterday privateOJ", "full", None, "AC"),
                        ("/board history 2026-01-01 privateOJ", "full", "2026-01-01", "AC"),
                        ("/board verdict WA privateOJ", "now", None, "WA"),
                    ]:
                        messages = await request(invocation)
                        assert len(messages) == 2
                        assert image.await_args.args[1:] == (mode, "privateOJ")
                        assert image.await_args.kwargs == {"day": day, "verdict": verdict}
                    before = image.await_count
                    for malformed in (
                        "/board tomorrow",
                        "/board bind",
                        "/board today a b",
                        "/board push invalid",
                    ):
                        assert "参数格式" in str((await request(malformed))[0])
                    assert image.await_count == before
                with patch.object(services.boards, "user", AsyncMock(return_value="profile")) as user:
                    assert "profile" in str((await request('/board user name "Alice Smith" privateOJ'))[0])
                    assert user.await_args.args[1:] == ("name", "Alice Smith", "privateOJ")
                    assert "profile" in str((await request("/board user id 2 privateOJ"))[0])
                    assert user.await_args.args[1:] == ("id", "2", "privateOJ")
                assert "已注册" in str((await request("/board push status", group=True, role="admin"))[0])
                assert "权限不足" in str((await request("/board push status", group=True))[0])
                assert "权限不足" in str((await request("/board push test", group=True))[0])
                assert "已开启" in str((await request("/board push on", group=True, role="admin"))[0])
                from framework.clients.reply import DeliveryFailed

                with (
                    patch.object(services.boards, "image_for_config", AsyncMock(return_value=artifact)),
                    patch.object(services.clients.artifacts, "read", AsyncMock(return_value=b"PNG")),
                    patch.object(services.clients.reply, "send_to", AsyncMock()) as proactive,
                ):
                    response = await request("/board push test", group=True, role="admin")
                    assert len(response) == 2 and "主动接口" in str(response[-1])
                    assert proactive.await_args.args[1].kind == "group"
                    proactive.side_effect = DeliveryFailed(status_code=403, code=400123)
                    response = await request("/board push test", group=True, role="admin")
                    assert "HTTP 403" in str(response[-1]) and "400123" in str(response[-1])
                assert "已关闭" in str((await request("/board starred privateOJ"))[0])
                # Board generation is separately tested with the real renderer; this probe checks routing and authorization.
                assert "解除绑定" in str((await request("/board unbind privateOJ"))[0])
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "scoped_binding": True,
                            "group_confirmation": True,
                            "native_commands": True,
                            "push_permissions": True,
                        }
                    )
                )
        finally:
            await nonebot.get_driver()._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
