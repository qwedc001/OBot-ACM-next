from web.content.contestlist import build_router as content_router
from web.plugin import WebPlugin


def build_router(web):
    return content_router()


PLUGIN = WebPlugin("contestlist", "算法竞赛列表", "维护比赛信息，提交修改并审核应用。", build_router)
