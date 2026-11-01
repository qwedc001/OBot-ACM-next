"""Offline render-only benchmark: python -m tests.benchmark_rendering --workers 1 4 8.

Real renderers, synthetic public data, private temporary output, no bot/network/DB.
Reports end-to-end queue/spawn/draw/PNG-transfer time, sampled child RSS and loop lag.
This is a manual benchmark, not a timing-sensitive CI assertion.
"""

import argparse
import asyncio
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import psutil

from framework.catalog import CommandSpec
from framework.clients.jobs import JobQueue
from framework.clients.renderer import RenderClient, RenderedImage
from framework.contests import Contest
from framework.contracts import Actor
from framework.integration import processes
from framework.views.contests import ContestBackend
from framework.views.help import HelpPixieBackend
from framework.views.profile import ProfilePixieBackend


def workloads():
    groups = []
    for name, count in (("common", 6), ("cp", 6), ("fun", 1), ("fun.pickone", 12), ("maintenance", 4)):
        commands = [
            CommandSpec(
                f"{name}.c{i}",
                name,
                f"c{i}",
                "查询当前可用内容，默认返回图片",
                usage=f"/c{i} <名称> [参数]",
                aliases=(f"{name}{i}",),
                example_alias=f"{name}{i}",
                examples=(f"/c{i} example", f"/c{i} other"),
                directory=tuple(name.split(".")),
            ).to_dict()
            for i in range(count)
        ]
        groups.append({"path": name.split("."), "name": name, "summary": "功能模块", "commands": commands})
    help_model = {"title": "帮助", "count": 29, "found": True, "groups": groups}
    now = 1792335600
    contests = [
        asdict(
            Contest(
                f"cf:{i}",
                "cf",
                "Codeforces",
                f"CF{i}",
                f"示例算法竞赛 Example Round {i}",
                now + i * 86400,
                7200,
                "CF 赛制",
                "https://example.com/contest",
            )
        )
        for i in range(16)
    ]
    listing = {"title": "近日算法竞赛", "now": now, "items": contests, "warnings": []}
    detail = {**listing, "detail": True, "items": contests[:1]}
    profile = {
        "platform": "Codeforces",
        "handle": "Example",
        "rank": "Master",
        "rating": 2200,
        "details": ["示例学校"],
        "metrics": [("通过题目", "1234"), ("最近提交", "10")],
        "sections": [{"title": "最近提交", "lines": ["Example problem · AC"] * 3}],
    }
    return [("help", help_model), ("contests", listing), ("contests", detail), ("profile", profile)]


def rss(pids):
    processes = {}
    for pid in pids:
        try:
            parent = psutil.Process(pid)
            for process in [parent, *parent.children(recursive=True)]:
                processes[process.pid] = process
        except psutil.Error:
            pass
    total = 0
    for process in processes.values():
        try:
            total += process.memory_info().rss
        except psutil.Error:
            pass
    return total


class Sink:
    max_bytes = 16 * 1024 * 1024

    async def create(self, actor, data, mime, **metadata):
        return RenderedImage(data, mime, **metadata)


class ThreadRenderer(RenderClient):
    """Experiment only: no draw lock, same shared backend instances as the old client.

    Run in a disposable interpreter: native drawing libraries may not be thread safe.
    This does not change the production renderer or its cancellation guarantees.
    """

    async def _images(self, view, model, multiple):
        backend = self._views[view]

        def draw():
            def execute():
                return (
                    backend.render_pages(model)
                    if multiple and hasattr(backend, "render_pages")
                    else (backend.render(model),)
                )

            if self.draw_lock:
                with self.draw_lock:
                    return execute()
            return execute()

        return await asyncio.get_running_loop().run_in_executor(self.executor, draw)


async def benchmark(workers, count, all_help, mode="process"):
    queue = JobQueue(workers, max(32, count), timeout=120)
    renderer = (RenderClient if mode == "process" else ThreadRenderer)(queue, Sink())
    executor = ThreadPoolExecutor(max_workers=workers) if mode != "process" else None
    if executor:
        renderer.executor = executor
        renderer.draw_lock = threading.Lock() if mode == "thread-locked" else None
    for key, backend in (
        ("help", HelpPixieBackend()),
        ("contests", ContestBackend()),
        ("profile", ProfilePixieBackend()),
    ):
        renderer.register(key, backend)
    children, lag, durations = [], [], []
    peak, active_peak = 0, 0
    create = processes.asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        child = await create(*args, **kwargs)
        children.append(child)
        return child

    async def monitor():
        nonlocal peak, active_peak
        while True:
            active = [child.pid for child in children if child.returncode is None]
            active_peak = max(active_peak, len(active))
            peak = max(peak, await asyncio.to_thread(rss, active))
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            lag.append(max(0, time.perf_counter() - before - 0.01))

    cases = workloads()

    async def render(index):
        kind, model = cases[0 if all_help else index % len(cases)]
        started = time.perf_counter()
        images = await renderer.render_pages(Actor("benchmark", "local"), kind, model)
        durations.append(time.perf_counter() - started)
        assert all(image.data.startswith(b"\x89PNG\r\n\x1a\n") for image in images)
        return [(image.width, image.height) for image in images]

    processes.asyncio.create_subprocess_exec = tracked
    await queue.start()
    sampling = asyncio.create_task(monitor())
    await asyncio.sleep(0)
    started = time.perf_counter()
    try:
        sizes = await asyncio.gather(*(render(index) for index in range(count)))
        elapsed = time.perf_counter() - started
        return {
            "mode": mode,
            "workers": workers,
            "tasks": count,
            "workload": "help" if all_help else "mixed",
            "wall_seconds": round(elapsed, 3),
            "mean_reply_seconds": round(statistics.mean(durations), 3),
            "tasks_per_second": round(count / elapsed, 3),
            "max_child_processes": active_peak,
            "sampled_child_peak_mib": round(peak / 1024**2, 1),
            "loop_lag_max_ms": round(max(lag, default=0) * 1000, 1),
            "sizes": sizes,
        }
    finally:
        await renderer.close()
        await queue.close()
        sampling.cancel()
        await asyncio.gather(sampling, return_exceptions=True)
        processes.asyncio.create_subprocess_exec = create
        if executor:
            executor.shutdown(wait=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--all-help", action="store_true")
    parser.add_argument("--mode", choices=("process", "thread", "thread-locked"), default="process")
    args = parser.parse_args()
    for workers in args.workers:
        print(json.dumps(await benchmark(workers, args.count, args.all_help, args.mode)), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
