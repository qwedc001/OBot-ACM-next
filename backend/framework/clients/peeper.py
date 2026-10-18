"""Peeper integration through its official CLI and configuration/cache files."""

import asyncio
import struct
from urllib.parse import urlsplit

from nonebot.log import logger

from ..board import BoardError, hydro_host
from ..integration.https_tunnel import pinned_https_proxy
from ..integration.peeper_cli import PeeperCLIError, cli_environment, cli_workspace, run_cli, run_process
from ..paths import PROJECT_ROOT
from .base import Client
from .hydro import public_addresses

SOURCE = PROJECT_ROOT / "third-party/peeper-board-generator"


def generator_config(model):
    return {
        "id": "board",
        "handler": "Hydro",
        "board_name": model["name"],
        "url": model.get("host", "https://offline.invalid/"),
        "credentials": model.get("credentials"),
        "show_unrated": model.get("show_unrated", True),
        "exclude_uid": model.get("exclude_uid", []),
        "exclude_reg_date": model.get("exclude_before", "1970-01-01"),
    }


class PeeperClient(Client):
    dependencies = ("clients.jobs", "clients.artifacts")

    @classmethod
    def build(cls, container):
        return cls(container.clients.jobs, container.clients.artifacts)

    def __init__(self, jobs, artifacts):
        self.jobs, self.artifacts = jobs, artifacts

    async def version(self):
        try:
            async with cli_workspace(SOURCE, generator_config({"name": "version"})) as work:
                output = work / "version.txt"
                await run_cli(work, ["--version", "--output", str(output)], timezone="UTC")
                text = await asyncio.to_thread(output.read_text, encoding="utf-8")
                return text.removeprefix("Peeper-Board-Generator ").strip()
        except (OSError, PeeperCLIError, TimeoutError):
            return "Unknown"

    async def render(self, actor, model):
        async def execute():
            if not (SOURCE / "main.py").is_file():
                raise BoardError("榜单生成器未初始化，请联系管理员。")
            try:
                async with cli_workspace(SOURCE, generator_config(model), model["snapshots"]) as work:
                    output, final = work / "board.png", work / "output.png"
                    arguments = ["--id", "board", "--separate_cols", "--output", str(output)]
                    if model["mode"] == "now":
                        host = urlsplit(hydro_host(model["host"])).hostname
                        addresses = await public_addresses(host)
                        async with pinned_https_proxy(host, addresses) as proxy:
                            await run_cli(
                                work,
                                [*arguments, "--now", "--verdict", model["verdict"]],
                                timezone=model["timezone"],
                                proxy=proxy,
                            )
                    else:
                        await run_cli(work, [*arguments, "--full", model["day"]], timezone=model["timezone"])
                    if not output.is_file():
                        raise PeeperCLIError("missing_image")
                    code = await run_process(
                        ["-m", "framework.integration.image_footer_worker", str(output), str(final)],
                        cwd=PROJECT_ROOT,
                        environment=cli_environment(model["timezone"]),
                    )
                    if code or not final.is_file() or final.stat().st_size > self.artifacts.max_bytes:
                        raise PeeperCLIError("invalid_image")
                    data = await asyncio.to_thread(final.read_bytes)
                    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24:
                        raise PeeperCLIError("invalid_image")
                    width, height = struct.unpack(">II", data[16:24])
                    return await self.artifacts.create(actor, data, "image/png", width=width, height=height)
            except (PeeperCLIError, OSError, TimeoutError) as error:
                reason = str(error) if isinstance(error, PeeperCLIError) else type(error).__name__
                logger.warning("Peeper CLI failed: {}", reason)
                raise BoardError(
                    "榜单生成器执行失败，请稍后重试；若持续失败，请管理员检查上游 CLI 的站点兼容性。"
                ) from None

        return await (await self.jobs.submit("render:peeper", execute, key="render:peeper"))
