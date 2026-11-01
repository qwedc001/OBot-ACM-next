"""File/CLI boundary for Peeper; never import or patch its Python modules.

The upstream CLI writes caches and logs beside main.py, regardless of cwd or
--config. Run an unchanged private copy so concurrent users cannot share files
and the pinned submodule remains read-only. No credentials go on argv.
"""

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from .processes import process_environment as cli_environment
from .processes import run_process


class PeeperCLIError(Exception):
    """Safe diagnostic code, without upstream logs or configuration values."""


def prepare_workspace(source: Path, work: Path, config: dict, snapshots=()):
    """Stage upstream program/resources and its documented JSON cache format."""
    # Copy the distribution, not an inventory of current internal modules or
    # assets: new upstream resources should need no adapter change.
    shutil.copytree(
        source,
        work,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.log", ".env*", "config.json"),
    )
    (work / "data").mkdir(exist_ok=True)
    (work / "config.json").write_text(json.dumps([config], ensure_ascii=False), encoding="utf-8")
    for snapshot in snapshots:
        path = work / "data" / f"{config['id']}-{snapshot['day']}.json"
        path.write_text(json.dumps(snapshot["payload"], ensure_ascii=False), encoding="utf-8")
        os.utime(path, (snapshot["captured_at"], snapshot["captured_at"]))


@asynccontextmanager
async def cli_workspace(source, config, snapshots=()):
    directory = tempfile.TemporaryDirectory(prefix="obot-peeper-")
    work = Path(directory.name)
    preparation = asyncio.create_task(asyncio.to_thread(prepare_workspace, source, work, config, snapshots))
    try:
        try:
            await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # A cancelled to_thread call keeps copying. Reap it before cleanup
            # so it cannot recreate files after the private directory is gone.
            with suppress(Exception):
                await preparation
            raise
        yield work
    finally:
        await asyncio.to_thread(directory.cleanup)


async def run_cli(work: Path, arguments: list[str], *, timezone: str, timeout: float = 55, proxy=None):
    """Run the official entry point; cancellation kills and reaps the child.

    v1.2.5 catches generation exceptions and still exits zero. Its documented
    last_traceback.log marker must therefore say 'ok'; never expose that log.
    Callers also validate the requested output before using it.
    """
    marker = work / "last_traceback.log"
    marker.unlink(missing_ok=True)
    code = await run_process(
        [str(work / "main.py"), "--config", str(work / "config.json"), *arguments],
        cwd=work,
        environment={**cli_environment(timezone), **(proxy or {})},
        timeout=timeout,
    )
    if code:
        raise PeeperCLIError("cli_exit")
    try:
        success = await asyncio.to_thread(marker.read_text, encoding="utf-8")
    except OSError:
        raise PeeperCLIError("missing_status") from None
    if success.strip() != "ok":
        raise PeeperCLIError("cli_failed")
