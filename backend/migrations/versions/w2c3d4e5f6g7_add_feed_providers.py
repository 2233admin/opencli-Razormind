"""add feed providers

Revision ID: w2c3d4e5f6g7
Revises: v1b2c3d4e5f6
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "w2c3d4e5f6g7"
down_revision = "v1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feed_providers",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("feed_providers")
