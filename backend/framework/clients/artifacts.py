"""Temporary rendered images, private to their requester and bot administrators."""

import asyncio
import hashlib
import os
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from framework.contracts import AccessDenied, Actor

from .base import Client


class ArtifactUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Artifact:
    id: str
    mime: str
    size: int
    sha256: str
    expires_at: float
    width: int | None = None
    height: int | None = None


class ArtifactClient(Client):
    dependencies = ("identities",)

    @classmethod
    def build(cls, container):
        return cls(
            container.settings.obot_data_dir / "artifacts"
            if container.data_dir is None
            else container.data_dir / "artifacts",
            container.session_factory,
            container.identities.is_superuser,
            container.settings.obot_artifact_ttl,
            container.settings.obot_artifact_max_bytes,
        )

    def __init__(
        self,
        root: Path,
        session_factory: Callable[[], AsyncSession],
        is_superuser: Callable[[Actor], Awaitable[bool]],
        ttl: float = 3600,
        max_bytes: int = 16 * 1024 * 1024,
    ):
        self.root = root.resolve()
        self.session_factory = session_factory
        self.is_superuser = is_superuser
        self.ttl, self.max_bytes = ttl, max_bytes
        self._leases: Counter[str] = Counter()
        self._lock = asyncio.Lock()

    def _path(self, artifact_id: str) -> Path:
        if len(artifact_id) != 32 or any(c not in "0123456789abcdef" for c in artifact_id):
            raise ArtifactUnavailable("Invalid artifact ID")
        return self.root / artifact_id

    async def create(
        self, actor: Actor, data: bytes, mime: str, *, width: int | None = None, height: int | None = None
    ) -> Artifact:
        from framework.models import ImageArtifact

        if not data or len(data) > self.max_bytes:
            raise ValueError("Artifact is empty or exceeds the size budget")
        artifact = Artifact(
            uuid4().hex,
            mime,
            len(data),
            await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest()),
            time.time() + self.ttl,
            width,
            height,
        )
        path = self._path(artifact.id)

        def write():
            self.root.mkdir(parents=True, exist_ok=True)
            with open(path, "xb") as handle:
                if os.name != "nt":
                    os.chmod(path, 0o600)
                handle.write(data)

        await asyncio.to_thread(write)
        try:
            async with self.session_factory() as session, session.begin():
                session.add(
                    ImageArtifact(
                        id=artifact.id,
                        owner_id=actor.id,
                        mime=mime,
                        size=artifact.size,
                        sha256=artifact.sha256,
                        expires_at=artifact.expires_at,
                        width=width,
                        height=height,
                    )
                )
        except BaseException:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
        return artifact

    @asynccontextmanager
    async def lease(self, actor: Actor, artifact_id: str) -> AsyncIterator[Path]:
        from framework.models import ImageArtifact

        path = self._path(artifact_id)
        async with self._lock:
            async with self.session_factory() as session:
                artifact = await session.get(ImageArtifact, artifact_id)
                if artifact is None or artifact.expires_at <= time.time():
                    raise ArtifactUnavailable("Artifact is missing or expired")
                if actor.id != artifact.owner_id and not await self.is_superuser(actor):
                    raise AccessDenied("Image belongs to another user")
            if not await asyncio.to_thread(path.is_file):
                raise ArtifactUnavailable("Artifact file is missing")
            self._leases[artifact_id] += 1
        try:
            yield path
        finally:
            async with self._lock:
                self._leases[artifact_id] -= 1
                if not self._leases[artifact_id]:
                    del self._leases[artifact_id]

    async def read(self, actor: Actor, artifact_id: str) -> bytes:
        """Read an authorized image without exposing paths or blocking the event loop."""
        async with self.lease(actor, artifact_id) as path:
            return await asyncio.to_thread(path.read_bytes)

    async def cleanup(self, now: float | None = None) -> int:
        from framework.models import ImageArtifact

        now = time.time() if now is None else now
        removed = 0
        async with self._lock:
            async with self.session_factory() as session, session.begin():
                for artifact in (
                    await session.scalars(select(ImageArtifact).where(ImageArtifact.expires_at <= now))
                ).all():
                    if self._leases[artifact.id]:
                        continue
                    await asyncio.to_thread(self._path(artifact.id).unlink, missing_ok=True)
                    await session.delete(artifact)
                    removed += 1
        return removed
