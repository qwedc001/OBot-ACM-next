import pytest

from framework.catalog import CommandCatalog, CommandSpec, MatchMode
from framework.contracts import Role

from .conftest import access


def test_alias_conflicts_and_prefix_shadowing_fail_at_registration():
    catalog = CommandCatalog()
    catalog.register(CommandSpec("one", "one", "help", "说明", Role.EVERYONE, aliases=("help", "帮助")))
    with pytest.raises(ValueError):
        catalog.register(CommandSpec("two", "two", "帮助", "说明", Role.EVERYONE, aliases=("帮助",)))
    with pytest.raises(ValueError):
        catalog.register(
            CommandSpec("three", "three", "he", "说明", Role.EVERYONE, mode=MatchMode.PREFIX, aliases=("he",))
        )
    catalog.freeze()
    with pytest.raises(RuntimeError):
        catalog.register(CommandSpec("four", "four", "next", "说明", Role.EVERYONE))


async def test_help_hides_unavailable_commands():
    catalog = CommandCatalog()
    catalog.register(
        CommandSpec("ping", "core", "ping", "检查连通", Role.EVERYONE, aliases=("ping",), examples=("/ping",))
    )
    catalog.register(CommandSpec("status", "core", "status", "状态", Role.SUPERUSER))
    catalog.register(CommandSpec("private", "board", "secret", "私有榜单", Role.SUPERUSER))
    help_text = await catalog.help_text(access())
    assert "core" in help_text and "/core.ping" in help_text and ("board" not in help_text)
    module = await catalog.help_text(access(), "core")
    assert "/core.ping" in module and "status" not in module and ("secret" not in module)
    detail = await catalog.help_text(access(), "ping")
    assert "示例：/core.ping" in detail
    assert "没有找到" in await catalog.help_text(access(), "secret")
