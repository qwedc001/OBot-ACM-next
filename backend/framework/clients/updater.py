"""Asynchronous access to the independent local supervisor; no shell execution."""

import asyncio
import json
import os

from .base import Client


class SupervisorUnavailable(RuntimeError):
    pass


class UpdaterClient(Client):
    @classmethod
    def build(cls, container):
        return cls(os.getenv("OBOT_SUPERVISOR_SOCKET"), os.getenv("OBOT_SUPERVISOR_BOOT_ID"))

    def __init__(self, socket=None, boot_id=None):
        self.socket, self.boot_id = socket, boot_id

    async def request(self, action, **values):
        if not self.socket:
            raise SupervisorUnavailable("未配置更新守护进程。")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket, limit=262144), 5
            )
            try:
                writer.write(json.dumps({"action": action, **values}).encode() + b"\n")
                await writer.drain()
                result = json.loads(await asyncio.wait_for(reader.readline(), 60))
                if "error" in result:
                    raise SupervisorUnavailable("守护进程拒绝操作：" + str(result["error"]))
                return result["result"]
            finally:
                writer.close()
                await writer.wait_closed()
        except (OSError, TimeoutError, ValueError) as error:
            raise SupervisorUnavailable("更新守护进程暂时不可用。") from error
