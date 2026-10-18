"""比赛列表的数据访问层：读写 OBot-ACM 的 `manual_contests.json`。

这个文件**不是**本工具独占的 —— OBot-ACM 的 `/导入比赛` 指令（MOD 权限）
会往同一个文件里追加比赛，所以：

  1. 写盘用「临时文件 + os.replace」原子替换，并发读不会看到半截 JSON；
  2. 下发前要按提交时的快照做冲突检测（见 `changes.py`），不能直接覆盖。

文件布局：一个 JSON 数组，元素就是上游 `ManualContest` 的 dataclass 字段：

    [{"platform": "ICPC", "abbr": "滚木区域赛", "name": "第50届…",
      "start_time": 1768315000, "duration": 18000, "supplement": "说的道理大学"}, …]

**身份是 `平台 + 开始时间 + 名称` 的哈希**（见 hashing.py），不是下标：Bot 往数组里
插入或删除比赛都不会让在途的提交单指到别的条目上。编辑、删除都就地操作、不重排数组，
这样 Bot 按时间顺序追加进来的顺序（也就是它渲染出来的顺序）不会因为 Web 端改一条就被打乱。
改掉那三个字段等于换了一场比赛 —— 同一批下发里的其它提交跟着改名走（见 changes.py），
而跨批次时旧哈希已经不在文件里了，提交单会判成冲突交给管理员看清楚。

字段未知的键会原样保留（上游以后加字段时，Web 端不会把它抹掉）。
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# 错误类型必须来自框架：核心已经给它们装好了 400/404 处理器，
# 插件自己再定义一套只会得到 500 而不是 400/404。
from framework.curation.errors import NotFoundError, StoreError, ValidationError

from .hashing import badge_of, hash_of
from .types import FIELD_ORDER, MAX_PLATFORM_LENGTH

# 时间与长度的上限。都是为了在提交阶段就挡住明显录错的数据：
# 上游拿到不合法的时间戳会渲染出一张荒唐的卡片，而 Bot 那边不会报错。
MIN_START_TIME = 946_684_800  # 2000-01-01
MAX_START_TIME = 4_102_444_800  # 2100-01-01
MIN_DURATION = 60  # 1 分钟
MAX_DURATION = 604_800  # 7 天（ICPC 区域赛是 5 小时，7 天足够宽松）
MAX_ABBR_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_SUPPLEMENT_LENGTH = 128
MAX_NOTE_LENGTH = 200

# 一次提交里给前端回显的「来源」标记，避免在三个函数里各自拼一遍。
SOURCE_BASE = "base"
SOURCE_CURRENT = "current"
SOURCE_SUBMITTED = "submitted"


class Phase(Enum):
    """与上游 `DynamicContestPhase` 一致，用来在浏览页标出比赛处在哪个阶段。"""

    UPCOMING = "upcoming"
    RUNNING = "running"
    ENDED = "ended"

    @property
    def label(self) -> str:
        return {"upcoming": "未开始", "running": "进行中", "ended": "已结束"}[self.value]


# ------------------------------------------------------------------ 取值与校验


def text_of(value: Any) -> str:
    """任意值 -> 去掉首尾空白的字符串（None -> 空串）。"""
    if value is None:
        return ""
    return str(value).strip()


def int_of(value: Any) -> int | None:
    """尽可能把值解析成整数；解析不了返回 None（由调用方决定怎么报错）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def phase_of(entry: dict[str, Any], now: float | None = None) -> Phase:
    """与上游 `DynamicContest.get_phase()` 同样的判定。"""
    current = int(now if now is not None else time.time())
    start = int_of(entry.get("start_time")) or 0
    duration = int_of(entry.get("duration")) or 0
    if current < start:
        return Phase.UPCOMING
    if current > start + duration:
        return Phase.ENDED
    return Phase.RUNNING


def validate_short_text(value: Any, label: str, limit: int, *, required: bool) -> str:
    text = text_of(value)
    if not text:
        if required:
            raise ValidationError(f"{label}不能为空")
        return ""
    if len(text) > limit:
        raise ValidationError(f"{label}过长（上限 {limit} 字）")
    return text


def validate_platform(value: Any) -> str:
    """平台：自由文本，只限长度。

    上游 `ManualContest.platform` 就是个字符串，Bot 的 `/导入比赛` 也收任意值，
    所以这里**不设受控词表** —— 加了枚举就意味着新平台（校赛、AtCoder、
    Codeforces…）要么录不进去，要么被硬塞进某个兜底取值里。
    """
    return validate_short_text(value, "平台", MAX_PLATFORM_LENGTH, required=True)


def validate_start_time(value: Any) -> int:
    stamp = int_of(value)
    if stamp is None:
        raise ValidationError("开始时间必须是整数时间戳（秒）")
    if not MIN_START_TIME <= stamp <= MAX_START_TIME:
        raise ValidationError("开始时间超出合理范围（2000-01-01 ~ 2100-01-01）")
    return stamp


def validate_duration(value: Any) -> int:
    seconds = int_of(value)
    if seconds is None:
        raise ValidationError("时长必须是整数秒")
    if not MIN_DURATION <= seconds <= MAX_DURATION:
        raise ValidationError(
            f"时长必须是 {MIN_DURATION} ~ {MAX_DURATION} 秒（{MIN_DURATION // 60} 分钟 ~ 7 天）"
        )
    return seconds


def clean_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """把一份提交内容校验并归一化成写盘用的字段值。

    返回值一定包含 `FIELD_ORDER` 里的全部键、顺序固定、且类型正确 ——
    下游（changes.py）用 `==` 比对字段时不会因为键顺序不同而误判。
    """
    return {
        "platform": validate_platform(raw.get("platform")),
        "abbr": validate_short_text(raw.get("abbr"), "简称", MAX_ABBR_LENGTH, required=True),
        "name": validate_short_text(raw.get("name"), "比赛名称", MAX_NAME_LENGTH, required=True),
        "start_time": validate_start_time(raw.get("start_time")),
        "duration": validate_duration(raw.get("duration")),
        "supplement": validate_short_text(
            raw.get("supplement"), "补充信息", MAX_SUPPLEMENT_LENGTH, required=False
        ),
    }


# ------------------------------------------------------------------ 数据模型


@dataclass
class Contest:
    """一条比赛。

    `index` 只是它在**数组里的位置**，为写盘服务（改第 N 项、删第 N 项），
    不对外暴露成身份 —— 对外一律用 `hash`（见 hashing.py）。
    """

    index: int
    fields: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)
    valid: bool = True
    problems: list[str] = field(default_factory=list)
    # 校验失败的条目没有可信的字段，哈希按文件里的原值算（仍然稳定可寻址）
    hash: str = ""

    def badge(self) -> str:
        """列表里的显示格式：`ICPC · 济南区域赛`。"""
        return badge_of(self.fields)

    def phase(self, now: float | None = None) -> Phase:
        return phase_of(self.fields, now)

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        phase = self.phase(now)
        return {
            "hash": self.hash,
            "badge": self.badge(),
            "platform": self.fields.get("platform", ""),
            "abbr": self.fields.get("abbr", ""),
            "name": self.fields.get("name", ""),
            "start_time": self.fields.get("start_time", 0),
            "duration": self.fields.get("duration", 0),
            "supplement": self.fields.get("supplement", ""),
            "phase": phase.value,
            "phase_label": phase.label,
            "valid": self.valid,
            "problems": list(self.problems),
            "extra_keys": sorted(self.extra),
        }

    def snapshot(self) -> dict[str, Any]:
        """提交时记下的「原值」：就是写盘用的那份字段字典。"""
        return dict(self.fields)


def _parse_entries(raw: list[Any]) -> list[Contest]:
    """把文件内容归一化成 Contest 列表，坏条目只在界面上标记，不阻断整个列表。"""
    contests: list[Contest] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            contests.append(
                Contest(
                    index=position,
                    fields=dict.fromkeys(FIELD_ORDER, ""),
                    extra={},
                    valid=False,
                    problems=["这一项不是对象，格式不对"],
                    hash=hash_of({}),
                )
            )
            continue

        fields = {key: item[key] for key in FIELD_ORDER if key in item}
        # 身份哈希按「文件里怎么写的」算：校验失败时字段会被搬空，那样算出来的
        # 哈希对不上任何东西，界面上就成了一行没法操作的坏数据。
        identity_source = dict(fields)
        extra = {key: value for key, value in item.items() if key not in FIELD_ORDER}
        problems: list[str] = []
        try:
            fields = clean_fields(fields)
        except ValidationError as error:
            problems.append(str(error))
            fields = {key: item[key] for key in FIELD_ORDER if key in item}

        contests.append(
            Contest(
                index=position,
                fields=fields,
                extra=extra,
                valid=not problems,
                problems=problems,
                hash=hash_of(identity_source),
            )
        )
    return contests


class ContestStore:
    """`manual_contests.json` 的读写入口。"""

    def __init__(self, data_path: Path) -> None:
        self.data_path = Path(data_path)

    # ---------- 读 ----------

    def load_raw(self) -> list[Any]:
        """读原始 JSON 数组。文件不存在时返回空列表（Bot 也是这么兜底的）。"""
        try:
            text = self.data_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise StoreError(f"读取比赛列表失败: {error}") from error

        if not text.strip():
            return []
        try:
            # object_pairs_hook 让「同一个键出现两次」在解析期就报错，
            # 否则 json 会静默丢掉前一个值。
            raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        except ValueError as error:
            raise ValidationError(f"比赛列表不是合法的 JSON: {error}") from error
        if not isinstance(raw, list):
            raise ValidationError("比赛列表格式不对：顶层应该是一个数组")
        return raw

    def load(self) -> list[Contest]:
        return _parse_entries(self.load_raw())

    def find(self, hash_: str) -> Contest | None:
        """按身份哈希找一条。列表里没有这个身份时返回 None。

        哈希是唯一性判据算出来的，所以「找不到」通常意味着那场比赛被删了，
        或者它的平台/开始时间/名称被改成了别的值。
        """
        for contest in self.load():
            if contest.hash == hash_:
                return contest
        return None

    def entry_or_404(self, hash_: str) -> Contest:
        contest = self.find(hash_)
        if contest is None:
            raise NotFoundError(f"找不到这场比赛（{hash_}）：可能已被删除，或它的平台/时间/名称被改过")
        return contest

    def entries_by_hash(self) -> dict[str, list[Contest]]:
        """按哈希分组。同一身份在文件里出现两次时，这个列表会有两个元素。

        Bot 的 `save_contest` 会拒绝重复，但历史文件未必干净；重复条目没有唯一
        的寻址目标，所以下发时会被判成冲突而不是随便改一条。
        """
        grouped: dict[str, list[Contest]] = {}
        for contest in self.load():
            grouped.setdefault(contest.hash, []).append(contest)
        return grouped

    # ---------- 写 ----------

    def save(self, raw: list[Any]) -> None:
        """原子写：临时文件 + os.replace，避免并发读到半截 JSON。

        刻意不加结尾换行：上游 `JsonSerializer.save_data` 用 json.dump 直接写，
        这里保持一致，Web 端改完的文件与 Bot 写出来的逐个字节相同。
        """
        if not isinstance(raw, list):
            raise StoreError("比赛列表顶层必须是数组")

        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.data_path.with_name(
            f".{self.data_path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, ensure_ascii=False, indent=4)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.data_path)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    # ---------- 领域操作 ----------

    @staticmethod
    def merge_entry(fields: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """按上游字段顺序拼回一个条目对象，未知键原样跟在后头。

        `fields` 由 `clean_fields()` 产出（六个键都在）；这里按 FIELD_ORDER 取一次
        只是为了固定 JSON 里的键顺序，让 Web 端写出来的文件和 Bot 写出来的长得一样。
        """
        entry: dict[str, Any] = {name: fields.get(name) for name in FIELD_ORDER}
        for key, value in {**(extra or {}), **fields}.items():
            if key not in entry:
                entry[key] = value
        return entry

    def create(self, fields: dict[str, Any]) -> str:
        """追加一条，返回它的身份哈希。"""
        raw = self.load_raw()
        raw.append(self.merge_entry(fields))
        self.save(raw)
        return hash_of(fields)

    def overwrite(self, index: int, fields: dict[str, Any]) -> None:
        """就地覆盖数组里的第 index 项（下标由调用方从 `find()` 的结果拿到）。

        未知键（上游以后加的字段）原样保留：Web 端不认识它们，但也不该抹掉。
        """
        raw = self.load_raw()
        if not 0 <= index < len(raw):
            raise NotFoundError("比赛不存在")
        previous = raw[index] if isinstance(raw[index], dict) else {}
        extra = {key: value for key, value in previous.items() if key not in FIELD_ORDER}
        raw[index] = self.merge_entry(fields, extra)
        self.save(raw)

    def remove(self, index: int) -> None:
        raw = self.load_raw()
        if not 0 <= index < len(raw):
            raise NotFoundError("比赛不存在")
        del raw[index]
        self.save(raw)

    # ---------- 体检与统计 ----------

    def duplicate_hashes(self) -> list[tuple[str, list[int]]]:
        """同一个身份出现多次的条目（Bot 的 save_contest 会拒绝重复，但历史文件未必干净）。"""
        grouped = self.entries_by_hash()
        return [
            (hash_, [contest.index for contest in items])
            for hash_, items in grouped.items()
            if len(items) > 1
        ]

    def summary(self) -> dict[str, Any]:
        contests = self.load()
        invalid = [contest for contest in contests if not contest.valid]
        duplicates = self.duplicate_hashes()
        return {
            "total": len(contests),
            "invalid": len(invalid),
            "duplicates": len(duplicates),
            "file_available": self.data_path.is_file(),
            "dir_available": self.data_path.parent.is_dir(),
        }

    def conflicts_with(self, fields: dict[str, Any], *, exclude_hash: str | None = None) -> str | None:
        """新内容与列表中其它条目撞唯一性判据时，返回那个条目的身份哈希。

        `exclude_hash` 用于「改这一条」：它自己当然和自己是同一个身份。
        """
        wanted = hash_of(fields)
        if wanted == exclude_hash:
            return None
        for contest in self.load():
            if contest.hash == wanted:
                return contest.hash
        return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"对象里出现了重复的键 {key!r}")
        result[key] = value
    return result
