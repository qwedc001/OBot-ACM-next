"""算法竞赛列表的用户侧路由：浏览比赛 + 提交改动。

**审核流程不用自己实现**：`/api/plugins/curation/admin/queue`、`/api/plugins/curation/admin/review/*` 是框架提供的，
对任何工具通用。这里只负责把改动写成一条「提交单」，并把 plugin 标成自己。

浏览接口刻意把「磁盘原值」与「在途改动」分开返回：字段值永远是磁盘原值，
别人的在途改动只作为参考信息展示，不能被当成修改起点（否则两个人都基于
同一个旧值提交，后来者一定撞冲突）。

**删除只有管理员能发起**（`POST /admin/delete`）：删掉一场比赛是不可逆的，
而且它经常是「上游已经取消、Bot 却还在渲染」才需要做的事。放在管理员侧，
普通用户提不了，审核队列里也不会出现来自普通用户的删除单。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from framework.curation.contestlist import types
from framework.curation.contestlist.changes import (
    DELETE_MARKER,
    SLUG,
    changed_fields,
    identity_change,
    load_open_submissions,
    pending_creates,
    pending_for,
)
from framework.curation.contestlist.hashing import hash_of
from framework.curation.contestlist.schemas import (
    ContestCreateRequest,
    ContestDeleteRequest,
    ContestUpdateRequest,
)
from framework.curation.contestlist.store import (
    MAX_DURATION,
    MAX_NAME_LENGTH,
    MAX_PLATFORM_LENGTH,
    MAX_SUPPLEMENT_LENGTH,
    MIN_DURATION,
    clean_fields,
)
from framework.curation.contestlist.types import FIELD_LABELS, FIELD_ORDER
from framework.curation.errors import StoreError, ValidationError
from framework.curation.repository import OPEN_STATUSES, STATUS_CONFLICT, Submission
from web.content.deps import AdminUser, CurrentUser, Repo

from .deps import Store

router = APIRouter(tags=["contestlist"])

# 「还在流程里」的提交：待审核 + 已通过待下发 + 冲突挂起
OPEN_FLOW_STATUSES = (*OPEN_STATUSES, STATUS_CONFLICT)

# list_submissions 每次最多返回 200 条（框架内部的页大小上限），本工具的提交
# 量级远小于它；写成这个值是为了让「一次取全」的意图在代码里看得见。
_MAX_ROWS = 200


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def _log(repo: Repo, user, submission: Submission, created: bool) -> None:
    repo.add_log(
        actor_id=user.id,
        username=user.username,
        action="submit_contest",
        detail=f"{'create' if created else 'update'} {submission.type} @ {submission.img_key}",
        is_admin=bool(getattr(user, "is_admin", False)),
    )


@router.get("/meta")
def meta(_user: CurrentUser) -> dict:
    """前端表单要用的字段中文名与长度/时长限制。

    刻意**不提供平台取值列表**：平台是自由文本（见 types.MAX_PLATFORM_LENGTH
    的注释），前端用普通输入框，后端只限长度。
    """
    return {
        "fields": [{"name": name, "label": FIELD_LABELS.get(name, name)} for name in FIELD_ORDER],
        "limits": {
            "platform": MAX_PLATFORM_LENGTH,
            "name": MAX_NAME_LENGTH,
            "supplement": MAX_SUPPLEMENT_LENGTH,
            "duration_min": MIN_DURATION,
            "duration_max": MAX_DURATION,
        },
        "types": [{"value": value, "label": types.TYPE_LABELS[value]} for value in types.SUBMISSION_TYPES],
    }


@router.get("/items")
def list_items(store: Store, repo: Repo, user: CurrentUser) -> dict:
    """列出比赛列表：磁盘原值 + 全部在途改动。

    顺序保持文件里的原样（也就是 Bot 渲染出来的顺序）。条目身份是身份哈希而不是
    下标，所以这里排序与否都不影响提交能不能落对地方 —— 不排只是为了让人
    在文件里找到某一场时更容易对上界面。
    """
    contests = store.load()
    pending = load_open_submissions(repo)
    items = []
    for contest in contests:
        item = contest.to_dict()
        changes = pending_for(pending, contest.hash, viewer_id=user.id)
        item["pending_changes"] = changes
        item["has_pending_change"] = bool(changes)
        items.append(item)

    summary = store.summary()
    return {
        "total": len(items),
        "items": items,
        "drafts": pending_creates(pending, viewer_id=user.id),
        "invalid": summary["invalid"],
        "duplicates": summary["duplicates"],
        # 只给「在不在」，不给路径：绝对路径属于服务端内部信息（见 admin.overview）
        "file_available": summary["file_available"],
        "dir_available": summary["dir_available"],
        # 列表页要的是「谁在改这场比赛」，所以 count 带上所有用户的提交
        "counts": repo.count_by_status(SLUG),
        "mine": repo.count_by_status_for_author(user.id, plugin=SLUG),
    }


@router.get("/items/{hash_}")
def get_item(hash_: str, store: Store, _user: CurrentUser) -> dict:
    contest = store.entry_or_404(hash_)
    return {"item": contest.to_dict(), "raw": contest.snapshot(), "extra": contest.extra}


@router.get("/submissions")
def list_submissions(
    repo: Repo,
    user: CurrentUser,
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    status_filter: str = Query(
        default="open",
        alias="status",
        pattern="^(open|pending|approved|conflict|applied|rejected|all)$",
    ),
    q: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """列出提交单。

    普通用户只能看到自己的；管理员用 `scope=all` 看全部（「我的提交」页的
    「查看全部用户」开关）。

    `q` 按「这条改动落在哪场比赛上」搜：匹配 `平台 · 简称`、比赛名称，或哈希前缀。
    搜索在服务端做，因为要跟着分页 —— 只在前端过滤的话，第 2 页的数据会漏。
    """
    author_id = None
    if scope == "mine" or not user.is_admin:
        author_id = user.id

    rows = query_submissions(repo, status_filter, q, author_id=author_id)
    total = len(rows)
    start = (page - 1) * page_size

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row(item) for item in rows[start : start + page_size]],
        # counts 是本插件各状态的数字（管理员看全部用户时的口径），
        # mine 是当前用户自己的 —— 前端按当前 scope 挑一份显示
        "counts": repo.count_by_status(SLUG),
        "mine": repo.count_by_status_for_author(user.id, plugin=SLUG),
    }


def query_submissions(
    repo: Repo,
    status_filter: str,
    q: str,
    *,
    author_id: int | None = None,
) -> list[Submission]:
    """按状态 + 搜索词取提交单，按时间倒序。

    一次取全再自己筛选，而不是让 SQL 分页，原因有两个：

      - `open` 是「待审 + 已通过 + 冲突」三个状态的并集，框架的
        `list_submissions` 只能按单个状态过滤；
      - `q` 要匹配的内容（比赛名、简称）存在 JSON 列里，没法走 SQL。

    本工具的提交量级是几十条，一次读完更简单，也保证「搜索 + 分页 + 计数」
    三者算的是同一批数据。审核台也用它，所以搜索的行为两处一致。
    """
    rows, _ = repo.list_submissions(plugin=SLUG, author_id=author_id, limit=_MAX_ROWS, offset=0)
    if status_filter == "open":
        rows = [item for item in rows if item.status in OPEN_FLOW_STATUSES]
    elif status_filter != "all":
        rows = [item for item in rows if item.status == status_filter]

    rows.sort(key=lambda item: item.created_at, reverse=True)

    needle = q.strip().lower()
    if needle:
        rows = [item for item in rows if _matches_query(item, needle)]
    return rows


def _matches_query(submission: Submission, needle: str) -> bool:
    """提交单与搜索词是否匹配（看它落在哪场比赛上）。

    提交时的原值优先 —— 那是审核员在队列里看到的那个名字；新增类提交没有原值，
    用提交值。
    """
    if needle in submission.img_key.lower():
        return True
    source = submission.base_value or submission.submitted_value or {}
    if not isinstance(source, dict):
        return False
    return any(needle in str(source.get(name) or "").lower() for name in ("platform", "abbr", "name"))


@router.get("/queue")
def review_queue(
    repo: Repo,
    admin: AdminUser,
    status_filter: str = Query(
        default="pending",
        alias="status",
        pattern="^(open|pending|approved|conflict|rejected|applied|all)$",
    ),
    q: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """审核队列（本工具自己的）。

    框架的 `GET /api/plugins/curation/admin/queue` 只认 `img_key` 精确匹配、也没法按比赛名搜，
    而审核台需要「按比赛找一条提交」。这里用的是同一套提交单模型和同一个
    `_row()` 形状，所以两个接口返回的东西是一致的，只是这个多了 `q`。
    """
    rows = query_submissions(repo, status_filter, q)
    total = len(rows)
    start = (page - 1) * page_size

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row(item) for item in rows[start : start + page_size]],
        "counts": repo.count_by_status(SLUG),
    }


@router.post("/submissions", status_code=status.HTTP_201_CREATED)
def submit_create(payload: ContestCreateRequest, repo: Repo, store: Store, user: CurrentUser) -> dict:
    """提交一条新增。

    「新增」有时候其实是修改：手工录入和 Bot 的 `/导入比赛` 都可能重复录同一场比赛。
    提交内容的身份哈希与列表里某条相同时，这里自动改发「修改」，
    免得下发后文件里出现两条重复的比赛。

    降级成修改时**同样按字段拆分**：用户以为自己在录一场新比赛，可能有三四个字段
    和列表里那条不一样，那就不再是一条「整条覆盖」，而是每个字段一条提交单。
    """
    try:
        fields = clean_fields(payload.contest.as_raw())
    except (ValidationError, StoreError) as error:
        raise _bad_request(error) from error

    img_key = hash_of(fields)
    clash = store.conflicts_with(fields)
    if clash is None:
        # 同一身份只可能有一条在途提交，反复改是覆盖而不是刷队列
        submission, created = repo.upsert_submission(
            plugin=SLUG,
            type=types.TYPE_CREATE,
            img_key=img_key,
            target="",
            submitted_value=fields,
            note=payload.note.strip(),
            author_id=user.id,
            base_value=None,
        )
        _log(repo, user, submission, created)
        return {
            "submissions": [_row(submission)],
            "submission": _row(submission),
            "fields": list(FIELD_ORDER),
            "resolved_type": types.TYPE_CREATE,
            "resolved_hash": submission.img_key,
            "reason": "",
        }

    contest = store.find(clash)
    base_value = contest.snapshot() if contest is not None else {}
    touched = changed_fields(base_value, fields)
    if not touched:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="列表里已经有完全相同的比赛，无需重复录入",
        )

    submissions = []
    for name in touched:
        submission, created = repo.upsert_submission(
            plugin=SLUG,
            type=types.TYPE_UPDATE,
            img_key=clash,
            target=name,
            submitted_value=fields[name],
            note=payload.note.strip(),
            author_id=user.id,
            base_value=base_value,
        )
        submissions.append(_row(submission))
        _log(repo, user, submission, created)

    return {
        "submissions": submissions,
        "submission": submissions[0],
        "fields": touched,
        "resolved_type": types.TYPE_UPDATE,
        "resolved_hash": clash,
        "reason": (
            "列表里已经有同一场比赛（平台 + 开始时间 + 名称相同），"
            f"这 {len(submissions)} 条改动按「修改」排队，否则下发后会多出一条重复的比赛"
        ),
    }


@router.post("/submissions/update", status_code=status.HTTP_201_CREATED)
def submit_update(payload: ContestUpdateRequest, repo: Repo, store: Store, user: CurrentUser) -> dict:
    """提交一次修改。

    **改了几个字段就拆成几条提交单**（与 PickOne 一致：改描述、改点赞是两条提交）。
    管理员因此可以只接受其中一条 —— 整条一起提交的话，他要么全接受，要么全驳回，
    而「比赛名写错了但时长是对的」这种情况很常见。

    每条提交单：
      - `target`   = 字段名（框架靠 `(plugin, type, img_key, target, author_id)` 去重，
                     所以同一场比赛的不同字段互不干扰，反复改同一个字段是覆盖）；
      - `submitted_value` = 那个字段的新值（标量）；
      - `base_value`      = 提交时看到的**整条快照**，冲突检测仍然按整条比。
    """
    contest = store.entry_or_404(payload.hash)
    try:
        fields = clean_fields(payload.contest.as_raw())
    except (ValidationError, StoreError) as error:
        raise _bad_request(error) from error

    base_value = contest.snapshot()
    # 按字段逐个比，而不是直接比字典：clean_fields 的键顺序固定，
    # 而请求模型给出来的键顺序跟着请求体走，直接 == 会因为顺序不同误判成「改过」。
    touched = changed_fields(base_value, fields)
    if not touched:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="提交内容与当前值相同，无需修改")

    submissions = []
    for name in touched:
        submission, created = repo.upsert_submission(
            plugin=SLUG,
            type=types.TYPE_UPDATE,
            img_key=contest.hash,
            target=name,
            submitted_value=fields[name],
            note=payload.note.strip(),
            author_id=user.id,
            base_value=base_value,
        )
        submissions.append(_row(submission))
        _log(repo, user, submission, created)

    return {
        "submissions": submissions,
        "submission": submissions[0],
        "fields": touched,
        "resolved_type": types.TYPE_UPDATE,
        "reason": "",
    }


@router.post("/admin/delete", status_code=status.HTTP_201_CREATED)
def submit_delete(payload: ContestDeleteRequest, repo: Repo, store: Store, admin: AdminUser) -> dict:
    """提交一条删除。

    **刻意只开给管理员**（`AdminUser` 而不是 `CurrentUser`）：删掉一场比赛不可逆，
    而且通常发生在「上游已取消、Bot 还在渲染」的时候，属于维护动作而不是用户贡献。
    这里同样只排队，真正写盘仍然要经过审核台的一键下发。
    """
    contest = store.entry_or_404(payload.hash)
    submission, created = repo.upsert_submission(
        plugin=SLUG,
        type=types.TYPE_DELETE,
        img_key=contest.hash,
        target="",
        submitted_value=DELETE_MARKER,
        note=payload.note.strip(),
        author_id=admin.id,
        base_value=contest.snapshot(),
    )
    _log(repo, admin, submission, created)
    return {"submission": _row(submission), "resolved_type": types.TYPE_DELETE, "reason": ""}


@router.delete("/submissions/{submission_id}")
def withdraw(submission_id: int, repo: Repo, user: CurrentUser) -> dict:
    """撤回自己待审核的提交。"""
    submission = repo.get_submission(submission_id)
    if submission is None or submission.plugin != SLUG:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在")
    if submission.author_id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能撤回自己的提交")

    try:
        repo.withdraw_submission(submission_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except Exception as error:  # noqa: BLE001 - 框架用通用异常报「该状态不能撤回」
        raise _bad_request(error) from error

    repo.add_log(
        actor_id=user.id,
        username=user.username,
        action="withdraw_submission",
        detail=f"撤回提交单 #{submission_id}",
    )
    return {"ok": True}


# ------------------------------------------------------------------ 辅助


def _badge(value: dict[str, Any]) -> str:
    """`平台 · 简称`：列表和提交单里指代一场比赛用的都是这个写法。"""
    platform = str(value.get("platform") or "").strip()
    abbr = str(value.get("abbr") or "").strip() or str(value.get("name") or "").strip()
    if platform and abbr:
        return f"{platform} · {abbr}"
    return platform or abbr or "（未命名）"


def _target_label(submission: Submission) -> str:
    """这条提交落在哪场比赛上。

    优先用「提交时 / 提交成的值」里的名字：比赛被删掉或改名之后，
    磁盘上已经找不到它了，但审核员仍然需要看到它当时叫什么。
    """
    if submission.type == types.TYPE_CREATE:
        # 新增类只说「新条目 · 简称」：平台在改动列里已经列出来了，重复一遍只会太长
        value = submission.submitted_value
        name = str(value.get("abbr") or value.get("name") or "").strip()
        return f"新条目 · {name}" if name else "新条目"
    # 修改/删除类都拿提交时的整条快照来指代（提交值是标量，拼不出名字）
    source = submission.base_value or {}
    return _badge(source) if source else submission.img_key


def _row(submission: Submission) -> dict:
    """提交单在界面上的统一形状。

    比框架原样多几个东西：

      - `target`：那条比赛的 `平台 · 简称`（新增时是「新条目 · 简称」）。后端能算出
        可读的名字，就不该让前端对着哈希拼字符串；
      - `field` / `field_label`：这条提交单负责的字段（逐字段拆分提交的产物）。
        界面上的「类型」列用它显示「时长」这种具体字段，而不是笼统的「修改比赛」；
      - `identity_changed` / `base_hash` / `new_hash`：生效后 ID 会不会变。
        改「平台 / 比赛全称 / 开始时间」会换掉身份哈希，审核和下发前都要看得见。
    """
    data = submission.to_dict()
    detail = submission.conflict_detail or {}
    field_name = submission.target.strip() or None
    change = identity_change(submission)
    return {
        "id": data["id"],
        "type": data["type"],
        "type_label": data["type_label"],
        "status": data["status"],
        "img_key": data["img_key"],
        "target": _target_label(submission),
        "field": field_name,
        "field_label": FIELD_LABELS.get(field_name or "", ""),
        "identity_changed": change["identity_changed"],
        "base_hash": change["id_before"],
        "new_hash": change["id_after"],
        "submitted_value": data["submitted_value"],
        "base_value": data["base_value"],
        "note": data["note"],
        "author_name": data["author_name"],
        "review_comment": data["review_comment"],
        "created_at": data["created_at"],
        "reviewed_at": data["reviewed_at"],
        "applied_at": data["applied_at"],
        "conflict_reason": detail.get("reason", ""),
        # 冲突裁定的三方对比（提交时原值 / 磁盘现值 / 提交新值）在这个对象里，
        # 裁定弹窗直接用它拼表 —— 只给一句 reason 的话，管理员看不到要比什么
        "conflict_detail": data["conflict_detail"],
        "conflict_at": data["conflict_at"],
    }
