from web.content.review import router
from web.plugin import WebPlugin


def build_router(web):
    return router


PLUGIN = WebPlugin("curation", "内容操作日志", "查看内容提交、审核与应用记录。", build_router, superuser=True)
