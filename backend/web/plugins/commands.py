"""The browser command reference uses the same catalogue and permissions as bot help."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import HEADERS
from ..plugin import WebPlugin


def build_router(web):
    router = APIRouter()

    @router.get("")
    async def commands(request: Request):
        account = await web.account(request)
        context = web.request_context(account)
        visible = await web.services.catalog.visible(context.command_allowed)
        catalog = web.services.catalog
        return JSONResponse(
            {
                "separator": catalog.separator,
                "directories": catalog.directory_index(visible),
                "commands": [spec.to_dict(catalog.separator) for spec in visible],
            },
            headers=HEADERS,
        )

    return router


PLUGIN = WebPlugin("commands", "指令手册", "查看当前账号可使用的机器人指令", build_router)
