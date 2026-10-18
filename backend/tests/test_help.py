import copy
import struct
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import select

from framework.catalog import CommandCatalog, CommandDirectory, CommandSpec
from framework.clients.artifacts import ArtifactUnavailable
from framework.config import Settings
from framework.contracts import AccessDenied, Actor, RequestContext, Role, SceneKind, SceneRef
from framework.models import ImageArtifact
from framework.responses import text_chunks
from framework.runtime import Services
from framework.services.help import HelpService
from framework.views.help import MAX_HEIGHT, HelpPixieBackend
from framework.views.layout import fits

from .conftest import ROOT, USER, access


def help_catalog():
    catalog = CommandCatalog()
    catalog.register_directory(CommandDirectory(("maintenance",), "维护与诊断"))
    catalog.register(
        CommandSpec(
            "help",
            "help",
            "help",
            "查看帮助",
            Role.EVERYONE,
            aliases=(),
            directory=(),
            examples=("/help render",),
        )
    )
    catalog.register(
        CommandSpec("status", "maintenance", "status", "运行状态", Role.SUPERUSER, aliases=("status",))
    )
    catalog.register(CommandSpec("nested", "tools", "nested.ping", "嵌套功能", Role.EVERYONE, hidden=True))
    catalog.register(
        CommandSpec(
            "private",
            "secret",
            "board",
            "私有榜单",
            Role.SUPERUSER,
        )
    )
    catalog.register(
        CommandSpec("dm", "dm", "identity", "私聊身份", Role.EVERYONE, scopes=frozenset({SceneKind.C2C}))
    )
    return catalog


def command_ids(model):
    return {command["id"] for group in model["groups"] for command in group["commands"]}


async def test_unmatched_suggestions_filter_permissions_scopes_and_hidden_commands():
    service = HelpService(help_catalog(), Mock())
    normal = await service.unmatched(access(), "/", "stats")
    assert "指令匹配失败 /stats，请检查指令语法后重试。" in normal
    assert "/status" not in normal and "运行状态" not in normal
    admin = await service.unmatched(access(Role.SUPERUSER), "!", "stats")
    assert "!status — 运行状态" in admin
    assert "私聊身份" not in await service.unmatched(access(), "/", "dm.identit")
    assert "私聊身份" in await service.unmatched(access(scene=SceneKind.C2C), "/", "dm.identit")
    assert "嵌套功能" not in await service.unmatched(access(), "/", "tools.nested.pin")
    empty = HelpService(CommandCatalog(), Mock())
    assert "你可能想用" not in await empty.unmatched(access(), "/", "unknown")


async def test_unmatched_suggestions_are_bounded_and_deduplicate_aliases():
    catalog = CommandCatalog()
    for index in range(5):
        catalog.register(
            CommandSpec(
                f"test.{index}",
                "test",
                f"ping{index}",
                f"测试{index}",
                aliases=(f"ping{index}", f"pang{index}"),
            )
        )
    response = await HelpService(catalog, Mock()).unmatched(access(), "/", "ping")
    suggestions = [line for line in response.splitlines() if " — " in line]
    assert len(suggestions) == 3
    assert len({line.split(" — ")[1] for line in suggestions}) == 3


async def test_full_help_filters_roles_and_scenes():
    catalog = help_catalog()
    model = await catalog.help_model(access())
    assert command_ids(model) == {"help", "nested"}
    assert model["count"] == 2
    text = catalog.help_model_text(model)
    assert "/help" in text and "/maintenance.status" not in text and "secret" not in text
    assert command_ids(await catalog.help_model(access(), "tools")) == {"nested"}
    assert command_ids(await catalog.help_model(access(scene=SceneKind.C2C))) == {"help", "nested", "dm"}
    assert not (await catalog.help_model(access(), "status"))["found"]
    assert command_ids(await catalog.help_model(access(Role.GROUP_ADMIN))) == {"help", "nested"}
    assert command_ids(await catalog.help_model(access(Role.SUPERUSER))) == {
        "help",
        "nested",
        "dm",
        "status",
        "private",
    }


async def test_superuser_full_help_is_identical_in_group_and_private():
    catalog = help_catalog()
    group = await catalog.help_model(access(Role.SUPERUSER))
    private = await catalog.help_model(access(Role.SUPERUSER))
    assert group == private and command_ids(group) == {"help", "nested", "status", "private", "dm"}


async def test_native_png_help_is_private_and_filtered(tmp_path, database):
    pixie = pytest.importorskip("pixie")
    services = Services(
        Settings(obot_data_dir=tmp_path, obot_superusers=frozenset({ROOT.id})),
        help_catalog(),
        session_factory=database,
    )
    await services.start()
    context = RequestContext(
        USER, SceneRef("qq", "bot", SceneKind.GROUP, "g"), "event", command_allowed=access()
    )
    try:
        result = await services.help.reply(context, "")
        from framework.messages import Image, Message

        assert isinstance(result, tuple) and len(result) == 1 and isinstance(result[0], Message)
        data = next(part.source for part in result[0].parts if isinstance(part, Image))
        assert isinstance(data, bytes) and data.startswith(b"\x89PNG\r\n\x1a\n")
        async with database() as session:
            artifact = (await session.scalars(select(ImageArtifact))).one()
        assert fits(artifact.width, artifact.height) and artifact.height <= MAX_HEIGHT
        async with services.clients.artifacts.lease(USER, artifact.id) as path:
            image = pixie.read_image(str(path))
            assert image.width == artifact.width and image.height == artifact.height
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        with pytest.raises(AccessDenied):
            async with services.clients.artifacts.lease(Actor("other", "other"), artifact.id):
                pass
        async with services.clients.artifacts.lease(ROOT, artifact.id) as path:
            assert path.is_file()
        with pytest.raises(ArtifactUnavailable):
            async with services.clients.artifacts.lease(USER, "bad"):
                pass
    finally:
        await services.close()


async def test_render_unavailable_returns_complete_permission_filtered_text(tmp_path, database):
    services = Services(Settings(obot_data_dir=tmp_path), help_catalog(), session_factory=database)
    await services.start()
    try:
        context = RequestContext(
            USER, SceneRef("qq", "bot", SceneKind.GROUP, "g"), "event", command_allowed=access()
        )
        with patch.object(services.clients.renderer, "render_pages", side_effect=ImportError("no pixie")):
            result = await services.help.reply(context, "")
        assert "/tools.nested.ping" in result and "/help" in result
        assert "私有榜单" not in result and "图片暂不可用" in result
        for query in ("render secret", "render status"):
            assert "没有找到" in await services.help.reply(context, query)
    finally:
        await services.close()


async def test_permission_change_during_render_does_not_send_stale_picture(tmp_path, database):
    services = Services(Settings(obot_data_dir=tmp_path), help_catalog(), session_factory=database)
    await services.start()
    current = Role.SUPERUSER

    async def check(spec):
        return await access(current)(spec)

    context = RequestContext(
        USER, SceneRef("qq", "bot", SceneKind.GROUP, "g"), "event", command_allowed=check
    )
    try:

        async def changed(*args, **kwargs):
            nonlocal current
            current = Role.EVERYONE
            return ()

        with patch.object(services.clients.renderer, "render_pages", side_effect=changed):
            result = await services.help.reply(context, "")
        assert isinstance(result, str) and "运行状态" not in result
        assert "权限发生变化" in result and "/tools.nested.ping" in result
    finally:
        await services.close()


async def test_many_commands_paginate_without_omission_or_clipping():
    pytest.importorskip("pixie")
    catalog = CommandCatalog()
    for index in range(34):
        catalog.register(
            CommandSpec(
                f"many.c{index:02}",
                "many",
                f"c{index:02}",
                "较长的中文说明用于检查折行：" + "可用功能 " * 9,
                Role.EVERYONE,
                examples=(f"/many.c{index:02} 参数",),
            )
        )
    model = await catalog.help_model(access())
    backend = HelpPixieBackend()
    pages = backend.layout(model)
    assert pages and all(page.sections[1].get_columns() == 4 for page in pages)
    assert [
        item._help.id for page in pages for category in page.categories for item in category.section_items
    ] == [spec.id for spec in catalog.commands]
    for page in pages:
        assert page.measure()[1] <= MAX_HEIGHT
        assert fits(*page.measure())
    images = backend.render_pages(model)
    assert len(images) == len(pages)
    assert all(
        struct.unpack(">II", image.data[16:24]) == page.measure()
        for page, image in zip(pages, images, strict=True)
    )
    # Excessively long authored metadata falls back to full text rather than truncation.
    oversized = copy.deepcopy(model)
    oversized["groups"][0]["commands"][0]["summary"] = "长说明" * 5000
    with pytest.raises(ValueError, match="exceeds"):
        backend.layout(oversized)


def layout_model(counts):
    return {
        "title": "分栏测试",
        "count": sum(counts),
        "groups": [
            {
                "name": f"module{index}",
                "summary": "模块说明",
                "commands": [
                    {
                        "id": f"m{index}.c{number}",
                        "name": f"module{index}.command{number}",
                        "mode": "command",
                        "summary": "功能说明",
                        "usage": f"/module{index}.command{number}",
                        "aliases": [],
                        "examples": [f"/module{index}.command{number} 参数"],
                    }
                    for number in range(count)
                ],
            }
            for index, count in enumerate(counts)
        ],
    }


@pytest.mark.parametrize("counts", [(4,), (2, 1), (2, 1, 1), (1,) * 7, (0, 1, 0)])
def test_help_uses_original_four_column_canvas_and_keeps_all_categories(counts):
    pytest.importorskip("pixie")
    model = layout_model(counts)
    pages = HelpPixieBackend().layout(model)
    assert all(page.measure()[0] == 4496 and fits(*page.measure()) for page in pages)
    assert [
        item._help.id for page in pages for category in page.categories for item in category.section_items
    ] == [command["id"] for group in model["groups"] for command in group["commands"]]
    assert not HelpPixieBackend().layout(layout_model((0, 0)))


def test_whole_categories_keep_original_order_and_geometry():
    pytest.importorskip("pixie")
    from framework.views.renderer.pixie.render_help import _HELP_CATEGORY_PADDING

    model = layout_model((1, 4, 2, 1, 3, 1))
    pages = HelpPixieBackend().layout(model)
    assert len(pages) == 1
    page = pages[0]
    assert [category.str_title.content for category in page.categories] == [
        f"module{i} · 模块说明" for i in range(6)
    ]
    columns, height = page.sections[1]._split_columns(page.categories, _HELP_CATEGORY_PADDING)
    assert len(columns) <= 4
    assert [c for column in columns for c in column] == page.categories
    assert height == max(sum(c.get_height() for c in column) + 136 * (len(column) - 1) for column in columns)
    assert struct.unpack(">II", HelpPixieBackend().render(model).data[16:24]) == page.measure()


def test_overflow_preserves_every_command_in_order_and_repeats_category_heading():
    pytest.importorskip("pixie")
    model = layout_model((140, 3, 2, 1))
    model["groups"][0]["summary"] = "多行模块标题 " * 20
    pages = HelpPixieBackend().layout(model)
    assert len(pages) > 1
    assert all(fits(*page.measure()) for page in pages)
    categories = [category for page in pages for category in page.categories]
    assert [item._help.id for category in categories for item in category.section_items] == [
        c["id"] for group in model["groups"] for c in group["commands"]
    ]
    assert sum(c.str_title.content.startswith("module0 ") for c in categories) > 1


def test_long_single_module_uses_columns_before_pages():
    pytest.importorskip("pixie")
    pages = HelpPixieBackend().layout(layout_model((20,)))
    assert len(pages) == 1
    assert sum(len(c.section_items) for c in pages[0].categories) == 20


def test_long_text_help_splits_losslessly():
    text = ("中文帮助。\n" * 600) + "a" * 3200
    chunks = text_chunks(text)
    assert len(chunks) > 1 and "".join(chunks) == text
    assert all(len(chunk) <= 1500 for chunk in chunks)
    assert not text_chunks("")
    with pytest.raises(ValueError):
        text_chunks("x", 0)


async def test_explicit_text_help_never_invokes_renderer(tmp_path, database):
    services = Services(Settings(obot_data_dir=tmp_path), help_catalog(), session_factory=database)
    await services.start()
    try:
        context = RequestContext(
            USER, SceneRef("qq", "bot", SceneKind.GROUP, "g"), "event", command_allowed=access()
        )
        with patch.object(
            services.clients.renderer, "render_pages", side_effect=AssertionError("text should not render")
        ):
            result = await services.help.reply(context, draw=False)
        assert isinstance(result, str) and "/help" in result and "/maintenance.status" not in result
    finally:
        await services.close()
