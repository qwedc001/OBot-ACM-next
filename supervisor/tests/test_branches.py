"""Branch deployment uses an immutable stable code/data pair, including crash recovery."""

import asyncio
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_engine import read_value

from obot_supervisor.cli import control, control_parser
from obot_supervisor.config import validate_branch
from obot_supervisor.engine import Engine


def write_value(engine, value):
    with closing(sqlite3.connect(engine.config.data_dir / "bot.sqlite3")) as db, db:
        db.execute("update test set value = ?", (value,))


@pytest.fixture
def deployed(engine, monkeypatch):
    monkeypatch.setattr(engine, "fetch", AsyncMock(return_value="b" * 40))
    monkeypatch.setattr(engine, "prepare", AsyncMock(return_value="feature"))
    monkeypatch.setattr(engine, "command", AsyncMock())
    monkeypatch.setattr(engine, "boot", AsyncMock())
    return engine


async def test_repeated_tests_preserve_master_baseline_and_restore_data(deployed):
    engine = deployed
    engine.submit("test", branch="codex/feature")
    await engine.task
    baseline = dict(engine.state["baseline"])
    assert baseline["good"] == "old"
    assert baseline["branch"] == "master"
    assert engine.state["branch"] == "codex/feature"
    write_value(engine, "test data")
    engine.submit("test", branch="codex/second")
    await engine.task
    engine.submit("restart")
    await engine.task
    assert engine.state["baseline"] == baseline
    assert len(engine.state["baselines"]) == 1
    assert Engine(engine.config).state["baseline"] == baseline
    assert read_value(engine) == "test data"
    engine.submit("restore")
    await engine.task
    assert engine.state["branch"] == "master"
    assert engine.state["good"] == "old"
    assert "baseline" not in engine.state
    assert read_value(engine) == "original"
    # Restore keeps both the immutable stable backup and the last test data.
    with closing(sqlite3.connect(Path(engine.state["operation"]["snapshot"]) / "bot.sqlite3")) as db:
        assert db.execute("select value from test").fetchone()[0] == "test data"
    assert Path(baseline["snapshot"]).is_dir()
    engine.submit("test", branch="codex/third")
    await engine.task
    assert engine.state["baseline"]["snapshot"] != baseline["snapshot"]
    assert len(engine.state["baselines"]) == 2


async def test_second_test_failure_restores_master_not_previous_test(deployed):
    engine = deployed
    engine.submit("test", branch="codex/first")
    await engine.task
    write_value(engine, "first test")

    async def boot(name):
        if name == "feature":
            write_value(engine, "broken migration")
            raise RuntimeError("synthetic startup failure")
        assert name == "old" and read_value(engine) == "original"

    engine.boot.side_effect = boot
    engine.submit("test", branch="codex/second")
    await engine.task
    assert engine.state["operation"]["outcome"] == "rolled_back"
    assert engine.state["good"] == "old"
    assert engine.state["commit"] == "a" * 40
    assert engine.state["branch"] == "master"
    assert "baseline" not in engine.state


async def test_prepare_failure_does_not_stop_bot_or_change_baseline(deployed):
    engine = deployed
    engine.submit("test", branch="codex/feature")
    await engine.task
    baseline = dict(engine.state["baseline"])
    engine.fetch.side_effect = RuntimeError("missing branch")
    engine.stop = AsyncMock()
    engine.submit("test", branch="missing")
    await engine.task
    engine.stop.assert_not_awaited()
    assert engine.state["baseline"] == baseline
    assert engine.state["good"] == "feature"


async def test_stable_update_requires_merge_and_preserves_live_test_data(deployed):
    engine = deployed
    engine.submit("test", branch="codex/feature")
    await engine.task
    baseline = dict(engine.state["baseline"])
    write_value(engine, "test data")
    engine.git = AsyncMock(return_value="a" * 40)
    engine.stop = AsyncMock()
    engine.submit("update")
    await engine.task
    engine.stop.assert_not_awaited()
    assert engine.state["operation"]["outcome"] == "rejected"
    assert engine.state["baseline"] == baseline
    assert "Merge the tested commit" in engine.state["operation"]["log"]
    engine.git.return_value = "b" * 40
    engine.fetch.return_value = "c" * 40
    engine.prepare.return_value = "merged"
    engine.submit("update")
    await engine.task
    assert engine.state["operation"]["outcome"] == "success"
    assert engine.state["good"] == "merged" and engine.state["branch"] == "master"
    assert read_value(engine) == "test data"
    assert "baseline" not in engine.state
    assert engine.state["baselines"] == [baseline]


async def test_interrupted_test_recovers_stable_code_and_data(deployed):
    engine = deployed
    engine.submit("test", branch="codex/first")
    await engine.task
    entered = asyncio.Event()

    async def migrate(*args, **kwargs):
        write_value(engine, "partial migration")
        entered.set()
        await asyncio.Event().wait()

    engine.command.side_effect = migrate
    engine.submit("test", branch="codex/second")
    await entered.wait()
    engine.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await engine.task
    restarted = Engine(engine.config)
    await restarted.recover()
    assert read_value(restarted) == "original"
    assert restarted.state["good"] == "old"
    assert restarted.state["branch"] == "master"
    assert restarted.state["operation"]["outcome"] == "interrupted"


async def test_failed_restore_is_retried_on_supervisor_restart(deployed):
    engine = deployed
    engine.submit("test", branch="codex/first")
    await engine.task
    engine.boot.side_effect = RuntimeError("bad boot")
    engine.restore = lambda _: (_ for _ in ()).throw(OSError("disk unavailable"))
    engine.submit("test", branch="codex/second")
    await engine.task
    assert engine.state["operation"]["outcome"] == "recovery_failed"
    assert engine.state["recovery"]["branch"] == "master"
    with pytest.raises(ValueError, match="recovery is pending"):
        engine.submit("update")
    restarted = Engine(engine.config)
    await restarted.recover()
    assert restarted.state["good"] == "old" and read_value(restarted) == "original"
    assert "recovery" not in restarted.state


async def test_manual_restore_boot_failure_returns_to_test_snapshot(deployed):
    engine = deployed
    engine.submit("test", branch="codex/feature")
    await engine.task
    baseline = dict(engine.state["baseline"])
    write_value(engine, "test data")

    async def boot(name):
        if name == "old":
            raise RuntimeError("stable boot failed")

    engine.boot.side_effect = boot
    engine.submit("restore")
    await engine.task
    assert engine.state["branch"] == "codex/feature"
    assert read_value(engine) == "test data"
    assert engine.state["baseline"] == baseline
    assert engine.state["operation"]["outcome"] == "rolled_back"


@pytest.mark.parametrize(
    "branch",
    [
        None,
        "",
        "--help",
        "../master",
        "a//b",
        "a.lock",
        "a/.b",
        "a.",
        "master:other",
        "HEAD",
        "a~1",
        "a b",
        "a\n",
        "a;id",
        "a\\b",
    ],
)
def test_invalid_branch_rejected_before_scheduling(engine, branch):
    with pytest.raises(ValueError, match="branch"):
        engine.submit("test", branch=branch)
    assert engine.task is None
    assert "operation" not in engine.state


def test_only_feature_branches_can_be_tested_and_restore_requires_backup(engine):
    assert validate_branch("codex/issue-123.fix") == "codex/issue-123.fix"
    with pytest.raises(ValueError, match="stable branch"):
        engine.submit("test", branch="master")
    with pytest.raises(ValueError, match="baseline"):
        engine.submit("restore")
    with pytest.raises(ValueError, match="Invalid"):
        engine.submit("restart", branch="codex/feature")


async def test_real_remote_branch_fetch_is_exact_and_missing_branch_does_not_reuse_old_ref(engine):
    remote = Path(engine.config.repository)
    remote.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(remote), *args], text=True).strip()

    git("init", "-b", "master")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "stable",
    )
    stable = git("rev-parse", "HEAD")
    git("switch", "-c", "codex/feature")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "feature",
    )
    feature = git("rev-parse", "HEAD")
    assert await engine.fetch("codex/feature") == feature
    assert await engine.fetch() == stable
    with pytest.raises(RuntimeError):
        await engine.fetch("missing")


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (["update"], {"action": "update"}),
        (["update", "test", "codex/feature"], {"action": "test", "branch": "codex/feature"}),
        (["update", "restore"], {"action": "restore"}),
    ],
)
async def test_cli_dispatch(engine, monkeypatch, arguments, expected):
    from obot_supervisor import cli

    request = AsyncMock(return_value="operation")
    monkeypatch.setattr(cli, "request", request)
    await control(engine.config, control_parser().parse_args(arguments))
    request.assert_awaited_once_with(engine.config.socket, expected, timeout=60)


def test_cli_wait_supported_before_and_after_test_mode():
    for args in (
        ["update", "--wait", "test", "codex/feature"],
        ["update", "test", "codex/feature", "--wait"],
        ["update", "restore", "--wait"],
    ):
        assert control_parser().parse_args(args).wait
