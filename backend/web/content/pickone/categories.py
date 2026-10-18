"""类别路由（挂载于 `/api/plugins/pickone/categories`）。"""

from __future__ import annotations

from fastapi import APIRouter

from framework.curation.pickone.changes import (
    SLUG,
    category_pending_changes,
    effective_category,
    load_open_submissions,
    pending_category_drafts,
)
from framework.curation.pickone.palette import accent_of
from framework.curation.pickone.types import TYPE_CATEGORY, TYPE_CATEGORY_CREATE
from web.content.deps import CurrentUser, Repo

from .deps import Store

router = APIRouter(prefix="/categories", tags=["pickone-categories"])


@router.get("")
def list_categories(store: Store, repo: Repo, user: CurrentUser) -> dict:
    """列出所有类别：图片数、缺 OCR 数、配色，以及审核中的改动。

    展示的是磁盘原值，别人的在途改动只放在 pending_changes 里，不替换 id / keys。
    """
    counts = store.load_category_counts()
    pending = category_pending_changes(load_open_submissions(repo))

    categories = []
    for category in store.list_categories():
        payload = effective_category(category, pending.get(category.img_key), viewer_id=user.id)
        payload["image_count"] = counts[category.img_key]["total"]
        payload["missing_ocr"] = counts[category.img_key]["missing_ocr"]
        payload["accent"] = accent_of(category.img_key)
        categories.append(payload)

    # 提交单里已存在、但还没落盘的新类别也一并展示
    known = {item["img_key"] for item in categories}
    for draft in pending_category_drafts(repo):
        if draft["img_key"] in known:
            continue
        categories.append(
            {
                "img_key": draft["img_key"],
                "id": draft["id"],
                "keys": draft["keys"],
                "image_count": 0,
                "missing_ocr": 0,
                "pending_fields": ["id", "keys"],
                "is_pending_new": True,
                "pending_changes": [],
                "accent": accent_of(draft["img_key"]),
                "draft_status": draft["status"],
                "draft_author": draft["author_name"],
            }
        )

    return {"categories": categories}


@router.get("/summary")
def summary(store: Store, repo: Repo, _user: CurrentUser) -> dict:
    """首页概览数据。"""
    open_submissions = load_open_submissions(repo)
    pending = category_pending_changes(open_submissions)
    stats = store.summary()

    return {
        **stats,
        "pending_category_edits": len([key for key in pending if key in store.load_categories()]),
        "new_category_drafts": len(
            [item for item in pending_category_drafts(repo) if item["img_key"] not in store.load_categories()]
        ),
        "submission_counts": repo.count_by_status(SLUG),
        "category_type_count": len(
            [item for item in open_submissions if item.type in (TYPE_CATEGORY, TYPE_CATEGORY_CREATE)]
        ),
    }


@router.get("/{img_key}")
def get_category(img_key: str, store: Store, repo: Repo, user: CurrentUser) -> dict:
    category = store.category_or_404(img_key)
    pending = category_pending_changes(load_open_submissions(repo)).get(img_key)
    payload = effective_category(category, pending, viewer_id=user.id)
    payload["accent"] = accent_of(img_key)
    return {"category": payload}
