"""Full card fidelity: complete data, bounded async avatars and legacy colors."""

import asyncio
import io
import struct
import threading
from dataclasses import asdict
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from framework.clients.atcoder import AtcoderClient
from framework.clients.avatars import AvatarsClient
from framework.clients.codeforces import CodeforcesClient
from framework.clients.http import HttpClient, UpstreamError
from framework.clients.nowcoder import NowcoderClient
from framework.profiles import PROFILE_TIMEZONE, Profile, ProfileSection
from framework.views.profile import ProfilePixieBackend, profile_accent

from .test_profiles import ATC, CF, NK

NOW = int(datetime(2026, 10, 18, 12, tzinfo=PROFILE_TIMEZONE).timestamp())


def submit(day, index="A", verdict="OK", contest=100):
    return {
        "creationTimeSeconds": NOW - day * 86400,
        "problem": {"contestId": contest, "index": index},
        "verdict": verdict,
        "timeConsumedMillis": 31,
        "passedTestCount": 2,
    }


def test_cf_full_solve_counts_recent_three_and_rating_history():
    rows = [submit(0), submit(0), submit(1, "B"), submit(7, "C"), submit(0, "D", "WRONG_ANSWER")]
    history = [{"contestName": "Round", "rank": 8, "contestId": 100, "oldRating": 1200, "newRating": 1234}]
    profile = CodeforcesClient.enrich(CodeforcesClient.parse(CF), history, rows, now=NOW)
    counts = dict(profile.metrics)
    assert counts["通过题数"] == "3" and counts["今日过题"] == "1" and counts["本周过题"] == "2"
    assert counts["Rated 比赛数"] == "1"
    assert profile.sections[0].title == "最近比赛" and "Rating +34" in profile.sections[0].lines[-1]
    assert len(profile.sections[1].lines) == 3 and "P100A · AC · 31ms" in profile.sections[1].lines[0]
    assert "WA on test 3" in CodeforcesClient.submit_text(rows[-1])
    assert "最近提交" in profile.text() and "最高 Rating" in profile.text()


def test_cf_absent_or_broken_enrichment_does_not_fabricate_zero_statistics():
    base = CodeforcesClient.parse(CF)
    partial = CodeforcesClient.enrich(base, None, None)
    assert "通过题数" not in dict(partial.metrics) and len(partial.warnings) == 2
    assert not partial.sections and partial.handle == base.handle
    empty = CodeforcesClient.enrich(base, [], [])
    assert dict(empty.metrics)["通过题数"] == "0" and not empty.warnings
    assert empty.sections[-1].lines == ("还未提交过题目",)
    malformed = CodeforcesClient.enrich(base, [{}], [{}])
    assert len(malformed.warnings) == 2


async def test_cf_fetches_all_status_and_uses_canonical_handle_for_extras():
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append((method, dict(request.url.params)))
        return httpx.Response(200, json=CF if method == "user.info" else {"status": "OK", "result": []})

    http = HttpClient(transport=httpx.MockTransport(handler))
    client = CodeforcesClient(http, interval=0)
    try:
        profile = await client.profile("old_name")
        assert profile.handle == "Example" and len(calls) == 3
        status = next(params for method, params in calls if method == "user.status")
        assert status["handle"] == "Example" and "count" not in status and "from" not in status
        assert not profile.warnings and len(profile.sections) == 2
    finally:
        await client.close()
        await http.close()


async def test_atcoder_avatar_linked_account_promotion_and_algorithm_history():
    http = AsyncMock()
    http.request.return_value = httpx.Response(
        200, text=ATC + '<img class="avatar" src="//img.atcoder.jp/a.png">'
    )
    http.get_json.return_value = [
        {
            "IsRated": True,
            "ContestName": "Rated",
            "Place": 4,
            "OldRating": 1200,
            "NewRating": 1234,
            "Performance": 1400,
            "InnerPerformance": 1410,
        },
        {"IsRated": False, "ContestName": "Unrated later"},
    ]
    client = AtcoderClient(http)
    try:
        profile = await client.profile("example")
        assert profile.avatar_url == "//img.atcoder.jp/a.png" and profile.rating_note == "+44 to promote"
        assert profile.sections[0].title == "关联账号"
        assert profile.sections[1].lines[0] == "Rated" and "Rating +34" in profile.sections[1].lines[1]
        http.get_json.assert_awaited_once_with("https://atcoder.jp/users/example/history/json")
        assert "最近参赛 2026/10/11" in profile.timeline
    finally:
        await client.close()


async def test_nowcoder_total_from_pagination_and_bounded_team_members():
    http = AsyncMock()
    http.request.return_value = httpx.Response(200, text=NK + '<a class="group-member-btn">members</a>')
    member_calls = []

    async def api(url, **kwargs):
        if url.endswith("contest-joined-history"):
            rows = [{"contestName": "Round", "rank": 2, "acceptedCount": 7, "changeValue": -5.0}]
            total = 70
        elif url.endswith("user-team-list"):
            rows = [{"name": f"team{i}", "rating": 2000} for i in range(9)]
            total = 9
        else:
            rows = [{"name": f"member{i}", "uid": 100 + i, "isTeamAdmin": i == 0} for i in range(8)]
            total = 8
        return {"msg": "OK", "data": {"dataList": rows, "pageInfo": {"totalCount": total, "pageCount": 9}}}

    http.get_json.side_effect = api
    client = NowcoderClient(http)
    original = client._basic

    async def basic(handle):
        if handle == "123456":
            return await original(handle)
        member_calls.append(handle)
        return Profile("牛客", handle, "https://ac.nowcoder.com", rating=1200)

    client._basic = basic
    try:
        profile = await client.profile("123456")
        assert dict(profile.metrics)["Rated 比赛数"] == "70"
        assert len(member_calls) == 5 and http.get_json.await_count == 3
        assert profile.is_team and "UID 123456" in profile.timeline
        assert [section.title for section in profile.sections] == ["最近比赛", "加入的队伍", "队伍成员"]
        assert all(len(section.lines) == 5 for section in profile.sections[1:])
        assert "队长" in profile.sections[-1].lines[0] and "Rating 1200" in profile.sections[-1].lines[0]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "platform,rating,rank,color",
    [
        ("Codeforces", None, "Unrated", "#808080"),
        ("Codeforces", 1199, "", "#808080"),
        ("Codeforces", 1200, "", "#008000"),
        ("Codeforces", 1400, "", "#03a89e"),
        ("Codeforces", 1600, "", "#0000ff"),
        ("Codeforces", 1900, "", "#aa00aa"),
        ("Codeforces", 2100, "", "#ff8c00"),
        ("Codeforces", 2300, "", "#bbbb00"),
        ("Codeforces", 2400, "", "#ff0000"),
        ("牛客", 700, "", "#c177e7"),
        ("牛客", 1500, "", "#25bb9b"),
        ("牛客", 2800, "", "#ff020a"),
        ("AtCoder", 1000, "3 Dan", "#ff8000"),
        ("AtCoder", 400, "8 Kyu", "#804000"),
        ("AtCoder", 0, "King", "#ff0000"),
    ],
)
def test_legacy_rating_color_thresholds(platform, rating, rank, color):
    assert profile_accent(platform, rating, rank) == color


def avatar_bytes(size=(300, 200)):
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", size, "#ff0000")
    # A centered green square verifies center-cropping, not stretching.
    image.paste("#00ff00", (50, 0, 250, 200))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


async def test_avatar_download_cache_crop_restricted_hosts_and_bad_images():
    http = AsyncMock()
    http.download.return_value = avatar_bytes()
    client = AvatarsClient(http)
    try:
        results = await asyncio.gather(*(client.image("//img.atcoder.jp/avatar.png") for _ in range(4)))
        assert len(set(results)) == 1 and struct.unpack(">II", results[0][16:24]) == (176, 176)
        from PIL import Image

        with Image.open(io.BytesIO(results[0])) as cropped:
            red, green, blue = cropped.getpixel((0, 0))[:3]
            assert red <= 10 and green >= 245 and blue == 0
        assert http.download.await_count == 1
        for url in (
            "https://localhost/a",
            "https://127.0.0.1/a",
            "https://img.atcoder.jp@evil.example/a",
            "https://img.atcoder.jp:444/a",
            "file:///private",
            "https://img.atcoder.jp.evil.example/a",
        ):
            assert await client.image(url) is None
        assert http.download.await_count == 1
        http.download.return_value = b"<html>not an image</html>"
        assert await client.image("https://img.atcoder.jp/bad") is None
        http.download.return_value = avatar_bytes()
        assert await client.image("https://img.atcoder.jp/bad") is not None  # failure not cached
    finally:
        await client.close()


async def test_avatar_decode_runs_in_worker_and_does_not_block_peers():
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    http = AsyncMock()
    http.download.return_value = b"image"
    client = AvatarsClient(http)

    def normalize(data):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(3), "Avatar decode blocked event loop"
        return b"normalized"

    with patch.object(client, "normalize", normalize):
        task = asyncio.create_task(client.image("https://img.atcoder.jp/avatar.png"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            assert await asyncio.wait_for(asyncio.sleep(0, result=True), 0.5)
            assert not task.done()
        finally:
            release.set()
            await task
            await client.close()


async def test_binary_download_stops_at_limit_and_does_not_follow_redirects():
    http = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345")))
    try:
        with pytest.raises(UpstreamError, match="size limit"):
            await http.download("https://img.atcoder.jp/a", limit=4)
    finally:
        await http.close()
    http = HttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "https://localhost/private"})
        )
    )
    try:
        with pytest.raises(UpstreamError, match="rejected"):
            await http.download("https://img.atcoder.jp/a")
    finally:
        await http.close()


def test_complete_card_renders_avatar_and_sections_using_source_layout(tmp_path):
    pytest.importorskip("pixie")
    profile = Profile(
        "Codeforces",
        "Example",
        "https://codeforces.com/profile/Example",
        1500,
        "Specialist",
        metrics=(
            ("通过题数", "100"),
            ("Rated 比赛数", "10"),
            ("今日过题", "2"),
            ("本周过题", "6"),
            ("贡献", "0"),
            ("粉丝", "20"),
        ),
        details=("Example University",),
        timeline=("注册于 2020/01/01",),
        rating_note="最高 Rating 1700",
        sections=(ProfileSection("最近提交", ("P100A · AC",)),),
    )
    model = asdict(profile)
    model["avatar"] = AvatarsClient.normalize(avatar_bytes())
    backend = ProfilePixieBackend()
    rendered = backend.render(model)
    model["avatar"] = None
    without = backend.render(model)
    assert rendered.data != without.data and rendered.width == 1472
    assert rendered.height > 1700 and rendered.height < 4096
    path = tmp_path / "card.png"
    path.write_bytes(rendered.data)
    from PIL import Image

    with Image.open(path) as image:
        # Original avatar origin is x128, y168; the middle retains real pixels.
        assert image.getpixel((216, 256))[:3] == (0, 255, 0)
