"""Bot and Dashboard share files, metadata, audit and concurrent writes."""

import asyncio
import io
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from framework.clients.ocr import OcrClient
from framework.contracts import AccessDenied, Role
from framework.curation.pickone import chat
from framework.curation.pickone.hashing import hash_id_of
from framework.models import PickOneContext
from framework.services.pickone import PickOneService

from .conftest import ROOT, access
from .test_boards import context
from .test_curation import seed_pickone
from .test_dashboard import login
from .test_dashboard import web as web


async def test_shared_search_like_comment_and_scene_isolation(web):
    services, client = web
    store, name, _ = await seed_pickone(services)
    pick = services.pickone
    ctx = context()
    cat, stat, data = await pick.select(ctx, "猫", "before")
    assert name == stat.name and data
    await pick.picked(ctx, cat, stat)
    await asyncio.gather(*(pick.like(ctx) for _ in range(12)))
    assert await pick.comment(ctx, "nice cat") == 1
    with pytest.raises(chat.StoreError, match="重复"):
        await pick.comment(ctx, "nice cat")
    with pytest.raises(chat.StoreError, match="字符"):
        await pick.comment(ctx, "a" * 33)
    with pytest.raises(chat.StoreError, match="本会话"):
        await pick.like(context(group="other-room"))
    # A new service instance restores scene references from ORM, not process memory.
    restored = PickOneService(services.curation, SimpleNamespace(renderer=Mock()))
    assert await restored.like(ctx) == 15
    async with services.session_factory() as session, session.begin():
        row = await session.get(PickOneContext, ctx.scene.key)
        row.last = {**row.last, "at": time.time() - 86401}
    with pytest.raises(chat.StoreError, match="一天"):
        await pick.like(ctx)
    await login(services, client)
    result = await client.get("/api/plugins/pickone/images/cats")
    assert result.status_code == 200
    record = store.get_image_stat("cats", name)
    assert record.likes == 15 and record.pickup_times == 1 and record.comments == ["nice cat"]
    # Dashboard writes are immediately visible to search; no separate cached library.
    parser = store.load_raw_parser("cats")
    parser[name] = {**parser[name], "ocr_text": "updated description"}
    store.save_raw_parser("cats", parser)
    assert (await pick.select(ctx, "cats", "updated"))[1].name == name


async def test_upload_pending_admin_approve_and_duplicate(web):
    services, client = web
    store, _, _ = await seed_pickone(services)
    output = io.BytesIO()
    Image.new("RGB", (24, 32), "blue").save(output, "PNG")
    data = output.getvalue()
    services.clients.http.download = AsyncMock(return_value=data)
    ctx = replace(context(), command_allowed=access(Role.EVERYONE))
    result = await services.pickone.upload(ctx, "猫", ["https://gchat.qpic.cn/synthetic"])
    assert result["pending"] == 1
    assert store.audit_count() == 1 and len(store.list_images("cats")) == 1
    admin = replace(context(ROOT), command_allowed=access(Role.SUPERUSER))
    with pytest.raises(AccessDenied):
        await services.pickone.approve(ctx)
    # Group managers have no privilege to approve globally shared content.
    with pytest.raises(AccessDenied):
        await services.pickone.approve(replace(ctx, command_allowed=access(Role.GROUP_ADMIN)))
    result = await services.pickone.approve(admin)
    assert result == {"approved": 1, "duplicate": 0}
    assert len(store.list_images("cats")) == 2 and not store.audit_count()
    result = await services.pickone.upload(admin, "cats", ["https://gchat.qpic.cn/synthetic"])
    assert result["duplicate"] == 1
    services.clients.http.download.return_value = b"not an image"
    result = await services.pickone.upload(ctx, "cats", ["https://gchat.qpic.cn/synthetic"])
    assert result["failed"] == 1 and result["category"] == "公共图库「猫」"
    assert result["errors"] == ["无法解码图片，或图片像素数超过限制。"]
    assert len(store.list_images("cats")) == 2
    with pytest.raises(chat.StoreError, match="最多"):
        await services.pickone.upload(ctx, "cats", ["https://gchat.qpic.cn/synthetic"] * 5)
    await login(services, client, ROOT)
    response = await client.get("/api/plugins/curation/admin/logs")
    assert response.status_code == 200 and "pickone.upload" in response.text


async def test_preview_prefix_and_empty_library(web):
    services, _ = web
    with pytest.raises(chat.StoreError, match="还没有"):
        await services.pickone.select(context(), "rand")
    store, name, _ = await seed_pickone(services)
    cat, images = await services.pickone.preview(context(), "猫")
    await services.pickone.remember_preview(context(), cat, images)
    assert len(images) == 1 and images[0]["thumbnail"]
    assert (await services.pickone.select(context(), "猫", prefix=hash_id_of(name[:-4])[:5]))[1].name == name
    stats = [SimpleNamespace(hash_id="abc"), SimpleNamespace(hash_id="abd")]
    with pytest.raises(chat.StoreError, match="多张"):
        chat.resolve_id(stats, "ab")
    assert chat.resolve_id(stats, "ab", ["abd"]).hash_id == "abd"
    assert chat.resolve_id(stats, "abc", ["abd"]).hash_id == "abc"


async def test_ocr_background_write_does_not_replace_human_edit(web):
    services, _ = web
    store, name, _ = await seed_pickone(services)
    pick = services.pickone
    store.save_raw_parser("cats", {name: {"ocr_text": "", "likes": 0, "comments": [], "pickup_times": 0}})
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def recognize(_):
        entered.set()
        await proceed.wait()
        return "OCR result"

    services.clients.ocr.enabled = True
    services.clients.ocr.read = AsyncMock(side_effect=recognize)
    await services.clients.jobs.start()
    await pick.enqueue_ocr()
    await entered.wait()
    await pick.enqueue_ocr()
    assert len(pick._ocr_jobs) == 1
    store.save_raw_parser("cats", {name: {"ocr_text": "Human description"}})
    proceed.set()
    await asyncio.gather(*tuple(pick._ocr_jobs.values()))
    assert store.get_image_stat("cats", name).ocr_text == "Human description"
    store.save_raw_parser("cats", {name: {"ocr_text": ""}})
    await pick.enqueue_ocr()
    await asyncio.gather(*tuple(pick._ocr_jobs.values()))
    assert store.get_image_stat("cats", name).ocr_text == "OCR result"
    assert not await pick.call(chat.missing_text)


async def test_disabled_ocr_never_loads_model(tmp_path, monkeypatch):
    client = OcrClient(tmp_path, enabled=False)
    read = Mock(side_effect=AssertionError("Must not load OCR"))
    monkeypatch.setattr(client, "_read", read)
    assert await client.read(b"unused") == ""
    read.assert_not_called()


def test_attachment_urls_only_come_from_trusted_native_qq_images():
    from framework.integration.platforms.qq import QQPlatform

    urls = [
        "https://gchat.qpic.cn/a",
        "//multimedia.nt.qq.com/a",
        "https://127.0.0.1/a",
        "https://qpic.cn.attacker.test/a",
        "https://qpic.cn@127.0.0.1/a",
        "http://gchat.qpic.cn/a",
        "https://gchat.qpic.cn:invalid/a",
    ]
    event = SimpleNamespace(attachments=[SimpleNamespace(content_type="image/png", url=url) for url in urls])
    assert QQPlatform().image_attachments(event) == (urls[0], "https:" + urls[1])


def test_attachment_diagnostics_classify_rejections_without_logging_private_data(monkeypatch):
    from framework.integration.platforms import qq

    records = []
    monkeypatch.setattr(qq.logger, "info", lambda template, *args: records.append(template.format(*args)))
    samples = [
        ("image/png", "https://gchat.qpic.cn/private-path?key=private-signature", "accepted"),
        ("image/png", "http://gchat.qpic.cn/private-path", "scheme"),
        ("image/png", "https://private-host.test/private-path", "host"),
        ("image/png", "https://private-user:YOUR_PASSWORD@gchat.qpic.cn/a", "userinfo"),
        ("image/png", "https://gchat.qpic.cn:8443/a", "port"),
        ("image/png", "https://gchat.qpic.cn:private-port/a", "invalid_url_or_port"),
        ("private-mime", "https://gchat.qpic.cn/a", "non_image"),
        ("image/png", "http://private-host.test:8080/a", "scheme,port,host"),
    ]
    event = SimpleNamespace(
        attachments=[SimpleNamespace(content_type=mime, url=url) for mime, url, _ in samples]
    )
    assert qq.QQPlatform().image_attachments(event) == (samples[0][1],)
    for record, (_, _, reason) in zip(records, samples, strict=False):
        assert f"result={reason}" in record
    assert len(records) == len(samples) + 1
    assert records[-1] == "QQ upload attachments: total=8 accepted=1"
    assert "private-" not in "\n".join(records)
    assert "YOUR_PASSWORD" not in "\n".join(records)
    assert "scheme=http domain=qpic.cn" in records[1]
    assert qq.QQPlatform().image_attachments(SimpleNamespace()) == ()
    assert records[-1] == "QQ upload attachments: total=0 accepted=0"


def test_native_qq_dispatch():
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.pickone_probe"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        capture_output=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"native_dispatch": true' in result.stdout


async def test_render_category_pages_and_preview(web):
    pytest.importorskip("pixie")
    from framework.views.pickone import PickOneBackend

    services, _ = web
    await seed_pickone(services)
    categories = await services.pickone.call(chat.categories)
    cat, items = await services.pickone.preview(context(), "猫")
    backend = PickOneBackend()
    pages = await asyncio.to_thread(
        backend.render_pages,
        {
            "title": "PickOne · 来只什么",
            "hint": "/来只 类别 [文字]",
            "items": [dict(categories[0], id="很长的类别名称" * 10, keys=["别名" * 60])] * 13,
        },
    )
    assert len(pages) == 2 and all(page.width == 1200 for page in pages)
    result = await asyncio.to_thread(
        backend.render,
        {
            "title": cat.id + " · 预览",
            "hint": "/预览来只 cats ID前缀 · 获取原图",
            "preview": True,
            "items": items * 8,
        },
    )
    assert result.width == 1200 and result.height < 2500
    assert result.data.startswith(b"\x89PNG")


async def test_ocr_reader_is_lazy_reused_and_off_event_loop(tmp_path, monkeypatch):
    import sys
    import threading

    main_thread = threading.get_ident()
    observed = []

    def readtext(pixels, detail):
        assert threading.get_ident() != main_thread
        assert detail == 0
        observed.append(pixels)
        return ["自动识别", "hello"]

    reader = Mock(readtext=readtext)
    factory = Mock(return_value=reader)
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=factory))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(array=lambda image: image.size))
    output = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(output, "PNG")
    client = OcrClient(tmp_path / "models", enabled=True)
    assert client._reader is None
    assert await client.read(output.getvalue()) == "自动识别 hello"
    assert await client.read(output.getvalue()) == "自动识别 hello"
    factory.assert_called_once_with(
        ["ch_sim", "en"],
        gpu=False,
        model_storage_directory=str(tmp_path / "models"),
        user_network_directory=str(tmp_path / "models" / "user_network"),
        verbose=False,
    )
    assert observed == [(40, 30), (40, 30)]


async def test_ocr_scheduler_retries_failure_and_does_not_repeat_empty_result(web):
    from nonebot_plugin_apscheduler import scheduler

    services, _ = web
    store, name, _ = await seed_pickone(services)
    store.save_raw_parser("cats", {name: {"ocr_text": ""}})
    services.clients.ocr.enabled = True
    services.clients.ocr.read = AsyncMock(side_effect=[RuntimeError("synthetic"), ""])
    await services.clients.jobs.start()
    await services.pickone.start()
    assert scheduler.get_job("pickone-ocr")
    results = await asyncio.gather(*tuple(services.pickone._ocr_jobs.values()), return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    assert await services.pickone.call(chat.missing_text)
    await services.pickone.enqueue_ocr()
    await asyncio.gather(*tuple(services.pickone._ocr_jobs.values()))
    assert not await services.pickone.call(chat.missing_text)
    await services.pickone.close()
    assert not scheduler.get_job("pickone-ocr")
