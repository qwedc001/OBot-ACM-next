import copy
import struct
from unittest.mock import patch

import pytest
from sqlalchemy import select

from framework.catalog import CommandCatalog, CommandDirectory, CommandSpec
from framework.clients.artifacts import ArtifactUnavailable
from framework.config import Settings
from framework.contracts import AccessDenied, Actor, RequestContext, Role, SceneKind, SceneRef
from framework.models import ImageArtifact
from framework.responses import text_chunks
from framework.runtime import Services
from framework.services.help import parse_help_request
from framework.views.help import (
    COLUMN_GAP,
    CONTENT_WIDTH,
    FOOTER_HEIGHT,
    HEADER_HEIGHT,
    MARGIN,
    MAX_COLUMNS,
    MAX_HEIGHT,
    WIDTH,
    HelpPixieBackend,
)

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


@pytest.mark.parametrize(
    "args,expected",
    [
        ("", (True, "")),
        ("maintenance", (True, "maintenance")),
        ("render", (True, "")),
        (" R ping ", (True, "ping")),
        ("draw tools.nested", (True, "tools.nested")),
        ("d status", (True, "status")),
        ("renderer", (True, "renderer")),
        ("text", (False, "")),
        ("text maintenance", (False, "maintenance")),
        (" TEXT ping ", (False, "ping")),
    ],
)
def test_render_mode_is_a_complete_token(args, expected):
    assert parse_help_request(args) == expected


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
        assert artifact.width == WIDTH + CONTENT_WIDTH + COLUMN_GAP and artifact.height <= MAX_HEIGHT
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
    assert len(pages) > 1
    assert [
        card.command_id
        for page in pages
        for column in page.columns
        for module in column.modules
        for card in module.cards
    ] == [spec.id for spec in catalog.commands]
    for page in pages:
        assert page.height <= MAX_HEIGHT
        assert HEADER_HEIGHT + FOOTER_HEIGHT + max(column.height for column in page.columns) == page.height
    images = backend.render_pages(model)
    assert len(images) == len(pages)
    assert all(struct.unpack(">II", image.data[16:24]) == (WIDTH, image.height) for image in images)
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
def test_column_count_is_based_on_nonempty_modules_and_capped_at_three(counts):
    pytest.importorskip("pixie")
    pages = HelpPixieBackend().layout(layout_model(counts))
    columns = min(MAX_COLUMNS, sum(count > 0 for count in counts))
    assert all(len(page.columns) == columns for page in pages)
    assert all(
        page.width == MARGIN * 2 + CONTENT_WIDTH * columns + COLUMN_GAP * (columns - 1) for page in pages
    )
    assert not HelpPixieBackend().layout(layout_model((0, 0)))


def test_whole_modules_use_measured_height_and_always_choose_the_shortest_column():
    pytest.importorskip("pixie")
    model = layout_model((1, 4, 2, 1, 3, 1))
    # One command with wrapped text must outweigh several short commands.
    model["groups"][0]["commands"][0]["summary"] = "较长说明用于实际折行测量 " * 60
    backend = HelpPixieBackend()
    pages = backend.layout(model)
    assert len(pages) == 1
    page = pages[0]
    measured = [(module, index) for index, column in enumerate(page.columns) for module in column.modules]
    # Replay longest-first assignment, with stable catalog order for ties.
    catalog_order = {f"module{index} · 模块说明": index for index in range(len(model["groups"]))}
    measured.sort(key=lambda pair: (-pair[0].height, catalog_order[pair[0].category]))
    heights = [0] * 3
    for module, actual_column in measured:
        assert actual_column == min(range(3), key=lambda index: heights[index])
        heights[actual_column] += module.height
        group = model["groups"][catalog_order[module.category]]
        assert [card.command_id for card in module.cards] == [command["id"] for command in group["commands"]]
    assert measured[0][0].category.startswith("module0 ")
    assert heights == [column.height for column in page.columns]
    assert page.height == HEADER_HEIGHT + FOOTER_HEIGHT + max(heights)
    image = backend.render_pages(model)[0]
    assert struct.unpack(">II", image.data[16:24]) == (page.width, page.height)


def test_multicolumn_continuations_preserve_every_command_and_heading():
    pytest.importorskip("pixie")
    model = layout_model((50, 3, 2, 1))
    model["groups"][0]["summary"] = "多行模块标题 " * 20
    pages = HelpPixieBackend().layout(model)
    assert len(pages) > 1
    ids = []
    continuation = []
    for page in pages:
        assert len(page.columns) == 3 and page.height <= MAX_HEIGHT
        for column in page.columns:
            assert column.height <= MAX_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT
            for module in column.modules:
                ids.extend(card.command_id for card in module.cards)
                assert module.cards and module.heading.height > 0
                if module.category.startswith("module0 "):
                    continuation.append(module)
    assert len(continuation) > 1
    assert len(ids) == len(set(ids)) == model["count"]
    assert set(ids) == command_ids(model)
    assert [card.command_id for module in continuation for card in module.cards] == [
        command["id"] for command in model["groups"][0]["commands"]
    ]


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
            result = await services.help.reply(context, "text")
        assert isinstance(result, str) and "/help" in result and "/maintenance.status" not in result
    finally:
        await services.close()
