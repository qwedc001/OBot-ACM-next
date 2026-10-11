"""Application models using the official NoneBot ORM plugin."""

from uuid import uuid4

from nonebot import require
from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model  # noqa: E402


class User(Model):
    __tablename__ = "bot_users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    superuser: Mapped[bool] = mapped_column(default=False)


class Identity(Model):
    __tablename__ = "bot_identities"
    key: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"), index=True)


class ImageArtifact(Model):
    __tablename__ = "bot_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"), index=True)
    mime: Mapped[str]
    size: Mapped[int]
    sha256: Mapped[str]
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    width: Mapped[int | None]
    height: Mapped[int | None]
