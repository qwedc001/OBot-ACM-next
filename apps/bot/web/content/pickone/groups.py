"""Group management entry; authenticate scope before touching any media or files.

This does not relax CurrentUser/AdminUser on the public EP routes. Scoped group
sessions can only use this router, and each request rechecks native authority.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from framework.contracts import AccessDenied, SceneKind
from framework.curation.pickone import chat, group
from framework.curation.pickone.schemas import CategorySubmitRequest, ImageBatchRequest
from framework.services.scene_access import scope_id


def build_router(web):
    router = APIRouter(prefix="/groups")
    services = web.services

    async def context(request: Request):
        mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
        account = await web.account(request, csrf=mutation)
        if mutation:
            request.state.pickone_body = await web.body(request, limit=16 * 1024)
        try:
            result = await services.scene_access.context(
                account, request.query_params.get("scope"), resource="pickone"
            )
            if result.scene.kind != SceneKind.GROUP or not await services.pickone.manages_group(result):
                raise AccessDenied()
            return result
        except AccessDenied:
            raise HTTPException(403, "请先验证本群管理身份；不能访问其他群的图库。") from None

    scoped = Depends(context)

    @router.get("/scopes")
    async def scopes(request: Request):
        account = await web.account(request)
        groups = (
            [{"id": scope_id(account.group_scope), "label": "本群图库"}]
            if account.group_scope
            else await services.scene_access.scopes(account.actor, resource="pickone")
        )
        return {"scopes": [{"id": item["id"], "label": item["label"]} for item in groups]}

    @router.get("/categories")
    async def categories(ctx=scoped):
        return {"categories": await services.pickone.manage(ctx, chat.categories)}

    @router.post("/categories/{key}")
    async def create(key: str, values: CategorySubmitRequest, ctx=scoped):
        await services.pickone.create_category(ctx, key, values.category_id, values.keys)
        return {"ok": True}

    @router.put("/categories/{key}")
    async def edit_category(key: str, values: CategorySubmitRequest, ctx=scoped):
        await services.pickone.manage(ctx, group.update_category, key, values, ctx.actor.id)
        return {"ok": True}

    @router.get("/images/{key}")
    async def images(key: str, page: int = Query(1, ge=1), ctx=scoped):
        return await services.pickone.manage(ctx, group.images, key, page)

    @router.put("/images/{key}")
    async def edit_image(key: str, values: ImageBatchRequest, ctx=scoped):
        await services.pickone.manage(ctx, group.update_image, key, values, ctx.actor.id)
        return {"ok": True}

    @router.delete("/images/{key}/{name}")
    async def remove(key: str, name: str, ctx=scoped):
        await services.pickone.manage(ctx, group.delete_image, key, name, ctx.actor.id)
        return {"ok": True}

    @router.get("/media/{key}/{name}")
    async def media(key: str, name: str, pending: bool = False, ctx=scoped):
        data = await services.pickone.manage(ctx, group.media, key, name, pending)
        return Response(data, media_type="image/webp")

    @router.get("/audit")
    async def audit(page: int = Query(1, ge=1), ctx=scoped):
        return await services.pickone.manage(ctx, group.audit, page)

    @router.post("/audit/{key}/{name}")
    async def review(key: str, name: str, request: Request, ctx=scoped):
        values = request.state.pickone_body
        if type(values.get("approve")) is not bool:
            raise HTTPException(400, "请选择通过或拒绝。")
        await services.pickone.manage(ctx, group.review, key, name, values["approve"], ctx.actor.id)
        await services.pickone.enqueue_ocr()
        return {"ok": True}

    return router
