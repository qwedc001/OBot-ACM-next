"""Host the EP-style API registry and compiled Vue application on the bot's FastAPI app."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from framework import __version__
from framework.paths import PROJECT_ROOT

from .auth import build_auth_router
from .context import HEADERS, WebContext
from .plugin import discover_plugins


def install_web(app, services):
    @app.middleware("http")
    async def privacy_headers(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith(("/qq/", "/internal/")):
            response.headers.update(HEADERS)
        return response

    web = WebContext(services)
    plugins = discover_plugins()
    app.include_router(build_auth_router(web))
    meta = APIRouter(include_in_schema=False)

    @meta.get("/api/meta")
    async def metadata():
        return JSONResponse({"name": "OBot's Endpoint", "version": __version__}, headers=HEADERS)

    @meta.get("/api/plugins")
    async def registry(request: Request):
        account = await web.account(request)
        return JSONResponse(
            {
                "plugins": [
                    plugin.manifest()
                    for plugin in plugins
                    if account.permits_tool(plugin.slug) and (not plugin.superuser or account.superuser)
                ]
            },
            headers=HEADERS,
        )

    app.include_router(meta)
    for plugin in plugins:
        app.include_router(plugin.build_router(web), prefix="/api/plugins/" + plugin.slug)

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.split("/", 1)[0] in {"api", "internal", "qq", "docs", "redoc", "openapi.json"}:
            raise HTTPException(404, "Not found")
        dist = (services.settings.obot_frontend_dist or PROJECT_ROOT / "frontend/dist").resolve()
        if not await asyncio.to_thread((dist / "index.html").is_file):
            raise HTTPException(503, "服务暂未就绪，请稍后重试。")
        if path.startswith("assets/") or path in {"theme-init.js", "favicon.svg"}:
            target = (dist / path).resolve()
            if not target.is_relative_to(dist) or not await asyncio.to_thread(target.is_file):
                raise HTTPException(404, "Not found")
            return FileResponse(target, headers=HEADERS)
        if Path(path).suffix or any(part in {".", ".."} for part in path.split("/")):
            raise HTTPException(404, "Not found")
        return FileResponse(dist / "index.html", media_type="text/html", headers=HEADERS)
