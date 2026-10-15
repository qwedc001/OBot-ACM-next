"""Bot administrator diagnostics; command declarations live in plugins."""

from framework.contracts import RequestContext

from .base import Service


class MaintenanceService(Service):
    dependencies = ("clients.jobs",)

    @classmethod
    def build(cls, container):
        return cls(container)

    def __init__(self, container):
        self.container = container

    async def ping(self) -> str:
        return "pong"

    async def whoami(self, context: RequestContext) -> str:
        return f"权限：超级管理员\n主体：{context.actor.id}\n外部身份：{context.actor.identity_key}\n场景：{context.scene.key}"

    async def status(self) -> str:
        snapshot = self.container.snapshot()
        jobs = snapshot["jobs"]
        return (
            f"Lowlight · OBot {snapshot['version']}\n运行：{snapshot['telemetry']['uptime_seconds']:.0f} 秒\n"
            f"指令：{snapshot['command_count']}\n任务：{jobs['running']} 运行 / {jobs['pending']} 排队"
        )
