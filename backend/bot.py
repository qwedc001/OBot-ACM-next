"""Entry point for the configured platforms. Run from root: python -m backend.bot."""

import nonebot

from framework.catalog import catalog
from framework.config import Settings
from framework.integration.lifecycle import install_lifecycle
from framework.paths import PROJECT_ROOT
from framework.runtime import configure_services

nonebot.init()
from patches import install as install_adapter_patches  # noqa: E402

install_adapter_patches()
driver = nonebot.get_driver()
separators = driver.config.command_sep
if not separators:
    raise ValueError("COMMAND_SEP must contain a nonempty punctuation separator")
catalog.configure_separator("." if "." in separators else sorted(separators)[0])
services = configure_services(nonebot.get_plugin_config(Settings))
install_lifecycle(services)
nonebot.load_from_toml(PROJECT_ROOT / "pyproject.toml")
failed_plugins = sorted(
    name for name in nonebot.get_available_plugin_names() if nonebot.get_plugin(name) is None
)
if failed_plugins:
    raise RuntimeError("Configured plugins failed to load: " + ", ".join(failed_plugins))
if not catalog.commands:
    raise RuntimeError("OBot core commands failed to load")

if __name__ == "__main__":
    nonebot.run()
