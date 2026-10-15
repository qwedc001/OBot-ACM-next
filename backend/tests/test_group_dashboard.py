import asyncio
import hashlib
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from framework.contracts import AccessDenied, ExternalIdentity, Role
from framework.dashboard import DashboardError
from framework.models import BoardSnapshot, Identity, SceneAuthority, WebGroupCheck, WebSceneGrant
from framework.services.scene_access import scene_scope, scope_id

from . import test_dashboard
from .conftest import ROOT, USER
from .test_boards import bind, context
from .test_dashboard import login

web = test_dashboard.web


async def group(services, database, *, actor=USER, name="shared", room="room", admin=True):
    async with database() as session, session.begin():
        if await session.get(Identity, actor.identity_key) is None:
            session.add(Identity(key=actor.identity_key, user_id=actor.id))
    ctx = context(actor, Role.GROUP_ADMIN if admin else Role.EVERYONE, group=room)
    await services.scene_access.observe(actor, ctx.scene, admin)
    if admin:
        await bind(services.boards, ctx, name)
        await services.boards.confirm(ctx, name)
    return ctx


async def test_group_scope_defaults_to_verified_admin_and_never_exposes_credentials(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    await bind(services.boards, name="shared")
    own = await group(services, database)
    other = await group(services, database, actor=ROOT, room="other")
    await login(services, client)
    scopes = (await client.get("/api/plugins/board/scopes")).json()["scopes"]
    selector = scope_id(scene_scope(own.scene))
    assert [item["id"] for item in scopes] == ["personal", selector]
    response = await client.get("/api/plugins/board/configs", params={"scope": selector})
    assert response.status_code == 200
    assert response.json()["configs"][0]["name"] == "shared"
    assert not any(
        value in response.text
        for value in (
            "credentials",
            "password",
            "test-secret",
            "test-admin",
            "owner_id",
            "push_target",
            "identity_key",
        )
    )
    assert "qq:bot:group" not in str(scopes)
    assert (
        await client.get("/api/plugins/board/configs", params={"scope": scope_id(scene_scope(other.scene))})
    ).status_code == 403
    assert (
        await client.get("/api/plugins/board/configs", params={"scope": "scene:qq:bot:group:room"})
    ).status_code == 403
    await login(services, client, ROOT)
    assert len((await client.get("/api/plugins/board/scopes")).json()["scopes"]) == 3


async def test_settings_preserve_credentials_and_history_and_reclassify_cached_rankings(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    config = await services.boards.choose(ctx)
    async with database() as session, session.begin():
        session.add(
            BoardSnapshot(
                id="history",
                config_id=config.id,
                day="2020-01-01",
                revision=config.revision,
                captured_at=1,
                payload={
                    "submissions": [],
                    "rankings": [{"user": {"uid": "7", "register_at": 0}, "unrated": False}],
                },
            )
        )
    _, headers = await login(services, client)
    selector = scope_id(scene_scope(ctx.scene))
    payload = {
        "scope": selector,
        "name": "shared",
        "options": {"exclude_uid": [7], "exclude_before": "1970-01-01", "show_unrated": False},
    }
    assert (await client.post("/api/plugins/board/settings", json=payload)).status_code == 403
    assert (
        await client.post("/api/plugins/board/settings", json=payload, headers=headers)
    ).status_code == 200
    changed = await services.boards.choose(ctx)
    assert (
        changed.credentials == config.credentials
        and changed.host == config.host
        and changed.revision == config.revision
    )
    snapshot = await services.boards.snapshot(changed, "2020-01-01", historical=True)
    assert snapshot["payload"]["rankings"][0]["unrated"]
    async with database() as session:
        assert await session.get(BoardSnapshot, "history") is not None
    payload["options"]["credentials"] = "ignored"
    assert (
        await client.post("/api/plugins/board/settings", json=payload, headers=headers)
    ).status_code == 400
    payload["options"].pop("credentials")
    payload["host"] = "https://another.example.com"
    assert (
        await client.post("/api/plugins/board/settings", json=payload, headers=headers)
    ).status_code == 400


async def test_group_role_demotion_and_expiry_revoke_existing_web_session(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    _, headers = await login(services, client)
    selector = scope_id(scene_scope(ctx.scene))
    await services.scene_access.observe(USER, ctx.scene, False)
    assert (await client.get("/api/plugins/board/configs", params={"scope": selector})).status_code == 403
    assert (
        await client.post(
            "/api/plugins/board/starred",
            json={"scope": selector, "name": "shared", "enabled": False},
            headers=headers,
        )
    ).status_code == 403
    await services.scene_access.observe(USER, ctx.scene, True)
    async with database() as session, session.begin():
        await session.execute(update(SceneAuthority).values(verified_at=time.time() - 901))
    assert (await client.get("/api/plugins/board/configs", params={"scope": selector})).status_code == 403
    assert (await client.get("/api/plugins/board/scopes")).json()["scopes"] == [
        {"id": "personal", "label": "个人榜单"}
    ]


async def test_cross_scene_identity_check_needs_real_group_admin_is_one_use_and_revocable(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    verifier = await services.identities.resolve(ExternalIdentity("qq", "bot", "group_member", "group-admin"))
    ctx = await group(services, database, actor=verifier)
    _, headers = await login(services, client)
    assert len((await client.get("/api/plugins/board/scopes")).json()["scopes"]) == 1
    assert (await client.post("/api/plugins/board/group-check", json={})).status_code == 403
    reply = await client.post("/api/plugins/board/group-check", json={}, headers=headers)
    assert reply.status_code == 200
    raw = reply.json()["command"].split()[-1]
    async with database() as session:
        rows = list(await session.scalars(select(WebGroupCheck)))
        assert len(rows) == 1 and rows[0].digest == hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(AccessDenied):
        await services.scene_access.verify(context(USER, group="room"), raw)
    assert "已验证" in await services.scene_access.verify(ctx, raw)
    with pytest.raises(DashboardError):
        await services.scene_access.verify(ctx, raw)
    selector = scope_id(scene_scope(ctx.scene))
    assert (await client.get("/api/plugins/board/configs", params={"scope": selector})).status_code == 200
    await services.scene_access.observe(verifier, ctx.scene, False)
    assert (await client.get("/api/plugins/board/configs", params={"scope": selector})).status_code == 403


async def test_check_rotation_expiry_and_concurrent_claim(web, database):
    services, _ = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    old = (await services.scene_access.issue_check(USER))["command"].split()[-1]
    raw = (await services.scene_access.issue_check(USER))["command"].split()[-1]
    with pytest.raises(DashboardError):
        await services.scene_access.verify(ctx, old)
    result = await asyncio.gather(
        services.scene_access.verify(ctx, raw), services.scene_access.verify(ctx, raw), return_exceptions=True
    )
    assert sum(isinstance(item, str) for item in result) == 1
    assert sum(isinstance(item, DashboardError) for item in result) == 1
    async with database() as session:
        assert len(list(await session.scalars(select(WebSceneGrant)))) == 1
    raw = (await services.scene_access.issue_check(USER))["command"].split()[-1]
    async with database() as session, session.begin():
        await session.execute(update(WebGroupCheck).values(expires_at=0))
    with pytest.raises(DashboardError):
        await services.scene_access.verify(ctx, raw)


async def test_render_rechecks_role_after_slow_client_finishes(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    _, headers = await login(services, client)
    artifact = await services.clients.artifacts.create(USER, b"PNG", "image/png")

    async def render(*args, **kwargs):
        await services.scene_access.observe(USER, ctx.scene, False)
        return artifact

    services.boards.image = AsyncMock(side_effect=render)
    result = await client.post(
        "/api/plugins/board/render",
        json={"scope": scope_id(scene_scope(ctx.scene)), "name": "shared", "mode": "today"},
        headers=headers,
    )
    assert result.status_code == 403 and result.content != b"PNG"
