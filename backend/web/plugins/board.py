"""Vue board UI routes share the existing scoped BoardService."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from framework.board import BoardError
from framework.contracts import AccessDenied
from framework.dashboard import DashboardError

from ..context import HEADERS
from ..plugin import WebPlugin


def build_router(web):
    router = APIRouter()
    services = web.services

    async def domain_call(call):
        try:
            return await call
        except (BoardError, DashboardError) as error:
            raise HTTPException(400, str(error)) from None
        except AccessDenied:
            raise HTTPException(403, "群管理权限未验证或已过期，请在群内重新验证后刷新。") from None
        except TimeoutError:
            raise HTTPException(408, "请求超时，请稍后重试。") from None
        except Exception:
            raise HTTPException(500, "暂时无法处理，请稍后重试。") from None

    @router.post("/bind/inspect")
    async def inspect(request: Request):
        values = await web.body(request)
        result = await domain_call(services.boards.inspect(values.get("token")))
        return JSONResponse(result, headers=HEADERS)

    @router.post("/bind/submit")
    async def submit(request: Request):
        values = await web.body(request)
        result = await domain_call(
            services.boards.submit(
                values.get("token"),
                values.get("host"),
                values.get("username"),
                values.get("password"),
                values.get("options"),
            )
        )
        return JSONResponse(result, headers=HEADERS)

    async def scoped_context(account, selector):
        return await domain_call(services.scene_access.context(account, selector))

    @router.get("/scopes")
    async def scopes(request: Request):
        account = await web.account(request)
        groups = await services.scene_access.scopes(account.actor)
        return JSONResponse(
            {
                "scopes": [
                    {"id": "personal", "label": "个人榜单"},
                    *[{"id": item["id"], "label": item["label"]} for item in groups],
                ],
                "verification_ttl": services.settings.obot_web_group_ttl,
            },
            headers=HEADERS,
        )

    @router.post("/group-check")
    async def group_check(request: Request):
        await web.body(request, limit=2048)
        account = await web.account(request, csrf=True)
        return JSONResponse(await services.scene_access.issue_check(account.actor), headers=HEADERS)

    @router.get("/configs")
    async def configs(request: Request):
        account = await web.account(request)
        context = await scoped_context(account, request.query_params.get("scope"))
        rows = await services.boards.configs(context)
        return JSONResponse(
            {
                "configs": [
                    {
                        "name": row.name,
                        "host": row.host,
                        "show_unrated": row.options.get("show_unrated", True),
                        "options": {
                            key: row.options[key]
                            for key in ("exclude_uid", "exclude_before", "show_unrated")
                            if key in row.options
                        },
                        "push_enabled": bool(row.push_target),
                    }
                    for row in rows
                ]
            },
            headers=HEADERS,
        )

    @router.post("/starred")
    async def starred(request: Request):
        values = await web.body(request)
        account = await web.account(request, csrf=True)
        context = await scoped_context(account, values.get("scope"))
        setting = values.get("enabled")
        if type(setting) is not bool or not isinstance(values.get("name"), str):
            raise HTTPException(400, "请选择榜单配置和打星显示状态。")
        result = await domain_call(services.boards.starred(context, setting, values["name"]))
        return JSONResponse({"message": result}, headers=HEADERS)

    @router.post("/settings")
    async def settings(request: Request):
        values = await web.body(request)
        account = await web.account(request, csrf=True)
        context = await scoped_context(account, values.get("scope"))
        if not isinstance(values.get("name"), str) or not isinstance(values.get("options"), dict):
            raise HTTPException(400, "请选择榜单配置并填写显示设置。")
        if set(values) - {"name", "options", "scope"} or set(values["options"]) - {
            "exclude_uid",
            "exclude_before",
            "show_unrated",
        }:
            raise HTTPException(400, "这里只能修改榜单显示设置。")
        result = await domain_call(services.boards.configure(context, values["options"], values["name"]))
        return JSONResponse({"message": result}, headers=HEADERS)

    @router.post("/render")
    async def render(request: Request):
        values = await web.body(request)
        account = await web.account(request, csrf=True)
        context = await scoped_context(account, values.get("scope"))
        if values.get("mode") not in {"today", "yesterday"} or not isinstance(values.get("name"), str):
            raise HTTPException(400, "请选择榜单配置和日期。")
        artifact = await domain_call(
            services.boards.image(context, "now" if values["mode"] == "today" else "full", values["name"])
        )
        data = await domain_call(services.clients.artifacts.read(account.actor, artifact.id))
        await scoped_context(account, values.get("scope"))
        return Response(data, media_type="image/png", headers=HEADERS)

    return router


PLUGIN = WebPlugin("board", "训练榜单", "个人与群榜单、显示设置与一次性绑定", build_router)
