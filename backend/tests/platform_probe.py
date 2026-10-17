"""Exercise the unchanged core plugins through a non-QQ synthetic adapter."""

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path

import nonebot
from nonebot.adapters import Adapter, Bot, Event, Message, MessageSegment
from nonebot.message import handle_event


class ProbeSegment(MessageSegment):
    @classmethod
    def get_message_class(cls):
        return ProbeMessage

    def is_text(self):
        return self.type == "text"

    def __str__(self):
        return self.data["text"] if self.is_text() else "[image]"


class ProbeMessage(Message):
    @classmethod
    def get_segment_class(cls):
        return ProbeSegment

    @staticmethod
    def _construct(message):
        yield ProbeSegment("text", {"text": message})


class ProbeEvent(Event):
    id: str
    text: str
    user_id: str
    group_id: str | None = None
    admin: bool = False

    def get_type(self):
        return "message"

    def get_event_name(self):
        return "group.message" if self.group_id else "private.message"

    def get_event_description(self):
        return "synthetic message"

    def get_message(self):
        return ProbeMessage(self.text)

    def get_user_id(self):
        return self.user_id

    def get_session_id(self):
        return f"{self.group_id}:{self.user_id}" if self.group_id else self.user_id

    def is_tome(self):
        return True


class ProbeAdapter(Adapter):
    @classmethod
    def get_name(cls):
        return "Probe"

    async def _call_api(self, bot, api, **data):
        raise AssertionError("No network/API calls in this probe")


class ProbeBot(Bot):
    def __init__(self, adapter, self_id):
        super().__init__(adapter, self_id)
        self.deliveries = []

    async def send(self, event, message, **kwargs):
        self.deliveries.append(("reply", event.id, message))
        return {"message_id": str(len(self.deliveries))}


async def main():
    with tempfile.TemporaryDirectory(prefix="obot-platform-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty, driver="~fastapi+~httpx+~websockets", superusers={"root"}, log_level="ERROR"
        )
        from framework.catalog import CommandSpec
        from framework.clients.reply import UnsupportedContent
        from framework.config import Settings
        from framework.contracts import ExternalIdentity, Role, SceneKind, SceneRef
        from framework.integration.command_context import Reply
        from framework.integration.commands import command
        from framework.integration.lifecycle import install_lifecycle
        from framework.integration.platforms import Incoming, Platform, make_context, register_platform
        from framework.messages import Image, Markdown, SendTarget, Text
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        class ProbePlatform(Platform):
            adapter_type = ProbeAdapter
            supported_segments = frozenset({Text, Image})

            def normalize(self, bot, event):
                assert isinstance(event, ProbeEvent)
                return Incoming(
                    ExternalIdentity("probe", bot.self_id, "person", event.user_id),
                    SceneRef(
                        "probe",
                        bot.self_id,
                        SceneKind.GROUP if event.group_id else SceneKind.C2C,
                        event.group_id or event.user_id,
                    ),
                    event.id,
                )

            async def group_admin(self, bot, event):
                return bool(event.group_id and event.admin)

            async def build_message(self, recipient, message, at_sender):
                parts = []
                for part in message.parts:
                    if isinstance(part, Text):
                        parts.append(ProbeSegment("text", {"text": part.content}))
                    elif isinstance(part, Image):
                        parts.append(ProbeSegment("image", {"picture": part.source}))
                    else:
                        raise NotImplementedError("Probe does not support this content type")
                return ProbeMessage(parts)

            async def send_to(self, bot, target, message):
                if target.kind not in {"person", "room"}:
                    raise ValueError("Invalid Probe target")
                bot.deliveries.append(("active", target.id, message))
                return {"message_id": str(len(bot.deliveries))}

        register_platform(ProbePlatform())

        class TextAdapter(ProbeAdapter):
            @classmethod
            def get_name(cls):
                return "TextOnly"

        class TextPlatform(ProbePlatform):
            adapter_type = TextAdapter
            supported_segments = frozenset({Text})

        register_platform(TextPlatform())
        services = configure_services(Settings(obot_data_dir=root / "data", obot_platforms=()))
        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.maintenance")
        assert nonebot.load_plugin("plugins.help")
        assert nonebot.load_plugin("plugins.debug")
        assert nonebot.load_plugin("plugins.fun")
        assert nonebot.load_plugin("plugins.board")
        manage = command(
            CommandSpec("probe.manage", "probe", "manage", "Manage", permission=Role.GROUP_ADMIN), block=True
        )

        @manage.handle()
        async def manage_handler(reply: Reply):
            await reply.finish("managed")

        bot = ProbeBot(nonebot.get_driver()._adapters["Probe"], "same-app")
        counter = 0

        async def request(text, user="member", group=None, admin=False, *, expect_reply=True):
            nonlocal counter
            counter += 1
            event = ProbeEvent(id=str(counter), text=text, user_id=user, group_id=group, admin=admin)
            before = len(bot.deliveries)
            await handle_event(bot, event)
            if not expect_reply:
                assert len(bot.deliveries) == before
                return event, None
            assert len(bot.deliveries) == before + 1
            return event, bot.deliveries[-1][2]

        await upgrade()
        await nonebot.get_driver()._lifespan.startup()
        try:
            _, denial = await request("/ping")
            assert "权限不足" in denial.extract_plain_text()
            _, help_text = await request("/help text")
            assert (
                "/help" in help_text.extract_plain_text()
                and "/maintenance.ping" not in help_text.extract_plain_text()
                and "/maintenance.whoami" in help_text.extract_plain_text()
            )
            for group in (None, "room"):
                _, pong = await request("/ping", user="root", group=group)
                assert pong.extract_plain_text() == "pong"
                value, whoami = await request("/whoami", user="root", group=group)
                context = await make_context(services, bot, value)
                assert context.scene.provider == "probe" and context.scene.key.startswith("probe:")
                assert "probe:" in whoami.extract_plain_text()
            for group, admin in ((None, True), ("room", False), ("room", True)):
                _, response = await request("/probe.manage", group=group, admin=admin)
                assert (response.extract_plain_text() == "managed") == bool(group and admin)
            for group in (None, "room"):
                for user in ("root", "member"):
                    for entry in ("/fun", "/maintenance", "/maintainence", "/probe"):
                        await request(entry, user=user, group=group, expect_reply=False)
                _, module = await request("/help text fun", group=group)
                assert "/fun.hitokoto" in module.extract_plain_text()
            qq_user = await services.identities.resolve(
                ExternalIdentity("qq", "same-app", "person", "member")
            )
            probe_user = await services.identities.resolve(
                ExternalIdentity("probe", "same-app", "person", "member")
            )
            assert qq_user.id != probe_user.id
            assert (
                SceneRef("qq", "same-app", SceneKind.C2C, "member").key
                != SceneRef("probe", "same-app", SceneKind.C2C, "member").key
            )
            _, image_help = await request("/help", user="root")
            render = (
                importlib.util.find_spec("pixie") is not None
                and importlib.util.find_spec("easy_pixie") is not None
            )
            if render:
                images = [part.data["picture"] for part in image_help if part.type == "image"]
                assert len(images) == 1 and images[0].startswith(b"\x89PNG\r\n\x1a\n")
            receipt = await services.clients.reply.send_to(
                "active", SendTarget("Probe", "room", "room"), bot=bot
            )
            assert receipt["message_id"] and bot.deliveries[-1][0:2] == ("active", "room")
            try:
                await services.clients.reply.send_to(
                    Markdown("unsupported"), SendTarget("Probe", "person", "member"), bot=bot
                )
            except UnsupportedContent:
                pass
            else:
                raise AssertionError("Unsupported content must not disappear silently")
            text_bot = ProbeBot(nonebot.get_driver()._adapters["TextOnly"], "text-app")
            await handle_event(text_bot, ProbeEvent(id="text-help", text="/help", user_id="root"))
            assert len(text_bot.deliveries) == 1
            assert "/maintenance.ping" in text_bot.deliveries[0][2].extract_plain_text()
            assert all(part.is_text() for part in text_bot.deliveries[0][2])
            await handle_event(text_bot, ProbeEvent(id="board-directory", text="/board", user_id="member"))
            assert "/board today" in text_bot.deliveries[-1][2].extract_plain_text()
            await handle_event(
                text_bot, ProbeEvent(id="board-image-unavailable", text="/board today", user_id="member")
            )
            assert "不支持图片" in text_bot.deliveries[-1][2].extract_plain_text()
            await handle_event(text_bot, ProbeEvent(id="text-module", text="/help fun", user_id="member"))
            module = text_bot.deliveries[-1][2]
            assert "/fun.hitokoto" in module.extract_plain_text() and all(part.is_text() for part in module)
            from unittest.mock import AsyncMock, patch

            with patch.object(
                services.clients.http,
                "get_json",
                AsyncMock(return_value={"hitokoto": "跨平台一言", "from": "测试", "from_who": None}),
            ):
                await handle_event(text_bot, ProbeEvent(id="public-quote", text="/一言", user_id="member"))
                assert "跨平台一言" in text_bot.deliveries[-1][2].extract_plain_text()
            for choice in ("markdown", "button"):
                await handle_event(
                    text_bot, ProbeEvent(id=f"debug-{choice}", text=f"/debug {choice}", user_id="root")
                )
                assert "当前平台未实现" in text_bot.deliveries[-1][2].extract_plain_text()
            assert services.telemetry.counts.get("failed", 0) == 0
            print(
                json.dumps(
                    {
                        "ok": True,
                        "generic_handlers": True,
                        "qualified_identities": True,
                        "platform_roles": True,
                        "proactive_send": True,
                        "unsupported_content_explicit": True,
                        "graphical_help": render,
                    }
                )
            )
        finally:
            await nonebot.get_driver()._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
