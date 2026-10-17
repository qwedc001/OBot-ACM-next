import asyncio
import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from obot_supervisor.engine import Engine
from obot_supervisor.state import Store, Tail


def read_value(engine):
    with closing(sqlite3.connect(engine.config.data_dir / "bot.sqlite3")) as db:
        return db.execute("select value from test").fetchone()[0]


async def test_failed_start_restores_database_cache_and_release(engine, monkeypatch):
    boots = []

    async def fetch():
        return "b" * 40

    async def prepare(*args):
        return "candidate"

    async def command(*args, **kwargs):
        with closing(sqlite3.connect(engine.config.data_dir / "bot.sqlite3")) as db, db:
            db.execute("update test set value = 'migrated'")
        (engine.config.data_dir / "nonebot_plugin_orm" / "revision").write_text("new")
        (engine.config.data_dir / "created-by-new-code").write_text("new")

    async def boot(name):
        boots.append(name)
        if name == "candidate":
            for n in range(60):
                engine.tail.add(f"startup {n}")
            engine.tail.add("password=top-secret-value")
            raise RuntimeError("bad startup")
        assert read_value(engine) == "original"

    monkeypatch.setattr(engine, "fetch", fetch)
    monkeypatch.setattr(engine, "prepare", prepare)
    monkeypatch.setattr(engine, "command", command)
    monkeypatch.setattr(engine, "boot", boot)
    engine.submit("update", {"adapter": "test", "id": "user", "kind": "private"})
    await engine.task
    assert boots == ["candidate", "old"]
    assert engine.state["good"] == "old"
    assert engine.state["operation"]["outcome"] == "rolled_back"
    assert (engine.config.data_dir / "nonebot_plugin_orm" / "revision").read_text() == "old"
    assert not (engine.config.data_dir / "created-by-new-code").exists()
    report = engine.state["reports"][0]
    assert len(report["log"].splitlines()) == 50
    assert "top-secret-value" not in report["log"]


async def test_prepare_failure_keeps_running_release_untouched(engine, monkeypatch):
    async def fail():
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(engine, "fetch", fail)
    engine.submit("update")
    await engine.task
    assert engine.state["operation"]["outcome"] == "rejected"
    assert read_value(engine) == "original"
    assert engine.state["good"] == "old"


async def test_success_and_busy_rejection(engine, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch():
        entered.set()
        await release.wait()
        return "b" * 40

    async def prepare(*args):
        return "candidate"

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(engine, "fetch", fetch)
    monkeypatch.setattr(engine, "prepare", prepare)
    monkeypatch.setattr(engine, "command", noop)
    monkeypatch.setattr(engine, "boot", noop)
    engine.submit("update")
    await entered.wait()
    with pytest.raises(ValueError, match="Another"):
        engine.submit("restart")
    release.set()
    await engine.task
    assert engine.state["good"] == "candidate"
    assert Store(engine.config.state_dir).value["operation"]["outcome"] == "success"


async def test_interrupted_migration_is_restored_before_boot(engine):
    snapshot = engine.snapshot("interrupted")
    with closing(sqlite3.connect(engine.config.data_dir / "bot.sqlite3")) as db, db:
        db.execute("update test set value = 'damaged'")
    engine.state["operation"] = {"id": "test", "phase": "mutating", "snapshot": snapshot}
    engine.save()
    restarted = Engine(engine.config)
    await restarted.recover()
    assert read_value(restarted) == "original"
    assert restarted.state["operation"]["outcome"] == "interrupted"


def test_logs_are_bounded_redacted_and_safe_for_markdown():
    tail = Tail()
    for n in range(70):
        tail.add(f"line {n}")
    tail.add("https://bot.example.net/path?token=secret")
    tail.add("Authorization: Bearer secret")
    tail.add("password='dummy-password'")
    tail.add("``` unsafe")
    assert len(tail.text().splitlines()) == 50
    assert "https://" not in tail.text()
    assert "secret" not in tail.text()
    assert "dummy-password" not in tail.text()
    assert "```" not in tail.text()


async def test_real_git_fetch_and_detached_release(engine, monkeypatch):
    remote = Path(engine.config.repository)
    remote.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(remote), *args], text=True).strip()

    git("init", "-b", "master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (remote / "README.md").write_text("test")
    git("add", ".")
    git("commit", "-m", "initial")
    commit = await engine.fetch()
    assert commit == git("rev-parse", "HEAD")
    original = engine.command

    async def command(*args, **kwargs):
        if str(args[0]) == "git":
            return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "command", command)
    if os.name == "nt":
        try:
            probe = engine.config.state_dir / "probe-link"
            probe.symlink_to(engine.config.env_file)
            probe.unlink()
        except OSError:
            pytest.skip("Windows symlink privilege unavailable")
    name = await engine.prepare(commit, "integration")
    assert (engine.release(name) / "README.md").read_text() == "test"
    assert (engine.release(name) / "data").resolve() == engine.config.data_dir
    assert not (engine.release(name) / ".env.dev").read_text()


@pytest.mark.skipif(os.name != "posix", reason="Production supervisor uses Linux Unix sockets")
async def test_private_ipc_ready_generation_and_invalid_action(engine):
    from obot_supervisor.ipc import request, serve

    engine.generation = "current"
    async with await serve(engine):
        await request(engine.config.socket, {"action": "ready", "boot_id": "stale"})
        assert not engine.ready.is_set()
        await request(engine.config.socket, {"action": "ready", "boot_id": "current"})
        assert engine.ready.is_set()
        assert engine.config.socket.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match="Unknown"):
            await request(engine.config.socket, {"action": "shell", "command": "anything"})


def test_state_is_valid_json_and_path_escape_rejected(engine):
    assert json.loads(engine.store.path.read_text())["good"] == "old"
    with pytest.raises(ValueError, match="Invalid release"):
        engine.release("../../outside")
