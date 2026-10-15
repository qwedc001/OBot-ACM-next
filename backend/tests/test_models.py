"""Only application model semantics; transactions are SQLAlchemy's responsibility."""

import asyncio

import pytest
from sqlalchemy import select

from framework.contracts import ExternalIdentity
from framework.models import Identity, User
from framework.services.identities import IdentityService


async def test_concurrent_first_messages_create_one_canonical_user(database):
    service = IdentityService(database)
    identity = ExternalIdentity("qq", "bot", "c2c_user", "same")
    actors = await asyncio.gather(*(service.resolve(identity) for _ in range(12)))
    assert len({actor.id for actor in actors}) == 1
    async with database() as session:
        ids = (await session.scalars(select(Identity).where(Identity.key == identity.key))).all()
        assert len(ids) == 1
        users = (await session.scalars(select(User))).all()
        assert len(users) == 3  # fixture's two users plus this account, no orphans.


async def test_failed_account_transaction_does_not_leave_partial_data(database):
    with pytest.raises(RuntimeError):
        async with database() as session, session.begin():
            session.add(User(id="rolled-back"))
            await session.flush()
            raise RuntimeError("abort")
    async with database() as session:
        assert await session.get(User, "rolled-back") is None
