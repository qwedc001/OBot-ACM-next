"""Load official plugins and configure their existing APIs."""

from pathlib import Path

import nonebot
from sqlalchemy import URL

from ..config import Settings


def load_infrastructure(settings: Settings):
    config = nonebot.get_driver().config
    if getattr(config, "localstore_data_dir", None) is None:
        config.localstore_data_dir = settings.obot_data_dir.resolve()
    nonebot.require("nonebot_plugin_localstore")
    from nonebot_plugin_localstore import get_data_dir, get_data_file

    data_dir = get_data_dir(None)
    data_dir.mkdir(parents=True, exist_ok=True)
    if not getattr(config, "sqlalchemy_database_url", None):
        config.sqlalchemy_database_url = URL.create(
            "sqlite+aiosqlite", database=str(get_data_file(None, "bot.sqlite3"))
        ).render_as_string(hide_password=False)
    config.alembic_startup_check = True
    config.alembic_version_locations = Path(__file__).resolve().parents[2] / "migrations"
    nonebot.require("nonebot_plugin_orm")
    nonebot.require("nonebot_plugin_apscheduler")
    from nonebot_plugin_orm import get_session

    async def dispose():
        async with get_session() as session:
            engine = session.bind
        await engine.dispose()

    nonebot.get_driver().on_shutdown(dispose)
    return data_dir, get_session
