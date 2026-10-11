"""Initial schema; breaking replacement of the prototype record store."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("superuser", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "bot_identities",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
    )
    op.create_index("ix_bot_identities_user_id", "bot_identities", ["user_id"])
    op.create_table(
        "bot_artifacts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("mime", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
    )
    op.create_index("ix_bot_artifacts_owner_id", "bot_artifacts", ["owner_id"])
    op.create_index("ix_bot_artifacts_expires_at", "bot_artifacts", ["expires_at"])


def downgrade():
    op.drop_table("bot_artifacts")
    op.drop_table("bot_identities")
    op.drop_table("bot_users")
