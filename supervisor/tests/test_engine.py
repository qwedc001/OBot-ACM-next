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


async def test_prepare_preserves_optional_dependencies_without_symlink_privilege(engine, monkeypatch):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    engine.config = replace(engine.config, extras=("render", "ocr"))
    calls = AsyncMock()
    monkeypatch.setattr(engine, "git", calls)
    monkeypatch.setattr(engine, "command", calls)
    monkeypatch.setattr(Path, "symlink_to", lambda *args, **kwargs: None)
    await engine.prepare("b" * 40, "extras-test")
    sync_call = next(call for call in calls.await_args_list if "sync" in call.args)
    assert sync_call.args == (engine.config.uv, "sync", "--locked", "--no-dev", "--extra", "render", "--extra", "ocr")
    assert sync_call.kwargs["env"]["UV_CACHE_DIR"] == str(engine.config.state_dir / "cache" / "uv")
    assert sync_call.kwargs["env"]["UV_LINK_MODE"] == "hardlink"


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


@pytest.mark.parametrize("extras", [("render",), ("render", "ocr")])
async def test_real_git_fetch_and_detached_release(engine, monkeypatch, extras):
    from dataclasses import replace

    engine.config = replace(engine.config, extras=extras)
    remote = Path(engine.config.repository)
    remote.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(remote), *args], text=True).strip()

    git("init", "-b", "master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (remote / "README.md").write_text("test")
    (remote / ".gitignore").write_text(
        (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8"
    )
    git("add", ".")
    git("commit", "-m", "initial")
    commit = await engine.fetch()
    assert commit == git("rev-parse", "HEAD")
    original = engine.command

    calls = []

    async def command(*args, **kwargs):
        calls.append(args)
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
    sync = next(args for args in calls if "sync" in args)
    assert sync == (
        engine.config.uv,
        "sync",
        "--locked",
        "--no-dev",
        *(argument for extra in extras for argument in ("--extra", extra)),
    )
    assert (engine.release(name) / "README.md").read_text() == "test"
    assert (engine.release(name) / "data").resolve() == engine.config.data_dir
    assert not (engine.release(name) / ".env.dev").read_text()
    status = await original("git", "-C", engine.release(name), "status", "--porcelain", capture=True)
    assert status == "", "Runtime mounts must remain ignored, including directory symlinks"


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


async def test_prune_releases_rotates_excess_history_and_preserves_running_and_baseline(engine):
    releases_dir = engine.config.state_dir / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = engine.config.state_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # Create 5 historical releases: rel1, rel2, rel3, rel4, rel5
    for name in ["rel1", "rel2", "rel3", "rel4", "rel5"]:
        (releases_dir / name).mkdir()
        (snapshots_dir / name).mkdir()

    engine.state["history"] = ["rel1", "rel2", "rel3", "rel4", "rel5"]
    engine.state["snapshot_history"] = ["rel1", "rel2", "rel3", "rel4", "rel5"]
    engine.state["good"] = "rel5"  # rel5 is current good
    engine.state["baseline"] = {"good": "rel2", "snapshot": str(snapshots_dir / "rel2")}

    # Default max_history_releases is 2.
    # Total releases: rel5 (good), plus history: rel1, rel2, rel3, rel4.
    # Historical count = 4 > 2, so 2 must be pruned.
    # Candidates for pruning in order: rel1, rel2 (protected by baseline!), rel3, rel4.
    # rel1 is pruned, rel2 skipped because it's protected, rel3 is pruned.
    # Left over: rel2, rel4 in history, and rel5 as current good.
    await engine.prune_releases()

    assert (releases_dir / "rel5").is_dir()
    assert (releases_dir / "rel2").is_dir()
    assert (releases_dir / "rel4").is_dir()
    assert not (releases_dir / "rel1").exists()
    assert not (releases_dir / "rel3").exists()

    # Total remaining on disk: 3 (rel5 current, rel2 and rel4 history)
    remaining = [p.name for p in releases_dir.iterdir() if p.is_dir()]
    assert set(remaining) == {"rel5", "rel2", "rel4"}
    assert len(remaining) == 3

    # Check snapshots: snapshots are only protected if they are in baseline/recovery/rollback/operation
    # rel2 is protected by baseline, rel5 is the newest snapshot (limit 2). rel1, rel3, rel4 are pruned.
    assert (snapshots_dir / "rel2").is_dir()
    assert (snapshots_dir / "rel5").is_dir()
    assert not (snapshots_dir / "rel1").exists()
    assert not (snapshots_dir / "rel3").exists()
    assert not (snapshots_dir / "rel4").exists()


async def test_update_workflow_automatically_prunes_old_releases(engine, monkeypatch):
    entered = 0

    async def fetch(*args):
        nonlocal entered
        entered += 1
        return f"{entered}" * 40

    async def prepare(commit, op_id):
        name = f"rel-{commit[:6]}"
        path = engine.release(name)
        path.mkdir(parents=True, exist_ok=True)
        return name

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(engine, "fetch", fetch)
    monkeypatch.setattr(engine, "prepare", prepare)
    monkeypatch.setattr(engine, "command", noop)
    monkeypatch.setattr(engine, "boot", noop)

    # Initial good is 'old'
    (engine.config.state_dir / "releases" / "old").mkdir(parents=True, exist_ok=True)
    engine.state["history"] = ["old"]

    # 1st update -> good = rel-111111 (history: old)
    engine.submit("update")
    await engine.task
    assert engine.state["good"] == "rel-111111"

    # 2nd update -> good = rel-222222 (history: old, rel-111111)
    engine.submit("update")
    await engine.task
    assert engine.state["good"] == "rel-222222"

    # 3rd update -> good = rel-333333 (history: old, rel-111111, rel-222222)
    # Exceeds max_history_releases (2)! "old" should be pruned.
    engine.submit("update")
    await engine.task
    assert engine.state["good"] == "rel-333333"

    releases = [p.name for p in (engine.config.state_dir / "releases").iterdir() if p.is_dir()]
    assert set(releases) == {"rel-333333", "rel-222222", "rel-111111"}
    assert not (engine.config.state_dir / "releases" / "old").exists()
    assert len(releases) == 3


async def test_prepare_uses_shared_cache_and_pnpm_store(engine, monkeypatch):
    calls = []

    async def fake_command(*args, cwd=None, env=None, capture=False):
        calls.append({"args": args, "cwd": cwd, "env": env})
        return "ok"

    async def fake_git(*args, **kwargs):
        pass

    monkeypatch.setattr(engine, "command", fake_command)
    monkeypatch.setattr(engine, "git", fake_git)
    monkeypatch.setattr(Path, "symlink_to", lambda *a, **k: None)

    release_path = engine.release("commit123456-op1234567890")
    # Simulate presence of frontend directory
    (release_path / "frontend").mkdir(parents=True, exist_ok=True)

    await engine.prepare("commit1234567890abcdef", "op1234567890abcdef")

    cache_dir = engine.config.state_dir / "cache"
    uv_cache = cache_dir / "uv"
    pnpm_store = cache_dir / "pnpm"
    assert uv_cache.is_dir()
    assert pnpm_store.is_dir()

    uv_call = next(c for c in calls if "sync" in c["args"])
    assert uv_call["env"]["UV_CACHE_DIR"] == str(uv_cache)
    assert uv_call["env"]["UV_LINK_MODE"] == "hardlink"

    pnpm_call = next(c for c in calls if "install" in c["args"])
    assert "--store-dir" in pnpm_call["args"]
    store_dir_idx = pnpm_call["args"].index("--store-dir")
    assert pnpm_call["args"][store_dir_idx + 1] == str(pnpm_store)


