"""Public, same-origin one-use binding form. Tokens never appear in URL paths."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ..board import BoardError

HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


def build_board_router(services):
    router = APIRouter(prefix="/board", include_in_schema=False)
    assets = Path(__file__).resolve().parents[1] / "views"

    def enabled():
        if not services.settings.obot_public_base_url:
            raise HTTPException(404, "Not found")

    @router.get("/bind")
    async def page():
        enabled()
        return FileResponse(assets / "board_bind.html", media_type="text/html", headers=HEADERS)

    @router.get("/assets/{name}")
    async def asset(name: str):
        enabled()
        if name not in {"board_bind.js", "board_bind.css"}:
            raise HTTPException(404, "Not found")
        return FileResponse(assets / name, headers=HEADERS)

    async def body(request):
        enabled()
        if request.headers.get("origin") != services.boards.base_url() or not request.headers.get(
            "content-type", ""
        ).lower().startswith("application/json"):
            raise HTTPException(403, "Forbidden")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8192:
                raise HTTPException(413, "Request too large")
            chunks.append(chunk)
        try:
            value = json.loads(b"".join(chunks))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError:
            raise HTTPException(400, "Invalid request") from None

    @router.post("/api/bind/{action}")
    async def bind(action: str, request: Request):
        if action not in {"inspect", "submit"}:
            raise HTTPException(404, "Not found")
        values = await body(request)
        try:
            if action == "inspect":
                result = await services.boards.inspect(values.get("token"))
            else:
                result = await services.boards.submit(
                    values.get("token"),
                    values.get("host"),
                    values.get("username"),
                    values.get("password"),
                    values.get("options"),
                )
            return JSONResponse(result, headers=HEADERS)
        except BoardError as error:
            return JSONResponse({"error": str(error)}, status_code=400, headers=HEADERS)
        except TimeoutError:
            return JSONResponse({"error": "验证超时，请稍后重试。"}, status_code=400, headers=HEADERS)
        except Exception:
            # Never echo inputs or network/cryptography exception details.
            return JSONResponse({"error": "暂时无法保存配置，请稍后重试。"}, status_code=500, headers=HEADERS)

    return router
