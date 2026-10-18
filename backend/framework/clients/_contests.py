"""Public platform payload parsers; no network calls or query-time filtering."""

import re
from datetime import datetime

from ..contests import TIMEZONE, Contest
from ..profiles import clean
from ._profiles import content, document
from .http import UpstreamError


def contest(key, source, platform, abbr, name, start, duration, supplement="", url="", judging=""):
    if (
        type(start) is not int
        or not 0 <= start <= 4_102_444_800
        or type(duration) is not int
        or not 0 < duration <= 366 * 86400
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise UpstreamError("Contest: invalid fields")
    return Contest(
        key,
        source,
        clean(platform, 32),
        clean(abbr, 80),
        clean(name, 180),
        start,
        duration,
        clean(supplement, 160),
        url,
        judging,
    )


def codeforces_contests(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise UpstreamError("Codeforces: invalid contest list")
    result = []
    for row in rows:
        # Official API permits contests without an announced start time.
        if row.get("startTimeSeconds") is None:
            continue
        if (
            type(row.get("id")) is not int
            or row["id"] <= 0
            or row.get("phase") not in {"BEFORE", "CODING", "PENDING_SYSTEM_TEST", "SYSTEM_TEST", "FINISHED"}
        ):
            raise UpstreamError("Codeforces: invalid contest identity or phase")
        result.append(
            contest(
                f"cf:{row['id']}",
                "cf",
                "Codeforces",
                f"CF{row['id']}",
                row.get("name"),
                row["startTimeSeconds"],
                row.get("durationSeconds"),
                f"{row.get('type', '')} 赛制".strip(),
                f"https://codeforces.com/contest/{row['id']}",
                {"PENDING_SYSTEM_TEST": "等待系统测试", "SYSTEM_TEST": "系统测试中"}.get(row["phase"], ""),
            )
        )
    return tuple(result)


def _atcoder_rated_range(value):
    """Original OBot supplement wording, without the raw 'Rated' prefix."""
    value = value.replace(" ", "")
    if value == "-":
        return "不计分"
    if value == "All":
        return "为所有人计分"
    if not re.fullmatch(r"\d*-\d*", value):
        raise ValueError("Invalid AtCoder rating range")
    if value.startswith("-"):
        value = "0" + value
    if value.endswith("-"):
        value += "∞"
    return f"为 {value} 计分"


def _nowcoder_rated(row):
    """Match old _decode_rated: unrated / everyone / 0 through upper bound."""
    if not row.xpath(".//span[contains(@class,'tag-rating')]"):
        return "不计分"
    limit = content(row.xpath(".//li[@class='icon-nc-flash2']"))
    if not limit:
        return "为所有人计分"
    match = re.search(r"Rating\s*[＞>]\s*(\d+)\s*$", limit, re.IGNORECASE)
    if not match:
        raise ValueError("Invalid NowCoder rating range")
    return f"为 0-{int(match[1])} 计分"


def atcoder_contests(source):
    tree = document(source)
    sections = tree.xpath(
        "//div[@id='contest-table-active' or @id='contest-table-upcoming' or @id='contest-table-recent']"
    )
    if not sections:
        raise UpstreamError("AtCoder: contest tables missing")
    result = []
    try:
        for section in sections:
            for row in section.xpath(".//tbody/tr"):
                cells = row.xpath("./td")
                link = cells[1].xpath("./a")[0]
                match = re.fullmatch(r"/contests/([A-Za-z0-9_-]+)", link.get("href", ""))
                if not match:
                    raise ValueError
                stamp = datetime.fromisoformat(content(cells[0].xpath(".//time")))
                if stamp.tzinfo is None:
                    raise ValueError
                hours, minutes = map(int, content([cells[2]]).split(":"))
                if hours < 0 or not 0 <= minutes < 60:
                    raise ValueError
                identifier = match[1]
                result.append(
                    contest(
                        "atc:" + identifier,
                        "atc",
                        "AtCoder",
                        identifier.upper(),
                        content([link]),
                        int(stamp.timestamp()),
                        hours * 3600 + minutes * 60,
                        _atcoder_rated_range(content([cells[3]])),
                        "https://atcoder.jp/contests/" + identifier,
                    )
                )
    except (IndexError, TypeError, ValueError, OverflowError):
        raise UpstreamError("AtCoder: invalid contest row") from None
    return tuple(result)


def nowcoder_contests(source, category):
    tree = document(source)
    sections = tree.xpath(
        "//div[contains(concat(' ',normalize-space(@class),' '),' platform-mod ') and "
        "(contains(concat(' ',normalize-space(@class),' '),' js-current ') or "
        "contains(concat(' ',normalize-space(@class),' '),' js-end '))]"
    )
    if not sections:
        raise UpstreamError("Nowcoder: contest sections missing")
    result = []
    try:
        for section in sections:
            for row in section.xpath(
                ".//div[contains(concat(' ',normalize-space(@class),' '),' platform-item-cont ')]"
            ):
                link = row.xpath("./h4/a")[0]
                match = re.fullmatch(r"/acm/contest/([0-9]+)", link.get("href", ""))
                times = re.findall(
                    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", content(row.xpath(".//li[@class='match-time-icon']"))
                )
                if not match or len(times) != 2:
                    raise ValueError
                start, end = [
                    int(datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE).timestamp())
                    for t in times
                ]
                identifier = match[1]
                result.append(
                    contest(
                        "nk:" + identifier,
                        "nk",
                        "牛客",
                        category,
                        content([link]),
                        start,
                        end - start,
                        _nowcoder_rated(row),
                        "https://ac.nowcoder.com/acm/contest/" + identifier,
                    )
                )
    except (IndexError, TypeError, ValueError, OverflowError):
        raise UpstreamError("Nowcoder: invalid contest row") from None
    return tuple(result)
