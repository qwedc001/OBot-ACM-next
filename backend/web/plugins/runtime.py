"""Administrator-only real process diagnostics, without message bodies or identities."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import HEADERS
from ..plugin import WebPlugin


def build_router(web):
    router = APIRouter()

    @router.get("")
    async def overview(request: Request):
        await web.account(request, admin=True)
        snapshot = web.services.snapshot()
        # Correlation IDs are useful to local operators but need not leave the bot process.
        telemetry = {
            **snapshot["telemetry"],
            "recent_runs": [
                {key: value for key, value in item.items() if key != "correlation_id"}
                for item in snapshot["telemetry"]["recent_runs"]
            ],
        }
        return JSONResponse({**snapshot, "telemetry": telemetry}, headers=HEADERS)

    return router


PLUGIN = WebPlugin("runtime", "运行状态", "连接、队列与最近的指令耗时", build_router, superuser=True)
