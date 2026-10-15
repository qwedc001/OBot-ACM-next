"""Rendering backends own drawing libraries; callers exchange JSON and artifacts."""

import asyncio
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from framework.contracts import Actor

from .artifacts import Artifact, ArtifactClient
from .base import Client
from .jobs import JobQueue


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
        self._draw_lock = threading.Lock()

    def _draw(self, view: str, model: dict[str, Any]) -> RenderedImage:
        # Cancelling to_thread cannot stop native drawing. Keep serialization
        # inside the thread so a timed-out draw cannot overlap the next one.
        with self._draw_lock:
            return self._views[view].render(model)

    def _draw_pages(self, view: str, model: dict[str, Any]) -> tuple[RenderedImage, ...]:
        with self._draw_lock:
            backend = self._views[view]
            render_pages = getattr(backend, "render_pages", None)
            return tuple(render_pages(model)) if render_pages else (backend.render(model),)

    async def render_pages(
        self,
        actor: Actor,
        view: str,
        model: dict[str, Any],
    ) -> tuple[Artifact, ...]:
        async def execute() -> tuple[Artifact, ...]:
            images = await asyncio.to_thread(self._draw_pages, view, model)
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

        if view not in self._views:
            raise ValueError("Unknown render view")
        future = await self.jobs.submit(f"render:{view}", execute, key="render:pixie")
        return await future

    def register(self, view: str, backend: RenderBackend) -> None:
        if not view or view in self._views:
            raise ValueError("Render view is empty or already registered")
        self._views[view] = backend

    async def render(
        self,
        actor: Actor,
        view: str,
        model: dict[str, Any],
    ) -> Artifact:
        async def execute() -> Artifact:
            image = await asyncio.to_thread(self._draw, view, model)
            return await self.artifacts.create(
                actor,
                image.data,
                image.mime,
                width=image.width,
                height=image.height,
            )

        if view not in self._views:
            raise ValueError("Unknown render view")
        future = await self.jobs.submit(f"render:{view}", execute, key="render:pixie")
        return await future
