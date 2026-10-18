"""Adapt QQ sessions to EP handlers; never introduce an independent account system."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from framework.curation.repository import Repository
from framework.services.curation import CurationService


@dataclass(frozen=True)
class ContentUser:
    id: str
    is_admin: bool

    @property
    def username(self):
        return self.id


async def current_user(request: Request):
    web = request.app.state.content_web
    mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
    account = await web.account(request, csrf=mutation)
    if account.group_scope:
        raise HTTPException(403, "本次登录仅允许管理本群内容，请进入群图库页面。")
    if mutation:
        if request.headers.get("origin") != web.origin() or request.headers.get(
            "sec-fetch-site", "same-origin"
        ) not in {"same-origin", "none"}:
            raise HTTPException(403, "请求来源不正确，请刷新页面。")
        if await request.body():
            await web.body(request, limit=128 * 1024)
    return ContentUser(account.actor.id, account.superuser)


CurrentUser = Annotated[ContentUser, Depends(current_user)]


async def admin_user(user: CurrentUser):
    if not user.is_admin:
        raise HTTPException(403, "仅机器人超级管理员可以访问。")
    return user


AdminUser = Annotated[ContentUser, Depends(admin_user)]


async def content(request: Request, _user: CurrentUser):
    service = request.app.state.content_web.services.curation
    await service.start()
    return service


Content = Annotated[CurationService, Depends(content)]


async def repository(service: Content):
    async with service.transaction() as repo:
        yield repo


Repo = Annotated[Repository, Depends(repository)]
