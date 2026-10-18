"""Group stores, native permissions, scoped web sessions and media isolation."""

import io
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from framework.contracts import AccessDenied, ExternalIdentity, Role
from framework.curation.pickone import chat
from framework.services.scene_access import scene_scope, scope_id

from .conftest import ROOT, access
from .test_boards import context
from .test_curation import seed_pickone
from .test_dashboard import login
from .test_dashboard import web as web
from .test_group_login import scoped_login


async def manager(services, room):
    actor = await services.identities.resolve(ExternalIdentity("qq", "bot", "group_member", room + "-admin"))
    ctx = context(actor, Role.GROUP_ADMIN, group=room)
    await services.scene_access.observe(actor, ctx.scene, True)
    return ctx


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (24, 32), color).save(output, "PNG")
    return output.getvalue()


async def test_same_names_are_isolated_and_group_admin_cannot_manage_public(web):
    services, _ = web
    public, _, _ = await seed_pickone(services)
    pick = services.pickone
    own, other = await manager(services, "own"), await manager(services, "other")
    member = replace(own, command_allowed=access(Role.EVERYONE))
    with pytest.raises(AccessDenied):
        await pick.create_category(member, "cats", "猫")
    await pick.create_category(own, "cats", "猫")
    await pick.create_category(other, "cats", "猫")
    with pytest.raises(chat.StoreError):
        await pick.create_category(own, "CATS", "同名")
    services.clients.http.download = AsyncMock(return_value=png("blue"))
    assert (await pick.upload(own, "group:cats", ["https://gchat.qpic.cn/test"]))["added"] == 1
    services.clients.http.download.return_value = png("red")
    assert (await pick.upload(other, "group:cats", ["https://gchat.qpic.cn/test"]))["added"] == 1
    own_cat, own_stat, own_data = await pick.select(own, "猫")
    _, other_stat, other_data = await pick.select(other, "猫")
    assert own_data != other_data and own_cat.img_key == "group:cats"
    assert (await pick.select(own, "public:cats"))[0].img_key == "public:cats"
    for ctx in (other, context()):
        with pytest.raises(chat.StoreError):
            await pick.select(ctx, "group:cats", prefix=own_stat.hash_id)
    await pick.picked(own, own_cat, own_stat)
    await pick.like(own)
    assert (
        services.curation.group_pickone(scene_scope(own.scene)).get_image_stat("cats", own_stat.name).likes
        == 1
    )
    assert (
        services.curation.group_pickone(scene_scope(other.scene))
        .get_image_stat("cats", other_stat.name)
        .likes
        == 0
    )
    services.clients.http.download.return_value = png("green")
    assert (await pick.upload(own, "public:cats", ["https://gchat.qpic.cn/test"]))["pending"] == 1
    assert public.audit_count() == 1
    with pytest.raises(AccessDenied):
        await pick.approve(own)
    assert (await pick.approve(own, "group"))["approved"] == 0
    assert public.audit_count() == 1
    # Normal group uploads wait for that group's administrators, not another group's.
    assert (await pick.upload(member, "group:cats", ["https://gchat.qpic.cn/test"]))["pending"] == 1
    assert (await pick.approve(other, "group"))["approved"] == 0
    assert (await pick.approve(own, "group"))["approved"] == 1
    private = context()
    with pytest.raises(chat.StoreError):
        await pick.select(private, "group:cats")
    assert all(
        item["img_key"].startswith("public:") for item in await pick.in_scene(private, chat.categories)
    )


async def test_dashboard_group_crud_media_and_role_revocation(web):
    services, client = web
    await seed_pickone(services)
    own, other = await manager(services, "own"), await manager(services, "other")
    await services.pickone.create_category(other, "cats", "Other secret")
    _, headers = await scoped_login(services, client, own)
    base = "/api/plugins/pickone/groups"
    own_id, other_id = scope_id(scene_scope(own.scene)), scope_id(scene_scope(other.scene))
    assert (await client.get(base + "/scopes")).json()["scopes"] == [{"id": own_id, "label": "本群图库"}]
    values = {"category_id": "本群猫", "keys": ["猫", "本群猫"]}
    assert (await client.post(base + "/categories/cats", json=values)).status_code == 403
    result = await client.post(base + "/categories/cats", json=values, headers=headers)
    assert result.status_code == 200, result.text
    assert (await client.get(base + "/categories")).json()["categories"][0]["keys"] == values["keys"]
    for path in ("/categories", "/images/cats", "/audit", "/media/cats/" + "a" * 32 + ".gif"):
        assert (await client.get(base + path, params={"scope": other_id})).status_code == 403
    for path in ("/categories", "/images/cats", "/admin/audit", "/submissions"):
        assert (await client.get("/api/plugins/pickone" + path)).status_code == 403
    services.clients.http.download = AsyncMock(return_value=png("navy"))
    member = replace(own, command_allowed=access(Role.EVERYONE))
    await services.pickone.upload(member, "group:cats", ["https://gchat.qpic.cn/test"])
    pending = (await client.get(base + "/audit")).json()["items"][0]
    name = pending["name"]
    response = await client.get(base + f"/media/cats/{name}", params={"pending": True})
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert "no-store" in response.headers["cache-control"]
    assert (
        await client.post(base + f"/audit/cats/{name}", json={"approve": True}, headers=headers)
    ).status_code == 200
    assert (await client.get(base + "/images/cats")).json()["total"] == 1
    assert (
        await client.put(
            base + "/images/cats",
            json={"name": name, "ocr_text": "本群描述", "comments": ["好"]},
            headers=headers,
        )
    ).status_code == 200
    assert (await services.pickone.select(member, "猫", "本群描述"))[1].name == name
    assert (
        await client.put(base + "/images/cats", json={"name": name, "comments": ["a" * 33]}, headers=headers)
    ).status_code == 400
    # An administrator of both groups still cannot expand a group-issued session.
    await services.scene_access.observe(own.actor, other.scene, True)
    assert (await client.get(base + "/categories", params={"scope": other_id})).status_code == 403
    await services.scene_access.observe(own.actor, own.scene, False)
    assert (await client.get(base + f"/media/cats/{name}")).status_code == 401
    await services.scene_access.observe(own.actor, own.scene, True)
    # Private superuser sessions can select every registered library.
    _, headers = await login(services, client, ROOT)
    scopes = (await client.get(base + "/scopes")).json()["scopes"]
    assert {item["id"] for item in scopes} >= {own_id, other_id}
    assert (await client.get(base + "/categories", params={"scope": other_id})).json()["categories"][0][
        "id"
    ] == "Other secret"
    response = await client.request(
        "DELETE", base + f"/images/cats/{name}", params={"scope": own_id}, json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert (await client.get(base + f"/media/cats/{name}", params={"scope": own_id})).status_code == 404
