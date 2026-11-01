"""Platform-neutral public profile values; no IO, bot or drawing dependencies."""

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

PROFILE_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ProfileError(Exception):
    """An expected, safe-to-display query error."""


class ProfileNotFound(ProfileError):
    def __init__(self):
        super().__init__("没有找到该账号，请检查账号标识。")


def clean(value: object, limit: int = 140) -> str:
    text = " ".join(str(value).split()) if value is not None else ""
    text = "".join(char for char in text if char.isprintable())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def number(value: str) -> int | None:
    match = re.match(r"^-?\d[\d,]*", value.strip())
    return int(match[0].replace(",", "")) if match else None


@dataclass(frozen=True)
class ProfileSection:
    title: str
    lines: tuple[str, ...]


def date_text(timestamp: int | float) -> str:
    return datetime.fromtimestamp(timestamp, PROFILE_TIMEZONE).strftime("%Y/%m/%d %H:%M")


@dataclass(frozen=True)
class Profile:
    platform: str
    handle: str
    url: str
    rating: int | None = None
    rank: str = "Unrated"
    metrics: tuple[tuple[str, str], ...] = ()
    details: tuple[str, ...] = ()
    avatar_url: str | None = None
    rating_note: str = ""
    timeline: tuple[str, ...] = ()
    sections: tuple[ProfileSection, ...] = ()
    warnings: tuple[str, ...] = ()
    is_team: bool = False

    def text(self) -> str:
        return "\n".join(
            filter(
                None,
                (
                    f"{self.platform} · {self.handle}",
                    f"Rating {self.rating if self.rating is not None else 'Unrated'} · {self.rank}",
                    self.rating_note,
                    *self.details,
                    *self.timeline,
                    *(f"{label}：{value}" for label, value in self.metrics),
                    *(f"{section.title}\n" + "\n".join(section.lines) for section in self.sections),
                    *self.warnings,
                    self.url,
                ),
            )
        )
