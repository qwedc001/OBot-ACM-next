import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from framework.clients.http import HttpClient, UpstreamError
from framework.clients.oier import OIAward, OIerClient, OIStudent
from framework.services.oier import OIerService, ccf_level


def student(id=1, name="测试选手", rating=12.5):
    return {
        "oiStudentId": id,
        "name": name,
        "rating": rating,
        "province": "测试省",
        "graduationYear": 2027,
        "rank": 3,
    }


async def test_exact_name_pagination_sort_deduplicate_and_encoding():
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.url.params["name"] == "测试选手"
        page = int(request.url.params["current"])
        items = [student(8, "测试选手甲", 900)] if page == 1 else [student(1), student(2, rating=90)]
        if page == 3:
            items = [student(2, rating=90), student(3, rating=100)]
        return httpx.Response(200, json={"data": items, "pagination": {"pages": 3}})

    http = HttpClient(transport=httpx.MockTransport(upstream))
    try:
        client = OIerClient(http)
        assert await client.search(" ") == ([], True)
        assert not requests
        candidates, complete = await client.search(" 测试选手 ")
        assert [s.id for s in candidates] == [3, 2, 1] and complete
        assert len(requests) == 3
    finally:
        await http.close()


async def test_search_cap_reports_incomplete():
    http = HttpClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": [student()], "pagination": {"pages": 99}})
        )
    )
    try:
        candidates, complete = await OIerClient(http).search("测试选手")
        assert len(candidates) == 1 and not complete
    finally:
        await http.close()


async def test_detail_and_awards_normalization_unknown_contest_preserved():
    def upstream(request):
        if request.url.path.endswith("participations"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "contestName": "2025 冬令营",
                            "contestType": "CTSC",
                            "eventDate": "2025-01-01",
                            "medal": "国际金牌",
                            "schoolName": None,
                            "grade": "高一",
                        },
                        {
                            "contestName": "2024 NOIP",
                            "contestType": "NOIP",
                            "contestYear": 2024,
                            "medal": "一等奖",
                        },
                        {"contestName": "新比赛", "contestType": "NEW", "medal": None},
                    ]
                },
            )
        return httpx.Response(200, json={"data": student()})

    http = HttpClient(transport=httpx.MockTransport(upstream))
    try:
        client = OIerClient(http)
        detail = await client.detail(1)
        assert detail == OIStudent(1, "测试选手", "测试省", 2027, 12.5, 3)
        awards = await client.awards(1)
        assert (awards[0].year, awards[0].contest_type, awards[0].medal, awards[0].school) == (
            2025,
            "CTS",
            "金牌",
            "",
        )
        assert awards[1].contest_type == "NOIP提高" and awards[2].contest_type == "NEW"
    finally:
        await http.close()


@pytest.mark.parametrize(
    "method,payload",
    [
        ("search", {}),
        ("search", {"data": None}),
        ("search", {"data": [None]}),
        ("search", {"data": [student(rating="bad")]}),
        ("search", {"data": [student(rating="NaN")]}),
        ("search", {"data": [], "pagination": {"pages": "3"}}),
        ("detail", {"data": student(2)}),
        ("detail", {"data": None}),
        ("awards", {"data": {}}),
        ("awards", {"data": [{"contestYear": "wrong"}]}),
        ("awards", {"data": [None]}),
    ],
)
async def test_malformed_response_has_safe_error(method, payload):
    http = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    try:
        with pytest.raises(UpstreamError, match="CPCFinder OI malformed"):
            await getattr(OIerClient(http), method)("测试选手" if method == "search" else 1)
    finally:
        await http.close()


@pytest.mark.parametrize(
    "kind,medal,expected",
    [
        ("NOI", "金牌", 10),
        ("NOI", "铜牌", 8),
        ("IOI", "银牌", 10),
        ("IOI", "Cu", 9),
        ("APIO", "Au", 10),
        ("WC", "Ag", 9),
        ("CSP提高", "一等奖", 6),
        ("NOIP提高", "三等奖", 4),
        ("CSP入门", "一等奖", 5),
        ("NOIP普及", "三等奖", 3),
        ("NEW", "金牌", 0),
    ],
)
def test_legacy_ccf_level(kind, medal, expected):
    assert ccf_level([OIAward("比赛", kind, 2025, "学校", "", medal)]) == expected


def service():
    return OIerService(
        AsyncMock(
            search=AsyncMock(
                return_value=(
                    [
                        OIStudent(1, "测试选手", "省", 2027, 20, 3),
                        OIStudent(2, "测试选手", "省", None, 10, None),
                    ],
                    True,
                )
            ),
            detail=AsyncMock(return_value=OIStudent(1, "测试选手", "省", 2027, 20, 3)),
            awards=AsyncMock(
                return_value=[
                    OIAward("旧 NOI", "NOI", 2024, "测试校", "高一", "金牌"),
                    OIAward("新 NOI", "NOI", 2025, "测试校", "高二", "银牌"),
                    OIAward("新类型", "NEW", 2025, "", "", ""),
                ]
            ),
        )
    )


async def test_single_profile_same_names_order_and_unknown_types():
    svc = service()
    text = await svc.reply(["测试选手"])
    assert "共 2 名" in text and "最高" in text and "2027届" in text and "#3" in text
    assert "估算）: 10" in text and text.index("新 NOI") < text.index("旧 NOI")
    assert "[未知] 新类型" in text and "未知学校" in text
    svc.client.detail.assert_awaited_once_with(1)


async def test_batch_errors_are_per_name_and_count_limit_is_pre_io():
    svc = service()
    assert "1–3" in await svc.reply(["a", "b", "c", "d"])
    assert "1–3" in await svc.reply([])
    assert "80" in await svc.reply(["x" * 81])
    svc.client.search.assert_not_awaited()
    svc.client.search.side_effect = [
        UpstreamError("secret body"),
        ([], True),
        ([OIStudent(1, "测试选手", "", None, 0, None)], False),
    ]
    text = await svc.reply(["错误", "缺失", "测试选手"])
    assert "数据源暂时不可用" in text and "缺失：未找到" in text and "结果" not in text
    assert "secret" not in text and "已检索候选中" in text and "获奖: 3次" in text


async def test_empty_profile_and_incomplete_not_found():
    svc = service()
    svc.client.awards.return_value = []
    svc.client.detail.return_value = replace(svc.client.detail.return_value, rank=None, graduation_year=None)
    assert "暂无获奖记录" in await svc.reply(["测试选手"])
    svc.client.search.return_value = ([], False)
    assert "结果可能不完整" in await svc.reply(["未命中"])


@pytest.mark.parametrize("partial", [False, True])
async def test_overall_deadline_cancels_io_keeps_partial_results_and_stops_batch(monkeypatch, partial):
    monkeypatch.setattr("framework.services.oier.QUERY_TIMEOUT", 0.01)
    svc = service()
    cancelled = asyncio.Event()

    async def search(name):
        if name == "已完成":
            return [], True
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    svc.client.search.side_effect = search
    names = ["已完成", "等待", "未查询"] if partial else ["等待", "未查询"]
    text = await asyncio.wait_for(svc.reply(names), timeout=1)
    assert "查询超时" in text
    assert cancelled.is_set()
    assert svc.client.search.await_count == (2 if partial else 1)
    if partial:
        assert "已完成：未找到选手" in text
    svc.client.detail.assert_not_awaited()
    svc.client.awards.assert_not_awaited()
