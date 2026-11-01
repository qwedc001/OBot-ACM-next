"""Offline OpenAPI JSON schema dumper for OBot FastAPI application."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "bot"))

os.environ.setdefault("ENVIRONMENT", "dev")

import nonebot  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.openapi.utils import get_openapi  # noqa: E402


class DummyServices:
    class DummySettings:
        obot_frontend_dist = None

    settings = DummySettings()

    async def account(self, request, csrf=False):
        return None


def generate_openapi():
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()

    from web.app import install_web

    app = FastAPI(
        title="OBot API",
        version="0.17.0",
        description="OBot-ACM Next Web API and Contracts",
    )
    services = DummyServices()
    install_web(app, services)

    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    return schema


def main():
    target_path = ROOT / "packages" / "contracts" / "schema" / "openapi.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    schema = generate_openapi()
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"OpenAPI schema successfully written to {target_path}")
    print(f"Total paths: {len(schema.get('paths', {}))}")


if __name__ == "__main__":
    main()
