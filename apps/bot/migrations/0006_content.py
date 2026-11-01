"""EP content submissions and audit, owned by existing bot identities."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_content_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plugin", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("img_key", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False, server_default=""),
        sa.Column("base_value", sa.String()),
        sa.Column("submitted_value", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("author_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("reviewer_id", sa.String(32), sa.ForeignKey("bot_users.id")),
        sa.Column("review_comment", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("reviewed_at", sa.Float()),
        sa.Column("applied_at", sa.Float()),
        sa.Column("applied_value", sa.String()),
        sa.Column("conflict_detail", sa.String()),
        sa.Column("conflict_at", sa.Float()),
    )
    op.create_index("ix_content_plugin_status", "bot_content_submissions", ["plugin", "status", "created_at"])
    op.create_index("ix_content_author", "bot_content_submissions", ["author_id", "created_at"])
    op.create_index(
        "uq_content_open_target",
        "bot_content_submissions",
        ["plugin", "type", "img_key", "target", "author_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('pending', 'approved', 'conflict')"),
    )
    op.create_table(
        "bot_content_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(32), sa.ForeignKey("bot_users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("username", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_bot_content_logs_created_at", "bot_content_logs", ["created_at"])


def downgrade():
    op.drop_table("bot_content_logs")
    op.drop_table("bot_content_submissions")
