from importlib import import_module

import pytest

from framework.catalog import CommandCatalog
from framework.clients import Client
from framework.config import Settings
from framework.runtime import Services
from framework.services import Service


def package(tmp_path, monkeypatch, name, modules):
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("events = []", encoding="utf-8")
    for module, source in modules.items():
        (root / f"{module}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def implementation(name, dependencies=(), *, fail=False):
    dependency = f"container.get({dependencies[0]!r})" if dependencies else "None"
    return f"""
from framework.services import Service
from . import events
class Concrete(Service):
    dependencies = {dependencies!r}
    @classmethod
    def build(cls, container):
        instance = cls()
        instance.dependency = {dependency}
        return instance
    async def start(self):
        events.append('start:{name}')
        if {fail!r}: raise RuntimeError('start failed')
    async def close(self):
        events.append('close:{name}')
"""


async def test_discovery_dependency_order_reverse_shutdown_and_restart(tmp_path, monkeypatch, database):
    name = package(
        tmp_path,
        monkeypatch,
        "discovery_test",
        {
            "consumer": implementation("consumer", ("provider",)),
            "provider": implementation("provider"),
            "new_module": implementation("new_module"),
            "helpers": "class Helper: pass\nfrom framework.services import Service\nclass Abstract(Service): pass",
            "imported": "from .provider import Concrete",
        },
    )
    services = Services(
        Settings(), CommandCatalog(), session_factory=database, service_package=name, client_package=None
    )
    events = import_module(name).events
    await services.start()
    assert services.consumer.dependency is services.provider
    assert isinstance(services.new_module, Service)
    assert set(services._instances) == {"consumer", "provider", "new_module"}
    assert events == ["start:provider", "start:consumer", "start:new_module"]
    original = services.provider
    await services.start()
    await services.close()
    await services.close()
    assert events[-3:] == ["close:new_module", "close:consumer", "close:provider"]
    await services.start()
    assert services.provider is not original
    await services.close()


def test_missing_dependency_and_cycles_fail_explicitly(tmp_path, monkeypatch, database):
    name = package(
        tmp_path,
        monkeypatch,
        "cycle_test",
        {
            "a": implementation("a", ("b",)),
            "b": implementation("b", ("a",)),
        },
    )
    services = Services(Settings(), session_factory=database, service_package=name, client_package=None)
    with pytest.raises(ValueError, match="Cyclic"):
        services.get("a")
    assert not services._building
    monkeypatch.setattr(services._types["b"], "dependencies", ("missing",))
    with pytest.raises(KeyError, match="Unknown"):
        services.get("a")


async def test_failed_start_closes_dependents_and_dependencies(tmp_path, monkeypatch, database):
    name = package(
        tmp_path,
        monkeypatch,
        "failure_test",
        {
            "dependent": implementation("dependent", ("provider",), fail=True),
            "provider": implementation("provider"),
        },
    )
    services = Services(Settings(), session_factory=database, service_package=name, client_package=None)
    with pytest.raises(RuntimeError, match="start failed"):
        await services.start()
    assert import_module(name).events == [
        "start:provider",
        "start:dependent",
        "close:dependent",
        "close:provider",
    ]
    assert not services.started and not services._instances


def test_ambiguous_service_module_is_rejected(tmp_path, monkeypatch, database):
    name = package(
        tmp_path,
        monkeypatch,
        "ambiguous_test",
        {
            "multiple": implementation("one") + "\nclass Another(Concrete): pass\n",
        },
    )
    with pytest.raises(ValueError, match="Multiple concrete services"):
        Services(Settings(), session_factory=database, service_package=name, client_package=None)


def test_invalid_factory_result_is_rejected(tmp_path, monkeypatch, database):
    name = package(tmp_path, monkeypatch, "invalid_factory_test", {"item": implementation("item")})
    services = Services(Settings(), session_factory=database, service_package=name, client_package=None)
    monkeypatch.setattr(services._types["item"], "build", classmethod(lambda cls, container: object()))
    with pytest.raises(TypeError, match="invalid instance"):
        services.get("item")
    assert not services._instances and not services._building


async def test_shutdown_error_does_not_skip_other_services(tmp_path, monkeypatch, database):
    name = package(
        tmp_path,
        monkeypatch,
        "shutdown_test",
        {
            "dependent": implementation("dependent", ("provider",)).replace(
                "events.append('close:dependent')", "raise RuntimeError('close failed')"
            ),
            "provider": implementation("provider"),
        },
    )
    services = Services(Settings(), session_factory=database, service_package=name, client_package=None)
    await services.start()
    with pytest.raises(ExceptionGroup, match="shutdown failed"):
        await services.close()
    assert import_module(name).events[-1] == "close:provider"
    assert not services._instances and not services.started


async def test_client_namespace_and_cross_kind_dependency_lifecycle(tmp_path, monkeypatch, database):
    service_package = package(
        tmp_path,
        monkeypatch,
        "cross_services",
        {
            "shared": implementation("service", ("clients.shared",)),
        },
    )
    client_package = package(
        tmp_path,
        monkeypatch,
        "cross_clients",
        {
            "shared": implementation("client")
            .replace("services import Service", "clients import Client")
            .replace("Concrete(Service)", "Concrete(Client)"),
            "wrong_kind": implementation("ignored service"),
        },
    )
    # Both packages record into one log so ordering across their boundary is visible.
    events = import_module(service_package).events
    import_module(client_package).events = events
    services = Services(
        Settings(),
        CommandCatalog(),
        session_factory=database,
        service_package=service_package,
        client_package=client_package,
    )
    await services.start()
    assert isinstance(services.shared, Service) and not isinstance(services.shared, Client)
    assert isinstance(services.clients.shared, Client) and not isinstance(services.clients.shared, Service)
    assert services.shared.dependency is services.clients.shared
    assert services.get("clients.shared") is services.clients.shared
    with pytest.raises(AttributeError):
        _ = services.clients.wrong_kind
    await services.close()
    assert events == ["start:client", "start:service", "close:service", "close:client"]
