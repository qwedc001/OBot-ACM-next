"""Vue board UI routes share the existing scoped BoardService."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from framework.board import BoardError
from framework.contracts import AccessDenied

from ..context import HEADERS
from ..plugin import WebPlugin


def build_router(web):
    router = APIRouter()
    services = web.services

    async def domain_call(call):
        try:
            return await call
        except BoardError as error:
            raise HTTPException(400, str(error)) from None
        except AccessDenied:
            raise HTTPException(403, "没有管理此配置的权限。") from None
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

    @router.get("/configs")
    async def configs(request: Request):
        account = await web.account(request)
        rows = await services.boards.configs(web.request_context(account))
        return JSONResponse(
            {
                "configs": [
                    {
                        "name": row.name,
                        "host": row.host,
                        "show_unrated": row.options.get("show_unrated", True),
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
        setting = values.get("enabled")
        if type(setting) is not bool or not isinstance(values.get("name"), str):
            raise HTTPException(400, "请选择榜单配置和打星显示状态。")
        result = await domain_call(
            services.boards.starred(web.request_context(account), setting, values["name"])
        )
        return JSONResponse({"message": result}, headers=HEADERS)

    @router.post("/render")
    async def render(request: Request):
        values = await web.body(request)
        account = await web.account(request, csrf=True)
        if values.get("mode") not in {"today", "yesterday"} or not isinstance(values.get("name"), str):
            raise HTTPException(400, "请选择榜单配置和日期。")
        artifact = await domain_call(
            services.boards.image(
                web.request_context(account), "now" if values["mode"] == "today" else "full", values["name"]
            )
        )
        data = await domain_call(services.clients.artifacts.read(account.actor, artifact.id))
        return Response(data, media_type="image/png", headers=HEADERS)

    return router


PLUGIN = WebPlugin("board", "训练榜单", "个人榜单、打星显示与一次性绑定", build_router)
