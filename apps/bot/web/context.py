"""HTTP-only request validation; application services remain framework independent."""

import hmac
import json
from uuid import uuid4

from fastapi import HTTPException

from framework.contracts import RequestContext, Role, SceneKind, SceneRef
from framework.web_url import public_origin

COOKIE = "obot_session"
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


class WebContext:
    def __init__(self, services):
        self.services = services

    def origin(self):
        try:
            return public_origin(self.services.settings.obot_public_base_url)
        except ValueError:
            raise HTTPException(404, "Not found") from None

    async def body(self, request, *, limit=8192):
        if (
            request.headers.get("origin") != self.origin()
            or request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}
            or not request.headers.get("content-type", "").lower().startswith("application/json")
        ):
            raise HTTPException(403, "请求来源不正确，请刷新页面。")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "请求内容过大。")
            chunks.append(chunk)
        try:
            value = json.loads(b"".join(chunks))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError:
            raise HTTPException(400, "请求格式不正确。") from None

    async def account(self, request, *, admin=False, csrf=False):
        self.origin()
        account = await self.services.dashboard.authenticate(request.cookies.get(COOKIE))
        if account is None:
            raise HTTPException(401, "请先使用 QQ 登录。")
        if request.url.path.startswith("/api/plugins/") and not account.permits_tool(
            request.url.path.split("/")[3]
        ):
            raise HTTPException(403, "本次登录仅允许管理发起登录群的榜单和图库。")
        if admin and not account.superuser:
            raise HTTPException(403, "仅机器人超级管理员可以访问。")
        if csrf and not hmac.compare_digest(
            account.csrf.encode(), request.headers.get("x-csrf-token", "").encode()
        ):
            raise HTTPException(403, "会话校验失败，请刷新页面。")
        return account

    @staticmethod
    def request_context(account):
        async def allowed(spec):
            return account.superuser or spec.permission == Role.EVERYONE

        return RequestContext(
            account.actor,
            SceneRef("web", "dashboard", SceneKind.WEB, account.actor.id),
            "http:" + uuid4().hex,
            command_allowed=allowed,
        )
