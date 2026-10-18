"""Single-writer update engine. The running release is never edited in place."""

import asyncio
import contextlib
import os
import shutil
import signal
import time
from pathlib import Path
from uuid import uuid4

from .config import validate_branch
from .state import Store, Tail


class Engine:
    def __init__(self, config, emit=None):
        self.config = config
        self.store = Store(config.state_dir)
        self.tail = Tail()
        self.child = None
        self.reader = None
        self.generation = None
        self.ready = asyncio.Event()
        self.ready_at = 0
        self.task = None
        self.closing = False
        self.lock = asyncio.Lock()
        self.emit = emit

    def output(self, line):
        self.tail.add(line)
        if self.emit:
            self.emit(self.tail.lines[-1])

    @property
    def state(self):
        return self.store.value

    def save(self):
        self.store.save()

    def release(self, name):
        path = (self.config.state_dir / "releases" / name).resolve()
        root = (self.config.state_dir / "releases").resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("Invalid release path")
        return path

    async def consume(self, stream):
        # readline's default limit can itself fail on long compiler output.
        pending = b""
        while chunk := await stream.read(4096):
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                self.output(line.decode("utf-8", "replace"))
            if len(pending) > 8192:
                self.output(pending[:8192].decode("utf-8", "replace"))
                pending = b""
        if pending:
            self.output(pending.decode("utf-8", "replace"))

    async def terminate(self, process):
        if os.name == "posix":
            # Render workers may outlive their parent. Always stop the whole group.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        if process.returncode is None:
            if os.name != "posix":
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), self.config.stop_timeout)
            except TimeoutError:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                await process.wait()
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

    async def command(self, *args, cwd=None, env=None, capture=False):
        process = await asyncio.create_subprocess_exec(
            *map(str, args),
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        if capture:
            output = asyncio.create_task(process.communicate())
        else:
            output = asyncio.create_task(self.consume(process.stdout))
        try:
            await asyncio.wait_for(process.wait(), self.config.command_timeout)
            result = await output
            if process.returncode:
                if capture:
                    for line in result[0].decode("utf-8", "replace").splitlines():
                        self.tail.add(line)
                raise RuntimeError(f"{Path(str(args[0])).name} exited {process.returncode}")
            return result[0].decode().strip() if capture else None
        finally:
            await self.terminate(process)
            await output

    async def git(self, *args, capture=False):
        return await self.command(
            "git", "--git-dir", self.config.state_dir / "repository.git", *args, capture=capture
        )

    async def fetch(self, branch=None):
        branch = validate_branch(branch or self.config.branch)
        repository = self.config.state_dir / "repository.git"
        if not repository.exists():
            await self.command("git", "init", "--bare", repository)
            await self.git("remote", "add", "origin", self.config.repository)
        await self.git("fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/deploy")
        return await self.git("rev-parse", "refs/remotes/origin/deploy", capture=True)

    async def prepare(self, commit, operation_id):
        name = f"{commit[:12]}-{operation_id[:12]}"
        path = self.release(name)
        path.parent.mkdir(exist_ok=True)
        await self.git("worktree", "add", "--detach", path, commit)
        # Runtime mounts must never replace anything supplied by a Git revision.
        for relative, target in (("data", self.config.data_dir), (".env.dev", self.config.env_file)):
            mount = path / relative
            if mount.exists() or mount.is_symlink():
                raise ValueError("Release contains a reserved runtime path")
            mount.symlink_to(target, target_is_directory=target.is_dir())
        await self.command("git", "-C", path, "submodule", "update", "--init", "--recursive")
        await self.command(self.config.uv, "sync", "--locked", "--no-dev", "--extra", "render", cwd=path)
        await self.command(
            self.config.pnpm, "--dir", path / "frontend", "install", "--frozen-lockfile", cwd=path
        )
        await self.command(self.config.pnpm, "--dir", path / "frontend", "build", cwd=path)
        return name

    def environment(self):
        return {
            **os.environ,
            "OBOT_SUPERVISOR_SOCKET": str(self.config.socket),
            "OBOT_SUPERVISOR_BOOT_ID": self.generation or "",
            "PYTHONUNBUFFERED": "1",
        }

    async def stop(self):
        if self.child:
            await self.terminate(self.child)
        if self.reader:
            await self.reader
        self.child = self.reader = None
        self.ready.clear()
        self.ready_at = 0

    async def boot(self, name):
        self.generation = uuid4().hex
        self.ready.clear()
        path = self.release(name)
        self.child = await asyncio.create_subprocess_exec(
            str(path / ".venv/bin/python"),
            "-m",
            "backend.bot",
            cwd=path,
            env=self.environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        self.reader = asyncio.create_task(self.consume(self.child.stdout))
        deadline = time.monotonic() + self.config.startup_timeout
        stable = None
        while time.monotonic() < deadline:
            if self.child.returncode is not None:
                await self.reader
                raise RuntimeError(f"Bot exited {self.child.returncode} during startup")
            if self.ready.is_set() and time.monotonic() - self.ready_at < 4:
                stable = stable or time.monotonic()
                if time.monotonic() - stable >= self.config.stable_seconds:
                    return
            else:
                stable = None
            await asyncio.sleep(0.2)
        raise RuntimeError("Bot readiness timeout (services and adapter connection required)")

    def snapshot(self, operation_id):
        snapshot = self.config.state_dir / "snapshots" / operation_id
        snapshot.parent.mkdir(exist_ok=True)
        # All bot processes are stopped. Include DB WAL, ORM migration cache and keys together.
        shutil.copytree(self.config.data_dir, snapshot, symlinks=True)
        return str(snapshot)

    def restore(self, snapshot):
        source = Path(snapshot).resolve()
        root = (self.config.state_dir / "snapshots").resolve()
        if not source.is_relative_to(root) or source == root or not source.is_dir():
            raise ValueError("Invalid snapshot")
        data = self.config.data_dir
        staging = self.config.state_dir / "restore-data"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging, symlinks=True)
        # Copy into the original directory, so release mounts keep the same destination.
        # The snapshot remains intact throughout; interrupted restoration is repeatable.
        for item in data.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        shutil.copytree(staging, data, dirs_exist_ok=True, symlinks=True)
        shutil.rmtree(staging)

    def finish(self, operation, outcome, error=None, log=None):
        operation.update(outcome=outcome, phase="finished", error=error, log=log or self.tail.text())
        operation.update(
            running_branch=self.state.get("branch", self.config.branch), running_commit=self.state["commit"]
        )
        self.state["operation"] = operation
        if operation.get("target"):
            self.state.setdefault("reports", []).append(dict(operation))
        self.save()

    async def recover(self):
        if self.state.get("recovery"):
            await self.restore_release(self.state["recovery"])
        if self.state.get("restore_required"):
            await asyncio.to_thread(self.restore, self.state["restore_required"])
            del self.state["restore_required"]
            self.save()
        operation = self.state.get("operation")
        if operation and operation.get("phase") != "finished":
            if operation.get("phase") == "mutating":
                if operation.get("rollback"):
                    await self.restore_release(operation["rollback"])
                else:
                    await asyncio.to_thread(self.restore, operation["snapshot"])
            self.finish(operation, "interrupted", "Supervisor interrupted; restored last known good release")

    def current_release(self):
        return {
            "good": self.state["good"],
            "commit": self.state["commit"],
            "branch": self.state.get("branch", self.config.branch),
        }

    async def restore_release(self, backup):
        """Persist recovery intent before replacing data; safe to repeat after power loss."""
        self.state["recovery"] = backup
        self.save()
        await asyncio.to_thread(self.restore, backup["snapshot"])
        self.state.update({key: backup[key] for key in ("good", "commit", "branch")})
        if backup["branch"] == self.config.branch:
            self.state.pop("baseline", None)
        del self.state["recovery"]
        self.save()

    async def update(self, operation):
        previous = self.current_release()
        snapshot = None
        stopped = False
        try:
            action = operation["action"]
            branch = operation.get("branch", previous["branch"])
            baseline = self.state.get("baseline")
            if action in {"update", "test"}:
                commit = await self.fetch(branch) if action == "test" else await self.fetch()
                if action == "update" and baseline:
                    # Returning with live test data is only safe after the tested code is merged.
                    ancestor = await self.git("merge-base", previous["commit"], commit, capture=True)
                    if ancestor != previous["commit"]:
                        raise ValueError(
                            "Merge the tested commit into the stable branch first, or use update restore"
                        )
                candidate = await self.prepare(commit, operation["id"])
            elif action == "restore":
                candidate, commit, branch = baseline["good"], baseline["commit"], baseline["branch"]
            else:
                candidate, commit = previous["good"], previous["commit"]
            operation.update(candidate=candidate, commit=commit)
            self.save()
            await self.stop()
            stopped = True
            operation["phase"] = "stopped"
            self.save()
            snapshot = await asyncio.to_thread(self.snapshot, operation["id"])
            if action == "test" and not baseline:
                # A baseline is a matched code/data pair from the running stable release,
                # not an unverified newer remote commit. Never overwrite it during a test cycle.
                baseline = {**previous, "snapshot": snapshot}
                self.state["baseline"] = baseline
                self.state.setdefault("baselines", []).append(baseline)
            rollback = baseline if baseline and action != "restore" else {**previous, "snapshot": snapshot}
            operation.update(snapshot=snapshot, rollback=rollback, phase="mutating")
            self.save()
            if action == "restore":
                await asyncio.to_thread(self.restore, baseline["snapshot"])
            path = self.release(candidate)
            await self.command(
                path / ".venv/bin/python",
                "-m",
                "framework.migrate",
                cwd=path,
                env={**self.environment(), "PYTHONPATH": str(path / "backend")},
            )
            self.tail = Tail()
            await self.boot(candidate)
            self.state.update(good=candidate, commit=commit, branch=branch)
            if branch == self.config.branch:
                self.state.pop("baseline", None)
            self.finish(operation, "success")
        except Exception as error:
            self.tail.add(f"{type(error).__name__}: {error}")
            failure_log = self.tail.text()
            if stopped:
                await self.stop()
                try:
                    if snapshot:
                        await self.restore_release(
                            operation.get("rollback") or {**previous, "snapshot": snapshot}
                        )
                    await self.boot(self.state["good"])
                except Exception as recovery_error:
                    self.tail.add(f"Recovery failed: {type(recovery_error).__name__}")
                    self.finish(operation, "recovery_failed", "Manual intervention required", failure_log)
                    return
            self.finish(
                operation, "rolled_back" if stopped else "rejected", type(error).__name__, failure_log
            )

    def submit(self, action, target=None, branch=None):
        if self.lock.locked() or self.task and not self.task.done():
            raise ValueError("Another operation is running")
        if self.state.get("recovery") or self.state.get("restore_required"):
            raise ValueError("Data recovery is pending; restart the supervisor first")
        if action == "test":
            validate_branch(branch)
            if branch == self.config.branch:
                raise ValueError("Use update for the stable branch")
            if self.state.get("branch", self.config.branch) != self.config.branch and not self.state.get(
                "baseline"
            ):
                raise ValueError("No stable baseline is available")
        elif branch is not None or action not in {"update", "restart", "restore"}:
            raise ValueError("Invalid update action or branch")
        if action == "restore" and not self.state.get("baseline"):
            raise ValueError("No active test baseline to restore")
        operation = {"id": uuid4().hex, "action": action, "phase": "preparing", "target": target}
        if action in {"update", "test"}:
            operation["branch"] = branch or self.config.branch
        self.state["operation"] = operation
        self.tail = Tail()
        self.save()
        self.task = asyncio.create_task(self.update(operation))
        return operation["id"]

    async def start(self):
        await self.recover()
        await self.boot(self.state["good"])

    async def watch(self):
        while not self.closing:
            await asyncio.sleep(3)
            if self.task and not self.task.done():
                continue
            if self.child is None or self.child.returncode is not None:
                async with self.lock:
                    with contextlib.suppress(Exception):
                        await self.stop()
                        await self.recover()
                        await self.boot(self.state["good"])
                await asyncio.sleep(5)

    async def close(self):
        self.closing = True
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.stop()
