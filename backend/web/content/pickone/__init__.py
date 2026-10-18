"""PickOne 的路由集合（挂载在 `/api/plugins/pickone` 下）。

新增一个工具时照抄这个结构：`api/` 下按资源分文件，`plugin.py` 把它们 include
进同一个 router。
"""

from fastapi import APIRouter

from . import admin, audit, categories, images, submissions

# 顺序无实际影响，但保持「用户侧 → 管理侧」的阅读顺序。
MODULES = (categories, images, submissions, admin, audit)


def build_router() -> APIRouter:
    """把本插件所有子路由合成一个，由核心统一挂到 api_prefix 下。"""
    router = APIRouter()
    for module in MODULES:
        router.include_router(module.router)
    return router
