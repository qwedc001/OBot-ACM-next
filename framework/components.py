"""Shared construction and lifecycle contract; no business or transport behavior."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Self

if TYPE_CHECKING:
    from .runtime import Services


class Component(ABC):
    dependencies: ClassVar[tuple[str, ...]] = ()

    @classmethod
    @abstractmethod
    def build(cls, container: "Services") -> Self:
        """Construct from configured dependencies without performing blocking I/O."""

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
