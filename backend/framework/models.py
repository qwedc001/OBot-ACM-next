"""Application models using the official NoneBot ORM plugin."""

from uuid import uuid4

from nonebot import require
from sqlalchemy import JSON, Float, ForeignKey, String, UniqueConstraint
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


class BoardConfig(Model):
    __tablename__ = "bot_board_configs"
    __table_args__ = (UniqueConstraint("scope", "name", name="uq_board_scope_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    scope: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    owner_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"))
    host: Mapped[str]
    credentials: Mapped[str]  # Fernet encrypted username/password JSON
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    push_target: Mapped[dict | None] = mapped_column(JSON)
    revision: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[float] = mapped_column(Float)


class BoardBindToken(Model):
    __tablename__ = "bot_board_bind_tokens"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    owner_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    claim: Mapped[str | None] = mapped_column(String(32))
    used: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(default=0)
    pending: Mapped[dict | None] = mapped_column(JSON)  # contains only encrypted credentials


class BoardSnapshot(Model):
    __tablename__ = "bot_board_snapshots"
    __table_args__ = (UniqueConstraint("config_id", "day", name="uq_board_snapshot_day"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    config_id: Mapped[str] = mapped_column(ForeignKey("bot_board_configs.id"), index=True)
    day: Mapped[str]
    revision: Mapped[str] = mapped_column(String(32))
    captured_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class WebLoginToken(Model):
    __tablename__ = "bot_web_login_tokens"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("bot_users.id"), index=True)
    identity_key: Mapped[str]
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class WebSession(Model):
    __tablename__ = "bot_web_sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("bot_users.id"), index=True)
    identity_key: Mapped[str]
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    created_at: Mapped[float] = mapped_column(Float)
