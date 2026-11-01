"""PickOne 的管理侧路由（挂载于 `/api/plugins/pickone/admin`）。

这里放的全是「只有 PickOne 才知道怎么做」的动作：把审核通过的提交写回
OBot-ACM 的 config.json / parser.json、裁定写回冲突、数据体检。

通用的那部分（审核队列、审核动作、账号、日志）在框架的 `/api/plugins/curation/admin/*` 下，
不要在这里重复实现。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from framework.curation.errors import StoreError, ValidationError
from framework.curation.pickone.changes import (
    SLUG,
    ApplyConflictError,
    apply_approved,
    build_apply_plan,
    resolve_conflict,
    scan_conflicts,
)
from framework.curation.pickone.schemas import ApplyRequest, ConflictResolveRequest
from framework.curation.repository import (
    STATUS_APPROVED,
    STATUS_CONFLICT,
    STATUS_PENDING,
    Repository,
)
from web.content.deps import AdminUser, Repo

from .deps import Store

router = APIRouter(prefix="/admin", tags=["pickone-admin"])


@router.get("/apply/preview")
def apply_preview(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """应用前的 dry-run：列出将要写入的内容与冲突。"""
    return _build_apply_preview(repo, store)


def _build_apply_preview(repo: Repository, store) -> dict:
    submissions = repo.list_approved_submissions(SLUG)
    if not submissions:
        return {
            "submissions": 0,
            "image_changes": [],
            "category_entries": [],
            "conflicts": [],
            "new_keys": [],
        }

    plan = build_apply_plan(store, submissions)

    image_changes = [
        {
            "img_key": img_key,
            "count": len(items),
            "items": [
                {"name": item["name"], "field": item["field"], "value": item["value"]} for item in items
            ],
        }
        for img_key, items in plan.image_changes.items()
    ]

    return {
        "submissions": len(plan.applied_ids),
        "image_changes": image_changes,
        "category_entries": [
            {"img_key": img_key, "entry": entry} for img_key, entry in plan.category_entries.items()
        ],
        "new_keys": plan.new_keys,
        "conflicts": [conflict.to_dict() for conflict in plan.conflicts],
    }


@router.post("/conflicts/scan")
def scan(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """把「已通过但已经不能直接下发」的提交单挂成冲突（不写数据文件）。

    审核台每次刷新都调它：这样「两个人改了同一个字段」在过审那一刻就会分出胜负
    （最早过审的留在待下发，其余进冲突待裁定），不用等到点一键下发才发现。
    反复调用幂等。
    """
    try:
        return scan_conflicts(repo, store)
    except (StoreError, ValidationError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.get("/conflicts")
def list_conflicts(
    repo: Repo,
    admin: AdminUser,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """列出本插件挂起的冲突，附三方对比信息。"""
    items, total = repo.list_submissions(
        plugin=SLUG, status=STATUS_CONFLICT, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [item.to_dict() for item in items],
        "counts": repo.count_by_status(SLUG),
    }


@router.post("/conflicts/{submission_id}/resolve")
def resolve(
    submission_id: int,
    payload: ConflictResolveRequest,
    repo: Repo,
    store: Store,
    admin: AdminUser,
) -> dict:
    """裁定冲突：keep_new=true 保留提交新值（排回待下发），false 丢弃提交。"""
    try:
        result = resolve_conflict(repo, store, submission_id, keep_new=payload.keep_new, reviewer_id=admin.id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except ApplyConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (StoreError, ValidationError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except OSError:
        # 底层异常信息里带着服务端的绝对路径，不往响应里放
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="写盘失败（权限或磁盘问题），请查看服务端日志",
        ) from None

    superseded = "、".join(f"#{item['submission_id']}" for item in result["superseded"])
    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="resolve_conflict",
        detail=(
            f"冲突裁定 #{submission_id}："
            + (
                f"保留提交新值并排回待下发（取代 {superseded}）"
                if payload.keep_new
                else "丢弃提交，保留磁盘现值"
            )
        ),
        is_admin=True,
    )
    return result


@router.post("/apply")
def apply_changes(payload: ApplyRequest, repo: Repo, store: Store, admin: AdminUser) -> dict:
    """一键应用：把已审核的改动写回 OBot-ACM 的 config.json / parser.json。"""
    if payload.dry_run:
        return _build_apply_preview(repo, store)

    if not store.lib_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PickOne 数据目录不可用",
        )

    try:
        result = apply_approved(repo, store)
    except (StoreError, ValidationError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="写盘失败（权限或磁盘问题），请查看服务端日志",
        ) from None

    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="apply_changes",
        detail=(
            f"应用 {result['submissions']} 条提交：图片字段 {result['applied_images']} 处，"
            f"类别 {result['applied_categories']} 个，冲突 {len(result['conflicts'])} 条"
        ),
        is_admin=True,
    )
    return result


@router.get("/integrity")
def integrity(store: Store, admin: AdminUser) -> dict:
    """数据体检：图片和 parser.json 是否对齐。"""
    return store.check_integrity()


@router.get("/overview")
def overview(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """审核台首页需要的计数与数据目录状态。"""
    _, pending_total = repo.list_submissions(plugin=SLUG, status=STATUS_PENDING, limit=1)
    _, approved_total = repo.list_submissions(plugin=SLUG, status=STATUS_APPROVED, limit=1)
    counts = repo.count_by_status(SLUG)
    return {
        "submission_counts": counts,
        "pending_total": pending_total,
        "approved_total": approved_total,
        "conflict_total": counts[STATUS_CONFLICT],
        # 上游待审图片不经过提交单表，框架的导航角标数不到它们，
        # 所以这里一并报出去，由插件前端的 navBadge() 数进角标。
        "audit_total": store.audit_count(),
        "lib_available": store.lib_dir.is_dir(),
    }
