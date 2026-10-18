"""PickOne 的请求模型。

共享审核请求模型位于 `web/content/review.py`；
「提交一张图的哪些字段」是这个工具自己的事，所以放这里。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .store import (
    MAX_ALIASES,
    MAX_CATEGORY_ID_LENGTH,
    MAX_COMMENT_LENGTH,
    MAX_COMMENTS_PER_IMAGE,
    MAX_LIKES_PER_REQUEST,
    MAX_OCR_TEXT_LENGTH,
)


class OcrSubmitRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    ocr_text: str = Field(max_length=MAX_OCR_TEXT_LENGTH)
    note: str = Field(default="", max_length=200)


class LikesSubmitRequest(BaseModel):
    """likes_delta 是这次「加多少赞」，不是这张图的目标赞数。

    限的是单次能加的数量（对应 Bot 一条 /点赞 加 1 个），磁盘上的总数不封顶，
    所以本来就有几十个赞的图照样能继续加。
    """

    name: str = Field(min_length=1, max_length=64)
    likes_delta: int = Field(ge=1, le=MAX_LIKES_PER_REQUEST)
    note: str = Field(default="", max_length=200)


class ImageBatchRequest(BaseModel):
    """一张图片的多个字段一起提交（编辑表单）。

    只处理明确给出的字段；没传的字段不动，所以「清空 OCR」用空字符串表示即可。
    """

    name: str = Field(min_length=1, max_length=64)
    ocr_text: str | None = Field(default=None, max_length=MAX_OCR_TEXT_LENGTH)
    likes_delta: int | None = Field(default=None, ge=1, le=MAX_LIKES_PER_REQUEST)
    comments: list[str] | None = Field(default=None, max_length=MAX_COMMENTS_PER_IMAGE)
    note: str = Field(default="", max_length=200)


class CommentsSubmitRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    comments: list[str] = Field(max_length=MAX_COMMENTS_PER_IMAGE)
    note: str = Field(default="", max_length=200)

    def cleaned(self) -> list[str]:
        out: list[str] = []
        for comment in self.comments:
            text = " ".join(comment.split())
            if not text:
                continue
            if len(text) > MAX_COMMENT_LENGTH:
                raise ValueError(f"单条评论过长（上限 {MAX_COMMENT_LENGTH} 字）")
            out.append(text)
        return out


class CategorySubmitRequest(BaseModel):
    category_id: str = Field(min_length=1, max_length=MAX_CATEGORY_ID_LENGTH)
    keys: list[str] = Field(min_length=1, max_length=MAX_ALIASES)
    note: str = Field(default="", max_length=200)


class CategoryCreateRequest(BaseModel):
    category_id: str = Field(default="", max_length=MAX_CATEGORY_ID_LENGTH)
    keys: list[str] = Field(default_factory=list, max_length=MAX_ALIASES)
    note: str = Field(default="", max_length=200)


class ApplyRequest(BaseModel):
    dry_run: bool = False


class ConflictResolveRequest(BaseModel):
    """冲突裁定：保留提交的新值（写入），或丢弃提交（保持磁盘现状）。"""

    keep_new: bool
