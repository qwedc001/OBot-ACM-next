"""Targeted unit tests for CPCFinder client and service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from framework.clients.cpcfinder import (
    CPCAwardItem,
    CPCFinderClient,
    CPCSchoolCandidate,
    CPCStudentCandidate,
    CPCStudentDetail,
)
from framework.clients.http import HttpClient, UpstreamError
from framework.contracts import Actor, RequestContext, SceneKind, SceneRef
from framework.messages import Message
from framework.services.cpcfinder import CPCFinderService


@pytest.fixture
def mock_actor():
    return Actor(id="user_123", identity_key="qq:user_123")


@pytest.fixture
def mock_scene():
    return SceneRef(provider="qq", app_id="bot_001", kind=SceneKind.GROUP, external_id="group_456")


@pytest.fixture
def mock_context(mock_actor, mock_scene):
    return RequestContext(
        actor=mock_actor, scene=mock_scene, event_id="evt_001", correlation_id="req_test_001"
    )


@pytest.mark.asyncio
async def test_cpcfinder_client_search_students():
    http = MagicMock(spec=HttpClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": [
            {
                "studentId": "id-001",
                "name": "李欣鹏",
                "schoolName": "哈尔滨工业大学（威海）",
                "goldCount": 1,
                "silverCount": 3,
                "bronzeCount": 4,
                "rating": 628.9,
                "rank": 1462,
                "latestEventDate": "2026-10-18",
            }
        ]
    }
    http.request = AsyncMock(return_value=resp)

    client = CPCFinderClient(http)
    students = await client.search_students("李欣鹏", "哈尔滨工业大学")

    assert len(students) == 1
    s = students[0]
    assert s.student_id == "id-001"
    assert s.name == "李欣鹏"
    assert s.gold == 1
    assert s.silver == 3
    assert s.bronze == 4
    assert s.rating == 628.9


@pytest.mark.asyncio
async def test_cpcfinder_client_awards_and_school():
    http = MagicMock(spec=HttpClient)
    resp_awards = MagicMock()
    resp_awards.status_code = 200
    resp_awards.json.return_value = {
        "data": [
            {
                "awardId": 36060,
                "contestId": 79,
                "contestName": "第 51 届 ICPC 国际大学生程序设计竞赛亚洲区域赛西安站",
                "teamId": 38118,
                "teamName": "先生，这得花不少token",
                "medal": "金牌",
                "medalType": "GOLD",
                "date": "2026-10-18",
                "place": "西安",
                "rank": 33,
                "schoolRank": 24,
                "official": True,
                "officialRank": 29,
                "totalTeams": 418,
            }
        ]
    }
    resp_schools = MagicMock()
    resp_schools.status_code = 200
    resp_schools.json.return_value = {
        "data": [
            {
                "schoolId": "sch-001",
                "name": "北京大学",
                "championCount": 43,
                "secCount": 15,
                "thiCount": 6,
                "goldCount": 193,
                "silverCount": 32,
                "bronzeCount": 2,
                "maxRank": 1,
                "rating": 126728.6,
            }
        ]
    }

    async def mock_request(method, url):
        if "school" in url:
            return resp_schools
        return resp_awards

    http.request = AsyncMock(side_effect=mock_request)
    client = CPCFinderClient(http)

    awards = await client.get_student_awards("id-001")
    assert len(awards) == 1
    assert awards[0].medal == "金牌"
    assert awards[0].official is True
    assert awards[0].official_rank == 29

    schools = await client.search_schools("北京大学")
    assert len(schools) == 1
    assert schools[0].name == "北京大学"
    assert schools[0].gold == 193


@pytest.mark.asyncio
async def test_cpcfinder_service_single_student_text(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_students = AsyncMock(
        return_value=[
            CPCStudentCandidate(
                student_id="id-001",
                name="李欣鹏",
                school_name="哈尔滨工业大学（威海）",
                gold=1,
                silver=3,
                bronze=4,
                rating=628.9,
                rank=1462,
                latest_event_date="2026-10-18",
            )
        ]
    )
    client.get_student_detail = AsyncMock(
        return_value=CPCStudentDetail(
            student_id="id-001",
            name="李欣鹏",
            school_name="哈尔滨工业大学（威海）",
            champion=0,
            second=0,
            third=0,
            gold=1,
            silver=3,
            bronze=4,
            rating=628.9,
            rank=1462,
        )
    )
    client.get_student_awards = AsyncMock(
        return_value=[
            CPCAwardItem(
                award_id=36060,
                contest_id=79,
                contest_name="第 51 届 ICPC 国际大学生程序设计竞赛亚洲区域赛西安站",
                team_id=38118,
                team_name="先生，这得花不少token",
                medal="金牌",
                medal_type="GOLD",
                date="2026-10-18",
                place="西安",
                rank=33,
                official=True,
                official_rank=29,
                school_rank=24,
                total_teams=418,
            )
        ]
    )

    renderer = MagicMock()
    service = CPCFinderService(client, renderer)

    # 1. Plain text reply
    res = await service.reply(mock_context, "李欣鹏", image_available=False)
    assert isinstance(res, str)
    assert "[CPCFinder] 选手查询" in res
    assert "李欣鹏 / 哈尔滨工业大学（威海）" in res
    assert "金牌: 1 / 银牌: 3 / 铜牌: 4" in res
    assert "[金牌] 第 51 届 ICPC 国际大学生程序设计竞赛亚洲区域赛西安站" in res


@pytest.mark.asyncio
async def test_cpcfinder_service_card_rendering(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_students = AsyncMock(
        return_value=[
            CPCStudentCandidate(
                student_id="id-001",
                name="李欣鹏",
                school_name="哈尔滨工业大学（威海）",
                gold=1,
                silver=3,
                bronze=4,
                rating=628.9,
                rank=1462,
                latest_event_date="2026-10-18",
            )
        ]
    )
    client.get_student_detail = AsyncMock(
        return_value=CPCStudentDetail(
            student_id="id-001",
            name="李欣鹏",
            school_name="哈尔滨工业大学（威海）",
            champion=0,
            second=0,
            third=0,
            gold=1,
            silver=3,
            bronze=4,
            rating=628.9,
            rank=1462,
        )
    )
    client.get_student_awards = AsyncMock(return_value=[])

    renderer = MagicMock()
    mock_artifact = MagicMock()
    mock_artifact.id = "art-123"
    renderer.render = AsyncMock(return_value=mock_artifact)
    renderer.artifacts.read = AsyncMock(return_value=b"fake-image-bytes")

    service = CPCFinderService(client, renderer)

    res = await service.reply(mock_context, "李欣鹏", image_available=True)
    assert isinstance(res, Message)
    assert "CPCFinder · 李欣鹏" in str(res)


@pytest.mark.asyncio
async def test_cpcfinder_service_multiple_candidates(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_students = AsyncMock(
        return_value=[
            CPCStudentCandidate(
                student_id="id-001",
                name="张三",
                school_name="清华大学",
                gold=2,
                silver=1,
                bronze=0,
                rating=1500.0,
                rank=10,
                latest_event_date="2025-10-18",
            ),
            CPCStudentCandidate(
                student_id="id-002",
                name="张三",
                school_name="北京大学",
                gold=1,
                silver=2,
                bronze=1,
                rating=1400.0,
                rank=25,
                latest_event_date="2025-11-09",
            ),
        ]
    )

    renderer = MagicMock()
    service = CPCFinderService(client, renderer)

    res = await service.reply(mock_context, "张三", image_available=True)
    assert isinstance(res, str)
    assert "找到 2 个候选结果" in res
    assert "清华大学" in res
    assert "北京大学" in res


@pytest.mark.asyncio
async def test_cpcfinder_service_school_mode(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_schools = AsyncMock(
        return_value=[
            CPCSchoolCandidate(
                school_id="sch-pku",
                name="北京大学",
                champion=43,
                second=15,
                third=6,
                gold=193,
                silver=32,
                bronze=2,
                max_rank=1,
                rating=126728.6,
            )
        ]
    )
    client.get_school_awards = AsyncMock(return_value=[])

    renderer = MagicMock()
    service = CPCFinderService(client, renderer)

    res = await service.reply(mock_context, "北京大学", mode="school", image_available=False)
    assert isinstance(res, str)
    assert "[CPCFinder] 高校战绩查询" in res
    assert "北京大学" in res
    assert "金牌: 193" in res


@pytest.mark.asyncio
async def test_cpcfinder_service_upstream_error(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_students = AsyncMock(side_effect=UpstreamError("network down"))

    renderer = MagicMock()
    service = CPCFinderService(client, renderer)

    res = await service.reply(mock_context, "任何人", image_available=True)
    assert "CPCFinder 接口暂时无法访问" in res


@pytest.mark.asyncio
async def test_cpcfinder_service_school_disambiguation(mock_context):
    client = MagicMock(spec=CPCFinderClient)
    client.search_schools = AsyncMock(
        return_value=[
            CPCSchoolCandidate("s1", "北京大学", 1, 0, 0, 10, 2, 0, 1, 1000.0),
            CPCSchoolCandidate("s2", "北京航空航天大学", 0, 1, 0, 5, 5, 1, 5, 500.0),
        ]
    )
    renderer = MagicMock()
    service = CPCFinderService(client, renderer)

    target, awards, candidates = await service.query_school("北京")
    assert target is None
    assert len(candidates) == 2

    res = await service.reply(mock_context, "北京", mode="school", image_available=False)
    assert "找到 2 所匹配学校" in res
    assert "北京大学" in res
    assert "北京航空航天大学" in res
