"""Codeforces account bindings, pending verifications, and audit logs."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_cf_bindings",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), primary_key=True),
        sa.Column("handle", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="bound"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_bot_cf_bindings_handle", "bot_cf_bindings", ["handle"])

    op.create_table(
        "bot_cf_pending_bindings",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), primary_key=True),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("problem", sa.String(32), nullable=False, server_default="1A"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_bot_cf_pending_bindings_expires_at", "bot_cf_pending_bindings", ["expires_at"])

    op.create_table(
        "bot_cf_binding_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_bot_cf_binding_logs_user_id", "bot_cf_binding_logs", ["user_id"])
    op.create_index("ix_bot_cf_binding_logs_created_at", "bot_cf_binding_logs", ["created_at"])


def downgrade():
    op.drop_table("bot_cf_binding_logs")
    op.drop_table("bot_cf_pending_bindings")
    op.drop_table("bot_cf_bindings")
