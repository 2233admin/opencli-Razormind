"""add provider-neutral automations"""

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("precheck", sa.Text()),
        sa.Column("executor", sa.String(64), nullable=False),
        sa.Column("schedule", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("session_mode", sa.String(32), nullable=False),
        sa.Column("approval_mode", sa.String(32), nullable=False),
        sa.Column("project", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_automations_workspace_id", "automations", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("automations")
