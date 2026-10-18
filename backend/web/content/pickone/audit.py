"""OBot-ACM 原生 ``__AUDIT__`` 图片审核接口。

这条队列不经过 OBot-EP 的提交单表：上游 Bot 已经把普通用户上传的文件
直接放进 Pick-One 的 ``__AUDIT__`` 目录，审核动作就是移动或删除文件。
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.responses import Response as RawResponse

from framework.curation.pickone import config as server_config
from framework.curation.pickone.hashing import ThumbnailError, make_thumbnail, thumbnail_cache_key
from framework.curation.pickone.store import NotFoundError, PickOneStore, StoreError, ValidationError
from web.content.deps import AdminUser, Repo

from .deps import Store
from .images import _read_thumb_cache, _write_thumb_cache

router = APIRouter(prefix="/admin/audit", tags=["pickone-upstream-audit"])
_GIF_MEDIA_TYPE = "image/gif"


def _item_to_payload(item) -> dict:
    return {
        "img_key": item.img_key,
        "category_id": item.category_id,
        "name": item.name,
        "md5": item.md5,
        "hash_id": item.hash_id,
        "add_time": item.add_time,
        "size": item.size,
    }


def _audit_path(store: PickOneStore, img_key: str, name: str):
    try:
        return store.audit_image_path(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("")
def list_audit_images(
    store: Store,
    _admin: AdminUser,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=60, ge=1, le=200),
    img_key: str = Query(default="", max_length=64),
) -> dict:
    try:
        items, total = store.list_audit_images(
            img_key=img_key.strip() or None, page=page, page_size=page_size
        )
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return {
        "items": [_item_to_payload(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{img_key}/raw/{name}")
def raw_audit_image(img_key: str, name: str, store: Store, _admin: AdminUser) -> FileResponse:
    path = _audit_path(store, img_key, name)
    return FileResponse(
        path,
        media_type=_GIF_MEDIA_TYPE,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{img_key}/thumb/{name}")
def audit_thumbnail(img_key: str, name: str, store: Store, _admin: AdminUser) -> RawResponse:
    path = _audit_path(store, img_key, name)
    stat = path.stat()
    cache_key = thumbnail_cache_key(str(path), stat.st_mtime_ns, stat.st_size)
    cache_path = store.lib_dir / ".thumbnails" / f"{cache_key}.webp"
    cached = _read_thumb_cache(cache_path)
    if cached:
        return RawResponse(content=cached, media_type="image/webp")
    if stat.st_size > server_config.THUMB_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="图片文件过大，无法生成缩略图",
        )
    try:
        data = make_thumbnail(path.read_bytes())
    except ThumbnailError as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="无法生成缩略图（图片格式损坏或不受支持）",
        ) from None
    with contextlib.suppress(OSError):
        _write_thumb_cache(cache_path, data)
    return RawResponse(content=data, media_type="image/webp")


@router.post("/{img_key}/{name}/approve")
def approve_audit_image(img_key: str, name: str, store: Store, repo: Repo, admin: AdminUser) -> dict:
    try:
        result = store.approve_audit_image(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="写入正式目录失败（权限或磁盘问题），请查看服务端日志",
        ) from None
    admin_action = "approve_upstream_sticker" if result == "approved" else "accept_duplicate_upstream_sticker"
    store_result = {"status": result, "img_key": img_key, "name": name}
    # The repository audit log is intentionally the only persistent record: the upstream
    # queue itself has no user or submission id to attach to the decision.
    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action=admin_action,
        detail=f"{img_key}/{name}",
        is_admin=True,
    )
    return store_result


@router.post("/{img_key}/{name}/reject")
def reject_audit_image(img_key: str, name: str, store: Store, repo: Repo, admin: AdminUser) -> dict:
    try:
        store.reject_audit_image(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除待审文件失败（权限或磁盘问题），请查看服务端日志",
        ) from None
    result = {"status": "rejected", "img_key": img_key, "name": name}
    repo.add_log(
        actor_id=admin.id,
        username=admin.username,
        action="reject_upstream_sticker",
        detail=f"{img_key}/{name}",
        is_admin=True,
    )
    return result
