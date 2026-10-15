"""Start the deployed module with an existing schema and isolated configuration."""

import asyncio
import json
import runpy
import tempfile
from pathlib import Path

import nonebot


async def main():
    with tempfile.TemporaryDirectory(prefix="obot-entrypoint-") as directory:
        root = Path(directory)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            log_level="ERROR",
            localstore_data_dir=str(root / "data"),
            sqlalchemy_database_url=f"sqlite+aiosqlite:///{(root / 'bot.sqlite3').as_posix()}",
        )
        scope = runpy.run_module("backend.bot", run_name="entrypoint_check")
        services = scope["services"]
        from nonebot_plugin_orm import AlembicConfig, migrate
        from sqlalchemy import select, text
        from sqlalchemy.util import greenlet_spawn

        from framework.migrate import upgrade
        from framework.models import User

        async with services.session_factory() as session:
            engine = session.bind
        with AlembicConfig() as config:
            await greenlet_spawn(migrate.upgrade, config, "0002")
        async with services.session_factory() as session, session.begin():
            session.add(User(id="existing-admin", superuser=True))
        await upgrade()
        driver = scope["driver"]
        await driver._lifespan.startup()
        try:
            assert services.started
            assert "dashboard" in {spec.id for spec in services.catalog.commands}
            async with services.session_factory() as session:
                assert (await session.get(User, "existing-admin")).superuser
                assert len(list(await session.scalars(select(User)))) == 1
                assert (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one() == "0004"
        finally:
            await driver._lifespan.shutdown()
            await engine.dispose()
        print(json.dumps({"ok": True, "module_entrypoint": True, "upgrade_existing": True}))


if __name__ == "__main__":
    asyncio.run(main())
