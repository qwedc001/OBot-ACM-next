"""Unit tests for Codeforces problem retrieval, tag filtering and recent submissions (T03)."""

from unittest.mock import AsyncMock

import pytest

from framework.clients.codeforces import CodeforcesClient
from framework.contracts import Actor, RequestContext, SceneKind, SceneRef
from framework.profiles import ProfileNotFound
from framework.services.cf_problem import CFProblemService, decode_range

TEST_USER_ID = "test-user-cf-prob"


def make_context(user_id: str = TEST_USER_ID) -> RequestContext:
    actor = Actor(user_id, f"qq:bot:c2c_user:{user_id}")
    scene = SceneRef("qq", "bot", SceneKind.C2C, user_id)
    return RequestContext(actor=actor, scene=scene, event_id="ev-prob-1")


@pytest.fixture
def sample_problemset():
    return [
        {"contestId": 1800, "index": "A", "name": "Is It a Cat?", "rating": 800, "tags": ["strings", "implementation"]},
        {"contestId": 1800, "index": "B", "name": "Count the Number of Pairs", "rating": 1000, "tags": ["greedy", "strings"]},
        {"contestId": 1800, "index": "C", "name": "Setting up Camp", "rating": 1200, "tags": ["math", "greedy"]},
        {"contestId": 1800, "index": "D", "name": "Remove Two Letters", "rating": 1500, "tags": ["strings", "hashing"]},
        {"contestId": 900, "index": "A", "name": "Find Extra One", "rating": 900, "tags": ["geometry"]},
    ]


@pytest.fixture
def sample_submissions():
    return [
        {
            "id": 12345678,
            "contestId": 1800,
            "creationTimeSeconds": 1700000000,
            "timeConsumedMillis": 15,
            "verdict": "OK",
            "passedTestCount": 10,
            "problem": {
                "contestId": 1800,
                "index": "A",
                "name": "Is It a Cat?",
                "rating": 800,
                "tags": ["strings"],
            },
        },
        {
            "id": 12345677,
            "contestId": 1800,
            "creationTimeSeconds": 1699999000,
            "timeConsumedMillis": 30,
            "verdict": "WRONG_ANSWER",
            "passedTestCount": 3,
            "problem": {
                "contestId": 1800,
                "index": "B",
                "name": "Count the Number of Pairs",
                "rating": 1000,
                "tags": ["greedy"],
            },
        },
    ]


@pytest.fixture
def mock_cf_client(sample_problemset, sample_submissions):
    client = AsyncMock(spec=CodeforcesClient)
    client.get_problemset = AsyncMock(return_value=sample_problemset)
    client.get_user_submissions = AsyncMock(return_value=sample_submissions)
    return client


@pytest.fixture
async def cf_problem_service(mock_cf_client):
    service = CFProblemService(mock_cf_client, cache_ttl=60, sync_interval=3600)
    await service.start()
    try:
        yield service
    finally:
        await service.close()


def test_decode_range():
    assert decode_range("1500") == (1500, 1500)
    assert decode_range("1400-1600") == (1400, 1600)
    assert decode_range("1800-1200") == (1200, 1800)
    assert decode_range("invalid") == (-1, -1)
    assert decode_range("10-20") == (-1, -1)  # Length not between 3 and 4
    assert decode_range("800-10000") == (-1, -1)
    assert decode_range("") == (0, 0)


@pytest.mark.asyncio
async def test_pick_problem_all_conditions(cf_problem_service, sample_problemset):
    # 1. Random pick without filters
    ok, msg, prob = await cf_problem_service.pick_problem()
    assert ok
    assert prob is not None
    assert "[Codeforces] 随机选题" in msg
    assert "P" in msg
    assert "https://codeforces.com/contest/" in msg

    # 2. Pick by tag
    ok, msg, prob = await cf_problem_service.pick_problem(tag="math")
    assert ok
    assert prob is not None
    assert prob["index"] == "C"

    # 3. Pick by fuzzy tag
    ok, msg, prob = await cf_problem_service.pick_problem(tag="strngs")
    assert ok
    assert prob is not None
    assert "strings" in prob["tags"]
    assert "已将标签匹配为 strings" in msg

    # 4. Pick by rating
    ok, msg, prob = await cf_problem_service.pick_problem(rating_range="1500")
    assert ok
    assert prob["rating"] == 1500
    assert prob["index"] == "D"

    # 5. Pick by rating range
    ok, msg, prob = await cf_problem_service.pick_problem(rating_range="800-1100")
    assert ok
    assert prob["rating"] in (800, 900, 1000)

    # 6. Pick newer only (contestId >= 1000)
    ok, msg, prob = await cf_problem_service.pick_problem(tag="geometry", newer_only=True)
    assert not ok
    assert "未找到满足条件的题目" in msg

    # 8. User without rating in range
    ok, msg, prob = await cf_problem_service.pick_problem(rating_range="2500")
    assert not ok
    assert "未找到满足条件的题目" in msg


@pytest.mark.asyncio
async def test_cf_problem_command_matcher(mock_cf_client, sample_problemset, sample_submissions):
    service = CFProblemService(mock_cf_client, cache_ttl=60)
    
    # Test pick problem service call
    ok, msg, prob = await service.pick_problem(tag="strings", rating_range="800-1000", newer_only=True)
    assert ok
    assert prob["index"] in ("A", "B")
    assert "P1800" in msg
    assert "https://codeforces.com/contest/" in msg

    # Test recent service call
    ok, msg = await service.get_recent_submissions("tourist", 2)
    assert ok
    assert "P1800A" in msg
    assert "P1800B" in msg
    await service.close()


@pytest.mark.asyncio
async def test_problemset_lru_cache_and_background_sync(mock_cf_client):
    # Test caching without starting background periodic sync task immediately
    service = CFProblemService(mock_cf_client, cache_ttl=10, sync_interval=100)
    try:
        # First call fetches from client
        res1 = await service.get_problemset()
        assert len(res1) == 5
        assert mock_cf_client.get_problemset.call_count == 1

        # Second call hits LRU cache
        res2 = await service.get_problemset()
        assert res2 == res1
        assert mock_cf_client.get_problemset.call_count == 1

        # Refresh forces refetch
        res3 = await service.get_problemset(refresh=True)
        assert res3 == res1
        assert mock_cf_client.get_problemset.call_count == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_get_recent_submissions(cf_problem_service, mock_cf_client):
    # 1. Existing user with submissions
    ok, msg = await cf_problem_service.get_recent_submissions("tourist", count=2)
    assert ok
    assert "[Codeforces] tourist" in msg
    assert "最近 2 发提交:" in msg
    assert "P1800A *800 · AC" in msg
    assert "P1800B *1000 · WA on test 4" in msg

    # 2. User not found
    mock_cf_client.get_user_submissions.side_effect = ProfileNotFound()
    ok, msg = await cf_problem_service.get_recent_submissions("UnknownUser")
    assert not ok
    assert "用户不存在" in msg

    # 3. User with no submissions
    mock_cf_client.get_user_submissions.side_effect = None
    mock_cf_client.get_user_submissions.return_value = []
    ok, msg = await cf_problem_service.get_recent_submissions("Newbie")
    assert ok
    assert "还未提交过题目" in msg
