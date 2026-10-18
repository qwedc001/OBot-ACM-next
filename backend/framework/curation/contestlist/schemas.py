"""算法竞赛列表的请求模型。

共享审核请求模型位于 `web/content/review.py`；
「提交什么内容」是这个工具自己的事。

这里只做「形状」校验（字段在不在、是不是那个类型、长度上限），
值域的校验统一放在 `store.clean_fields()` —— 那边同时被下发路径复用，
两处各写一份迟早会不一致。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .store import MAX_NOTE_LENGTH
from .types import MAX_PLATFORM_LENGTH


class ContestFields(BaseModel):
    """一场比赛的全部可编辑字段（与上游 ManualContest 一一对应）。

    `platform` 是自由文本，这里只限长度、不做枚举校验：上游没有受控词表，
    实际列表里 ICPC / CCPC / 校赛 / AtCoder 都有（见 types.MAX_PLATFORM_LENGTH）。
    """

    platform: str = Field(default="", max_length=MAX_PLATFORM_LENGTH)
    abbr: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=128)
    # 用 int 而不是 datetime：文件里存的就是 Unix 时间戳（秒），
    # 前端的时间选择器负责转换，接口这一层保持与文件同构。
    start_time: int = Field(default=0)
    duration: int = Field(default=0)
    supplement: str = Field(default="", max_length=128)

    def as_raw(self) -> dict[str, object]:
        return self.model_dump()


class ContestCreateRequest(BaseModel):
    contest: ContestFields
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


class ContestUpdateRequest(BaseModel):
    """修改哪一场。`hash` 是那场比赛的身份哈希（平台+开始时间+名称），不是下标。"""

    hash: str = Field(min_length=1, max_length=64)
    contest: ContestFields
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


class ContestDeleteRequest(BaseModel):
    """删除哪一场（只有管理员能提）。"""

    hash: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


class ApplyRequest(BaseModel):
    dry_run: bool = False


class ConflictResolveRequest(BaseModel):
    """冲突裁定：保留提交的新值（写入），或丢弃提交（保持磁盘现状）。

    `allow_duplicate` 是给「文件里本来就有两条重复比赛」这种历史遗留数据留的口子：
    正常情况下写入会撞唯一性判据（平台+开始时间+名称）而被拒绝，管理员确认过
    确实要写成重复时，可以显式放行。
    """

    keep_new: bool
    allow_duplicate: bool = False
