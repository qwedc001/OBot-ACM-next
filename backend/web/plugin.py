"""EP-style automatic API plugin discovery; adding a tool does not change the app."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules

from fastapi import APIRouter


@dataclass(frozen=True)
class WebPlugin:
    slug: str
    name: str
    summary: str
    build_router: Callable[..., APIRouter]
    superuser: bool = False

    def manifest(self):
        return {"slug": self.slug, "name": self.name, "summary": self.summary, "superuser": self.superuser}


def discover_plugins(package_name="web.plugins"):
    package = import_module(package_name)
    plugins = []
    for item in sorted(iter_modules(package.__path__), key=lambda value: value.name):
        if item.name.startswith("_"):
            continue
        plugin = getattr(import_module(package_name + "." + item.name), "PLUGIN", None)
        if not isinstance(plugin, WebPlugin) or plugin.slug != item.name:
            raise ValueError("Invalid web plugin declaration: " + item.name)
        plugins.append(plugin)
    return tuple(plugins)
