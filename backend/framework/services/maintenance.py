"""Public identity inspection and administrator diagnostics; declarations live in plugins."""

from framework.catalog import CommandSpec
from framework.contracts import AccessDenied, MentionedUser, RequestContext, Role, SceneKind

from .base import Service


class MaintenanceService(Service):
    dependencies = ("clients.jobs", "identities")

    @classmethod
    def build(cls, container):
        return cls(container)

    def __init__(self, container):
        self.container = container

    async def ping(self) -> str:
        return "pong"

    async def whoami(self, context: RequestContext) -> str:
        role = "普通用户"
        if context.command_allowed:
            if await context.command_allowed(
                CommandSpec("identity.role", "identity", "role", "权限", permission=Role.SUPERUSER)
            ):
                role = "超级管理员"
            elif context.scene.kind in {SceneKind.GROUP, SceneKind.CHANNEL} and await context.command_allowed(
                CommandSpec("identity.role", "identity", "role", "权限", permission=Role.GROUP_ADMIN)
            ):
                role = "群管理员"
        return f"权限：{role}\n主体：{context.actor.id}\n外部身份：{context.actor.identity_key}\n场景：{context.scene.key}"

    async def whoisthis(self, context: RequestContext, targets: tuple[MentionedUser, ...]) -> str:
        permission = CommandSpec(
            "maintenance.lookup", "maintenance", "whoisthis", "查询提及用户的身份", permission=Role.SUPERUSER
        )
        if not context.command_allowed or not await context.command_allowed(permission):
            raise AccessDenied("Bot administrator required")
        if context.scene.kind not in {SceneKind.GROUP, SceneKind.CHANNEL}:
            return "请在群聊或频道中使用 /whoisthis @某人。"
        unique = {}
        for target in targets:
            if (
                target.identity.provider != context.scene.provider
                or target.identity.app_id != context.scene.app_id
            ):
                return "提及身份与当前平台不一致，无法查询。"
            previous = unique.get(target.identity.key)
            if previous is None or target.authoritative:
                unique[target.identity.key] = target
        if not unique:
            return "平台未提供目标用户的身份。请尝试引用回复目标用户的消息，并发送 /whoisthis；也可在指令后 @一位用户。若引用仍无法识别，说明本次推送缺少可用于查询的身份信息。"
        if len(unique) != 1:
            return "请在指令后 @且仅@一位用户，例如 /whoisthis @某人；不要选择全体成员或机器人。"
        target = next(iter(unique.values()))
        identities = self.container.identities
        actor = await identities.lookup(target.identity)
        created = actor is None
        if actor is None and target.authoritative:
            actor = await identities.resolve(target.identity)
        if actor is None:
            return "尚未找到该用户的主体，且平台未提供完整提及身份。请让对方在本群 @机器人发送任意消息（无需管理权限），再重新查询。"
        registered = await identities.is_superuser(actor)
        result = (
            f"主体：{actor.id}\n外部身份：{actor.identity_key}\n场景：{context.scene.key}\n"
            f"超管登记：{'已登记' if registered else '未登记'}"
        )
        if created:
            result += "\n已建立身份记录；本次查询不会更改权限。"
        return result

    async def status(self) -> str:
        snapshot = self.container.snapshot()
        jobs = snapshot["jobs"]
        return (
            f"Lowlight · OBot {snapshot['version']}\n运行：{snapshot['telemetry']['uptime_seconds']:.0f} 秒\n"
            f"指令：{snapshot['command_count']}\n任务：{jobs['running']} 运行 / {jobs['pending']} 排队"
        )
