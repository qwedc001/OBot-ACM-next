import asyncio
import hashlib
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, update

from framework.catalog import CommandCatalog, CommandSpec
from framework.config import Settings
from framework.contracts import Role
from framework.dashboard import DashboardError
from framework.models import User, WebLoginToken, WebSession
from framework.runtime import Services
from framework.services.dashboard import csrf_for
from framework.web_url import public_origin
from web.app import install_web
from web.context import COOKIE
from web.plugin import discover_plugins

from .conftest import ROOT, USER
from .test_boards import bind, context


@pytest_asyncio.fixture
async def web(database, tmp_path):
    dist = tmp_path / "frontend"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=app>EP</div>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets/app.js").write_text("/* compiled frontend */", encoding="utf-8")
    catalog = CommandCatalog()
    catalog.register(CommandSpec("help", "help", "help", "帮助", directory=()))
    catalog.register(CommandSpec("status", "maintenance", "status", "状态", permission=Role.SUPERUSER))
    services = Services(
        Settings(
            obot_data_dir=tmp_path, obot_public_base_url="https://bot.example.com", obot_frontend_dist=dist
        ),
        catalog,
        session_factory=database,
        data_dir=tmp_path,
    )
    app = FastAPI()
    install_web(app, services)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://bot.example.com"
    ) as client:
        try:
            yield services, client
        finally:
            await services.close()


async def login(services, client, actor=USER):
    raw = (await services.dashboard.issue(context(actor))).split("#")[1]
    response = await client.post(
        "/api/auth/exchange", json={"token": raw}, headers={"Origin": "https://bot.example.com"}
    )
    assert response.status_code == 200
    return response, {"Origin": "https://bot.example.com", "X-CSRF-Token": response.json()["csrf_token"]}


async def test_login_is_private_revokes_old_link_and_saves_only_digests(web, database):
    services, _ = web
    with pytest.raises(DashboardError, match="私聊"):
        await services.dashboard.issue(context(group="room"))
    old = (await services.dashboard.issue(context())).split("#")[1]
    raw = (await services.dashboard.issue(context())).split("#")[1]
    with pytest.raises(DashboardError):
        await services.dashboard.exchange(old)
    async with database() as session:
        rows = (await session.scalars(select(WebLoginToken))).all()
        assert len(rows) == 1 and rows[0].digest == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in repr(rows[0].__dict__)
    session_raw, account = await services.dashboard.exchange(raw)
    assert account.actor.id == USER.id and not account.superuser
    assert account.csrf == csrf_for(session_raw) and account.csrf != session_raw
    async with database() as session:
        row = await session.get(WebSession, hashlib.sha256(session_raw.encode()).hexdigest())
        assert row and session_raw not in repr(row.__dict__)
    with pytest.raises(DashboardError):
        await services.dashboard.exchange(raw)


async def test_login_atomic_exchange_and_expiry(web, database):
    services, _ = web
    raw = (await services.dashboard.issue(context())).split("#")[1]
    results = await asyncio.gather(
        services.dashboard.exchange(raw), services.dashboard.exchange(raw), return_exceptions=True
    )
    assert sum(isinstance(value, tuple) for value in results) == 1
    assert sum(isinstance(value, DashboardError) for value in results) == 1
    raw = (await services.dashboard.issue(context())).split("#")[1]
    async with database() as session, session.begin():
        await session.execute(update(WebLoginToken).values(expires_at=time.time() - 1))
    with pytest.raises(DashboardError):
        await services.dashboard.exchange(raw)
    assert await services.dashboard.authenticate("invalid") is None


async def test_session_cookie_csrf_logout_and_no_role_injection(web):
    services, client = web
    response, headers = await login(services, client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert response.json()["user"]["role"] == "everyone" and client.cookies.get(COOKIE) not in response.text
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["csrf_token"] == response.json()["csrf_token"]
    assert (
        await client.post("/api/auth/logout", json={}, headers={"Origin": headers["Origin"]})
    ).status_code == 403
    assert (
        await client.post(
            "/api/auth/logout", json={}, headers={**headers, "Origin": "https://evil.example.com"}
        )
    ).status_code == 403
    assert (await client.post("/api/auth/logout", json={}, headers=headers)).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401
    raw = (await services.dashboard.issue(context())).split("#")[1]
    result = await client.post(
        "/api/auth/exchange",
        json={"token": raw, "role": "superuser", "user_id": ROOT.id},
        headers={"Origin": headers["Origin"]},
    )
    assert result.json()["user"]["role"] == "everyone"


async def test_session_expiry_and_bounded_live_sessions(web, database):
    services, client = web
    tokens = []
    for _ in range(10):
        raw = (await services.dashboard.issue(context())).split("#")[1]
        tokens.append((await services.dashboard.exchange(raw))[0])
    assert await services.dashboard.authenticate(tokens[0]) is None
    assert await services.dashboard.authenticate(tokens[-1]) is not None
    async with database() as session, session.begin():
        assert len((await session.scalars(select(WebSession))).all()) == 8
        await session.execute(update(WebSession).values(expires_at=time.time() - 1))
    assert await services.dashboard.authenticate(tokens[-1]) is None


async def test_admin_data_catalogue_registry_and_live_revocation(web, database):
    services, client = web
    assert (await client.get("/api/plugins/runtime")).status_code == 401
    await login(services, client)
    assert (await client.get("/api/plugins/runtime")).status_code == 403
    assert {p["slug"] for p in (await client.get("/api/plugins")).json()["plugins"]} == {"board", "commands"}
    assert [c["id"] for c in (await client.get("/api/plugins/commands")).json()["commands"]] == ["help"]
    await login(services, client, ROOT)
    result = await client.get("/api/plugins/runtime")
    assert result.status_code == 200 and result.json()["telemetry"]["connected_bot_count"] == 0
    assert "correlation_id" not in result.text and "identity_key" not in result.text
    assert {c["id"] for c in (await client.get("/api/plugins/commands")).json()["commands"]} == {
        "help",
        "status",
    }
    async with database() as session, session.begin():
        await session.execute(update(User).where(User.id == ROOT.id).values(superuser=False))
    assert (await client.get("/api/plugins/runtime")).status_code == 403
    assert (await client.get("/api/auth/me")).json()["user"]["role"] == "everyone"


async def test_board_api_owns_personal_scope_csrf_and_reuses_service(web):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    await bind(services.boards, name="personal")
    await bind(services.boards, context(ROOT), name="other")
    group = context(role=Role.GROUP_ADMIN, group="room")
    await bind(services.boards, group, "group")
    await services.boards.confirm(group, "group")
    _, headers = await login(services, client)
    result = await client.get("/api/plugins/board/configs")
    assert [item["name"] for item in result.json()["configs"]] == ["personal"]
    assert all(
        word not in result.text
        for word in ["credentials", "owner_id", "test-secret", "test-admin", "push_target"]
    )
    payload = {"name": "personal", "enabled": False}
    assert (await client.post("/api/plugins/board/starred", json=payload)).status_code == 403
    assert (
        await client.post("/api/plugins/board/starred", json=payload, headers={"Origin": headers["Origin"]})
    ).status_code == 403
    assert (await client.post("/api/plugins/board/starred", json=payload, headers=headers)).status_code == 200
    assert not (await services.boards.choose(context(), "personal")).options["show_unrated"]
    for name in ["other", "group"]:
        assert (
            await client.post(
                "/api/plugins/board/starred", json={"name": name, "enabled": False}, headers=headers
            )
        ).status_code == 400
    assert (
        await client.post(
            "/api/plugins/board/starred", json={"name": "personal", "enabled": "off"}, headers=headers
        )
    ).status_code == 400
    artifact = await services.clients.artifacts.create(USER, b"PNG", "image/png")
    services.boards.image = AsyncMock(return_value=artifact)
    rendered = await client.post(
        "/api/plugins/board/render", json={"name": "personal", "mode": "yesterday"}, headers=headers
    )
    assert (
        rendered.status_code == 200
        and rendered.content == b"PNG"
        and rendered.headers["content-type"] == "image/png"
    )
    assert services.boards.image.await_args.args[1:] == ("full", "personal")


async def test_static_shell_safe_paths_missing_build_and_private_headers(web):
    services, client = web
    for path in ["/", "/login", "/board/bind", "/commands"]:
        response = await client.get(path)
        assert response.status_code == 200 and "id=app" in response.text
        assert (
            response.headers["cache-control"] == "no-store"
            and response.headers["referrer-policy"] == "no-referrer"
        )
        assert "unsafe-eval" not in response.headers["content-security-policy"]
    assert (await client.get("/assets/app.js")).status_code == 200
    for path in [
        "/.env.dev",
        "/assets/%2e%2e/%2e%2e/.env.dev",
        "/api/missing",
        "/internal/missing",
        "/qq/missing",
        "/assets/no.js",
    ]:
        assert (await client.get(path)).status_code == 404
    denied = await client.get("/api/plugins/board/configs")
    assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
    services.settings.obot_frontend_dist = services.settings.obot_data_dir / "missing"
    assert (await client.get("/login")).status_code == 503


async def test_login_json_validation_is_bounded_and_never_echoes_inputs(web):
    _, client = web
    invalid = "not-echoed" * 300
    response = await client.post(
        "/api/auth/exchange", json={"token": invalid}, headers={"Origin": "https://bot.example.com"}
    )
    assert response.status_code == 413 and invalid not in response.text
    assert (await client.post("/api/auth/exchange", json={"token": None})).status_code == 403
    bad = await client.post(
        "/api/auth/exchange",
        content=b"{bad-json",
        headers={"Origin": "https://bot.example.com", "Content-Type": "application/json"},
    )
    assert bad.status_code == 400 and "bad-json" not in bad.text
    assert (
        await client.post(
            "/api/auth/exchange",
            json={},
            headers={"Origin": "https://bot.example.com", "Sec-Fetch-Site": "cross-site"},
        )
    ).status_code == 403


@pytest.mark.parametrize(
    "value,allowed",
    [
        ("https://bot.example.com", True),
        ("http://127.0.0.1:5173", True),
        ("http://localhost:5173", True),
        ("http://[::1]:5173", True),
        ("http://example.com", False),
        ("https://user:password@example.com", False),
        ("https://example.com/path", False),
        ("https://example.com?x=1", False),
        (None, False),
    ],
)
def test_public_origin_only_https_or_local_development(value, allowed):
    if allowed:
        assert public_origin(value) == value
    else:
        with pytest.raises(ValueError):
            public_origin(value)


def test_web_plugins_are_discovered_without_manual_registration():
    assert {plugin.slug for plugin in discover_plugins()} == {"board", "commands", "runtime"}
