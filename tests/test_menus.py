import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.config import BotInfo
from nonebot.drivers import URL, Response

from framework.catalog import CommandCatalog, CommandDirectory, CommandSpec
from framework.clients.qq_menu import QQMenuClient
from framework.contracts import Role, SceneKind
from framework.integration.platforms.qq_menu import QQMenuError
from framework.services.menus import MenuService


def catalogue(count=1):
    catalog = CommandCatalog()
    catalog.register_directory(CommandDirectory(("fun",), "趣味工具"))
    catalog.register(CommandSpec("help", "help", "help", "完整帮助", directory=(), menu_label="查看帮助"))
    for index in range(count):
        catalog.register(
            CommandSpec(
                f"fun.word{index}",
                "fun",
                f"word{index}",
                "获取一句话",
                aliases=(f"word{index}",),
                example_alias=f"word{index}",
                menu_label="随机一言",
            )
        )
    catalog.register(
        CommandSpec(
            "admin.status",
            "maintenance",
            "status",
            "查看状态",
            permission=Role.SUPERUSER,
            menu_label="运行状态",
        )
    )
    catalog.register(CommandSpec("hidden", "private", "hidden", "隐蔽功能", hidden=True))
    catalog.register(CommandSpec("group", "group", "manage", "群管理", scopes=frozenset({SceneKind.GROUP})))
    return catalog


def test_global_menu_is_public_two_levels_with_aliases_and_help():
    service = MenuService(catalogue(), QQMenuClient())
    menu = service.preview()
    assert {root["name"] for root in menu["items"]} == {"通用功能", "趣味工具"}
    commands = [child["send_message"] for root in menu["items"] for child in root["sub_menu_items"]]
    assert "/word0" in commands and "/help text fun" in commands
    assert not any("status" in command or "hidden" in command or "manage" in command for command in commands)
    assert all(child["type"] == "send_message" for root in menu["items"] for child in root["sub_menu_items"])
    admin = service.preview(include_admin=True)
    status = [
        child
        for root in admin["items"]
        for child in root["sub_menu_items"]
        if child["send_message"] == "/maintenance.status"
    ]
    assert status and "超管" in status[0]["name"]


def test_limits_split_large_modules_without_omitting_commands():
    service = MenuService(catalogue(17), QQMenuClient())
    menu = service.preview(command_start="!")
    names = [root["name"] for root in menu["items"]]
    assert len(names) == len(set(names))
    commands = []
    for root in menu["items"]:
        assert sum(1 if c.isascii() else 2 for c in root["name"]) <= 10
        assert len(root["sub_menu_items"]) <= 5
        child_names = [child["name"] for child in root["sub_menu_items"]]
        assert len(child_names) == len(set(child_names))
        for child in root["sub_menu_items"]:
            assert sum(1 if c.isascii() else 2 for c in child["name"]) <= 14
            commands.append(child["send_message"])
    assert all(f"!word{i}" in commands for i in range(17))
    with pytest.raises(ValueError, match="ten root"):
        MenuService(catalogue(41), QQMenuClient()).preview()


def test_menu_sends_only_command_names_never_runtime_examples():
    commands = CommandCatalog()
    commands.configure_separator(":")
    commands.register(
        CommandSpec(
            "lookup",
            "contest",
            "lookup",
            "查询",
            aliases=("lookup",),
            example_alias="lookup",
            examples=("/lookup 1234",),
        )
    )
    commands.register(CommandSpec("create", "contest", "create", "创建", examples=("/contest.create 示例",)))
    children = MenuService(commands, QQMenuClient()).preview(command_start="!")["items"][0]["sub_menu_items"]
    assert [item["send_message"] for item in children] == ["!contest:create", "!lookup", "!help text contest"]


def make_bot(responses):
    adapter = SimpleNamespace(
        get_api_base=lambda: URL("https://api.bot.qq.com/"),
        config=nonebot.get_driver().config,
        request=AsyncMock(side_effect=responses),
    )
    bot = Bot(adapter, "app", BotInfo(id="app", secret="synthetic"))
    bot.get_authorization_header = AsyncMock(return_value={"Authorization": "synthetic"})
    return bot, adapter


async def test_native_sdk_auth_get_put_readback_and_snapshot(tmp_path):
    client = QQMenuClient()
    service = MenuService(catalogue(), client)
    desired = service.preview()
    before = {"menu": {}}
    bot, adapter = make_bot(
        [
            Response(200, content=json.dumps(before)),
            Response(200, content='{"version": 1}'),
            Response(
                200,
                content=json.dumps(
                    {
                        "version": 1,
                        "menu": {
                            "items": [
                                {**item, "icon": "https://example.test/default.png"}
                                for item in desired["items"]
                            ]
                        },
                    }
                ),
            ),
        ]
    )
    backup = tmp_path / "snapshot.json"
    assert await service.sync(bot, backup=backup) == {"changed": True, "verified": True, "version": 1}
    assert json.loads(backup.read_text()) == before
    requests = [call.args[0] for call in adapter.request.await_args_list]
    assert [request.method for request in requests] == ["GET", "PUT", "GET"]
    assert all(str(request.url) == "https://api.bot.qq.com/v2/menu" for request in requests)
    assert requests[1].json == {"menu": desired}
    assert all(request.headers["Authorization"] == "synthetic" for request in requests)
    assert bot.get_authorization_header.await_count == 3


async def test_unchanged_menu_is_not_written_or_backed_up(tmp_path):
    service = MenuService(catalogue(), QQMenuClient())
    bot, adapter = make_bot([Response(200, content=json.dumps({"version": 3, "menu": service.preview()}))])
    backup = tmp_path / "unchanged.json"
    assert await service.sync(bot, backup=backup) == {"changed": False, "verified": True, "version": 3}
    assert adapter.request.await_count == 1 and not backup.exists()


async def test_qq_default_icons_do_not_trigger_repeated_updates(tmp_path):
    service = MenuService(catalogue(), QQMenuClient())
    menu = service.preview()
    menu["items"][0]["icon"] = "https://example.test/default.png"
    bot, adapter = make_bot([Response(200, content=json.dumps({"version": 1, "menu": menu}))])
    assert await service.sync(bot, backup=tmp_path / "unused.json") == {
        "changed": False,
        "verified": True,
        "version": 1,
    }
    assert adapter.request.await_count == 1


async def test_backup_failure_prevents_modification(tmp_path):
    service = MenuService(catalogue(), QQMenuClient())
    bot, adapter = make_bot([Response(200, content='{"menu": {}}')])
    backup = tmp_path / "exists.json"
    backup.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        await service.sync(bot, backup=backup)
    assert adapter.request.await_count == 1 and backup.read_text() == "keep"


@pytest.mark.parametrize(
    "response",
    [
        Response(403, content='{"code": 40030020, "message": "private response"}'),
        Response(404, content="missing"),
        Response(200, content='{"unexpected": "private response"}'),
    ],
)
async def test_api_failure_is_explicit_and_redacted(response):
    bot, _ = make_bot([response])
    with pytest.raises(QQMenuError) as error:
        await QQMenuClient().get(bot)
    assert "private" not in str(error.value)
    if response.status_code == 403:
        assert error.value.status == 403 and error.value.code == 40030020


async def test_success_status_with_no_update_version_is_not_success():
    bot, _ = make_bot([Response(200, content="{}")])
    with pytest.raises(QQMenuError):
        await QQMenuClient().put(bot, {"items": []})


async def test_readback_mismatch_keeps_snapshot_for_recovery(tmp_path):
    service = MenuService(catalogue(), QQMenuClient())
    bot, _ = make_bot(
        [
            Response(200, content='{"menu": {}}'),
            Response(200, content='{"version": 1}'),
            Response(200, content='{"menu": {}}'),
        ]
    )
    with pytest.raises(RuntimeError, match="readback differs"):
        await service.sync(bot, backup=tmp_path / "snapshot.json")
    assert (tmp_path / "snapshot.json").exists()
