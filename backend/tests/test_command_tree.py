import pytest

from framework.catalog import CommandCatalog, CommandDirectory, CommandSpec, MatchMode
from framework.contracts import Role, SceneKind, SceneRef

from .conftest import access

SCENE = SceneRef("qq", "bot", SceneKind.GROUP, "group")


def ping_spec(**kwargs):
    return CommandSpec("core.ping", "maintenance", "ping", "连通检查", Role.EVERYONE, **kwargs)


def test_short_command_requires_explicit_alias():
    canonical = ping_spec()
    assert canonical.path == ("maintenance", "ping")
    aliased = ping_spec(aliases=("ping",))
    assert canonical.invocation_names() == ("maintenance.ping",)
    assert aliased.invocation_names() == ("maintenance.ping", "ping")
    assert ping_spec(examples=("/ping",)).to_dict()["examples"] == ["/maintenance.ping"]
    assert ping_spec(examples=("/maintenance.ping",)).to_dict(":")["examples"] == ["/maintenance:ping"]


async def test_tree_queries_aliases_and_examples_share_canonical_path():
    catalog = CommandCatalog()
    catalog.register_directory(CommandDirectory(("maintenance",), "维护与诊断", aliases=("maintainence",)))
    catalog.register(ping_spec(aliases=("ping",), usage="/ping [文字]", examples=("/ping hello",)))
    root = await catalog.help_text(access())
    assert "maintenance — 维护与诊断" in root and "/maintenance.ping" in root
    module = await catalog.help_text(access(), "maintenance")
    detail = await catalog.help_text(access(), "maintenance.ping")
    assert "用法：/maintenance.ping [文字]" in detail
    assert "示例：/maintenance.ping hello" in detail and "别名：/ping" in detail
    assert detail == await catalog.help_text(access(), "/PING")
    assert module == await catalog.help_text(access(), "maintainence")
    assert detail == await catalog.help_text(access(), "maintainence.ping")
    assert "没有找到" in await catalog.help_text(access(), "maintenance.missing")


async def test_nested_directories_include_all_executable_descendants():
    catalog = CommandCatalog()
    catalog.register_directory(CommandDirectory(("contest",), "比赛"))
    catalog.register_directory(CommandDirectory(("contest", "cf"), "Codeforces"))
    spec = CommandSpec(
        "contest.list", "contest", "list", "近期比赛", Role.EVERYONE, directory=("contest", "cf")
    )
    catalog.register(spec)
    root = await catalog.help_text(access())
    assert "contest.cf — Codeforces" in root and "/contest.cf.list" in root
    module = await catalog.help_text(access(), "contest")
    assert "contest.cf — Codeforces" in module and "/contest.cf.list" in module
    submodule = await catalog.help_text(access(), "contest.cf")
    assert "/contest.cf.list" in submodule


async def test_denied_private_hidden_and_wrong_scene_directories_do_not_leak():
    catalog = CommandCatalog()
    for spec in (
        ping_spec(),
        CommandSpec("denied", "denied", "show", "拒绝", Role.SUPERUSER),
        CommandSpec("private", "private", "show", "私有", Role.SUPERUSER),
        CommandSpec("hidden", "hidden", "show", "隐藏", Role.EVERYONE, hidden=True),
        CommandSpec(
            "scene", "private_scene", "show", "私聊", Role.EVERYONE, scopes=frozenset({SceneKind.C2C})
        ),
    ):
        catalog.register(spec)
    visible = await catalog.visible(access())
    assert [item["path"] for item in catalog.directory_index(visible)] == [["maintenance"]]
    for query in ("denied", "private", "private_scene"):
        assert "没有找到" in await catalog.help_text(access(), query)
    assert "/hidden.show" in await catalog.help_text(access())
    assert "/hidden.show" in await catalog.help_text(access(), "hidden.show")
    assert len(catalog.directory_index(await catalog.visible(access(Role.SUPERUSER)))) == 4


@pytest.mark.parametrize("separator", [":", "::", "-"])
async def test_system_separator_controls_matching_help_and_dashboard(separator):
    catalog = CommandCatalog()
    catalog.configure_separator(separator)
    spec = ping_spec(aliases=("ping",), examples=("/ping hi",))
    catalog.register(spec)
    name = f"maintenance{separator}ping"
    detail = await catalog.help_text(access(), name)
    assert f"示例：/{name} hi" in detail
    description = spec.to_dict(catalog.separator)
    assert description["name"] == name and description["directory"] == ["maintenance"]
    assert description["aliases"] == ["ping"] and description["examples"] == [f"/{name} hi"]


def test_same_local_name_is_allowed_across_modules_but_alias_is_global():
    catalog = CommandCatalog()
    catalog.register(ping_spec(aliases=("ping",)))
    catalog.register(CommandSpec("other.ping", "other", "ping", "其他检查", Role.EVERYONE))
    with pytest.raises(ValueError, match="alias"):
        catalog.register(
            CommandSpec("third.ping", "third", "ping", "第三检查", Role.EVERYONE, aliases=("ping",))
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_leaf_directory_collision_is_rejected_in_either_order(reverse):
    catalog = CommandCatalog()
    specs = [ping_spec(), CommandSpec("nested", "maintenance", "ping.child", "子项", Role.EVERYONE)]
    if reverse:
        specs.reverse()
    catalog.register(specs[0])
    with pytest.raises(ValueError, match="directory"):
        catalog.register(specs[1])


def test_directory_alias_cannot_shadow_module_or_command():
    catalog = CommandCatalog()
    catalog.register(ping_spec(aliases=("ping",)))
    with pytest.raises(ValueError):
        catalog.register_directory(CommandDirectory(("other",), "其他", aliases=("maintenance",)))
    with pytest.raises(ValueError):
        catalog.register_directory(CommandDirectory(("ping",), "冲突"))
    catalog.register_directory(CommandDirectory(("maintenance",), "维护", aliases=("maintainence",)))
    with pytest.raises(ValueError):
        catalog.register(CommandSpec("typo", "maintainence", "ping", "错误目录", Role.EVERYONE))


def test_separator_and_catalog_freezing_fail_fast():
    catalog = CommandCatalog()
    with pytest.raises(ValueError):
        catalog.configure_separator("")
    catalog.configure_separator(":")
    with pytest.raises(ValueError):
        catalog.register(ping_spec(aliases=("maintenance:ping",)))
    with pytest.raises(ValueError):
        catalog.register(CommandSpec("bad", "bad:module", "ping", "错误目录", Role.EVERYONE))
    catalog.register(ping_spec())
    with pytest.raises(RuntimeError):
        catalog.configure_separator(".")
    catalog.freeze()
    with pytest.raises(RuntimeError):
        catalog.register_directory(CommandDirectory(("other",), "其他"))


@pytest.mark.parametrize("mode,prefix,tail", [(MatchMode.PREFIX, "/", "猫猫"), (MatchMode.KEYWORD, "", "")])
def test_legacy_modes_expand_examples_without_changing_alias_matching(mode, prefix, tail):
    spec = CommandSpec(
        "animal.cat",
        "animal",
        "cat",
        "猫",
        Role.EVERYONE,
        aliases=("猫",),
        mode=mode,
        examples=(f"{prefix}猫{tail}",),
    )
    assert spec.to_dict()["examples"] == [f"{prefix}animal.cat{tail}"]


async def test_empty_visible_catalog_does_not_expose_registered_directories():
    catalog = CommandCatalog()
    catalog.register_directory(CommandDirectory(("secret",), "不可见目录"))
    assert "当前场景没有可用指令" in await catalog.help_text(access())
    assert "没有找到" in await catalog.help_text(access(), "secret")


@pytest.mark.parametrize("separator", [".", ":"])
async def test_example_alias_changes_examples_only_and_preserves_arguments(separator):
    catalog = CommandCatalog()
    catalog.configure_separator(separator)
    spec = ping_spec(
        aliases=("ping", "检查"),
        example_alias="检查",
        usage="/ping [文字]",
        examples=("/maintenance.ping 中文 /maintenance.ping", "/PING other", "/检查 中文"),
    )
    catalog.register(spec)
    detail = await catalog.help_text(access(), "ping")
    assert f"/{spec.qualified_name(separator)} —" in detail
    assert f"用法：/{spec.qualified_name(separator)} [文字]" in detail
    assert "示例：/检查 中文 /maintenance.ping；/检查 other；/检查 中文" in detail
    description = spec.to_dict(separator)
    assert description["name"] == f"maintenance{separator}ping"
    assert description["example_alias"] == "检查"
    assert description["examples"][0] == "/检查 中文 /maintenance.ping"
    assert detail == await catalog.help_text(access(), f"maintenance{separator}ping")


@pytest.mark.parametrize("alias", ["missing", "", "maintenance.ping"])
def test_example_alias_must_already_be_registered(alias):
    with pytest.raises(ValueError, match="Example alias"):
        ping_spec(aliases=("ping",), example_alias=alias)


@pytest.mark.parametrize(
    "mode,prefix,tail",
    [(MatchMode.COMMAND, "/", " 中文"), (MatchMode.PREFIX, "/", "猫猫"), (MatchMode.KEYWORD, "", "")],
)
def test_example_alias_preserves_legacy_modes_and_boundaries(mode, prefix, tail):
    spec = CommandSpec(
        "animal.cat", "animal", "cat", "猫", Role.EVERYONE, aliases=("猫",), example_alias="猫", mode=mode
    )
    example = spec.expand_example(f"{prefix}animal.cat{tail}")
    assert example == f"{prefix}猫{tail}"
    if mode == MatchMode.COMMAND:
        assert spec.expand_example("/catfish") == "/catfish"


def test_usage_without_arguments_remains_canonical_with_example_alias():
    spec = ping_spec(aliases=("ping",), example_alias="ping")
    assert spec.display_usage() == "/maintenance.ping"
    assert spec.expand_example("/maintenance.ping") == "/ping"
    assert ping_spec(aliases=("ping",)).to_dict()["example_alias"] is None


@pytest.mark.parametrize("reverse", [False, True])
def test_command_alias_cannot_shadow_a_directory(reverse):
    catalog = CommandCatalog()
    specs = [
        CommandSpec("leaf", "fun", "word", "一句话"),
        CommandSpec("shadow", "other", "cmd", "冲突", aliases=("fun",)),
    ]
    if reverse:
        specs.reverse()
    catalog.register(specs[0])
    with pytest.raises(ValueError, match="directory"):
        catalog.register(specs[1])
