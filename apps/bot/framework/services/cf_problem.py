"""Codeforces problem retrieval, tag filtering and submission history service."""

import asyncio
import difflib
import logging
import random

from ..clients._cache import AsyncCache
from ..clients.codeforces import CodeforcesClient
from ..clients.http import UpstreamError
from ..profiles import ProfileNotFound
from .base import Service

logger = logging.getLogger(__name__)


def decode_range(range_str: str, length: tuple[int, int] = (3, 4)) -> tuple[int, int]:
    """
    Parse a rating or rating range string like '1500' or '1400-1600'.
    Returns (min_r, max_r) or (-1, -1) on invalid syntax.
    """
    if not range_str:
        return 0, 0
    range_str = range_str.strip()
    if "-" in range_str:
        parts = range_str.split("-")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return -1, -1
        if not (length[0] <= len(parts[0]) <= length[1] and length[0] <= len(parts[1]) <= length[1]):
            return -1, -1
        min_p, max_p = int(parts[0]), int(parts[1])
        if min_p > max_p:
            min_p, max_p = max_p, min_p
        return min_p, max_p
    else:
        if not range_str.isdigit() or not (length[0] <= len(range_str) <= length[1]):
            return -1, -1
        val = int(range_str)
        return val, val


class CFProblemService(Service):
    """
    Service for Codeforces problem random pick, tag filtering, and recent submissions.
    Uses LRU cache and periodic background sync for Codeforces problemset.
    """

    dependencies = ("clients.codeforces",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.codeforces)

    def __init__(self, cf_client: CodeforcesClient, cache_ttl: int = 3600, sync_interval: int = 86400):
        self.cf_client = cf_client
        self.cache_ttl = cache_ttl
        self.sync_interval = sync_interval
        # LRU cache for problemsets keyed by tag or "all"
        self._cache = AsyncCache[list[dict]](ttl=cache_ttl, capacity=128, timeout=30)
        self._sync_task: asyncio.Task | None = None
        self._cached_tags: list[str] = []
        self._tag_alias_map: dict[str, str] = {}  # alias (hyphenated) -> original CF tag

    async def start(self):
        # Start periodic background synchronization task
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.create_task(self._periodic_sync())

    async def close(self):
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        await self._cache.close()

    async def _periodic_sync(self):
        """Periodically sync problemset to refresh cache (delayed on startup)."""
        while True:
            try:
                # Wait for initial interval before eager sync to not block startup rate gates
                await asyncio.sleep(self.sync_interval)
                await self.get_problemset(refresh=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Periodic problemset sync failed: %s", e)

    async def get_problemset(self, tag: str | None = None, refresh: bool = False) -> list[dict]:
        """Fetch problemset with LRU cache."""
        key = (tag or "all").lower()

        async def _fetch():
            problems = await self.cf_client.get_problemset(tag)
            if not problems:
                # Do not populate cache with empty result if network or upstream failed
                raise UpstreamError("Empty problemset received from Codeforces")
            if key == "all":
                self._update_tags(problems)
            return problems

        if refresh:
            if key == "all":
                # Refreshing "all" invalidates all tag-specific caches to maintain consistency
                self._cache.invalidate()
            else:
                self._cache.invalidate(key)

        return await self._cache.get(key, _fetch)

    def _update_tags(self, problems: list[dict]):
        tags = set()
        tag_map = {}
        for p in problems:
            for t in p.get("tags", []):
                alias = t.replace(" ", "-").lower()
                tags.add(alias)
                if alias not in tag_map:
                    tag_map[alias] = t
        self._cached_tags = sorted(tags)
        self._tag_alias_map = tag_map

    async def get_all_tags(self) -> list[str]:
        """Get all known problem tags, refreshed from problemset cache."""
        problems = await self.get_problemset()
        self._update_tags(problems)
        return self._cached_tags

    def match_tag(self, tag: str, all_tags: list[str]) -> str | None:
        """Fuzzy match tag against all known tags."""
        tag = tag.strip().lower()
        if tag in all_tags:
            return tag
        matches = difflib.get_close_matches(tag, all_tags, n=1, cutoff=0.55)
        if matches:
            return matches[0]
        # Try substring match
        for known in all_tags:
            if tag in known:
                return known
        return None

    async def pick_problem(
        self,
        tag: str | None = None,
        rating_range: str | None = None,
        newer_only: bool = False,
    ) -> tuple[bool, str, dict | None]:
        """
        Pick a random problem based on tag, rating range, and newer_only condition.
        Returns: (success: bool, message: str, problem: dict | None)
        """
        tag_matched_msg = ""
        fetch_tag = tag

        if tag and tag.lower() != "all":
            try:
                all_tags = await self.get_all_tags()
            except Exception:
                return False, "获取 Codeforces 题目标签列表失败，请稍后重试。", None
            matched = self.match_tag(tag, all_tags)
            if not matched:
                return False, f"未找到匹配的题目标签 [{tag}]。请使用有效标签如 dp, greedy, math 等。", None
            if matched != tag.lower():
                tag_matched_msg = f"（已将标签匹配为 {matched}）\n"
            # Use original tag for API query if known
            fetch_tag = self._tag_alias_map.get(matched, matched)

        min_rating, max_rating = None, None
        if rating_range:
            min_rating, max_rating = decode_range(rating_range)
            if min_rating < 0:
                return False, f"难度范围格式错误 [{rating_range}]，请输入单数值如 1500 或区间如 1400-1600。", None

        try:
            problems = await self.get_problemset(fetch_tag)
        except Exception:
            return False, "网络连接异常，无法获取 Codeforces 题目列表，请稍后重试。", None

        candidates = []
        for p in problems:
            if not isinstance(p, dict):
                continue
            # If a tag filter was requested, ensure the problem has this tag
            if fetch_tag and fetch_tag.lower() != "all":
                raw_tags = p.get("tags")
                if not isinstance(raw_tags, list):
                    continue
                p_tags = [str(t).lower() for t in raw_tags] + [str(t).replace(" ", "-").lower() for t in raw_tags]
                if fetch_tag.lower() not in p_tags:
                    continue
            r = p.get("rating")
            if min_rating is not None:
                if not isinstance(r, (int, float)) or r < min_rating or r > max_rating:
                    continue
            contest_id = p.get("contestId")
            if not isinstance(contest_id, int):
                continue
            if newer_only and contest_id < 1000:
                continue
            candidates.append(p)

        if not candidates:
            return False, f"{tag_matched_msg}未找到满足条件的题目，请尝试放宽筛选条件。", None

        chosen = random.choice(candidates)
        contest_id = chosen.get("contestId")
        index = chosen.get("index")
        name = chosen.get("name")
        rating = chosen.get("rating", "Unrated")
        tags = ", ".join(chosen.get("tags", []))
        prob_url = f"https://codeforces.com/contest/{contest_id}/problem/{index}"

        content = (
            f"{tag_matched_msg}"
            f"[Codeforces] 随机选题\n\n"
            f"P{contest_id}{index} {name}\n\n"
            f"链接: {prob_url}\n"
            f"标签: {tags}\n"
            f"难度: *{rating}"
        )
        return True, content, chosen

    async def get_recent_submissions(self, handle: str, count: int = 5) -> tuple[bool, str]:
        """
        Query user's recent submissions.
        Returns: (success: bool, message: str)
        """
        handle = handle.strip()
        count = max(1, min(count, 50))
        try:
            submissions = await self.cf_client.get_user_submissions(handle, count=count)
        except ProfileNotFound:
            return False, f"[Codeforces] {handle}\n\n用户不存在"
        except Exception:
            return False, "获取 Codeforces 提交记录失败，请稍后重试。"

        if not submissions:
            return True, f"[Codeforces] {handle}\n\n还未提交过题目"

        lines = [f"[Codeforces] {handle}\n最近 {len(submissions)} 发提交:"]
        for sub in submissions:
            lines.append(f"[{sub.get('id', '')}] {CodeforcesClient.submit_text(sub)}")

        return True, "\n".join(lines)
