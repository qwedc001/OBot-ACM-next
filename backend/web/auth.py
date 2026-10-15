"""Same-origin QQ capability exchange and HttpOnly cookie lifecycle."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from framework.dashboard import DashboardError

from .context import COOKIE, HEADERS


def build_auth_router(web):
    router = APIRouter(prefix="/api/auth", include_in_schema=False)

    @router.post("/exchange")
    async def exchange(request: Request):
        values = await web.body(request, limit=2048)
        try:
            raw, account = await web.services.dashboard.exchange(values.get("token"))
        except DashboardError as error:
            raise HTTPException(401, str(error)) from None
        # Rotating login replaces this browser's previous session.
        await web.services.dashboard.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"user": account.profile(), "csrf_token": account.csrf}, headers=HEADERS)
        response.set_cookie(
            COOKIE,
            raw,
            httponly=True,
            secure=web.origin().startswith("https://"),
            samesite="strict",
            max_age=web.services.settings.obot_web_session_ttl,
            path="/",
        )
        return response

    @router.get("/me")
    async def me(request: Request):
        account = await web.account(request)
        return JSONResponse({"user": account.profile(), "csrf_token": account.csrf}, headers=HEADERS)

    @router.post("/logout")
    async def logout(request: Request):
        await web.body(request, limit=2048)
        await web.account(request, csrf=True)
        await web.services.dashboard.logout(request.cookies.get(COOKIE))
        response = Response(status_code=204, headers=HEADERS)
        response.delete_cookie(
            COOKIE, path="/", httponly=True, secure=web.origin().startswith("https://"), samesite="strict"
        )
        return response

    return router
