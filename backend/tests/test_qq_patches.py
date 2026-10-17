import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

from nonebot.adapters.qq import Adapter, Bot
from nonebot.adapters.qq.models import Dispatch

from patches import qq_trace


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
    raw = next(r for r in records if "parallel_message" in r["data"])
    processed = next(r for r in records if "message" in r)
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
