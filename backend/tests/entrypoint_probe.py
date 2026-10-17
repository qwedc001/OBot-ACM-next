"""Start the deployed module with an existing schema and isolated configuration."""

import asyncio
import hashlib
import json
import runpy
import sys
import tempfile
import time
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
        if "--failed-plugin" in sys.argv:
            original = nonebot.get_available_plugin_names
            nonebot.get_available_plugin_names = lambda: original() | {"synthetic_failed_plugin"}
            try:
                runpy.run_module("backend.bot", run_name="entrypoint_check")
            except RuntimeError as error:
                assert "Configured plugins failed to load: synthetic_failed_plugin" in str(error)
            else:
                raise AssertionError("Partially loaded application must not become healthy")
            print(json.dumps({"rejected_failed_plugin": True}))
            return
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
            await greenlet_spawn(migrate.upgrade, config, "0004")
        async with services.session_factory() as session, session.begin():
            session.add(User(id="existing-admin", superuser=True))
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO bot_web_sessions (digest,user_id,identity_key,expires_at,created_at) VALUES (:digest,:user,:identity,:expires,:created)"
                ),
                {
                    "digest": hashlib.sha256(("a" * 43).encode()).hexdigest(),
                    "user": "existing-admin",
                    "identity": "qq:app:c2c_user:existing",
                    "expires": time.time() + 600,
                    "created": time.time(),
                },
            )
        await upgrade()
        driver = scope["driver"]
        await driver._lifespan.startup()
        try:
            assert services.started
            account = await services.dashboard.authenticate("a" * 43)
            assert account and account.superuser and account.group_scope is None
            assert "dashboard" in {spec.id for spec in services.catalog.commands}
            async with services.session_factory() as session:
                assert (await session.get(User, "existing-admin")).superuser
                assert len(list(await session.scalars(select(User)))) == 1
                assert (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one() == "0005"
        finally:
            await driver._lifespan.shutdown()
            await engine.dispose()
        print(json.dumps({"ok": True, "module_entrypoint": True, "upgrade_existing": True}))


if __name__ == "__main__":
    asyncio.run(main())
