"""Application models using the official NoneBot ORM plugin."""

from uuid import uuid4

from nonebot import require
from sqlalchemy import JSON, Float, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

require("nonebot_plugin_orm")
from nonebot_plugin_orm import Model  # noqa: E402


class User(Model):
    __tablename__ = "bot_users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)
    superuser: Mapped[bool] = mapped_column(default=False)


class PickOneContext(Model):
    """Scene-local references, stored by the existing ORM and included in backups."""

    __tablename__ = "bot_pickone_contexts"
    scene: Mapped[str] = mapped_column(primary_key=True)
    last: Mapped[dict | None] = mapped_column(JSON)
    preview: Mapped[dict | None] = mapped_column(JSON)


class PickOneLibrary(Model):
    __tablename__ = "bot_pickone_libraries"

    scope: Mapped[str] = mapped_column(primary_key=True)


class ContentSubmission(Model):
    """Shared EP review queue; authors are canonical bot identities."""

    __tablename__ = "bot_content_submissions"
    __table_args__ = (
        Index("ix_content_plugin_status", "plugin", "status", "created_at"),
        Index("ix_content_author", "author_id", "created_at"),
        Index(
            "uq_content_open_target",
            "plugin",
            "type",
            "img_key",
            "target",
            "author_id",
            unique=True,
            sqlite_where=text("status IN ('pending', 'approved', 'conflict')"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin: Mapped[str]
    type: Mapped[str]
    img_key: Mapped[str]
    target: Mapped[str] = mapped_column(server_default="")
    base_value: Mapped[str | None]
    submitted_value: Mapped[str]
    note: Mapped[str] = mapped_column(server_default="")
    status: Mapped[str] = mapped_column(server_default="pending")
    author_id: Mapped[str] = mapped_column(String(32), ForeignKey("bot_users.id"))
    reviewer_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("bot_users.id"))
    review_comment: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[float] = mapped_column(Float)
    reviewed_at: Mapped[float | None] = mapped_column(Float)
    applied_at: Mapped[float | None] = mapped_column(Float)
    applied_value: Mapped[str | None]
    conflict_detail: Mapped[str | None]
    conflict_at: Mapped[float | None] = mapped_column(Float)


class ContentLog(Model):
    __tablename__ = "bot_content_logs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("bot_users.id", ondelete="SET NULL"))
    action: Mapped[str]
    detail: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[float] = mapped_column(Float, index=True)
    is_admin: Mapped[bool] = mapped_column(server_default="0")
    username: Mapped[str] = mapped_column(server_default="")


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
    group_scope: Mapped[str | None]
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class WebSession(Model):
    __tablename__ = "bot_web_sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("bot_users.id"), index=True)
    identity_key: Mapped[str]
    group_scope: Mapped[str | None]
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    created_at: Mapped[float] = mapped_column(Float)


class SceneAuthority(Model):
    __tablename__ = "bot_scene_authorities"
    scope: Mapped[str] = mapped_column(primary_key=True)
    identity_key: Mapped[str] = mapped_column(ForeignKey("bot_identities.key"), primary_key=True)
    is_admin: Mapped[bool]
    verified_at: Mapped[float] = mapped_column(Float, index=True)


class WebSceneGrant(Model):
    __tablename__ = "bot_web_scene_grants"
    user_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(primary_key=True)
    identity_key: Mapped[str] = mapped_column(ForeignKey("bot_identities.key"), index=True)


class WebGroupCheck(Model):
    __tablename__ = "bot_web_group_checks"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("bot_users.id"), index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
