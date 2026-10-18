from web.content.pickone import build_router as content_router
from web.plugin import WebPlugin


def build_router(web):
    return content_router()


PLUGIN = WebPlugin("pickone", "PickOne 表情包", "维护图片描述、类别别名与点赞评论。", build_router)
