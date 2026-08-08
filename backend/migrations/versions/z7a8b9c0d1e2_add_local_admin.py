"""add local administrator credential

Revision ID: z7a8b9c0d1e2
Revises: k8l9m0n1o2p3
"""
import sqlalchemy as sa
from alembic import op

revision = "z7a8b9c0d1e2"
down_revision = "k8l9m0n1o2p3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_admin_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("local_admin_credentials")
