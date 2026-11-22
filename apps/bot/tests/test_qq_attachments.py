"""Task161 synthetic contract probes; no production messages or credentials."""

import copy
from types import SimpleNamespace

import pytest
from nonebot.adapters.qq import Adapter
from nonebot.adapters.qq.bot import _check_reply
from nonebot.adapters.qq.models import Dispatch

from framework.integration.platforms.qq import QQPlatform

URL = "https://multimedia.nt.qq.com.cn/synthetic-image"
REFERENCE_URL = "https://gchat.qpic.cn/synthetic-reference"


def event(kind, **updates):
    data = {
        "id": "synthetic-message",
        "content": "/pickone",
        "timestamp": "2026-11-22T00:00:00+08:00",
        "author": {"id": "synthetic-user", "user_openid": "synthetic-user"},
    }
    if kind != "C2C_MESSAGE_CREATE":
        data.update(group_id="synthetic-group", group_openid="synthetic-group")
        data["author"] = {
            "id": "synthetic-user",
            "member_openid": "synthetic-user",
            "bot": False,
            "member_role": "member",
        }
    data.update(updates)
    payload = Dispatch.model_validate({"op": 0, "s": 1, "t": kind, "d": data})
    snapshot = copy.deepcopy(payload.data)
    parsed = Adapter.payload_to_event(payload)
    assert payload.data == snapshot
    return parsed


KINDS = ["C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "mime,segment",
    [("image/png", "image"), ("application/pdf", "file"), ("audio/silk", "audio"), ("video/mp4", "video")],
)
def test_top_level_attachments_return_in_native_message(kind, mime, segment):
    parsed = event(kind, attachments=[{"content_type": mime, "url": URL}])
    assert parsed.attachments[0].url == URL
    assert parsed.get_message()[segment][0].data["url"] == URL
    assert QQPlatform().image_attachments(parsed) == ((URL,) if segment == "image" else ())


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("attachments", [None, []])
def test_absent_attachment_does_not_invent_image(kind, attachments):
    parsed = event(kind, attachments=attachments)
    assert not parsed.get_message()["image"]
    assert QQPlatform().image_attachments(parsed) == ()


@pytest.mark.parametrize("kind", KINDS)
async def test_reference_attachments_are_preserved_but_not_current_upload(kind):
    parsed = event(
        kind,
        message_type=103,
        message_scene={"source": "default", "ext": ["ref_msg_idx=synthetic-ref"]},
        attachments=[{"content_type": "image/png", "url": URL}],
        msg_elements=[
            {
                "content": "quoted",
                "msg_idx": "synthetic-ref",
                "attachments": [{"content_type": "image/png", "url": REFERENCE_URL}],
            }
        ],
    )
    assert parsed.msg_elements[0].attachments[0].url == REFERENCE_URL
    await _check_reply(SimpleNamespace(), parsed)
    assert parsed.reply.attachments[0].url == REFERENCE_URL
    assert parsed.get_reply_message()["image"][0].data["url"] == REFERENCE_URL
    assert [s.data["url"] for s in parsed.get_message()["image"]] == [URL]
    assert QQPlatform().image_attachments(parsed) == (URL,)


@pytest.mark.parametrize("kind", KINDS)
async def test_reference_only_does_not_become_current_message_attachment(kind):
    parsed = event(
        kind,
        message_type=103,
        message_scene={"source": "default", "ext": ["ref_msg_idx=synthetic-ref"]},
        msg_elements=[
            {
                "content": "quoted",
                "msg_idx": "synthetic-ref",
                "attachments": [{"content_type": "image/png", "url": REFERENCE_URL}],
            }
        ],
    )
    await _check_reply(SimpleNamespace(), parsed)
    assert parsed.get_reply_message()["image"][0].data["url"] == REFERENCE_URL
    assert not parsed.get_message()["image"]
    assert QQPlatform().image_attachments(parsed) == ()


@pytest.mark.parametrize("kind", KINDS)
def test_platform_omitted_reference_attachment_is_not_recoverable(kind):
    parsed = event(kind, message_type=103, msg_elements=[{"content": "quoted", "msg_idx": "ref"}])
    assert parsed.msg_elements[0].attachments is None
    assert not parsed.get_message()["image"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("missing", [{}, {"url": None}, {"url": ""}])
def test_missing_image_url_is_skipped_without_losing_valid_attachment(kind, missing, monkeypatch):
    from framework.integration.platforms import qq

    records = []
    monkeypatch.setattr(qq.logger, "info", lambda template, *args: records.append(template.format(*args)))
    parsed = event(
        kind,
        attachments=[{"content_type": "image/png", **missing}, {"content_type": "image/png", "url": URL}],
    )
    assert [s.data["url"] for s in parsed.get_message()["image"]] == [URL]
    assert QQPlatform().image_attachments(parsed) == (URL,)
    assert records[0] == "QQ upload attachment: index=1 result=missing_url"
    assert records[-1] == "QQ upload attachments: total=2 accepted=1"
    assert "synthetic-image" not in "\n".join(records)
