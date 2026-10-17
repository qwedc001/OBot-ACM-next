"""Opt-in, short-lived evidence capture around the native QQ adapter.

Targets (nonebot-adapter-qq 1.7.3):
* Adapter.payload_to_event: record Dispatch.data BEFORE Pydantic validates nested
  models (top-level Event extras survive, nested model extras may be discarded).
* Bot.handle_event: record message/original_message/reply AFTER the adapter's
  _check_reply and _check_at_me processing and normal NoneBot dispatch.

This is diagnostic instrumentation, not a parser fix. It does not add identities,
change permissions or guess missing users. No credential configuration is read.
Enable with a private data/qq-diagnostics/request.json; see README.md here.
"""

import asyncio
import copy
import json
import os
import re
import time
from functools import wraps
from pathlib import Path
from uuid import uuid4


def redact(value):
    """Preserve evidence fields, while removing credentials and signed URLs."""
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if re.search(r"token|secret|password|authorization|cookie|file_info", key, re.I)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if re.match(r"[^=]*(token|secret|password)[^=]*=", value, re.I):
            return value.partition("=")[0] + "=[redacted]"
        return re.sub(r"https?://[^\s\"<>]+", "[url redacted]", value)
    return value


class Recorder:
    def __init__(self, directory, request):
        self.directory, self.request = directory, request
        self.tasks = set()

    def matches(self, data):
        return (
            time.time() < self.request["expires_at"]
            and data.get("group_openid") == self.request["group_openid"]
            and any(command in data.get("content", "") for command in self.request["commands"])
        )

    def submit(self, phase, value):
        # Serialize a private snapshot before any later handler can mutate it.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        snapshot = {"phase": phase, "captured_at": time.time(), **redact(copy.deepcopy(value))}
        task = loop.create_task(asyncio.to_thread(self.write, phase, snapshot))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def write(self, phase, value):
        try:
            encoded = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode()
            if len(encoded) > 262144:
                return
            path = self.directory / f"{time.time_ns()}-{phase}-{uuid4().hex[:8]}.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
            for previous in sorted(self.directory.glob("*-*.json"))[:-40]:
                previous.unlink(missing_ok=True)
        except OSError:
            # Evidence capture must never interrupt message delivery.
            return


def load_recorder():
    from nonebot import get_driver

    directory = Path(getattr(get_driver().config, "obot_data_dir", "data")) / "qq-diagnostics"
    path = directory / "request.json"
    try:
        stat = path.stat()
        if os.name == "posix" and (stat.st_uid != os.geteuid() or stat.st_mode & 0o077):
            return None
        request = json.loads(path.read_text(encoding="utf-8"))
        if not time.time() < request["expires_at"] <= time.time() + 3600:
            return None
        if not isinstance(request["group_openid"], str) or not request["group_openid"]:
            return None
        commands = request.get("commands", ["/whoisthis"])
        if not commands or any(not isinstance(cmd, str) or not cmd.startswith("/") for cmd in commands):
            return None
        request["commands"] = commands
        os.chmod(directory, 0o700)
        return Recorder(directory, request)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def install_trace():
    recorder = load_recorder()
    if recorder is None:
        return
    from nonebot.adapters.qq import Adapter, Bot

    if getattr(Adapter.payload_to_event, "__obot_trace__", False):
        return
    original_parse = Adapter.payload_to_event
    original_handle = Bot.handle_event

    @wraps(original_parse)
    def parse(payload):
        if isinstance(payload.data, dict) and recorder.matches(payload.data):
            recorder.submit("received", {"event_type": str(payload.type), "data": payload.data})
        return original_parse(payload)

    @wraps(original_handle)
    async def handle(bot, event):
        matched = recorder.matches(event.model_dump())
        try:
            return await original_handle(bot, event)
        finally:
            if matched:
                recorder.submit(
                    "processed",
                    {
                        "event_type": event.get_event_name(),
                        "data": event.model_dump(),
                        "message": [
                            segment.model_dump()
                            if hasattr(segment, "model_dump")
                            else {"type": segment.type, "data": segment.data}
                            for segment in event.get_message()
                        ],
                        "original_message": str(getattr(event, "original_message", "")),
                    },
                )

    parse.__obot_trace__ = True
    Adapter.payload_to_event = staticmethod(parse)
    Bot.handle_event = handle
