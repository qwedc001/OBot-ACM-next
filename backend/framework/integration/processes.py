"""Small shared subprocess boundary for native renderers and third-party CLIs."""

import asyncio
import os
import signal
import subprocess
import sys
from contextlib import suppress


def process_environment(timezone="UTC"):
    # Workers receive OS essentials, never the bot's tokens or settings.
    allowed = (
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
    return {
        **{key: os.environ[key] for key in allowed if key in os.environ},
        "PYTHONIOENCODING": "utf-8",
        "TZ": timezone,
    }


async def finish_io(function, *args):
    """Do not remove a private workspace while a cancelled thread still uses it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await settle(task)
        raise


async def settle(task):
    """Finish resource cleanup even if shutdown cancels its caller again."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def reap(spawning, process):
    if process is None:
        try:
            process = await spawning
        except Exception:
            return
    if os.name == "nt":
        if process.returncode is None:
            import psutil

            def kill_tree():
                # Windows venv python.exe may only be a launcher. Suspend it
                # before enumerating so it cannot create an interpreter after
                # our snapshot, then terminate/reap the whole private tree.
                try:
                    parent = psutil.Process(process.pid)
                    parent.suspend()
                    children = parent.children(recursive=True)
                except psutil.NoSuchProcess:
                    return
                targets = [*reversed(children), parent]
                for child in targets:
                    with suppress(psutil.NoSuchProcess):
                        child.kill()
                _, alive = psutil.wait_procs(targets, timeout=5)
                for child in alive:
                    with suppress(psutil.NoSuchProcess):
                        child.kill()
                        child.wait(timeout=5)

            await asyncio.to_thread(kill_tree)
    else:
        # Each job owns a session, including any descendants of a CLI.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def run_process(arguments, *, cwd, environment, timeout=55):
    # Shield spawn too: cancellation can arrive after the OS creates a child
    # but before asyncio returns its Process handle.
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-X",
            "utf8",
            *arguments,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
    )
    process = None
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.shield(spawning)
            return await process.wait()
    finally:
        # On timeout/cancellation, reclaim the actual OS process before its
        # queue slot is released. No orphan keeps drawing behind the counter.
        await settle(asyncio.create_task(reap(spawning, process)))
