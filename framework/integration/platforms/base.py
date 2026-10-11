"""Platform-specific event, permission and message integration contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from nonebot.adapters import Adapter, Bot, Event

from ...contracts import ExternalIdentity, SceneRef
from ...messages import Message, SendTarget


@dataclass(frozen=True)
class Incoming:
    identity: ExternalIdentity
    scene: SceneRef
    event_id: str


class Platform(ABC):
    adapter_type: ClassVar[type[Adapter]]
    supported_segments: ClassVar[frozenset[type]]

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
