"""Limit group-issued dashboard links and sessions to the issuing group."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("bot_web_login_tokens", "bot_web_sessions"):
        op.add_column(table, sa.Column("group_scope", sa.String(), nullable=True))


def downgrade():
    for table in ("bot_web_sessions", "bot_web_login_tokens"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("group_scope")
