"""CPCFinder service: contestant, school and team award queries."""

import logging
from dataclasses import asdict

from ..clients.cpcfinder import (
    CPCAwardItem,
    CPCFinderClient,
    CPCSchoolCandidate,
    CPCStudentCandidate,
    CPCStudentDetail,
)
from ..clients.http import UpstreamError
from ..clients.jobs import QueueClosed, QueueFull
from ..contracts import RequestContext
from ..messages import Image, Message, Text
from ..profiles import Profile, ProfileSection
from .base import Service

logger = logging.getLogger(__name__)


def format_cpc_rank(award: CPCAwardItem) -> str:
    if award.official:
        return f"#{award.official_rank}({award.rank})" if award.official_rank > 0 else f"#{award.rank}"
    return f"*{award.rank}"


class CPCFinderService(Service):
    dependencies = ("clients.cpcfinder", "clients.renderer", "profiles")

    @classmethod
    def build(cls, container):
        return cls(container.clients.cpcfinder, container.clients.renderer, container.profiles)

    def __init__(self, client: CPCFinderClient, renderer, profiles=None):
        self.client = client
        self.renderer = renderer
        self.profiles = profiles

    async def query_student(
        self, name: str, school: str | None = None
    ) -> tuple[CPCStudentDetail | None, list[CPCAwardItem], list[CPCStudentCandidate]]:
        """Query student profile.
        Returns:
            (detail, awards, candidates)
            - If exactly 1 match: (detail, awards, [])
            - If multiple matches: (None, [], candidates)
            - If no match: (None, [], [])
        """
        candidates = await self.client.search_students(name, school)
        if not candidates:
            return None, [], []
        if len(candidates) > 1:
            # Check exact match if school was provided
            exact = [c for c in candidates if c.name == name.strip()]
            if school:
                exact = [c for c in exact if school.strip() in c.school_name]
            if len(exact) == 1:
                detail = await self.client.get_student_detail(exact[0].student_id)
                awards = await self.client.get_student_awards(exact[0].student_id)
                return detail, awards, []
            return None, [], candidates

        student_id = candidates[0].student_id
        detail = await self.client.get_student_detail(student_id)
        awards = await self.client.get_student_awards(student_id)
        return detail, awards, []

    async def query_school(
        self, school_name: str
    ) -> tuple[CPCSchoolCandidate | None, list[CPCAwardItem], list[CPCSchoolCandidate]]:
        """Query school profile and award history."""
        schools = await self.client.search_schools(school_name)
        if not schools:
            return None, [], []
        exact = [s for s in schools if s.name == school_name.strip()]
        if exact:
            target = exact[0]
            awards = await self.client.get_school_awards(target.school_id)
            return target, awards, []
        if len(schools) > 1:
            # Ambiguous: return candidates for disambiguation
            return None, [], schools

        target = schools[0]
        awards = await self.client.get_school_awards(target.school_id)
        return target, awards, []

    async def reply(
        self,
        context: RequestContext,
        query: str,
        school: str | None = None,
        *,
        mode: str = "auto",
        image_available: bool = True,
    ) -> Message | str:
        """Handle /cpc query and render result as card image or text."""
        try:
            # 1. Mode dispatch or auto
            if mode == "school" or (mode == "auto" and not school and ("大学" in query or "学院" in query)):
                return await self._reply_school(context, query, image_available=image_available)

            if mode == "team":
                team_res = await self._search_team(context, query, image_available)
                if team_res:
                    return team_res
                return f"[CPCFinder] 未找到队伍「{query}」的参赛或获奖记录。提示：由于上游 API 限制，队伍查询依赖参赛选手或高校关联，若未找到可尝试查询队伍成员姓名。"

            # 2. Try student query
            detail, awards, candidates = await self.query_student(query, school)

            if detail:
                return await self._render_student_profile(context, detail, awards, image_available)

            if candidates:
                return self._render_student_candidates(candidates)

            # 3. If mode is auto and no student matched, try school query as fallback
            if mode == "auto" and not school:
                school_res, school_awards, all_schools = await self.query_school(query)
                if school_res:
                    return await self._render_school_profile(
                        context, school_res, school_awards, image_available
                    )
                if all_schools:
                    return self._render_school_candidates(all_schools)

            # 4. Try team query from awards if query might be a team name
            team_res = await self._search_team(context, query, image_available)
            if team_res:
                return team_res

            return f"[CPCFinder] 未找到与「{query}」相关的 XCPC 选手或战绩信息。"

        except UpstreamError:
            logger.warning("CPCFinder API unavailable")
            return "CPCFinder 接口暂时无法访问，请稍后重试。"
        except Exception as e:
            logger.exception("Error in CPCFinder reply: %s", e)
            return "查询出现意外错误，请稍后重试。"

    async def _reply_school(self, context: RequestContext, school_name: str, *, image_available: bool):
        target, awards, candidates = await self.query_school(school_name)
        if not target:
            if candidates:
                return self._render_school_candidates(candidates)
            return f"[CPCFinder] 未找到学校「{school_name}」的相关信息。"
        return await self._render_school_profile(context, target, awards, image_available)

    def _render_school_candidates(self, candidates: list[CPCSchoolCandidate]) -> str:
        lines = [f"[CPCFinder] 找到 {len(candidates)} 所匹配学校，请使用完整名称重试："]
        for c in candidates[:10]:
            lines.append(f"- {c.name} (积分: {c.rating:.1f}, 奖牌: {c.gold}金/{c.silver}银/{c.bronze}铜)")
        return "\n".join(lines)

    async def _search_team(self, context: RequestContext, team_name: str, image_available: bool):
        """Search student awards or contest results matching team name."""
        # Find candidates by searching student name or search student awards
        # Since cpcfinder does not have a direct team API, check if any student search matches
        students = await self.client.search_students(team_name)
        for s in students[:3]:
            awards = await self.client.get_student_awards(s.student_id)
            for a in awards:
                if team_name.lower() in a.team_name.lower():
                    # Matched team
                    return self._render_team_awards(a.team_name, awards)
        return None

    def _render_team_awards(self, team_name: str, awards: list[CPCAwardItem]) -> str:
        team_awards = [a for a in awards if team_name.lower() in a.team_name.lower()]
        if not team_awards:
            return ""
        lines = [f"[CPCFinder] 队伍战绩查询：{team_name}", ""]
        for a in team_awards[:8]:
            rank_str = format_cpc_rank(a)
            lines.append(f"[{a.medal}] {a.contest_name}")
            lines.append(f"{rank_str} {a.team_name} @{a.place} {a.date}")
        return "\n".join(lines)

    def _render_student_candidates(self, candidates: list[CPCStudentCandidate]) -> str:
        lines = [f"[CPCFinder] 找到 {len(candidates)} 个候选结果：", ""]
        for i, stu in enumerate(candidates[:6], 1):
            rank_str = f"#{stu.rank} " if stu.rank else ""
            lines.append(
                f"#{i} {stu.name} / {stu.school_name}\n"
                f"   Rating: {stu.rating:.1f} {rank_str}| 金: {stu.gold} 银: {stu.silver} 铜: {stu.bronze}"
            )
        lines.append("")
        lines.append('提示：可通过输入「/cpc 姓名 学校」精确查询（含空格的姓名请用引号包裹，如 /cpc "First Last" 学校）。')
        return "\n".join(lines)

    async def _render_student_profile(
        self,
        context: RequestContext,
        detail: CPCStudentDetail,
        awards: list[CPCAwardItem],
        image_available: bool,
    ) -> Message | str:
        url = f"https://cpcfinder.com/student/{detail.student_id}"

        # Build awards text lines
        award_lines = []
        for a in awards[:6]:
            rank_str = format_cpc_rank(a)
            award_lines.append(f"[{a.medal}] {a.contest_name} ({rank_str})")

        profile = Profile(
            platform="CPCFinder",
            handle=detail.name,
            url=url,
            rating=int(round(detail.rating)),
            rank=detail.school_name,
            metrics=(
                ("金牌", str(detail.gold)),
                ("银牌", str(detail.silver)),
                ("铜牌", str(detail.bronze)),
                ("冠/亚/季", f"{detail.champion}/{detail.second}/{detail.third}"),
            ),
            details=(
                f"所属学校：{detail.school_name}",
                f"全站排名：#{detail.rank}" if detail.rank else "全站排名：-",
                f"CPC Rating：{detail.rating:.1f}",
            ),
            sections=(
                ProfileSection("XCPC 获奖历史", tuple(award_lines) if award_lines else ("暂无获奖记录",)),
            ),
        )

        plain_text = self._student_plain_text(detail, awards, url)
        if not image_available:
            return plain_text

        try:
            model = asdict(profile)
            artifact = await self.renderer.render(context.actor, "profile", model)
            data = await self.renderer.artifacts.read(context.actor, artifact.id)
            return Message(Text(f"CPCFinder · {detail.name} ({detail.school_name})\n{url}"), Image(data))
        except (ImportError, OSError, ValueError, TimeoutError, QueueFull, QueueClosed):
            logger.warning("CPCFinder image render unavailable, fallback to text")
            return plain_text + "\n(卡片图片渲染暂不可用，已返回文字资料)"

    def _student_plain_text(self, detail: CPCStudentDetail, awards: list[CPCAwardItem], url: str) -> str:
        lines = [
            "[CPCFinder] 选手查询",
            f"{detail.name} / {detail.school_name}",
            "",
            f"Rating: {detail.rating:.1f}" + (f" (全站 #{detail.rank})" if detail.rank else ""),
            f"冠军: {detail.champion} / 亚军: {detail.second} / 季军: {detail.third}",
            f"金牌: {detail.gold} / 银牌: {detail.silver} / 铜牌: {detail.bronze}",
            "",
            "获奖信息：",
        ]
        if not awards:
            lines.append("暂无获奖记录")
        else:
            for a in awards[:10]:
                rank_str = format_cpc_rank(a)
                lines.append(f"[{a.medal}] {a.contest_name}")
                lines.append(f"  {rank_str} {a.team_name} @{a.place} {a.date}")
        lines.append("")
        lines.append(url)
        return "\n".join(lines)

    async def _render_school_profile(
        self,
        context: RequestContext,
        school: CPCSchoolCandidate,
        awards: list[CPCAwardItem],
        image_available: bool,
    ) -> Message | str:
        url = f"https://cpcfinder.com/school/{school.school_id}"
        award_lines = []
        for a in awards[:6]:
            rank_str = format_cpc_rank(a)
            award_lines.append(f"[{a.medal}] {a.contest_name} ({rank_str} {a.team_name})")

        profile = Profile(
            platform="CPCFinder",
            handle=school.name,
            url=url,
            rating=int(round(school.rating)),
            rank=f"最高名次 #{school.max_rank}" if school.max_rank else "高校战队",
            metrics=(
                ("金牌", str(school.gold)),
                ("银牌", str(school.silver)),
                ("铜牌", str(school.bronze)),
                ("冠/亚/季", f"{school.champion}/{school.second}/{school.third}"),
            ),
            details=(
                f"高校战绩统计：{school.name}",
                f"最佳名次：#{school.max_rank}" if school.max_rank else "最佳名次：-",
                f"积分：{school.rating:.1f}",
            ),
            sections=(
                ProfileSection("战队近年荣誉", tuple(award_lines) if award_lines else ("暂无获奖记录",)),
            ),
        )

        plain_text = self._school_plain_text(school, awards, url)
        if not image_available:
            return plain_text

        try:
            model = asdict(profile)
            artifact = await self.renderer.render(context.actor, "profile", model)
            data = await self.renderer.artifacts.read(context.actor, artifact.id)
            return Message(Text(f"CPCFinder · {school.name} 战绩档案\n{url}"), Image(data))
        except (ImportError, OSError, ValueError, TimeoutError, QueueFull, QueueClosed):
            logger.warning("CPCFinder school image render unavailable")
            return plain_text + "\n(卡片图片渲染暂不可用，已返回文字资料)"

    def _school_plain_text(self, school: CPCSchoolCandidate, awards: list[CPCAwardItem], url: str) -> str:
        lines = [
            "[CPCFinder] 高校战绩查询",
            f"{school.name}",
            "",
            f"积分: {school.rating:.1f}" + (f" (最高名次 #{school.max_rank})" if school.max_rank else ""),
            f"冠军: {school.champion} / 亚军: {school.second} / 季军: {school.third}",
            f"金牌: {school.gold} / 银牌: {school.silver} / 铜牌: {school.bronze}",
            "",
            "代表战绩：",
        ]
        if not awards:
            lines.append("暂无获奖记录")
        else:
            for a in awards[:10]:
                rank_str = format_cpc_rank(a)
                lines.append(f"[{a.medal}] {a.contest_name}")
                lines.append(f"  {rank_str} {a.team_name} @{a.place} {a.date}")
        lines.append("")
        lines.append(url)
        return "\n".join(lines)
