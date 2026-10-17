"""Platform-specific event, permission and message integration contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from nonebot.adapters import Adapter, Bot, Event

from ...contracts import ExternalIdentity, MentionedUser, SceneRef
from ...messages import Message, SendTarget


@dataclass(frozen=True)
class Incoming:
    identity: ExternalIdentity
    scene: SceneRef
    event_id: str


class Platform(ABC):
    adapter_type: ClassVar[type[Adapter]]
    supported_segments: ClassVar[frozenset[type]]
    private_identity_kinds: ClassVar[frozenset[str]] = frozenset()

    @property
    def name(self) -> str:
        return self.adapter_type.get_name()

    @abstractmethod
    def normalize(self, bot: Bot, event: Event) -> Incoming:
        """Map platform IDs to qualified identities, scenes and a stable event ID."""

    @abstractmethod
    async def group_admin(self, bot: Bot, event: Event) -> bool:
        """Use the platform's native role checks; unavailable roles fail closed."""

    @abstractmethod
    async def build_message(
        self, recipient: Event | SendTarget, message: Message, at_sender: bool | None
    ) -> Any:
        """Convert supported content; unsupported features raise explicitly."""

    @abstractmethod
    async def send_to(self, bot: Bot, target: SendTarget, message: Any) -> Any:
        """Use the native SDK's proactive delivery and upload methods."""

    def send_target(self, bot: Bot, scene: SceneRef) -> SendTarget:
        """Optional scene routing for opted-in scheduled workflows."""
        raise NotImplementedError("Platform has no scene-to-target mapping")

    def mentioned_users(self, bot: Bot, event: Event) -> tuple[MentionedUser, ...]:
        """Map native mentions to identities; unsupported platforms return no targets."""
        return ()

    def reply_author(self, bot: Bot, event: Event) -> MentionedUser | None:
        """Return an authenticated native reply author when the platform supplies one."""
        return None

    def private_target(self, bot: Bot, identity: ExternalIdentity) -> SendTarget:
        """Route a previously verified private identity, without inventing an ID."""
        raise NotImplementedError("Platform has no private message route")
