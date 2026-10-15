import time

import pytest

from framework.clients.artifacts import ArtifactClient, ArtifactUnavailable
from framework.contracts import AccessDenied
from framework.services.identities import IdentityService

from .conftest import ROOT, USER


async def test_image_is_private_to_requester_and_bot_administrator(database, tmp_path):
    identities = IdentityService(database)
    artifacts = ArtifactClient(tmp_path / "images", database, identities.is_superuser)
    image = await artifacts.create(USER, b"image", "image/png")
    async with artifacts.lease(USER, image.id) as path:
        assert path.read_bytes() == b"image"
    async with artifacts.lease(ROOT, image.id):
        pass
    from framework.contracts import ExternalIdentity

    stranger = await identities.resolve(ExternalIdentity("qq", "bot", "group_member", "admin"))
    with pytest.raises(AccessDenied):
        async with artifacts.lease(stranger, image.id):
            pytest.fail("A group administrator cannot retrieve another user's help image")


async def test_expired_artifact_cleanup_respects_active_lease(database, tmp_path):
    artifacts = ArtifactClient(tmp_path / "images", database, IdentityService(database).is_superuser, ttl=10)
    image = await artifacts.create(USER, b"image", "image/png")
    async with artifacts.lease(USER, image.id) as path:
        assert await artifacts.cleanup(time.time() + 100) == 0 and path.exists()
    assert await artifacts.cleanup(time.time() + 100) == 1 and not path.exists()
    with pytest.raises(ArtifactUnavailable):
        async with artifacts.lease(USER, image.id):
            pytest.fail("Expired image must be unavailable")


async def test_invalid_images_and_paths_rejected(database, tmp_path):
    artifacts = ArtifactClient(
        tmp_path / "images", database, IdentityService(database).is_superuser, max_bytes=2
    )
    with pytest.raises(ValueError):
        await artifacts.create(USER, b"too big", "image/png")
    with pytest.raises(ArtifactUnavailable):
        artifacts._path("../escape")
