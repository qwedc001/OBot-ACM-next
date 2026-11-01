"""Native QQ conversion/API behavior; no real credentials or network."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.exception import FinishedException
from nonebot.matcher import current_bot, current_event

from framework.clients.reply import DeliveryFailed, ReplyClient
from framework.integration.platforms.qq_messages import build_qq_reply, send_qq_to
from framework.messages import (
    Button,
    ButtonAccess,
    ButtonAction,
    ButtonModal,
    ButtonPermission,
    Image,
    Keyboard,
    Markdown,
    MarkdownParam,
    Message,
    SendTarget,
    Text,
)


@pytest.fixture
def client():
    value = ReplyClient()
    value.register("QQ", build_qq_reply, send_qq_to)
    return value


@pytest.fixture
def bot():
    return Bot(
        SimpleNamespace(get_name=lambda: "QQ", config=nonebot.get_driver().config),
        "app",
        BotInfo(id="app", secret="synthetic"),
    )


def event(group=False):
    payload = dict(id="incoming", content="/ping", timestamp="2026-10-12T00:00:00+08:00")
    if group:
        return GroupMessageCreateEvent.model_validate(
            dict(
                **payload,
                group_openid="group",
                group_id="group",
                author=dict(id="member", member_openid="member", member_role="member", bot=False),
            )
        )
    return C2CMessageCreateEvent.model_validate(dict(**payload, author=dict(id="user", user_openid="user")))


@pytest.mark.parametrize("group", [False, True])
async def test_passive_send_handles_plain_group_and_private_native_sequences(client, bot, monkeypatch, group):
    value = event(group)
    api = AsyncMock(return_value={"id": "receipt"})
    monkeypatch.setattr(Bot, "post_group_messages" if group else "post_c2c_messages", api)
    receipts = await asyncio.gather(
        *(client.send("pong", bot=bot, event=value, at_sender=group) for _ in range(2))
    )
    assert receipts == [{"id": "receipt"}] * 2
    calls = [call.kwargs for call in api.await_args_list]
    assert {call["msg_seq"] for call in calls} == {1, 2}
    assert all(call["msg_id"] == "incoming" and call["msg_type"] == 0 for call in calls)
    assert (
        all("<@member>" in call["content"] for call in calls)
        if group
        else all(call["content"] == "pong" for call in calls)
    )


async def test_send_context_finish_and_failed_finish(client, bot, monkeypatch):
    api = AsyncMock(return_value={"id": "reply"})
    monkeypatch.setattr(Bot, "post_c2c_messages", api)
    bot_token, event_token = current_bot.set(bot), current_event.set(event())
    try:
        assert await client.send(Text("first")) == {"id": "reply"}
        with pytest.raises(FinishedException):
            await client.finish("last")
        with pytest.raises(FinishedException):
            await client.finish()
        assert api.await_count == 2
        api.side_effect = RuntimeError("secret SDK response")
        with pytest.raises(DeliveryFailed, match="^Message delivery failed$"):
            await client.finish("failed")
    finally:
        current_bot.reset(bot_token)
        current_event.reset(event_token)
    with pytest.raises(ValueError, match="active matcher"):
        await client.send("outside")


@pytest.mark.parametrize(
    "kind, api_name, id_key",
    [
        ("c2c", "post_c2c_messages", "openid"),
        ("group", "post_group_messages", "group_openid"),
        ("channel", "post_messages", "channel_id"),
        ("dms", "post_dms_messages", "guild_id"),
    ],
)
async def test_send_to_uses_real_sdk_without_passive_event(client, bot, monkeypatch, kind, api_name, id_key):
    api = AsyncMock(return_value={"id": "proactive"})
    monkeypatch.setattr(Bot, api_name, api)
    assert await client.send_to("hello", SendTarget("QQ", kind, "destination", "app"), bot=bot) == {
        "id": "proactive"
    }
    args = api.await_args.kwargs
    assert args[id_key] == "destination" and args["msg_id"] is None and args["event_id"] is None
    assert args["content"] == "hello"


async def test_proactive_bot_selection_is_explicit_or_unique(client, bot, monkeypatch):
    monkeypatch.setattr(
        nonebot,
        "get_bots",
        lambda: {"app": bot, "other": SimpleNamespace(adapter=bot.adapter, self_id="other")},
    )
    api = AsyncMock(return_value={"id": "selected"})
    monkeypatch.setattr(Bot, "post_c2c_messages", api)
    with pytest.raises(ValueError, match="one matching"):
        await client.send_to("hi", SendTarget("QQ", "c2c", "user"))
    assert await client.send_to("hi", SendTarget("QQ", "c2c", "user", "app")) == {"id": "selected"}
    with pytest.raises(ValueError, match="does not match"):
        await client.send_to("hi", SendTarget("QQ", "c2c", "user", "other"), bot=bot)
    with pytest.raises(ValueError, match="Unsupported QQ target"):
        await client.send_to("hi", SendTarget("QQ", "invalid", "user"), bot=bot)
    api.side_effect = RuntimeError("secret SDK response")
    with pytest.raises(DeliveryFailed, match="^Message delivery failed$"):
        await client.send_to("hi", SendTarget("QQ", "c2c", "user"), bot=bot)


@pytest.mark.parametrize("group", [False, True])
async def test_markdown_keyboard_reaches_native_api(client, bot, monkeypatch, group):
    api = AsyncMock(return_value={"id": "markdown"})
    monkeypatch.setattr(Bot, "post_group_messages" if group else "post_c2c_messages", api)
    keyboard = Keyboard(
        rows=(
            (
                Button("Help", "/help", id="help", visited_label="Opened", enter=False),
                Button("Site", "https://example.org", action=ButtonAction.LINK, style=0),
                Button(
                    "Confirm",
                    "opaque-data",
                    action=ButtonAction.CALLBACK,
                    id="confirm",
                    group_id="choice",
                    permission=ButtonPermission(ButtonAccess.USERS, user_ids=("qq-user-id",)),
                    modal=ButtonModal("Continue?"),
                    unsupported_tips="Update QQ",
                ),
            ),
        )
    )
    await client.send(
        Message(Markdown.template("custom", title="Title", items=["a", "b"]), keyboard),
        bot=bot,
        event=event(group),
    )
    args = api.await_args.kwargs
    assert args["msg_type"] == 2
    assert args["markdown"].custom_template_id == "custom"
    assert args["markdown"].params[1].values == ["a", "b"]
    buttons = args["keyboard"].content.rows[0].buttons
    assert buttons[0].action.type == 2 and buttons[0].action.enter is False
    assert buttons[0].render_data.visited_label == "Opened"
    assert buttons[1].action.type == 0 and buttons[1].render_data.style == 0
    assert buttons[2].action.type == 1 and buttons[2].action.permission.specify_user_ids == ["qq-user-id"]
    assert buttons[2].action.modal.content == "Continue?" and buttons[2].group_id == "choice"
    await client.send_to(
        Message(Markdown("# raw"), Keyboard(template_id="keyboard-template")),
        SendTarget("QQ", "group" if group else "c2c", "destination"),
        bot=bot,
    )
    assert api.await_args.kwargs["markdown"].content == "# raw"
    assert api.await_args.kwargs["keyboard"].id == "keyboard-template"


@pytest.mark.parametrize("source", [b"image-bytes", "https://example.org/image.png", Path("image.png")])
async def test_image_upload_remains_sdk_owned(client, bot, monkeypatch, tmp_path, source):
    if isinstance(source, Path):
        source = tmp_path / source
        source.write_bytes(b"image-bytes")
    upload = AsyncMock(return_value=SimpleNamespace(file_info="uploaded"))
    api = AsyncMock(return_value={"id": "image"})
    monkeypatch.setattr(Bot, "post_c2c_upload", upload)
    monkeypatch.setattr(Bot, "post_c2c_files", upload)
    monkeypatch.setattr(Bot, "post_c2c_messages", api)
    await client.send_to(Message("caption", Image(source)), SendTarget("QQ", "c2c", "user"), bot=bot)
    assert upload.await_count == 1
    assert upload.await_args.kwargs["srv_send_msg"] is False
    assert api.await_args.kwargs["msg_type"] == 7 and api.await_args.kwargs["media"].file_info == "uploaded"
    assert api.await_args.kwargs["content"] == "caption"


async def test_image_path_read_does_not_block_event_loop(client, bot, monkeypatch, tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"image")
    started, release = threading.Event(), threading.Event()
    original = Path.read_bytes

    def slow_read(value):
        started.set()
        assert release.wait(5)
        return original(value)

    monkeypatch.setattr(Path, "read_bytes", slow_read)
    send = AsyncMock(return_value="receipt")
    monkeypatch.setattr(Bot, "send", send)
    pending = asyncio.create_task(client.send(Image(path), bot=bot, event=event()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(client.send("quick", bot=bot, event=event()), timeout=1)
        assert not pending.done() and send.await_count == 1
    finally:
        release.set()
        await pending


@pytest.mark.parametrize(
    "message",
    [
        Message(Image(b"a"), Image(b"b")),
        Message(Markdown("a"), Markdown("b")),
        Message(Image(b"a"), Markdown("b")),
        Message(Keyboard(template_id="only")),
        Message(Markdown("a"), Keyboard(template_id="a"), Keyboard(template_id="b")),
        Message(Markdown("a"), Keyboard(rows=((Button("x", "x"),) * 6,))),
    ],
)
async def test_qq_rejects_combinations_instead_of_silently_dropping_parts(message):
    with pytest.raises(ValueError):
        await build_qq_reply(event(), message, False)


def test_message_objects_validate_and_snapshot_mutable_inputs():
    values = ["a"]
    markdown = Markdown.template("id", title=values)
    values.append("b")
    assert markdown.params[0].values == ("a",)
    rows = [[Button("a", "a")]]
    keyboard = Keyboard(rows=rows)
    rows[0].clear()
    assert len(keyboard.rows[0]) == 1
    for build in (
        lambda: Markdown(),
        lambda: Markdown("raw", template_id="both"),
        lambda: Markdown("raw", params=(MarkdownParam("a", ("b",)),)),
        lambda: Image("local.png"),
        lambda: Image(b""),
        lambda: Message(),
        lambda: ButtonPermission(ButtonAccess.USERS),
        lambda: ButtonPermission(ButtonAccess.EVERYONE, user_ids=("user",)),
        lambda: Button("Callback", "data", ButtonAction.CALLBACK),
        lambda: Keyboard(rows=((Button("a", "a", id="same"), Button("b", "b", id="same")),)),
    ):
        with pytest.raises(ValueError):
            build()


async def test_registration_unknown_adapter_and_cancellation(client, bot, monkeypatch):
    with pytest.raises(ValueError, match="already registered"):
        client.register("QQ", build_qq_reply, send_qq_to)
    with pytest.raises(ValueError, match="Unsupported reply adapter"):
        await ReplyClient().send("hi", bot=bot, event=event())
    monkeypatch.setattr(Bot, "send", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await client.send("hi", bot=bot, event=event())


@pytest.mark.parametrize("proactive", [False, True])
async def test_sdk_error_retains_only_numeric_diagnostics(client, bot, monkeypatch, proactive):
    import json

    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.drivers import Response

    error = ActionFailed(
        Response(
            status_code=403,
            headers={},
            content=json.dumps(
                {"code": 400123, "message": "secret-account-password", "data": {"token": "secret-token"}}
            ),
        )
    )
    monkeypatch.setattr(Bot, "post_c2c_messages", AsyncMock(side_effect=error))
    with pytest.raises(DeliveryFailed) as failure:
        if proactive:
            await client.send_to("hello", SendTarget("QQ", "c2c", "user", "app"), bot=bot)
        else:
            await client.send("hello", bot=bot, event=event())
    assert failure.value.status_code == 403 and failure.value.code == 400123
    assert str(failure.value) == "Message delivery failed"
    assert "secret" not in str(failure.value.__dict__)
