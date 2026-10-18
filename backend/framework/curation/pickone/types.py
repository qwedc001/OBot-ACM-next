"""PickOne 的提交类型。

框架只存 `type` 字符串，不关心里面有什么；这些常量、以及「哪个类型叫什么名字」，
都是本插件自己的词汇表，通过 `register_submission_types()` 告知框架。
"""

from __future__ import annotations

TYPE_OCR_TEXT = "ocr_text"
TYPE_LIKES = "likes"
TYPE_COMMENTS = "comments"
TYPE_CATEGORY = "category"
TYPE_CATEGORY_CREATE = "category_create"

SUBMISSION_TYPES = (
    TYPE_OCR_TEXT,
    TYPE_LIKES,
    TYPE_COMMENTS,
    TYPE_CATEGORY,
    TYPE_CATEGORY_CREATE,
)

TYPE_LABELS = {
    TYPE_OCR_TEXT: "图片描述",
    TYPE_LIKES: "点赞",
    TYPE_COMMENTS: "评论",
    TYPE_CATEGORY: "类别信息",
    TYPE_CATEGORY_CREATE: "新增类别",
}

# 落在 parser.json 里的提交类型（相对的是写 config.json 的类别类提交）
IMAGE_TYPES = (TYPE_OCR_TEXT, TYPE_LIKES, TYPE_COMMENTS)

# 写在 config.json 里的提交类型
CATEGORY_TYPES = (TYPE_CATEGORY, TYPE_CATEGORY_CREATE)
