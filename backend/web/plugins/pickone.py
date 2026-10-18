from web.content.pickone import build_router as content_router
from web.content.pickone.groups import build_router as group_router
from web.plugin import WebPlugin


def build_router(web):
    router = content_router()
    router.include_router(group_router(web))
    return router


PLUGIN = WebPlugin("pickone", "PickOne 表情包", "维护图片描述、类别别名与点赞评论。", build_router)
