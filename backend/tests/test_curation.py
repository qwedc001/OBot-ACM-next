"""Exercise restored EP workflows through real QQ-session HTTP boundaries."""

import asyncio
import hashlib
import io
import json
from unittest.mock import AsyncMock

from PIL import Image
from sqlalchemy import select

from framework.dashboard import WebAccount
from framework.models import ContentSubmission

from .conftest import ROOT, USER
from .test_dashboard import login
from .test_dashboard import web as web

PICK = "/api/plugins/pickone"
CONTEST = "/api/plugins/contestlist"
REVIEW = "/api/plugins/curation/admin"
CONTEST_VALUE = {
    "platform": "ICPC",
    "abbr": "Demo",
    "name": "Demo regional",
    "start_time": 1800000000,
    "duration": 18000,
    "supplement": "Campus",
}


async def seed_pickone(services):
    await services.curation.start()
    store = services.curation.pickone
    store.config_path.write_text(json.dumps({"cats": {"id": "猫", "key": ["猫"]}}), encoding="utf-8")
    folder = store.lib_dir / "cats"
    folder.mkdir(exist_ok=True)
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(stream, format="GIF")
    data = stream.getvalue()
    name = hashlib.md5(data).hexdigest() + ".gif"
    (folder / name).write_bytes(data)
    (folder / "parser.json").write_text(json.dumps({name: {"ocr_text": "before", "likes": 2}}))
    return store, name, data


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


async def approve(client, headers, submission):
    return ok(await client.post(f"{REVIEW}/review/{submission}", headers=headers, json={"approve": True}))


async def test_contest_create_review_preview_apply_and_delete(web):
    services, client = web
    _, headers = await login(services, client)
    assert ok(await client.get(CONTEST + "/items"))["items"] == []
    created = ok(
        await client.post(CONTEST + "/submissions", headers=headers, json={"contest": CONTEST_VALUE}), 201
    )
    submission = created["submission"]["id"]
    assert created["submission"]["author_name"] == USER.id
    assert (
        await client.post(f"{REVIEW}/review/{submission}", headers=headers, json={"approve": True})
    ).status_code == 403
    assert ok(await client.get(CONTEST + "/items"))["items"] == []
    _, headers = await login(services, client, ROOT)
    await approve(client, headers, submission)
    path = services.curation.contestlist.data_path
    ok(await client.get(CONTEST + "/admin/apply/preview"))
    assert not path.exists(), "Preview must not write files"
    result = ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))
    assert result["created"] == 1
    assert json.loads(path.read_text()) == [CONTEST_VALUE]
    assert ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))["applied"] == 0
    item = ok(await client.get(CONTEST + "/items"))["items"][0]
    deletion = ok(
        await client.post(CONTEST + "/admin/delete", headers=headers, json={"hash": item["hash"]}), 201
    )
    await approve(client, headers, deletion["submission"]["id"])
    ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))
    assert json.loads(path.read_text()) == []
    logs = ok(await client.get(REVIEW + "/logs"))["items"]
    assert any(row["action"] == "apply_contest_changes" for row in logs)


async def test_pickone_metadata_media_and_category_workflow(web):
    services, client = web
    store, name, data = await seed_pickone(services)
    assert (await client.get(f"{PICK}/images/cats/raw/{name}")).status_code == 401
    _, headers = await login(services, client)
    assert ok(await client.get(PICK + "/categories"))["categories"][0]["id"] == "猫"
    assert (await client.get(f"{PICK}/images/cats/raw/{name}")).content == data
    thumb = await client.get(f"{PICK}/images/cats/thumb/{name}")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    batch = ok(
        await client.post(
            PICK + "/submissions/batch?img_key=cats",
            headers=headers,
            json={"name": name, "ocr_text": "after", "likes_delta": 3, "comments": ["hello"]},
        )
    )
    assert len(batch["created"]) == 3
    ids = [row["id"] for row in ok(await client.get(PICK + "/submissions"))["items"]]
    assert store.parser_entry("cats", name)["ocr_text"] == "before"
    new_category = ok(
        await client.post(
            PICK + "/submissions?img_key=dogs&type=category_create",
            headers=headers,
            json={"category_id": "狗", "keys": ["狗"]},
        ),
        201,
    )
    ids.append(new_category["submission"]["id"])
    _, headers = await login(services, client, ROOT)
    reviewed = ok(
        await client.post(REVIEW + "/review/batch", headers=headers, json={"ids": ids, "approve": True})
    )
    assert reviewed["succeeded"] == ids and not reviewed["failed"]
    ok(await client.get(PICK + "/admin/apply/preview"))
    assert store.parser_entry("cats", name)["ocr_text"] == "before"
    ok(await client.post(PICK + "/admin/apply", headers=headers, json={}))
    entry = store.parser_entry("cats", name)
    assert entry["ocr_text"] == "after" and entry["likes"] == 5 and entry["comments"] == ["hello"]
    assert store.load_categories()["dogs"].id == "狗"


async def test_conflict_preview_resolution_and_plugin_isolation(web, database):
    services, client = web
    store, name, _ = await seed_pickone(services)
    _, headers = await login(services, client)
    created = ok(
        await client.post(
            PICK + "/submissions?img_key=cats&type=ocr_text",
            headers=headers,
            json={"name": name, "ocr_text": "submitted"},
        ),
        201,
    )
    submission = created["submission"]["id"]
    _, headers = await login(services, client, ROOT)
    await approve(client, headers, submission)
    parser = store.parser_path("cats")
    parser.write_text(json.dumps({name: {"ocr_text": "external change", "likes": 2}}))
    preview = ok(await client.get(PICK + "/admin/apply/preview"))
    assert preview["conflicts"]
    async with database() as session:
        assert (await session.get(ContentSubmission, submission)).status == "approved"
    ok(await client.post(PICK + "/admin/conflicts/scan", headers=headers))
    # One plugin cannot resolve or withdraw another plugin's submission.
    assert (
        await client.post(
            f"{CONTEST}/admin/conflicts/{submission}/resolve", headers=headers, json={"keep_new": True}
        )
    ).status_code in {400, 404, 409}
    assert (
        await client.post(
            f"{CONTEST}/admin/conflicts/{submission}/resolve", headers=headers, json={"keep_new": False}
        )
    ).status_code == 404
    ok(
        await client.post(
            f"{PICK}/admin/conflicts/{submission}/resolve", headers=headers, json={"keep_new": True}
        )
    )
    ok(await client.post(PICK + "/admin/apply", headers=headers, json={}))
    assert store.parser_entry("cats", name)["ocr_text"] == "submitted"


async def test_owner_csrf_scope_and_live_admin_revocation(web, database):
    services, client = web
    _, headers = await login(services, client, ROOT)
    created = ok(
        await client.post(CONTEST + "/submissions", headers=headers, json={"contest": CONTEST_VALUE}), 201
    )
    submission = created["submission"]["id"]
    _, headers = await login(services, client)
    assert (await client.delete(f"{CONTEST}/submissions/{submission}", headers=headers)).status_code == 403
    assert (await client.post(CONTEST + "/submissions", json={"contest": CONTEST_VALUE})).status_code == 403
    assert (
        await client.post(
            CONTEST + "/submissions",
            headers={**headers, "Origin": "https://other.example"},
            json={"contest": CONTEST_VALUE},
        )
    ).status_code == 403
    assert (await client.get(REVIEW + "/logs")).status_code == 403
    assert (
        await client.post(CONTEST + "/admin/delete", headers=headers, json={"hash": "x"})
    ).status_code == 403
    services.dashboard.authenticate = AsyncMock(
        return_value=WebAccount(ROOT, True, "csrf", "qq:bot:group:room")
    )
    for path in (PICK + "/categories", CONTEST + "/items", REVIEW + "/queue"):
        assert (await client.get(path)).status_code == 403


async def test_withdraw_and_duplicate_resubmit_are_atomic(web, database):
    services, client = web
    _, headers = await login(services, client)
    responses = await asyncio.gather(
        *[
            client.post(CONTEST + "/submissions", headers=headers, json={"contest": CONTEST_VALUE})
            for _ in range(4)
        ]
    )
    ids = {ok(response, 201)["submission"]["id"] for response in responses}
    assert len(ids) == 1
    async with database() as session:
        assert len(list(await session.scalars(select(ContentSubmission)))) == 1
    ok(await client.delete(f"{CONTEST}/submissions/{ids.pop()}", headers=headers))
    assert ok(await client.get(CONTEST + "/submissions"))["total"] == 0


async def test_native_image_audit_accept_reject_and_bad_media(web):
    services, client = web
    store, name, data = await seed_pickone(services)
    folder = store.lib_dir / "__AUDIT__" / "cats"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    _, headers = await login(services, client)
    assert (await client.get(PICK + "/admin/audit")).status_code == 403
    _, headers = await login(services, client, ROOT)
    assert ok(await client.get(PICK + "/admin/audit"))["total"] == 1
    ok(await client.post(f"{PICK}/admin/audit/cats/{name}/approve", headers=headers))
    assert not (folder / name).exists()
    (folder / name).write_bytes(data)
    ok(await client.post(f"{PICK}/admin/audit/cats/{name}/reject", headers=headers))
    assert not (folder / name).exists()
    (store.lib_dir / "cats" / name).write_bytes(b"corrupt GIF")
    assert (await client.get(f"{PICK}/images/cats/thumb/{name}")).status_code == 415
    assert (await client.get(PICK + "/images/cats/raw/not-an-image.gif")).status_code == 404


async def test_contest_field_updates_conflict_rejection_and_unreview(web):
    services, client = web
    await services.curation.start()
    path = services.curation.contestlist.data_path
    path.write_text(json.dumps([{**CONTEST_VALUE, "external": "preserve me"}]))
    _, headers = await login(services, client)
    item = ok(await client.get(CONTEST + "/items"))["items"][0]
    payload = {"hash": item["hash"], "contest": {**CONTEST_VALUE, "abbr": "Edited", "duration": 12000}}
    response = ok(await client.post(CONTEST + "/submissions/update", headers=headers, json=payload), 201)
    ids = [row["id"] for row in response["submissions"]]
    assert len(ids) == 2
    _, headers = await login(services, client, ROOT)
    for id_ in ids:
        await approve(client, headers, id_)
    ok(await client.post(f"{REVIEW}/unreview/{ids[0]}", headers=headers))
    await approve(client, headers, ids[0])
    # A concurrent external edit is shown as conflict, never silently overwritten.
    path.write_text(json.dumps([{**CONTEST_VALUE, "abbr": "External", "external": "preserve me"}]))
    scanned = ok(await client.post(CONTEST + "/admin/conflicts/scan", headers=headers))
    assert scanned["conflicts"]
    for row in ok(await client.get(CONTEST + "/admin/conflicts"))["items"]:
        ok(
            await client.post(
                f"{CONTEST}/admin/conflicts/{row['id']}/resolve", headers=headers, json={"keep_new": False}
            )
        )
    ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))
    after = json.loads(path.read_text())[0]
    assert after["abbr"] == "External" and after["external"] == "preserve me"


async def test_failed_apply_and_corrupt_files_do_not_claim_success(web, database, monkeypatch):
    services, client = web
    _, headers = await login(services, client)
    response = ok(
        await client.post(CONTEST + "/submissions", headers=headers, json={"contest": CONTEST_VALUE}), 201
    )
    id_ = response["submission"]["id"]
    _, headers = await login(services, client, ROOT)
    await approve(client, headers, id_)

    def fail_write(*args, **kwargs):
        raise OSError("private filesystem path must not reach HTTP")

    from framework.curation.contestlist import store as store_module

    monkeypatch.setattr(store_module.os, "replace", fail_write)
    response = await client.post(CONTEST + "/admin/apply", headers=headers, json={})
    assert response.status_code == 500 and "private filesystem" not in response.text
    async with database() as session:
        assert (await session.get(ContentSubmission, id_)).status == "approved"
    services.curation.pickone.config_path.write_text("broken json")
    response = await client.get(PICK + "/categories")
    assert response.status_code == 400
    assert services.curation.pickone.config_path.read_text() == "broken json"


async def test_service_worker_yields_to_bot_and_uses_same_orm(database, tmp_path):
    import threading
    import time

    from framework.services.curation import CurationService

    service = CurationService(database, tmp_path)
    worker_started = threading.Event()
    loop_thread = threading.get_ident()

    def operation(repo):
        assert threading.get_ident() != loop_thread
        worker_started.set()
        time.sleep(0.08)
        repo.add_log(actor_id=ROOT.id, username="root", action="test_service")
        return repo.list_logs()[1]

    task = asyncio.create_task(service.run(operation))
    while not worker_started.is_set():
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.01)
    assert not task.done(), "Synchronous work must not block the bot event loop"
    assert await task == 1
