import asyncio
import threading

import httpx
import pytest

from framework.clients.http import HttpClient, UpstreamError
from framework.clients.jobs import JobQueue, QueueClosed, QueueFull


async def test_serial_keys_preserve_order_without_blocking_other_keys():
    queue = JobQueue(2, 10)
    await queue.start()
    first_started, release, independent_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    order = []

    async def first():
        order.append("first-start")
        first_started.set()
        await release.wait()
        order.append("first-end")

    async def second():
        order.append("second")

    async def independent():
        independent_done.set()

    futures = [await queue.submit("first", first, key="scene-a")]
    await first_started.wait()
    futures.append(await queue.submit("second", second, key="scene-a"))
    futures.append(await queue.submit("independent", independent, key="scene-b"))
    await asyncio.wait_for(independent_done.wait(), 1)
    assert "second" not in order
    release.set()
    await asyncio.gather(*futures)
    await queue.close()
    assert order == ["first-start", "first-end", "second"]
    assert queue.running == queue.pending == 0


async def test_capacity_is_bounded_and_shutdown_cancels_waiters():
    queue = JobQueue(1, 1)
    await queue.start()
    started = asyncio.Event()

    async def block():
        started.set()
        await asyncio.Event().wait()

    running = await queue.submit("running", block)
    await started.wait()
    waiting = await queue.submit("waiting", block)
    with pytest.raises(QueueFull):
        await queue.submit("overflow", block)
    await queue.close(timeout=0.02)
    assert running.cancelled() and waiting.cancelled()
    with pytest.raises(QueueClosed):
        await queue.submit("closed", block)


async def test_timeout_does_not_kill_worker():
    queue = JobQueue(1, 2, timeout=0.02)
    await queue.start()
    future = await queue.submit("slow", lambda: asyncio.sleep(1))
    with pytest.raises(TimeoutError):
        await future
    result = await queue.submit("next", lambda: asyncio.sleep(0, result="ok"))
    assert await result == "ok"
    await queue.close()
    assert queue.failed == 1


async def test_cancelled_waiter_releases_slot_and_worker_survives():
    queue = JobQueue(1, 1)
    await queue.start()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def block():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    try:
        running = await queue.submit("running", block)
        await started.wait()
        cancelled = await queue.submit("cancelled", block)
        cancelled.cancel()
        next_job = await queue.submit("next", lambda: asyncio.sleep(0, result="ok"))
        running.cancel()
        await asyncio.wait_for(stopped.wait(), 1)
        assert await asyncio.wait_for(next_job, 1) == "ok"
    finally:
        await queue.close()


async def test_factory_error_does_not_lose_worker_or_serial_key():
    queue = JobQueue(1, 2)
    await queue.start()

    def failed_factory():
        raise ValueError("failed before returning coroutine")

    try:
        first = await queue.submit("first", failed_factory, key="same")
        second = await queue.submit("second", lambda: asyncio.sleep(0, result="ok"), key="same")
        with pytest.raises(ValueError):
            await first
        assert await asyncio.wait_for(second, 1) == "ok"
    finally:
        await queue.close()


async def test_job_factory_may_return_a_future():
    queue = JobQueue(1, 1)
    await queue.start()
    value = asyncio.get_running_loop().create_future()
    try:
        result = await queue.submit("future", lambda: value)
        value.set_result("ok")
        assert await result == "ok"
    finally:
        await queue.close()


async def test_http_retries_get_but_never_repeats_post():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(503 if len(calls) < 3 else 200, json={"ok": True})

    client = HttpClient(transport=httpx.MockTransport(handler), retry_delay=0)
    try:
        assert await client.get_json("https://example.test/path") == {"ok": True}
        assert calls == ["GET"] * 3
        calls.clear()
        with pytest.raises(UpstreamError):
            await client.request("POST", "https://example.test/path")
        assert calls == ["POST"]
    finally:
        await client.close()


async def test_http_errors_do_not_expose_secrets_or_response_content():
    client = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(403, text="private")))
    try:
        with pytest.raises(UpstreamError) as error:
            await client.get_json("https://example.test/path?token=not-a-real-secret")
        assert "secret" not in str(error.value) and "private" not in str(error.value)
    finally:
        await client.close()


async def test_http_has_per_host_concurrency_limit():
    running, maximum = 0, 0

    async def handler(request):
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        await asyncio.sleep(0.01)
        running -= 1
        return httpx.Response(200, json={})

    client = HttpClient(concurrency=2, transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(*(client.get_json("https://example.test/path") for _ in range(8)))
        assert maximum == 2
    finally:
        await client.close()


async def test_slow_json_decode_does_not_block_other_async_work():
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    class SlowResponse(httpx.Response):
        def json(self, **kwargs):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3), "JSON decoding blocked the event loop"
            return {"ok": True}

    client = HttpClient(transport=httpx.MockTransport(lambda request: SlowResponse(200)))
    request = asyncio.create_task(client.get_json("https://example.test/large.json"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), 0.5) == "responsive"
        assert not request.done() and not release.is_set()
        release.set()
        assert await request == {"ok": True}
    finally:
        release.set()
        try:
            await request
        finally:
            await client.close()
