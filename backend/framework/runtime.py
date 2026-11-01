"""Discover application services, resolve dependencies, and manage their lifecycle."""

from collections.abc import Callable
from importlib import import_module
from inspect import getmembers, isabstract, isclass
from pathlib import Path
from pkgutil import iter_modules

from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import CommandCatalog, catalog
from .clients import Client
from .components import Component
from .config import Settings
from .services import Service
from .telemetry import Telemetry


class Services:
    def __init__(
        self,
        settings: Settings,
        commands: CommandCatalog | None = None,
        *,
        session_factory: Callable[[], AsyncSession],
        data_dir: Path | None = None,
        service_package: str = "framework.services",
        client_package: str | None = "framework.clients",
    ):
        self.settings, self.session_factory, self.data_dir = settings, session_factory, data_dir
        self.catalog = commands if commands is not None else catalog
        self.telemetry = Telemetry()
        self._types = self._discover(service_package, Service)
        if client_package:
            self._types.update(
                {f"clients.{name}": cls for name, cls in self._discover(client_package, Client).items()}
            )
        self.clients = _Clients(self)
        self._instances: dict[str, Component] = {}
        self._building = set()
        self.started = False

    @staticmethod
    def _discover(package_name: str, base_type: type[Component]) -> dict[str, type[Component]]:
        package = import_module(package_name)
        discovered = {}
        for item in sorted(iter_modules(package.__path__), key=lambda item: item.name):
            if item.name.startswith("_"):
                continue
            module = import_module(f"{package_name}.{item.name}")
            candidates = [
                cls
                for _, cls in getmembers(module, isclass)
                if cls.__module__ == module.__name__ and issubclass(cls, base_type) and not isabstract(cls)
            ]
            if len(candidates) > 1:
                raise ValueError(f"Multiple concrete services or clients in module: {item.name}")
            if candidates:
                discovered[item.name] = candidates[0]
        return discovered

    def get(self, name: str):
        if name in self._instances:
            return self._instances[name]
        if name not in self._types:
            raise KeyError(f"Unknown service: {name}")
        if name in self._building:
            raise ValueError(f"Cyclic service dependency: {name}")
        self._building.add(name)
        try:
            service_type = self._types[name]
            for dependency in service_type.dependencies:
                self.get(dependency)
            instance = service_type.build(self)
            if not isinstance(instance, service_type):
                raise TypeError(f"Service factory returned an invalid instance: {name}")
            self._instances[name] = instance
            return instance
        finally:
            self._building.remove(name)

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self._types:
            raise AttributeError(name)
        return self.get(name)

    async def start(self):
        if self.started:
            return
        try:
            for name in self._types:
                self.get(name)
            for instance in self._instances.values():
                await instance.start()
            self.catalog.freeze()
            self.started = True
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if not self._instances:
            return
        errors = []
        try:
            for instance in reversed(list(self._instances.values())):
                try:
                    await instance.close()
                except Exception as error:
                    errors.append(error)
        finally:
            self._instances.clear()
            self.started = False
        if errors:
            raise ExceptionGroup("Service shutdown failed", errors)

    def snapshot(self):
        from . import __version__

        return {
            "version": __version__,
            "ready": self.started,
            "command_count": len(self.catalog.commands),
            "jobs": self.clients.jobs.snapshot(),
            "telemetry": self.telemetry.snapshot(),
        }


class _Clients:
    """A separate namespace over the same dependency-ordered component lifecycle."""

    def __init__(self, container: Services):
        self._container = container

    def __getattr__(self, name: str):
        key = f"clients.{name}"
        if name.startswith("_") or key not in self._container._types:
            raise AttributeError(name)
        return self._container.get(key)


_services: Services | None = None


def configure_services(settings: Settings) -> Services:
    from .integration.infrastructure import load_infrastructure

    global _services
    if _services is not None:
        raise RuntimeError("Framework services are already configured")
    data_dir, get_session = load_infrastructure(settings)
    _services = Services(settings, session_factory=get_session, data_dir=data_dir)
    return _services


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Framework services are not configured")
    return _services
