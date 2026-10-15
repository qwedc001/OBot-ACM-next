"""One-use web login capabilities and revocable browser sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    for name, with_created in [("bot_web_login_tokens", False), ("bot_web_sessions", True)]:
        fields = [
            sa.Column("digest", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
            sa.Column("identity_key", sa.String(), nullable=False),
            sa.Column("expires_at", sa.Float(), nullable=False),
        ]
        if with_created:
            fields.append(sa.Column("created_at", sa.Float(), nullable=False))
        op.create_table(name, *fields)
        op.create_index("ix_" + name + "_user_id", name, ["user_id"])
        op.create_index("ix_" + name + "_expires_at", name, ["expires_at"])


def downgrade():
    op.drop_table("bot_web_sessions")
    op.drop_table("bot_web_login_tokens")
