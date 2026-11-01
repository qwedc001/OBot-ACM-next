"""Persist PickOne scene references without a separate storage engine."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bot_pickone_libraries", sa.Column("scope", sa.String(), primary_key=True))
    op.create_table(
        "bot_pickone_contexts",
        sa.Column("scene", sa.String(), primary_key=True),
        sa.Column("last", sa.JSON()),
        sa.Column("preview", sa.JSON()),
    )


def downgrade():
    op.drop_table("bot_pickone_contexts")
    op.drop_table("bot_pickone_libraries")
