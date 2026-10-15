"""Isolated loopback fixture for browser tests. Never imports real bot configuration."""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import nonebot
import uvicorn
from fastapi import Request


def main():
    with tempfile.TemporaryDirectory(prefix="obot-web-preview-") as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        empty = root / "empty.env"
        empty.write_text("", encoding="utf-8")
        nonebot.init(
            _env_file=empty,
            driver="~fastapi+~httpx+~websockets",
            qq_bots=[],
            superusers=set(),
            localstore_data_dir=str(root / "data"),
            sqlalchemy_database_url=f"sqlite+aiosqlite:///{(root / 'bot.sqlite3').as_posix()}",
            log_level="ERROR",
        )
        from framework.config import Settings
        from framework.contracts import Actor, ExternalIdentity, RequestContext, Role, SceneKind, SceneRef
        from framework.integration.lifecycle import install_lifecycle
        from framework.migrate import upgrade
        from framework.paths import PROJECT_ROOT
        from framework.runtime import configure_services

        services = configure_services(
            Settings(obot_data_dir=root / "data", obot_public_base_url="http://127.0.0.1:8765")
        )
        from framework.models import User

        asyncio.run(upgrade())
        app = nonebot.get_driver().server_app
        actors = {
            "user": Actor("fixture-user", "qq:fixture:person:member"),
            "admin": Actor("fixture-admin", "qq:fixture:person:admin"),
        }

        def context(role):
            actor = actors[role]
            return RequestContext(actor, SceneRef("qq", "fixture", SceneKind.C2C, actor.id), "fixture")

        # This route exists only in this explicit test entry point, on loopback and a disposable DB.
        @app.post("/__test__/issue", include_in_schema=False)
        async def issue(request: Request):
            values = await request.json()
            role = "admin" if values.get("role") == "admin" else "user"
            if values.get("kind") == "bind":
                url = await services.boards.issue(context(role), "browser-oj")
            else:
                url = await services.dashboard.issue(context(role))
            return {"url": url}

        group_scene = SceneRef("qq", "fixture", SceneKind.GROUP, "fixture-room")
        group_actor = None

        async def group_context(admin=True):
            async def allowed(spec):
                return spec.permission == Role.EVERYONE or (admin and spec.permission == Role.GROUP_ADMIN)

            return RequestContext(group_actor, group_scene, "fixture-group", command_allowed=allowed)

        @app.post("/__test__/group-role", include_in_schema=False)
        async def group_role(request: Request):
            values = await request.json()
            admin = values.get("admin") is True
            await services.scene_access.observe(group_actor, group_scene, admin)
            if values.get("command"):
                await services.scene_access.verify(await group_context(admin), values["command"].split()[-1])
            return {"ok": True}

        install_lifecycle(services)
        nonebot.load_from_toml(PROJECT_ROOT / "pyproject.toml")

        async def daily(host, credentials, day, timezone, **kwargs):
            at = int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(timezone), hour=12).timestamp())
            user = {"name": "测试同学", "uid": "2", "register_at": 0}
            return {
                "submissions": [
                    {
                        "user": user,
                        "score": 100,
                        "verdict": "Accepted",
                        "problem_id": "1",
                        "problem_name": "A + B",
                        "at": at,
                    }
                ],
                "rankings": [{"user": user, "accepted": "3", "rank": "1", "unrated": False}],
            }

        async def seed():
            nonlocal group_actor
            async with services.session_factory() as session, session.begin():
                session.add_all([User(id="fixture-user"), User(id="fixture-admin", superuser=True)])
            services.clients.hydro.validate = AsyncMock()
            services.clients.hydro.daily = AsyncMock(side_effect=daily)
            raw = (await services.boards.issue(context("user"), "demo-oj")).split("#")[1]
            await services.boards.submit(raw, "https://oj.example.com", "fixture-admin", "synthetic")
            group_actor = await services.identities.resolve(
                ExternalIdentity("qq", "fixture", "group_member", "fixture-group-admin")
            )
            await services.scene_access.observe(group_actor, group_scene, True)
            ctx = await group_context()
            raw = (await services.boards.issue(ctx, "team-oj")).split("#")[1]
            await services.boards.submit(raw, "https://oj.example.com", "fixture-admin", "synthetic")
            await services.boards.confirm(ctx, "team-oj")

        nonebot.get_driver().on_startup(seed)
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
