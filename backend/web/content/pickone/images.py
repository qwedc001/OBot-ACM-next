"""图片浏览与文件服务路由（挂载于 `/api/plugins/pickone/images`）。"""

from __future__ import annotations

import contextlib
import os
import secrets
import threading

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.responses import Response as RawResponse

# 缩略图缓存目录与上限是框架级配置（所有工具共用一个缓存目录）
from framework.curation.pickone import config
from framework.curation.pickone.changes import effective_image, image_pending_changes, load_open_submissions
from framework.curation.pickone.hashing import (
    ThumbnailError,
    base62_to_md5,
    hash_id_of,
    make_thumbnail,
    thumbnail_cache_key,
)
from framework.curation.pickone.palette import accent_of
from framework.curation.pickone.store import NotFoundError, PickOneStore, StoreError, ValidationError
from web.content.deps import CurrentUser, Repo

from .deps import Store

router = APIRouter(prefix="/images", tags=["pickone-images"])

_GIF_MEDIA_TYPE = "image/gif"


def _require_valid_category(img_key: str) -> None:
    """类别标识会参与拼路径，先按数据层的规则卡一道。

    非法标识直接 404，不要等到文件系统调用时才报错 —— 既不泄露路径，
    也不会把 os.stat 的错误信息带进响应。
    """
    if not PickOneStore.is_valid_key(img_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"类别不存在: {img_key}")


def _stat_to_payload(stat) -> dict:
    """把 ImageStat 转成前端需要的短结构（不含 pending 覆盖）。"""
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
    }


@router.get("/{img_key}")
def list_images(
    img_key: str,
    repo: Repo,
    store: Store,
    user: CurrentUser,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=60, ge=1, le=200),
    q: str = Query(default="", max_length=64),
    filter: str = Query(default="all", pattern="^(all|missing_ocr|has_change)$"),
    sort: str = Query(default="hash", pattern="^(hash|likes|comments|add_time|pickup_times)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> dict:
    """分页列出某个类别下的表情包。

    字段值都是磁盘原值；审核中的改动放在 pending_changes 里，只有自己的那条
    会带 mine=true（前端从它接着改，别人的只能看）。
    """
    _require_valid_category(img_key)
    try:
        store.category_or_404(img_key)
    except NotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    pending = image_pending_changes(load_open_submissions(repo)).get(img_key, {})

    stats = list(store.iter_image_stats(img_key))

    needle = q.strip().casefold()
    if needle:
        stats = [
            item
            for item in stats
            if needle in item.ocr_text.casefold()
            or needle in item.hash_id.casefold()
            or needle in item.name.casefold()
        ]

    if filter == "missing_ocr":
        stats = [item for item in stats if item.needs_ocr]
    elif filter == "has_change":
        stats = [item for item in stats if item.name in pending]

    if sort == "hash":
        stats.sort(key=lambda item: item.name, reverse=(order == "desc"))
    elif sort == "likes":
        stats.sort(key=lambda item: (item.likes, item.name), reverse=(order == "desc"))
    elif sort == "comments":
        stats.sort(key=lambda item: (len(item.comments), item.name), reverse=(order == "desc"))
    elif sort == "add_time":
        stats.sort(key=lambda item: (item.add_time, item.name), reverse=(order == "desc"))
    else:
        stats.sort(key=lambda item: (item.pickup_times, item.name), reverse=(order == "desc"))

    total = len(stats)
    start = (page - 1) * page_size
    page_items = stats[start : start + page_size]

    return {
        "img_key": img_key,
        "total": total,
        "page": page,
        "page_size": page_size,
        "missing_ocr_in_view": sum(1 for item in page_items if item.needs_ocr),
        "items": [effective_image(item, pending.get(item.name), viewer_id=user.id) for item in page_items],
    }


@router.get("/{img_key}/stats")
def image_stats(img_key: str, repo: Repo, store: Store, _user: CurrentUser) -> dict:
    """类别级统计，用于页面上的进度提示。"""
    _require_valid_category(img_key)
    try:
        store.category_or_404(img_key)
    except NotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    total = 0
    missing = 0
    legacy = 0
    likes = 0
    for stat in store.iter_image_stats(img_key):
        total += 1
        missing += 1 if stat.needs_ocr else 0
        legacy += 1 if stat.legacy else 0
        likes += stat.likes

    # 有多少张图带着待处理的改动（筛选按钮上要显示数量）
    pending = image_pending_changes(load_open_submissions(repo)).get(img_key, {})

    return {
        "img_key": img_key,
        "total": total,
        "missing_ocr": missing,
        "legacy_entries": legacy,
        "total_likes": likes,
        "pending_changes": len(pending),
    }


@router.get("/{img_key}/item/{name}")
def image_detail(img_key: str, name: str, repo: Repo, store: Store, user: CurrentUser) -> dict:
    _require_valid_category(img_key)
    try:
        stat = store.get_image_stat(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    pending = image_pending_changes(load_open_submissions(repo)).get(img_key, {})
    payload = effective_image(stat, pending.get(name), viewer_id=user.id)
    payload["accent"] = accent_of(img_key)
    return {"image": payload}


@router.get("/{img_key}/raw/{name}")
def raw_image(img_key: str, name: str, store: Store, _user: CurrentUser) -> FileResponse:
    """原图（GIF）。"""
    _require_valid_category(img_key)
    try:
        path = store.image_path(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return FileResponse(
        path,
        media_type=_GIF_MEDIA_TYPE,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{img_key}/thumb/{name}")
def thumbnail(img_key: str, name: str, store: Store, _user: CurrentUser) -> RawResponse:
    """缩略图（WebP，磁盘缓存）。动图只取前几帧，避免一次解码上千张。"""
    _require_valid_category(img_key)
    try:
        path = store.image_path(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    stat = path.stat()
    cache_key = thumbnail_cache_key(str(path), stat.st_mtime_ns, stat.st_size)
    cache_path = store.lib_dir / ".thumbnails" / f"{cache_key}.webp"

    cached = _read_thumb_cache(cache_path)
    if cached is not None:
        return RawResponse(
            content=cached,
            media_type="image/webp",
            headers={"Cache-Control": "private, max-age=604800"},
        )

    if stat.st_size > config.THUMB_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="图片文件过大，无法生成缩略图",
        )

    try:
        data = make_thumbnail(path.read_bytes())
    except ThumbnailError as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error
    except Exception:  # 坏图不应该把整个页面拖垮
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="无法生成缩略图（图片格式损坏或不受支持）",
        ) from None

    # 缓存写不进去不算失败：这次照样把图发回去，下次请求再重新生成
    with contextlib.suppress(OSError):
        _write_thumb_cache(cache_path, data)

    return RawResponse(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=604800"},
    )


def _read_thumb_cache(cache_path) -> bytes | None:
    """读缩略图缓存；文件缺失、读不出来或明显不完整时返回 None（下次重新生成）。

    缓存键只包含源文件的 (路径, mtime, 大小)，一旦缓存里落下的是被截断的 WebP，
    浏览器会一直拿到坏图。所以这里不只看文件在不在，还要看 RIFF 头声明的大小
    和实际长度对不对得上 —— 只做长度校验，不用重新解码。
    """
    try:
        data = cache_path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    declared = int.from_bytes(data[4:8], "little") + 8
    if declared != len(data):
        return None
    return data


def _write_thumb_cache(cache_path, data: bytes) -> None:
    """原子地写缩略图缓存。

    直接 write_bytes 的话，并发请求可能读到只写了一半的 WebP（浏览器随即报
    "image corrupt"）。临时文件 + os.replace 与数据文件的写回方式保持一致，
    临时名里带上线程 id 和随机串，避免两个并发请求共用同一个临时文件。
    """
    tmp_path = cache_path.with_name(
        f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, cache_path)
    finally:
        # 成功时临时文件已被搬走；这里清掉失败残留，删不掉也不该影响响应
        with contextlib.suppress(OSError):
            tmp_path.unlink()


@router.get("/{img_key}/hash-id/{hash_id}")
def lookup_by_hash_id(
    img_key: str,
    hash_id: str,
    store: Store,
    _user: CurrentUser,
) -> dict:
    """按 Bot 回复里的 Base62 ID 反查图片。"""
    _require_valid_category(img_key)
    # 先在路由层卡长度：base62_to_md5 会做大整数累乘，长度不设限就是白送 CPU
    if not 1 <= len(hash_id) <= 22:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID 长度不合法（1..22）")
    try:
        md5 = base62_to_md5(hash_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    name = f"{md5}.gif"
    try:
        stat = store.get_image_stat(img_key, name)
    except (NotFoundError, ValidationError, StoreError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return {"image": _stat_to_payload(stat), "name": name, "hash_id": hash_id_of(md5)}
