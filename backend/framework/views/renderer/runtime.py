"""Data/resource inputs for the copied renderer tree; no bot state or networking."""

import io
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pixie
from PIL import Image


@dataclass
class Help:
    command: str
    help: str
    restriction: str | None = None
    id: str = ""


@dataclass
class PickOne:
    ids: list[tuple[str, int]]
    conf: dict[str, SimpleNamespace]


@dataclass
class PickOneImgStat:
    hash_id: str
    likes: int
    comments: int
    pickup_times: int
    add_time: float = 0
    thumbnail: bytes | None = None


def format_timestamp(timestamp: int, chinese_weekday_format: bool = True) -> str:
    # Keep upstream's date/weekday/seconds presentation, explicitly in Beijing time.
    weekdays = (
        ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        if chinese_weekday_format
        else ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    )
    dt = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8)))
    return dt.strftime("%y/%m/%d ") + weekdays[dt.weekday()] + dt.strftime(" %H:%M:%S")


def format_timestamp_diff(diff: int) -> str:
    abs_diff = abs(diff)
    if abs_diff == 0:
        return "刚刚"

    if abs_diff < 60:
        info = f"{abs_diff}秒"
    elif abs_diff < 3600:
        minutes = abs_diff // 60
        info = f"{minutes}分钟"
    elif abs_diff < 86400:
        hours = abs_diff // 3600
        info = f"{hours}小时"
    else:
        days = abs_diff // 86400
        if days >= 365:
            years = days // 365
            info = f"{years}年"
        elif days >= 30:
            months = days // 30
            info = f"{months}个月"
        elif days >= 14:
            weeks = days // 7
            info = f"{weeks}周"
        else:
            info = f"{days}天"

    return f"{info}{'后' if diff < 0 else '前'}"


def format_seconds(seconds: int) -> str:
    units_in_seconds = [
        ["天", 365 * 24 * 3600, 24 * 3600],
        ["小时", 24 * 3600, 3600],
        ["分钟", 3600, 60],
        ["秒", 60, 1],
    ]
    return "".join(
        [
            f" {seconds % u_mod // u_div} {name}"
            for name, u_mod, u_div in units_in_seconds
            if seconds % u_mod // u_div > 0
        ]
    ).strip()


def rgb_luminance(red: float, green: float, blue: float) -> float:
    return 0.299 * red + 0.587 * green + 0.114 * blue


def coord_x_centralize(width: int, left: int, right: int) -> int:
    return max(left, left + (right - left - width) // 2)


def img_fit_in_bounds(data, bounds, max_scale):
    # Original img_fit_in_bounds, using already-fetched bytes rather than the
    # old global PickOne directory. No network or business storage in drawing.
    with Image.open(io.BytesIO(data)) as raw:
        raw.seek(0)
        frame = raw.convert("RGBA")
        scale = min(bounds[0] / frame.width, bounds[1] / frame.height, max_scale)
        size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
        if size != frame.size:
            frame = frame.resize(size, Image.Resampling.LANCZOS)
        with tempfile.TemporaryDirectory(prefix="obot-pickone-render-") as directory:
            path = Path(directory) / "thumbnail.png"
            frame.save(path, format="PNG")
            return pixie.read_image(str(path))
