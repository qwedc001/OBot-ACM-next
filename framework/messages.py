"""Portable message content, independent of any NoneBot adapter."""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Text:
    content: str


@dataclass(frozen=True)
class Image:
    source: bytes | Path | str
    filename: str | None = None

    def __post_init__(self):
        if isinstance(self.source, str):
            url = urlsplit(self.source)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("Image strings must be HTTP(S) URLs; use Path for local files")
        elif not isinstance(self.source, (bytes, Path)) or not self.source:
            raise ValueError("Image requires nonempty bytes, a URL, or a Path")


@dataclass(frozen=True)
class MarkdownParam:
    key: str
    values: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "values", tuple(self.values))
        if not self.key or not self.values or any(not isinstance(value, str) for value in self.values):
            raise ValueError("Markdown parameters require a key and string values")


@dataclass(frozen=True)
class Markdown:
    content: str | None = None
    template_id: str | None = None
    params: tuple[MarkdownParam, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "params", tuple(self.params))
        if bool(self.content) == bool(self.template_id):
            raise ValueError("Choose Markdown content or a template ID")
        if self.params and not self.template_id:
            raise ValueError("Markdown parameters require a template")
        if len({param.key for param in self.params}) != len(self.params):
            raise ValueError("Duplicate Markdown parameter")

    @classmethod
    def template(cls, template_id: str, **params: str | tuple[str, ...] | list[str]) -> Self:
        return cls(
            template_id=template_id,
            params=tuple(
                MarkdownParam(key, (values,) if isinstance(values, str) else tuple(values))
                for key, values in params.items()
            ),
        )


class ButtonAction(IntEnum):
    LINK = 0
    CALLBACK = 1
    COMMAND = 2
    NATIVE_LINK = 3


class ButtonAccess(IntEnum):
    USERS = 0
    ADMIN = 1
    EVERYONE = 2
    ROLES = 3


@dataclass(frozen=True)
class ButtonPermission:
    access: ButtonAccess = ButtonAccess.EVERYONE
    user_ids: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "access", ButtonAccess(self.access))
        object.__setattr__(self, "user_ids", tuple(self.user_ids))
        object.__setattr__(self, "role_ids", tuple(self.role_ids))
        if any(not isinstance(value, str) or not value for value in (*self.user_ids, *self.role_ids)):
            raise ValueError("Button permission IDs must be nonempty strings")
        if bool(self.user_ids) != (self.access == ButtonAccess.USERS) or bool(self.role_ids) != (
            self.access == ButtonAccess.ROLES
        ):
            raise ValueError("Button permission IDs must match the access type")


@dataclass(frozen=True)
class ButtonModal:
    content: str
    confirm_text: str = "确认"
    cancel_text: str = "取消"


@dataclass(frozen=True)
class Button:
    label: str
    data: str
    action: ButtonAction = ButtonAction.COMMAND
    id: str | None = None
    visited_label: str | None = None
    style: int = 1
    permission: ButtonPermission = ButtonPermission()
    enter: bool = True
    reply: bool = False
    unsupported_tips: str | None = None
    group_id: str | None = None
    anchor: int | None = None
    modal: ButtonModal | None = None

    def __post_init__(self):
        object.__setattr__(self, "action", ButtonAction(self.action))
        if not self.label or not self.data or self.style not in {0, 1, 3, 4}:
            raise ValueError("Buttons require a label, action data, and style 0, 1, 3, or 4")
        if self.action == ButtonAction.CALLBACK and not self.id:
            raise ValueError("Callback buttons require an ID")


@dataclass(frozen=True)
class Keyboard:
    rows: tuple[tuple[Button, ...], ...] = ()
    template_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))
        if bool(self.rows) == bool(self.template_id):
            raise ValueError("Choose keyboard rows or a template ID")
        if any(not row or any(not isinstance(button, Button) for button in row) for row in self.rows):
            raise ValueError("Keyboard rows require Button objects")
        ids = [button.id for row in self.rows for button in row if button.id is not None]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate button ID")


@dataclass(frozen=True)
class SendTarget:
    """Adapter-specific destination, without synthesizing an incoming event."""

    adapter: str
    kind: str
    id: str
    bot_id: str | None = None

    def __post_init__(self):
        if not self.adapter or not self.kind or not self.id:
            raise ValueError("Send targets require adapter, kind, and destination ID")


type Segment = Text | Image | Markdown | Keyboard


@dataclass(frozen=True, init=False)
class Message:
    parts: tuple[Segment, ...]

    def __init__(self, *parts: Segment | str):
        normalized = tuple(Text(part) if isinstance(part, str) else part for part in parts)
        if not normalized or any(
            not isinstance(part, (Text, Image, Markdown, Keyboard)) for part in normalized
        ):
            raise ValueError("Message requires text, image, Markdown, or keyboard parts")
        object.__setattr__(self, "parts", normalized)


type ReplyContent = str | Segment | Message
