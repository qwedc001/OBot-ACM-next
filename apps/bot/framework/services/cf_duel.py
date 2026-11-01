"""Codeforces 1v1 duel service and PTT rating settlement."""

import logging
import random
import secrets
import time
from collections.abc import Callable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.codeforces import CodeforcesClient
from ..contracts import RequestContext
from ..models import CFBinding, CFDuel, CFUserPtt
from .base import Service

logger = logging.getLogger(__name__)

# Pairing code expiry: 5 minutes
PAIRING_EXPIRE_SECONDS = 5 * 60


class PttSystem:
    """PTT (Potential Rating) settlement system based on Elo mechanism."""

    MIN_PTT = -500
    MAX_PTT = 6000
    BASE_RATING = 1400
    BASE_K = 128

    # Extra bonuses for the first 6 duels (sums to 1400)
    BONUSES = [500, 350, 250, 150, 100, 50]

    @classmethod
    def get_calc_ptt(cls, user_ptt: int, contest_count: int) -> int:
        calc_ptt = user_ptt + 1400
        for count, bonus in enumerate(cls.BONUSES):
            if contest_count == count:
                break
            calc_ptt -= bonus
        return calc_ptt

    @classmethod
    def calculate_ptt_change(cls, r_a: int, r_b: int, outcome: int, difficulty: int) -> tuple[int, int]:
        """
        Calculate PTT rating changes.
        :param r_a: User A calculated PTT
        :param r_b: User B calculated PTT
        :param outcome: 0=A win, 1=B win, 2=Tie
        :param difficulty: Problem difficulty rating (800~3000)
        :return: (delta_a, delta_b)
        """
        difficulty = max(800, min(difficulty, 3000))
        difficulty_factor = difficulty / 3000.0
        inc_k = 2.0 * (difficulty_factor**0.9)
        dec_k = difficulty_factor**1.6

        ptt_factor_a = (r_a - cls.MIN_PTT) / (cls.MAX_PTT - cls.MIN_PTT)
        ptt_factor_b = (r_b - cls.MIN_PTT) / (cls.MAX_PTT - cls.MIN_PTT)

        exp_a = 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))
        exp_b = 1.0 - exp_a

        if outcome == 0:  # A win
            delta_a = cls.BASE_K * inc_k * (1.0 - exp_a) * (1.0 - ptt_factor_a)
            delta_b = cls.BASE_K * dec_k * (0.0 - exp_b) * ptt_factor_b
        elif outcome == 1:  # B win
            delta_a = cls.BASE_K * dec_k * (0.0 - exp_a) * ptt_factor_a
            delta_b = cls.BASE_K * inc_k * (1.0 - exp_b) * (1.0 - ptt_factor_b)
        else:  # Tie
            delta_a = cls.BASE_K * 0.08 * max(0.0, difficulty_factor - ptt_factor_a)
            delta_b = cls.BASE_K * 0.08 * max(0.0, difficulty_factor - ptt_factor_b)

        return int(round(delta_a)), int(round(delta_b))

    @classmethod
    def apply_delta(cls, current_ptt: int, history: list[int], raw_delta: int) -> tuple[int, int]:
        """
        Applies rating delta with cold-start bonuses and boundary checks.
        Returns: (new_ptt, final_delta)
        """
        contest_count = len(history)
        calc_ptt = cls.get_calc_ptt(current_ptt, contest_count)
        new_calc_ptt = max(cls.MIN_PTT, min(calc_ptt + raw_delta, cls.MAX_PTT))

        if contest_count < len(cls.BONUSES):
            delta = raw_delta + cls.BONUSES[contest_count]
        else:
            delta = new_calc_ptt - calc_ptt

        new_ptt = current_ptt + delta
        return new_ptt, delta


class CFDuelService(Service):
    dependencies = ("clients.codeforces",)

    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.clients.codeforces)

    def __init__(self, session_factory: Callable[[], AsyncSession], cf_client: CodeforcesClient):
        self.session_factory = session_factory
        self.cf_client = cf_client

    async def get_bound_handle(self, user_id: str) -> str | None:
        async with self.session_factory() as session:
            binding = await session.get(CFBinding, user_id)
            if binding and binding.status == "bound":
                return binding.handle
            return None

    async def get_user_ptt(self, user_id: str) -> tuple[int, list[int]]:
        async with self.session_factory() as session:
            user_ptt = await session.get(CFUserPtt, user_id)
            if user_ptt:
                return user_ptt.ptt, list(user_ptt.contest_history or [])
            return 0, []

    async def get_active_duel_for_user(self, session: AsyncSession, user_id: str) -> CFDuel | None:
        stmt = (
            select(CFDuel)
            .where(
                or_(CFDuel.sender_id == user_id, CFDuel.receiver_id == user_id),
                CFDuel.status.in_(["pending", "active"]),
            )
            .order_by(CFDuel.created_at.desc())
        )
        duels = (await session.scalars(stmt)).all()
        now = time.time()
        for duel in duels:
            if duel.status == "pending" and duel.expires_at < now:
                duel.status = "expired"
                continue
            return duel
        return None

    async def challenge(
        self,
        context: RequestContext,
        rating_range: str | None = None,
        tag: str | None = None,
    ) -> tuple[bool, str]:
        """
        Initiate a 1v1 duel challenge.
        """
        user_id = context.actor.id
        scene_key = context.scene.key if hasattr(context.scene, "key") else str(context.scene)

        # 1. Check if user is bound
        handle = await self.get_bound_handle(user_id)
        if not handle:
            return False, "你当前未绑定 Codeforces 账号，请先使用 /cf bind [handle] 进行绑定。"

        now = time.time()
        async with self.session_factory() as session, session.begin():
            # 2. Check if user already in an active or pending duel
            existing = await self.get_active_duel_for_user(session, user_id)
            if existing:
                if existing.status == "pending":
                    return False, f"你已有等待接受的对战请求（对战码：{existing.pair_code}），请勿重复发起。"
                return False, f"你当前正在对战中（对战码：{existing.pair_code}），无法发起新对战。"

            # 3. Generate unique pair code (6 hex chars uppercase)
            pair_code = secrets.token_hex(3).upper()

            duel = CFDuel(
                scene=scene_key,
                pair_code=pair_code,
                sender_id=user_id,
                sender_handle=handle,
                status="pending",
                rating_range=rating_range,
                tag=tag,
                created_at=now,
                expires_at=now + PAIRING_EXPIRE_SECONDS,
            )
            session.add(duel)

        msg = (
            f"已发起 Codeforces 1v1 对战请求！\n"
            f"发起者：{handle}\n"
            f"对战码：{pair_code}\n\n"
            f"请对手在 5 分钟内发送 /cf duel accept {pair_code} 接受对战。"
        )
        return True, msg

    async def accept(self, context: RequestContext, pair_code: str) -> tuple[bool, str]:
        """
        Accept an existing challenge, select problem and start the duel.
        """
        user_id = context.actor.id
        scene_key = context.scene.key if hasattr(context.scene, "key") else str(context.scene)
        pair_code = pair_code.strip().upper()

        handle = await self.get_bound_handle(user_id)
        if not handle:
            return False, "你当前未绑定 Codeforces 账号，请先使用 /cf bind [handle] 进行绑定。"

        now = time.time()
        async with self.session_factory() as session, session.begin():
            stmt = select(CFDuel).where(CFDuel.pair_code == pair_code).with_for_update()
            duel = (await session.scalars(stmt)).first()
            if not duel:
                return False, "对战码无效或不存在，请检查输入。"

            if duel.scene != scene_key:
                return False, "无法跨群或跨场景接受对战，请在发起对战的对应场景中接受。"

            if duel.sender_id == user_id:
                return False, "你不能接受自己发起的对战。"

            if duel.status != "pending" or duel.expires_at < now:
                duel.status = "expired" if duel.status == "pending" else duel.status
                return False, "该对战请求已失效或已被接受。"

            # Check user's own status
            existing = await self.get_active_duel_for_user(session, user_id)
            if existing:
                return False, f"你当前已有进行中或等待中的对战（对战码：{existing.pair_code}），无法接受新对战。"

            # Check sender binding
            sender_binding = await session.get(CFBinding, duel.sender_id)
            if not sender_binding or sender_binding.status != "bound":
                duel.status = "canceled"
                return False, "发起者账号状态异常，无法开始对战。"

            sender_handle = duel.sender_handle
            receiver_handle = handle

            # Problem selection:
            # 1. Fetch ratings
            r_a = await self.cf_client.get_user_rating(sender_handle)
            r_b = await self.cf_client.get_user_rating(receiver_handle)
            r_avg = ((r_a or 1400) + (r_b or 1400)) // 2

            # Parse rating range
            min_r, max_r = max(800, r_avg - 250), min(3000, r_avg + 250)
            if duel.rating_range:
                try:
                    if "-" in duel.rating_range:
                        parts = duel.rating_range.split("-")
                        min_r, max_r = int(parts[0]), int(parts[1])
                    else:
                        val = int(duel.rating_range)
                        min_r, max_r = val, val
                except Exception:
                    pass

            # 2. Exclude submitted problems
            excludes = set()
            excludes.update(await self.cf_client.get_user_submitted_problems(sender_handle))
            excludes.update(await self.cf_client.get_user_submitted_problems(receiver_handle))

            # 3. Fetch candidate problems
            problems = await self.cf_client.get_problemset(duel.tag)
            candidates = [
                p
                for p in problems
                if p.get("rating")
                and min_r <= p["rating"] <= max_r
                and f"{p.get('contestId')}{p.get('index')}" not in excludes
            ]

            # Fallback if too strict: do NOT silently discard tag or rating range!
            # If user explicitly specified tag/range and no candidate matched, fail explicitly instead of silently giving random problem.
            if not candidates:
                reason_parts = []
                if duel.tag:
                    reason_parts.append(f"标签 [{duel.tag}]")
                if duel.rating_range:
                    reason_parts.append(f"难度 [{min_r}-{max_r}]")
                filter_desc = "及".join(reason_parts) if reason_parts else f"难度 [{min_r}-{max_r}]"
                return False, f"未找到符合条件（{filter_desc}）且双方未做过的题目，请更换筛选条件重新发起对战。"

            chosen = random.choice(candidates)

            # Update duel state
            duel.receiver_id = user_id
            duel.receiver_handle = receiver_handle
            duel.status = "active"
            duel.problem = chosen
            duel.started_at = now

        contest_id = chosen.get("contestId")
        index = chosen.get("index")
        name = chosen.get("name")
        rating = chosen.get("rating", "Unrated")
        prob_url = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"

        msg = (
            f"对战开始！\n"
            f"对战双方：{sender_handle} vs {receiver_handle}\n"
            f"题目：CF{contest_id}{index} - {name} (*{rating})\n"
            f"链接：{prob_url}\n\n"
            f"规则：任意一方 AC 后发送 /cf duel finish 结束并结算。\n"
            f"若均未通过或认输可发送 /cf duel giveup。"
        )
        return True, msg

    async def finish(self, context: RequestContext) -> tuple[bool, str]:
        """
        Finish active duel and settle PTT rating atomically.
        """
        user_id = context.actor.id
        now = time.time()

        # Step 1: Read active duel info first without holding a long transaction
        async with self.session_factory() as session:
            stmt = select(CFDuel).where(
                or_(CFDuel.sender_id == user_id, CFDuel.receiver_id == user_id),
                CFDuel.status == "active",
            )
            duel = (await session.scalars(stmt)).first()
            if not duel:
                return False, "你当前没有进行中的对战，无法结算。"

            duel_id = duel.id
            sender_id = duel.sender_id
            receiver_id = duel.receiver_id
            sender_handle = duel.sender_handle
            receiver_handle = duel.receiver_handle
            problem = duel.problem or {}
            contest_id = problem.get("contestId")
            index = problem.get("index")
            prob_rating = problem.get("rating", 1400)
            duel_start_time = duel.started_at or duel.created_at

        # Step 2: Fetch both participants' submission status outside DB transaction
        status_sender = await self.cf_client.get_problem_status(
            sender_handle, contest_id, index, duel_start_time
        )
        status_receiver = await self.cf_client.get_problem_status(
            receiver_handle, contest_id, index, duel_start_time
        )

        if status_sender is None or status_receiver is None:
            return False, "获取提交状态失败，请稍后重试。"

        ac_sender, penalty_sender = status_sender
        ac_receiver, penalty_receiver = status_receiver

        if not ac_sender and not ac_receiver:
            return False, "双方均未通过题目，无法结算。若放弃对战请发送 /cf duel giveup。"

        if ac_sender and not ac_receiver:
            outcome = 0
        elif ac_receiver and not ac_sender:
            outcome = 1
        else:
            if penalty_sender < penalty_receiver:
                outcome = 0
            elif penalty_sender > penalty_receiver:
                outcome = 1
            else:
                outcome = 2

        # Step 3: Atomic state change and PTT settlement inside transaction
        async with self.session_factory() as session, session.begin():
            duel = await session.get(CFDuel, duel_id, with_for_update=True)
            if not duel or duel.status != "active":
                return False, "对战状态已发生变化，请勿重复结算。"

            sender_ptt_obj = await session.get(CFUserPtt, sender_id, with_for_update=True)
            if not sender_ptt_obj:
                sender_ptt_obj = CFUserPtt(
                    user_id=sender_id,
                    ptt=0,
                    contest_history=[],
                    created_at=now,
                    updated_at=now,
                )
                session.add(sender_ptt_obj)

            receiver_ptt_obj = await session.get(CFUserPtt, receiver_id, with_for_update=True)
            if not receiver_ptt_obj:
                receiver_ptt_obj = CFUserPtt(
                    user_id=receiver_id,
                    ptt=0,
                    contest_history=[],
                    created_at=now,
                    updated_at=now,
                )
                session.add(receiver_ptt_obj)

            r_a_calc = PttSystem.get_calc_ptt(sender_ptt_obj.ptt, len(sender_ptt_obj.contest_history or []))
            r_b_calc = PttSystem.get_calc_ptt(receiver_ptt_obj.ptt, len(receiver_ptt_obj.contest_history or []))

            delta_a_raw, delta_b_raw = PttSystem.calculate_ptt_change(r_a_calc, r_b_calc, outcome, prob_rating)

            sender_history = list(sender_ptt_obj.contest_history or [])
            receiver_history = list(receiver_ptt_obj.contest_history or [])

            new_ptt_a, delta_a = PttSystem.apply_delta(sender_ptt_obj.ptt, sender_history, delta_a_raw)
            new_ptt_b, delta_b = PttSystem.apply_delta(receiver_ptt_obj.ptt, receiver_history, delta_b_raw)

            sender_history.append(new_ptt_a)
            receiver_history.append(new_ptt_b)

            sender_ptt_obj.ptt = new_ptt_a
            sender_ptt_obj.contest_history = sender_history
            sender_ptt_obj.updated_at = now

            receiver_ptt_obj.ptt = new_ptt_b
            receiver_ptt_obj.contest_history = receiver_history
            receiver_ptt_obj.updated_at = now

            duel.status = "finished"
            duel.outcome = outcome
            duel.sender_ptt_delta = delta_a
            duel.receiver_ptt_delta = delta_b
            duel.finished_at = now

            sender_handle = duel.sender_handle
            receiver_handle = duel.receiver_handle

        winner_desc = "平局"
        if outcome == 0:
            winner_desc = f"{sender_handle} 胜"
        elif outcome == 1:
            winner_desc = f"{receiver_handle} 胜"

        sign_a = f"+{delta_a}" if delta_a > 0 else f"{delta_a}"
        sign_b = f"+{delta_b}" if delta_b > 0 else f"{delta_b}"

        msg = (
            f"对战结束！结果：{winner_desc}\n\n"
            f"[{sender_handle}]: {'AC' if ac_sender else '未通过'}，罚时 {penalty_sender} min，"
            f"PTT: {new_ptt_a} ({sign_a})\n"
            f"[{receiver_handle}]: {'AC' if ac_receiver else '未通过'}，罚时 {penalty_receiver} min，"
            f"PTT: {new_ptt_b} ({sign_b})"
        )
        return True, msg

    async def giveup(self, context: RequestContext) -> tuple[bool, str]:
        """
        Give up active duel or cancel pending duel.
        """
        user_id = context.actor.id
        now = time.time()

        async with self.session_factory() as session, session.begin():
            stmt = (
                select(CFDuel)
                .where(
                    or_(CFDuel.sender_id == user_id, CFDuel.receiver_id == user_id),
                    CFDuel.status.in_(["pending", "active"]),
                )
                .with_for_update()
            )
            duel = (await session.scalars(stmt)).first()
            if not duel:
                return False, "你当前没有进行中或等待中的对战。"

            if duel.status == "pending":
                duel.status = "canceled"
                return True, "已取消对战请求。"

            # Active duel: giving up counts as defeat
            problem = duel.problem or {}
            prob_rating = problem.get("rating", 1400)

            # If sender gave up, receiver wins (outcome = 1). If receiver gave up, sender wins (outcome = 0).
            outcome = 1 if duel.sender_id == user_id else 0

            sender_ptt_obj = await session.get(CFUserPtt, duel.sender_id, with_for_update=True)
            if not sender_ptt_obj:
                sender_ptt_obj = CFUserPtt(
                    user_id=duel.sender_id,
                    ptt=0,
                    contest_history=[],
                    created_at=now,
                    updated_at=now,
                )
                session.add(sender_ptt_obj)

            receiver_ptt_obj = await session.get(CFUserPtt, duel.receiver_id, with_for_update=True)
            if not receiver_ptt_obj:
                receiver_ptt_obj = CFUserPtt(
                    user_id=duel.receiver_id,
                    ptt=0,
                    contest_history=[],
                    created_at=now,
                    updated_at=now,
                )
                session.add(receiver_ptt_obj)

            r_a_calc = PttSystem.get_calc_ptt(sender_ptt_obj.ptt, len(sender_ptt_obj.contest_history or []))
            r_b_calc = PttSystem.get_calc_ptt(receiver_ptt_obj.ptt, len(receiver_ptt_obj.contest_history or []))

            delta_a_raw, delta_b_raw = PttSystem.calculate_ptt_change(r_a_calc, r_b_calc, outcome, prob_rating)

            sender_history = list(sender_ptt_obj.contest_history or [])
            receiver_history = list(receiver_ptt_obj.contest_history or [])

            new_ptt_a, delta_a = PttSystem.apply_delta(sender_ptt_obj.ptt, sender_history, delta_a_raw)
            new_ptt_b, delta_b = PttSystem.apply_delta(receiver_ptt_obj.ptt, receiver_history, delta_b_raw)

            sender_history.append(new_ptt_a)
            receiver_history.append(new_ptt_b)

            sender_ptt_obj.ptt = new_ptt_a
            sender_ptt_obj.contest_history = sender_history
            sender_ptt_obj.updated_at = now

            receiver_ptt_obj.ptt = new_ptt_b
            receiver_ptt_obj.contest_history = receiver_history
            receiver_ptt_obj.updated_at = now

            duel.status = "finished"
            duel.outcome = outcome
            duel.sender_ptt_delta = delta_a
            duel.receiver_ptt_delta = delta_b
            duel.finished_at = now

            surrenderer = duel.sender_handle if duel.sender_id == user_id else duel.receiver_handle
            winner = duel.receiver_handle if duel.sender_id == user_id else duel.sender_handle

        sign_a = f"+{delta_a}" if delta_a > 0 else f"{delta_a}"
        sign_b = f"+{delta_b}" if delta_b > 0 else f"{delta_b}"

        msg = (
            f"对战结束！{surrenderer} 认输，判定 {winner} 获胜。\n\n"
            f"[{duel.sender_handle}]: PTT: {new_ptt_a} ({sign_a})\n"
            f"[{duel.receiver_handle}]: PTT: {new_ptt_b} ({sign_b})"
        )
        return True, msg
