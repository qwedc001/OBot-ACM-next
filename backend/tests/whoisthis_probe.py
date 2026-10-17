"""Native QQ mentions, Alconna aliases and authorization over isolated accounts."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.models import Dispatch, User


async def main():
    separator = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="obot-whoisthis-") as directory:
        root = Path(directory)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            superusers={"operator"},
            command_sep={separator},
            log_level="ERROR",
        )
        if "--patches" in sys.argv:
            from patches import install

            install()
        from framework.catalog import catalog
        from framework.config import Settings
        from framework.contracts import ExternalIdentity
        from framework.integration.lifecycle import install_lifecycle
        from framework.integration.platforms.qq_transport import QQAdapter
        from framework.migrate import upgrade
        from framework.runtime import configure_services

        catalog.configure_separator(separator)
        services = configure_services(Settings(obot_data_dir=root / "data"))
        install_lifecycle(services)
        assert nonebot.load_plugin("plugins.maintenance")
        assert nonebot.load_plugin("plugins.help")
        await upgrade()
        driver = nonebot.get_driver()
        await driver._lifespan.startup()
        bot = Bot(nonebot.get_adapter("QQ"), "app", BotInfo(id="app", secret="synthetic"))
        bot._self_info = User(id="app", username="test-bot", bot=True)
        sent, counter = [], 0

        def mention(id="display_id", member="member_openid", *, bot=False, is_you=False):
            return {
                "scope": "single",
                "bot": bot,
                "is_you": is_you,
                "id": id,
                "member_openid": member,
                "username": "test-person",
            }

        async def request(
            content, mentions=None, *, user="operator", private=False, role="member", extra=None, full=False
        ):
            nonlocal counter
            counter += 1
            payload = {
                "id": f"event-{counter}",
                "content": content,
                "to_me": True,
                "timestamp": "2026-10-16T00:00:00+08:00",
            }
            if private:
                payload["author"] = {"id": user, "user_openid": user}
            else:
                payload.update(
                    author={"id": user, "member_openid": user, "member_role": role, "bot": False},
                    group_id="room",
                    group_openid="room",
                    mentions=mentions,
                )
            if extra:
                payload.update(extra)
            if payload.get("mentions") is None:
                payload.pop("mentions", None)
            if full:
                # Full-mode to_me must come from native mention processing,
                # rather than a pre-filled synthetic event flag.
                payload.pop("to_me", None)
            event = QQAdapter.payload_to_event(
                Dispatch(
                    type="C2C_MESSAGE_CREATE"
                    if private
                    else "GROUP_MESSAGE_CREATE"
                    if full
                    else "GROUP_AT_MESSAGE_CREATE",
                    data=payload,
                    id=f"dispatch-{counter}",
                )
            )
            assert event is not None
            before = len(sent)
            await bot.handle_event(event)
            assert len(sent) == before + 1
            return sent[-1]["content"]

        try:
            with (
                patch.object(
                    Bot,
                    "post_group_messages",
                    AsyncMock(side_effect=lambda **kw: sent.append(kw) or {"id": "reply"}),
                ),
                patch.object(
                    Bot,
                    "post_c2c_messages",
                    AsyncMock(side_effect=lambda **kw: sent.append(kw) or {"id": "reply"}),
                ),
            ):
                person = ExternalIdentity("qq", "app", "group_member", "member_openid")
                denied = await request("/whoisthis <@display_id>", [mention()], user="member")
                assert "权限不足" in denied and await services.identities.lookup(person) is None
                denied = await request(
                    "/whoisthis <@display_id>", [mention()], user="group_admin", role="admin"
                )
                assert "权限不足" in denied and await services.identities.lookup(person) is None
                output = await request("/whoisthis <@display_id>", [mention()])
                actor = await services.identities.lookup(person)
                assert actor and actor.id in output and person.key in output and "未登记" in output
                # Upstream #195/#214 demonstrate real full-mode mentions. Once
                # QQ grants that delivery mode, no alternate command handler or
                # nickname/member-list matching should be needed in OBot.
                full_mentions = [mention("bot_openid", "bot_openid", bot=True, is_you=True), mention()]
                assert actor.id in await request("/whoisthis", full_mentions, full=True)
                assert "权限不足" in await request("/whoisthis", full_mentions, user="member", full=True)
                assert "平台未提供" in await request("/whoisthis", full=True)
                assert not await services.identities.is_superuser(actor)
                assert (
                    await services.identities.lookup(
                        ExternalIdentity("qq", "app", "group_member", "display_id")
                    )
                    is None
                )
                # Metadata-only mentions and legacy segments both resolve to the same observed account.
                assert actor.id in await request(f"/maintenance{separator}whoisthis", [mention()])
                if "--patches" in sys.argv:
                    documented_user = {
                        key: value for key, value in mention().items() if key not in {"scope", "is_you"}
                    }
                    assert actor.id in await request("/whoisthis", [documented_user])
                assert actor.id in await request('/whoisthis <qqbot-at-user id="member_openid" />')
                assert actor.id in await request(
                    "/whoisthis <@display_id> <@display_id>", [mention(), mention()]
                )
                assert actor.id in await request(
                    "/whoisthis <@app> <@display_id>",
                    [mention("app", "bot", bot=True, is_you=True), mention()],
                )
                assert "平台未提供" in await request("/whoisthis")
                assert "平台未提供" in await request("/whoisthis", [mention(bot=True)])
                assert "平台未提供" in await request(
                    "/whoisthis", [{"scope": "all", "is_you": True, "username": "all"}]
                )
                assert "仅@一位" in await request(
                    "/whoisthis <@display_id> <@other>", [mention(), mention("other", "another_openid")]
                )
                assert (
                    await services.identities.lookup(
                        ExternalIdentity("qq", "app", "group_member", "another_openid")
                    )
                    is None
                )
                missing = ExternalIdentity("qq", "app", "group_member", "legacy_unseen")
                assert "尚未找到" in await request("/whoisthis <@legacy_unseen>")
                assert await services.identities.lookup(missing) is None
                assert "群聊或频道" in await request("/whoisthis", private=True)
                assert "whoisthis" not in await request("/help text", user="member")
                assert "whoisthis" in await request("/help text")
                # Match the observed production payload: at text and mentions are
                # absent before native SDK processing, not stripped by Alconna.
                assert "平台未提供" in await request(
                    "/whoisthis  ",
                    extra={
                        "message_type": 0,
                        "message_scene": {
                            "source": "default",
                            "ext": ["msg_idx=current", "auth_token=synthetic"],
                        },
                    },
                )
                reference = {
                    "message_type": 103,
                    "message_scene": {"source": "default", "ext": ["ref_msg_idx=target", "msg_idx=current"]},
                    "msg_elements": [
                        {
                            "content": "not the target",
                            "msg_idx": "other",
                            "author": {"id": "decoy", "member_openid": "decoy"},
                        },
                        {
                            "content": "hello",
                            "msg_idx": "target",
                            "author": {"id": "display_id", "member_openid": "member_openid"},
                        },
                    ],
                }
                assert actor.id in await request("/whoisthis", extra=reference)
                assert (
                    await services.identities.lookup(ExternalIdentity("qq", "app", "group_member", "decoy"))
                    is None
                )
                for author in (None, {"id": "display_only"}, {"member_openid": "robot", "bot": True}):
                    missing_author = {
                        **reference,
                        "msg_elements": [{"content": "hello", "msg_idx": "target", "author": author}],
                    }
                    assert "平台未提供" in await request("/whoisthis", extra=missing_author)
                mismatched = {
                    **reference,
                    "message_scene": {"source": "default", "ext": ["ref_msg_idx=missing"]},
                }
                assert "平台未提供" in await request("/whoisthis", extra=mismatched)
                assert "权限不足" in await request("/whoisthis", user="member", extra=reference)
                assert "权限不足" in await request(
                    "/whoisthis", user="group_admin", role="admin", extra=reference
                )
                # A later actual message uses the account created from authoritative member_openid.
                assert (await services.identities.resolve(person)).id == actor.id
                print(json.dumps({"ok": True, "native_mentions": True, "no_privilege_change": True}))
        finally:
            await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
