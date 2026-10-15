import hmac
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request

from ..contracts import Role
from ..runtime import Services


def build_internal_router(services: Services) -> APIRouter:
    async def authenticate(request: Request) -> None:
        secret = services.settings.obot_internal_token
        if secret is None:
            raise HTTPException(404, "Not found")
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        supplied = request.headers.get("authorization", "")
        if not local or not hmac.compare_digest(
            supplied.encode("utf-8"), f"Bearer {secret.get_secret_value()}".encode()
        ):
            raise HTTPException(401, "Unauthorized")

    router = APIRouter(prefix="/internal/v1", dependencies=[Depends(authenticate)], include_in_schema=False)

    @router.get("/health")
    async def health() -> dict:
        return services.snapshot()

    @router.get("/commands")
    async def commands() -> dict:
        # A machine token does not impersonate a platform group administrator.
        async def public(spec):
            return spec.permission == Role.EVERYONE

        visible = await services.catalog.visible(public)
        return {
            "separator": services.catalog.separator,
            "directories": services.catalog.directory_index(visible),
            "commands": [spec.to_dict(services.catalog.separator) for spec in visible],
        }

    return router
