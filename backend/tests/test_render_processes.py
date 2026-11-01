import asyncio
import json
import os

import psutil
import pytest

from framework.catalog import CommandCatalog
from framework.config import Settings
from framework.integration import processes
from framework.runtime import Services

from .conftest import USER
from .render_backends import ProbeBackend


async def until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("workers", [4, 8])
async def test_isolated_render_jobs_really_overlap_and_bot_remains_responsive(tmp_path, database, workers):
    services = Services(
        Settings(obot_data_dir=tmp_path, obot_job_workers=workers), CommandCatalog(), session_factory=database
    )
    await services.start()
    services.clients.renderer.register("probe", ProbeBackend())
    gates = [tmp_path / f"started-{index}" for index in range(workers)]
    release = tmp_path / "release"
    draws = [
        asyncio.create_task(
            services.clients.renderer.render(
                USER,
                "probe",
                {
                    "started": str(path),
                    "release": str(release),
                    "width": index + 1,
                },
            )
        )
        for index, path in enumerate(gates)
    ]
    try:
        await until(lambda: all(path.exists() and path.stat().st_size for path in gates))
        pids = {json.loads(path.read_text())["pid"] for path in gates}
        assert len(pids) == workers and os.getpid() not in pids
        assert services.clients.jobs.running == workers
        assert all(not task.done() for task in draws)
        assert await asyncio.wait_for(services.maintenance.ping(), 0.5) == "pong"
        release.touch()
        artifacts = await asyncio.gather(*draws)
        assert [artifact.width for artifact in artifacts] == list(range(1, workers + 1))
        assert len({artifact.id for artifact in artifacts}) == workers
    finally:
        release.touch()
        await asyncio.gather(*draws, return_exceptions=True)
        await services.close()


@pytest.mark.parametrize("stop", ["timeout", "cancel", "close"])
async def test_stopped_draw_is_reaped_before_slot_reuse(tmp_path, database, monkeypatch, stop):
    children = []
    create = processes.asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(processes.asyncio, "create_subprocess_exec", tracked)
    monkeypatch.setenv("OBOT_TEST_SECRET", "must-not-be-in-child")
    services = Services(
        Settings(obot_data_dir=tmp_path, obot_job_workers=1), CommandCatalog(), session_factory=database
    )
    await services.start()
    services.clients.renderer.register("probe", ProbeBackend())
    gate = tmp_path / "started"
    if stop == "timeout":
        services.clients.jobs.timeout = 4
    drawing = asyncio.create_task(
        services.clients.renderer.render(
            USER,
            "probe",
            {
                "started": str(gate),
                "release": str(tmp_path / "never"),
            },
        )
    )
    try:
        await until(gate.exists)
        actual_worker = json.loads(gate.read_text())["pid"]
        if stop == "cancel":
            drawing.cancel()
        elif stop == "close":
            await services.clients.renderer.close()
        with pytest.raises(TimeoutError if stop == "timeout" else asyncio.CancelledError):
            await drawing
        await until(lambda: services.clients.jobs.running == 0)
        assert children[0].returncode is not None
        assert not psutil.pid_exists(actual_worker)
        if stop != "close":
            services.clients.jobs.timeout = 15
            pages = await services.clients.renderer.render_pages(USER, "probe", {})
            assert [page.width for page in pages] == [24, 36]
            assert all(child.returncode == 0 for child in children[1:])
    finally:
        await services.close()


@pytest.mark.parametrize("model,kind", [({"exit": True}, OSError), ({"error": True}, ValueError)])
async def test_failed_process_does_not_break_later_jobs_or_leak_data(tmp_path, database, model, kind):
    services = Services(Settings(obot_data_dir=tmp_path), CommandCatalog(), session_factory=database)
    await services.start()
    services.clients.renderer.register("probe", ProbeBackend())
    try:
        with pytest.raises(kind) as error:
            await services.clients.renderer.render(USER, "probe", model)
        assert "private-content" not in str(error.value)
        assert (await services.clients.renderer.render(USER, "probe", {})).width == 24
    finally:
        await services.close()


async def test_cancel_during_spawn_still_reaps_child(tmp_path, monkeypatch):
    created, release = asyncio.Event(), asyncio.Event()
    create = processes.asyncio.create_subprocess_exec
    children = []

    async def slow_spawn(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        created.set()
        await release.wait()
        return child

    monkeypatch.setattr(processes.asyncio, "create_subprocess_exec", slow_spawn)
    running = asyncio.create_task(
        processes.run_process(
            ["-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            environment=processes.process_environment(),
        )
    )
    try:
        await asyncio.wait_for(created.wait(), 5)
        running.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert children[0].returncode is not None
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
