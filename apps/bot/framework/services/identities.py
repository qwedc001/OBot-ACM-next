"""Canonical platform identities and the persistent bot administrator flag."""

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from framework.contracts import Actor, ExternalIdentity
from framework.models import Identity, User

from .base import Service


class IdentityService(Service):
    dependencies = ()

    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.settings.obot_superusers)

    def __init__(self, session_factory: Callable[[], AsyncSession], superusers: frozenset[str] = frozenset()):
        self.session_factory = session_factory
        self.superusers = superusers

    async def lookup(self, identity: ExternalIdentity) -> Actor | None:
        async with self.session_factory() as session:
            known = await session.get(Identity, identity.key)
            return Actor(known.user_id, identity.key) if known else None

    async def resolve(self, identity: ExternalIdentity) -> Actor:
        async with self.session_factory() as session:
            known = await session.get(Identity, identity.key)
            if known:
                return Actor(known.user_id, identity.key)
        async with self.session_factory() as session, session.begin():
            user_id = uuid4().hex
            session.add(User(id=user_id))
            await session.flush()
            result = await session.execute(
                insert(Identity)
                .values(key=identity.key, user_id=user_id)
                .on_conflict_do_nothing(index_elements=[Identity.key])
                .returning(Identity.user_id)
            )
            actual = result.scalar_one_or_none()
            if actual is None:
                await session.execute(User.__table__.delete().where(User.id == user_id))
                actual = (await session.get(Identity, identity.key)).user_id
            return Actor(actual, identity.key)

    async def is_superuser(self, actor: Actor) -> bool:
        if actor.id in self.superusers or actor.identity_key in self.superusers:
            return True
        async with self.session_factory() as session:
            return bool(await session.scalar(select(User.superuser).where(User.id == actor.id)))

    async def link(self, identity_key: str, target_user_id: str) -> Actor:
        """Trusted local operator action; bot and web handlers never expose this."""
        async with self.session_factory() as session, session.begin():
            if await session.get(User, target_user_id) is None:
                raise ValueError("Target user does not exist")
            result = await session.execute(
                update(Identity).where(Identity.key == identity_key).values(user_id=target_user_id)
            )
            if result.rowcount != 1:
                raise ValueError("Identity has not been observed")
        return Actor(target_user_id, identity_key)
