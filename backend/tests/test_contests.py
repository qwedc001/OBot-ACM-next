"""Public fixtures, time boundaries and shared Dashboard/command workflows."""

import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from framework.clients._contests import atcoder_contests, codeforces_contests, nowcoder_contests
from framework.clients.atcoder import AtcoderClient
from framework.clients.codeforces import CodeforcesClient
from framework.clients.http import HttpClient, UpstreamError
from framework.clients.nowcoder import NowcoderClient
from framework.contests import TIMEZONE, Contest, search_contests, select_contests
from framework.messages import Image, Text
from framework.services.contests import ContestSnapshot, ContestsService
from framework.views.contests import ContestBackend

from .conftest import ROOT
from .test_boards import context
from .test_curation import CONTEST, CONTEST_VALUE, approve, ok
from .test_dashboard import login
from .test_dashboard import web as web

FIXTURES = Path(__file__).with_name("fixtures") / "contests"
ATC = (FIXTURES / "atcoder.html").read_text(encoding="utf-8")
NK = (FIXTURES / "nowcoder.html").read_text(encoding="utf-8")


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=TIMEZONE).timestamp())


NOW = stamp("2026-10-18T12:00")
SAMPLE = Contest("cf:1", "cf", "Codeforces", "CF1", "Example Round", NOW + 3600, 7200)
CF = [
    {
        "id": 1,
        "name": SAMPLE.name,
        "phase": "BEFORE",
        "type": "CF",
        "startTimeSeconds": SAMPLE.start,
        "durationSeconds": 7200,
    }
]


def test_platform_parsers_times_urls_and_validation():
    cf = codeforces_contests(CF)[0]
    assert cf.start == SAMPLE.start and cf.url == "https://codeforces.com/contest/1"
    atc, recent = atcoder_contests(ATC)
    assert atc.start == stamp("2026-10-18T20:00") and atc.duration == 6000
    assert atc.abbr == "ABC999" and recent.key == "atc:arc999"
    assert atc.supplement == "为 0-1999 计分" and recent.supplement == "为所有人计分"
    assert cf.supplement == "CF 赛制"
    nk = nowcoder_contests(NK, "牛客系列赛")[0]
    assert nk.start == stamp("2026-10-18T23:00") and nk.end == stamp("2026-10-19T01:00")
    assert nk.supplement == "为 0-1599 计分" and nk.url.endswith("/999")
    assert codeforces_contests([{"id": 9}]) == ()  # no announced start
    assert atcoder_contests('<div id="contest-table-upcoming"><table><tbody/></table></div>') == ()
    assert nowcoder_contests('<div class="platform-mod js-current"/>', "高校比赛") == ()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("-", "不计分"),
        ("All", "为所有人计分"),
        ("- 1999", "为 0-1999 计分"),
        ("1600 - 2999", "为 1600-2999 计分"),
        ("2000 -", "为 2000-∞ 计分"),
    ],
)
def test_atcoder_supplement_uses_original_wording(value, expected):
    assert atcoder_contests(ATC.replace("- 1999", value))[0].supplement == expected


def test_nowcoder_supplement_uses_original_wording_without_extra_conditions():
    assert (
        nowcoder_contests(NK.replace('<span class="tag-rating">Rated</span>', ""), "牛客")[0].supplement
        == "不计分"
    )
    no_limit = NK.replace('<li class="icon-nc-flash2">不计Rating的范围：Rating＞1599</li>', "")
    assert nowcoder_contests(no_limit, "牛客")[0].supplement == "为所有人计分"
    assert nowcoder_contests(NK.replace("＞", ">"), "牛客")[0].supplement == "为 0-1599 计分"
    with pytest.raises(UpstreamError):
        nowcoder_contests(NK.replace("＞", "≥"), "牛客")  # do not invent a different bound


@pytest.mark.parametrize(
    "parse, data",
    [
        (codeforces_contests, {}),
        (codeforces_contests, [{**CF[0], "durationSeconds": 0}]),
        (codeforces_contests, [{**CF[0], "id": True}]),
        (codeforces_contests, [{**CF[0], "name": None}]),
        (atcoder_contests, "<h1>Please sign in</h1>"),
        (atcoder_contests, ATC.replace("01:40", "01:90")),
        (atcoder_contests, ATC.replace("+0900", "")),
        (atcoder_contests, ATC.replace("/contests/abc999", "https://attacker.test/")),
        (lambda x: nowcoder_contests(x, "高校比赛"), "<html>challenge</html>"),
        (lambda x: nowcoder_contests(x, "高校比赛"), NK.replace("2026-10-19 01:00", "2026-10-18 22:00")),
    ],
)
def test_bad_responses_do_not_become_empty_contest_lists(parse, data):
    with pytest.raises(UpstreamError):
        parse(data)


async def test_native_clients_reuse_http_gate_and_deduplicate_categories():
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "codeforces.com":
            assert request.url.params["gym"] == "false"
            return httpx.Response(200, json={"status": "OK", "result": CF})
        return httpx.Response(200, text=ATC if request.url.host == "atcoder.jp" else NK)

    http = HttpClient(transport=httpx.MockTransport(respond))
    cf, atc, nk = CodeforcesClient(http, interval=0), AtcoderClient(http), NowcoderClient(http)
    try:
        async with cf._gate:
            pending = asyncio.create_task(cf.contests())
            await asyncio.sleep(0)
            assert not calls  # profile and contests share the same API gate
        assert len(await pending) == 1
        assert len(await atc.contests()) == 2
        assert len(await nk.contests()) == 1
        assert {r.url.params["topCategoryFilter"] for r in calls if r.url.host == "ac.nowcoder.com"} == {
            "13",
            "14",
        }
    finally:
        await asyncio.gather(cf.close(), atc.close(), nk.close())
        await http.close()


def test_today_midnight_overlap_and_recent_window_per_source():
    day = stamp("2026-10-18T00:00")

    def item(key, start, duration=3600, source="cf"):
        return replace(SAMPLE, key=key, source=source, start=start, duration=duration)

    items = (
        item("overnight", day - 1800, 7200),
        item("midnight_end", day - 3600),
        item("midnight_start", day + 86400),
        item("tomorrow", day + 86400 + 3600),
        item("horizon", day + 31 * 86400),
        item("recent_atc", day - 10 * 86400, source="atc"),
        item("older_atc", day - 20 * 86400, source="atc"),
        SAMPLE,
        SAMPLE,
    )
    assert {c.key for c in select_contests(items, NOW, today=True)} == {"overnight", "cf:1"}
    assert {c.key for c in select_contests(items, NOW)} == {
        "overnight",
        "cf:1",
        "midnight_start",
        "tomorrow",
        "recent_atc",
    }
    assert SAMPLE.phase(SAMPLE.start) == "running" and SAMPLE.phase(SAMPLE.end) == "ended"
    judging = replace(SAMPLE, judging="系统测试中")
    assert judging.status(SAMPLE.end + 1) == "系统测试中"
    assert judging.phase(SAMPLE.end + 7 * 86400) == "ended"


def test_search_exact_keys_ambiguous_names_and_typo_candidates():
    other = replace(SAMPLE, key="manual:2", source="manual")
    assert search_contests((SAMPLE, other), "CF:1") == ((SAMPLE,), False)
    assert len(search_contests((SAMPLE, other), "Example Round")[0]) == 2
    assert search_contests((SAMPLE,), "Exampel Round") == ((SAMPLE,), True)
    assert not search_contests((SAMPLE,), "无关比赛")[0]
    assert not search_contests((SAMPLE,), "x" * 161)[0]


async def test_shared_fetch_partial_failure_retry_and_live_manual_reads(web):
    services, _ = web
    service = services.contests
    clients = {
        key: AsyncMock(return_value=(replace(SAMPLE, key=key + ":1", source=key),))
        for key in ("cf", "atc", "nk")
    }
    clients["nk"].side_effect = UpstreamError("never leak response")
    for key, mock in clients.items():
        service.clients[key].contests = mock
    first, second = await asyncio.gather(service.collect(now=NOW), service.collect(now=NOW))
    assert first == second and len(first.items) == 2
    assert first.warnings == ("牛客暂时无法获取。",)
    assert all(mock.await_count == 1 for mock in clients.values())
    await service.collect(now=NOW)
    assert clients["cf"].await_count == 1 and clients["nk"].await_count == 2
    path = services.curation.contestlist.data_path
    path.write_text(json.dumps([CONTEST_VALUE]), encoding="utf-8")
    manual = await service.collect("manual", now=NOW)
    assert manual.items[0].name == CONTEST_VALUE["name"]
    path.write_text("[]", encoding="utf-8")
    assert not (await service.collect("manual", now=NOW)).items
    path.write_text("broken", encoding="utf-8")
    assert (await service.collect("manual", now=NOW)).warnings == ("手动比赛暂时无法获取。",)


async def test_dashboard_pending_approved_applied_and_deleted_visibility(web):
    services, client = web
    _, headers = await login(services, client, ROOT)
    created = ok(
        await client.post(CONTEST + "/submissions", headers=headers, json={"contest": CONTEST_VALUE}), 201
    )
    assert not (await services.contests.collect("manual")).items
    await approve(client, headers, created["submission"]["id"])
    assert not (await services.contests.collect("manual")).items
    ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))
    item = (await services.contests.collect("manual")).items[0]
    deletion = ok(
        await client.post(
            CONTEST + "/admin/delete", headers=headers, json={"hash": item.key.removeprefix("manual:")}
        ),
        201,
    )
    await approve(client, headers, deletion["submission"]["id"])
    ok(await client.post(CONTEST + "/admin/apply", headers=headers, json={}))
    assert not (await services.contests.collect("manual")).items


async def test_text_fallback_detail_disambiguation_and_no_query_in_errors():
    renderer = Mock()
    renderer.render_pages = AsyncMock(side_effect=ValueError("private failure"))
    service = ContestsService(None, {}, renderer)
    snapshot = ContestSnapshot((SAMPLE,), ("牛客暂时无法获取。",), NOW)
    service.collect = AsyncMock(return_value=snapshot)
    try:
        result = await service.listing(context())
        assert "图片暂不可用" in "".join(result) and "private failure" not in "".join(result)
        assert "牛客暂时无法获取" in "".join(result)
        assert SAMPLE.name in "".join(await service.detail(context(), "cf:1"))
        service.collect.return_value = replace(snapshot, items=(SAMPLE, replace(SAMPLE, key="manual:2")))
        assert "/contest detail manual:2" in "".join(await service.detail(context(), SAMPLE.name))
        service.collect.reset_mock()
        assert "160" in "".join(await service.detail(context(), "x" * 161))
        service.collect.assert_not_awaited()
        service.collect.return_value = replace(snapshot, items=())
        assert "成功获取的数据" in "".join(await service.listing(context()))
    finally:
        await service.close()


async def test_image_pages_keep_actor_scope_order_and_text_only_skips_rendering():
    renderer = Mock()
    renderer.render_pages = AsyncMock(
        return_value=(SimpleNamespace(id="first"), SimpleNamespace(id="second"))
    )
    renderer.artifacts.read = AsyncMock(side_effect=(b"first image", b"second image"))
    service = ContestsService(None, {}, renderer)
    service.collect = AsyncMock(return_value=ContestSnapshot((SAMPLE,), (), NOW))
    ctx = context()
    try:
        messages = await service.listing(ctx)
        assert [message.parts for message in messages] == [
            (Text("近日算法竞赛 · 1/2"), Image(b"first image")),
            (Text("近日算法竞赛 · 2/2"), Image(b"second image")),
        ]
        assert renderer.render_pages.call_args.args[0] == ctx.actor
        assert [call.args for call in renderer.artifacts.read.await_args_list] == [
            (ctx.actor, "first"),
            (ctx.actor, "second"),
        ]
        renderer.render_pages.reset_mock()
        text = "".join(await service.listing(ctx, image_available=False))
        assert SAMPLE.name in text and "cf:1" in text
        renderer.render_pages.assert_not_awaited()
    finally:
        await service.close()


async def test_detail_image_preserves_link_fuzzy_label_and_text_fallback():
    renderer = Mock()
    renderer.render_pages = AsyncMock(return_value=(SimpleNamespace(id="detail"),))
    renderer.artifacts.read = AsyncMock(return_value=b"image")
    service = ContestsService(None, {}, renderer)
    contest = replace(SAMPLE, url="https://codeforces.com/contest/1")
    service.collect = AsyncMock(return_value=ContestSnapshot((contest,), ("牛客暂时无法获取。",), NOW))
    ctx = context()
    try:
        result = await service.detail(ctx, "cf:1")
        assert len(result) == 1 and result[0].parts[1] == Image(b"image")
        assert contest.url in result[0].parts[0].content and "1/1" not in result[0].parts[0].content
        model = renderer.render_pages.call_args.args[2]
        assert model["detail"] and model["warnings"] == ("牛客暂时无法获取。",)
        assert model["items"] == [asdict(contest)]
        result = await service.detail(ctx, "Exampel Round")
        assert "近似匹配" in result[0].parts[0].content
        assert "近似匹配" in renderer.render_pages.call_args.args[2]["title"]
        renderer.render_pages.reset_mock()
        text = "".join(await service.detail(ctx, "cf:1", image_available=False))
        assert contest.url in text and contest.name in text
        renderer.render_pages.assert_not_awaited()
        renderer.render_pages.side_effect = ValueError("private render error")
        fallback = "".join(await service.detail(ctx, "cf:1"))
        assert "图片暂不可用" in fallback and contest.url in fallback and "private" not in fallback
    finally:
        await service.close()


def test_measured_render_pages_are_bounded_and_include_all_contests(monkeypatch):
    pytest.importorskip("pixie")
    items = [
        replace(SAMPLE, key=f"cf:{i}", name="示例公开赛与非常长的中英文标题 Example Round " * 4)
        for i in range(13)
    ]
    model = {
        "title": "近日算法竞赛",
        "now": NOW,
        "items": [asdict(c) for c in items],
        "warnings": ["牛客暂时无法获取。"],
    }
    backend = ContestBackend()
    # Fit one full item plus the original Tips/footer, but force overflow for
    # the collection without depending on the footer's exact height.
    _, single_height = backend.measure({**model, "items": model["items"][:1]})
    height_limit = single_height + 100
    monkeypatch.setattr("framework.views.contests.MAX_HEIGHT", height_limit)
    seen, palettes = [], []
    draw = backend.draw

    def record(page):
        seen.extend(c["key"] for c in page["items"])
        palettes.append(page["_gradient"])
        return draw(page)

    monkeypatch.setattr(backend, "draw", record)
    pages = backend.render_pages(model)
    assert seen == [c.key for c in items]
    assert all(gradient is palettes[0] for gradient in palettes)
    assert len(pages) >= 2 and all(p.height <= height_limit and p.width <= 8192 for p in pages)
    assert all(p.data.startswith(b"\x89PNG") for p in pages)
    assert all(p.width * p.height <= 24_000_000 for p in pages)


def test_lists_use_three_columns_without_splitting_at_twelve():
    pytest.importorskip("pixie")
    model = {
        "title": "近日算法竞赛",
        "now": NOW,
        "items": [asdict(replace(SAMPLE, key=f"cf:{i}")) for i in range(17)],
    }
    pages = ContestBackend().render_pages(model)
    assert len(pages) == 1 and pages[0].width == 3388


@pytest.mark.parametrize("count,columns", [(1, 1), (5, 1), (6, 2), (12, 2), (13, 3)])
def test_contest_column_count_follows_original_phase_thresholds(count, columns):
    pytest.importorskip("pixie")
    model = {"title": "比赛", "now": NOW, "items": [asdict(SAMPLE)] * count}
    width, _ = ContestBackend().measure(model)
    assert width == 256 + columns * 916 + (columns - 1) * 192


def test_contest_phases_use_largest_group_and_original_typography():
    pytest.importorskip("pixie")
    items = [
        asdict(replace(SAMPLE, start=start, duration=3600))
        for start in (NOW - 7200, NOW - 60, NOW + 3600)
        for _ in range(5)
    ]
    renderer = ContestBackend().renderer({"title": "比赛", "now": NOW, "items": items})
    assert renderer.measure()[0] == 1172
    body = renderer.sections[1]
    assert [len(body.section_running), len(body.section_upcoming), len(body.section_finished)] == [5, 5, 5]
    assert body.str_running_title.font.size == 52
    item = body.section_running[0]
    assert item.str_idx.font.size == 78 and item.str_title.font.size == 56
    assert item.str_time.font.size == 32 and item.str_title.padding_bottom == 12
    assert item._elapsed_ratio == pytest.approx(60 / 3600)


def test_detail_layout_long_fields_stay_inside_fixed_width_card():
    pytest.importorskip("pixie")
    from framework.views.contest_detail import ContestDetailRenderer

    contest = replace(SAMPLE, name="跨平台竞赛 " * 20, supplement="比赛说明 " * 30, start=NOW - 60)
    model = {
        "title": "近似匹配的比赛",
        "detail": True,
        "now": NOW,
        "items": [asdict(contest)],
        "warnings": ["牛客暂时无法获取。"],
    }
    width, height, blocks, bars, footer, bottom = ContestDetailRenderer().detail_layout(model)
    assert width == 1472 and bottom + footer.height < height
    assert all(x + block.max_width <= width and y + block.height < bottom for block, x, y in blocks)
    assert len(bars) == 1 and 0 < bars[0][-1] < 1
    image = ContestBackend().render(model)
    assert image.data.startswith(b"\x89PNG") and image.height == height


def test_native_contest_commands():
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.contests_probe"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        capture_output=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
