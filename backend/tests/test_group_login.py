"""Group-issued browser sessions cannot expand into another group's authority."""

import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from framework.contracts import ExternalIdentity, Role
from framework.dashboard import DashboardError
from framework.models import SceneAuthority, WebLoginToken
from framework.services.scene_access import scene_scope, scope_id

from . import test_dashboard
from .conftest import USER
from .test_boards import bind, context
from .test_group_dashboard import group

web = test_dashboard.web


async def scoped_login(services, client, ctx):
    raw = (await services.dashboard.issue(ctx)).split("#")[1]
    response = await client.post(
        "/api/auth/exchange", json={"token": raw}, headers={"Origin": "https://bot.example.com"}
    )
    assert response.status_code == 200
    return response, {"Origin": "https://bot.example.com", "X-CSRF-Token": response.json()["csrf_token"]}


async def test_group_session_only_lists_and_edits_issuing_group(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    own = await group(services, database, room="own")
    other = await group(services, database, room="other")
    await bind(services.boards, name="personal")
    response, headers = await scoped_login(services, client, own)
    assert response.json()["user"]["role"] == "group_admin"
    assert response.json()["user"]["tools"] == ["board", "pickone"]
    selector, other_selector = scope_id(scene_scope(own.scene)), scope_id(scene_scope(other.scene))
    assert (await client.get("/api/plugins/board/scopes")).json()["scopes"] == [
        {"id": selector, "label": "本群榜单"}
    ]
    assert [p["slug"] for p in (await client.get("/api/plugins")).json()["plugins"]] == ["board", "pickone"]
    assert (await client.get("/api/plugins/board/configs")).status_code == 200
    configs = await client.get("/api/plugins/board/configs", params={"scope": selector})
    assert configs.status_code == 200 and configs.json()["configs"][0]["name"] == "shared"
    assert not any(
        value in configs.text for value in ("password", "credentials", "test-secret", "test-admin")
    )
    for target in ("personal", other_selector, "scene:qq:bot:group:own"):
        assert (await client.get("/api/plugins/board/configs", params={"scope": target})).status_code == 403
        assert (
            await client.post(
                "/api/plugins/board/starred",
                json={"scope": target, "name": "shared", "enabled": False},
                headers=headers,
            )
        ).status_code == 403
    assert (
        await client.post(
            "/api/plugins/board/settings",
            json={"scope": selector, "name": "shared", "options": {"show_unrated": False}},
            headers=headers,
        )
    ).status_code == 200
    for path in ("/api/plugins/commands", "/api/plugins/runtime"):
        assert (await client.get(path)).status_code == 403
    assert (await client.post("/api/plugins/board/group-check", json={}, headers=headers)).status_code == 403


async def test_group_link_and_session_recheck_role_and_expiry(web, database):
    services, client = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    raw = (await services.dashboard.issue(ctx)).split("#")[1]
    await services.scene_access.observe(USER, ctx.scene, False)
    with pytest.raises(DashboardError):
        await services.dashboard.exchange(raw)
    await services.scene_access.observe(USER, ctx.scene, True)
    await scoped_login(services, client, ctx)
    await services.scene_access.observe(USER, ctx.scene, False)
    assert (await client.get("/api/auth/me")).status_code == 401
    await services.scene_access.observe(USER, ctx.scene, True)
    await scoped_login(services, client, ctx)
    async with database() as session, session.begin():
        await session.execute(
            update(SceneAuthority).values(verified_at=time.time() - services.settings.obot_web_group_ttl - 1)
        )
    assert (await client.get("/api/plugins/board/configs")).status_code == 401
    with pytest.raises(DashboardError):
        await services.dashboard.issue(context(group="room"))


async def test_private_routing_requires_canonical_or_explicitly_verified_identity(web, database):
    services, _ = web
    private = await services.identities.resolve(ExternalIdentity("qq", "bot", "c2c_user", "private-person"))
    actor = await services.identities.resolve(ExternalIdentity("qq", "bot", "group_member", "group-person"))
    ctx = context(actor, Role.GROUP_ADMIN, group="room")
    await services.scene_access.observe(actor, ctx.scene, True)
    with pytest.raises(DashboardError, match="关联"):
        await services.dashboard.private_recipient(ctx, {"c2c_user"})
    code = (await services.scene_access.issue_check(private))["command"].split()[-1]
    await services.scene_access.verify(ctx, code)
    assert (await services.dashboard.private_recipient(ctx, {"c2c_user"})).external_id == "private-person"
    # This explicit group grant must not route links for a different group.
    other = context(actor, Role.GROUP_ADMIN, group="other")
    await services.scene_access.observe(actor, other.scene, True)
    with pytest.raises(DashboardError):
        await services.dashboard.private_recipient(other, {"c2c_user"})
    actor = await services.identities.link(actor.identity_key, private.id)
    other = context(actor, Role.GROUP_ADMIN, group="other")
    assert (await services.dashboard.private_recipient(other, {"c2c_user"})).external_id == "private-person"
    extra = ExternalIdentity("qq", "bot", "c2c_user", "ambiguous")
    await services.identities.resolve(extra)
    await services.identities.link(extra.key, private.id)
    with pytest.raises(DashboardError):
        await services.dashboard.private_recipient(other, {"c2c_user"})


async def test_revoked_undeliverable_link_cannot_be_exchanged(web, database):
    services, _ = web
    services.clients.hydro.validate = AsyncMock()
    ctx = await group(services, database)
    link = await services.dashboard.issue(ctx)
    await services.dashboard.revoke_link(link)
    with pytest.raises(DashboardError):
        await services.dashboard.exchange(link.split("#")[1])
    async with database() as session:
        assert list(await session.scalars(select(WebLoginToken))) == []
