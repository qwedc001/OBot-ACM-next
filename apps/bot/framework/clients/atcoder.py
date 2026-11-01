"""Read the public Algorithm profile in English; never query linked platforms."""

import asyncio
import random
import re
from dataclasses import replace

from ..profiles import Profile, ProfileNotFound, ProfileSection, clean, number
from ._cache import AsyncCache
from ._contests import atcoder_contests
from ._profiles import ProfileClient, content, document, elements, optional
from .http import UpstreamError


class AtcoderClient(ProfileClient):
    pattern = r"[A-Za-z0-9_]{1,32}"
    hint = "AtCoder 账号应为不超过 32 位的英文字母、数字或下划线。"

    def __init__(self, http, *, ttl=60, capacity=128, timeout=30):
        super().__init__(http, ttl=ttl, capacity=capacity, timeout=timeout)
        self._kenkoooo_cache = AsyncCache[tuple[list[dict], dict[str, dict]]](
            ttl=3600, capacity=2, timeout=20
        )

    async def close(self):
        await asyncio.gather(
            super().close(),
            self._kenkoooo_cache.close(),
            return_exceptions=True,
        )

    async def get_kenkoooo_data(self) -> tuple[list[dict], dict[str, dict]]:
        async def _fetch():
            problems_data, models_data = await asyncio.gather(
                self.http.get_json("https://kenkoooo.com/atcoder/resources/problems.json"),
                self.http.get_json("https://kenkoooo.com/atcoder/resources/problem-models.json"),
            )
            if not isinstance(problems_data, list) or not isinstance(models_data, dict):
                raise UpstreamError("Kenkoooo: invalid problem data")
            return problems_data, models_data

        return await self._kenkoooo_cache.get("kenkoooo_data", _fetch)

    @staticmethod
    def parse_difficulty_range(diff_str: str | None) -> tuple[int | None, int | None]:
        if not diff_str:
            return None, None
        s = diff_str.strip()

        # Check single value like 1200 or -500 first
        m_single = re.match(r"^[+-]?\d+$", s)
        if m_single:
            val = int(s)
            return val, val

        # Handle range cases like 1200-1600, -800-500, -800--500, 1000-, -800-
        # Notice range delimiter is '-' between two numbers or trailing
        # Regex: optional signed int, hyphen, optional signed int
        m = re.match(r"^([+-]?\d+)\s*-\s*([+-]?\d+)?$", s)
        if m:
            p1, p2 = m.group(1), m.group(2)
            min_diff = int(p1) if p1 else None
            max_diff = int(p2) if p2 else None
            return min_diff, max_diff

        # Range with no lower bound e.g. "- 800" (with space) or "<= 800"
        m_upper = re.match(r"^-\s*(\d+)$", s)
        if m_upper:
            return None, int(m_upper.group(1))

        raise ValueError("难度格式错误，应为整数或区间如 1200-1600 或 -800-500")

    async def pick_problem(
        self, contest_type: str | None = None, diff_str: str | None = None
    ) -> dict | None:
        """Pick a random AtCoder problem by contest type and difficulty."""
        problems, models = await self.get_kenkoooo_data()
        ctype = (contest_type or "").strip().lower()
        if ctype and ctype not in {"abc", "arc", "agc", "all"}:
            raise ValueError("比赛类型仅支持 abc、arc、agc 或 all")

        min_diff, max_diff = self.parse_difficulty_range(diff_str)
        if min_diff is not None and max_diff is not None and min_diff > max_diff:
            min_diff, max_diff = max_diff, min_diff

        candidates = []
        for prob in problems:
            cid = str(prob.get("contest_id", "")).lower()
            if ctype and ctype != "all" and not cid.startswith(ctype):
                continue

            pid = prob.get("id")
            model = models.get(pid, {})
            difficulty = model.get("difficulty")

            if min_diff is not None or max_diff is not None:
                if difficulty is None:
                    continue
                if min_diff is not None and difficulty < min_diff:
                    continue
                if max_diff is not None and difficulty > max_diff:
                    continue

            candidates.append((prob, difficulty))

        if not candidates:
            return None

        chosen, diff = random.choice(candidates)
        contest_id = chosen.get("contest_id", "")
        prob_id = chosen.get("id", "")
        index = chosen.get("problem_index", "")
        title = chosen.get("title") or chosen.get("name", "")
        url = f"https://atcoder.jp/contests/{contest_id}/tasks/{prob_id}"

        return {
            "id": prob_id,
            "contest_id": contest_id,
            "index": index,
            "title": title,
            "url": url,
            "difficulty": diff,
        }

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
