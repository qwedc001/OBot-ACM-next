"""Apply the official ORM plugin's Alembic migrations before starting the bot."""

import asyncio
from importlib import import_module


async def upgrade():
    import_module("framework.models")
    from nonebot_plugin_orm import AlembicConfig, get_session, migrate
    from sqlalchemy.util import greenlet_spawn

    async with get_session() as session:
        engine = session.bind
    try:
        with AlembicConfig() as config:
            await greenlet_spawn(migrate.upgrade, config, "head")
    finally:
        await engine.dispose()


async def main():
    import nonebot

    from .config import Settings
    from .integration.infrastructure import load_infrastructure

    nonebot.init(log_level="ERROR")
    load_infrastructure(nonebot.get_plugin_config(Settings))
    await upgrade()


if __name__ == "__main__":
    asyncio.run(main())
