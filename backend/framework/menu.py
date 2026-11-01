"""Trusted operator CLI for the QQ private menu."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import nonebot


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--include-admin", action="store_true", help="Expose labelled admin entries to all users"
    )
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    nonebot.init(log_level="ERROR", driver="~fastapi+~httpx+~websockets")
    from nonebot.adapters.qq import Bot
    from nonebot.adapters.qq.config import Config

    from .catalog import catalog
    from .config import Settings
    from .integration.lifecycle import install_lifecycle
    from .paths import PROJECT_ROOT
    from .runtime import configure_services

    driver = nonebot.get_driver()
    separators = driver.config.command_sep
    catalog.configure_separator("." if "." in separators else sorted(separators)[0])
    services = configure_services(nonebot.get_plugin_config(Settings))
    install_lifecycle(services)
    nonebot.load_from_toml(PROJECT_ROOT / "pyproject.toml")
    start = "/" if "/" in driver.config.command_start else sorted(driver.config.command_start)[0]
    try:
        if not args.apply:
            preview = services.menus.preview(include_admin=args.include_admin, command_start=start)
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return
        config = nonebot.get_plugin_config(Config)
        if len(config.qq_bots) != 1:
            raise ValueError("Menu publishing requires exactly one configured QQ bot")
        info = config.qq_bots[0]
        bot = Bot(nonebot.get_adapter("QQ"), info.id, info)
        backup = args.backup or services.data_dir / "menu-backups" / (
            "qq-menu" + datetime.now(UTC).strftime("-%Y%m%dT%H%M%S%fZ.json")
        )
        result = await services.menus.sync(
            bot, backup=backup, include_admin=args.include_admin, command_start=start
        )
        print(json.dumps(result))
    finally:
        await services.close()


if __name__ == "__main__":
    asyncio.run(main())
