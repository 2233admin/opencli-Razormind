"""add durable Admin III collection ledger

Revision ID: a8b9c0d1e2f3
Revises: k8l9m0n1o2p3
Create Date: 2026-08-29
"""

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "k8l9m0n1o2p3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iii_collection_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("source_binding_id", sa.String(length=36), nullable=True),
        sa.Column("source_binding_revision_id", sa.String(length=36), nullable=True),
        sa.Column("source_binding_revision_number", sa.Integer(), nullable=True),
        sa.Column("odp_source_id", sa.String(length=36), nullable=False),
        sa.Column("collector_function_id", sa.String(length=100), nullable=False),
        sa.Column("collector_payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["studio_projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["studio_workflow_version_id"], ["studio_workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["studio_workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_iii_collection_command_run_key"),
    )
    op.create_index(
        "ix_iii_collection_commands_workspace_id", "iii_collection_commands", ["workspace_id"]
    )
    op.create_index(
        "ix_iii_collection_commands_project_id", "iii_collection_commands", ["project_id"]
    )
    op.create_index(
        "ix_iii_collection_commands_workflow_id", "iii_collection_commands", ["workflow_id"]
    )
    op.create_index(
        "ix_iii_collection_commands_studio_workflow_version_id",
        "iii_collection_commands",
        ["studio_workflow_version_id"],
    )
    op.create_index("ix_iii_collection_commands_run_id", "iii_collection_commands", ["run_id"])
    op.create_index(
        "ix_iii_collection_commands_payload_sha256", "iii_collection_commands", ["payload_sha256"]
    )
    op.create_index("ix_iii_collection_commands_trace_id", "iii_collection_commands", ["trace_id"])
    op.create_index(
        "ix_iii_collection_commands_scope",
        "iii_collection_commands",
        ["workspace_id", "project_id", "workflow_id"],
    )

    op.create_table(
        "iii_collection_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "command_id", "attempt_number", name="uq_iii_collection_attempt_number"
        ),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        "ix_iii_collection_attempts_command_id", "iii_collection_attempts", ["command_id"]
    )
    op.create_index("ix_iii_collection_attempts_task_id", "iii_collection_attempts", ["task_id"])
    op.create_index("ix_iii_collection_attempts_trace_id", "iii_collection_attempts", ["trace_id"])

    op.create_table(
        "iii_collection_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("dispatch_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index("ix_iii_collection_outbox_attempt_id", "iii_collection_outbox", ["attempt_id"])
    op.create_index(
        "ix_iii_collection_outbox_delivery",
        "iii_collection_outbox",
        ["state", "available_at"],
    )

    op.create_table(
        "iii_collection_lifecycle_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_content_hash", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "command_id", "attempt_id", "sequence", name="uq_iii_collection_lifecycle_replay"
        ),
    )
    op.create_index(
        "ix_iii_collection_lifecycle_command_id",
        "iii_collection_lifecycle_observations",
        ["command_id"],
    )
    op.create_index(
        "ix_iii_collection_lifecycle_attempt_id",
        "iii_collection_lifecycle_observations",
        ["attempt_id"],
    )
    op.create_index(
        "ix_iii_collection_lifecycle_attempt",
        "iii_collection_lifecycle_observations",
        ["attempt_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_table("iii_collection_lifecycle_observations")
    op.drop_table("iii_collection_outbox")
    op.drop_table("iii_collection_attempts")
    op.drop_table("iii_collection_commands")
