"""QQ-issued, one-use web login and revocable sessions over the existing user table."""

import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import unquote

from sqlalchemy import delete, select

from ..contracts import Actor, ExternalIdentity, SceneKind
from ..dashboard import DashboardError, WebAccount
from ..models import Identity, User, WebLoginToken, WebSceneGrant, WebSession
from ..web_url import public_origin
from .base import Service
from .scene_access import MANAGE, parse_scene, scene_scope


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


def csrf_for(value: str) -> str:
    return hmac.new(value.encode(), b"obot.web.csrf.v1", hashlib.sha256).hexdigest()


class DashboardService(Service):
    dependencies = ("identities", "scene_access")

    @classmethod
    def build(cls, container):
        return cls(
            container.session_factory, container.settings, container.identities, container.scene_access
        )

    def __init__(self, sessions, settings, identities, scene_access):
        self.sessions, self.settings, self.identities = sessions, settings, identities
        self.scene_access = scene_access

    async def _group_scope(self, context):
        if context.scene.kind == SceneKind.GROUP:
            if not (
                context.command_allowed
                and await context.command_allowed(MANAGE)
                and await self.scene_access.allowed(context.actor, context.scene)
            ):
                raise DashboardError("请由群主、群管理员或机器人超级管理员在群内执行。")
            return scene_scope(context.scene)
        if context.scene.kind not in {SceneKind.C2C, SceneKind.DIRECT}:
            raise DashboardError("请在私聊或群聊中使用 /dashboard。")
        return None

    async def private_recipient(self, context, kinds):
        """Use only canonical or explicitly verified routing; never guess QQ IDs."""
        scope = await self._group_scope(context)
        async with self.sessions() as session:
            owners = [context.actor.id]
            delegated = list(
                await session.scalars(
                    select(WebSceneGrant.user_id).where(
                        WebSceneGrant.scope == scope, WebSceneGrant.identity_key == context.actor.identity_key
                    )
                )
            )
            for ids in (owners, delegated):
                if not ids:
                    continue
                keys = await session.scalars(select(Identity.key).where(Identity.user_id.in_(ids)))
                recipients = set()
                for key in keys:
                    parts = [unquote(part) for part in key.split(":")]
                    if (
                        len(parts) == 4
                        and parts[:2] == [context.scene.provider, context.scene.app_id]
                        and parts[2] in kinds
                    ):
                        recipients.add(ExternalIdentity(*parts))
                if len(recipients) == 1:
                    return recipients.pop()
                if recipients:
                    break
        raise DashboardError(
            "尚未建立唯一的私聊身份关联。请先私聊机器人发送 /dashboard，在网页中完成一次本群管理身份验证，再使用群内入口。"
        )

    async def revoke_link(self, link):
        raw = link.rsplit("#", 1)[-1]
        if valid(raw):
            async with self.sessions() as session, session.begin():
                await session.execute(delete(WebLoginToken).where(WebLoginToken.digest == digest(raw)))

    async def issue(self, context):
        group_scope = await self._group_scope(context)
        try:
            base = public_origin(self.settings.obot_public_base_url)
        except ValueError:
            raise DashboardError("网页入口尚未配置，请联系机器人管理员。") from None
        raw, now = secrets.token_urlsafe(32), time.time()
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(WebLoginToken).where(
                    (WebLoginToken.user_id == context.actor.id) | (WebLoginToken.expires_at <= now)
                )
            )
            await session.execute(delete(WebSession).where(WebSession.expires_at <= now))
            session.add(
                WebLoginToken(
                    digest=digest(raw),
                    user_id=context.actor.id,
                    identity_key=context.actor.identity_key,
                    group_scope=group_scope,
                    expires_at=now + self.settings.obot_web_login_ttl,
                )
            )
        return base + "/login#" + raw

    async def exchange(self, raw):
        error = "登录链接无效、已使用或已过期，请私聊机器人重新发送 /dashboard。"
        if not valid(raw):
            raise DashboardError(error)
        now, session_token = time.time(), secrets.token_urlsafe(32)
        async with self.sessions() as session, session.begin():
            row = (
                await session.execute(
                    delete(WebLoginToken)
                    .where(WebLoginToken.digest == digest(raw), WebLoginToken.expires_at > now)
                    .returning(WebLoginToken.user_id, WebLoginToken.identity_key, WebLoginToken.group_scope)
                )
            ).first()
            if row is None or await session.get(User, row.user_id) is None:
                raise DashboardError(error)
            # Keep at most eight live browser sessions for this account.
            stale = list(
                await session.scalars(
                    select(WebSession.digest)
                    .where(WebSession.user_id == row.user_id)
                    .order_by(WebSession.created_at.desc())
                    .offset(7)
                )
            )
            if stale:
                await session.execute(delete(WebSession).where(WebSession.digest.in_(stale)))
            session.add(
                WebSession(
                    digest=digest(session_token),
                    user_id=row.user_id,
                    identity_key=row.identity_key,
                    group_scope=row.group_scope,
                    created_at=now,
                    expires_at=now + self.settings.obot_web_session_ttl,
                )
            )
        account = await self.authenticate(session_token)
        if account is None:
            raise DashboardError(error)
        return session_token, account

    async def authenticate(self, raw):
        if not valid(raw):
            return None
        async with self.sessions() as session:
            row = await session.get(WebSession, digest(raw))
            if row is None or row.expires_at <= time.time() or await session.get(User, row.user_id) is None:
                return None
            actor = Actor(row.user_id, row.identity_key)
            group_scope = row.group_scope
        if group_scope:
            scene = parse_scene(group_scope)
            if scene is None or not await self.scene_access.allowed(actor, scene):
                return None
        return WebAccount(
            actor, not group_scope and await self.identities.is_superuser(actor), csrf_for(raw), group_scope
        )

    async def logout(self, raw):
        if valid(raw):
            async with self.sessions() as session, session.begin():
                await session.execute(delete(WebSession).where(WebSession.digest == digest(raw)))
