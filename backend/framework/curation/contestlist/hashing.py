"""比赛条目的身份：`平台 + 开始时间 + 名称` 的哈希。

**为什么不用下标。** 原来的做法是「第 N 条」，靠位置指代一条比赛。位置是假的身份：
Bot 往列表中间插一场比赛，后面所有下标都会错位，一条「修改第 3 条」的提交单
就可能落到另一场比赛上（下发时的冲突检测能挡住，但那是补救，不是设计）。
哈希跟着内容走，插队、删除、重排都不会改变某一场比赛的身份。

哈希取自唯一性判据（见 `types.IDENTITY_FIELDS`），与上游 `ManualContest.__eq__`
判断「是不是同一场比赛」用的完全是同一组字段 —— 两处必须一致，否则会出现
「上游认为这是两场、本工具认为是同一场」的裂缝。

哈希是**派生值**，只用来在接口和提交单里指代条目，不写进 JSON 文件：
上游的 `ManualContest` 不认识这个键，多写一个字段会让文件与 Bot 写出来的不一样。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .types import IDENTITY_FIELDS

# 哈希长度够了：这是「一个文件里的几十条比赛」的标识，不是密码学用途。
# 取 16 位十六进制（64 bit）在一万个条目下碰撞概率也可以忽略，而它要出现在
# 提交单的 img_key 里、要能人手抄，太长只会碍事。
HASH_LENGTH = 16


def _text_of(value: Any) -> str:
    """本模块自带一份取值归一化，**刻意不从 store 里 import**：store 要用 hash_of，
    反过来 hashing 再依赖 store 就成了循环导入。"""
    if value is None:
        return ""
    return str(value).strip()


def _int_of(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def identity_of(entry: dict[str, Any]) -> tuple[Any, ...]:
    """一场比赛的身份：开始时间 + 平台 + 名称（与上游 ManualContest.__eq__ 一致）。

    取字段的顺序就是 `types.IDENTITY_FIELDS` 的顺序，所以两处永远同步。
    开始时间统一按整数算：文件里写成 `"1768315000"` 和写成 `1768315000`
    必须是同一场比赛。
    """
    normalized: dict[str, Any] = {
        "start_time": _int_of(entry.get("start_time")) or _text_of(entry.get("start_time")),
        "platform": _text_of(entry.get("platform")),
        "name": _text_of(entry.get("name")),
    }
    return tuple(normalized[field] for field in IDENTITY_FIELDS)


def hash_of(entry: dict[str, Any]) -> str:
    """算出条目身份哈希。同一场比赛（判据相同）永远得到同一个值。"""
    return hash_of_identity(identity_of(entry))


def hash_of_identity(identity: tuple[Any, ...]) -> str:
    # 用 0x1f（单元分隔符）拼：它不会出现在平台名或比赛名里，
    # 所以 ("ab", "c") 和 ("a", "bc") 不会算出同一个哈希。
    raw = "\x1f".join(str(part) for part in identity)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def badge_of(entry: dict[str, Any]) -> str:
    """界面与日志里指代一场比赛的写法：`ICPC · 济南区域赛`。

    简称缺失时退回比赛名称；两边都没有时给一个占位而不是空字符串，
    否则界面上会出现一行看着像坏了的数据。放在这里是因为 store 与 changes
    都要用它，而它们之间不能互相 import。
    """
    platform = _text_of(entry.get("platform"))
    abbr = _text_of(entry.get("abbr")) or _text_of(entry.get("name"))
    if platform and abbr:
        return f"{platform} · {abbr}"
    return platform or abbr or "（未命名）"
