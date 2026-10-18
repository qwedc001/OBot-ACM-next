"""Aggregate online contests with the Dashboard's applied manual data."""

import asyncio
import logging
from dataclasses import asdict, dataclass
from time import time

from ..clients._cache import AsyncCache
from ..clients.http import UpstreamError
from ..clients.jobs import QueueClosed, QueueFull
from ..contests import SOURCES, Contest, format_time, search_contests, select_contests
from ..curation.errors import StoreError
from ..messages import Image, Message, Text
from ..responses import text_chunks
from ..views.contests import ContestBackend
from .base import Service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContestSnapshot:
    items: tuple[Contest, ...]
    warnings: tuple[str, ...]
    now: float


class ContestsService(Service):
    dependencies = (
        "curation",
        "clients.codeforces",
        "clients.atcoder",
        "clients.nowcoder",
        "clients.renderer",
    )

    @classmethod
    def build(cls, container):
        return cls(
            container.curation,
            {
                "cf": container.clients.codeforces,
                "atc": container.clients.atcoder,
                "nk": container.clients.nowcoder,
            },
            container.clients.renderer,
        )

    def __init__(self, curation, clients, renderer):
        self.curation, self.clients, self.renderer = curation, clients, renderer
        self.cache = AsyncCache(ttl=60, capacity=3, timeout=30)
        renderer.register("contests", ContestBackend())

    async def close(self):
        await self.cache.close()

    def _manual(self, repository):
        # Same store and worker-thread transaction as Dashboard apply; pending
        # submissions are deliberately absent. Do not cache this mutable data.
        rows = self.curation.contestlist.load()
        warnings = ("手动比赛中有无效条目，已跳过。",) if any(not row.valid for row in rows) else ()
        items = tuple(
            Contest(
                "manual:" + row.hash,
                "manual",
                row.fields["platform"],
                row.fields["abbr"],
                row.fields["name"],
                row.fields["start_time"],
                row.fields["duration"],
                row.fields["supplement"],
            )
            for row in rows
            if row.valid
        )
        return items, warnings

    async def _source(self, source):
        try:
            if source == "manual":
                return await self.curation.run(self._manual)
            return await self.cache.get(source, self.clients[source].contests), ()
        except (UpstreamError, StoreError, OSError, ValueError) as error:
            # Do not put response bodies, file paths or arbitrary query text in logs.
            logger.warning("Contest source unavailable: %s (%s)", source, type(error).__name__)
            return (), (f"{SOURCES[source]}暂时无法获取。",)

    async def collect(self, source="all", *, now=None):
        if source not in {*SOURCES, "all"}:
            raise ValueError("Unknown contest source")
        groups = await asyncio.gather(
            *(self._source(key) for key in (SOURCES if source == "all" else (source,)))
        )
        return ContestSnapshot(
            tuple({c.key: c for items, _ in groups for c in items}.values()),
            tuple(warning for _, warnings in groups for warning in warnings),
            time() if now is None else now,
        )

    async def listing(self, context, *, today=False, source="all", image_available=True):
        snapshot = await self.collect(source)
        items = select_contests(snapshot.items, snapshot.now, today=today)
        title = ("今日" if today else "近日") + "算法竞赛"
        if source != "all":
            title += " · " + SOURCES[source]
        warnings = list(snapshot.warnings)
        if len(items) > 60:
            warnings.append(f"共 {len(items)} 场，列表展示前 60 场；可按平台筛选或查询详情。")
            items = items[:60]
        text = title + "\n" + "\n\n".join(c.text(snapshot.now) for c in items)
        if not items:
            text += "当前成功获取的数据中没有符合条件的比赛。" if warnings else "暂无符合条件的比赛。"
        if warnings:
            text += "\n\n" + "\n".join(warnings)
        if not image_available or not items:
            return text_chunks(text)
        return await self._images(
            context,
            {"title": title, "now": snapshot.now, "items": [asdict(c) for c in items], "warnings": warnings},
            text,
            title,
        )

    async def _images(self, context, model, fallback, caption):
        """Shared render/artifact path; links stay clickable in the caption."""
        try:
            artifacts = await self.renderer.render_pages(context.actor, "contests", model)
            messages = []
            for index, artifact in enumerate(artifacts, 1):
                data = await self.renderer.artifacts.read(context.actor, artifact.id)
                label = f"{caption} · {index}/{len(artifacts)}" if len(artifacts) > 1 else caption
                messages.append(Message(Text(label), Image(data)))
            return tuple(messages)
        except (ImportError, OSError, ValueError, TimeoutError, QueueFull, QueueClosed):
            logger.warning("Contest image unavailable")
            return text_chunks(fallback + "\n图片暂不可用，已返回文字内容。")

    async def detail(self, context, query, *, image_available=True):
        if not query.strip() or len(query) > 160:
            return ("请输入不超过 160 字的比赛名称、简称或查询标识。",)
        snapshot = await self.collect()
        candidates, fuzzy = await asyncio.to_thread(search_contests, snapshot.items, query)
        if not candidates:
            text = "未找到匹配的比赛，可先使用 /contest 查看近日比赛。"
        elif len(candidates) == 1:
            text = ("近似匹配：\n" if fuzzy else "") + candidates[0].text(snapshot.now)
        else:
            text = "找到多个相近比赛，请用查询标识选择：\n" + "\n\n".join(
                f"{c.name} · {format_time(c.start)}\n/contest detail {c.key}" for c in candidates[:5]
            )
            if len(candidates) > 5:
                text += "\n仅显示前 5 项，请缩小查询范围。"
        if snapshot.warnings:
            text += "\n\n" + "\n".join(snapshot.warnings)
        if len(candidates) == 1 and image_available:
            contest = candidates[0]
            title = "近似匹配的比赛" if fuzzy else "比赛详情"
            caption = f"{title} · {contest.name}" + ("\n" + contest.url if contest.url else "")
            return await self._images(
                context,
                {
                    "detail": True,
                    "title": title,
                    "now": snapshot.now,
                    "items": [asdict(contest)],
                    "warnings": snapshot.warnings,
                },
                text,
                caption,
            )
        return text_chunks(text)
