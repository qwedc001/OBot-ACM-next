"""Nowcoder ACM profiles, including teams; no per-member or history fanout."""

import asyncio
from dataclasses import replace

from ..profiles import Profile, ProfileNotFound, ProfileSection, clean, number
from ._profiles import ProfileClient, content, document, elements, optional
from .http import UpstreamError


class NowcoderClient(ProfileClient):
    pattern = r"[1-9][0-9]{0,19}"
    hint = "牛客暂不支持昵称检索，请使用数字 UID，例如 /nk user 123456。"

    async def _fetch(self, handle: str) -> Profile:
        profile = await self._basic(handle)
        history, teams, members = await asyncio.gather(
            optional(lambda: self._history(handle)),
            optional(lambda: self._teams(handle)),
            optional(lambda: self._members(handle)) if profile.is_team else asyncio.sleep(0, result=()),
        )
        sections, warnings, metrics = [], [], list(profile.metrics)
        if history is not None:
            count, recent = history
            if count is not None:
                metrics.insert(1, ("Rated 比赛数", str(count)))
            sections.append(recent)
        else:
            warnings.append("最近比赛暂时无法获取。")
        for title, lines in (("加入的队伍", teams), ("队伍成员", members)):
            if lines is None:
                warnings.append(title + "暂时无法获取。")
            elif lines:
                sections.append(ProfileSection(title, tuple(lines)))
        return replace(profile, metrics=tuple(metrics), sections=tuple(sections), warnings=tuple(warnings))

    async def _basic(self, handle):
        response = await self.http.request(
            "GET",
            f"https://ac.nowcoder.com/acm/contest/profile/{handle}",
            accepted_statuses=frozenset({404}),
        )
        if response.status_code == 404:
            raise ProfileNotFound
        # Explicit UTF-8: this site's HTML has a charset meta tag but its HTTP
        # Content-Type may omit charset; httpx's default is suitable here.
        return await asyncio.to_thread(self.parse, response.text, handle)

    async def _page(self, path, **params):
        data = await self.http.get_json("https://ac.nowcoder.com/" + path, params=params)
        if (
            not isinstance(data, dict)
            or data.get("msg") != "OK"
            or not isinstance(data.get("data"), dict)
            or not isinstance(data["data"].get("dataList"), list)
            or not isinstance(data["data"].get("pageInfo", {}), dict)
            or any(not isinstance(row, dict) for row in data["data"]["dataList"])
        ):
            raise UpstreamError("Nowcoder: invalid profile API response")
        return data["data"]["dataList"], data["data"].get("pageInfo", {})

    async def _history(self, handle):
        rows, page = await self._page(
            "acm-heavy/acm/contest/profile/contest-joined-history",
            uid=handle,
            onlyJoinedFilter="true",
            onlyRatingFilter="true",
            contestEndFilter="true",
        )
        total = page.get("totalCount")
        count = total if type(total) is int else (len(rows) if page.get("pageCount") == 1 else None)
        if not rows:
            return count, ProfileSection("最近比赛", ("还未参加过 Rated 比赛",))
        last = rows[0]
        return count, ProfileSection(
            "最近比赛",
            (
                clean(last["contestName"]),
                f"位次 {last['rank']} · AC 数量 {last['acceptedCount']} · Rating {int(last['changeValue']):+d}",
            ),
        )

    async def _teams(self, handle):
        rows, _ = await self._page("acm/contest/profile/user-team-list", uid=handle)
        return tuple(f"{clean(row['name'], 80)} · Rating {int(row['rating'])}" for row in rows[:5])

    async def _members(self, handle):
        rows, _ = await self._page("acm/team/member-list", teamId=handle)

        async def member_line(row):
            text = clean(row["name"], 80) + (" · 队长" if row.get("isTeamAdmin") else "")
            uid = str(row.get("uid", ""))
            if not uid.isdigit():
                return text
            profile = await optional(lambda: self._basic(uid))
            if profile is not None:
                text += f" · Rating {profile.rating if profile.rating is not None else 'Unrated'}"
            return text

        return tuple(await asyncio.gather(*(member_line(row) for row in rows[:5])))

    @staticmethod
    def parse(source: str, handle: str) -> Profile:
        tree = document(source)
        name = content(elements(tree, "coder-name"))
        if not name:
            if elements(tree, "null"):
                raise ProfileNotFound
            raise UpstreamError("Nowcoder: unrecognized profile page")
        boxes = elements(tree, "profile-status-box")
        if not boxes:
            raise UpstreamError("Nowcoder: profile status missing")
        box = boxes[0]
        ratings = box.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' state-num ')]")
        rating = number(content(ratings))
        if rating is None:
            raise UpstreamError("Nowcoder: invalid rating")
        metrics = tuple(
            (label, content(nodes))
            for suffix, label in (("/rating-index", "位次"), ("/following", "关注"), ("/followers", "粉丝"))
            if (nodes := box.xpath(f".//a[contains(@href,'{suffix}')]"))
        )
        team = bool(elements(tree, "group-member-btn"))
        details = tuple(
            value
            for value in (
                content(elements(tree, "coder-brief")),
                *(clean(node.text_content()) for node in elements(tree, "coder-edu-txt")),
            )
            if value
        )
        return Profile(
            "牛客",
            clean(name, 64),
            f"https://ac.nowcoder.com/acm/contest/profile/{handle}",
            rating,
            "团队" if team else "ACM",
            metrics,
            details[:6],
            avatar_url=next(iter(tree.xpath("//a[contains(@class,'head-pic')]//img/@src")), None),
            timeline=(f"UID {handle}",) + (("团队账号",) if team else ()),
            is_team=team,
        )
