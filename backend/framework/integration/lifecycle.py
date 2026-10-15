import time
from collections import OrderedDict

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from web.app import install_web

from ..runtime import Services
from .api import build_internal_router
from .command_context import observe_delivery
from .platforms import get_platform, load_platforms, registered_platforms, supports_bot


def install_lifecycle(services: Services) -> None:
    driver = get_driver()
    load_platforms(services.settings.obot_platforms)

    async def startup():
        for platform in registered_platforms():
            services.clients.reply.register(
                platform.name, platform.build_message, platform.send_to, supported=platform.supported_segments
            )
        services.clients.reply.on_delivery = observe_delivery
        await services.start()

    driver.on_startup(startup)
    driver.on_shutdown(services.close)
    driver.server_app.include_router(build_internal_router(services))
    install_web(driver.server_app, services)
    # Bounded, process-local duplicate suppression, not a durable event ledger.
    seen: OrderedDict[tuple[str, str, str, str, str], float] = OrderedDict()

    @event_preprocessor
    async def receive(bot: Bot, event: Event) -> None:
        if event.get_type() != "message" or not supports_bot(bot):
            return
        now = time.monotonic()
        while seen and (next(iter(seen.values())) < now - 120 or len(seen) >= 4096):
            seen.popitem(last=False)
        incoming = get_platform(bot).normalize(bot, event)
        key = (
            bot.adapter.get_name(),
            bot.self_id,
            incoming.scene.key,
            event.get_event_name(),
            incoming.event_id,
        )
        if key in seen:
            raise IgnoredException("Duplicate message event")
        seen[key] = now
        services.telemetry.last_received_at = time.time()

    @driver.on_bot_connect
    async def connected(bot: Bot) -> None:
        services.telemetry.connected_bots.add(f"{bot.adapter.get_name()}:{bot.self_id}")

    @driver.on_bot_disconnect
    async def disconnected(bot: Bot) -> None:
        services.telemetry.connected_bots.discard(f"{bot.adapter.get_name()}:{bot.self_id}")
