"""Async passive replies and proactive delivery through adapter registrations."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from framework.messages import Image, Keyboard, Markdown, Message, ReplyContent, SendTarget, Text

from .base import Client

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

type Builder = Callable[["Event | SendTarget", Message, bool | None], Awaitable[Any]]
type Sender = Callable[["Bot", SendTarget, Any], Awaitable[Any]]
type DeliveryObserver = Callable[[float, float, bool], None]


class DeliveryFailed(Exception):
    """Safe delivery failure, without SDK response bodies or credentials."""


class UnsupportedContent(ValueError):
    """The selected platform cannot represent this message part."""


@dataclass(frozen=True)
class _Adapter:
    build: Builder
    send_to: Sender
    supported: frozenset[type]


class ReplyClient(Client):
    @classmethod
    def build(cls, container):
        return cls()

    def __init__(self):
        self._adapters: dict[str, _Adapter] = {}
        self.on_delivery: DeliveryObserver | None = None

    def _report(self, started: float, delivered: bool) -> None:
        if self.on_delivery is not None:
            self.on_delivery(started, time.perf_counter(), delivered)

    def register(
        self,
        adapter_name: str,
        builder: Builder,
        sender: Sender,
        *,
        supported: frozenset[type] = frozenset({Text, Image, Markdown, Keyboard}),
    ) -> None:
        if not adapter_name or adapter_name in self._adapters:
            raise ValueError("Reply adapter name is empty or already registered")
        self._adapters[adapter_name] = _Adapter(builder, sender, frozenset(supported))

    def supports(self, adapter_name: str, part_type: type) -> bool:
        adapter = self._adapters.get(adapter_name)
        return adapter is not None and part_type in adapter.supported

    def _adapter(self, name: str) -> _Adapter:
        if name not in self._adapters:
            raise ValueError(f"Unsupported reply adapter: {name}")
        return self._adapters[name]

    @staticmethod
    def _message(content: ReplyContent) -> Message:
        return content if isinstance(content, Message) else Message(content)

    def _checked_message(self, adapter: _Adapter, content: ReplyContent) -> Message:
        message = self._message(content)
        for part in message.parts:
            if type(part) not in adapter.supported:
                raise UnsupportedContent(f"Platform does not support {type(part).__name__}")
        return message

    async def send(
        self,
        content: ReplyContent,
        *,
        bot: "Bot | None" = None,
        event: "Event | None" = None,
        at_sender: bool | None = None,
    ):
        """Send a passive reply; omitted bot/event come from the active matcher."""
        from nonebot.matcher import current_bot, current_event

        try:
            bot = bot if bot is not None else current_bot.get()
            event = event if event is not None else current_event.get()
        except LookupError:
            raise ValueError("Passive replies require bot/event or an active matcher") from None
        adapter = self._adapter(bot.adapter.get_name())
        started, delivered = time.perf_counter(), False
        try:
            native = await adapter.build(event, self._checked_message(adapter, content), at_sender)
            try:
                result = await bot.send(event, native)
            except Exception:
                raise DeliveryFailed("Message delivery failed") from None
            delivered = True
            return result
        finally:
            self._report(started, delivered)

    async def finish(
        self,
        content: ReplyContent | None = None,
        *,
        bot: "Bot | None" = None,
        event: "Event | None" = None,
        at_sender: bool | None = None,
    ) -> NoReturn:
        """Send if supplied, then terminate this matcher using NoneBot's signal."""
        from nonebot.exception import FinishedException

        if content is not None:
            await self.send(content, bot=bot, event=event, at_sender=at_sender)
        raise FinishedException

    async def send_to(self, content: ReplyContent, target: SendTarget, *, bot: "Bot | None" = None):
        """Deliver without an event; only infer a bot when the choice is unique."""
        if bot is None:
            from nonebot import get_bots

            matches = [
                value
                for value in get_bots().values()
                if value.adapter.get_name() == target.adapter
                and (target.bot_id is None or value.self_id == target.bot_id)
            ]
            if len(matches) != 1:
                raise ValueError("Proactive delivery requires one matching bot or an explicit bot")
            bot = matches[0]
        if bot.adapter.get_name() != target.adapter or (
            target.bot_id is not None and bot.self_id != target.bot_id
        ):
            raise ValueError("Bot does not match the send target")
        adapter = self._adapter(target.adapter)
        started, delivered = time.perf_counter(), False
        try:
            native = await adapter.build(target, self._checked_message(adapter, content), False)
            try:
                result = await adapter.send_to(bot, target, native)
            except Exception:
                raise DeliveryFailed("Message delivery failed") from None
            delivered = True
            return result
        finally:
            self._report(started, delivered)
