"""提交单 -> 数据文件的落盘逻辑：三方合并、冲突检测、一键下发。

冲突从哪来：`manual_contests.json` 是 Bot（`/导入比赛` 指令）与 Web 端共同写入的文件。
一条提交单在「用户提交」和「管理员下发」之间，磁盘上的内容可能已经变了 ——
Bot 追加了新比赛，或者另一个人改了同一条。所以本模块的流程是：

  1. 下发时逐字段做三方比对：**提交时的快照 / 磁盘现值 / 用户提交的新值**
  2. 只有「用户真的改了、而且磁盘上还是原样」的字段才会写入；
     用户没碰的字段一律保留磁盘现值（Bot 的修改因此不会被抹掉）
  3. 同一条比赛的**同一个字段**被两个人都改过（各自基于当时的原值）时，只有
     **最早过审**的那条能下发，其余的转 `conflict` 挂起 —— 判断标准是过审的先后，
     不是提交的先后：先审过的那条才是管理员认可的值。两种冲突都**不写盘**。
  4. 管理员在冲突裁定里看到三方对比后决定：保留新值（交换到待下发，由下一次
     一键下发写盘）或丢弃提交

条目身份是 `平台 + 开始时间 + 名称` 的哈希（见 hashing.py），不是下标：

  - Bot 往列表里插比赛、删比赛都不会让在途提交单指向别的条目；
  - **改掉这三个字段就等于换了一场比赛**，但在同一批下发里这不是问题：一批是一起
    写盘的，后面的提交单跟着改名走到新哈希上就行（见 `build_apply_plan` 的 renamed），
    没有「某个提交依赖旧哈希」这种情况；
  - 文件里同一个身份出现两次时无法唯一寻址，仍判成冲突（那是文件脏了，不是谁改了同一处）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from framework.curation.repository import (
    OPEN_STATUSES,
    STATUS_APPROVED,
    STATUS_CONFLICT,
    Repository,
    Submission,
)

from . import config, types
from .hashing import badge_of, hash_of
from .store import (
    Contest,
    ContestStore,
    StoreError,
    ValidationError,
    clean_fields,
)
from .types import FIELD_LABELS, FIELD_ORDER

# 插件自身的 slug：所有提交单都要打上它，审核队列与一键下发才知道归属。
SLUG = config.SLUG

# 删除类提交单没有「新值」，用这个标记占位（提交单的 submitted_value 不能为空）
DELETE_MARKER = {"deleted": True}


class ApplyBlockedError(Exception):
    """裁定「保留新值」时目标已经写不进去了（比赛被删、唯一性判据撞车等）。"""


@dataclass
class Operation:
    """一条提交单解析出来的动作，供冲突判定与写盘共用。

    `field` 是这条提交单负责的字段名（单字段提交）；为 None 表示「整条比赛」，
    也就是新增类提交。
    """

    type: str
    fields: dict[str, Any]
    field: str | None = None

    @property
    def is_create(self) -> bool:
        return self.type == types.TYPE_CREATE

    @property
    def is_delete(self) -> bool:
        return self.type == types.TYPE_DELETE

    @property
    def action_label(self) -> str:
        return types.TYPE_LABELS.get(self.type, self.type)


@dataclass
class Conflict:
    """一条无法直接应用的提交，以及裁定它需要的全部信息。

    三方对比由界面自己拼：`field` 指明是哪一格，`base_value` / `current_value`
    是提交时与现在的**整条快照**，`submission.submitted_value` 是那个字段的新值。
    """

    submission: Submission
    reason: str
    base_value: Any = None
    current_value: Any = None
    missing: bool = False
    # 抢到了同一处的另一条提交（「同一个字段被两个人改」时才有）：裁定保留本条时
    # 它要被驳回。磁盘被 Bot 改动那种冲突没有对手，这里就是 None。
    rival: Submission | None = None

    def to_dict(self) -> dict[str, Any]:
        field_name = self.submission.target.strip() or None
        return {
            "submission_id": self.submission.id,
            "type": self.submission.type,
            "type_label": self.submission.type_label,
            "img_key": self.submission.img_key,
            "target": self.submission.target,
            "field": field_name,
            "field_label": FIELD_LABELS.get(field_name or "", ""),
            "author_name": self.submission.author_name,
            "note": self.submission.note,
            "reason": self.reason,
            "missing": self.missing,
            # base_value      提交时看到的整条快照
            # current_value   磁盘现在的那一条，或者对手将要下发的那一条
            # submitted_value 用户提交的新值（单字段提交单里是那个字段的标量）
            "base_value": self.base_value,
            "current_value": self.current_value,
            "submitted_value": self.submission.submitted_value,
            # 对手是谁：界面据此把「现值」那一栏说成「另一个用户的修改」
            "rival_id": self.rival.id if self.rival else None,
            "rival_author_name": self.rival.author_name if self.rival else "",
            "detected_at": self.submission.conflict_at,
        }


@dataclass
class ApplyPlan:
    """一次下发要落盘的内容，以及被判定冲突而挂起的提交。"""

    # 落盘后的完整数组（只在下发时求值）
    raw: list[Any] = field(default_factory=list)
    # submission_id -> 提交单生效后的值
    applied_values: dict[int, Any] = field(default_factory=dict)
    applied_ids: list[int] = field(default_factory=list)
    # 便于审核台展示的落盘摘要
    created: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    # 冲突槽位 -> 本批真正会下发的那条。刷新别的冲突时用它当「现在的对手」：
    # 对手是会换人的（裁定把另一条排回了待下发）。
    holders: dict[tuple[str, str, str], Submission] = field(default_factory=dict)


# ------------------------------------------------------------------ 解析与比对


def operation_of(submission: Submission) -> Operation:
    """把提交单解析成动作。

    **一条提交单只承载一个字段的改动**（与 PickOne 一致：改描述和改点赞是两条提交）。
    每种动作的 `submitted_value` 形状是固定的，不存在第二种可能：

      - `contest_create`  整条比赛（六个字段的对象），`target` 为空；
      - `contest_update`  那个字段的新值（标量），`target` 就是字段名 —— 没有值、
                          或者值不是标量的提交单一律当成写坏了，不猜；
      - `contest_delete`  没有值（只有删除标记），`target` 为空。

    解析时把「单字段的新值」叠回提交时的快照上，得到一个完整的字段字典 ——
    冲突判定与写盘都只看这一个字段，不必为单字段提交单写第二套逻辑。
    """
    if submission.type == types.TYPE_DELETE:
        return Operation(type=submission.type, fields={}, field=None)

    if submission.type == types.TYPE_CREATE:
        value = submission.submitted_value
        if not isinstance(value, dict):
            raise ValidationError(f"提交单 #{submission.id} 的新增内容不是对象，无法下发")
        return Operation(type=submission.type, fields=clean_fields(value), field=None)

    # 修改：只可能是单字段
    field_name = submission.target.strip()
    if not field_name:
        raise ValidationError(f"提交单 #{submission.id} 没有指明要改哪个字段，无法下发")
    if field_name not in FIELD_ORDER:
        raise ValidationError(f"提交单 #{submission.id} 的字段名不认识: {field_name!r}")
    if isinstance(submission.submitted_value, (dict, list)):
        raise ValidationError(f"提交单 #{submission.id} 的字段值形状不对，无法下发")

    base = submission.base_value if isinstance(submission.base_value, dict) else {}
    merged = dict(base)
    merged[field_name] = submission.submitted_value
    return Operation(type=submission.type, fields=clean_fields(merged), field=field_name)


def changed_fields(base: dict[str, Any], submitted: dict[str, Any]) -> list[str]:
    """用户相对提交时快照真正改动过的字段（顺序按 FIELD_ORDER）。

    这就是「要拆成几条提交单」的依据：改了几个字段就发几条，管理员可以只收其中一条。
    """
    return [
        name
        for name in FIELD_ORDER
        if (name in submitted or name in base) and submitted.get(name) != base.get(name)
    ]


def apply_order(submissions: list[Submission]) -> list[Submission]:
    """下发顺序：**最早过审**的排最前，同一时刻过审的按提交单 id。

    冲突裁定的口径就是「过审早的那条先下发」，所以这里必须按 reviewed_at 排，
    而不是按提交单 id —— 先提交不等于先被管理员认可。裁定里被排回「待下发」的提交
    会拿到新的 reviewed_at，于是自然排到队尾。
    """
    return sorted(submissions, key=lambda item: (item.reviewed_at or 0.0, item.id))


def claim_slot(submission: Submission) -> tuple[str, str, str]:
    """这条提交争的是「哪一处」。

    同一处只容得下一条改动：修改类是按字段算的（同一场比赛的不同字段互不干扰），
    删除单是整场比赛。注意删除单不参与这个竞争（只有管理员能删，见 build_apply_plan）。
    """
    return (submission.type, submission.img_key, submission.target)


def rival_conflict(submission: Submission, holder: Submission) -> Conflict:
    """同一处被两个人改过时，后过审那条的冲突。

    `current_value` 是**对手生效后的整条比赛**（和「文件被改」那种冲突同一个形状，
    界面按字段名从里面取那一格）；对手会换人，展示这一格时要用现在的持有人，
    见 `refresh_conflicts`。
    """
    field_name = FIELD_LABELS.get(submission.target.strip() or "", "")
    return Conflict(
        submission=submission,
        reason=f"#{holder.id}（{holder.author_name or '另一位用户'}）先过审，"
        f"改的是{field_name or '同一场比赛'}；本条下发会盖掉那条",
        base_value=submission.base_value,
        current_value=entry_of(holder),
        rival=holder,
    )


def _parse_conflict(submission: Submission, error: Exception) -> Conflict:
    return Conflict(
        submission=submission,
        reason=f"提交内容无法解析：{error}",
        base_value=submission.base_value,
        missing=True,
    )


# ------------------------------------------------------------------ 下发计划


def _bucket(plan: ApplyPlan, submission: Submission) -> list[dict[str, Any]]:
    if submission.type == types.TYPE_CREATE:
        return plan.created
    if submission.type == types.TYPE_DELETE:
        return plan.deleted
    return plan.updated


def _find(raw: list[Any], hash_: str) -> int | None:
    """在「本批下发之后的样子」（raw）里找一条比赛，返回它在数组里的位置。

    找不到返回 None —— 只有删除单会把它当成正常情况（目标已经被前面的删除单删掉）。
    """
    for position, item in enumerate(raw):
        if isinstance(item, dict) and hash_of(item) == hash_:
            return position
    return None


def _locate(raw: list[Any], hash_: str) -> int:
    """同 `_find`，但找不到时抛 ApplyBlockedError。

    调用方都在下发流程里，那时目标已经确认存在，找不到只可能是同批前面的动作
    把它删掉了，属于编程错误而不是用户错误。
    """
    position = _find(raw, hash_)
    if position is None:
        raise ApplyBlockedError(f"找不到这场比赛（{hash_}），可能已被删除")
    return position


def _summary(
    submission: Submission,
    operation: Operation,
    hash_: str,
    label: str,
    entry: Any,
    *,
    current_hash: str | None = None,
) -> dict[str, Any]:
    """一条将要在下发里写盘的改动（下发预览与落盘摘要共用）。

    `old_value` / `new_value` 是**给界面核对用的那一格**：下发前就是要看清
    「哪个字段、从什么变成什么」。新增类没有旧值，删除类没有新值。

    修改类的旧值取提交时的快照（`base_value`）而不是磁盘现值 —— 磁盘现值已经被
    冲突判定挡在外面了，两条一致时取哪个都一样。
    """
    name = operation.field
    if operation.is_create:
        old_value: Any = None
        new_value: Any = None
    elif operation.is_delete:
        old_value = label
        new_value = None
    else:
        old_value = (submission.base_value or {}).get(name)
        new_value = operation.fields.get(name)

    return {
        "submission_id": submission.id,
        "type": operation.type,
        "type_label": operation.action_label,
        # 这条提交单负责的字段（单字段提交）与其中文名；整条新增/删除时为 None / ''
        "field": name,
        "field_label": FIELD_LABELS.get(name or "", ""),
        # 摘要里带 hash 而不是下标：审核台展示的就是它，人也照着它核对
        "hash": hash_,
        "label": label,
        "old_value": old_value,
        "new_value": new_value,
        **identity_change(submission, current_hash=current_hash),
        "entry": entry,
    }


def entry_of(submission: Submission) -> dict[str, Any]:
    """这条提交单生效后的**整条比赛**（六字段齐全），用来算生效后的身份哈希。

    修改类是单字段提交，所以要把那一格叠回提交时的快照上；
    新增类的提交值本身就是整条；删除类没有「生效后的内容」。
    """
    if submission.type == types.TYPE_DELETE:
        return {}
    if submission.type == types.TYPE_CREATE:
        value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}
        return clean_fields(value)

    base = submission.base_value if isinstance(submission.base_value, dict) else {}
    merged = dict(base)
    field_name = submission.target.strip()
    if field_name:
        merged[field_name] = submission.submitted_value
    return clean_fields(merged)


def identity_change(submission: Submission, *, current_hash: str | None = None) -> dict[str, Any]:
    """这条提交单会不会改变比赛的身份（也就是界面上显示的 ID）。

    身份是三字段哈希，所以改「平台 / 比赛全称 / 开始时间」会让 ID 变掉 ——
    这是下发前最容易被忽略、后果又最明显的一件事（下发之后就不是原来那个 ID 了），
    所以审核弹窗与下发预览都显式标注。

    `current_hash` 是同一批里前面的改名之后的哈希：同一条比赛被连着改两次身份时，
    「改之前」应该是上一次的结果，而不是提交时那个已经作废的哈希。
    """
    before = submission.img_key if submission.type != types.TYPE_CREATE else None
    if before and current_hash:
        before = current_hash
    after = None if submission.type == types.TYPE_DELETE else hash_of(entry_of(submission))
    return {
        "id_before": before,
        "id_after": after,
        "identity_changed": bool(before and after and before != after),
    }


def build_apply_plan(repo: Repository, store: ContestStore) -> ApplyPlan:
    """把 approved 提交单分成「可以直接写盘」和「有冲突」两组。

    刻意**不落盘**：预览接口与下发接口共用它，预览因此不会碰到数据文件。

    四个「同一批」的规则：

      - 冲突判定用**磁盘现状**（一开始读的那一份）：一批下发只写一次盘，
        所以每条提交都该按「我提交之后、这批下发之前」的样子判冲突。
      - 写入依次叠加到同一份数组上：前面的改动就是后面的现值。
      - 同一处的改动只认**最早过审**的那条，后面的转冲突 ——
        否则先写的会被后写的静默覆盖，提交单却还标着「已下发」。
      - 前面的改动给比赛换了身份（改了平台 / 开始时间 / 名称）时，后面那些指向旧
        哈希的提交单跟着改名走（`renamed`），不判冲突：一批是一起下发的，不存在
        「某个提交依赖旧哈希」的情况。
    """
    plan = ApplyPlan()
    ordered = apply_order(repo.list_approved_submissions(SLUG))

    # 磁盘现状：冲突判定以它为基准。同一个身份出现两次时 entries[hash] 会有两个元素，
    # 那种情况下没有唯一的寻址目标，一律判冲突。
    entries = store.entries_by_hash()
    disk_raw = store.load_raw()

    if not ordered:
        plan.raw = list(disk_raw)
        return plan

    raw: list[Any] = list(disk_raw)
    # 同一处的改动只认最前面那条。改动是逐字段拆开提交的，
    # 所以同一场比赛有好几条不同的提交单是正常情况，只有「同一处撞车」才要拦。
    claimed: dict[tuple[str, str, str], Submission] = {}
    # 本批里已经换过身份的比赛：提交时看到的哈希 -> 它现在的哈希。
    # 同一场比赛可能在本批里被连着改两次身份，所以要顺着映射往前走。
    renamed: dict[str, str] = {}

    def current_hash_of(hash_: str) -> str:
        return renamed.get(hash_, hash_)

    def remember_rename(old_hash: str, new_hash: str) -> None:
        for key, value in list(renamed.items()):
            if value == old_hash:
                renamed[key] = new_hash
        renamed[old_hash] = new_hash

    for submission in ordered:
        try:
            operation = operation_of(submission)
        except (ValidationError, StoreError) as error:
            plan.conflicts.append(_parse_conflict(submission, error))
            continue

        # 删除单只由管理员发起（他本人就是下发的人），不参与「谁改了同一处」的竞争：
        # 目标已经被本批前面的删除单删掉时，删除的目的已经达成。
        slot = claim_slot(submission)
        earlier = None if operation.is_delete else claimed.get(slot)
        if earlier is not None:
            plan.conflicts.append(rival_conflict(submission, earlier))
            continue

        conflict = _conflict_against(entries, submission, operation)
        if conflict is not None:
            plan.conflicts.append(conflict)
            continue

        current_hash = current_hash_of(submission.img_key)
        try:
            summary = _write_into(raw, store, operation, submission, current_hash)
        except ApplyBlockedError as error:
            # 兜底：定位不到就挂冲突，别让整个下发 500
            plan.conflicts.append(
                Conflict(
                    submission=submission,
                    reason=str(error),
                    base_value=submission.base_value,
                    current_value=None,
                )
            )
            continue

        if not operation.is_delete:
            claimed[slot] = submission
        if summary.get("identity_changed"):
            remember_rename(current_hash, summary["hash"])
        _bucket(plan, submission).append(summary)
        plan.applied_ids.append(submission.id)
        plan.applied_values[submission.id] = summary["entry"]

    plan.raw = raw
    plan.holders = claimed
    return plan


def _conflict_against(
    entries: dict[str, list[Contest]], submission: Submission, operation: Operation
) -> Conflict | None:
    """按磁盘现状判定一条提交单能不能直接下发。"""
    base_value = submission.base_value if isinstance(submission.base_value, dict) else None

    if operation.is_create:
        return None

    matches = entries.get(submission.img_key, [])
    if not matches:
        if operation.is_delete:
            # 删除单只由管理员发起，而他本人就是下发的人：列表里已经没有这一条了
            # （更早的批次删掉了，或哈希被改过），删除的目的已经达成，不必再挂冲突
            # 让他点一次 —— 那种冲突他也无从裁定，只能丢弃。
            return None
        return Conflict(
            submission=submission,
            reason="比赛不存在，可能已被删除，或「平台 / 开始时间 / 名称」被修改",
            base_value=base_value,
            current_value=None,
            missing=True,
        )

    if len(matches) > 1:
        current = matches[0].snapshot()
        return Conflict(
            submission=submission,
            reason=f"文件里有 {len(matches)} 条比赛的「平台 + 开始时间 + 名称」完全相同，请先清理重复条目",
            base_value=base_value,
            current_value=current,
        )

    current = matches[0].snapshot()

    if operation.is_delete:
        # 删除提交：整条都不要了，只要求「这条还在、身份没变」（上面已经确认过）
        return None

    # 单字段提交：只关心**自己那个字段**有没有被别人改过。
    #
    # 别的字段变了与本条无关 —— 逐字段拆分提交的意义正是如此：Bot 改了「比赛地点」
    # 不该把别人待审的「时长」改动一起打回。冲突判定严格对着自己的那一格，
    # 下发时也只写那一格（见 _write_into），两边是同一个口径。
    name = operation.field
    base_one = (base_value or {}).get(name)
    if base_value is not None and current.get(name) != base_one:
        return Conflict(
            submission=submission,
            reason=f"提交之后「{FIELD_LABELS.get(name, name)}」已被改动",
            base_value=base_value,
            current_value=current,
        )
    return None


def _write_into(
    raw: list[Any],
    store: ContestStore,
    operation: Operation,
    submission: Submission,
    current_hash: str,
) -> dict[str, Any]:
    """把一个动作叠加到数组上，返回供审核台展示的摘要。

    `current_hash` 是这条提交现在指向的哈希：前面的改动可能已经给这场比赛换了身份，
    那就得按新哈希去找它。
    """
    if operation.is_create:
        entry = store.merge_entry(operation.fields)
        raw.append(entry)
        return _summary(submission, operation, hash_of(entry), badge_of(entry), entry)

    if operation.is_delete:
        position = _find(raw, current_hash)
        if position is None:
            # 本批里前面的删除单已经把它删掉了：删除的目的已经达成，记成已下发即可
            # （删除单只有管理员能发起，不该为这个挂冲突）。
            base = submission.base_value if isinstance(submission.base_value, dict) else {}
            return _summary(submission, operation, current_hash, badge_of(base), None)
        removed = raw[position]
        del raw[position]
        label = badge_of(removed if isinstance(removed, dict) else {})
        return _summary(submission, operation, current_hash, label, removed)

    position = _locate(raw, current_hash)

    # 现值取数组里的那一条：同批里前面的改动已经叠加上去了，
    # 所以这里读到的是「最后会被写进文件的样子」。
    current_raw = raw[position]
    current = current_raw if isinstance(current_raw, dict) else {}
    extra = {key: value for key, value in current.items() if key not in FIELD_ORDER}

    # 修改只可能是单字段：只写自己那个字段，其余字段以**磁盘现值**为准。
    # 这样两个管理员分别通过了「改简称」和「改时间」两条提交单时，
    # 第二条不会把第一条的结果覆盖掉。
    assert operation.field is not None
    entry = store.merge_entry(dict(current), extra)
    entry[operation.field] = operation.fields[operation.field]
    raw[position] = entry
    return _summary(submission, operation, hash_of(entry), badge_of(entry), entry, current_hash=current_hash)


def _persist_conflicts(repo: Repository, conflicts: list[Conflict]) -> list[dict[str, Any]]:
    """把冲突挂到 conflict 状态（只动数据库，不碰数据文件）。"""
    payloads = [conflict.to_dict() for conflict in conflicts]
    for conflict in conflicts:
        repo.mark_conflict(conflict.submission.id, conflict.to_dict())
    return payloads


def apply_approved(repo: Repository, store: ContestStore) -> dict[str, Any]:
    """一键下发：把 approved 提交单写回数据文件，冲突单转 conflict 状态挂起。"""
    plan = build_apply_plan(repo, store)

    # 在本次 ORM 事务内记录冲突；写盘失败时事务回滚，下次重新比对。
    _persist_conflicts(repo, plan.conflicts)

    if plan.applied_ids:
        store.save(plan.raw)

    for submission_id in plan.applied_ids:
        repo.mark_applied(submission_id, plan.applied_values.get(submission_id))

    return {
        "applied": len(plan.applied_ids),
        "submissions": len(plan.applied_ids),
        "created": len(plan.created),
        "updated": len(plan.updated),
        "deleted": len(plan.deleted),
        "conflicts": [conflict.to_dict() for conflict in plan.conflicts],
        "details": [*plan.created, *plan.updated, *plan.deleted],
    }


def scan_conflicts(repo: Repository, store: ContestStore) -> dict[str, Any]:
    """体检一遍「已通过待下发」的提交单：挂起新的冲突，并刷新已经挂起的那些。

    为什么需要它：冲突判定平时只在**下发**那一瞬间做，而 `preview_approved` 是只读的
    （dry-run 不能改状态）。于是「两个人改了同一处」「提交后磁盘上那条被别人改过」
    这些冲突，在下发之前只出现在下发预览的提醒里，**冲突待裁定表里是空的** ——
    点「去处理冲突」过去什么也没有。

    这里让审核台在需要时主动跑一遍同一套判定（`build_apply_plan` 全程不碰数据文件），
    把结果持久化。反复调用是幂等的：`mark_conflict` 只对 approved 生效，
    已经挂起的不会再动。

    挂起的冲突还要**重新对一遍对手**（`refresh_conflicts`）：裁定保留 #2 时 #1 被
    驳回、#2 排回待下发，那么同一处上还挂着的 #3 该对照的就是 #2 而不是 #1。

    语意上仍然是「审核通过 + 待下发」：状态走的是 conflict 分支，但**没有写盘**，
    真正的写入还是要管理员裁定后点一键下发。
    """
    plan = build_apply_plan(repo, store)
    payloads = _persist_conflicts(repo, plan.conflicts)
    return {
        "conflicts": payloads,
        "total": len(payloads),
        "submission_ids": [conflict.submission.id for conflict in plan.conflicts],
        "refreshed_ids": refresh_conflicts(repo, store, plan.holders),
    }


def refresh_conflicts(
    repo: Repository, store: ContestStore, holders: dict[tuple[str, str, str], Submission]
) -> list[int]:
    """让已经挂起的冲突重新对上「现在的对手」，返回被改写的提交单 id。

    只改 `conflict_detail`（三方对比），状态一律不动：回不回「待下发」是管理员裁定
    的事，体检只负责让界面上的对照物跟得上现状。
    """
    rows, _ = repo.list_submissions(plugin=SLUG, status=STATUS_CONFLICT, limit=200)
    refreshed: list[int] = []
    for row in rows:
        if row.type == types.TYPE_DELETE:
            # 删除单不参与「谁改了同一处」的竞争（见 build_apply_plan）
            continue
        holder = holders.get(claim_slot(row))
        if holder is None and not (row.conflict_detail or {}).get("rival_id"):
            # 既没有对手、当初也不是「撞车」那种冲突（磁盘被改 / 比赛没了 / 文件里有
            # 重复条目）：那是另一套判定，理由由下发时重算，体检不该改写它
            continue
        detail = _conflict_now(store, row, holder)
        if detail is None or detail == row.conflict_detail:
            continue
        repo.refresh_conflict_detail(row.id, detail)
        refreshed.append(row.id)
    return refreshed


def _conflict_now(
    store: ContestStore, submission: Submission, holder: Submission | None
) -> dict[str, Any] | None:
    """这条冲突现在的对照物：还在待下发的对手，或者文件现值。

    提交内容本身解析不了（写坏了的提交单）时返回 None —— 那条理由说的是「这条数据
    有问题」，不该被「谁先过审」盖掉。对手走了而又没有别人改过文件时也不能留着那条
    旧的对照物：显示成「现在下发不会盖掉任何东西」。
    """
    try:
        operation = operation_of(submission)
    except (ValidationError, StoreError):
        return None

    if holder is not None:
        return rival_conflict(submission, holder).to_dict()

    entries = store.entries_by_hash()
    disk = _conflict_against(entries, submission, operation)
    if disk is not None:
        return disk.to_dict()

    matches = entries.get(submission.img_key, [])
    if operation.is_create or operation.is_delete or len(matches) != 1:
        return None
    return Conflict(
        submission=submission,
        reason="原先与它冲突的那条已经不在待下发里了，文件里也没有别人改过这一处",
        base_value=submission.base_value,
        current_value=matches[0].snapshot(),
    ).to_dict()


def preview_approved(repo: Repository, store: ContestStore) -> dict[str, Any]:
    """下发前的 dry-run：只列出将要写入的内容与被挂起的冲突，不碰磁盘。"""
    plan = build_apply_plan(repo, store)
    return {
        "submissions": len(plan.applied_ids),
        "created": plan.created,
        "updated": plan.updated,
        "deleted": plan.deleted,
        "details": [*plan.created, *plan.updated, *plan.deleted],
        "conflicts": [conflict.to_dict() for conflict in plan.conflicts],
    }


# ------------------------------------------------------------------ 冲突裁定


def _unique_target(store: ContestStore, submission: Submission) -> Any:
    """裁定「保留新值」前定位目标。

    身份哈希必须唯一命中：找不到说明这场比赛已经不在列表里了；命中多条说明
    文件里有重复条目，没法确定要改哪一条 —— 两种情况都只能拒绝写入。
    """
    matches = store.entries_by_hash().get(submission.img_key, [])
    if not matches:
        raise ApplyBlockedError("比赛不存在，无法写入")
    if len(matches) > 1:
        raise ApplyBlockedError(
            f"文件里有 {len(matches)} 条比赛的「平台 + 开始时间 + 名称」完全相同，请先清理重复条目"
        )
    return matches[0]


def _ensure_identity_free(store: ContestStore, fields: dict[str, Any], *, exclude_hash: str | None) -> None:
    """写入之后不能出现两条「同一场比赛」（撞了 Bot 的 match_dict 会随机挑一条）。

    `exclude_hash` 是这条提交自己的身份：它当然「和自己撞」，不算新增重复。
    """
    clash = store.conflicts_with(fields, exclude_hash=exclude_hash)
    if clash is not None:
        raise ApplyBlockedError(
            "另一条比赛已经有相同的「平台 + 开始时间 + 名称」，写入会新增一条重复；"
            "确认无误时可以勾选「允许重复」再试"
        )


def resolve_conflict(
    repo: Repository,
    store: ContestStore,
    submission_id: int,
    *,
    keep_new: bool,
    reviewer_id: str,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    """裁定一条冲突：保留提交的新值（排回待下发），或丢弃提交保留磁盘现状。

    「保留新值」= 管理员在知情的前提下决定用这个值覆盖磁盘现值，于是做一次**交换**：
    这条排回「待下发」，还在待下发的对手（同一处早先过审的那条）改成「已驳回」，
    磁盘等下一次一键下发再动。同时把这条的比对基准挪到磁盘现值，否则下一次下发会拿
    旧基准再判一次冲突，裁定永远走不出去；对手已经写盘（applied）的不动它 ——
    那条改动确实写过盘，本条下发时自然覆盖它。

    仍然在这里拦住的只有两件事：目标还在且唯一（被删了/文件里有重复条目就没法寻址），
    以及新增不会多出一条重复（`allow_duplicate` 可放行）。
    """
    submission = repo.get_submission(submission_id)
    if submission is None or submission.plugin != SLUG:
        raise KeyError(submission_id)

    if not keep_new:
        updated = repo.resolve_submission_conflict(submission_id, keep_new=False, reviewer_id=reviewer_id)
        return {
            "resolved": "discarded",
            "applied": False,
            "superseded": [],
            "submission": updated.to_dict(),
        }

    operation = operation_of(submission)
    target = None if operation.is_create else _unique_target(store, submission)
    if operation.is_create and not allow_duplicate:
        _ensure_identity_free(store, operation.fields, exclude_hash=submission.img_key)

    superseded = _supersede_rivals(repo, submission, reviewer_id=reviewer_id)

    updated = repo.resolve_submission_conflict(
        submission_id,
        keep_new=True,
        reviewer_id=reviewer_id,
        base_value=None if target is None else target.snapshot(),
    )
    return {
        "resolved": "queued",
        "applied": False,
        "superseded": superseded,
        "submission": updated.to_dict(),
    }


def _supersede_rivals(repo: Repository, submission: Submission, *, reviewer_id: str) -> list[dict[str, Any]]:
    """把同一处上还排在「待下发」里的其它提交驳回，返回被驳回的那些。

    裁定保留本条，就等于本条取代了它们：它们不会被写盘，再挂着「已通过待下发」只会
    在下一次下发里又被判成冲突。已经写盘（applied）的不动 —— 见 resolve_conflict。
    删除单不参与这个竞争（只有管理员能删，见 build_apply_plan）。
    """
    if submission.type == types.TYPE_DELETE:
        return []

    slot = claim_slot(submission)
    rows, _ = repo.list_submissions(plugin=SLUG, status=STATUS_APPROVED, limit=200)
    superseded: list[dict[str, Any]] = []
    for row in rows:
        if row.id == submission.id or claim_slot(row) != slot:
            continue
        comment = f"冲突裁定：被 #{submission.id} 取代，未下发"
        updated = repo.supersede_submission(row.id, reviewer_id=reviewer_id, comment=comment)
        if updated is None:
            continue
        superseded.append({"submission_id": updated.id, "author_name": updated.author_name})
    return superseded


# ------------------------------------------------------------------ 浏览页辅助


def pending_for(
    submissions: list[Submission], hash_: str, *, viewer_id: str | None = None
) -> list[dict[str, Any]]:
    """某一条比赛上还没落盘的改动。

    改动是逐字段拆开提交的，所以这里一行就是「一个字段的一条在途改动」，
    `field` 是字段名、`value` 是它的新值（标量）。

    `mine` 是给界面用的：我自己的那条会被回填进编辑表单（继续改就是给它打补丁），
    别人的只能看 —— 拿别人的在途值当起点，等于把他们的改动并进我这条提交里。
    """
    return [
        {
            "submission_id": item.id,
            "type": item.type,
            "type_label": item.type_label,
            "field": item.target.strip() or None,
            "field_label": FIELD_LABELS.get(item.target.strip(), ""),
            "status": item.status,
            "author_name": item.author_name,
            "mine": item.author_id == viewer_id,
            "created_at": item.created_at,
            "note": item.note,
            "value": item.submitted_value,
        }
        for item in submissions
        if item.img_key == hash_
    ]


def pending_creates(submissions: list[Submission], *, viewer_id: str | None = None) -> list[dict[str, Any]]:
    """只在提交单里存在、还没下发的新比赛（浏览页把它们列成「待新增」）。"""
    drafts: list[dict[str, Any]] = []
    for item in submissions:
        if item.type != types.TYPE_CREATE:
            continue
        value = item.submitted_value if isinstance(item.submitted_value, dict) else {}
        drafts.append(
            {
                "submission_id": item.id,
                "type": item.type,
                "type_label": item.type_label,
                "status": item.status,
                "author_name": item.author_name,
                "mine": item.author_id == viewer_id,
                "created_at": item.created_at,
                "note": item.note,
                "value": value,
            }
        )
    return drafts


def load_open_submissions(repo: Repository) -> list[Submission]:
    """取出本插件所有 pending + approved 的提交单（浏览页展示在途改动用）。"""
    collected: list[Submission] = []
    for status in OPEN_STATUSES:
        rows, _ = repo.list_submissions(plugin=SLUG, status=status, limit=200)
        collected.extend(rows)
    return collected


__all__ = [
    "SLUG",
    "ApplyBlockedError",
    "ApplyPlan",
    "Conflict",
    "Operation",
    "apply_approved",
    "build_apply_plan",
    "changed_fields",
    "entry_of",
    "identity_change",
    "load_open_submissions",
    "operation_of",
    "pending_creates",
    "pending_for",
    "preview_approved",
    "resolve_conflict",
    "scan_conflicts",
]
