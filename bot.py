"""Entry point for the configured NoneBot platforms and application plugins."""

import nonebot

from framework.catalog import catalog
from framework.config import Settings
from framework.integration.lifecycle import install_lifecycle
from framework.runtime import configure_services

nonebot.init()
driver = nonebot.get_driver()
separators = driver.config.command_sep
if not separators:
    raise ValueError("COMMAND_SEP must contain a nonempty punctuation separator")
catalog.configure_separator("." if "." in separators else sorted(separators)[0])
services = configure_services(nonebot.get_plugin_config(Settings))
install_lifecycle(services)
nonebot.load_from_toml("pyproject.toml")
if not catalog.commands:
    raise RuntimeError("OBot core commands failed to load")

if __name__ == "__main__":
    nonebot.run()
