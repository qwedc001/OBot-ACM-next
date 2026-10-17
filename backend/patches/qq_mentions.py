"""Normalize documented QQ group User mentions before adapter validation.

Target: nonebot.adapters.qq.Adapter.payload_to_event, adapter 1.7.3 only.
QQ's official group event schema describes mentions[] as User records, whereas
the adapter's GroupMentionUser also requires scope/is_you. A documented User
record with member_openid but without these adapter-specific fields otherwise
rejects the ENTIRE event during Pydantic validation. Reproduced in unit tests.

For an actual provided member_openid, fill adapter-only defaults on a COPY.
Never recover a missing mentions list, infer IDs from nicknames/QQ numbers,
decode auth_token, search a message cache or modify author/group/permissions.
Existing explicit scope/is_you values remain upstream-owned. is_you defaults
False: missing self metadata must not turn a third-party bot into this bot.
This compatibility path is not a fix for the real platform payloads that omit
all target information; see README.md for the phone/transport evidence.
Remove when upstream accepts the documented User representation itself.
"""

from functools import wraps
from importlib.metadata import version

from nonebot.adapters.qq import Adapter


def normalize_mentions(payload):
    if payload.type not in {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
        return payload
    data = payload.data
    if not isinstance(data, dict) or not isinstance(data.get("mentions"), list):
        return payload
    normalized = []
    for mention in data["mentions"]:
        if (
            isinstance(mention, dict)
            and mention.get("scope") != "all"
            and isinstance(mention.get("member_openid"), str)
            and mention["member_openid"].strip()
        ):
            mention = dict(mention)
            for key, value in (
                ("scope", "single"),
                ("is_you", False),
                ("id", mention["member_openid"]),
                ("bot", False),
                ("username", ""),
            ):
                mention.setdefault(key, value)
        normalized.append(mention)
    if normalized == data["mentions"]:
        return payload
    return payload.model_copy(update={"data": {**data, "mentions": normalized}})


def install_mentions():
    if version("nonebot-adapter-qq") != "1.7.3":
        from nonebot.log import logger

        logger.warning("OBot QQ mention compatibility patch skipped: recheck adapter version")
        return
    if getattr(Adapter.payload_to_event, "__obot_mentions__", False):
        return
    original = Adapter.payload_to_event

    @wraps(original)
    def parse(payload):
        return original(normalize_mentions(payload))

    parse.__obot_mentions__ = True
    Adapter.payload_to_event = staticmethod(parse)
