"""Bridge short-lived native group authority to an authenticated web account."""

import hashlib
import re
import secrets
import time
from urllib.parse import unquote

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.dialects.sqlite import insert

from ..catalog import CommandSpec
from ..contracts import AccessDenied, Actor, RequestContext, Role, SceneKind, SceneRef
from ..dashboard import DashboardError
from ..models import BoardConfig, Identity, SceneAuthority, WebGroupCheck, WebSceneGrant
from .base import Service

MANAGE = CommandSpec("dashboard.group", "dashboard", "group", "验证群管理权限", permission=Role.GROUP_ADMIN)


def scene_scope(scene):
    return "scene:" + scene.key


def scope_id(scope):
    return hashlib.sha256(scope.encode()).hexdigest()[:32]


def parse_scene(scope):
    parts = [unquote(part) for part in scope.removeprefix("scene:").split(":")]
    if not scope.startswith("scene:") or len(parts) != 4 or parts[2] != SceneKind.GROUP:
        return None
    return SceneRef(parts[0], parts[1], SceneKind.GROUP, parts[3])


class SceneAccessService(Service):
    dependencies = ("identities",)

    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.settings, container.identities)

    def __init__(self, sessions, settings, identities):
        self.sessions, self.settings, self.identities = sessions, settings, identities

    async def observe(self, actor, scene, is_admin):
        if scene.kind != SceneKind.GROUP:
            return
        async with self.sessions() as session, session.begin():
            await session.execute(
                insert(SceneAuthority)
                .values(
                    scope=scene_scope(scene),
                    identity_key=actor.identity_key,
                    is_admin=bool(is_admin),
                    verified_at=time.time(),
                )
                .on_conflict_do_update(
                    index_elements=[SceneAuthority.scope, SceneAuthority.identity_key],
                    set_={"is_admin": bool(is_admin), "verified_at": time.time()},
                )
            )

    async def _authorities(self, actor, scope=None):
        delegated = exists().where(
            WebSceneGrant.user_id == actor.id,
            WebSceneGrant.scope == SceneAuthority.scope,
            WebSceneGrant.identity_key == SceneAuthority.identity_key,
        )
        async with self.sessions() as session:
            query = (
                select(SceneAuthority, Identity.user_id)
                .join(Identity, Identity.key == SceneAuthority.identity_key)
                .where(
                    SceneAuthority.verified_at > time.time() - self.settings.obot_web_group_ttl,
                    or_(Identity.user_id == actor.id, delegated),
                )
            )
            if scope is not None:
                query = query.where(SceneAuthority.scope == scope)
            return (await session.execute(query)).all()

    async def allowed(self, actor, scene):
        if await self.identities.is_superuser(actor):
            return True
        rows = await self._authorities(actor, scene_scope(scene))
        for authority, user_id in rows:
            if authority.is_admin or await self.identities.is_superuser(
                Actor(user_id, authority.identity_key)
            ):
                return True
        return False

    async def scopes(self, actor):
        superuser = await self.identities.is_superuser(actor)
        allowed = set()
        verified = {}
        if not superuser:
            for authority, user_id in await self._authorities(actor):
                if not authority.is_admin and authority.identity_key not in verified:
                    verified[authority.identity_key] = await self.identities.is_superuser(
                        Actor(user_id, authority.identity_key)
                    )
                if authority.is_admin or verified.get(authority.identity_key):
                    allowed.add(authority.scope)
        async with self.sessions() as session:
            query = select(BoardConfig.scope, BoardConfig.name).where(BoardConfig.scope.startswith("scene:"))
            if not superuser:
                query = query.where(BoardConfig.scope.in_(allowed))
            rows = (await session.execute(query.order_by(BoardConfig.scope, BoardConfig.name))).all()
        names = {}
        for scope, name in rows:
            names.setdefault(scope, []).append(name)
        result = []
        for scope, labels in names.items():
            scene = parse_scene(scope)
            if scene:
                result.append(
                    {
                        "id": scope_id(scope),
                        "label": "群聊 " + scope_id(scope)[:6] + " · " + "、".join(labels[:3]),
                        "scene": scene,
                    }
                )
        return result

    async def context(self, account, selector=None):
        if selector in (None, "personal"):

            async def personal_allowed(spec):
                return await self.identities.is_superuser(account.actor) or spec.permission == Role.EVERYONE

            return RequestContext(
                account.actor,
                SceneRef("web", "dashboard", SceneKind.WEB, account.actor.id),
                secrets.token_hex(16),
                command_allowed=personal_allowed,
            )
        if not isinstance(selector, str):
            raise AccessDenied("Group access denied")
        for group in await self.scopes(account.actor):
            if group["id"] == selector:
                scene = group["scene"]

                async def allowed(spec, scene=scene):
                    return (
                        await self.allowed(account.actor, scene)
                        and spec.permission in {Role.EVERYONE, Role.GROUP_ADMIN}
                    ) or await self.identities.is_superuser(account.actor)

                return RequestContext(account.actor, scene, secrets.token_hex(16), command_allowed=allowed)
        raise AccessDenied("Group access expired or denied")

    async def issue_check(self, actor):
        raw, now = secrets.token_urlsafe(18), time.time()
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(WebGroupCheck).where(
                    or_(WebGroupCheck.user_id == actor.id, WebGroupCheck.expires_at <= now)
                )
            )
            session.add(
                WebGroupCheck(
                    digest=hashlib.sha256(raw.encode()).hexdigest(), user_id=actor.id, expires_at=now + 300
                )
            )
        return {"command": "/dashboard-verify " + raw, "expires_in": 300}

    async def verify(self, context, raw):
        if context.scene.kind != SceneKind.GROUP or not (
            context.command_allowed and await context.command_allowed(MANAGE)
        ):
            raise AccessDenied("Group administrator required")
        if not await self.allowed(context.actor, context.scene):
            raise AccessDenied("Native group role not verified")
        if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24}", raw):
            raise DashboardError("验证指令无效、已使用或过期，请在网页重新获取。")
        async with self.sessions() as session, session.begin():
            user_id = (
                await session.execute(
                    delete(WebGroupCheck)
                    .where(
                        WebGroupCheck.digest == hashlib.sha256(raw.encode()).hexdigest(),
                        WebGroupCheck.expires_at > time.time(),
                    )
                    .returning(WebGroupCheck.user_id)
                )
            ).scalar_one_or_none()
            if user_id is None:
                raise DashboardError("验证指令无效、已使用或过期，请在网页重新获取。")
            await session.execute(
                insert(WebSceneGrant)
                .values(
                    user_id=user_id,
                    scope=scene_scope(context.scene),
                    identity_key=context.actor.identity_key,
                )
                .on_conflict_do_update(
                    index_elements=[WebSceneGrant.user_id, WebSceneGrant.scope],
                    set_={"identity_key": context.actor.identity_key},
                )
            )
        return "群管理身份已验证，请返回网页刷新群榜单。授权随群管理权限更新并定期过期。"
