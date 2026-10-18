"""用户侧提交路由（挂载于 `/api/plugins/pickone/submissions`）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError as PydanticValidationError

from framework.curation.pickone.changes import SLUG, field_for_type
from framework.curation.pickone.schemas import (
    CategoryCreateRequest,
    CategorySubmitRequest,
    CommentsSubmitRequest,
    ImageBatchRequest,
    LikesSubmitRequest,
    OcrSubmitRequest,
)
from framework.curation.pickone.store import NotFoundError, PickOneStore, StoreError, ValidationError
from framework.curation.pickone.types import (
    TYPE_CATEGORY,
    TYPE_CATEGORY_CREATE,
    TYPE_COMMENTS,
    TYPE_LIKES,
    TYPE_OCR_TEXT,
)
from framework.curation.repository import (
    OPEN_STATUSES,
    STATUS_CONFLICT,
    STATUS_PENDING,
    Repository,
    Submission,
)
from web.content.deps import CurrentUser, Repo

from .deps import Store

router = APIRouter(prefix="/submissions", tags=["pickone-submissions"])

# 「待处理」的提交：还没写入 OBot-ACM 的全部状态
OPEN_FLOW_STATUSES = (*OPEN_STATUSES, STATUS_CONFLICT)


def _s(value: Any) -> Any:
    """规范化后用于比较，避免把等价改动也算成一次修改。"""
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def _parse_body(payload: dict, schema):
    try:
        return schema.model_validate(payload or {})
    except PydanticValidationError as error:
        first = error.errors()[0] if error.errors() else {}
        field_name = ".".join(str(item) for item in first.get("loc", ()))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name}: {first.get('msg', '参数不合法')}",
        ) from error


def _require_image(store: PickOneStore, img_key: str, name: str) -> None:
    """确认图片确实属于该类别。"""
    if not store.is_valid_image_name(name):
        raise ValidationError(f"非法的图片名: {name}")
    if not (store.category_dir(img_key) / name).is_file():
        raise NotFoundError(f"图片不存在: {img_key}/{name}")


def _submit_image_field(
    repo: Repository,
    store: PickOneStore,
    user: CurrentUser,
    img_key: str,
    name: str,
    type: str,
    value: Any,
    note: str,
) -> Submission:
    """图片字段类提交的公共部分。"""
    _require_image(store, img_key, name)
    entry = store.parser_entry(img_key, name)
    field_name = field_for_type(type)
    assert field_name is not None

    # base_value 始终记录「磁盘上的真实值」，应用时用它检测冲突（点赞是增量，不比对）
    base_value = entry.get(field_name)
    same_as_current = type != TYPE_LIKES and _s(base_value) == _s(value)

    # 已经是「待下发 / 撞了冲突」的话，允许用户改主意并覆盖，不做去重。
    pending = [
        item
        for item in repo.open_submissions_for_target(SLUG, type, img_key, name, author_id=user.id)
        if item.status == STATUS_PENDING
    ]

    if pending and _s(pending[0].submitted_value) == _s(value):
        # 还没改主意：提交同一个值没有意义
        raise ValidationError("这个改动已经在待审队列里了")
    if same_as_current:
        # 提交回磁盘当前值等于撤回这次修改，而不是一条新提交；
        # 要撤回请用「我的提交」里的撤回按钮，避免队列里留下空改动。
        raise ValidationError("提交内容与当前值相同，无需修改")

    submission, created = repo.upsert_submission(
        plugin=SLUG,
        type=type,
        img_key=img_key,
        target=name,
        submitted_value=value,
        note=note,
        author_id=user.id,
        base_value=base_value,
    )
    repo.add_log(
        actor_id=user.id,
        username=user.username,
        action="submit_image_change",
        detail=f"{'新建' if created else '更新'} {type} @ {img_key}/{name}",
    )
    return submission


def _submit_category(
    repo: Repository,
    store: PickOneStore,
    user: CurrentUser,
    img_key: str,
    category_id: str,
    keys: list[str],
    note: str,
) -> Submission:
    """类别改动的公共部分（改已有类别 / 新增类别）。"""
    existing = store.load_categories().get(img_key)
    is_new = existing is None

    try:
        if is_new:
            store.validate_new_key(img_key)
        clean_id = store.validate_category_id(category_id)
        clean_keys = store.validate_alias_list(keys, exclude_key=img_key)
    except ValidationError as error:
        raise _bad_request(error) from error

    value = {"id": clean_id, "keys": clean_keys}
    base_value = None if is_new else {"id": existing.id, "keys": list(existing.keys)}

    if not is_new and _s(base_value["id"]) == _s(clean_id) and _s(base_value["keys"]) == _s(clean_keys):
        raise ValidationError("提交内容与当前值相同，无需修改")

    type_ = TYPE_CATEGORY_CREATE if is_new else TYPE_CATEGORY
    try:
        submission, created = repo.upsert_submission(
            plugin=SLUG,
            type=type_,
            img_key=img_key,
            target="",
            submitted_value=value,
            note=note,
            author_id=user.id,
            base_value=base_value,
        )
    except ValueError as error:
        raise _bad_request(error) from error

    repo.add_log(
        actor_id=user.id,
        username=user.username,
        action="submit_category",
        detail=f"{'申请新增' if is_new else '修改'}类别 {img_key}（{'新建' if created else '更新'}提交单）",
    )
    return submission


@router.get("")
def list_submissions(
    repo: Repo,
    user: CurrentUser,
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    status_filter: str = Query(
        default="open",
        alias="status",
        pattern="^(open|pending|approved|conflict|applied|rejected|all)$",
    ),
    img_key: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """列出提交单。普通用户只能看自己的，管理员可以看全部。"""
    author_id = None
    if scope == "mine" or not user.is_admin:
        author_id = user.id

    status_map = {
        "pending": "pending",
        "approved": "approved",
        "conflict": "conflict",
        "applied": "applied",
        "rejected": "rejected",
    }

    if status_filter == "open":
        # 「待处理」= 待审 + 已通过 + 冲突挂起，一次性查出来再分页
        rows, total = repo.list_submissions(
            plugin=SLUG,
            author_id=author_id,
            img_key=img_key or None,
            statuses=OPEN_FLOW_STATUSES,
            limit=None,
            offset=0,
        )
        rows.sort(key=lambda item: item.created_at, reverse=True)
        start = (page - 1) * page_size
        rows = rows[start : start + page_size]
    elif status_filter == "all":
        rows, total = repo.list_submissions(
            plugin=SLUG,
            author_id=author_id,
            img_key=img_key or None,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    else:
        rows, total = repo.list_submissions(
            plugin=SLUG,
            status=status_map[status_filter],
            author_id=author_id,
            img_key=img_key or None,
            limit=page_size,
            offset=(page - 1) * page_size,
        )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [item.to_dict() for item in rows],
        # counts 是本插件各状态的数字，mine 是当前用户自己的
        "counts": repo.count_by_status(SLUG),
        "mine": repo.count_by_status_for_author(user.id, plugin=SLUG),
    }


@router.get("/mine")
def my_submissions(repo: Repo, user: CurrentUser) -> dict:
    """当前用户所有待处理的提交，前端用来在浏览页打标记。"""
    items = repo.list_open_submissions_for_author(user.id)
    return {"items": [item.to_dict() for item in items]}


@router.post("/batch", status_code=status.HTTP_200_OK)
def create_submissions_batch(
    img_key: str,
    payload: ImageBatchRequest,
    repo: Repo,
    store: Store,
    user: CurrentUser,
) -> dict:
    """一次提交一张图片的多个字段（编辑表单用）。

    各字段独立处理：某个字段校验不过或没变化，不影响其它字段。
    """
    img_key = img_key.strip()
    if img_key not in store.load_categories():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"类别不存在: {img_key}")

    if not store.is_valid_image_name(payload.name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="非法的图片名")

    created: list[dict] = []
    skipped: list[dict] = []

    def attempt(field: str, value: Any) -> None:
        try:
            _submit_image_field(repo, store, user, img_key, payload.name, field, value, payload.note.strip())
            created.append({"field": field})
        except (ValidationError, NotFoundError, StoreError) as error:
            skipped.append({"field": field, "reason": str(error)})

    if payload.ocr_text is not None:
        attempt(TYPE_OCR_TEXT, payload.ocr_text.strip())
    if payload.likes_delta is not None:
        attempt(TYPE_LIKES, payload.likes_delta)
    if payload.comments is not None:
        try:
            comments = CommentsSubmitRequest(name=payload.name, comments=payload.comments).cleaned()
        except ValueError as error:
            skipped.append({"field": TYPE_COMMENTS, "reason": str(error)})
        else:
            attempt(TYPE_COMMENTS, comments)

    if not created:
        reason = skipped[0]["reason"] if skipped else "没有需要提交的改动"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    return {
        "created": created,
        "skipped": skipped,
        "message": f"已提交 {len(created)} 项" + (f"，{len(skipped)} 项未提交" if skipped else ""),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_submission(
    img_key: str,
    payload: dict,
    repo: Repo,
    store: Store,
    user: CurrentUser,
    type: str = Query(..., pattern="^(ocr_text|likes|comments|category|category_create)$"),
) -> dict:
    """统一入口：类别走路径参数，提交内容走 JSON body，按 `type` 分发。"""
    img_key = img_key.strip()
    if not img_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺乏类别参数")

    try:
        if img_key not in store.load_categories() and type != TYPE_CATEGORY_CREATE:
            raise NotFoundError(f"类别不存在: {img_key}")

        if type == TYPE_OCR_TEXT:
            body = _parse_body(payload, OcrSubmitRequest)
            submission = _submit_image_field(
                repo, store, user, img_key, body.name, type, body.ocr_text.strip(), body.note
            )
        elif type == TYPE_LIKES:
            body = _parse_body(payload, LikesSubmitRequest)
            submission = _submit_image_field(
                repo, store, user, img_key, body.name, type, body.likes_delta, body.note
            )
        elif type == TYPE_COMMENTS:
            body = _parse_body(payload, CommentsSubmitRequest)
            try:
                comments = body.cleaned()
            except ValueError as error:
                raise _bad_request(error) from error
            submission = _submit_image_field(repo, store, user, img_key, body.name, type, comments, body.note)
        elif type == TYPE_CATEGORY:
            body = _parse_body(payload, CategorySubmitRequest)
            submission = _submit_category(repo, store, user, img_key, body.category_id, body.keys, body.note)
        else:
            body = _parse_body(payload, CategoryCreateRequest)
            submission = _submit_category(
                repo,
                store,
                user,
                img_key,
                body.category_id or img_key,
                body.keys or [],
                body.note,
            )
    except (ValidationError, NotFoundError, StoreError) as error:
        raise _bad_request(error) from error

    created = repo.get_submission(submission.id) or submission
    return {"submission": created.to_dict()}


@router.delete("/{submission_id}")
def withdraw(submission_id: int, repo: Repo, user: CurrentUser) -> dict:
    """撤回自己待审核的提交。"""
    submission = repo.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在")
    if submission.author_id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能撤回自己的提交")

    try:
        repo.withdraw_submission(submission_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except Exception as error:
        raise _bad_request(error) from error

    repo.add_log(
        actor_id=user.id,
        username=user.username,
        action="withdraw_submission",
        detail=f"撤回提交单 #{submission_id}",
    )
    return {"ok": True}
