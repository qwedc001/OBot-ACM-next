"""算法竞赛列表的管理侧路由（挂载于 `/api/plugins/contestlist/admin`）。

这里放的全是「只有这个工具才知道怎么做」的动作：把审核通过的提交写回
OBot-ACM 的 `manual_contests.json`、裁定写回冲突、数据体检。
通用的那部分（审核队列、审核动作、账号、日志）在框架的 `/api/plugins/curation/admin/*` 下，不要重复实现。

`/apply`、`/apply/preview`、`/overview` 三个路径是**约定**：框架的通用审核台
（web/src/views/admin/PluginReviewView.vue）按这三个路径调用，请保持它们存在。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from framework.curation.contestlist.changes import (
    SLUG,
    ApplyBlockedError,
    apply_approved,
    preview_approved,
    resolve_conflict,
    scan_conflicts,
)
from framework.curation.contestlist.schemas import ApplyRequest, ConflictResolveRequest
from framework.curation.contestlist.store import ContestStore
from framework.curation.errors import StoreError, ValidationError
from framework.curation.repository import STATUS_CONFLICT
from web.content.deps import AdminUser, Repo

from .deps import Store

router = APIRouter(prefix="/admin", tags=["contestlist-admin"])


def _write_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


def _disk_error() -> HTTPException:
    # 底层异常信息里带着服务端的绝对路径，不往响应里放
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="写盘失败（权限或磁盘问题），请查看服务端日志",
    )


@router.get("/overview")
def overview(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """审核台首页需要的计数与数据文件状态。

    刻意**不返回文件路径**：审核台只需要知道「能不能下发」，绝对路径属于服务端
    内部信息，进了响应体就会出现在页面上（也没有任何界面需要它）。
    """
    counts = repo.count_by_status(SLUG)
    return {
        "submission_counts": counts,
        "conflict_total": counts[STATUS_CONFLICT],
        # 文件在不在决定「下发能不能成功」，缺失时审核台会提示只能审核
        "lib_available": _available(store),
    }


@router.get("/integrity")
def integrity(store: Store, admin: AdminUser) -> dict:
    """数据体检：格式坏掉的条目、重复的比赛（同一身份出现多次）。

    这是本插件唯一返回数据文件路径的接口：管理员排查「文件不在预期位置」
    时必须看得到它。前端不展示，只作为接口自省信息。
    """
    summary = store.summary()
    contests = store.load()
    return {
        **summary,
        "duplicate_groups": [
            {
                # 同一个身份哈希出现多次：这些条目没法唯一寻址，下发时会被判冲突
                "hash": hash_,
                "indexes": indexes,
                "badges": [contest.badge() for contest in contests if contest.hash == hash_],
            }
            for hash_, indexes in store.duplicate_hashes()
        ],
        "invalid_entries": [
            {"hash": contest.hash, "badge": contest.badge(), "problems": contest.problems}
            for contest in contests
            if not contest.valid
        ],
    }


@router.get("/apply/preview")
def apply_preview(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """下发前的 dry-run：只列出将要写入的内容与被挂起的冲突，不碰磁盘。"""
    try:
        return preview_approved(repo, store)
    except (StoreError, ValidationError) as error:
        raise _write_error(error) from error


@router.post("/apply")
def apply_changes(payload: ApplyRequest, repo: Repo, store: Store, admin: AdminUser) -> dict:
    """一键下发：把 approved 的提交写回比赛列表，冲突单转 conflict 状态挂起。"""
    if payload.dry_run:
        return apply_preview(repo, store, admin)

    if not _available(store):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="比赛列表文件不可用，请查看服务端日志（插件健康状态里有路径）",
        )

    try:
        result = apply_approved(repo, store)
    except (StoreError, ValidationError) as error:
        raise _write_error(error) from error
    except OSError:
        raise _disk_error() from None

    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="apply_contest_changes",
        detail=(
            f"下发 {result['applied']} 条：新增 {result['created']}，"
            f"修改 {result['updated']}，删除 {result['deleted']}，"
            f"冲突 {len(result['conflicts'])}"
        ),
        is_admin=True,
    )
    return result


@router.post("/conflicts/scan")
def scan(repo: Repo, store: Store, admin: AdminUser) -> dict:
    """把「已通过但已经不能直接下发」的提交单挂成冲突（不写数据文件）。

    审核台在刷新时调它：这样「两个人改了同一处」在过审那一刻就分出胜负（最早过审的
    留在待下发，其余进冲突待裁定），不用等到真的点一键下发才发现 —— 否则只是下发
    预览提醒一句，点「去处理冲突」过去是空的。反复调用幂等。
    """
    try:
        return scan_conflicts(repo, store)
    except (StoreError, ValidationError) as error:
        raise _write_error(error) from error


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
        result = resolve_conflict(
            repo,
            store,
            submission_id,
            keep_new=payload.keep_new,
            reviewer_id=admin.id,
            allow_duplicate=payload.allow_duplicate,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="提交单不存在") from error
    except ApplyBlockedError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (StoreError, ValidationError) as error:
        raise _write_error(error) from error
    except OSError:
        raise _disk_error() from None

    superseded = "、".join(f"#{item['submission_id']}" for item in result["superseded"])
    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="resolve_contest_conflict",
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


def _available(store: ContestStore) -> bool:
    """文件已经存在，或者它所在的目录在（Bot 首次运行前文件可能还没建出来）。"""
    return store.data_path.is_file() or store.data_path.parent.is_dir()
