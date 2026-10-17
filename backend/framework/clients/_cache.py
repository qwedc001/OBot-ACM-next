"""Bounded success cache; shared work survives cancellation of one waiter."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic

from .http import UpstreamError


class AsyncCache[T]:
    def __init__(self, *, ttl=60, capacity=128, timeout=30):
        if ttl <= 0 or capacity < 1 or timeout <= 0:
            raise ValueError("Cache and timeout limits must be positive")
        self.ttl, self.capacity, self.timeout = ttl, capacity, timeout
        self._cache: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._pending: dict[str, asyncio.Task] = {}
        self._closed = False

    async def get(self, key: str, fetch: Callable[[], Awaitable[T]]) -> T:
        if self._closed:
            raise UpstreamError("Cached client closed")
        cached = self._cache.get(key)
        if cached and cached[0] > monotonic():
            self._cache.move_to_end(key)
            return cached[1]
        task = self._pending.get(key)
        if task is None:
            if len(self._pending) >= 32:
                raise UpstreamError("Query queue full")
            task = asyncio.create_task(self._load(key, fetch))
            self._pending[key] = task
            # All waiters may leave before an upstream failure arrives.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def _load(self, key, fetch):
        try:
            async with asyncio.timeout(self.timeout):
                value = await fetch()
            self._cache[key] = (monotonic() + self.ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
            return value
        except TimeoutError:
            raise UpstreamError("Cached request timed out") from None
        finally:
            self._pending.pop(key, None)

    async def close(self):
        self._closed = True
        tasks = tuple(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first step never enters _load's finally.
        self._pending.clear()
        self._cache.clear()
