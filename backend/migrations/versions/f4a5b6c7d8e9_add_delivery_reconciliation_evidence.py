"""add controlled delivery reconciliation evidence

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_executions",
        sa.Column("final_reconciliation_id", sa.String(36), nullable=True),
    )

    op.create_table(
        "delivery_execution_reconciliations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["delivery_executions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_delivery_execution_reconciliation_execution",
        "delivery_execution_reconciliations",
        ["execution_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_column("delivery_executions", "final_reconciliation_id")
    op.drop_table("delivery_execution_reconciliations")
