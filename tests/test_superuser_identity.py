import pytest

from framework.admin import apply, parser
from framework.contracts import ExternalIdentity
from framework.models import Identity
from framework.services.identities import IdentityService

from .conftest import USER


async def owner_identities(database):
    service = IdentityService(database)
    private = ExternalIdentity("qq", "bot", "c2c_user", "owner")
    group = ExternalIdentity("qq", "bot", "group_member", "owner")
    return service, private, group, await service.resolve(private), await service.resolve(group)


async def test_explicit_link_and_persistent_superuser_share_both_scenes(database):
    service, private, group, actor, other = await owner_identities(database)
    assert actor.id != other.id
    await service.link(group.key, actor.id)
    linked = await service.resolve(group)
    assert linked.id == actor.id and linked.identity_key != actor.identity_key
    await apply(database, parser().parse_args(["superuser", "add", actor.id]))
    assert await service.is_superuser(actor) and await service.is_superuser(linked)
    assert not await service.is_superuser(USER)
    # Fresh service/sessions still see the same canonical administrator.
    assert await IdentityService(database).is_superuser(await IdentityService(database).resolve(group))
    await apply(database, parser().parse_args(["superuser", "remove", actor.id]))
    assert not await service.is_superuser(actor) and not await service.is_superuser(linked)


async def test_link_requires_observed_identity_and_existing_target(database):
    service, private, group, actor, other = await owner_identities(database)
    with pytest.raises(ValueError):
        await service.link(group.key, "missing")
    with pytest.raises(ValueError):
        await service.link("missing", actor.id)
    async with database() as session:
        assert (await session.get(Identity, group.key)).user_id == other.id


async def test_configured_canonical_or_external_superusers_are_supported(database):
    service, private, group, actor, other = await owner_identities(database)
    assert await IdentityService(database, frozenset({actor.id})).is_superuser(actor)
    assert await IdentityService(database, frozenset({private.key})).is_superuser(actor)
    assert not await IdentityService(database, frozenset({private.key})).is_superuser(other)
