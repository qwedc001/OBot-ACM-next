"""QQ-issued, one-use web login and revocable sessions over the existing user table."""

import hashlib
import hmac
import re
import secrets
import time

from sqlalchemy import delete, select

from ..contracts import Actor, SceneKind
from ..dashboard import DashboardError, WebAccount
from ..models import User, WebLoginToken, WebSession
from ..web_url import public_origin
from .base import Service


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


def csrf_for(value: str) -> str:
    return hmac.new(value.encode(), b"obot.web.csrf.v1", hashlib.sha256).hexdigest()


class DashboardService(Service):
    dependencies = ("identities",)

    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.settings, container.identities)

    def __init__(self, sessions, settings, identities):
        self.sessions, self.settings, self.identities = sessions, settings, identities

    async def issue(self, context):
        if context.scene.kind not in {SceneKind.C2C, SceneKind.DIRECT}:
            raise DashboardError("请私聊机器人发送 /dashboard 获取个人登录链接。")
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
                    .returning(WebLoginToken.user_id, WebLoginToken.identity_key)
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
        return WebAccount(actor, await self.identities.is_superuser(actor), csrf_for(raw))

    async def logout(self, raw):
        if valid(raw):
            async with self.sessions() as session, session.begin():
                await session.execute(delete(WebSession).where(WebSession.digest == digest(raw)))
