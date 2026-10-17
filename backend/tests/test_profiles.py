import asyncio
import struct
import threading
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from framework.catalog import CommandCatalog
from framework.clients import _cache
from framework.clients.atcoder import AtcoderClient
from framework.clients.codeforces import CodeforcesClient
from framework.clients.http import HttpClient, UpstreamError
from framework.clients.jobs import QueueClosed, QueueFull
from framework.clients.nowcoder import NowcoderClient
from framework.clients.renderer import RenderedImage
from framework.config import Settings
from framework.contracts import RequestContext, SceneKind, SceneRef
from framework.messages import Image, Message
from framework.profiles import Profile, ProfileError, ProfileNotFound
from framework.runtime import Services
from framework.views.profile import ProfilePixieBackend

from .conftest import USER

FIXTURES = Path(__file__).with_name("fixtures") / "profiles"
ATC = (FIXTURES / "atcoder.html").read_text(encoding="utf-8")
NK = (FIXTURES / "nowcoder.html").read_text(encoding="utf-8")
CF = {
    "status": "OK",
    "result": [
        {
            "handle": "Example",
            "rating": 0,
            "rank": "newbie",
            "maxRating": 1400,
            "friendOfCount": 0,
            "contribution": -2,
            "firstName": "Test",
            "lastName": "User",
            "country": "Earth",
            "registrationTimeSeconds": 0,
        }
    ],
}
SAMPLE = Profile("Codeforces", "example", "https://codeforces.com/profile/example", 1234, "Pupil")


def test_platform_fields_zero_rating_and_no_linked_platform_io():
    cf = CodeforcesClient.parse(CF)
    assert cf.handle == "Example" and cf.rating == 0 and cf.rank == "Newbie"
    assert ("粉丝", "0") in cf.metrics and ("贡献", "-2") in cf.metrics
    assert "Test User" in cf.details and "注册于 1970/01/01" in cf.timeline
    atc = AtcoderClient.parse(ATC, "example")
    assert atc.rating == 2345 and atc.rank == "3 Dan"
    assert ("Rated 比赛数", "56") in atc.metrics
    assert "组织：算法协会" in atc.details and "Codeforces: other_account" in atc.sections[0].lines
    nk = NowcoderClient.parse(NK, "123456")
    assert nk.rating == 2500 and nk.handle == "示例选手"
    assert ("关注", "0") in nk.metrics and "示例大学" in nk.details
    assert NowcoderClient.parse(NK + '<a class="group-member-btn">members</a>', "123456").rank == "团队"


def test_unrated_and_optional_fields_are_not_fabricated():
    cf = CodeforcesClient.parse({"status": "OK", "result": [{"handle": "new"}]})
    assert cf.rating is None and cf.rank == "Unrated" and cf.metrics == cf.details == ()
    atc = AtcoderClient.parse(
        '<h3><a class="username">new</a></h3><table class="dl-table">'
        "<tr><th>Country/Region</th><td>Japan</td></tr></table>",
        "new",
    )
    assert atc.rating is None and atc.rank == "Unrated"
    assert AtcoderClient.parse(ATC.replace("2345", "0"), "example").rating == 0
    assert NowcoderClient.parse(NK.replace("2500", "0"), "123456").rating == 0
    assert "Unrated" in cf.text() and "None" not in cf.text()


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"status": "OK", "result": []},
        {"status": "OK", "result": [{"handle": "a", "rating": "oops"}]},
        {"status": "FAILED", "comment": "Call limit exceeded"},
    ],
)
def test_cf_bad_responses_are_upstream_errors(data):
    with pytest.raises(UpstreamError):
        CodeforcesClient.parse(data)


def test_absent_accounts_are_distinct_from_block_pages():
    with pytest.raises(ProfileNotFound):
        CodeforcesClient.parse({"status": "FAILED", "comment": "handles: User with handle noone not found"})
    with pytest.raises(ProfileNotFound):
        NowcoderClient.parse('<div class="null">不存在</div>', "123456")
    for parse, handle in ((AtcoderClient.parse, "example"), (NowcoderClient.parse, "123456")):
        for page in ("", "<html>Verify you are human / login</html>"):
            with pytest.raises(UpstreamError):
                parse(page, handle)
    with pytest.raises(UpstreamError):
        AtcoderClient.parse(ATC, "someone_else")
    with pytest.raises(UpstreamError):
        NowcoderClient.parse(NK.replace("2500", "hidden"), "123456")


@pytest.mark.parametrize(
    "client_cls,handle",
    [
        (CodeforcesClient, "a;b"),
        (AtcoderClient, "../abc"),
        (NowcoderClient, "nickname"),
        (NowcoderClient, "0"),
    ],
)
async def test_invalid_identifiers_never_reach_network(client_cls, handle):
    http = AsyncMock()
    client = client_cls(http)
    with pytest.raises(ProfileError):
        await client.profile(handle)
    http.request.assert_not_called()
    http.get_json.assert_not_called()


async def test_http_profiles_use_fixed_hosts_and_expected_statuses():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "codeforces.com":
            return httpx.Response(400, json={"status": "FAILED", "comment": "User with handle no not found"})
        return httpx.Response(404)

    http = HttpClient(transport=httpx.MockTransport(handler))
    clients = [CodeforcesClient(http, interval=0), AtcoderClient(http), NowcoderClient(http)]
    try:
        for client, handle in zip(clients, ("no", "no", "123"), strict=True):
            with pytest.raises(ProfileNotFound):
                await client.profile(handle)
        assert calls[0].url.params["handles"] == "no"
        assert calls[1].url.params["lang"] == "en" and calls[1].url.params["contestType"] == "algo"
    finally:
        await asyncio.gather(*(client.close() for client in clients))
        await http.close()


async def test_cache_expiry_lru_bound_failure_recovery_and_casefold(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(_cache, "monotonic", lambda: clock[0])
    client = AtcoderClient(AsyncMock(), capacity=2)
    client._fetch = AsyncMock(return_value=SAMPLE)
    assert await client.profile("Example") == await client.profile("example") == SAMPLE
    assert client._fetch.await_count == 1
    await client.profile("other")
    await client.profile("example")  # touch: evict other next
    await client.profile("third")
    await client.profile("other")
    assert client._fetch.await_count == 4 and len(client.cache._cache) == 2
    clock[0] += 61
    await client.profile("other")
    assert client._fetch.await_count == 5
    client._fetch.side_effect = UpstreamError("safe")
    with pytest.raises(UpstreamError):
        await client.profile("failed")
    client._fetch.side_effect = None
    await client.profile("failed")
    assert client._fetch.await_count == 7
    await client.close()


async def test_coalesced_requests_parallel_handles_and_cancellation_isolation():
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def fetch(handle):
        calls.append(handle)
        if len(calls) == 2:
            started.set()
        await release.wait()
        return SAMPLE

    client = AtcoderClient(AsyncMock())
    client._fetch = fetch
    cancelled = asyncio.create_task(client.profile("same"))
    peer = asyncio.create_task(client.profile("SAME"))
    other = asyncio.create_task(client.profile("other"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert not peer.done() and not other.done() and len(calls) == 2
        release.set()
        assert await peer == await other == SAMPLE
        assert not client.cache._pending
    finally:
        release.set()
        await client.close()


async def test_deadlines_pending_capacity_and_shutdown():
    client = AtcoderClient(AsyncMock(), timeout=0.02)

    async def blocked(handle):
        await asyncio.Event().wait()

    client._fetch = blocked
    with pytest.raises(UpstreamError, match="timed out"):
        await client.profile("slow")
    assert not client.cache._pending
    client.cache.timeout = 10
    tasks = [asyncio.create_task(client.profile(f"user{i}")) for i in range(32)]
    await asyncio.sleep(0)
    with pytest.raises(UpstreamError, match="queue full"):
        await client.profile("overflow")
    await client.close()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert not client.cache._pending and not client.cache._cache
    with pytest.raises(UpstreamError, match="closed"):
        await client.profile("after_close")


async def test_cf_actual_requests_are_spaced_and_http_does_not_retry():
    times = []

    async def handler(request):
        times.append(asyncio.get_running_loop().time())
        return httpx.Response(503 if len(times) == 1 else 200, json=CF)

    http = HttpClient(transport=httpx.MockTransport(handler), retry_delay=0)
    client = CodeforcesClient(http, interval=0.03)
    try:
        with pytest.raises(UpstreamError):
            await client._api("user.info", handles="first")
        assert len(times) == 1
        await asyncio.gather(client._api("user.info", handles="second"), client._api("user.info", handles="third"))
        assert len(times) == 3
        assert all(b - a >= 0.025 for a, b in zip(times, times[1:], strict=False))
    finally:
        await client.close()
        await http.close()


async def test_html_parsing_runs_off_the_event_loop():
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    client = AtcoderClient(AsyncMock())
    client.http.request.return_value = httpx.Response(200, text=ATC)
    client.http.get_json.return_value = []

    def parse(*args):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(3), "HTML parsing blocked the event loop"
        return SAMPLE

    with patch.object(client, "parse", parse):
        task = asyncio.create_task(client.profile("example"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            assert await asyncio.wait_for(asyncio.sleep(0, result=True), 0.5)
            assert not task.done()
        finally:
            release.set()
            await task
            await client.close()


async def test_service_discovery_portable_image_and_text_errors(tmp_path, database):
    services = Services(Settings(obot_data_dir=tmp_path), CommandCatalog(), session_factory=database)
    await services.start()
    context = RequestContext(USER, SceneRef("qq", "bot", SceneKind.GROUP, "g"), "event")
    try:
        service = services.profiles
        assert set(service.clients) == {"codeforces", "atcoder", "nowcoder"}
        with patch.object(services.clients.codeforces, "profile", AsyncMock(return_value=SAMPLE)):
            assert await service.query("codeforces", "example") == SAMPLE
            with patch.object(
                service.renderer, "render", AsyncMock(side_effect=AssertionError("must not draw"))
            ):
                assert (
                    await service.reply(context, "codeforces", "example", image_available=False)
                    == SAMPLE.text()
                )
            # Test the jobs/artifacts pipeline even without optional pixie;
            # real PNG rendering has separate, optional tests below.
            rendered = RenderedImage(b"\x89PNG\r\n\x1a\nsynthetic", "image/png", 1040, 800)
            with patch.object(service.renderer._views["profile"], "render", return_value=rendered):
                result = await service.reply(context, "codeforces", "example")
            assert isinstance(result, Message)
            assert any(isinstance(segment, Image) for segment in result.parts)
            for error in (ImportError(), OSError(), ValueError(), TimeoutError(), QueueFull(), QueueClosed()):
                with patch.object(service.renderer, "render", AsyncMock(side_effect=error)):
                    text = await service.reply(context, "codeforces", "example")
                    assert SAMPLE.text() in text and "已返回文字资料" in text
        for error, expected in (
            (ProfileNotFound(), "没有找到"),
            (ProfileError("输入错误"), "输入错误"),
            (UpstreamError("never leak detail"), "暂时无法获取"),
        ):
            with patch.object(services.clients.codeforces, "profile", AsyncMock(side_effect=error)):
                text = await service.reply(context, "codeforces", "example")
                assert expected in text and "never leak" not in text
    finally:
        await services.close()


@pytest.mark.parametrize(
    "profile",
    [
        CodeforcesClient.parse(CF),
        AtcoderClient.parse(ATC, "example"),
        NowcoderClient.parse(NK, "123456"),
        Profile("AtCoder", "新人", "https://atcoder.jp/users/new"),
        Profile(
            "牛客",
            "很长的中文名字" * 10,
            "https://ac.nowcoder.com/acm/contest/profile/123",
            9999,
            "团队",
            metrics=(("最高 Rating", "9" * 60),) * 6,
            details=("很长的简介与组织信息" * 30,) * 9,
        ),
    ],
)
def test_real_card_render_has_bounded_png_and_measured_height(profile):
    pytest.importorskip("pixie")
    image = ProfilePixieBackend().render(asdict(profile))
    assert image.mime == "image/png" and image.data.startswith(b"\x89PNG")
    assert struct.unpack(">II", image.data[16:24]) == (image.width, image.height)
    assert image.width == 1472 and 450 < image.height < 8192
