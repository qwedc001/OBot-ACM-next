"""Scoped board configuration, one-use binding capabilities and daily snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_board_configs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("credentials", sa.String(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("push_target", sa.JSON()),
        sa.Column("revision", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("scope", "name", name="uq_board_scope_name"),
    )
    op.create_index("ix_bot_board_configs_scope", "bot_board_configs", ["scope"])
    op.create_table(
        "bot_board_bind_tokens",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("claim", sa.String(32)),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("pending", sa.JSON()),
    )
    op.create_index("ix_bot_board_bind_tokens_scope", "bot_board_bind_tokens", ["scope"])
    op.create_index("ix_bot_board_bind_tokens_expires_at", "bot_board_bind_tokens", ["expires_at"])
    op.create_table(
        "bot_board_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("config_id", sa.String(32), sa.ForeignKey("bot_board_configs.id"), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("revision", sa.String(32), nullable=False),
        sa.Column("captured_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("config_id", "day", name="uq_board_snapshot_day"),
    )
    op.create_index("ix_bot_board_snapshots_config_id", "bot_board_snapshots", ["config_id"])


def downgrade():
    op.drop_table("bot_board_snapshots")
    op.drop_table("bot_board_bind_tokens")
    op.drop_table("bot_board_configs")
