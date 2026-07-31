"""link Studio workflow runs to immutable published versions

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import context, op

revision = "j7k8l9m0n1o2"
down_revision = "i6j7k8l9m0n1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if (
        not context.is_offline_mode()
        and "workflow_runs" not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return

    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(
            sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_workflow_runs_studio_workflow_version_id",
            "studio_workflow_versions",
            ["studio_workflow_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_workflow_runs_studio_workflow_version_id",
            ["studio_workflow_version_id"],
            unique=False,
        )


def downgrade() -> None:
    if (
        not context.is_offline_mode()
        and "workflow_runs" not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return

    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_index("ix_workflow_runs_studio_workflow_version_id")
        batch.drop_constraint(
            "fk_workflow_runs_studio_workflow_version_id",
            type_="foreignkey",
        )
        batch.drop_column("studio_workflow_version_id")
