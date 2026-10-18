import argparse
import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path
from uuid import uuid4

from .config import Config
from .engine import Engine
from .ipc import request, serve

DEFAULT_CONFIG = Path("~/.config/obot/supervisor.toml").expanduser()


def exclusive(config):
    import fcntl

    lock = (config.state_dir / "daemon.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SystemExit("Supervisor is already running") from None
    return lock


async def daemon(config):
    lock = exclusive(config)
    engine = Engine(config, emit=lambda line: print(line, flush=True))
    if "good" not in engine.state:
        raise SystemExit("Run obotctl bootstrap before starting the supervisor")
    shutdown = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, shutdown.set)
    server = await serve(engine)
    watcher = None
    try:
        # The IPC server must run while boot() waits for the bot's ready handshake.
        async def start_locked():
            async with engine.lock:
                await engine.start()

        startup = asyncio.create_task(start_locked())
        stopped = asyncio.create_task(shutdown.wait())
        done, _ = await asyncio.wait({startup, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            startup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup
        else:
            await startup
            watcher = asyncio.create_task(engine.watch())
            await stopped
    except Exception:
        print(engine.tail.text(), flush=True)
        raise
    finally:
        if watcher:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        server.close()
        await server.wait_closed()
        await engine.close()
        config.socket.unlink(missing_ok=True)
        lock.close()


async def control(config, args):
    if args.action == "bootstrap":
        lock = exclusive(config)
        engine = Engine(config)
        try:
            if "good" in engine.state:
                raise ValueError("Already bootstrapped")
            commit = await engine.fetch()
            release = await engine.prepare(commit, uuid4().hex)
            engine.state.update(good=release, commit=commit, branch=config.branch)
            engine.save()
            print("Prepared initial release:", commit)
            print("First startup must be verified before removing the previous service backup.")
        except Exception:
            print(engine.tail.text())
            raise
        finally:
            lock.close()
        return
    action = "status" if args.action == "plog" else args.action
    if action == "update" and args.mode:
        action = args.mode
    payload = {"action": action}
    if action == "test":
        payload["branch"] = args.branch
    result = await request(config.socket, payload, timeout=60)
    if args.action in {"update", "restart"} and args.wait:
        operation_id = result
        while True:
            await asyncio.sleep(2)
            status = await request(config.socket, {"action": "status"})
            operation = status.get("operation") or {}
            if operation.get("id") != operation_id:
                raise RuntimeError("Operation replaced before completion")
            if operation.get("phase") == "finished":
                print(json.dumps(operation, ensure_ascii=False, indent=2))
                if operation["outcome"] != "success":
                    raise SystemExit(1)
                return
    if args.action == "plog":
        result = (result.get("operation") or {}).get("log", "No previous operation")
    print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))


def parser():
    result = argparse.ArgumentParser(description="OBot independent supervisor")
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return result


def control_parser():
    arguments = parser()
    actions = arguments.add_subparsers(dest="action", required=True)
    for action in ("bootstrap", "status", "update", "restart", "fetch", "log", "plog"):
        command = actions.add_parser(action)
        command.add_argument("--wait", action="store_true")
        if action == "update":
            modes = command.add_subparsers(dest="mode")
            test = modes.add_parser("test", help="Deploy a remote feature branch with a stable backup")
            test.add_argument("branch")
            restore = modes.add_parser("restore", help="Restore pre-test stable code and data")
            for mode in (test, restore):
                mode.add_argument("--wait", action="store_true", default=argparse.SUPPRESS)
    return arguments


def main():
    args = control_parser().parse_args()
    os.umask(0o077)
    asyncio.run(control(Config.load(args.config), args))


def daemon_main():
    arguments = parser()
    args = arguments.parse_args()
    os.umask(0o077)
    asyncio.run(daemon(Config.load(args.config)))
