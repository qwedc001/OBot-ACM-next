"""Codeforces 1v1 duel matches and PTT rating records."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_cf_user_ptt",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), primary_key=True),
        sa.Column("ptt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contest_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )

    op.create_table(
        "bot_cf_duels",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scene", sa.String(64), nullable=False),
        sa.Column("pair_code", sa.String(16), nullable=False),
        sa.Column("sender_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("sender_handle", sa.String(64), nullable=False),
        sa.Column("receiver_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("receiver_handle", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("rating_range", sa.String(32), nullable=True),
        sa.Column("tag", sa.String(64), nullable=True),
        sa.Column("problem", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.Integer(), nullable=True),
        sa.Column("sender_ptt_delta", sa.Integer(), nullable=True),
        sa.Column("receiver_ptt_delta", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_cf_duels_scene_status", "bot_cf_duels", ["scene", "status"])
    op.create_index("ix_cf_duels_sender", "bot_cf_duels", ["sender_id", "status"])
    op.create_index("ix_cf_duels_receiver", "bot_cf_duels", ["receiver_id", "status"])
    op.create_index("ix_bot_cf_duels_scene", "bot_cf_duels", ["scene"])
    op.create_index("ix_bot_cf_duels_pair_code", "bot_cf_duels", ["pair_code"], unique=True)
    op.create_index("ix_bot_cf_duels_expires_at", "bot_cf_duels", ["expires_at"])


def downgrade():
    op.drop_table("bot_cf_duels")
    op.drop_table("bot_cf_user_ptt")
