"""Real Linux child/IPC/migration tests; never connect to QQ or production data."""

import os
import sys
from unittest.mock import AsyncMock

import pytest

from obot_supervisor.ipc import serve

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux process groups and Unix sockets")


def release(engine, name, *, broken=False, ready=True):
    root = engine.release(name)
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    (root / "backend").mkdir()
    (root / "backend/__init__.py").touch()
    (root / "framework").mkdir()
    (root / "framework/__init__.py").touch()
    (root / "framework/migrate.py").write_text("print('migration complete')")
    (root / "data").symlink_to(engine.config.data_dir)
    (root / "backend/bot.py").write_text(
        """
import asyncio, json, os, sqlite3
async def main():
    if BROKEN:
        with sqlite3.connect('data/bot.sqlite3') as db:
            db.execute("update test set value='broken'")
        print('synthetic startup failure', flush=True)
        raise SystemExit(3)
    if READY:
        r,w = await asyncio.open_unix_connection(os.environ['OBOT_SUPERVISOR_SOCKET'])
        w.write((json.dumps({'action':'ready','boot_id':os.environ['OBOT_SUPERVISOR_BOOT_ID']})+'\\n').encode())
        await w.drain()
        await r.readline()
        w.close()
        await w.wait_closed()
    await asyncio.sleep(600)
asyncio.run(main())
""".replace("BROKEN", repr(broken)).replace("READY", repr(ready))
    )


async def test_actual_failed_process_restores_and_restarts_old_process(engine, monkeypatch):
    from test_engine import read_value

    release(engine, "old")
    release(engine, "broken", broken=True)
    monkeypatch.setattr(engine, "fetch", AsyncMock(return_value="b" * 40))
    monkeypatch.setattr(engine, "prepare", AsyncMock(return_value="broken"))
    async with await serve(engine):
        try:
            await engine.start()
            old_pid = engine.child.pid
            engine.submit("update", {"adapter": "test", "kind": "private", "id": "synthetic"})
            await engine.task
            assert engine.child.pid != old_pid
            assert engine.child.returncode is None
            assert engine.state["operation"]["outcome"] == "rolled_back"
            assert "synthetic startup failure" in engine.state["operation"]["log"]
            assert read_value(engine) == "original"
            assert engine.state["good"] == "old"
        finally:
            await engine.close()


async def test_running_process_without_ready_is_not_healthy(engine):
    from dataclasses import replace

    engine.config = replace(engine.config, startup_timeout=0.5)
    release(engine, "unready", ready=False)
    try:
        with pytest.raises(RuntimeError, match="readiness timeout"):
            await engine.boot("unready")
    finally:
        await engine.close()


async def test_process_boot_success_switches_known_good(engine, monkeypatch):
    release(engine, "old")
    release(engine, "new")
    monkeypatch.setattr(engine, "fetch", AsyncMock(return_value="b" * 40))
    monkeypatch.setattr(engine, "prepare", AsyncMock(return_value="new"))
    async with await serve(engine):
        try:
            await engine.start()
            engine.submit("update")
            await engine.task
            assert engine.state["operation"]["outcome"] == "success"
            assert engine.state["good"] == "new"
            assert engine.child.returncode is None
        finally:
            await engine.close()
