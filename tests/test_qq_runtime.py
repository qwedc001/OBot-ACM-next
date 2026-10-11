from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from nonebot.adapters.qq.event import (
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    GuildMessageEvent,
)

from framework.catalog import CommandCatalog, CommandSpec
from framework.clients.renderer import PixieBackend
from framework.config import Settings
from framework.contracts import ExternalIdentity, RequestContext, Role, SceneKind, SceneRef
from framework.integration.api import build_internal_router
from framework.integration.platforms.qq import QQPlatform
from framework.runtime import Services
from framework.services.identities import IdentityService

from .conftest import USER


def event(model, **kwargs):
    return model.model_construct(id="event-1", **kwargs)


def test_group_users_share_scene_but_not_identity():
    first = event(GroupMessageCreateEvent, group_openid="group", author=SimpleNamespace(member_openid="a"))
    second = event(GroupMessageCreateEvent, group_openid="group", author=SimpleNamespace(member_openid="b"))
    a = QQPlatform().normalize(SimpleNamespace(self_id="app"), first)
    identity_a, scene_a = a.identity, a.scene
    b = QQPlatform().normalize(SimpleNamespace(self_id="app"), second)
    identity_b, scene_b = b.identity, b.scene
    assert identity_a != identity_b and scene_a == scene_b
    assert scene_a.kind == SceneKind.GROUP


def test_direct_message_is_not_mistaken_for_public_channel():
    dm = event(DirectMessageCreateEvent, author=SimpleNamespace(id="a"), guild_id="dm", channel_id="channel")
    guild = event(GuildMessageEvent, author=SimpleNamespace(id="a"), guild_id="guild", channel_id="channel")
    a = QQPlatform().normalize(SimpleNamespace(self_id="app"), dm)
    identity_dm, scene_dm = a.identity, a.scene
    b = QQPlatform().normalize(SimpleNamespace(self_id="app"), guild)
    identity_guild, scene_guild = b.identity, b.scene
    assert identity_dm == identity_guild
    assert scene_dm.kind == SceneKind.DIRECT and scene_dm.external_id == "dm"
    assert scene_guild.kind == SceneKind.CHANNEL and scene_guild.external_id == "channel"


async def test_identity_mapping_does_not_merge_apps_or_identifier_types(database):
    identities = IdentityService(database)
    keys = [
        ExternalIdentity("qq", "app", "c2c_user", "same"),
        ExternalIdentity("qq", "app", "group_member", "same"),
        ExternalIdentity("qq", "other", "c2c_user", "same"),
    ]
    actors = [await identities.resolve(key) for key in keys]
    assert len({actor.id for actor in actors}) == 3
    assert await IdentityService(database).resolve(keys[0]) == actors[0]


@pytest.mark.parametrize(
    ("token", "host", "authorization", "expected"),
    [
        (None, "127.0.0.1", "Bearer token", 404),
        ("token", "127.0.0.1", "", 401),
        ("token", "127.0.0.1", b"Bearer \xff", 401),
        ("token", "203.0.113.9", "Bearer token", 401),
        ("token", "127.0.0.1", "Bearer token", 200),
    ],
)
async def test_internal_api_requires_token_and_local_transport(
    tmp_path, token, host, authorization, expected, database
):
    catalog = CommandCatalog()
    catalog.register(
        CommandSpec(
            "ping",
            "core",
            "ping",
            "连通",
            Role.EVERYONE,
            aliases=("ping",),
            example_alias="ping",
            examples=("/core.ping hi",),
        )
    )
    catalog.register(CommandSpec("status", "core", "status", "状态", Role.SUPERUSER))
    services = Services(
        Settings(obot_data_dir=tmp_path, obot_internal_token=token), catalog, session_factory=database
    )
    app = FastAPI()
    app.include_router(build_internal_router(services))
    await services.start()
    try:
        assert services.started and services.clients.http is not None
        transport = httpx.ASGITransport(app=app, client=(host, 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/internal/v1/commands",
                headers={"Authorization": authorization, "X-Forwarded-For": "127.0.0.1"},
            )
            assert response.status_code == expected
            if expected == 200:
                payload = response.json()
                assert payload["separator"] == "."
                assert payload["directories"] == [{"path": ["core"], "name": "core", "summary": "core"}]
                assert [item["name"] for item in payload["commands"]] == ["core.ping"]
                assert payload["commands"][0]["path"] == ["core", "ping"]
                assert payload["commands"][0]["example_alias"] == "ping"
                assert payload["commands"][0]["examples"] == ["/ping hi"]
                assert payload["commands"][0]["usage"] == "/core.ping"
                health = await client.get("/internal/v1/health", headers={"Authorization": authorization})
                assert health.json()["ready"] and health.json()["command_count"] == 2
    finally:
        await services.close()
    assert not services.started
    await services.close()


async def test_pixie_render_service_produces_png_with_authorized_lease(tmp_path, database):
    pixie = pytest.importorskip("pixie")
    services = Services(Settings(obot_data_dir=tmp_path), CommandCatalog(), session_factory=database)
    await services.start()
    try:
        services.clients.renderer.register(
            "test", PixieBackend(lambda model: pixie.Image(model["width"], 12))
        )
        context = RequestContext(USER, SceneRef("qq", "app", SceneKind.C2C, "a"), "event-1")
        artifact = await services.clients.renderer.render(context.actor, "test", {"width": 24})
        assert artifact.width == 24 and artifact.height == 12 and artifact.mime == "image/png"
        async with services.clients.artifacts.lease(USER, artifact.id) as path:
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        await services.close()
