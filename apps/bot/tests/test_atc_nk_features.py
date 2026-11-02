"""Targeted unit tests for AtCoder Kenkoooo problem randomizer and Nowcoder standings."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from framework.clients.atcoder import AtcoderClient
from framework.clients.nowcoder import NowcoderClient
from framework.clients.http import UpstreamError
from framework.contracts import Actor, RequestContext, SceneKind, SceneRef


def make_context(user_id: str = "test-user") -> RequestContext:
    actor = Actor(user_id, f"qq:bot:c2c_user:{user_id}")
    scene = SceneRef("qq", "bot", SceneKind.C2C, user_id)
    return RequestContext(actor=actor, scene=scene, event_id="ev-1")


MOCK_PROBLEMS = [
    {
        "id": "abc138_a",
        "contest_id": "abc138",
        "problem_index": "A",
        "name": "Red or Not",
        "title": "A. Red or Not",
    },
    {
        "id": "abc138_e",
        "contest_id": "abc138",
        "problem_index": "E",
        "name": "Strings of Impurity",
        "title": "E. Strings of Impurity",
    },
    {
        "id": "arc100_a",
        "contest_id": "arc100",
        "problem_index": "C",
        "name": "Linear Approximation",
        "title": "C. Linear Approximation",
    },
    {
        "id": "agc001_a",
        "contest_id": "agc001",
        "problem_index": "A",
        "name": "BBQ Easy",
        "title": "A. BBQ Easy",
    },
]

MOCK_MODELS = {
    "abc138_a": {"difficulty": -848},
    "abc138_e": {"difficulty": 1257},
    "arc100_a": {"difficulty": 1050},
    "agc001_a": {"difficulty": 300},
}


@pytest.fixture
def mock_http():
    http = AsyncMock()
    return http


@pytest.fixture
def atc_client(mock_http):
    return AtcoderClient(mock_http)


@pytest.fixture
def nk_client(mock_http):
    return NowcoderClient(mock_http)


# ---------------- AtCoder Tests ----------------

def test_atc_parse_difficulty_range():
    assert AtcoderClient.parse_difficulty_range(None) == (None, None)
    assert AtcoderClient.parse_difficulty_range("") == (None, None)
    assert AtcoderClient.parse_difficulty_range("1200") == (1200, 1200)
    assert AtcoderClient.parse_difficulty_range("1200-1600") == (1200, 1600)
    assert AtcoderClient.parse_difficulty_range("-800") == (-800, -800)
    assert AtcoderClient.parse_difficulty_range("-800-500") == (-800, 500)
    assert AtcoderClient.parse_difficulty_range("-800--500") == (-800, -500)
    assert AtcoderClient.parse_difficulty_range("-800-") == (-800, None)
    assert AtcoderClient.parse_difficulty_range("1000-") == (1000, None)

    with pytest.raises(ValueError, match="难度格式错误|难度区间格式错误"):
        AtcoderClient.parse_difficulty_range("abc")


@pytest.mark.asyncio
async def test_atc_pick_problem_filtering(atc_client, mock_http):
    mock_http.get_json.side_effect = [MOCK_PROBLEMS, MOCK_MODELS]

    # Pick ABC with diff around 1200
    prob = await atc_client.pick_problem("abc", "1200-1300")
    assert prob is not None
    assert prob["id"] == "abc138_e"
    assert prob["contest_id"] == "abc138"
    assert prob["difficulty"] == 1257
    assert prob["url"] == "https://atcoder.jp/contests/abc138/tasks/abc138_e"

    # Pick ARC
    prob_arc = await atc_client.pick_problem("arc", None)
    assert prob_arc is not None
    assert prob_arc["id"] == "arc100_a"
    assert prob_arc["contest_id"] == "arc100"

    # Pick impossible range
    prob_none = await atc_client.pick_problem("abc", "3000-4000")
    assert prob_none is None

    # Invalid contest type
    with pytest.raises(ValueError, match="比赛类型仅支持"):
        await atc_client.pick_problem("invalid_type", None)


@pytest.mark.asyncio
async def test_atc_pick_problem_upstream_error(atc_client, mock_http):
    mock_http.get_json.side_effect = Exception("Network error")
    with pytest.raises(Exception):
        await atc_client.pick_problem("abc", None)


# ---------------- Nowcoder Standings Tests ----------------

MOCK_STANDINGS_RESPONSE = {
    "msg": "OK",
    "code": 0,
    "data": {
        "basicInfo": {
            "contestId": 81596,
            "rankType": "ICPC",
            "pageCount": 1,
            "rankCount": 2,
        },
        "rankData": [
            {
                "ranking": 1,
                "userName": "HoMaMaOvO",
                "school": "東京大学",
                "team": True,
                "acceptedCount": 11,
                "penaltyTime": 55537000,
            },
            {
                "ranking": 2,
                "userName": "tourist",
                "school": "ITMO",
                "team": False,
                "acceptedCount": 10,
                "penaltyTime": 60000000,
            },
        ],
    },
}


@pytest.mark.asyncio
async def test_nk_standings_fetch_and_formatting(nk_client, mock_http):
    mock_http.get_json.return_value = MOCK_STANDINGS_RESPONSE

    res = await nk_client.standings(81596, search_user_name="tourist")
    assert res["contest_id"] == "81596"
    assert res["rank_type"] == "ICPC"
    assert len(res["rank_data"]) == 2

    # Verify query params
    mock_http.get_json.assert_awaited_with(
        "https://ac.nowcoder.com/acm-heavy/acm/contest/real-time-rank-data",
        params={"id": "81596", "limit": 0, "searchUserName": "tourist"},
    )

    # Format ICPC
    entry1 = nk_client.format_standing_entry(res["rank_data"][0], rank_type="ICPC")
    assert "#1 HoMaMaOvO @東京大学" in entry1
    assert "团队，通过 11 题，罚时 925" in entry1

    entry2 = nk_client.format_standing_entry(res["rank_data"][1], rank_type="ICPC")
    assert "#2 tourist @ITMO" in entry2
    assert "个人，通过 10 题，罚时 1000" in entry2

    # Format OI / Score
    entry_oi = nk_client.format_standing_entry(
        {"ranking": 5, "userName": "Alice", "school": "", "team": False, "acceptedCount": 3, "totalScore": 300},
        rank_type="OI",
    )
    assert "#5 Alice\n个人，通过 3 题，总分 300" in entry_oi


@pytest.mark.asyncio
async def test_nk_standings_upstream_error(nk_client, mock_http):
    mock_http.get_json.return_value = {"msg": "FAIL", "code": 500}
    with pytest.raises(UpstreamError, match="Nowcoder API error: FAIL"):
        await nk_client.standings(81596)

    # Invalid rankData entries
    mock_http.get_json.return_value = {
        "msg": "OK",
        "code": 0,
        "data": {
            "basicInfo": {},
            "rankData": ["not-a-dict"],
        },
    }
    with pytest.raises(UpstreamError, match="invalid rankData entries"):
        await nk_client.standings(81596)
