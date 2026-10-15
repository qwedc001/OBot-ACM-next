import asyncio
import threading

import pytest

from framework.catalog import CommandCatalog
from framework.clients.renderer import RenderedImage
from framework.config import Settings
from framework.contracts import RequestContext, SceneKind, SceneRef
from framework.runtime import Services

from .conftest import USER


@pytest.mark.parametrize(
    "first_method,second_method",
    [("render", "render"), ("render", "render_pages"), ("render_pages", "render")],
)
async def test_timed_out_native_draw_cannot_overlap_next_draw(
    tmp_path, monkeypatch, first_method, second_method, database
):
    services = Services(
        Settings(obot_data_dir=tmp_path, obot_job_timeout=0.25), CommandCatalog(), session_factory=database
    )
    started, release, second_attempt = threading.Event(), threading.Event(), threading.Event()
    overlap = False
    drawing = False

    class Backend:
        def render(self, model):
            nonlocal overlap, drawing
            overlap |= drawing
            drawing = True
            if model["slow"]:
                started.set()
                release.wait(5)
            drawing = False
            return RenderedImage(b"png", "image/png", 1, 1)

    await services.start()
    services.clients.renderer.register("test", Backend())
    # Observe the second native invocation reaching the draw boundary. An
    # arbitrary short sleep did not prove it ran before the first was released.
    native_draw = getattr(
        services.clients.renderer, "_draw_pages" if second_method == "render_pages" else "_draw"
    )

    def observed(view, model):
        if not model["slow"]:
            second_attempt.set()
        return native_draw(view, model)

    monkeypatch.setattr(
        services.clients.renderer, "_draw_pages" if second_method == "render_pages" else "_draw", observed
    )
    context = RequestContext(USER, SceneRef("qq", "app", SceneKind.C2C, "user"), "event")
    try:
        with pytest.raises(TimeoutError):
            await getattr(services.clients.renderer, first_method)(context.actor, "test", {"slow": True})
        assert started.is_set()
        # The second job tests serialization, not filesystem speed under a
        # 60ms deadline. Give its authorized artifact persistence normal time.
        services.clients.jobs.timeout = 3
        second = asyncio.create_task(
            getattr(services.clients.renderer, second_method)(context.actor, "test", {"slow": False})
        )
        assert await asyncio.to_thread(second_attempt.wait, 2)
        await asyncio.sleep(0.02)
        assert not overlap and not second.done()
        release.set()
        artifact = await second
        assert (artifact[0] if isinstance(artifact, tuple) else artifact).width == 1
        assert not overlap
    finally:
        release.set()
        await services.close()


async def test_native_render_keeps_simple_plugin_service_responsive(tmp_path, database):
    services = Services(Settings(obot_data_dir=tmp_path), CommandCatalog(), session_factory=database)
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    class SlowBackend:
        def render(self, model):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3), "Drawing blocked the event loop"
            return RenderedImage(b"png", "image/png", 1, 1)

    await services.start()
    services.clients.renderer.register("slow", SlowBackend())
    drawing = asyncio.create_task(services.clients.renderer.render(USER, "slow", {}))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await asyncio.wait_for(services.maintenance.ping(), 0.5) == "pong"
        assert not drawing.done() and not release.is_set()
    finally:
        release.set()
        try:
            await drawing
        finally:
            await services.close()
