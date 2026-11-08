"""OI profiles adapted from the original OBot's CPCFinder-backed OIerDB."""

import asyncio
from collections import defaultdict

from ..clients.http import UpstreamError
from .base import Service

QUERY_TIMEOUT = 25.0

TYPE_ORDER = (
    "NOI",
    "IOI",
    "WC",
    "CTS",
    "APIO",
    "CSP提高",
    "CSP入门",
    "NOIP提高",
    "NOIST",
    "NOIP普及",
    "NOID类",
)


def ccf_level(awards):
    """Legacy OBot classification, not an official CCF certification query."""
    level = 0
    for award in awards:
        kind, medal = award.contest_type, award.medal
        if kind in {"IOI", "NOI", "WC", "CTS", "APIO"}:
            values = (10, 10, 9) if kind == "IOI" else (10, 9, 8)
            for labels, value in zip((("金牌", "Au"), ("银牌", "Ag"), ("铜牌", "Cu")), values, strict=True):
                if any(label in medal for label in labels):
                    level = max(level, value)
        elif kind in {"NOIP提高", "CSP提高", "NOIP普及", "CSP入门"}:
            values = (6, 4, 4) if kind in {"NOIP提高", "CSP提高"} else (5, 4, 3)
            for label, value in zip(("一等奖", "二等奖", "三等奖"), values, strict=True):
                if label in medal:
                    level = max(level, value)
    return level


class OIerService(Service):
    dependencies = ("clients.oier",)

    @classmethod
    def build(cls, container):
        return cls(container.clients.oier)

    def __init__(self, client):
        self.client = client

    async def _query(self, name, *, summary):
        candidates, complete = await self.client.search(name)
        if not candidates:
            return f"{name}：未找到选手" + ("（搜索达到上限，结果可能不完整）" if not complete else "")
        student = await self.client.detail(candidates[0].id)
        awards = await self.client.awards(student.id)
        lines = [
            student.name
            + (f" / {student.province}" if student.province else "")
            + (f" / {student.graduation_year}届" if student.graduation_year else "")
        ]
        if not complete:
            lines.append(f"搜索达到上限：已找到 {len(candidates)} 名同名候选，仅展示已检索候选中评分最高者")
        elif len(candidates) > 1:
            lines.append(f"同名选手：共 {len(candidates)} 名，仅显示评分最高者")
        lines.append(
            f"CCF等级（原版规则估算）: {ccf_level(awards)} / 评分: {student.rating:.2f}"
            + (f" / 全站排名: #{student.rank}" if student.rank else "")
        )
        if summary:
            schools = list(dict.fromkeys(a.school for a in awards if a.school))
            lines.append(f"获奖: {len(awards)}次" + (f" / 学校: {'、'.join(schools)}" if schools else ""))
        elif awards:
            schools = defaultdict(list)
            for award in awards:
                schools[award.school or "未知学校"].append(award)
            for school, records in schools.items():
                lines.append("\n" + school)

                def order(award):
                    kind = (
                        TYPE_ORDER.index(award.contest_type)
                        if award.contest_type in TYPE_ORDER
                        else len(TYPE_ORDER)
                    )
                    return kind, -award.year, award.contest_name

                for award in sorted(records, key=order):
                    lines.append(
                        f"[{award.medal or '未知'}] {award.contest_name}"
                        + (f" @{award.grade}" if award.grade else "")
                    )
        else:
            lines.append("暂无获奖记录")
        return "\n".join(lines)

    async def reply(self, names):
        if not 1 <= len(names) <= 3 or any(not name.strip() or len(name) > 80 for name in names):
            return "单次查询支持 1–3 个选手姓名，每个姓名不超过 80 字。用法：/oier 张三 [李四 王五]"
        blocks = []
        try:
            async with asyncio.timeout(QUERY_TIMEOUT):
                for name in names:
                    try:
                        blocks.append(await self._query(name.strip(), summary=len(names) > 1))
                    except UpstreamError:
                        blocks.append(f"{name}：OI 选手数据源暂时不可用，请稍后重试。")
        except TimeoutError:
            blocks.append("查询超时，已中止未完成的选手请求，请稍后重试。")
        return (
            "[OIerDb] "
            + ("选手查询" if len(names) == 1 else f"选手批量查询 ({len(names)}人)")
            + "\n\n"
            + "\n\n".join(blocks)
        )
