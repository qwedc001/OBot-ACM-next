import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from framework.clients.codeforces import CodeforcesClient
from framework.contracts import Actor, RequestContext, SceneKind, SceneRef
from framework.models import CFBinding, CFBindingLog, CFBindingPending, User
from framework.services.cf_binding import CFBindingService

TEST_USER_ID = "test-user-cf-1"
OTHER_USER_ID = "test-user-cf-2"


def make_context(user_id: str) -> RequestContext:
    actor = Actor(user_id, f"qq:bot:c2c_user:{user_id}")
    scene = SceneRef("qq", "bot", SceneKind.C2C, user_id)
    return RequestContext(actor=actor, scene=scene, event_id="ev-1")


@pytest.fixture
def mock_cf_client():
    client = AsyncMock(spec=CodeforcesClient)
    client.check_user_exists = AsyncMock(return_value=True)
    client.validate_submission_ce = AsyncMock(return_value=True)
    return client


@pytest.fixture
async def cf_binding_service(database, mock_cf_client):
    async with database() as session, session.begin():
        session.add_all([User(id=TEST_USER_ID), User(id=OTHER_USER_ID)])
    return CFBindingService(database, mock_cf_client)


async def test_cf_bind_nonexistent_handle(cf_binding_service, mock_cf_client):
    mock_cf_client.check_user_exists.return_value = False
    context = make_context(TEST_USER_ID)
    success, msg, info = await cf_binding_service.start_binding(context, "NonExistentUser123456")
    assert not success
    assert "不存在" in msg
    assert info is None


async def test_cf_bind_lifecycle(cf_binding_service, mock_cf_client, database):
    context = make_context(TEST_USER_ID)

    # 1. Start binding
    success, msg, info = await cf_binding_service.start_binding(context, "tourist")
    assert success
    assert "tourist" in msg
    assert info is not None
    assert info["handle"] == "tourist"
    assert "token" in info

    # Verify pending state in database
    async with database() as session:
        pending = await session.get(CFBindingPending, TEST_USER_ID)
        assert pending is not None
        assert pending.handle == "tourist"
        assert pending.token == info["token"]

    # 2. Confirm binding when CE verification fails
    mock_cf_client.validate_submission_ce.return_value = False
    success, msg = await cf_binding_service.confirm_binding(context)
    assert not success
    assert "验证未通过" in msg

    # 3. Confirm binding when CE verification succeeds
    mock_cf_client.validate_submission_ce.return_value = True
    success, msg = await cf_binding_service.confirm_binding(context)
    assert success
    assert "绑定成功" in msg

    # Verify binding and logs in database
    async with database() as session:
        pending = await session.get(CFBindingPending, TEST_USER_ID)
        assert pending is None

        binding = await session.get(CFBinding, TEST_USER_ID)
        assert binding is not None
        assert binding.handle == "tourist"
        assert binding.status == "bound"

        logs = (await session.scalars(select(CFBindingLog).where(CFBindingLog.user_id == TEST_USER_ID))).all()
        assert len(logs) == 1
        assert logs[0].action == "bind"
        assert logs[0].handle == "tourist"

    # 4. Check already bound when querying status or trying to re-bind
    success, msg = await cf_binding_service.confirm_binding(context)
    assert success
    assert "你当前已绑定 Codeforces 账号: [tourist]" in msg

    success, msg, _ = await cf_binding_service.start_binding(context, "tourist")
    assert not success
    assert "无需重复绑定" in msg

    # Another user trying to bind the same handle
    other_context = make_context(OTHER_USER_ID)
    success, msg, _ = await cf_binding_service.start_binding(other_context, "tourist")
    assert not success
    assert "已被其他用户绑定" in msg

    # 5. Unbind
    success, msg = await cf_binding_service.unbind(context)
    assert success
    assert "解绑成功" in msg

    # Verify unbind effect and audit log in database
    async with database() as session:
        binding = await session.get(CFBinding, TEST_USER_ID)
        assert binding is None

        logs = (
            await session.scalars(
                select(CFBindingLog)
                .where(CFBindingLog.user_id == TEST_USER_ID)
                .order_by(CFBindingLog.created_at)
            )
        ).all()
        assert len(logs) == 2
        assert logs[1].action == "unbind"
        assert logs[1].handle == "tourist"


async def test_cf_bind_expired(cf_binding_service, mock_cf_client, database):
    context = make_context(TEST_USER_ID)

    # Start binding
    success, msg, info = await cf_binding_service.start_binding(context, "Petr")
    assert success

    # Simulate expiration by modifying expires_at in DB
    async with database() as session, session.begin():
        pending = await session.get(CFBindingPending, TEST_USER_ID)
        pending.expires_at = time.time() - 100

    success, msg = await cf_binding_service.confirm_binding(context)
    assert not success
    assert "超时" in msg

    async with database() as session:
        pending = await session.get(CFBindingPending, TEST_USER_ID)
        assert pending is None
