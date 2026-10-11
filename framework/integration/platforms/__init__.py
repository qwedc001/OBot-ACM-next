"""Explicit platform loading; no QQ assumptions in common request handling."""

import inspect
from importlib import import_module

from nonebot import get_driver
from nonebot.adapters import Bot, Event

from ...contracts import RequestContext
from ...runtime import Services
from .base import Incoming, Platform

_platforms: dict[str, Platform] = {}


def register_platform(platform: Platform) -> None:
    if platform.name in _platforms:
        raise ValueError(f"Platform already registered: {platform.name}")
    get_driver().register_adapter(platform.adapter_type)
    _platforms[platform.name] = platform


def load_platforms(names: tuple[str, ...]) -> None:
    for name in names:
        if not name.isidentifier():
            raise ValueError("Platform module names must be identifiers")
        module = import_module(f"{__name__}.{name}")
        implementations = [
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if cls.__module__ == module.__name__ and issubclass(cls, Platform) and not inspect.isabstract(cls)
        ]
        if len(implementations) != 1:
            raise ValueError(f"Platform module requires exactly one implementation: {name}")
        implementation = implementations[0]
        existing = _platforms.get(implementation.adapter_type.get_name())
        if existing is not None:
            if type(existing) is not implementation:
                raise ValueError("Conflicting platform implementation")
            continue
        register_platform(implementation())


def registered_platforms() -> tuple[Platform, ...]:
    return tuple(_platforms.values())


def get_platform(bot: Bot) -> Platform:
    name = bot.adapter.get_name()
    if name not in _platforms:
        raise ValueError(f"Unsupported platform: {name}")
    return _platforms[name]


def supports_bot(bot: Bot) -> bool:
    return bot.adapter.get_name() in _platforms


async def make_context(services: Services, bot: Bot, event: Event) -> RequestContext:
    incoming = get_platform(bot).normalize(bot, event)
    actor = await services.identities.resolve(incoming.identity)
    return RequestContext(actor, incoming.scene, incoming.event_id)


__all__ = [
    "Incoming",
    "Platform",
    "get_platform",
    "load_platforms",
    "make_context",
    "register_platform",
    "registered_platforms",
]
