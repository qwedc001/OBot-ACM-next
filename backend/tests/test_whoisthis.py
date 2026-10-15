import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("separator", [".", ":"])
def test_whoisthis_native_mentions_authorization_and_aliases(separator):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.whoisthis_probe", separator],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        encoding="utf-8",
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "ok": True,
        "native_mentions": True,
        "no_privilege_change": True,
    }


def test_guild_mentions_use_guild_identity_namespace_and_ignore_bots():
    from types import SimpleNamespace

    from nonebot.adapters.qq.event import AtMessageCreateEvent

    from framework.contracts import ExternalIdentity
    from framework.integration.platforms.qq import QQPlatform

    bot = SimpleNamespace(self_id="app", self_info=SimpleNamespace(id="robot"))
    event = AtMessageCreateEvent.model_validate(
        {
            "id": "guild-event",
            "guild_id": "guild",
            "channel_id": "channel",
            "content": "/whoisthis <@person>",
            "author": {"id": "operator"},
            "mentions": [{"id": "person"}, {"id": "robot", "bot": True}, {"id": "other_bot", "bot": True}],
        }
    )
    targets = QQPlatform().mentioned_users(bot, event)
    assert len(targets) == 1 and targets[0].authoritative
    assert targets[0].identity == ExternalIdentity("qq", "app", "guild_user", "person")
