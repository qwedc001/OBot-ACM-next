"""Rendering backends own drawing libraries; callers exchange JSON and artifacts."""

import asyncio
import json
import pickle
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from framework.contracts import Actor

from ..integration.processes import finish_io, process_environment, run_process
from .artifacts import Artifact, ArtifactClient
from .base import Client
from .jobs import JobQueue, QueueClosed


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    mime: str
    width: int
    height: int


class RenderBackend(Protocol):
    def render(self, model: dict[str, Any]) -> RenderedImage: ...


class PixieBackend:
    """Wraps an existing Pixie renderer without leaking Image to application code."""

    def __init__(self, render_image: Callable[[dict[str, Any]], Any]) -> None:
        self._render_image = render_image

    def render(self, model: dict[str, Any]) -> RenderedImage:
        image = self._render_image(model)
        with tempfile.TemporaryDirectory(prefix="obot-render-") as directory:
            path = Path(directory) / "output.png"
            image.write_file(str(path))
            return RenderedImage(path.read_bytes(), "image/png", image.width, image.height)


class RenderClient(Client):
    dependencies = ("clients.jobs", "clients.artifacts")

    @classmethod
    def build(cls, container):
        return cls(container.clients.jobs, container.clients.artifacts)

    def __init__(self, jobs: JobQueue, artifacts: ArtifactClient) -> None:
        self.jobs = jobs
        self.artifacts = artifacts
        self._views: dict[str, RenderBackend] = {}
        self._futures: set[asyncio.Future] = set()
        self._active: set[asyncio.Task] = set()
        self._closed = False

    async def _images(self, view, model, multiple):
        directory = tempfile.TemporaryDirectory(prefix="obot-render-")
        work = Path(directory.name)

        def prepare():
            with (work / "input.pickle").open("wb") as handle:
                pickle.dump((self._views[view], model, multiple, self.artifacts.max_bytes), handle)

        def read():
            result = json.loads((work / "result.json").read_text(encoding="utf-8"))
            if "error" in result:
                kind = {"ImportError": ImportError, "OSError": OSError, "ValueError": ValueError}
                raise kind.get(result["error"], OSError)("Isolated renderer unavailable")
            images = []
            for index, page in enumerate(result["pages"]):
                path = work / f"{index}.png"
                if path.stat().st_size > self.artifacts.max_bytes:
                    raise ValueError("Rendered image exceeds artifact budget")
                images.append(RenderedImage(path.read_bytes(), **page))
            return tuple(images)

        try:
            await finish_io(prepare)
            code = await run_process(
                ["-m", "framework.integration.render_worker", str(work)],
                cwd=work,
                environment={**process_environment(), "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                timeout=self.jobs.timeout,
            )
            if code:
                raise OSError("Isolated renderer exited unsuccessfully")
            return await finish_io(read)
        finally:
            await finish_io(directory.cleanup)

    async def _submit(self, actor, view, model, multiple):
        if self._closed:
            raise QueueClosed("Renderer closed")
        if view not in self._views:
            raise ValueError("Unknown render view")

        async def execute():
            task = asyncio.current_task()
            self._active.add(task)
            try:
                images = await self._images(view, model, multiple)
                result = []
                for image in images:
                    result.append(
                        await self.artifacts.create(
                            actor,
                            image.data,
                            image.mime,
                            width=image.width,
                            height=image.height,
                        )
                    )
                return tuple(result)
            finally:
                self._active.discard(task)

        # Each job owns a process and private files. Only the shared queue's
        # configured worker count limits concurrency; there is no global draw key.
        future = await self.jobs.submit(f"render:{view}", execute)
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)
        return await future

    async def close(self):
        self._closed = True
        for future in tuple(self._futures):
            future.cancel()
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def render_pages(
        self,
        actor: Actor,
        view: str,
        model: dict[str, Any],
    ) -> tuple[Artifact, ...]:
        return await self._submit(actor, view, model, True)

    def register(self, view: str, backend: RenderBackend) -> None:
        if not view or view in self._views:
            raise ValueError("Render view is empty or already registered")
        # A worker imports the backend anew; closures/local classes/native
        # drawing objects cannot be transferred to a clean interpreter.
        try:
            pickle.dumps(backend)
        except (pickle.PickleError, TypeError, AttributeError) as error:
            raise ValueError("Render backend must be importable and serializable") from error
        self._views[view] = backend

    async def render(
        self,
        actor: Actor,
        view: str,
        model: dict[str, Any],
    ) -> Artifact:
        return (await self._submit(actor, view, model, False))[0]
