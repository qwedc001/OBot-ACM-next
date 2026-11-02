"""Direct unit tests for CF 1v1 duel and PTT settlement."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from framework.contracts import Actor, RequestContext, SceneKind, SceneRef
from framework.models import CFBinding, CFDuel, CFUserPtt, User
from framework.services.cf_duel import CFDuelService, PttSystem

USER_A_ID = "user_a"
USER_B_ID = "user_b"


def make_context(user_id: str) -> RequestContext:
    actor = Actor(user_id, f"qq:bot:c2c_user:{user_id}")
    scene = SceneRef("qq", "bot", SceneKind.GROUP, "1001")
    return RequestContext(actor=actor, scene=scene, event_id="ev-1")


@pytest.fixture
def mock_cf_client():
    client = AsyncMock()
    client.get_user_rating = AsyncMock(return_value=1500)
    client.get_user_submitted_problems = AsyncMock(return_value=set())
    client.get_problemset = AsyncMock(
        return_value=[
            {"contestId": 1000, "index": "A", "name": "Problem A", "rating": 1500},
            {"contestId": 1000, "index": "B", "name": "Problem B", "rating": 1600},
        ]
    )
    client.get_problem_status = AsyncMock()
    return client


@pytest.fixture
async def cf_duel_service(database, mock_cf_client):
    async with database() as session, session.begin():
        session.add_all([User(id=USER_A_ID), User(id=USER_B_ID)])
    async with database() as session, session.begin():
        session.add_all(
            [
                CFBinding(
                    user_id=USER_A_ID,
                    handle="tourist",
                    status="bound",
                    created_at=time.time(),
                    updated_at=time.time(),
                ),
                CFBinding(
                    user_id=USER_B_ID,
                    handle="Petr",
                    status="bound",
                    created_at=time.time(),
                    updated_at=time.time(),
                ),
            ]
        )
    return CFDuelService(database, mock_cf_client)


def test_ptt_system_calculations():
    # Test PTT change calculation
    delta_a, delta_b = PttSystem.calculate_ptt_change(1500, 1500, 0, 1500)
    assert delta_a > 0
    assert delta_b < 0

    # Test tie
    delta_tie_a, delta_tie_b = PttSystem.calculate_ptt_change(1500, 1500, 2, 1500)
    assert delta_tie_a >= 0
    assert delta_tie_b >= 0

    # Test cold start bonuses
    history = []
    ptt = 0
    new_ptt, final_delta = PttSystem.apply_delta(ptt, history, 20)
    # 20 + BONUSES[0] (500) = 520
    assert new_ptt == 520
    assert final_delta == 520


@pytest.mark.asyncio
async def test_duel_lifecycle_and_settlement(cf_duel_service, mock_cf_client, database):
    ctx_a = make_context(USER_A_ID)
    ctx_b = make_context(USER_B_ID)

    # 1. Player A challenges
    ok_inv, msg_inv = await cf_duel_service.challenge(ctx_a, rating_range="abc")
    assert not ok_inv
    assert "有效数字" in msg_inv

    ok_rev, msg_rev = await cf_duel_service.challenge(ctx_a, rating_range="1600-1400")
    assert not ok_rev
    assert "不能大于上限" in msg_rev

    for rejected_range in ["700-1000", "1400-3600", "3600", "1200-1400-1600"]:
        ok_rej, _ = await cf_duel_service.challenge(ctx_a, rating_range=rejected_range)
        assert not ok_rej

    # Valid boundary cases
    for accepted_range in ["800", "3500", "800-3500"]:
        ok_bound, _ = await cf_duel_service.challenge(ctx_a, rating_range=accepted_range)
        assert ok_bound
        # Clean up pending duel for next iteration
        async with database() as session, session.begin():
            await session.execute(delete(CFDuel).where(CFDuel.sender_id == USER_A_ID))

    ok, msg = await cf_duel_service.challenge(ctx_a, rating_range="1400-1600")
    assert ok
    assert "已发起 Codeforces 1v1 对战请求" in msg

    # Find pair code from database
    async with database() as session:
        duel = (await session.scalars(select(CFDuel))).first()
        assert duel is not None
        pair_code = duel.pair_code

    # Duplicate challenge should be rejected
    ok2, msg2 = await cf_duel_service.challenge(ctx_a)
    assert not ok2
    assert "请勿重复发起" in msg2

    # Self-accept should be rejected
    ok_self, msg_self = await cf_duel_service.accept(ctx_a, pair_code)
    assert not ok_self
    assert "不能接受自己发起的对战" in msg_self

    # Cross-scene accept should be rejected
    from framework.contracts import SceneKind, SceneRef
    ctx_other_scene = RequestContext(
        actor=ctx_b.actor,
        scene=SceneRef("qq", "bot", SceneKind.GROUP, "9999"),
        event_id="ev-diff",
    )
    ok_scene, msg_scene = await cf_duel_service.accept(ctx_other_scene, pair_code)
    assert not ok_scene
    assert "无法跨群或跨场景" in msg_scene

    # 2. Player B accepts
    ok_acc, msg_acc = await cf_duel_service.accept(ctx_b, pair_code)
    assert ok_acc
    assert "对战开始" in msg_acc
    assert "tourist vs Petr" in msg_acc

    # Verify status in database
    async with database() as session:
        active_duel = await session.get(CFDuel, duel.id)
        assert active_duel.status == "active"
        assert active_duel.receiver_id == USER_B_ID
        assert active_duel.problem is not None

    # 3. Settlement before anyone ACs
    mock_cf_client.get_problem_status.side_effect = [
        (False, 0),  # Player A
        (False, 0),  # Player B
    ]
    ok_fin, msg_fin = await cf_duel_service.finish(ctx_a)
    assert not ok_fin
    assert "双方均未通过题目" in msg_fin

    # 4. Settlement with Player A AC and Player B not AC
    mock_cf_client.get_problem_status.side_effect = [
        (True, 15),  # Player A AC, 15 min penalty
        (False, 0),  # Player B no AC
    ]
    ok_fin2, msg_fin2 = await cf_duel_service.finish(ctx_a)
    assert ok_fin2
    assert "tourist 胜" in msg_fin2
    assert "AC" in msg_fin2

    # 5. Verify duel status and PTT records in DB
    async with database() as session:
        fin_duel = await session.get(CFDuel, duel.id)
        assert fin_duel.status == "finished"
        assert fin_duel.outcome == 0
        assert fin_duel.sender_ptt_delta > 0
        assert fin_duel.receiver_ptt_delta is not None

        ptt_a = await session.get(CFUserPtt, USER_A_ID)
        ptt_b = await session.get(CFUserPtt, USER_B_ID)
        assert ptt_a is not None
        assert ptt_b is not None
        assert ptt_a.ptt > 0
        assert len(ptt_a.contest_history) == 1
        assert len(ptt_b.contest_history) == 1


@pytest.mark.asyncio
async def test_duel_giveup_flow(cf_duel_service, mock_cf_client, database):
    ctx_a = make_context(USER_A_ID)
    ctx_b = make_context(USER_B_ID)

    # A challenges
    ok, _ = await cf_duel_service.challenge(ctx_a)
    assert ok

    # A cancels pending challenge
    ok_cancel, msg_cancel = await cf_duel_service.giveup(ctx_a)
    assert ok_cancel
    assert "已取消对战请求" in msg_cancel

    # A challenges again and B accepts
    ok, _ = await cf_duel_service.challenge(ctx_a)
    async with database() as session:
        duel = (
            await session.scalars(
                select(CFDuel).where(CFDuel.status == "pending")
            )
        ).first()
        pair_code = duel.pair_code

    ok_acc, _ = await cf_duel_service.accept(ctx_b, pair_code)
    assert ok_acc

    # B gives up during active match -> A wins
    ok_giveup, msg_giveup = await cf_duel_service.giveup(ctx_b)
    assert ok_giveup
    assert "Petr 认输" in msg_giveup
    assert "tourist 获胜" in msg_giveup

    async with database() as session:
        fin_duel = await session.get(CFDuel, duel.id)
        assert fin_duel.status == "finished"
        assert fin_duel.outcome == 0  # Sender (A) wins


@pytest.mark.asyncio
async def test_duel_concurrent_settlement(cf_duel_service, mock_cf_client, database):
    ctx_a = make_context(USER_A_ID)
    ctx_b = make_context(USER_B_ID)

    # 1. Challenge & Accept
    ok, _ = await cf_duel_service.challenge(ctx_a)
    assert ok
    async with database() as session:
        duel = (await session.scalars(select(CFDuel).where(CFDuel.status == "pending"))).first()
        pair_code = duel.pair_code

    ok_acc, _ = await cf_duel_service.accept(ctx_b, pair_code)
    assert ok_acc

    # 2. Both AC
    barrier = asyncio.Barrier(2)

    async def status(*args, **kwargs):
        await barrier.wait()
        return (True, 20)

    mock_cf_client.get_problem_status.side_effect = status

    # Call finish concurrently
    res1, res2 = await asyncio.gather(
        cf_duel_service.finish(ctx_a),
        cf_duel_service.finish(ctx_b),
        return_exceptions=False,
    )

    # One must succeed, one must fail with status changed / already settled
    results = [res1, res2]
    succeeded = [r for r in results if r[0] is True]
    failed = [r for r in results if r[0] is False]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert "请勿重复结算" in failed[0][1]

    # Check PTT history has only 1 entry per user
    async with database() as session:
        ptt_a = await session.get(CFUserPtt, USER_A_ID)
        ptt_b = await session.get(CFUserPtt, USER_B_ID)
        assert len(ptt_a.contest_history) == 1
        assert len(ptt_b.contest_history) == 1

