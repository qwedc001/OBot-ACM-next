import asyncio

import httpx
import pytest

from framework.clients import hitokoto as module
from framework.clients.hitokoto import HitokotoClient
from framework.clients.http import HttpClient, UpstreamError
from framework.services.hitokoto import HitokotoService

PAYLOAD = {"hitokoto": "今天也要开心地 AC。", "from": "训练日记", "from_who": "小明"}


async def test_sentence_attribution_cache_and_concurrent_requests(monkeypatch):
    calls = []
    clock = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])

    async def api(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=PAYLOAD)

    http = HttpClient(transport=httpx.MockTransport(api), retry_delay=0)
    service = HitokotoService(HitokotoClient(http))
    try:
        replies = await asyncio.gather(*(service.random() for _ in range(10)))
        assert replies == ["[Hitokoto]\n今天也要开心地 AC。\nBy 小明「训练日记」"] * 10
        assert len(calls) == 1 and calls[0].url.params["encode"] == "json"
        clock[0] = 11.0
        await service.random()
        assert len(calls) == 1
        clock[0] = 12.0
        await service.random()
        assert len(calls) == 2
    finally:
        await http.close()


@pytest.mark.parametrize(
    "source,author,expected",
    [("出处", None, "By 「出处」"), (None, "", "By 「未知出处」"), ("  出处  ", " 作者 ", "By 作者「出处」")],
)
async def test_missing_author_or_source_is_not_rendered_as_none(source, author, expected):
    http = HttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={**PAYLOAD, "from": source, "from_who": author})
        )
    )
    try:
        assert (await HitokotoService(HitokotoClient(http)).random()).endswith(expected)
    finally:
        await http.close()


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"hitokoto": " "}, {**PAYLOAD, "hitokoto": 123}, {**PAYLOAD, "from_who": 123}]
)
async def test_invalid_response_has_a_safe_user_facing_failure(payload):
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    try:
        assert await HitokotoService(HitokotoClient(http)).random() == "一言暂时无法获取，请稍后再试。"
    finally:
        await http.close()


async def test_timeout_is_bounded_and_does_not_block_other_tasks():
    started = asyncio.Event()

    async def api(request):
        started.set()
        await asyncio.Event().wait()

    http = HttpClient(transport=httpx.MockTransport(api))
    client = HitokotoClient(http, timeout=0.05)
    task = asyncio.create_task(client.random())
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await asyncio.sleep(0, result="responsive") == "responsive"
        with pytest.raises(UpstreamError, match="timed out"):
            await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await http.close()


async def test_unavailable_upstream_is_not_cached_and_can_recover():
    available = False

    def api(request):
        return httpx.Response(200, json=PAYLOAD) if available else httpx.Response(503, text="private error")

    http = HttpClient(transport=httpx.MockTransport(api), retry_delay=0)
    service = HitokotoService(HitokotoClient(http))
    try:
        assert await service.random() == "一言暂时无法获取，请稍后再试。"
        available = True
        assert "今天也要开心地 AC。" in await service.random()
    finally:
        await http.close()
