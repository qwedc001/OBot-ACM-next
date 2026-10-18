"""Authorized update requests and durable post-restart notifications."""

import asyncio
import contextlib
from dataclasses import asdict, replace

from framework.catalog import CommandSpec
from framework.clients.reply import DeliveryFailed
from framework.clients.updater import SupervisorUnavailable
from framework.contracts import AccessDenied, Role
from framework.messages import Markdown, SendTarget

from .base import Service


def report_pages(operation):
    outcomes = {
        "success": "更新或重启完成",
        "rolled_back": "启动失败，已恢复最近可用版本",
        "rejected": "更新准备失败，原版本继续运行",
        "interrupted": "更新中断，已恢复最近可用版本",
        "recovery_failed": "恢复启动失败，需要服务器管理员介入",
    }
    title = outcomes.get(operation.get("outcome"), "操作进行中")
    if operation.get("outcome") == "success":
        title = {"test": "测试分支已启动", "restore": "已恢复测试前稳定版及数据"}.get(
            operation.get("action"), title
        )
    header = f"## {title}\n操作：{operation.get('id', '')[:12]}\n"
    if operation.get("running_branch"):
        header += (
            f"当前分支：{operation['running_branch']}\n提交：{operation.get('running_commit', '')[:12]}\n"
        )
    lines = str(operation.get("log", "")).replace("`", "'").splitlines()[-50:]
    chunks, current = [], ""
    for line in lines:
        line = line[:500]
        if len(current) + len(line) > 1800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    return [header + "\n```text\n" + chunk + "```" for chunk in chunks] or [header]


class UpdatesService(Service):
    dependencies = ("clients.updater", "clients.reply")

    @classmethod
    def build(cls, container):
        return cls(container)

    def __init__(self, container):
        self.container = container
        self.task = None
        self.health_task = None

    async def authorize(self, context):
        spec = CommandSpec("git.authorize", "git", "git", "代码更新", permission=Role.SUPERUSER)
        if not context.command_allowed or not await context.command_allowed(spec):
            raise AccessDenied("Bot administrator required")

    async def execute(self, context, action, target=None, *, branch=None):
        await self.authorize(context)
        return await self.container.clients.updater.request(
            action,
            **({"target": asdict(target)} if target else {}),
            **({"branch": branch} if branch is not None else {}),
        )

    async def start(self):
        if self.container.clients.updater.socket:
            self.task = asyncio.create_task(self.report_loop())
            self.health_task = asyncio.create_task(self.health_loop())

    async def close(self):
        for task in (self.task, self.health_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def health_loop(self):
        updater = self.container.clients.updater
        while True:
            await asyncio.sleep(1)
            if self.container.started and self.container.telemetry.connected_bots:
                with contextlib.suppress(SupervisorUnavailable):
                    await updater.request("ready", boot_id=updater.boot_id)

    async def report_loop(self):
        while True:
            await asyncio.sleep(2)
            if not self.container.started or not self.container.telemetry.connected_bots:
                continue
            updater = self.container.clients.updater
            try:
                for report in await updater.request("reports"):
                    target = SendTarget(**report["target"])
                    for index, page in enumerate(report_pages(report)):
                        if index < report.get("page", 0):
                            continue
                        recipient = replace(target, reply_sequence=(target.reply_sequence or 2) + index)
                        try:
                            await self.container.clients.reply.send_to(Markdown(content=page), recipient)
                        except DeliveryFailed:
                            # Native Markdown can be unavailable; retry text in the same passive window.
                            try:
                                await self.container.clients.reply.send_to(page, recipient)
                            except DeliveryFailed:
                                await self.container.clients.reply.send_to(
                                    Markdown(content=page),
                                    replace(recipient, reply_id=None, reply_sequence=None),
                                )
                        await updater.request("progress", id=report["id"], page=index + 1)
                    await updater.request("ack", id=report["id"])
            except (SupervisorUnavailable, DeliveryFailed, ValueError):
                # Keep the durable report; /git plog can reply to a fresh user event.
                await asyncio.sleep(28)
