"""Shared review actions. Authentication is the current QQ dashboard session."""

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from framework.curation.repository import ConflictError

from .deps import AdminUser, Repo


class ReviewRequest(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=200)


class ReviewBatchRequest(ReviewRequest):
    ids: list[int] = Field(min_length=1, max_length=200)


router = APIRouter(prefix="/admin")


@router.get("/queue")
def review_queue(
    repo: Repo,
    admin: AdminUser,
    plugin: str = Query(default="", max_length=64, description="只看某个工具的提交单"),
    status_filter: str = Query(
        default="pending",
        alias="status",
        pattern="^(pending|approved|conflict|rejected|applied|all)$",
    ),
    img_key: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """待审核 / 待应用 / 冲突 / 已处理队列。"""
    kwargs: dict = {}
    if status_filter != "all":
        kwargs["status"] = status_filter
    items, total = repo.list_submissions(
        plugin=plugin or None,
        img_key=img_key or None,
        limit=page_size,
        offset=(page - 1) * page_size,
        **kwargs,
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [item.to_dict() for item in items],
        "counts": repo.count_by_status(plugin or None),
    }


@router.post("/review/batch")
def review_batch(
    payload: ReviewBatchRequest,
    repo: Repo,
    admin: AdminUser,
) -> dict:
    """批量审核：批准 / 驳回一批提交单。"""
    succeeded: list[int] = []
    failed: list[dict] = []
    for submission_id in payload.ids:
        try:
            repo.review_submission(
                submission_id,
                approve=payload.approve,
                reviewer_id=admin.id,
                comment=payload.comment,
            )
            succeeded.append(submission_id)
        except KeyError:
            failed.append({"id": submission_id, "reason": "不存在"})
        except ConflictError as error:
            failed.append({"id": submission_id, "reason": str(error)})

    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="review_batch",
        detail=f"批量{'通过' if payload.approve else '驳回'} {len(succeeded)} 条，失败 {len(failed)} 条",
        is_admin=True,
    )
    return {"succeeded": succeeded, "failed": failed}


@router.post("/review/{submission_id}")
def review(
    submission_id: int,
    payload: ReviewRequest,
    repo: Repo,
    admin: AdminUser,
) -> dict:
    """审核（批准 / 驳回）。批准只是排队，真正落盘要再点「一键应用」。"""
    submission = repo.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在")

    try:
        updated = repo.review_submission(
            submission_id,
            approve=payload.approve,
            reviewer_id=admin.id,
            comment=payload.comment,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except ConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="review_approved" if payload.approve else "review_rejected",
        detail=f"提交单 #{submission_id}（{submission.type_label} @ {submission.img_key}）"
        + (f"，备注：{payload.comment}" if payload.comment else ""),
        is_admin=True,
    )
    return {"submission": updated.to_dict()}


@router.post("/unreview/{submission_id}")
def unreview(submission_id: int, repo: Repo, admin: AdminUser) -> dict:
    """撤回审核：把「已通过待下发」的提交退回「待审核」。

    审核通过后、下发之前管理员改主意（审错了、想再等等、想和别的改动一起发），
    这里可以把它放回待审核队列重新审。
    """
    submission = repo.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在")

    try:
        updated = repo.unreview_submission(submission_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except ConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="review_revoked",
        detail=f"撤回审核 #{submission_id}（{submission.type_label} @ {submission.img_key}），退回待审核",
        is_admin=True,
    )
    return {"submission": updated.to_dict()}


@router.get("/logs")
def logs(
    repo: Repo,
    admin: AdminUser,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    items, total = repo.list_logs(limit=page_size, offset=(page - 1) * page_size)
    return {"total": total, "page": page, "page_size": page_size, "items": items}
