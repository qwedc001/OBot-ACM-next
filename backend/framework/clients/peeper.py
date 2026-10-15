"""Run the unmodified submodule renderer asynchronously in an isolated process."""

import asyncio
import json
import os
import re
import struct
import sys

from nonebot.log import logger

from ..board import BoardError
from ..paths import PROJECT_ROOT
from .base import Client


class PeeperClient(Client):
    dependencies = ("clients.jobs", "clients.artifacts")

    @classmethod
    def build(cls, container):
        return cls(container.clients.jobs, container.clients.artifacts)

    def __init__(self, jobs, artifacts):
        self.jobs, self.artifacts = jobs, artifacts

    async def version(self):
        path = PROJECT_ROOT / "third-party/peeper-board-generator/module/constants.py"
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            found = re.search(r'VERSION_INFO\s*=\s*"([^"]+)"', text)
            return found[1] if found else "Unknown"
        except OSError:
            return "Unknown"

    async def render(self, actor, model):
        async def execute():
            root = PROJECT_ROOT
            if not (root / "third-party/peeper-board-generator/module/board/misc.py").is_file():
                raise BoardError("榜单生成器未初始化，请联系管理员。")
            environment = {
                key: os.environ[key]
                for key in (
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "HOME",
                    "USERPROFILE",
                    "LOCALAPPDATA",
                    "APPDATA",
                    "VIRTUAL_ENV",
                )
                if key in os.environ
            }
            environment.update(PYTHONIOENCODING="utf-8", TZ=model["timezone"])
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "framework.integration.peeper_worker",
                cwd=root,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(55):
                    data, _ = await process.communicate(json.dumps(model, ensure_ascii=False).encode())
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode or not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24:
                try:
                    failure = json.loads(data)
                    reason = failure.get("error") if isinstance(failure, dict) else None
                except (ValueError, UnicodeError):
                    reason = None
                reason = (
                    reason
                    if isinstance(reason, str) and reason in {"image_too_large", "render_failed"}
                    else "invalid_output"
                )
                logger.warning("Peeper renderer failed: exit={} reason={}", process.returncode, reason)
                if reason == "image_too_large":
                    raise BoardError("榜单过大，超过单次渲染上限，请调整打星用户显示设置或联系管理员。")
                raise BoardError("榜单图片生成失败，请稍后重试。")
            width, height = struct.unpack(">II", data[16:24])
            return await self.artifacts.create(actor, data, "image/png", width=width, height=height)

        return await (await self.jobs.submit("render:peeper", execute, key="render:peeper"))
