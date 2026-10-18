"""Read the public Algorithm profile in English; never query linked platforms."""

import asyncio
import re
from dataclasses import replace

from ..profiles import Profile, ProfileNotFound, ProfileSection, clean, number
from ._contests import atcoder_contests
from ._profiles import ProfileClient, content, document, elements, optional
from .http import UpstreamError


class AtcoderClient(ProfileClient):
    pattern = r"[A-Za-z0-9_]{1,32}"
    hint = "AtCoder 账号应为不超过 32 位的英文字母、数字或下划线。"

    async def contests(self):
        response = await self.http.request("GET", "https://atcoder.jp/contests/", params={"lang": "en"})
        return await asyncio.to_thread(atcoder_contests, response.text)

    async def _fetch(self, handle: str) -> Profile:
        response = await self.http.request(
            "GET",
            f"https://atcoder.jp/users/{handle}",
            params={"lang": "en", "contestType": "algo"},
            accepted_statuses=frozenset({404}),
        )
        if response.status_code == 404:
            raise ProfileNotFound
        profile = await asyncio.to_thread(self.parse, response.text, handle)
        recent = await optional(lambda: self._history(handle))
        if recent is None:
            return replace(profile, warnings=("最近比赛暂时无法获取。",))
        return replace(profile, sections=profile.sections + (recent,))

    async def _history(self, handle):
        data = await self.http.get_json(f"https://atcoder.jp/users/{handle}/history/json")
        return await asyncio.to_thread(self.history, data)

    @staticmethod
    def history(data):
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise UpstreamError("AtCoder: invalid rating history")
        rated = [row for row in data if row.get("IsRated") is True]
        if not rated:
            return ProfileSection("最近比赛", ("还未参加过 Rated 比赛",))
        last = rated[-1]
        try:
            delta = int(last["NewRating"]) - int(last["OldRating"])
            return ProfileSection(
                "最近比赛",
                (
                    clean(last["ContestName"]),
                    f"位次 {last['Place']} · 表现分 {last['Performance']} ({last.get('InnerPerformance', last['Performance'])}) · Rating {delta:+d}",
                ),
            )
        except (KeyError, TypeError, ValueError):
            raise UpstreamError("AtCoder: invalid rating history fields") from None

    @staticmethod
    def parse(source: str, handle: str) -> Profile:
        tree = document(source)
        names = tree.xpath("//h3//a[contains(concat(' ',normalize-space(@class),' '),' username ')]")
        name = content(names)
        if not name or name.casefold() != handle.casefold():
            raise UpstreamError("AtCoder: unrecognized profile page")
        # Scope to the profile tables, not navigation links or AHC history.
        fields = {}
        for table in elements(tree, "dl-table"):
            for row in table.xpath(".//tr[th and td]"):
                fields[content(row.xpath("./th"))] = content(row.xpath("./td"))
        if not fields:
            raise UpstreamError("AtCoder: profile tables missing")
        rating = number(fields.get("Rating", ""))
        if fields.get("Rating") and rating is None and fields["Rating"] not in {"-", "Unrated"}:
            raise UpstreamError("AtCoder: invalid rating")
        metrics = tuple(
            (
                label,
                str(number(fields[key]))
                if key == "Highest Rating" and number(fields[key]) is not None
                else clean(fields[key], 60),
            )
            for key, label in (
                ("Highest Rating", "最高 Rating"),
                ("Rank", "位次"),
                ("Rated Matches", "Rated 比赛数"),
            )
            if fields.get(key)
        )
        details = tuple(
            f"{label}：{fields[key]}"
            for key, label in (
                ("Country/Region", "地区"),
                ("Affiliation", "组织"),
                ("Birth Year", "生于"),
            )
            if fields.get(key)
        )
        rank_nodes = tree.xpath("//h3[b]/b")
        rank = content(rank_nodes) or ("Algorithm" if rating is not None else "Unrated")
        linked = tuple(
            f"{label}: {fields[key]}"
            for key, label in (
                ("Codeforces ID", "Codeforces"),
                ("TopCoder ID", "TopCoder"),
                ("X(Twitter) ID", "Twitter"),
                ("Twitter ID", "Twitter"),
            )
            if fields.get(key)
        )
        avatar = elements(tree, "avatar")
        note = re.search(r"\(([^)]*to promote)\)", fields.get("Highest Rating", ""))
        return Profile(
            "AtCoder",
            clean(name, 64),
            f"https://atcoder.jp/users/{handle}",
            rating,
            rank,
            metrics,
            details,
            avatar_url=avatar[0].get("src") if avatar else None,
            rating_note=note[1] if note else "",
            timeline=("最近参赛 " + fields["Last Competed"],) if fields.get("Last Competed") else (),
            sections=(ProfileSection("关联账号", linked),) if linked else (),
        )
