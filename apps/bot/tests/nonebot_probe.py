"""Actual matchers/native permissions, synthetic QQ events, no network or credentials."""

import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot, Message
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupAtMessageCreateEvent,
    GroupMessageCreateEvent,
    MessageEvent,
)
from nonebot.matcher import current_matcher
from nonebot.message import handle_event


async def main():
    separator = sys.argv[1] if len(sys.argv) > 1 else "."
    prefix = f"maintenance{separator}"
    render_enabled = (
        importlib.util.find_spec("pixie") is not None and importlib.util.find_spec("easy_pixie") is not None
    )
    with tempfile.TemporaryDirectory(prefix="obot-test-") as temporary:
        root = Path(temporary)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            log_level="ERROR",
            command_sep={separator},
            command_start={"/", "!"},
            superusers={"native-config-root"},
        )
        driver = nonebot.get_driver()
        from framework.integration.platforms.qq_transport import QQAdapter

        driver.register_adapter(QQAdapter)
        from nonebot.params import CommandArg

        from framework.admin import apply, parser
        from framework.catalog import CommandSpec, catalog
        from framework.config import Settings
        from framework.contracts import ExternalIdentity, Role, SceneKind
        from framework.integration.command_context import BotServices, Reply
        from framework.integration.commands import command
        from framework.integration.lifecycle import install_lifecycle
        from framework.integration.permissions import EVERYONE, GROUP_ADMIN, SUPERUSER, command_check
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        catalog.configure_separator(separator)
        services = configure_services(Settings(obot_data_dir=root / "data"))
        from framework.messages import Message as ReplyMessage

        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.maintenance")
        assert nonebot.load_plugin("plugins.help")

        manage_matcher = command(
            CommandSpec("test.manage", "probe", "manage", "Group management", permission=Role.GROUP_ADMIN),
            priority=10,
            block=True,
        )
        priority_trace = []
        before_manage = nonebot.on_command(
            ("probe", "manage"), priority=5, block=False, force_whitespace=True
        )
        after_manage = nonebot.on_command(
            ("probe", "manage"), priority=20, block=False, force_whitespace=True
        )

        @before_manage.handle()
        async def earlier(event: MessageEvent):
            priority_trace.append((event.id, "before"))

        @after_manage.handle()
        async def later(event: MessageEvent):
            priority_trace.append((event.id, "after"))

        @manage_matcher.handle()
        async def manage(
            services: BotServices, reply: Reply, event: MessageEvent, args: Annotated[Message, CommandArg()]
        ):
            args = args.extract_plain_text().strip()
            priority_trace.append((event.id, "handler"))
            if args == "multi":
                await asyncio.gather(
                    services.clients.reply.send(ReplyMessage("first")),
                    services.clients.reply.send(ReplyMessage("second")),
                )
                return None
            if args == "finish":
                await services.clients.reply.finish("finished")
                raise AssertionError("finish must stop command execution")
            if args == "ordered 中文":
                await reply.send("started")
                result = await services.clients.http.get_json("https://example.test/work")
                await reply.finish(result["reply"])
                raise AssertionError("finish must stop command execution")
            if args == "return":
                return "must not become a reply"
            if args == "pause":
                current_matcher.get().state["probe_followup"] = True
                await reply.send("continue?")
                await current_matcher.get().pause()
            await reply.send("managed")

        @manage_matcher.handle()
        async def followup(reply: Reply, event: MessageEvent):
            if current_matcher.get().state.get("probe_followup"):
                await reply.finish(f"continued: {event.get_plaintext()}")

        bot = Bot(
            driver._adapters[QQAdapter.get_name()],
            "test-app",
            BotInfo(id="test-app", secret="synthetic", use_websocket=False),
        )
        replies, images, models, deliveries = [], [], [], []
        services.get("help")
        renderer = services.clients.renderer
        native = renderer.render_pages

        async def draw(actor, view, model):
            models.append(model)
            return await native(actor, view, model)

        # Observe the parent-side request; worker backends must stay importable.
        renderer.render_pages = draw

        async def send(**kwargs):
            content = kwargs["message"]
            mentions = [segment for segment in content if segment.type == "mention_user"]
            assert bool(mentions) == ("group_openid" in kwargs)
            deliveries.append((kwargs["msg_id"], kwargs["msg_seq"]))
            if not replies:
                await asyncio.sleep(0.02)
            if isinstance(content, Message):
                images.extend(segment.data["content"] for segment in content if segment.type == "file_image")
                replies.append(
                    content.extract_plain_text().lstrip() if mentions else content.extract_plain_text()
                )
            else:
                replies.append(str(content))
            return {"id": "reply"}

        counter = 0

        def event(command, *, role="private", user="test-user", ordinary=False):
            nonlocal counter
            counter += 1
            payload = dict(
                id=f"event-{counter}", content=command, to_me=True, timestamp="2026-10-12T00:00:00+08:00"
            )
            if role == "private":
                payload["author"] = dict(id=user, user_openid=user)
                return C2CMessageCreateEvent.model_validate(payload)
            payload.update(
                author=dict(id=user, member_openid=user, bot=False, member_role=role),
                group_openid="group",
                group_id="group",
            )
            if role is None:
                payload["author"]["member_role"] = "member"
            value = (GroupMessageCreateEvent if ordinary else GroupAtMessageCreateEvent).model_validate(
                payload
            )
            if ordinary:
                value.to_me = False
            if role is None:
                # Validate an otherwise normal event, then simulate unavailable
                # role metadata. NoneBot's native admin checks must fail closed.
                value.author.member_role = None
            return value

        async def request(command, **kwargs):
            before = len(replies)
            value = event(command, **kwargs)
            await handle_event(bot, value)
            assert len(replies) == before + 1
            return value, replies[-1]

        await upgrade()
        await driver._lifespan.startup()
        try:
            with (
                patch.object(Bot, "send_to_c2c", AsyncMock(side_effect=send)),
                patch.object(Bot, "send_to_group", AsyncMock(side_effect=send)),
            ):
                first, denial = await request("/ping")
                assert "权限不足" in denial
                await handle_event(bot, first)
                assert len(replies) == 1
                _, default = await request("/help")
                assert "2 条" in default if render_enabled else "/help" in default
                _, text = await request("/help text")
                assert "/help —" in text and f"/{prefix}ping" not in text and f"/{prefix}whoami" in text
                _, public_identity = await request("/whoami")
                assert "普通用户" in public_identity
                _, missing = await request("/help ping")
                assert "没有找到" in missing
                _, denial = await request("/status")
                assert "权限不足" in denial
                for role in ("member", "admin", "owner", None):
                    value, reply = await request(f"/probe{separator}manage", role=role, user="other")
                    allowed = role in {"admin", "owner"}
                    assert await EVERYONE(bot, value)
                    assert bool(await GROUP_ADMIN(bot, value)) == allowed
                    assert not await SUPERUSER(bot, value)
                    assert (reply == "managed") == allowed
                    _, help_text = await request("/help text", role=role, user="other")
                    assert (f"/probe{separator}manage" in help_text) == allowed
                    assert f"/{prefix}ping" not in help_text and f"/{prefix}whoami" in help_text
                    _, identity_text = await request("/whoami", role=role, user="other")
                    assert ("群管理员" if allowed else "普通用户") in identity_text
                    _, denial = await request("/ping", role=role, user="other")
                    assert "权限不足" in denial
                value, denial = await request(f"/probe{separator}manage", user="other")
                assert not await GROUP_ADMIN(bot, value) and "权限不足" in denial
                _, help_text = await request("/help text", user="other")
                assert "probe" not in help_text

                # Explicitly enroll/link fresh accounts; no migration of old records.
                private_key = ExternalIdentity("qq", "test-app", "c2c_user", "test-user")
                group_key = ExternalIdentity("qq", "test-app", "group_member", "test-user")
                owner = await services.identities.resolve(private_key)
                await services.identities.resolve(group_key)
                await services.identities.link(group_key.key, owner.id)
                await apply(services.session_factory, parser().parse_args(["superuser", "add", owner.id]))
                both = []
                for role in ("private", "member"):
                    actual = []
                    for command_text in (
                        "/ping",
                        f"/{prefix}ping",
                        "/help text",
                        "/help text maintenance",
                        f"/{prefix}whoami",
                        "/status",
                        "/help",
                    ):
                        value, reply = await request(command_text, role=role)
                        assert await SUPERUSER(bot, value) and await GROUP_ADMIN(bot, value)
                        assert await command_check(bot, value)(
                            CommandSpec("scope", "probe", "scope", "scope", scopes=frozenset({SceneKind.C2C}))
                        )
                        actual.append(reply)
                    assert actual[0] == actual[1] == "pong"
                    assert f"/{prefix}ping" in actual[2] and "/help —" in actual[2]
                    assert "/help —" not in actual[3] and f"/{prefix}whoami" in actual[3]
                    assert "超级管理员" in actual[4] and owner.id in actual[4]
                    from framework import __version__

                    assert f"OBot {__version__}" in actual[5]
                    both.append(actual)
                assert both[0][2:4] == both[1][2:4]
                _, compatible = await request("/help render")
                assert "6 条" in compatible if render_enabled else "/help" in compatible
                native_root = event("/ping", user="native-config-root")
                assert await SUPERUSER(bot, native_root)
                multiple = event(f"/probe{separator}manage multi", user="native-config-root")
                await handle_event(bot, multiple)
                assert set(replies[-2:]) == {"first", "second"}
                assert {seq for message_id, seq in deliveries if message_id == multiple.id} == {1, 2}
                _, finished = await request(f"/probe{separator}manage finish", user="native-config-root")
                assert finished == "finished"
                _, plain_group = await request(
                    "/ping", role="member", user="native-config-root", ordinary=True
                )
                assert plain_group == "pong"
                _, alternate_start = await request("!ping", user="native-config-root")
                assert alternate_start == "pong"
                before = len(replies)
                for text in ("hello", f"/probe{separator}manage return"):
                    await handle_event(bot, event(text, user="native-config-root"))
                assert len(replies) == before
                _, unknown = await request("/pingpong", user="native-config-root")
                assert "指令匹配失败 /pingpong" in unknown and "/ping" in unknown

                waiting, release = asyncio.Event(), asyncio.Future()

                async def slow_client(endpoint):
                    assert endpoint == "https://example.test/work"
                    waiting.set()
                    return await release

                ordered = event(f"/probe{separator}manage ordered 中文", user="native-config-root")
                with patch.object(services.clients.http, "get_json", AsyncMock(side_effect=slow_client)):
                    pending = asyncio.create_task(handle_event(bot, ordered))
                    try:
                        await asyncio.wait_for(waiting.wait(), 2)
                        assert replies[-1] == "started" and not pending.done()
                        _, other = await request("/ping", user="native-config-root")
                        assert other == "pong" and not pending.done()
                    finally:
                        release.set_result({"reply": "completed"})
                        await pending
                assert replies[-3:] == ["started", "pong", "completed"]
                assert [seq for message_id, seq in deliveries if message_id == ordered.id] == [1, 2]
                assert not any(phase == "after" for _, phase in priority_trace)
                assert priority_trace[-2:] == [(ordered.id, "before"), (ordered.id, "handler")]

                _, paused = await request(f"/probe{separator}manage pause")
                assert paused == "continue?"
                await apply(services.session_factory, parser().parse_args(["superuser", "remove", owner.id]))
                _, resumed = await request("next")
                assert "权限不足" in resumed
                await apply(services.session_factory, parser().parse_args(["superuser", "add", owner.id]))
                _, paused = await request(f"/probe{separator}manage pause")
                _, resumed = await request("next")
                assert resumed == "continued: next"
                before_failed_delivery = len(replies)
                with patch.object(
                    Bot, "send_to_c2c", AsyncMock(side_effect=RuntimeError("secret SDK detail"))
                ):
                    await handle_event(bot, event("/ping", user="native-config-root"))
                await apply(services.session_factory, parser().parse_args(["superuser", "remove", owner.id]))
                for role in ("private", "member"):
                    value = event("/status", role=role)
                    assert not await SUPERUSER(bot, value)

            assert len(replies) == before_failed_delivery
            if render_enabled:
                assert len(images) == 4 and all(data.startswith(b"\x89PNG\r\n\x1a\n") for data in images)
                assert models[0]["count"] == 2 and all(model["count"] == 6 for model in models[1:])
                if len(sys.argv) > 2:
                    Path(sys.argv[2]).write_bytes(images[1])
            outcomes = services.telemetry.snapshot()["command_outcomes"]
            assert outcomes["denied"] >= 9
            assert outcomes["delivery_failed"] == 1
            assert outcomes.get("failed", 0) == 0
            assert services.telemetry.last_sent_at is not None
            assert all(run.delivery_ms <= run.duration_ms + 0.1 for run in services.telemetry.runs)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "public_identity_roles": True,
                        "duplicate_suppressed": True,
                        "superuser_scene_equivalence": True,
                        "canonical_paths_and_aliases": True,
                        "three_native_roles": True,
                        "portable_reply": True,
                        "adapter_reply_sequence": True,
                        "delivery_failure_recorded": True,
                        "group_mentions_sender": True,
                        "native_priority_and_block": True,
                        "ordered_client_replies": True,
                        "native_arguments_and_start": True,
                        "return_not_sent": True,
                        "session_reauthorization": True,
                        "graphical_help": render_enabled,
                    }
                )
            )
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
