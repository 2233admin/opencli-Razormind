"""persist controlled delivery reserved attempt identity

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_executions",
        sa.Column("reserved_attempt_number", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("delivery_executions", "reserved_attempt_number")
