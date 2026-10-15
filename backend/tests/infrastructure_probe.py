"""Fresh schema and direct official plugin lifecycle checks in a separate process."""

import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import nonebot


async def main():
    mode = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="framework-plugins-") as temporary:
        root = Path(temporary)
        data = root / "data"
        config = {}
        target = data / "bot.sqlite3"
        if mode == "localstore":
            target = root / "localstore" / "bot.sqlite3"
            config["localstore_data_dir"] = str(target.parent)
        elif mode == "orm":
            target = root / "orm" / "bot.sqlite3"
            config["sqlalchemy_database_url"] = f"sqlite+aiosqlite:///{target.as_posix()}"
        target.parent.mkdir(parents=True)
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(_env_file=empty, driver="~fastapi", log_level="ERROR", **config)
        from framework.catalog import CommandCatalog
        from framework.config import Settings
        from framework.integration.infrastructure import load_infrastructure
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.runtime import Services

        data_dir, get_session = load_infrastructure(Settings(obot_data_dir=data))
        # Register models and migrate without first constructing any application service.
        await upgrade()
        services = Services(
            Settings(obot_data_dir=data, obot_platforms=()),
            CommandCatalog(),
            session_factory=get_session,
            data_dir=data_dir,
        )
        install_lifecycle(services)
        from nonebot_plugin_apscheduler import scheduler
        from sqlalchemy import inspect, select

        from framework.models import User

        scheduler.add_job(lambda: None, "cron", hour=0, id="other-plugin")
        driver = nonebot.get_driver()
        await driver._lifespan.startup()
        try:
            assert scheduler.running and services.started
            assert services.clients.artifacts.root == data_dir / "artifacts"
            async with get_session() as session:
                assert Path(session.bind.url.database) == target
                async with session.bind.connect() as connection:
                    tables = await connection.run_sync(
                        lambda connection: set(inspect(connection).get_table_names())
                    )
                assert {"bot_users", "bot_identities", "bot_artifacts", "alembic_version"} <= tables
                assert not {"records", "operations"} & tables
                assert not (await session.scalars(select(User))).all()
            fired = asyncio.Event()

            async def handler():
                fired.set()

            scheduler.add_job(
                handler,
                "date",
                run_date=datetime.now(UTC) + timedelta(milliseconds=50),
                id="direct-plugin-job",
            )
            await asyncio.wait_for(fired.wait(), 3)
            await services.close()
            assert scheduler.running and scheduler.get_job("other-plugin") is not None
        finally:
            await driver._lifespan.shutdown()
        assert not scheduler.running
        print(json.dumps({"ok": True, "mode": mode, "fresh_schema": True, "direct_scheduler": True}))


if __name__ == "__main__":
    asyncio.run(main())
