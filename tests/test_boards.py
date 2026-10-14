import asyncio
import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from framework.board import BoardError, hydro_host
from framework.clients.credentials import CredentialsClient
from framework.config import Settings
from framework.contracts import AccessDenied, RequestContext, Role, SceneKind, SceneRef
from framework.integration.board_web import build_board_router
from framework.models import BoardBindToken
from framework.services.boards import BoardService

from .conftest import ROOT, USER, access


def context(actor=USER, role=Role.EVERYONE, group=None):
    kind = SceneKind.GROUP if group else SceneKind.C2C
    return RequestContext(
        actor, SceneRef("qq", "bot", kind, group or actor.id), "event", command_allowed=access(role, kind)
    )


def service(database, tmp_path):
    settings = Settings(obot_public_base_url="https://bot.example.com", obot_data_dir=tmp_path)
    hydro = SimpleNamespace(
        validate=AsyncMock(), daily=AsyncMock(return_value={"submissions": [], "rankings": []})
    )
    return BoardService(
        database, settings, CredentialsClient(tmp_path / "key"), hydro, SimpleNamespace(render=AsyncMock())
    )


async def token(service, ctx=None, name="xxxOJ"):
    return (await service.issue(ctx or context(), name)).split("#")[1]


async def bind(service, ctx=None, name="xxxOJ"):
    value = await token(service, ctx, name)
    return await service.submit(value, "https://oj.example.com/d/team", "test-admin", "test-secret")


async def test_private_binding_one_use_encrypted_and_scoped(database, tmp_path):
    boards = service(database, tmp_path)
    value = await token(boards)
    assert await boards.inspect(value) == await boards.inspect(value)  # previews do not consume the link
    result = await boards.submit(value, "https://oj.example.com", "test-admin", "test-secret")
    assert not result["group"] and "test-secret" not in str(result)
    with pytest.raises(BoardError):
        await boards.inspect(value)
    with pytest.raises(BoardError):
        await boards.submit(value, "https://other.example.com", "another", "another")
    config = await boards.choose(context())
    assert config.name == "xxxoj" and config.host == "https://oj.example.com/"
    assert "test-secret" not in config.credentials and "test-admin" not in config.credentials
    values = await CredentialsClient(tmp_path / "key").decrypt(config.credentials)
    assert values == {"uname": "test-admin", "password": "test-secret"}
    assert not await boards.configs(context(ROOT))
    assert not await boards.configs(context(group="room"))
    async with database() as session:
        row = await session.get(BoardBindToken, hashlib.sha256(value.encode()).hexdigest())
        assert value not in row.digest and row.used


async def test_group_binding_requires_current_admin_and_original_actor_confirmation(database, tmp_path):
    boards = service(database, tmp_path)
    member, admin = context(group="room"), context(role=Role.GROUP_ADMIN, group="room")
    with pytest.raises(AccessDenied):
        await boards.issue(member, "oj")
    result = await bind(boards, admin, "oj")
    assert result["group"] and "confirm" in result["message"]
    assert not await boards.configs(member)
    with pytest.raises(AccessDenied):
        await boards.confirm(member, "oj")
    with pytest.raises(BoardError):
        await boards.confirm(context(ROOT, Role.SUPERUSER, "room"), "oj")
    await boards.confirm(admin, "oj")
    assert (await boards.choose(member)).name == "oj"
    assert not await boards.configs(context(group="other"))
    with pytest.raises(BoardError):
        await boards.confirm(admin, "oj")


async def test_failed_validation_keeps_link_retryable_and_never_stores_credentials(database, tmp_path):
    boards = service(database, tmp_path)
    value = await token(boards)
    boards.hydro.validate.side_effect = BoardError("验证失败")
    with pytest.raises(BoardError):
        await boards.submit(value, "https://oj.example.com", "admin", "password")
    assert (await boards.inspect(value))["name"] == "xxxoj"
    assert not await boards.configs(context())
    boards.hydro.validate.side_effect = None
    await boards.submit(value, "https://oj.example.com", "admin", "password")


async def test_concurrent_submit_claims_once_and_old_issued_link_is_revoked(database, tmp_path):
    boards = service(database, tmp_path)
    old = await token(boards)
    value = await token(boards)
    with pytest.raises(BoardError):
        await boards.inspect(old)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def validate(*_):
        entered.set()
        await resume.wait()

    boards.hydro.validate.side_effect = validate
    first = asyncio.create_task(boards.submit(value, "https://oj.example.com", "admin", "password"))
    await entered.wait()
    with pytest.raises(BoardError):
        await boards.submit(value, "https://oj.example.com", "admin", "password")
    resume.set()
    await first
    boards.hydro.validate.assert_awaited_once()


async def test_expired_or_revoked_inflight_link_cannot_commit(database, tmp_path):
    boards = service(database, tmp_path)
    value = await token(boards)

    async def issue_again(*_):
        await token(boards)

    boards.hydro.validate.side_effect = issue_again
    with pytest.raises(BoardError):
        await boards.submit(value, "https://oj.example.com", "admin", "password")
    assert not await boards.configs(context())
    fresh = await token(boards)
    async with database() as session, session.begin():
        row = await session.get(BoardBindToken, hashlib.sha256(fresh.encode()).hexdigest())
        row.expires_at = 1
    with pytest.raises(BoardError):
        await boards.inspect(fresh)


async def test_same_name_is_separate_for_person_and_group_and_unbind_revokes_links(database, tmp_path):
    boards = service(database, tmp_path)
    await bind(boards)
    admin = context(role=Role.GROUP_ADMIN, group="room")
    await bind(boards, admin)
    await boards.confirm(admin, "xxxOJ")
    assert (await boards.choose(context())).id != (await boards.choose(admin)).id
    value = await token(boards)
    await boards.unbind(context(), "xxxOJ")
    with pytest.raises(BoardError):
        await boards.inspect(value)
    assert (await boards.choose(admin)).name == "xxxoj"


async def test_binding_web_same_origin_no_url_tokens_no_password_echo(database, tmp_path):
    boards = service(database, tmp_path)
    app = FastAPI()
    app.include_router(build_board_router(SimpleNamespace(boards=boards, settings=boards.settings)))
    value = await token(boards)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://bot.example.com"
    ) as client:
        page = await client.get("/board/bind")
        assert page.status_code == 200 and page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["cache-control"] == "no-store" and value not in page.text
        data = {
            "token": value,
            "host": "https://oj.example.com",
            "username": "admin",
            "password": "not-echoed",
        }
        assert (await client.post("/board/api/bind/submit", json=data)).status_code == 403
        headers = {"Origin": "https://bot.example.com"}
        response = await client.post("/board/api/bind/submit", json=data, headers=headers)
        assert response.status_code == 200 and "not-echoed" not in response.text
        assert (
            await client.post("/board/api/bind/inspect", json={"token": value}, headers=headers)
        ).status_code == 400
        assert (
            await client.post(
                "/board/api/bind/submit",
                content="a" * 9000,
                headers={**headers, "Content-Type": "application/json"},
            )
        ).status_code == 413
        assert (await client.get("/board/assets/not-found.js")).status_code == 404


@pytest.mark.parametrize(
    "host",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com:8443",
        "https://example.com/login",
        "https://example.com/?secret=x",
    ],
)
def test_hydro_url_rejects_credential_and_path_injection(host):
    with pytest.raises(BoardError):
        hydro_host(host)


async def test_daily_push_is_opt_in_and_rechecks_target_before_delivery(database, tmp_path):
    boards = service(database, tmp_path)
    await bind(boards)
    boards.reply = SimpleNamespace(send_to=AsyncMock())
    boards.peeper.artifacts = SimpleNamespace(read=AsyncMock(return_value=b"PNG"))
    boards.peeper.render.return_value = SimpleNamespace(id="artifact")
    await boards.daily_push()
    boards.reply.send_to.assert_not_awaited()
    target = {"adapter": "QQ", "kind": "c2c", "id": "user", "bot_id": "bot"}
    await boards.push(context(), True, None, target)
    await boards.daily_push()
    boards.reply.send_to.assert_awaited_once()
    assert boards.reply.send_to.call_args.args[1].id == "user"

    async def disable(*args):
        await boards.push(context(), False, None, target)
        return SimpleNamespace(id="artifact")

    boards.peeper.render.side_effect = disable
    boards.reply.send_to.reset_mock()
    await boards.daily_push()
    boards.reply.send_to.assert_not_awaited()


async def test_binding_options_are_saved_and_historical_data_cannot_be_fabricated(database, tmp_path):
    boards = service(database, tmp_path)
    value = await token(boards)
    await boards.submit(
        value,
        "https://oj.example.com",
        "admin",
        "password",
        {"exclude_uid": [1], "exclude_before": "2026-01-01", "show_unrated": False},
    )
    config = await boards.choose(context())
    assert config.options["exclude_uid"] == [1] and config.options["show_unrated"] is False
    with pytest.raises(BoardError, match="历史"):
        await boards.snapshot(config, "2020-01-01", historical=True)
    boards.hydro.daily.assert_not_awaited()


async def test_daily_update_replaces_an_earlier_snapshot_instead_of_freezing_it(database, tmp_path):
    boards = service(database, tmp_path)
    await bind(boards)
    config = await boards.choose(context())
    day = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
    await boards.snapshot(config, day)
    await boards.snapshot(config, day)
    assert boards.hydro.daily.await_count == 1
    await boards.daily_update()
    assert boards.hydro.daily.await_count == 2
    assert boards.hydro.daily.await_args.kwargs["refresh"] is True


async def test_group_confirmation_can_only_commit_once_under_concurrency(database, tmp_path):
    boards = service(database, tmp_path)
    admin = context(role=Role.GROUP_ADMIN, group="room")
    await bind(boards, admin)
    results = await asyncio.gather(
        boards.confirm(admin, "xxxOJ"), boards.confirm(admin, "xxxOJ"), return_exceptions=True
    )
    assert sum(isinstance(value, str) for value in results) == 1
    assert sum(isinstance(value, BoardError) for value in results) == 1


async def test_starred_toggle_is_persistent_and_reuses_existing_snapshots(database, tmp_path):
    boards = service(database, tmp_path)
    value = await token(boards)
    await boards.submit(value, "https://oj.example.com", "admin", "password", {"exclude_uid": [2]})
    config = await boards.choose(context())
    day = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
    await boards.snapshot(config, day)
    assert "已关闭" in await boards.starred(context())
    updated = await service(database, tmp_path).choose(context())
    assert updated.options["show_unrated"] is False
    assert updated.options["exclude_uid"] == [2]
    assert updated.credentials == config.credentials and updated.revision == config.revision
    await boards.snapshot(updated, day)
    assert boards.hydro.daily.await_count == 1
    await boards.image(context(), "full")
    assert boards.peeper.render.await_args.args[1]["show_unrated"] is False
    assert "已开启" in await boards.starred(context(), True)
    assert (await boards.choose(context())).options["show_unrated"] is True


async def test_starred_changes_only_selected_scope_and_requires_group_management(database, tmp_path):
    boards = service(database, tmp_path)
    await bind(boards)
    await bind(boards, name="anotherOJ")
    member, admin = context(group="room"), context(role=Role.GROUP_ADMIN, group="room")
    await bind(boards, admin)
    await boards.confirm(admin, "xxxoj")
    with pytest.raises(BoardError, match="多个"):
        await boards.starred(context())
    await boards.starred(context(), False, "xxxoj")
    assert (await boards.choose(context(), "anotheroj")).options["show_unrated"] is True
    assert (await boards.choose(member)).options["show_unrated"] is True
    with pytest.raises(AccessDenied):
        await boards.starred(member, False)
    await boards.starred(admin, False)
    assert (await boards.choose(member)).options["show_unrated"] is False
    await boards.starred(context(ROOT, Role.SUPERUSER, "room"), True)
    assert (await boards.choose(member)).options["show_unrated"] is True
    with pytest.raises(BoardError):
        await boards.starred(context(group="other", role=Role.GROUP_ADMIN), False)
    assert (await boards.choose(context(), "xxxoj")).options["show_unrated"] is False


async def test_starred_changed_during_render_cannot_deliver_old_setting(database, tmp_path):
    boards = service(database, tmp_path)
    await bind(boards)

    async def change_setting(*_):
        await boards.starred(context(), False)
        return SimpleNamespace(id="artifact")

    boards.peeper.render.side_effect = change_setting
    with pytest.raises(BoardError, match="配置已改变"):
        await boards.image(context(), "full")
    boards.reply = SimpleNamespace(send_to=AsyncMock())
    boards.peeper.artifacts = SimpleNamespace(read=AsyncMock(return_value=b"PNG"))
    await boards.starred(context(), True)
    await boards.push(context(), True, None, {"adapter": "QQ", "kind": "c2c", "id": "user", "bot_id": "bot"})
    await boards.daily_push()
    boards.reply.send_to.assert_not_awaited()
