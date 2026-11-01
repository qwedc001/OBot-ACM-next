"""Codeforces account binding and verification service."""

import logging
import secrets
import time
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.codeforces import CodeforcesClient
from ..contracts import RequestContext
from ..models import CFBinding, CFBindingLog, CFBindingPending
from .base import Service

logger = logging.getLogger(__name__)

# Verification duration window: 10 minutes
BIND_TIMEOUT_SECONDS = 10 * 60


class CFBindingService(Service):
    dependencies = ("clients.codeforces",)

    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.clients.codeforces)

    def __init__(self, session_factory: Callable[[], AsyncSession], cf_client: CodeforcesClient):
        self.session_factory = session_factory
        self.cf_client = cf_client

    async def get_bound_handle(self, user_id: str) -> str | None:
        """Get currently bound CF handle for the user."""
        async with self.session_factory() as session:
            binding = await session.get(CFBinding, user_id)
            if binding and binding.status == "bound":
                return binding.handle
            return None

    async def get_user_by_handle(self, handle: str) -> str | None:
        """Find user_id bound to the given handle."""
        async with self.session_factory() as session:
            stmt = select(CFBinding.user_id).where(
                CFBinding.handle.ilike(handle), CFBinding.status == "bound"
            )
            return await session.scalar(stmt)

    async def get_pending_binding(self, user_id: str) -> CFBindingPending | None:
        """Get pending verification record if not expired."""
        now = time.time()
        async with self.session_factory() as session:
            pending = await session.get(CFBindingPending, user_id)
            if pending:
                if pending.expires_at < now:
                    await session.delete(pending)
                    await session.commit()
                    return None
                return pending
            return None

    async def start_binding(self, context: RequestContext, handle: str) -> tuple[bool, str, dict | None]:
        """
        Initiate binding process.
        Returns: (success: bool, message: str, info: dict | None)
        """
        user_id = context.actor.id
        now = time.time()

        # 1. Check if user already bound
        existing_handle = await self.get_bound_handle(user_id)
        if existing_handle:
            if existing_handle.lower() == handle.lower():
                return False, f"你已经绑定了 Codeforces 账号 [{existing_handle}]，无需重复绑定。", None
            return (
                False,
                f"你当前已绑定账号 [{existing_handle}]。\n如需更换，请先使用 /cf unbind 解绑后重试。",
                None,
            )

        # 2. Check if handle is already bound by another user
        bound_user = await self.get_user_by_handle(handle)
        if bound_user and bound_user != user_id:
            return False, f"Codeforces 账号 [{handle}] 已被其他用户绑定。", None

        # 3. Verify handle exists on CF
        exists = await self.cf_client.check_user_exists(handle)
        if not exists:
            return False, f"Codeforces 账号 [{handle}] 不存在，请检查输入拼写。", None

        # 4. Generate verification token and save pending
        token = secrets.token_hex(4).upper()  # 8-char hex code e.g. "A1B2C3D4"
        expires_at = now + BIND_TIMEOUT_SECONDS

        async with self.session_factory() as session, session.begin():
            # Delete any existing pending record for this user
            await session.execute(delete(CFBindingPending).where(CFBindingPending.user_id == user_id))
            pending = CFBindingPending(
                user_id=user_id,
                handle=handle,
                token=token,
                problem="1A",
                created_at=now,
                expires_at=expires_at,
            )
            session.add(pending)

        info = {
            "handle": handle,
            "token": token,
            "problem": "1A",
            "url": "https://codeforces.com/contest/1/submit",
            "expires_in_minutes": 10,
        }
        msg = (
            f"正在发起 Codeforces 账号绑定: [{handle}]\n\n"
            f"请在 10 分钟内使用该账号在 Codeforces P1A (Theatre Square) 提交一发编译错误 (Compilation Error)。\n"
            f"代码中可包含验证 Token: /* {token} */\n"
            f"题目提交链接: https://codeforces.com/contest/1/submit\n\n"
            f"提交完成后，请发送 /cf bind 确认完成绑定。"
        )
        return True, msg, info

    async def confirm_binding(self, context: RequestContext) -> tuple[bool, str]:
        """
        Verify pending submission from Codeforces API and finalize binding.
        """
        user_id = context.actor.id
        now = time.time()

        async with self.session_factory() as session:
            pending = await session.get(CFBindingPending, user_id)
            if not pending:
                bound = await session.get(CFBinding, user_id)
                if bound and bound.status == "bound":
                    return True, f"你当前已绑定 Codeforces 账号: [{bound.handle}]"
                return False, "你当前没有正在进行的绑定流程，请使用 /cf bind <handle> 发起绑定。"

            if pending.expires_at < now:
                await session.delete(pending)
                await session.commit()
                return False, "绑定已超时（超过 10 分钟），请重新发起 /cf bind <handle>。"

            handle = pending.handle
            created_at = pending.created_at

        # Verify on CF: P1A CE submission created between created_at and now + 60s
        is_valid = await self.cf_client.validate_submission_ce(
            handle=handle,
            contest_id=1,
            problem_index="A",
            min_time=created_at - 10,  # 10s clock skew tolerance
            max_time=now + 60,
        )

        if not is_valid:
            return (
                False,
                f"验证未通过：未在 Codeforces 找到账号 [{handle}] 在发起绑定后对 P1A 提交的编译错误 (CE) 记录。\n"
                f"请确保使用正确账号提交，并等待几十秒评测完成后再执行 /cf bind 确认。",
            )

        # Successful verification! Update DB
        async with self.session_factory() as session, session.begin():
            # Remove pending
            await session.execute(delete(CFBindingPending).where(CFBindingPending.user_id == user_id))

            # Upsert or save binding
            binding = await session.get(CFBinding, user_id)
            if binding:
                binding.handle = handle
                binding.status = "bound"
                binding.updated_at = now
            else:
                binding = CFBinding(
                    user_id=user_id,
                    handle=handle,
                    status="bound",
                    created_at=now,
                    updated_at=now,
                )
                session.add(binding)

            # Audit log
            log = CFBindingLog(
                user_id=user_id,
                handle=handle,
                action="bind",
                detail=f"Bound handle {handle} via P1A CE verification",
                created_at=now,
            )
            session.add(log)

        logger.info("User %s successfully bound CF handle %s", user_id, handle)
        return True, f"🎉 绑定成功！已成功将 Codeforces 账号 [{handle}] 绑定至你的身份。"

    async def unbind(self, context: RequestContext) -> tuple[bool, str]:
        """
        Unbind CF handle and record audit log.
        """
        user_id = context.actor.id
        now = time.time()

        async with self.session_factory() as session, session.begin():
            # Clear pending if any
            await session.execute(delete(CFBindingPending).where(CFBindingPending.user_id == user_id))

            binding = await session.get(CFBinding, user_id)
            if not binding or binding.status != "bound":
                return False, "你当前尚未绑定任何 Codeforces 账号，无需解绑。"

            old_handle = binding.handle
            await session.delete(binding)

            # Audit log
            log = CFBindingLog(
                user_id=user_id,
                handle=old_handle,
                action="unbind",
                detail=f"Unbound handle {old_handle}",
                created_at=now,
            )
            session.add(log)

        logger.info("User %s unbound CF handle %s", user_id, old_handle)
        return True, f"解绑成功，已解除 Codeforces 账号 [{old_handle}] 的绑定关系。"
