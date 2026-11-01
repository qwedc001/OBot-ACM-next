"""Contest values and query rules, independent of HTTP, storage and NoneBot."""

import difflib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Shanghai")
SOURCES = {"cf": "Codeforces", "atc": "AtCoder", "nk": "牛客", "manual": "手动比赛"}
PHASES = {"running": "正在进行", "upcoming": "即将开始", "ended": "已结束"}


@dataclass(frozen=True)
class Contest:
    key: str
    source: str
    platform: str
    abbr: str
    name: str
    start: int
    duration: int
    supplement: str = ""
    url: str = ""
    judging: str = ""

    @property
    def end(self):
        return self.start + self.duration

    def phase(self, now):
        if now < self.start:
            return "upcoming"
        if now < self.end or (self.judging and now < self.end + 7 * 86400):
            return "running"
        return "ended"

    def status(self, now):
        return self.judging if self.judging and self.phase(now) == "running" else PHASES[self.phase(now)]

    def text(self, now):
        lines = [
            f"[{self.platform} · {self.abbr}] {self.name}",
            f"{self.status(now)} · {format_time(self.start)}（北京时间）",
            f"持续 {format_duration(self.duration)}" + (f" · {self.supplement}" if self.supplement else ""),
            f"查询标识：{self.key}",
        ]
        if self.url:
            lines.append(self.url)
        return "\n".join(lines)


def format_time(timestamp):
    return datetime.fromtimestamp(timestamp, TIMEZONE).strftime("%Y/%m/%d %H:%M")


def format_duration(seconds):
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return (
        " ".join(
            f"{value}{unit}"
            for value, unit in ((days, "天"), (hours, "小时"), (minutes, "分"), (seconds, "秒"))
            if value
        )
        or "0秒"
    )


def select_contests(contests, now, *, today=False):
    """Legacy recent window, recalculated from cached facts at query time.

    Days and contests are half-open intervals: a contest ending exactly at
    midnight belongs to yesterday, while an overnight contest overlaps today.
    Each source retains today's ended contests or its most recent ended one.
    """
    start = datetime.fromtimestamp(now, TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start, day_end = start.timestamp(), (start + timedelta(days=1)).timestamp()
    horizon = (start + timedelta(days=31)).timestamp()
    unique = {contest.key: contest for contest in contests}

    def overlaps_today(contest):
        return contest.start < day_end and contest.end > day_start

    if today:
        selected = [contest for contest in unique.values() if overlaps_today(contest)]
    else:
        selected = [
            contest
            for contest in unique.values()
            if contest.phase(now) != "ended" and contest.start < horizon
        ]
        for source in SOURCES:
            ended = [c for c in unique.values() if c.source == source and c.phase(now) == "ended"]
            selected.extend(
                [c for c in ended if overlaps_today(c)] or sorted(ended, key=lambda c: c.start)[-1:]
            )
    order = {phase: index for index, phase in enumerate(PHASES)}
    return tuple(sorted(selected, key=lambda c: (order[c.phase(now)], c.start, c.key)))


def search_contests(contests, query):
    """Exact identities first, then names/abbreviations, then fuzzy suggestions.

    Never collapse distinct contests with the same name. Return ties for the
    caller to disambiguate using source-qualified stable keys.
    """

    def normalize(value):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    query = normalize(query)
    if not query or len(query) > 160:
        return (), False
    items = tuple({c.key: c for c in contests}.values())
    exact = tuple(c for c in items if normalize(c.key) == query)
    if exact:
        return exact, False

    def fields(c):
        return normalize(c.abbr), normalize(c.name)

    exact = tuple(c for c in items if query in fields(c))
    if exact:
        return exact, False
    partial = tuple(c for c in items if any(query in field for field in fields(c)))
    if partial:
        return partial, False
    scored = [
        (max(difflib.SequenceMatcher(None, query, field).ratio() for field in fields(c)), c) for c in items
    ]
    scored = sorted((pair for pair in scored if pair[0] >= 0.55), key=lambda pair: (-pair[0], pair[1].key))
    return tuple(c for _, c in scored[:5]), True
