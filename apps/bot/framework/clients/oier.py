"""Public CPCFinder OI API; no OJ login or local contestant database."""

import math
from dataclasses import dataclass
from urllib.parse import urlencode

from .base import Client
from .http import UpstreamError

BASE_URL = "https://cpcfinder.com/api/oi/student"
PAGE_SIZE = 2000
MAX_PAGES = 5


@dataclass(frozen=True)
class OIStudent:
    id: int
    name: str
    province: str
    graduation_year: int | None
    rating: float
    rank: int | None


@dataclass(frozen=True)
class OIAward:
    contest_name: str
    contest_type: str
    year: int
    school: str
    grade: str
    medal: str


def _text(item, key):
    value = item.get(key) or ""
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    return value.strip()


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Invalid positive integer")
    return value


def _student(item):
    rating = float(item.get("rating") or 0)
    if not math.isfinite(rating):
        raise ValueError("Invalid rating")
    name = _text(item, "name")
    if not name:
        raise ValueError("Missing name")
    return OIStudent(
        _integer(item["oiStudentId"]),
        name,
        _text(item, "province"),
        _integer(item["graduationYear"]) if item.get("graduationYear") else None,
        rating,
        _integer(item["rank"]) if item.get("rank") else None,
    )


class OIerClient(Client):
    dependencies = ("clients.http",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.http)

    def __init__(self, http):
        self.http = http

    async def _get(self, suffix="", *, params=None):
        data = await self.http.get_json(BASE_URL + suffix, params=params)
        if not isinstance(data, dict) or "data" not in data:
            raise UpstreamError("CPCFinder OI malformed response")
        return data

    async def search(self, name):
        name = name.strip()
        if not name:
            return [], True  # An empty query would enumerate the entire database.
        students = {}
        try:
            for page in range(1, MAX_PAGES + 1):
                params = dict(name=name, current=page, pageSize=PAGE_SIZE)
                data = await self._get("?" + urlencode(params))
                items = data["data"]
                if not isinstance(items, list):
                    raise ValueError("Invalid candidates")
                for item in items:
                    student = _student(item)
                    if student.name.casefold() == name.casefold():
                        students[student.id] = student
                pagination = data.get("pagination") or {}
                pages = pagination.get("pages", 1)
                if isinstance(pages, bool) or not isinstance(pages, int) or pages < 0:
                    raise ValueError("Invalid pagination")
                if page >= pages:
                    break
            return sorted(students.values(), key=lambda s: (-s.rating, s.id)), page >= pages
        except (KeyError, TypeError, ValueError, AttributeError):
            raise UpstreamError("CPCFinder OI malformed search") from None

    async def detail(self, student_id):
        student_id = _integer(student_id)
        data = await self._get(f"/{student_id}")
        try:
            student = _student(data["data"])
            if student.id != student_id:
                raise ValueError("Mismatched student")
            return student
        except (KeyError, TypeError, ValueError, AttributeError):
            raise UpstreamError("CPCFinder OI malformed detail") from None

    async def awards(self, student_id):
        student_id = _integer(student_id)
        data = await self._get(f"/{student_id}/participations")
        try:
            if not isinstance(data["data"], list):
                raise ValueError("Invalid participations")
            result = []
            for item in data["data"]:
                year = item.get("contestYear")
                if year is None:
                    date = _text(item, "eventDate")
                    year = int(date[:4]) if date else 0
                if isinstance(year, bool) or not isinstance(year, int) or year < 0:
                    raise ValueError("Invalid year")
                kind = _text(item, "contestType")
                kind = {"CTSC": "CTS", "NOIP": "NOIP提高"}.get(kind, kind)
                medal = _text(item, "medal")
                if kind in {"IOI", "NOI", "WC", "CTS", "APIO"}:
                    medal = {
                        "一等奖": "金牌",
                        "二等奖": "银牌",
                        "三等奖": "铜牌",
                        "国际金牌": "金牌",
                        "国际银牌": "银牌",
                    }.get(medal, medal)
                result.append(
                    OIAward(
                        _text(item, "contestName"),
                        kind,
                        year,
                        _text(item, "schoolName"),
                        _text(item, "grade"),
                        medal,
                    )
                )
            return result
        except (KeyError, TypeError, ValueError, AttributeError):
            raise UpstreamError("CPCFinder OI malformed participations") from None
