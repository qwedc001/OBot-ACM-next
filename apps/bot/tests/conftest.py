import tempfile
from pathlib import Path

import nonebot
import pytest_asyncio
from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Load the official ORM model API without reading bot credentials.
with tempfile.TemporaryDirectory(prefix="obot-test-env-") as directory:
    env = Path(directory) / ".env"
    env.write_text("", encoding="utf-8")
    nonebot.init(_env_file=env, driver="~fastapi", qq_bots=[], log_level="ERROR")

from framework.contracts import Actor, Role, SceneKind  # noqa: E402
from framework.models import Model, User  # noqa: E402

ROOT = Actor("root", "test:root")
USER = Actor("user", "qq:bot:c2c_user:member")


def access(role=Role.EVERYONE, scene=SceneKind.GROUP):
    async def check(spec):
        return (
            role == Role.SUPERUSER
            or spec.permission == Role.EVERYONE
            or (role == Role.GROUP_ADMIN and spec.permission == Role.GROUP_ADMIN)
        ) and (role == Role.SUPERUSER or scene in spec.scopes)

    return check


@pytest_asyncio.fixture
async def database(tmp_path):
    engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(tmp_path / "test.sqlite3")))

    @event.listens_for(engine.sync_engine, "connect")
    def pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all([User(id=ROOT.id, superuser=True), User(id=USER.id)])
    try:
        yield factory
    finally:
        await engine.dispose()
