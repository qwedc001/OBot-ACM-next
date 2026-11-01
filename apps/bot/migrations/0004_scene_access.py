"""Observed native group roles and one-use browser/group identity verification."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_scene_authorities",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("identity_key", sa.String(), sa.ForeignKey("bot_identities.key"), primary_key=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_bot_scene_authorities_verified_at", "bot_scene_authorities", ["verified_at"])
    op.create_table(
        "bot_web_scene_grants",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), primary_key=True),
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("identity_key", sa.String(), sa.ForeignKey("bot_identities.key"), nullable=False),
    )
    op.create_index("ix_bot_web_scene_grants_identity_key", "bot_web_scene_grants", ["identity_key"])
    op.create_table(
        "bot_web_group_checks",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    for field in ("user_id", "expires_at"):
        op.create_index("ix_bot_web_group_checks_" + field, "bot_web_group_checks", [field])


def downgrade():
    op.drop_table("bot_web_group_checks")
    op.drop_table("bot_web_scene_grants")
    op.drop_table("bot_scene_authorities")
