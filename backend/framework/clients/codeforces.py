"""Codeforces profiles, rating history and submissions share one API rate gate."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from time import monotonic, time
from urllib.parse import quote

from ..profiles import PROFILE_TIMEZONE, Profile, ProfileNotFound, ProfileSection, clean, date_text
from ._contests import codeforces_contests
from ._profiles import ProfileClient, optional
from .http import UpstreamError

VERDICTS = {
    "OK": "AC",
    "WRONG_ANSWER": "WA",
    "TIME_LIMIT_EXCEEDED": "TLE",
    "MEMORY_LIMIT_EXCEEDED": "MLE",
    "RUNTIME_ERROR": "RE",
    "COMPILATION_ERROR": "CE",
    "TESTING": "Testing",
    "CHALLENGED": "Hacked",
    "SKIPPED": "Skipped",
}


class CodeforcesClient(ProfileClient):
    pattern = r"[A-Za-z0-9_.-]{1,24}"
    hint = "CF 账号应为不超过 24 位的英文字母、数字、下划线、点或短横线。"

    async def contests(self):
        rows = await self._api("contest.list", gym="false")
        return await asyncio.to_thread(codeforces_contests, rows)

    def __init__(self, *args, interval=2.1, **kwargs):
        super().__init__(*args, **kwargs)
        self.interval = interval
        self._gate = asyncio.Lock()
        self._next_request = 0.0

    async def _api(self, method, **params):
        async with self._gate:
            # Windows timers may wake early; every actual request uses this gate.
            while (delay := self._next_request - monotonic()) > 0:
                await asyncio.sleep(delay)
            self._next_request = monotonic() + self.interval
            data = await self.http.get_json(
                "https://codeforces.com/api/" + method,
                params={**params, "lang": "en"},
                accepted_statuses=frozenset({400}),
                attempts=1,
            )
        self.check(data)
        return data["result"]

    @staticmethod
    def check(data):
        if isinstance(data, dict) and data.get("status") == "FAILED":
            if "not found" in str(data.get("comment", "")).casefold():
                raise ProfileNotFound
            raise UpstreamError("Codeforces: API rejected query")
        if not isinstance(data, dict) or data.get("status") != "OK" or "result" not in data:
            raise UpstreamError("Codeforces: invalid response")

    async def _fetch(self, handle: str) -> Profile:
        profile = self.parse({"status": "OK", "result": await self._api("user.info", handles=handle)})
        history, status = await asyncio.gather(
            optional(lambda: self._api("user.rating", handle=profile.handle)),
            optional(lambda: self._api("user.status", handle=profile.handle)),
        )
        return await asyncio.to_thread(self.enrich, profile, history, status)

    @staticmethod
    def parse(data) -> Profile:
        CodeforcesClient.check(data)
        users = data.get("result")
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise UpstreamError("Codeforces: invalid user data")
        user = users[0]
        handle, rating = user.get("handle"), user.get("rating")
        if not isinstance(handle, str) or not handle or (rating is not None and type(rating) is not int):
            raise UpstreamError("Codeforces: invalid user fields")
        metrics = tuple(
            (label, str(user[key]))
            for key, label in (("contribution", "贡献"), ("friendOfCount", "粉丝"))
            if type(user.get(key)) is int
        )
        details = [
            clean(" ".join(str(user.get(key) or "") for key in keys))
            for keys in (("firstName", "lastName"), ("country", "city"), ("organization",))
        ]
        timeline = []
        for key, label in (("registrationTimeSeconds", "注册于"), ("lastOnlineTimeSeconds", "最近在线")):
            value = user.get(key)
            if type(value) is int and 0 <= value <= 253402300799:
                timeline.append(
                    label + " " + datetime.fromtimestamp(value, PROFILE_TIMEZONE).strftime("%Y/%m/%d")
                )
        avatar = user.get("titlePhoto") or user.get("avatar")
        if isinstance(avatar, str):
            avatar = avatar.replace(
                "https://userpic.codeforces.org", "https://codeforces.com/userpic.codeforces.org"
            )
        return Profile(
            "Codeforces",
            clean(handle, 64),
            "https://codeforces.com/profile/" + quote(handle, safe=""),
            rating,
            clean(user.get("rank") or "Unrated").title(),
            metrics,
            tuple(value for value in details if value),
            avatar_url=avatar if isinstance(avatar, str) else None,
            rating_note=f"最高 Rating {user['maxRating']}" if type(user.get("maxRating")) is int else "",
            timeline=tuple(timeline),
        )

    @staticmethod
    def enrich(profile, history, status, *, now=None):
        metrics, sections, warnings = [], [], []
        if isinstance(status, list) and all(
            isinstance(row, dict)
            and isinstance(row.get("problem"), dict)
            and type(row.get("creationTimeSeconds")) is int
            and isinstance(row["problem"].get("index", ""), str)
            and isinstance(row["problem"].get("problemsetName", ""), str)
            and (row["problem"].get("contestId") is None or type(row["problem"]["contestId"]) is int)
            for row in status
        ):
            now = datetime.fromtimestamp(time() if now is None else now, PROFILE_TIMEZONE)
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week = today - timedelta(days=today.weekday())
            solved, daily, weekly = set(), set(), set()
            for submit in status:
                problem = submit.get("problem", {})
                if submit.get("verdict") != "OK" or not problem.get("index"):
                    continue
                # A problem can recur in several submissions; practice and
                # contest ACs count once. Preserve named problemsets too.
                key = (problem.get("problemsetName", ""), problem.get("contestId"), problem["index"])
                solved.add(key)
                at = submit.get("creationTimeSeconds", 0)
                if at >= today.timestamp():
                    daily.add(key)
                if at >= week.timestamp():
                    weekly.add(key)
            metrics += [
                ("通过题数", str(len(solved))),
                ("今日过题", str(len(daily))),
                ("本周过题", str(len(weekly))),
            ]
            lines = tuple(CodeforcesClient.submit_text(row) for row in status[:3])
            sections.append(ProfileSection("最近提交", lines or ("还未提交过题目",)))
        else:
            warnings.append("最近提交与过题统计暂时无法获取。")
        if isinstance(history, list) and all(
            isinstance(row, dict) and type(row.get("newRating")) is int and type(row.get("oldRating")) is int
            for row in history
        ):
            metrics.insert(1, ("Rated 比赛数", str(len(history))))
            lines = ("还未参加过 Rated 比赛",)
            if history:
                last = history[-1]
                lines = (
                    clean(last.get("contestName")),
                    f"位次 {last.get('rank', '—')} · 比赛编号 {last.get('contestId', '—')} · Rating {int(last['newRating']) - int(last['oldRating']):+d}",
                )
            sections.insert(0, ProfileSection("最近比赛", lines))
        else:
            warnings.append("最近比赛暂时无法获取。")
        return replace(
            profile,
            metrics=tuple(metrics) + profile.metrics,
            sections=tuple(sections),
            warnings=tuple(warnings),
        )

    @staticmethod
    def submit_text(row):
        problem = row.get("problem", {})
        identifier = f"P{problem.get('contestId', '')}{problem.get('index', '?')}"
        if problem.get("rating") is not None:
            identifier += f" *{problem['rating']}"
        verdict = VERDICTS.get(row.get("verdict"), clean(row.get("verdict") or "In queue", 32))
        if row.get("verdict") not in {None, "OK", "TESTING"} and type(row.get("passedTestCount")) is int:
            verdict += f" on test {row['passedTestCount'] + 1}"
        elapsed = f" · {row['timeConsumedMillis']}ms" if type(row.get("timeConsumedMillis")) is int else ""
        at = row.get("creationTimeSeconds")
        when = " · " + date_text(at) if type(at) is int and 0 <= at <= 253402300799 else ""
        return f"{identifier} · {verdict}{elapsed}{when}"
