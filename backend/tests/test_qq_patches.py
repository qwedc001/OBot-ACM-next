import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.qq import Adapter, Bot, Message, MessageSegment
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.adapters.qq.models import Dispatch, MessageMarkdown, User
from pydantic import ValidationError

from patches import qq_group_reply, qq_mentions, qq_trace


def payload():
    return Dispatch.model_validate(
        {
            "op": 0,
            "s": 1,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {
                "id": "test-event",
                "content": " /whoisthis ",
                "timestamp": "2026-10-17T00:00:00+08:00",
                "group_id": "test-group",
                "group_openid": "test-group",
                "author": {
                    "id": "test-member",
                    "member_openid": "test-member",
                    "bot": False,
                    "member_role": "admin",
                    "diagnostic_extra": "test-only",
                },
                "message_scene": {
                    "source": "default",
                    "ext": ["auth_token=synthetic-secret", "msg_idx=synthetic-index"],
                },
                "parallel_message": {"msg_nodes": [{"content": "evidence", "message_type": 0}]},
            },
        }
    )


async def test_diagnostic_captures_before_schema_and_after_dispatch_without_tokens(tmp_path, monkeypatch):
    recorder = qq_trace.Recorder(
        tmp_path, {"expires_at": time.time() + 30, "group_openid": "test-group", "commands": ["/whoisthis"]}
    )
    original = Adapter.payload_to_event
    monkeypatch.setattr(Adapter, "payload_to_event", staticmethod(original))
    monkeypatch.setattr(Bot, "handle_event", AsyncMock())
    monkeypatch.setattr(qq_trace, "load_recorder", lambda: recorder)
    qq_trace.install_trace()
    installed = Adapter.payload_to_event
    qq_trace.install_trace()
    assert Adapter.payload_to_event is installed
    event = Adapter.payload_to_event(payload())
    await Bot.handle_event(object(), event)
    await asyncio.gather(*recorder.tasks)
    records = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.glob("*.json")]
    raw = next(r for r in records if r["phase"] == "received")
    processed = next(r for r in records if r["phase"] == "processed")
    assert raw["data"]["parallel_message"]["msg_nodes"][0]["content"] == "evidence"
    assert "parallel_message" in processed["data"]
    assert raw["data"]["author"]["diagnostic_extra"] == "test-only"
    assert "diagnostic_extra" not in processed["data"]["author"]
    assert "synthetic-secret" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert raw["data"]["message_scene"]["ext"] == ["auth_token=[redacted]", "msg_idx=synthetic-index"]
    if os.name == "posix":
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in tmp_path.glob("*.json"))


def test_diagnostic_is_group_command_time_scoped_and_bounded(tmp_path):
    request = {"expires_at": time.time() + 30, "group_openid": "test-group", "commands": ["/whoisthis"]}
    recorder = qq_trace.Recorder(tmp_path, request)
    assert recorder.matches({"group_openid": "test-group", "content": "/whoisthis"})
    assert not recorder.matches({"group_openid": "other", "content": "/whoisthis"})
    assert not recorder.matches({"group_openid": "test-group", "content": "unrelated"})
    request["expires_at"] = 0
    assert not recorder.matches({"group_openid": "test-group", "content": "/whoisthis"})
    for index in range(45):
        recorder.write("received", {"index": index})
    assert len(list(tmp_path.glob("*.json"))) == 40
    recorder.write("received", {"oversized": "x" * 262144})
    assert len(list(tmp_path.glob("*.json"))) == 40


def test_diagnostic_redacts_nested_keys_and_signed_urls():
    data = qq_trace.redact(
        {
            "auth_token": "synthetic",
            "nested": [{"url": "https://example.invalid/?rkey=synthetic"}],
            "id": "synthetic-member",
        }
    )
    assert data["auth_token"] == "[redacted]"
    assert data["nested"][0]["url"] == "[url redacted]"
    assert data["id"] == "synthetic-member"


def test_documented_user_mentions_are_accepted_without_mutating_raw_payload():
    dispatch = payload()
    dispatch.data["mentions"] = [
        {"id": "display", "member_openid": "canonical", "bot": False, "username": "User"}
    ]
    with pytest.raises(ValidationError):
        Adapter.payload_to_event(dispatch)
    normalized = qq_mentions.normalize_mentions(dispatch)
    event = Adapter.payload_to_event(normalized)
    assert event.mentions[0].member_openid == "canonical"
    assert event.mentions[0].scope == "single" and not event.mentions[0].is_you
    assert "scope" not in dispatch.data["mentions"][0]


def test_missing_identity_is_never_synthesized_and_other_scenes_are_untouched():
    dispatch = payload()
    assert qq_mentions.normalize_mentions(dispatch) is dispatch
    dispatch.data["mentions"] = [{"id": "display", "username": "User"}]
    assert qq_mentions.normalize_mentions(dispatch) is dispatch
    canonical = {
        "id": "display",
        "member_openid": "canonical",
        "scope": "single",
        "is_you": True,
        "bot": True,
        "username": "Bot",
    }
    dispatch.data["mentions"] = [canonical]
    assert qq_mentions.normalize_mentions(dispatch) is dispatch
    other = dispatch.model_copy(update={"type": "AT_MESSAGE_CREATE"})
    assert qq_mentions.normalize_mentions(other) is other


def test_passive_group_reply_removes_only_redundant_sender_prefix():
    event = GroupAtMessageCreateEvent.model_validate(payload().data)
    native = Message(
        [
            MessageSegment.mention_user(event.author.member_openid),
            MessageSegment.text(" "),
            MessageSegment.text("pong"),
        ]
    )
    result = qq_group_reply.group_passive_message(event, native)
    assert result.extract_plain_text() == "pong"
    assert not result["mention_user"]
    assert native[0].type == "mention_user" and native[1].data["text"] == " "
    another = Message([MessageSegment.mention_user("other"), MessageSegment.text(" hello")])
    assert qq_group_reply.group_passive_message(event, another) is another
    only_mention = MessageSegment.mention_user(event.author.id)
    assert qq_group_reply.group_passive_message(event, only_mention) is only_mention
    # Preserve indentation in caller content after the one separator space.
    indented = Message([MessageSegment.mention_user(event.author.id), MessageSegment.text("   code")])
    assert qq_group_reply.group_passive_message(event, indented).extract_plain_text() == "  code"


def test_raw_markdown_receives_mention_in_body_without_touching_templates():
    model = MessageMarkdown(content="**body**")
    native = Message(
        [MessageSegment.mention_user("canonical"), MessageSegment.text(" "), MessageSegment.markdown(model)]
    )
    result = qq_group_reply.group_markdown_message(native)
    assert result["markdown"][0].data["markdown"].content == '<qqbot-at-user id="canonical" />\n**body**'
    assert model.content == "**body**" and native[0].type == "mention_user"
    assert not result["text"]  # QQ forbids nonempty content alongside Markdown.
    mixed = Message([*native, MessageSegment.text("literal **text**")])
    assert qq_group_reply.group_markdown_message(mixed) is mixed
    template = Message(
        [
            MessageSegment.mention_user("canonical"),
            MessageSegment.markdown(MessageMarkdown(custom_template_id="synthetic")),
        ]
    )
    assert qq_group_reply.group_markdown_message(template) is template
    private = C2CMessageCreateEvent.model_validate(
        {
            "id": "private",
            "content": "/ping",
            "timestamp": "2026-10-17T00:00:00+08:00",
            "author": {"id": "private", "user_openid": "private"},
        }
    )
    assert qq_group_reply.group_passive_message(private, native) is native


async def test_patch_delegates_all_native_reply_identifiers_and_is_idempotent(monkeypatch):
    sent, grouped = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Bot, "send", sent)
    monkeypatch.setattr(Bot, "send_to_group", grouped)
    # AsyncMock invents attributes; wrappers must see a genuinely absent marker.
    sent.__obot_group_reply__ = False
    qq_group_reply.install_group_reply()
    installed = Bot.send
    qq_group_reply.install_group_reply()
    assert Bot.send is installed
    message = Message(
        [MessageSegment.mention_user("target"), MessageSegment.markdown(MessageMarkdown(content="body"))]
    )
    await Bot.send_to_group(SimpleNamespace(), "group", message, "reply", 3, "event", "ref")
    values = grouped.await_args.kwargs
    assert {k: values[k] for k in ("group_openid", "msg_id", "msg_seq", "event_id", "msg_ref_id")} == {
        "group_openid": "group",
        "msg_id": "reply",
        "msg_seq": 3,
        "event_id": "event",
        "msg_ref_id": "ref",
    }


def test_patch_version_guard_leaves_new_sdk_untouched(monkeypatch):
    original = Bot.send
    monkeypatch.setattr(qq_group_reply, "version", lambda _: "99.0.0")
    qq_group_reply.install_group_reply()
    assert Bot.send is original


async def test_patched_native_sdk_group_plain_markdown_and_private_payloads(monkeypatch):
    monkeypatch.setattr(Bot, "send", Bot.send)
    monkeypatch.setattr(Bot, "send_to_group", Bot.send_to_group)
    group, private = AsyncMock(return_value={"id": "reply"}), AsyncMock(return_value={"id": "reply"})
    monkeypatch.setattr(Bot, "post_group_messages", group)
    monkeypatch.setattr(Bot, "post_c2c_messages", private)
    qq_group_reply.install_group_reply()
    bot = Bot(SimpleNamespace(), "app", BotInfo(id="app", secret="synthetic"))
    bot._self_info = User(id="app", username="bot", bot=True)
    event = GroupAtMessageCreateEvent.model_validate(payload().data)
    prefix = MessageSegment.mention_user(event.author.member_openid)
    await bot.send(event, Message([prefix, MessageSegment.text(" "), MessageSegment.text("pong")]))
    assert group.await_args.kwargs["content"] == "pong"
    assert group.await_args.kwargs["msg_id"] == event.id and group.await_args.kwargs["msg_seq"] == 1
    await bot.send(
        event,
        Message(
            [prefix, MessageSegment.text(" "), MessageSegment.markdown(MessageMarkdown(content="**body**"))]
        ),
    )
    assert group.await_args.kwargs["msg_type"] == 2
    assert not group.await_args.kwargs["content"]
    assert group.await_args.kwargs["markdown"].content == '<qqbot-at-user id="test-member" />\n**body**'
    assert group.await_args.kwargs["msg_seq"] == 2
    private_event = C2CMessageCreateEvent.model_validate(
        {
            "id": "private",
            "content": "/ping",
            "timestamp": "2026-10-17T00:00:00+08:00",
            "author": {"id": "private", "user_openid": "private"},
        }
    )
    await bot.send(private_event, MessageSegment.text("pong"))
    assert private.await_args.kwargs["content"] == "pong" and private.await_args.kwargs["msg_seq"] == 1


def test_patched_mentions_native_dispatch_authorization_and_documented_user_schema():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.whoisthis_probe", ".", "--patches"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        capture_output=True,
        encoding="utf-8",
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["ok"]
