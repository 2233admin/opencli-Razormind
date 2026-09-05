"""add analysis snapshot receipts and acquisition run correlation

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "r5s6t7u8v9w0"
down_revision = "q4r5s6t7u8v9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("acquisition_executions") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("workflow_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch_op.create_check_constraint(
            "ck_acquisition_executions_complete_run_correlation",
            "(workspace_id IS NULL AND project_id IS NULL "
            "AND workflow_id IS NULL AND run_id IS NULL) "
            "OR (workspace_id IS NOT NULL AND project_id IS NOT NULL "
            "AND workflow_id IS NOT NULL AND run_id IS NOT NULL)",
        )
    op.create_index(
        "ix_acquisition_executions_analysis_scope_started_at",
        "acquisition_executions",
        ["workspace_id", "project_id", "workflow_id", "run_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "analysis_snapshot_receipts",
        sa.Column("runtime", sa.String(length=32), server_default="questdb", nullable=False),
        sa.Column("selection_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("redaction_version", sa.Integer(), nullable=False),
        sa.Column("source_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_trace_event_count", sa.Integer(), nullable=False),
        sa.Column("acquisition_execution_metric_count", sa.Integer(), nullable=False),
        sa.Column("total_row_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("runtime = 'questdb'", name="ck_analysis_snapshot_runtime"),
        sa.CheckConstraint(
            "workflow_trace_event_count >= 0 AND acquisition_execution_metric_count >= 0 "
            "AND total_row_count = workflow_trace_event_count + acquisition_execution_metric_count",
            name="ck_analysis_snapshot_row_counts",
        ),
        sa.CheckConstraint("attempt_count >= 1", name="ck_analysis_snapshot_attempt_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("selection_hash", name="uq_analysis_snapshot_selection_hash"),
    )
    op.create_index(
        "ix_analysis_snapshot_scope_created",
        "analysis_snapshot_receipts",
        ["workspace_id", "project_id", "workflow_id", "run_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_snapshot_scope_created",
        table_name="analysis_snapshot_receipts",
    )
    op.drop_table("analysis_snapshot_receipts")

    op.drop_index(
        "ix_acquisition_executions_analysis_scope_started_at",
        table_name="acquisition_executions",
    )
    with op.batch_alter_table("acquisition_executions") as batch_op:
        batch_op.drop_constraint(
            "ck_acquisition_executions_complete_run_correlation",
            type_="check",
        )
        batch_op.drop_column("run_id")
        batch_op.drop_column("workflow_id")
        batch_op.drop_column("project_id")
        batch_op.drop_column("workspace_id")
