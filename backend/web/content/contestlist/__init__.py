"""算法竞赛列表 的路由集合（挂载在 `/api/plugins/contestlist` 下）。

新增资源时照这个结构在 `api/` 下加文件，并把它加进 MODULES。
"""

from fastapi import APIRouter

from . import admin, items

MODULES = (items, admin)


def build_router() -> APIRouter:
    """把本插件所有子路由合成一个，由核心统一挂到 api_prefix 下。"""
    router = APIRouter()
    for module in MODULES:
        router.include_router(module.router)
    return router
