import pytest

from framework.admin import apply, parser
from framework.models import User
from framework.services.identities import IdentityService

from .conftest import USER


async def test_local_cli_adds_and_revokes_bot_administrator(database):
    parse = parser().parse_args
    service = IdentityService(database)
    assert not await service.is_superuser(USER)
    await apply(database, parse(["superuser", "add", USER.id]))
    assert await service.is_superuser(USER)
    await apply(database, parse(["superuser", "remove", USER.id]))
    assert not await service.is_superuser(USER)


async def test_admin_requires_an_existing_user_and_removed_rule_cli_is_unavailable(database):
    parse = parser().parse_args
    with pytest.raises(ValueError):
        await apply(database, parse(["superuser", "add", "missing"]))
    with pytest.raises(SystemExit):
        parse(["grant", "rule", "*", "system.status"])
    async with database() as session:
        assert await session.get(User, "missing") is None
