"""Scoped board workflows and persistent one-use web binding capabilities."""

import asyncio
import difflib
import hashlib
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from nonebot.log import logger
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert

from ..board import BoardError, board_name, day_string, hydro_host
from ..catalog import CommandSpec
from ..contracts import AccessDenied, Actor, Role, SceneKind
from ..messages import Image, Message, SendTarget, Text
from ..models import BoardBindToken, BoardConfig, BoardSnapshot
from ..web_url import public_origin
from .base import Service

PRIVATE = {SceneKind.C2C, SceneKind.DIRECT, SceneKind.WEB}
MANAGE = CommandSpec("board.manage", "board", "manage", "管理本群榜单配置", permission=Role.GROUP_ADMIN)
ALIASES = {
    "AC": "Accepted",
    "WA": "Wrong Answer",
    "TLE": "Time Limit Exceeded",
    "MLE": "Memory Limit Exceeded",
    "OLE": "Output Limit Exceeded",
    "RE": "Runtime Error",
    "CE": "Compile Error",
    "SE": "System Error",
    "FE": "Format Error",
    "HKD": "Hacked",
}


class BoardService(Service):
    dependencies = ("clients.credentials", "clients.hydro", "clients.peeper", "clients.reply")

    @classmethod
    def build(cls, container):
        instance = cls(
            container.session_factory,
            container.settings,
            container.clients.credentials,
            container.clients.hydro,
            container.clients.peeper,
        )
        instance.reply = container.clients.reply
        return instance

    def __init__(self, session_factory, settings, credentials, hydro, peeper):
        self.sessions, self.settings = session_factory, settings
        self.credentials, self.hydro, self.peeper = credentials, hydro, peeper
        self._locks = defaultdict(asyncio.Lock)
        self.reply = None

    def scope(self, context):
        return "actor:" + context.actor.id if context.scene.kind in PRIVATE else "scene:" + context.scene.key

    async def can_manage(self, context):
        return context.scene.kind in PRIVATE or bool(
            context.command_allowed and await context.command_allowed(MANAGE)
        )

    async def require_manage(self, context):
        if not await self.can_manage(context):
            raise AccessDenied("Group administrator required")

    def base_url(self):
        try:
            return public_origin(self.settings.obot_public_base_url)
        except ValueError:
            raise BoardError("绑定网页地址未配置或无效，请联系机器人管理员。") from None

    async def issue(self, context, name):
        await self.require_manage(context)
        name = board_name(name)
        base, token, now = self.base_url(), secrets.token_urlsafe(32), time.time()
        scope = self.scope(context)
        async with self.sessions() as session, session.begin():
            await session.execute(delete(BoardBindToken).where(BoardBindToken.expires_at < now - 86400))
            names = set(await session.scalars(select(BoardConfig.name).where(BoardConfig.scope == scope)))
            active = set(
                await session.scalars(
                    select(BoardBindToken.name).where(
                        BoardBindToken.scope == scope,
                        BoardBindToken.used.is_(False),
                        BoardBindToken.expires_at > now,
                    )
                )
            )
            if (name not in names and len(names) >= 20) or (name not in active and len(active) >= 20):
                raise BoardError("当前对话最多保留 20 个榜单配置或待绑定名称，请先清理不需要的配置。")
            await session.execute(
                update(BoardBindToken)
                .where(BoardBindToken.scope == scope, BoardBindToken.name == name)
                .values(used=True, pending=None)
            )
            session.add(
                BoardBindToken(
                    digest=hashlib.sha256(token.encode()).hexdigest(),
                    scope=scope,
                    name=name,
                    owner_id=context.actor.id,
                    expires_at=now + self.settings.obot_board_bind_ttl,
                    used=False,
                    attempts=0,
                    claim=None,
                    pending=None,
                )
            )
        return base + "/board/bind#" + token

    def digest(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise BoardError("绑定链接无效、已使用或已过期，请重新发送 /board bind 配置名。")
        return hashlib.sha256(token.encode()).hexdigest()

    async def inspect(self, token):
        async with self.sessions() as session:
            row = await session.get(BoardBindToken, self.digest(token))
            if row is None or row.used or row.expires_at <= time.time() or row.attempts >= 5:
                raise BoardError("绑定链接无效、已使用或已过期，请重新发送 /board bind 配置名。")
            return {"name": row.name, "group": row.scope.startswith("scene:"), "expires_at": row.expires_at}

    async def submit(self, token, host, username, password, options=None):
        host = hydro_host(host)
        options = self.validate_options(options)
        if (
            not isinstance(username, str)
            or not 1 <= len(username.strip()) <= 128
            or not isinstance(password, str)
            or not 1 <= len(password) <= 1024
        ):
            raise BoardError("请填写管理员名称和密码。")
        digest, claim, now = self.digest(token), uuid4().hex, time.time()
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(BoardBindToken)
                .where(
                    BoardBindToken.digest == digest,
                    BoardBindToken.used.is_(False),
                    BoardBindToken.claim.is_(None),
                    BoardBindToken.expires_at > now,
                    BoardBindToken.attempts < 5,
                )
                .values(claim=claim, attempts=BoardBindToken.attempts + 1)
            )
            if not result.rowcount:
                raise BoardError("链接无效、已使用、过期或正在提交，请稍后重试或重新获取链接。")
        try:
            async with asyncio.timeout(50):
                values = {"uname": username.strip(), "password": password}
                await self.hydro.validate(host, values)
                encrypted = await self.credentials.encrypt(values)
            async with self.sessions() as session, session.begin():
                row = await session.get(BoardBindToken, digest)
                if row.used or row.claim != claim or row.expires_at <= time.time():
                    raise BoardError("绑定链接已失效，请重新获取链接。")
                pending = {"host": host, "credentials": encrypted, "options": options}
                row.used, row.claim = True, None
                group = row.scope.startswith("scene:")
                if group:
                    row.pending = pending
                else:
                    await self._commit(session, row, pending)
                return {
                    "name": row.name,
                    "group": group,
                    "message": f"已验证并保存。请发起人在原群发送 /board confirm {row.name} 完成群绑定。"
                    if group
                    else f"{row.name} 绑定成功，可返回 QQ 使用 /board today {row.name}。",
                }
        except BaseException:
            async with self.sessions() as session, session.begin():
                await session.execute(
                    update(BoardBindToken)
                    .where(BoardBindToken.digest == digest, BoardBindToken.claim == claim)
                    .values(claim=None)
                )
            raise

    async def _commit(self, session, row, pending):
        await session.execute(
            insert(BoardConfig)
            .values(
                id=uuid4().hex,
                scope=row.scope,
                name=row.name,
                owner_id=row.owner_id,
                host=pending["host"],
                credentials=pending["credentials"],
                options=pending["options"],
                revision=uuid4().hex,
                updated_at=time.time(),
            )
            .on_conflict_do_update(
                index_elements=[BoardConfig.scope, BoardConfig.name],
                set_={
                    "owner_id": row.owner_id,
                    "host": pending["host"],
                    "credentials": pending["credentials"],
                    "options": pending["options"],
                    "revision": uuid4().hex,
                    "updated_at": time.time(),
                },
            )
        )
        row.pending = None

    async def confirm(self, context, name):
        await self.require_manage(context)
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(BoardBindToken)
                .where(
                    BoardBindToken.scope == self.scope(context),
                    BoardBindToken.name == board_name(name),
                    BoardBindToken.owner_id == context.actor.id,
                    BoardBindToken.pending.is_not(None),
                    BoardBindToken.expires_at > time.time(),
                )
                .order_by(BoardBindToken.expires_at.desc())
            )
            if row is None or not row.pending:
                raise BoardError("没有由你发起且尚未过期的待确认配置。")
            pending = row.pending
            result = await session.execute(
                update(BoardBindToken)
                .where(
                    BoardBindToken.digest == row.digest,
                    BoardBindToken.claim.is_(None),
                    BoardBindToken.pending == pending,
                    BoardBindToken.expires_at > time.time(),
                )
                .values(claim=uuid4().hex)
            )
            if not result.rowcount:
                raise BoardError("该配置已确认或已失效，请重新获取绑定链接。")
            await self._commit(session, row, pending)
            return f"{row.name} 已绑定到本群，群员可以查询榜单。"

    async def configs(self, context):
        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(BoardConfig)
                    .where(BoardConfig.scope == self.scope(context))
                    .order_by(BoardConfig.name)
                )
            ).all()

    async def choose(self, context, name=None):
        configs = await self.configs(context)
        if name:
            chosen = next((config for config in configs if config.name == board_name(name)), None)
        else:
            if len(configs) > 1:
                raise BoardError(
                    "当前有多个配置，请在指令末尾指定名称。可用配置："
                    + "、".join(config.name for config in configs)
                )
            chosen = configs[0] if configs else None
        if chosen is None:
            raise BoardError("当前对话没有匹配的榜单配置，请先使用 /board bind 配置名。")
        return chosen

    async def unbind(self, context, name):
        await self.require_manage(context)
        config = await self.choose(context, name)
        async with self.sessions() as session, session.begin():
            await session.execute(delete(BoardSnapshot).where(BoardSnapshot.config_id == config.id))
            await session.execute(delete(BoardConfig).where(BoardConfig.id == config.id))
            await session.execute(
                update(BoardBindToken)
                .where(BoardBindToken.scope == config.scope, BoardBindToken.name == config.name)
                .values(used=True, pending=None)
            )
        return f"{config.name} 已解除绑定。"

    async def snapshot(self, config, day, *, historical=False, force=False):
        async with self._locks[config.id]:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(BoardSnapshot).where(
                        BoardSnapshot.config_id == config.id,
                        BoardSnapshot.day == day,
                        BoardSnapshot.revision == config.revision,
                    )
                )
                today = datetime.now(ZoneInfo(self.settings.obot_board_timezone)).date().isoformat()
                if row and not force and (day != today or row.captured_at > time.time() - 30):
                    return {
                        "day": row.day,
                        "captured_at": row.captured_at,
                        "payload": self.apply_options(row.payload, config.options),
                    }
                if historical:
                    raise BoardError("没有该日期的有效榜单缓存，历史记录从绑定后开始积累。")
            values = await self.credentials.decrypt(config.credentials)
            payload = await self.hydro.daily(
                config.host, values, day, self.settings.obot_board_timezone, refresh=force or row is None
            )
            payload = self.apply_options(payload, config.options)
            captured_at = time.time()
            async with self.sessions() as session, session.begin():
                current = await session.get(BoardConfig, config.id)
                if current is None or current.revision != config.revision:
                    raise BoardError("配置已改变，请重新查询榜单。")
                await session.execute(
                    insert(BoardSnapshot)
                    .values(
                        id=uuid4().hex,
                        config_id=config.id,
                        day=day,
                        revision=config.revision,
                        captured_at=captured_at,
                        payload=payload,
                    )
                    .on_conflict_do_update(
                        index_elements=[BoardSnapshot.config_id, BoardSnapshot.day],
                        set_={"revision": config.revision, "captured_at": captured_at, "payload": payload},
                    )
                )
            return {"day": day, "captured_at": captured_at, "payload": payload}

    async def image(self, context, mode, name=None, *, day=None, verdict="AC"):
        config = await self.choose(context, name)
        artifact = await self.image_for_config(context.actor, config, mode, day=day, verdict=verdict)
        latest = await self.choose(context, name)
        if latest.id != config.id or latest.revision != config.revision or latest.options != config.options:
            raise BoardError("配置已改变，请重新查询榜单。")
        return artifact

    async def image_for_config(self, actor, config, mode, *, day=None, verdict="AC"):
        now = datetime.now(ZoneInfo(self.settings.obot_board_timezone))
        target = day_string(day) if day else (now.date() - timedelta(days=mode == "full")).isoformat()
        if target > now.date().isoformat():
            raise BoardError("不能查询未来日期的榜单。")
        verdict = verdict.upper()
        if verdict not in ALIASES:
            raise BoardError("评测类型可选：" + "、".join(ALIASES))
        historical = target < (now.date() - timedelta(days=1)).isoformat()
        # --now owns today's login/collection. Seed yesterday's cache so the
        # official CLI can calculate ranks without re-fetching a fixed baseline.
        baseline = (now.date() - timedelta(days=1)).isoformat() if mode == "now" else target
        snapshots = [await self.snapshot(config, baseline, historical=historical)]
        if mode == "full":
            next_day = (datetime.fromisoformat(target).date() + timedelta(days=1)).isoformat()
            snapshots.append(await self.snapshot(config, next_day, historical=historical))
        model = {
            "name": config.name,
            "mode": mode,
            "day": target,
            "verdict": verdict,
            "snapshots": snapshots,
            "timezone": self.settings.obot_board_timezone,
            "show_unrated": config.options.get("show_unrated", True),
        }
        if mode == "now":
            model.update(
                host=config.host,
                credentials=await self.credentials.decrypt(config.credentials),
                exclude_uid=config.options.get("exclude_uid", []),
                exclude_before=config.options.get("exclude_before", "1970-01-01"),
            )
        return await self.peeper.render(actor, model)

    async def user(self, context, kind, value, name=None):
        if context.scene.kind not in PRIVATE:
            raise BoardError("用户资料仅可在私聊查询。")
        config = await self.choose(context, name)
        if kind == "name":
            today = datetime.now(ZoneInfo(self.settings.obot_board_timezone)).date().isoformat()
            data = (await self.snapshot(config, today))["payload"]
            users = {
                item["user"]["name"]: item["user"]["uid"]
                for item in [*data["rankings"], *data["submissions"]]
            }
            matches = difflib.get_close_matches(value, users, n=1, cutoff=0.4)
            if not matches:
                return "未找到该用户。"
            value = users[matches[0]]
        elif kind != "id":
            raise BoardError("用法：/board user id 用户ID 或 /board user name 用户名。")
        result = await self.hydro.user(config.host, await self.credentials.decrypt(config.credentials), value)
        today = datetime.now(ZoneInfo(self.settings.obot_board_timezone)).date().isoformat()
        data = (await self.snapshot(config, today))["payload"]
        submissions = [row for row in data["submissions"] if row["user"]["uid"] == str(value)]
        if submissions:
            result += f"\n\n今日提交：{len(submissions)} 次\n平均分：{sum(row['score'] for row in submissions) / len(submissions):.2f}\nAC率：{sum(row['verdict'] == 'Accepted' for row in submissions) / len(submissions):.2%}"
        else:
            result += "\n\n今日暂无提交。"
        return result

    def validate_options(self, options):
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise BoardError("榜单设置格式无效。")
        excluded = options.get("exclude_uid", [])
        if (
            not isinstance(excluded, list)
            or len(excluded) > 100
            or any(type(uid) is not int or uid < 0 for uid in excluded)
        ):
            raise BoardError("排除 UID 请填写最多 100 个非负整数。")
        show = options.get("show_unrated", True)
        if not isinstance(show, bool):
            raise BoardError("显示设置格式无效。")
        before = day_string(options.get("exclude_before") or "1970-01-01")
        return {"exclude_uid": excluded, "show_unrated": show, "exclude_before": before}

    async def starred(self, context, enabled=None, name=None):
        await self.require_manage(context)
        config = await self.choose(context, name)
        enabled = not config.options.get("show_unrated", True) if enabled is None else enabled
        if not isinstance(enabled, bool):
            raise BoardError("用法：/board starred [on|off|toggle] [配置名]。")
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(BoardConfig)
                .where(
                    BoardConfig.id == config.id,
                    BoardConfig.revision == config.revision,
                    BoardConfig.options == config.options,
                )
                .values(options={**config.options, "show_unrated": enabled}, updated_at=time.time())
            )
            if not result.rowcount:
                raise BoardError("配置已改变，请重新设置打星用户显示。")
        return f"{config.name}：已{'开启' if enabled else '关闭'}打星用户显示，下次生成榜单时生效。"

    def apply_options(self, payload, options):
        cutoff = (
            datetime.fromisoformat(options.get("exclude_before", "1970-01-01"))
            .replace(tzinfo=ZoneInfo(self.settings.obot_board_timezone))
            .timestamp()
        )
        return {
            **payload,
            "rankings": [
                {
                    **ranking,
                    "unrated": int(ranking["user"]["uid"]) in options.get("exclude_uid", [])
                    or bool(ranking["user"]["register_at"] and ranking["user"]["register_at"] < cutoff),
                }
                for ranking in payload["rankings"]
            ],
        }

    async def configure(self, context, options, name):
        await self.require_manage(context)
        config = await self.choose(context, name)
        options = self.validate_options(options)
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(BoardConfig)
                .where(
                    BoardConfig.id == config.id,
                    BoardConfig.revision == config.revision,
                    BoardConfig.options == config.options,
                )
                .values(options=options, updated_at=time.time())
            )
            if not result.rowcount:
                raise BoardError("配置已改变，请刷新页面后重试。")
        return f"{config.name}：榜单显示设置已保存。"

    async def push(self, context, enabled, name, target):
        await self.require_manage(context)
        config = await self.choose(context, name)
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(BoardConfig)
                .where(BoardConfig.id == config.id)
                .values(push_target=target if enabled else None)
            )
        return "已开启每日 00:30 昨日榜单推送。" if enabled else "已关闭每日榜单推送。"

    async def push_status(self, context, name=None):
        from nonebot_plugin_apscheduler import scheduler

        await self.require_manage(context)
        config = await self.choose(context, name)
        job = scheduler.get_job("board.push")
        next_run = getattr(job, "next_run_time", None)
        when = (
            next_run.astimezone(ZoneInfo(self.settings.obot_board_timezone)).strftime("%Y-%m-%d %H:%M:%S")
            if next_run
            else "暂无"
        )
        return (
            f"{config.name}：推送{'开启' if config.push_target else '关闭'}\n"
            f"调度器：{'运行中' if scheduler.running else '未运行'}\n"
            f"任务：{'已注册' if job else '未注册'}\n下次执行：{when}（{self.settings.obot_board_timezone}）\n"
            "推送配置保存在数据库；任务每次启动重新注册，离线时错过的推送不会自动补发。"
        )

    async def _deliver_board(self, config):
        actor = Actor(config.owner_id, "scheduler:board")
        artifact = await self.image_for_config(actor, config, "full")
        async with self.sessions() as session:
            latest = await session.get(BoardConfig, config.id)
            if (
                latest is None
                or latest.revision != config.revision
                or latest.options != config.options
                or latest.push_target != config.push_target
            ):
                logger.info("Board push skipped: config={} reason=config_changed", config.id)
                return False
        data = await self.peeper.artifacts.read(actor, artifact.id)
        await self.reply.send_to(Message(Text("昨日训练榜单"), Image(data)), SendTarget(**config.push_target))
        logger.info("Board push accepted by adapter: config={}", config.id)
        return True

    @staticmethod
    def log_push_failure(config, error):
        from ..clients.reply import DeliveryFailed

        diagnosis = DeliveryFailed.from_exception(error)
        logger.warning(
            "Board push failed: config={} type={} status={} code={}",
            config.id,
            type(error).__name__,
            diagnosis.status_code,
            diagnosis.code,
        )

    async def test_push(self, context, name=None):
        from ..clients.reply import DeliveryFailed

        await self.require_manage(context)
        config = await self.choose(context, name)
        if not config.push_target:
            raise BoardError("当前配置尚未开启推送，请先 /board push on。")
        try:
            delivered = await self._deliver_board(config)
        except DeliveryFailed as error:
            self.log_push_failure(config, error)
            details = [f"HTTP {error.status_code}" if error.status_code is not None else "HTTP 状态未知"]
            if error.code is not None:
                details.append(f"平台错误码 {error.code}")
            raise BoardError(
                "主动推送失败（"
                + "，".join(details)
                + "）。请检查 QQ 的允许主动发送开关及机器人主动消息能力。"
            ) from None
        if not delivered:
            raise BoardError("推送配置已改变，本次测试取消，请重新尝试。")
        return "已通过主动接口发送昨日榜单，请检查是否收到图片。"

    async def daily_push(self):
        async with self.sessions() as session:
            configs = (
                await session.scalars(select(BoardConfig).where(BoardConfig.push_target.is_not(None)))
            ).all()
        logger.info("Board daily push started: enabled={}", len(configs))
        for config in configs:
            try:
                await self._deliver_board(config)
            except Exception as error:
                self.log_push_failure(config, error)

    async def daily_update(self):
        yesterday = (
            datetime.now(ZoneInfo(self.settings.obot_board_timezone)).date() - timedelta(days=1)
        ).isoformat()
        async with self.sessions() as session:
            configs = (await session.scalars(select(BoardConfig))).all()
        for config in configs:
            try:
                await self.snapshot(config, yesterday, force=True)
            except (BoardError, TimeoutError) as error:
                logger.warning(
                    "Board daily snapshot failed: config={} type={}", config.id, type(error).__name__
                )

    async def start(self):
        from nonebot_plugin_apscheduler import scheduler

        scheduler.add_job(
            self.daily_update,
            "cron",
            hour=0,
            minute=5,
            timezone=self.settings.obot_board_timezone,
            id="board.daily",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            self.daily_push,
            "cron",
            hour=0,
            minute=30,
            timezone=self.settings.obot_board_timezone,
            id="board.push",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        logger.info("Board schedules registered: timezone={} push=00:30", self.settings.obot_board_timezone)

    async def close(self):
        from nonebot_plugin_apscheduler import scheduler

        if scheduler.get_job("board.daily"):
            scheduler.remove_job("board.daily")
        if scheduler.get_job("board.push"):
            scheduler.remove_job("board.push")
