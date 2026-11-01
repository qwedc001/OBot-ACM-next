"""CPCFinder API client for XCPC contestant, school, team and award queries."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .base import Client
from .http import HttpClient, UpstreamError

BASE_URL = "https://cpcfinder.com/api"


@dataclass(frozen=True)
class CPCStudentCandidate:
    student_id: str
    name: str
    school_name: str
    gold: int
    silver: int
    bronze: int
    rating: float
    rank: int | None
    latest_event_date: str | None


@dataclass(frozen=True)
class CPCStudentDetail:
    student_id: str
    name: str
    school_name: str
    champion: int
    second: int
    third: int
    gold: int
    silver: int
    bronze: int
    rating: float
    rank: int | None


@dataclass(frozen=True)
class CPCAwardItem:
    award_id: int
    contest_id: int
    contest_name: str
    team_id: int | None
    team_name: str
    medal: str
    medal_type: str
    date: str
    place: str
    rank: int
    official: bool
    official_rank: int
    school_rank: int | None
    total_teams: int | None


@dataclass(frozen=True)
class CPCSchoolCandidate:
    school_id: str
    name: str
    champion: int
    second: int
    third: int
    gold: int
    silver: int
    bronze: int
    max_rank: int | None
    rating: float


class CPCFinderClient(Client):
    dependencies = ("clients.http",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http: HttpClient):
        self._http = http

    async def search_students(
        self, name: str, school: str | None = None, page_size: int = 20
    ) -> list[CPCStudentCandidate]:
        """Search student candidates by name and optional school."""
        params: dict[str, Any] = {"name": name.strip()}
        if school:
            params["school"] = school.strip()
        params["pageSize"] = page_size
        data = await self._get(f"{BASE_URL}/student?{urlencode(params)}")
        items = data.get("data") or []
        results = []
        for item in items:
            results.append(
                CPCStudentCandidate(
                    student_id=str(item.get("studentId", "")),
                    name=str(item.get("name", "")),
                    school_name=str(item.get("schoolName", "")),
                    gold=int(item.get("goldCount", 0) or 0),
                    silver=int(item.get("silverCount", 0) or 0),
                    bronze=int(item.get("bronzeCount", 0) or 0),
                    rating=float(item.get("rating", 0.0) or 0.0),
                    rank=item.get("rank"),
                    latest_event_date=item.get("latestEventDate"),
                )
            )
        return results

    async def get_student_detail(self, student_id: str) -> CPCStudentDetail | None:
        """Fetch student detail profile."""
        data = await self._get(f"{BASE_URL}/student/{student_id}")
        item = data.get("data")
        if not item:
            return None
        return CPCStudentDetail(
            student_id=str(item.get("studentId", "")),
            name=str(item.get("name", "")),
            school_name=str(item.get("schoolName", "")),
            champion=int(item.get("championCount", 0) or 0),
            second=int(item.get("secCount", 0) or 0),
            third=int(item.get("thiCount", 0) or 0),
            gold=int(item.get("goldCount", 0) or 0),
            silver=int(item.get("silverCount", 0) or 0),
            bronze=int(item.get("bronzeCount", 0) or 0),
            rating=float(item.get("rating", 0.0) or 0.0),
            rank=item.get("rank"),
        )

    async def get_student_awards(self, student_id: str) -> list[CPCAwardItem]:
        """Fetch student awards history."""
        data = await self._get(f"{BASE_URL}/student/{student_id}/awards")
        items = data.get("data") or []
        return [self._parse_award(item) for item in items]

    async def search_schools(self, school: str, page_size: int = 10) -> list[CPCSchoolCandidate]:
        """Search school stats by name/alias."""
        params = {"school": school.strip(), "pageSize": page_size}
        data = await self._get(f"{BASE_URL}/school?{urlencode(params)}")
        items = data.get("data") or []
        results = []
        for item in items:
            results.append(
                CPCSchoolCandidate(
                    school_id=str(item.get("schoolId", "")),
                    name=str(item.get("name", "")),
                    champion=int(item.get("championCount", 0) or 0),
                    second=int(item.get("secCount", 0) or 0),
                    third=int(item.get("thiCount", 0) or 0),
                    gold=int(item.get("goldCount", 0) or 0),
                    silver=int(item.get("silverCount", 0) or 0),
                    bronze=int(item.get("bronzeCount", 0) or 0),
                    max_rank=item.get("maxRank"),
                    rating=float(item.get("rating", 0.0) or 0.0),
                )
            )
        return results

    async def get_school_awards(self, school_id: str) -> list[CPCAwardItem]:
        """Fetch school awards history."""
        data = await self._get(f"{BASE_URL}/school/{school_id}/awards")
        items = data.get("data") or []
        return [self._parse_award(item) for item in items]

    async def _get(self, url: str) -> dict[str, Any]:
        try:
            resp = await self._http.request("GET", url)
            if resp.status_code != 200:
                raise UpstreamError(f"CPCFinder HTTP {resp.status_code}")
            return resp.json()
        except Exception as e:
            if isinstance(e, UpstreamError):
                raise
            raise UpstreamError(f"CPCFinder request failed: {e}") from e

    @staticmethod
    def _parse_award(item: dict[str, Any]) -> CPCAwardItem:
        official = bool(item.get("official", False))
        medal_type = str(item.get("medalType", "") or "NONE").upper()
        medal = str(item.get("medal", "") or "")
        if not medal:
            medal_map = {"GOLD": "金牌", "SILVER": "银牌", "BRONZE": "铜牌", "NONE": "铁牌"}
            medal = medal_map.get(medal_type, "铁牌") if official else "打星"
        return CPCAwardItem(
            award_id=int(item.get("awardId", 0) or 0),
            contest_id=int(item.get("contestId", 0) or 0),
            contest_name=str(item.get("contestName", "") or ""),
            team_id=int(item.get("teamId", 0)) if item.get("teamId") else None,
            team_name=str(item.get("teamName", "") or ""),
            medal=medal,
            medal_type=medal_type,
            date=str(item.get("date", "") or ""),
            place=str(item.get("place", "") or ""),
            rank=int(item.get("rank", 0) or 0),
            official=official,
            official_rank=int(item.get("officialRank", -1) or -1),
            school_rank=int(item.get("schoolRank", 0)) if item.get("schoolRank") is not None else None,
            total_teams=int(item.get("totalTeams", 0)) if item.get("totalTeams") is not None else None,
        )
