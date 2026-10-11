"""Bounded async work; serial keys never occupy workers while waiting."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .base import Client


class QueueFull(Exception):
    pass


class QueueClosed(Exception):
    pass


@dataclass
class _Job:
    name: str
    factory: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    key: str | None


class JobQueue(Client):
    dependencies = ()

    @classmethod
    def build(cls, container):
        return cls(
            container.settings.obot_job_workers,
            container.settings.obot_job_capacity,
            container.settings.obot_job_timeout,
            container.settings.obot_shutdown_timeout,
        )

    def __init__(
        self, workers: int = 2, capacity: int = 32, timeout: float = 60, shutdown_timeout: float = 15
    ) -> None:
        if workers < 1 or capacity < 1 or timeout <= 0:
            raise ValueError("Job queue limits must be positive")
        self.workers = workers
        self.capacity = capacity
        self.timeout = timeout
        self.shutdown_timeout = shutdown_timeout
        self._pending: deque[_Job] = deque()
        self._active_keys: set[str] = set()
        self._condition = asyncio.Condition()
        self._tasks: list[asyncio.Task] = []
        self._accepting = False
        self.running = 0
        self.completed = 0
        self.failed = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def start(self) -> None:
        if self._tasks:
            return
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._worker(), name=f"obot-worker-{index}") for index in range(self.workers)
        ]

    async def submit(
        self, name: str, factory: Callable[[], Awaitable[Any]], *, key: str | None = None
    ) -> asyncio.Future:
        async with self._condition:
            if not self._accepting:
                raise QueueClosed("Job queue is closed")
            if len(self._pending) >= self.capacity:
                raise QueueFull("Job queue has reached capacity")
            future = asyncio.get_running_loop().create_future()
            self._pending.append(_Job(name, factory, future, key))
            self._condition.notify_all()
            return future

    async def _next(self) -> _Job | None:
        async with self._condition:
            while True:
                for job in tuple(self._pending):
                    if job.future.cancelled():
                        self._pending.remove(job)
                        continue
                    if job.key is None or job.key not in self._active_keys:
                        self._pending.remove(job)
                        if job.key is not None:
                            self._active_keys.add(job.key)
                        self.running += 1
                        return job
                if not self._accepting and not self._pending:
                    return None
                await self._condition.wait()

    async def _worker(self) -> None:
        while (job := await self._next()) is not None:
            try:
                async with asyncio.timeout(self.timeout):
                    result = await job.factory()
            except asyncio.CancelledError:
                job.future.cancel()
                raise
            except Exception as error:
                self.failed += 1
                if not job.future.done():
                    job.future.set_exception(error)
            else:
                self.completed += 1
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                async with self._condition:
                    self.running -= 1
                    if job.key is not None:
                        self._active_keys.remove(job.key)
                    self._condition.notify_all()

    async def close(self, timeout: float | None = None) -> None:
        async with self._condition:
            self._accepting = False
            self._condition.notify_all()
        if not self._tasks:
            return
        try:
            async with asyncio.timeout(self.shutdown_timeout if timeout is None else timeout):
                await asyncio.gather(*self._tasks)
        except TimeoutError:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            for job in self._pending:
                job.future.cancel()
            self._pending.clear()
            self._tasks.clear()

    def snapshot(self) -> dict:
        return {
            "pending": self.pending,
            "running": self.running,
            "capacity": self.capacity,
            "workers": self.workers,
            "completed": self.completed,
            "failed": self.failed,
        }
