"""Immutable identities and request context shared by application modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import uuid4

if TYPE_CHECKING:
    from .catalog import CommandSpec
else:
    # Runtime DI validates this dataclass; avoid a circular catalog import.
    CommandSpec = Any


def qualified_key(*parts: str) -> str:
    if any(not part or not part.strip() for part in parts):
        raise ValueError("Identity key components must not be empty")
    return ":".join(quote(part, safe="") for part in parts)


class SceneKind(StrEnum):
    GROUP = "group"
    CHANNEL = "channel"
    DIRECT = "direct"
    C2C = "c2c"
    WEB = "web"
    JOB = "job"


class Role(StrEnum):
    EVERYONE = "everyone"
    GROUP_ADMIN = "group_admin"
    SUPERUSER = "superuser"


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class ExternalIdentity:
    provider: str
    app_id: str
    kind: str
    external_id: str

    @property
    def key(self) -> str:
        return qualified_key(self.provider, self.app_id, self.kind, self.external_id)


@dataclass(frozen=True)
class MentionedUser:
    identity: ExternalIdentity
    authoritative: bool = False


@dataclass(frozen=True)
class Actor:
    id: str
    identity_key: str

    def __post_init__(self) -> None:
        if not self.id or not self.identity_key:
            raise ValueError("Actor identity must not be empty")


@dataclass(frozen=True)
class SceneRef:
    provider: str
    app_id: str
    kind: SceneKind
    external_id: str
    guild_id: str | None = None

    @property
    def key(self) -> str:
        return qualified_key(
            self.provider,
            self.app_id,
            self.kind,
            self.external_id,
        )


@dataclass(frozen=True)
class RequestContext:
    actor: Actor
    scene: SceneRef
    event_id: str
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    command_allowed: Callable[[CommandSpec], Awaitable[bool]] | None = field(
        default=None, repr=False, compare=False
    )
