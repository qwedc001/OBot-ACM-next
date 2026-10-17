"""Three roles composed from NoneBot and registered platform permissions."""

from collections.abc import Awaitable, Callable

from nonebot.adapters import Bot, Event
from nonebot.permission import MESSAGE, Permission
from nonebot.permission import SUPERUSER as CONFIGURED_SUPERUSER

from ..catalog import CommandSpec
from ..contracts import Role
from ..runtime import get_services
from .platforms import get_platform, make_context


async def canonical_superuser(bot: Bot, event: Event) -> bool:
    services = get_services()
    context = await make_context(services, bot, event)
    return await services.identities.is_superuser(context.actor)


EVERYONE = MESSAGE
SUPERUSER = CONFIGURED_SUPERUSER | Permission(canonical_superuser)


async def platform_group_admin(bot: Bot, event: Event) -> bool:
    return await get_platform(bot).group_admin(bot, event)


GROUP_ADMIN = SUPERUSER | Permission(platform_group_admin)
PERMISSIONS = {Role.EVERYONE: EVERYONE, Role.GROUP_ADMIN: GROUP_ADMIN, Role.SUPERUSER: SUPERUSER}


def permission_for(role: Role) -> Permission:
    return PERMISSIONS[role]


def command_check(bot: Bot, event: Event) -> Callable[[CommandSpec], Awaitable[bool]]:
    async def check(spec: CommandSpec) -> bool:
        scene = get_platform(bot).normalize(bot, event).scene
        if not await permission_for(spec.required_role(scene.kind))(bot, event):
            return False
        return scene.kind in spec.scopes or await SUPERUSER(bot, event)

    return check
