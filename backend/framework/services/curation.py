"""Shared EP content operations, with one ORM transaction per workflow.

File-based algorithms remain synchronous and run in AnyIO worker threads. Repository
queries return to the ORM event loop, so neither file decoding nor SQL blocks the bot.
The lock covers compare/write/review together, rather than individual JSON writes.
"""

import asyncio
from contextlib import asynccontextmanager
from functools import partial

from anyio import to_thread

from ..curation.contestlist.store import ContestStore
from ..curation.pickone.store import PickOneStore
from ..curation.repository import Repository
from .base import Service


class CurationService(Service):
    @classmethod
    def build(cls, container):
        return cls(container.session_factory, container.data_dir or container.settings.obot_data_dir)

    def __init__(self, sessions, data_dir):
        self.sessions = sessions
        self.pickone = PickOneStore(data_dir / "curation" / "Pick-One")
        self.contestlist = ContestStore(
            data_dir / "curation" / "Contest-List-Renderer" / "manual_contests.json"
        )
        self._lock = asyncio.Lock()
        self._ready = False

    def _prepare(self):
        self.pickone.lib_dir.mkdir(parents=True, exist_ok=True)
        self.contestlist.data_path.parent.mkdir(parents=True, exist_ok=True)

    async def start(self):
        if not self._ready:
            await to_thread.run_sync(self._prepare)
            self._ready = True

    @asynccontextmanager
    async def transaction(self):
        """HTTP dependencies and bot operations share the same serialization boundary."""
        async with self._lock:
            await self.start()
            async with self.sessions() as session, session.begin():
                yield Repository(await session.connection())

    async def run(self, operation, *args, **kwargs):
        """Run a domain operation accepting (repository, *args) from an async caller."""
        async with self.transaction() as repository:
            return await to_thread.run_sync(partial(operation, repository, *args, **kwargs))
