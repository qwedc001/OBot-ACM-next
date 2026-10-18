"""提交单叠加、冲突检测与一键应用。

浏览接口要给出「磁盘原值 + 审核中的改动」两套信息（原值用于编辑，在途改动只读展示），
审核后的写入需要按目标合并，这两件事都放在这里，路由层只负责鉴权和参数校验。

冲突有两种，判定入口和裁定流程是同一套：

  1. **同一个字段被两个人都改了**：只有**最早过审**的那条能下发，其余的转成
     ``conflict`` 挂起。判断标准是「过审的先后」，不是提交的先后 —— 先审过的那条
     才是管理员认可的现值。
  2. **磁盘原值在提交之后被别人改过**（Bot 的 OCR 任务、别人的提交）：比对「提交时
     看到的原值」与磁盘当前值，不一致的一样挂起。

两种都**不写盘**（体检 ``scan_conflicts`` 与下发共用同一套判定）。管理员在「冲突处理」
里看到三方对比后裁定：

  - 保留提交的新值 -> **交换**：这条排回「待下发」，被它取代的那条（还在待下发的对手）
    改成「已驳回」，写盘交给下一次一键下发；同时把这条的比对基准挪到磁盘现值，
    否则下一次下发会拿旧基准再判一次冲突，裁定永远走不出去。
    对手已经写盘（applied）的不动它 —— 那条改动确实写过盘，改状态等于篡改记录。
  - 丢弃提交 -> 直接驳回，磁盘保持现状。

点赞是增量（提交单里存的是「加多少」），加在磁盘现值上就是正确结果，
所以不存在冲突，也不参与上面的比对。
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

from . import config
from .hashing import new_legacy_entry
from .store import Category, ImageStat, PickOneStore
from .types import (
    CATEGORY_TYPES,
    TYPE_CATEGORY,
    TYPE_CATEGORY_CREATE,
    TYPE_COMMENTS,
    TYPE_LIKES,
    TYPE_OCR_TEXT,
)

# 插件自身的 slug：所有提交单都要打上它，审核队列与一键应用才知道归属。
SLUG = config.SLUG


class ApplyConflictError(Exception):
    """裁定冲突时冲突依然存在（例如裁定瞬间又被改）。"""


@dataclass
class Conflict:
    """一条无法直接应用的提交，以及裁定它需要的全部信息。"""

    submission: Submission
    reason: str
    current_value: Any = None
    base_value: Any = None
    missing: bool = False
    # 抢到了同一处的另一条提交（「同字段撞车」时才有）：裁定保留本条时它要被驳回。
    # 磁盘被 Bot 改动那种冲突没有对手，这里就是 None。
    rival: Submission | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission.id,
            "type": self.submission.type,
            "type_label": self.submission.type_label,
            "img_key": self.submission.img_key,
            "target": self.submission.target,
            "author_name": self.submission.author_name,
            "reason": self.reason,
            "missing": self.missing,
            # base_value      提交时看到的原值
            # current_value   磁盘现在的值，或者对手将要下发的值
            # submitted_value 用户提交的新值
            "base_value": self.base_value,
            "current_value": self.current_value,
            "submitted_value": self.submission.submitted_value,
            # 对手是谁：界面据此把「现值」那一栏说成「另一个用户的修改」
            "rival_id": self.rival.id if self.rival else None,
            "rival_author_name": self.rival.author_name if self.rival else "",
            "note": self.submission.note,
            "detected_at": self.submission.conflict_at,
        }


def field_of(submission: Submission) -> str | None:
    """提交类型 -> parser.json 字段名。"""
    return field_for_type(submission.type)


def field_for_type(submission_type: str) -> str | None:
    """提交类型 -> parser.json 字段名。"""
    if submission_type == TYPE_OCR_TEXT:
        return "ocr_text"
    if submission_type == TYPE_LIKES:
        return "likes"
    if submission_type == TYPE_COMMENTS:
        return "comments"
    return None


def _pending_entry(submission: Submission) -> dict[str, Any]:
    """一条在途改动在浏览接口里的展示形式。"""
    return {
        "submission_id": submission.id,
        "status": submission.status,
        "author_id": submission.author_id,
        "author_name": submission.author_name,
        "created_at": submission.created_at,
        "note": submission.note,
    }


def image_pending_changes(
    submissions: list[Submission],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """把在途提交整理成 {img_key: {image_name: [改动, ...]}}。

    只记录「有哪些审核中的改动」，不把新值叠加到原值上：浏览与编辑看到的
    字段值永远是磁盘原值，别人的在途改动只作为参考信息展示，不能被当成修改起点。
    """
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for submission in submissions:
        name = field_of(submission)
        if name is None:
            continue
        grouped.setdefault(submission.img_key, {}).setdefault(submission.target, []).append(
            {
                **_pending_entry(submission),
                "field": name,
                "value": submission.submitted_value,
            }
        )
    return grouped


def load_open_submissions(repo: Repository) -> list[Submission]:
    """取出本插件所有 pending + approved 的提交单（浏览页展示在途改动用）。"""
    collected: list[Submission] = []
    for status in OPEN_STATUSES:
        rows, _ = repo.list_submissions(plugin=SLUG, status=status, limit=200)
        collected.extend(rows)
    return collected


def category_pending_changes(
    submissions: list[Submission],
) -> dict[str, list[dict[str, Any]]]:
    """类别类在途提交折叠成 {img_key: [改动, ...]}（value 形如 {id, keys}）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for submission in submissions:
        if submission.type not in CATEGORY_TYPES:
            continue
        value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}
        grouped.setdefault(submission.img_key, []).append(
            {
                **_pending_entry(submission),
                "value": {"id": value.get("id"), "keys": value.get("keys")},
                "is_new": submission.type == TYPE_CATEGORY_CREATE,
            }
        )
    return grouped


def _sorted_pending(pending: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return sorted(pending or [], key=lambda item: item["submission_id"])


def _with_mine(pending: list[dict[str, Any]], viewer_id: str | None) -> list[dict[str, Any]]:
    return [{**item, "mine": item["author_id"] == viewer_id} for item in pending]


def effective_image(
    stat: ImageStat, pending: list[dict[str, Any]] | None = None, *, viewer_id: str | None = None
) -> dict[str, Any]:
    """把图片信息与在途改动一起交给前端。

    字段值始终是磁盘原值，在途改动单独放在 pending_changes 里 —— 前端据此
    既能看到别人审核中的内容，又只能基于原版本提交自己的修改。
    """
    pending = _sorted_pending(pending)
    return {
        "name": stat.name,
        "md5": stat.md5,
        "hash_id": stat.hash_id,
        "ocr_text": stat.ocr_text,
        "add_time": stat.add_time,
        "likes": stat.likes,
        "comments": list(stat.comments),
        "pickup_times": stat.pickup_times,
        "legacy": stat.legacy,
        "has_pending_change": bool(pending),
        "pending_fields": sorted({item["field"] for item in pending}),
        "pending_changes": _with_mine(pending, viewer_id),
    }


def effective_category(
    category: Category,
    pending: list[dict[str, Any]] | None = None,
    *,
    viewer_id: str | None = None,
) -> dict[str, Any]:
    """类别展示信息：磁盘上的原值 + 在途改动（同样不叠加）。"""
    pending = _sorted_pending(pending)
    return {
        "img_key": category.img_key,
        "id": category.id,
        "keys": list(category.keys),
        "image_count": category.image_count,
        "missing_ocr": 0,
        "pending_fields": ["id", "keys"] if pending else [],
        "pending_changes": _with_mine(pending, viewer_id),
        "is_pending_new": any(item["is_new"] for item in pending),
    }


def pending_category_drafts(repo: Repository) -> list[dict[str, Any]]:
    """列出只在提交单里存在、尚待处理的新类别。"""
    drafts: list[dict[str, Any]] = []
    rows, _ = repo.list_submissions(plugin=SLUG, types=CATEGORY_TYPES, limit=200)
    for submission in rows:
        if submission.type != TYPE_CATEGORY_CREATE:
            continue
        value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}
        drafts.append(
            {
                "img_key": submission.img_key,
                "id": value.get("id") or submission.img_key,
                "keys": value.get("keys") or [],
                "status": submission.status,
                "submission_id": submission.id,
                "author_name": submission.author_name,
            }
        )
    return drafts


# ------------------------------------------------------------------ 冲突检测


def _same(left: Any, right: Any) -> bool:
    """比较两个字段值是否等价。列表按集合比，避免顺序变化误报冲突。"""
    if isinstance(left, list) or isinstance(right, list):
        return sorted(str(item) for item in (left or [])) == sorted(str(item) for item in (right or []))
    return left == right


def conflict_slot(submission: Submission) -> tuple[str, str, str]:
    """这条提交争的是「哪一处」。

    同一处只容得下一条改动：图片是按 `(类别, 文件名, 字段)` 算的，类别类提交是按
    `img_key` 整块算的（改 id 与改别名是两条提交单，但落在同一份 config 条目上）。
    点赞是增量，不参与竞争，调用方自行排除。
    """
    return (submission.img_key, submission.target, field_of(submission) or "")


def apply_order(submissions: list[Submission]) -> list[Submission]:
    """下发顺序：**最早过审**的排最前，同一时刻过审的按提交单 id。

    冲突裁定的口径就是「过审早的那条先下发」，所以这里必须按 reviewed_at 排，
    而不是按提交单 id —— 先提交不等于先被管理员认可。裁定里被排回「待下发」的提交
    会拿到新的 reviewed_at，于是自然排到队尾。
    """
    return sorted(submissions, key=lambda item: (item.reviewed_at or 0.0, item.id))


def rival_conflict(submission: Submission, holder: Submission) -> Conflict:
    """同一处被两个人改过时，后过审那条的冲突。

    `current_value` 放的是**对手将要下发的那个值**（还没写盘），不是磁盘现值 ——
    对手会换人，所以展示这一格时要用「现在的持有人」，见 `refresh_conflicts`。
    """
    return Conflict(
        submission=submission,
        reason=f"#{holder.id}（{holder.author_name or '另一位用户'}）"
        "先过审，改的是同一处；本条下发会盖掉那条",
        base_value=submission.base_value,
        current_value=holder.submitted_value,
        rival=holder,
    )


def image_conflict(store: PickOneStore, submission: Submission) -> Conflict | None:
    """检查一条图片字段提交在磁盘上是否已经被改动过。"""
    name = field_of(submission)
    if name is None:
        return None

    try:
        stat = store.get_image_stat(submission.img_key, submission.target)
    except Exception:
        return Conflict(
            submission=submission,
            reason="图片已不存在或不再属于该类别",
            base_value=submission.base_value,
            current_value=None,
            missing=True,
        )

    current = list(stat.comments) if name == "comments" else getattr(stat, name)

    # 点赞是增量：不管磁盘现值被谁加过，把提交的增量加到现值上都是正确结果
    if submission.type == TYPE_LIKES:
        return None

    if submission.base_value is None or _same(submission.base_value, current):
        return None

    return Conflict(
        submission=submission,
        reason="提交后原值已被改动（可能是 Bot 的 OCR 任务，或另一个人的提交）",
        base_value=submission.base_value,
        current_value=current,
    )


def _case_twin(categories: dict[str, Category], img_key: str) -> str | None:
    """在已有类别里找出与 img_key 只差大小写的那个标识（没有则返回 None）。"""
    folded = img_key.casefold()
    for existing in categories:
        if existing != img_key and existing.casefold() == folded:
            return existing
    return None


def category_conflict(store: PickOneStore, submission: Submission) -> Conflict | None:
    """检查类别提交：新增撞名、已有类别被删或被改、别名被占用。"""
    categories = store.load_categories()
    existing = categories.get(submission.img_key)
    # 目录不区分大小写，「新类别」可能只是已有类别的另一种写法
    twin = _case_twin(categories, submission.img_key) if existing is None else None
    value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}

    if submission.type == TYPE_CATEGORY_CREATE:
        if existing is not None:
            current = {"id": existing.id, "keys": list(existing.keys)}
            # 「提交时看到的原值」等于现值，说明这条新增已经被裁定过（管理员确认要用
            # 它覆盖后建出来的那个类别），不该再判一次冲突 —— 与修改类同一个口径。
            base = submission.base_value if isinstance(submission.base_value, dict) else None
            if (
                base is not None
                and _same(base.get("id"), current["id"])
                and _same(base.get("keys"), current["keys"])
            ):
                return None
            return Conflict(
                submission=submission,
                reason="该类别标识已被占用（可能在申请之后被创建）",
                base_value=submission.base_value,
                current_value=current,
            )
        if twin is not None:
            return Conflict(
                submission=submission,
                reason=f"已存在只差大小写的类别标识 {twin}（Windows/macOS 下是同一个目录）",
                base_value=None,
                current_value={"id": twin, "keys": list(categories[twin].keys)},
            )
        return None

    if existing is None:
        return Conflict(
            submission=submission,
            reason="类别已从 config.json 中移除",
            base_value=submission.base_value,
            current_value=None,
            missing=True,
        )

    base = submission.base_value if isinstance(submission.base_value, dict) else {}
    current = {"id": existing.id, "keys": list(existing.keys)}
    changed_id = "id" in base and not _same(base.get("id"), current["id"])
    changed_keys = "keys" in base and not _same(base.get("keys"), current["keys"])

    if changed_id or changed_keys:
        return Conflict(
            submission=submission,
            reason="提交后类别信息已被改动",
            base_value=base,
            current_value=current,
        )

    keys = value.get("keys")
    if isinstance(keys, list):
        try:
            store.validate_alias_list(keys, exclude_key=submission.img_key)
        except Exception as error:
            return Conflict(
                submission=submission,
                reason=f"别名现在不可用：{error}",
                base_value=base,
                current_value=current,
            )
    return None


def detect_conflict(store: PickOneStore, submission: Submission) -> Conflict | None:
    """统一的冲突判定入口。"""
    if submission.type in CATEGORY_TYPES:
        return category_conflict(store, submission)
    return image_conflict(store, submission)


# ------------------------------------------------------------------ 应用计划


@dataclass
class ApplyPlan:
    """一次应用要落盘的改动，以及被判定冲突而挂起的提交。"""

    # {img_key: [{"name","field","value","submission_id"}]}
    image_changes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # {img_key: {"id":..., "key":[...]}}
    category_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    new_keys: list[str] = field(default_factory=list)
    applied_ids: list[int] = field(default_factory=list)
    # submission_id -> 落盘后应记录的值
    applied_values: dict[int, Any] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    # 冲突槽位 -> 本批真正会下发的那条。刷新别的冲突时用它当「现在的对手」：
    # 对手是会换人的（裁定把另一条排回了待下发）。
    holders: dict[tuple[str, str, str], Submission] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.applied_ids)


def _dedupe(values: list[str]) -> list[str]:
    """保序去重（大小写不敏感），落盘前再兜一次底。"""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = " ".join(str(value).split())
        if not text:
            continue
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(text)
    return out


def build_apply_plan(store: PickOneStore, submissions: list[Submission]) -> ApplyPlan:
    """把 approved 提交单分成「可以直接写盘」和「有冲突」两组。

    同一批里可能有多个人改了同一个字段 / 同一个类别（各自基于当时的原值）。
    这时只有**最早过审**的那条能写盘，后面的转成冲突交给管理员裁定 ——
    否则先写的那条会被后面的静默覆盖，提交单却还标着「已下发」。
    点赞是增量，叠加起来就是正确结果，不参与这里。

    判定用磁盘现状（而不是「本批已经写进 raw 的样子」）：这一批只写一次盘，
    每条提交都该按「我过审之后、这批下发之前」的样子判一次。
    """
    plan = ApplyPlan()
    ordered = apply_order(submissions)

    earlier: dict[tuple[str, str, str], Submission] = {}

    for submission in ordered:
        name = field_of(submission)
        conflict = detect_conflict(store, submission)

        if conflict is None and submission.type != TYPE_LIKES:
            holder = earlier.get(conflict_slot(submission))
            if holder is not None:
                conflict = rival_conflict(submission, holder)

        if conflict is not None:
            plan.conflicts.append(conflict)
            continue

        # 点赞是增量，不占槽位（两条点赞叠加起来本来就是正确结果）
        if submission.type != TYPE_LIKES:
            earlier[conflict_slot(submission)] = submission
        if name is None:
            continue

        plan.image_changes.setdefault(submission.img_key, []).append(
            {
                "name": submission.target,
                "field": name,
                "value": submission.submitted_value,
                "submission_id": submission.id,
            }
        )

    conflicted_ids = {conflict.submission.id for conflict in plan.conflicts}

    # 类别改动按 img_key 折叠（同一类别可能既有改 id 又有改别名）
    slots: dict[str, dict[str, Any]] = {}
    for submission in ordered:
        if submission.type not in CATEGORY_TYPES or submission.id in conflicted_ids:
            continue
        value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}
        slot = slots.setdefault(submission.img_key, {"id": None, "keys": None, "is_new": False})
        if submission.type == TYPE_CATEGORY_CREATE:
            slot["is_new"] = True
            if submission.img_key not in plan.new_keys:
                plan.new_keys.append(submission.img_key)
        if isinstance(value.get("id"), str) and value["id"].strip():
            slot["id"] = value["id"].strip()
        if isinstance(value.get("keys"), list):
            slot["keys"] = [str(item) for item in value["keys"]]

    existing = store.load_categories()
    for img_key, slot in slots.items():
        current = existing.get(img_key)
        if img_key in plan.new_keys or current is None:
            keys = slot["keys"] or [slot["id"] or img_key, img_key]
            entry = {"id": slot["id"] or img_key, "key": _dedupe(keys)}
        else:
            keys = slot["keys"] if slot["keys"] is not None else list(current.keys)
            entry = {"id": slot["id"] or current.id, "key": _dedupe(keys)}
        plan.category_entries[img_key] = entry

    # 记录本批要标记为 applied 的提交单及写入值
    for _img_key, items in plan.image_changes.items():
        for item in items:
            plan.applied_ids.append(item["submission_id"])
            plan.applied_values[item["submission_id"]] = item["value"]

    for submission in ordered:
        if submission.type not in CATEGORY_TYPES or submission.id in conflicted_ids:
            continue
        plan.applied_ids.append(submission.id)
        plan.applied_values[submission.id] = plan.category_entries.get(submission.img_key)

    plan.holders = earlier
    return plan


def _write_plan(store: PickOneStore, plan: ApplyPlan) -> dict[str, int]:
    """把计划里的内容写盘（不含提交单状态变更）。"""
    image_result = store.apply_image_changes(plan.image_changes)
    category_result = store.apply_category_entries(plan.category_entries)
    for img_key in plan.new_keys:
        store.ensure_category_dir(img_key)
    return {
        "applied_images": image_result["applied"],
        "applied_categories": category_result["applied"],
    }


def apply_approved(repo: Repository, store: PickOneStore) -> dict[str, Any]:
    """一键应用：把 approved 提交单写回，冲突单转 conflict 状态挂起。"""
    submissions = repo.list_approved_submissions(SLUG)
    if not submissions:
        return {
            "applied_images": 0,
            "applied_categories": 0,
            "new_keys": [],
            "submissions": 0,
            "conflicts": [],
            "details": [],
        }

    plan = build_apply_plan(store, submissions)

    # 在本次 ORM 事务内记录冲突；写盘失败时事务回滚，下次重新比对。
    for conflict in plan.conflicts:
        repo.mark_conflict(conflict.submission.id, conflict.to_dict())

    written = _write_plan(store, plan)

    for submission_id in plan.applied_ids:
        repo.mark_applied(submission_id, plan.applied_values.get(submission_id))

    return {
        **written,
        "new_keys": plan.new_keys,
        "submissions": len(plan.applied_ids),
        "conflicts": [conflict.to_dict() for conflict in plan.conflicts],
        "details": [
            {"img_key": img_key, "count": len(items)} for img_key, items in plan.image_changes.items()
        ],
    }


def scan_conflicts(repo: Repository, store: PickOneStore) -> dict[str, Any]:
    """体检一遍「已通过待下发」的提交单：挂起新的冲突，并刷新已经挂起的那些。

    为什么需要它：冲突判定平时只在**下发**那一瞬间做，而 `/admin/apply/preview`
    是只读的（dry-run 不能改状态）。于是「过审之后又有人先改了这一处」这种冲突，
    在下发之前只出现在预览的提醒里，冲突待裁定表是空的 —— 点「去处理冲突」过去
    什么也没有。审核台每刷新一次就跑一遍同一套判定（不写数据文件），把结果持久化。
    反复调用是幂等的：`mark_conflict` 只对 approved 生效，挂起过的不会再动。

    挂起的冲突还要**重新对一遍对手**（`refresh_conflicts`）：裁定保留 #2 时 #1 被
    驳回、#2 排回待下发，那么同一处上还挂着的 #3 该对照的就是 #2 而不是 #1。
    """
    plan = build_apply_plan(store, repo.list_approved_submissions(SLUG))
    payloads = [conflict.to_dict() for conflict in plan.conflicts]
    for conflict in plan.conflicts:
        repo.mark_conflict(conflict.submission.id, conflict.to_dict())
    return {
        "conflicts": payloads,
        "total": len(payloads),
        "submission_ids": [conflict.submission.id for conflict in plan.conflicts],
        "refreshed_ids": refresh_conflicts(repo, store, plan.holders),
    }


def refresh_conflicts(
    repo: Repository, store: PickOneStore, holders: dict[tuple[str, str, str], Submission]
) -> list[int]:
    """让已经挂起的冲突重新对上「现在的对手」，返回被改写的提交单 id。

    只改 `conflict_detail`（三方对比），状态一律不动：回不回「待下发」是管理员裁定
    的事，体检只负责让界面上的对照物跟得上现状。
    """
    rows, _ = repo.list_submissions(plugin=SLUG, status=STATUS_CONFLICT, limit=200)
    refreshed: list[int] = []
    for row in rows:
        holder = None if row.type == TYPE_LIKES else holders.get(conflict_slot(row))
        if holder is None and not (row.conflict_detail or {}).get("rival_id"):
            # 既没有对手、当初也不是「撞车」那种冲突（磁盘被改 / 图片没了）：
            # 那是另一套判定，理由由下发时重算，体检不该改写它
            continue
        detail = _conflict_now(store, row, holder)
        if detail is None or detail == row.conflict_detail:
            continue
        repo.refresh_conflict_detail(row.id, detail)
        refreshed.append(row.id)
    return refreshed


def _conflict_now(
    store: PickOneStore, submission: Submission, holder: Submission | None
) -> dict[str, Any] | None:
    """这条冲突现在的对照物：还在待下发的对手，或者磁盘现值。

    对手走了（被驳回 / 撤回）而又没有别人改过磁盘时，也不能留着那条旧的对照物：
    显示成「现在下发不会盖掉任何东西」，让管理员知道这条其实可以直接采纳。
    """
    if holder is not None:
        return rival_conflict(submission, holder).to_dict()

    disk = detect_conflict(store, submission)
    if disk is not None:
        return disk.to_dict()

    return Conflict(
        submission=submission,
        reason="原先与它冲突的那条已经不在待下发里了，磁盘上也没有别人改过这一处",
        base_value=submission.base_value,
        current_value=_baseline_after_resolve(store, submission),
    ).to_dict()


def resolve_conflict(
    repo: Repository,
    store: PickOneStore,
    submission_id: int,
    *,
    keep_new: bool,
    reviewer_id: str,
) -> dict[str, Any]:
    """裁定一条冲突：保留提交的新值（排回待下发）或丢弃提交（保持磁盘现状）。

    「保留新值」= 管理员在知情的前提下决定用这个值覆盖磁盘现值，于是做一次**交换**：
    这条排回「待下发」，还在待下发的对手（同字段早先过审的那条）改成「已驳回」，
    磁盘等下一次一键下发再动。这里必须把这条的比对基准挪到磁盘现值上，否则下一次
    下发会拿旧基准再判一次冲突，裁定永远走不出去。

    对手已经写盘（applied）时不动它 —— 那条改动确实写过盘；本条下发时自然覆盖它。

    另外，别名的唯一性是 config.json 的硬约束（同一个别名指向两个类别时 Bot 的
    match_dict 会随机挑一个）：被别的类别占用的别名会在**这里**摘掉并写回提交单，
    因为真正写盘的是下一次批量下发，它无从知道这次裁定。返回值的 dropped_aliases
    会列出摘掉的别名。
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

    _ensure_target_writable(store, submission)

    dropped_aliases: list[str] = []
    if submission.type in CATEGORY_TYPES:
        dropped_aliases = _drop_taken_aliases(repo, store, submission)

    superseded = _supersede_rivals(repo, submission, reviewer_id=reviewer_id)

    updated = repo.resolve_submission_conflict(
        submission_id,
        keep_new=True,
        reviewer_id=reviewer_id,
        base_value=_baseline_after_resolve(store, submission),
    )

    result = {
        "resolved": "queued",
        "applied": False,
        "superseded": superseded,
        "submission": updated.to_dict(),
    }
    if dropped_aliases:
        result["dropped_aliases"] = dropped_aliases
    return result


def _baseline_after_resolve(store: PickOneStore, submission: Submission) -> Any:
    """裁定「保留新值」之后，这条提交要比对的「原值」—— 也就是磁盘现值。

    这条提交下一次下发时还会走一遍冲突判定，基准不跟着挪就会永远冲突下去。
    新增类别在磁盘上还没有那一份时基准是 None（它本来就没有原值）；但如果这个类别
    已经被别人建出来了（对手先写盘了），基准就是现在这一份 —— 管理员裁定「保留新值」
    等于确认要覆盖它，类别判定会因此放行（见 category_conflict）。
    """
    name = field_of(submission)
    if name is not None:
        stat = store.get_image_stat(submission.img_key, submission.target)
        return list(stat.comments) if name == "comments" else getattr(stat, name)

    existing = store.load_categories().get(submission.img_key)
    if existing is None:
        return None
    return {"id": existing.id, "keys": list(existing.keys)}


def _supersede_rivals(repo: Repository, submission: Submission, *, reviewer_id: str) -> list[dict[str, Any]]:
    """把同一处上还排在「待下发」里的其它提交驳回，返回被驳回的那些。

    裁定保留本条，就等于本条取代了它们：它们不会被写盘，再挂着「已通过待下发」只会
    在下一次下发里又被判成冲突。已经写盘（applied）的不动 —— 见 resolve_conflict。
    """
    if submission.type == TYPE_LIKES:
        return []

    slot = conflict_slot(submission)
    rows, _ = repo.list_submissions(plugin=SLUG, status=STATUS_APPROVED, limit=200)
    superseded: list[dict[str, Any]] = []
    for row in rows:
        if row.id == submission.id or conflict_slot(row) != slot:
            continue
        comment = f"冲突裁定：被 #{submission.id} 取代，未下发"
        updated = repo.supersede_submission(row.id, reviewer_id=reviewer_id, comment=comment)
        if updated is None:
            continue
        superseded.append({"submission_id": updated.id, "author_name": updated.author_name})
    return superseded


def _drop_taken_aliases(repo: Repository, store: PickOneStore, submission: Submission) -> list[str]:
    """把被别的类别占用的别名从这条提交里摘掉（并写回提交单），返回摘掉的那些。"""
    value = submission.submitted_value if isinstance(submission.submitted_value, dict) else {}
    keys = _dedupe([str(item) for item in (value.get("keys") or [])])
    if not keys:
        return []

    current = store.load_categories().get(submission.img_key)
    kept, dropped = _without_taken_aliases(store, keys, exclude_key=submission.img_key)
    if not dropped:
        return []
    if not kept:
        # 别名全被占走了：至少留下类别标识本身，别写出一个没有 key 的条目
        kept = [str(value.get("id") or (current.id if current else submission.img_key))]
    repo.restate_submission(submission.id, {**value, "keys": kept})
    return dropped


def _without_taken_aliases(
    store: PickOneStore, keys: list[str], *, exclude_key: str
) -> tuple[list[str], list[str]]:
    """摘掉已被其它类别占用的别名，返回 (留下的, 摘掉的)。"""
    owner: dict[str, str] = {}
    for category in store.load_categories().values():
        if category.img_key == exclude_key:
            continue
        for alias in category.keys:
            owner[store.normalize_alias(alias).casefold()] = category.img_key

    kept: list[str] = []
    dropped: list[str] = []
    for alias in keys:
        folded = store.normalize_alias(alias).casefold()
        if folded in owner:
            dropped.append(alias)
        else:
            kept.append(alias)
    return kept, dropped


def _ensure_target_writable(store: PickOneStore, submission: Submission) -> None:
    """裁定「保留新值」前的最后一道检查：目标还在不在。

    ApplyConflictError 里的文案是给界面看的；下面这两条属于异常路径，
    所以保持英文，免得被开发模式的 traceback 打进终端。
    """
    if submission.type in CATEGORY_TYPES:
        # 新增类别时目标本来就不存在；改已有类别则要求类别仍在
        if submission.type == TYPE_CATEGORY and store.load_categories().get(submission.img_key) is None:
            raise ApplyConflictError("类别已从 config.json 中移除，无法写入")
        return

    try:
        store.get_image_stat(submission.img_key, submission.target)
    except Exception as error:
        raise ApplyConflictError("图片已不存在，无法写入该提交") from error


def image_entry_or_placeholder(store: PickOneStore, img_key: str, name: str) -> dict[str, Any]:
    """图片在 parser.json 里缺记录时使用的占位条目。"""
    path = store.category_dir(img_key) / name
    return new_legacy_entry("", PickOneStore.fallback_add_time(path))
