"""add immutable evidence batch materialization manifests

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_batch_materialization_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("derivation", sa.String(length=64), nullable=False),
        sa.Column("reconciliation_revision", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("source_binding_id", sa.String(length=36), nullable=True),
        sa.Column("source_binding_revision_id", sa.String(length=36), nullable=True),
        sa.Column("report_id", sa.String(length=128), nullable=True),
        sa.Column("report_hash", sa.String(length=64), nullable=True),
        sa.Column("expected_key_set_hash", sa.String(length=64), nullable=True),
        sa.Column("receipt_hashes", sa.JSON(), nullable=False),
        sa.Column("query_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("page_snapshot_as_of", sa.String(length=64), nullable=True),
        sa.Column("redaction_profile_version", sa.String(length=64), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("materialization_status", sa.String(length=32), nullable=False),
        sa.Column("record_references", sa.JSON(), nullable=False),
        sa.Column("retention_state", sa.String(length=32), nullable=False),
        sa.Column("finalization_reason", sa.String(length=256), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "command_id",
            "attempt_id",
            "reconciliation_revision",
            name="uq_evidence_batch_materialization_revision",
        ),
        sa.UniqueConstraint(
            "manifest_hash", name="uq_evidence_batch_materialization_hash"
        ),
    )
    op.create_index(
        "ix_evidence_batch_materialization_manifests_batch_id",
        "evidence_batch_materialization_manifests",
        ["batch_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_manifests_command_id",
        "evidence_batch_materialization_manifests",
        ["command_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_manifests_attempt_id",
        "evidence_batch_materialization_manifests",
        ["attempt_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_status",
        "evidence_batch_materialization_manifests",
        ["materialization_status"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_scope",
        "evidence_batch_materialization_manifests",
        ["workspace_id", "project_id", "workflow_id", "run_id", "batch_id"],
    )
    op.create_table(
        "evidence_batch_materialization_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_id", sa.String(length=36), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("materialization_status", sa.String(length=32), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["iii_collection_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["iii_collection_commands.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"],
            ["evidence_batch_materialization_manifests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash"),
        sa.UniqueConstraint(
            "manifest_id",
            "event_type",
            name="uq_evidence_batch_materialization_event_manifest",
        ),
    )
    op.create_index(
        "ix_evidence_batch_materialization_events_manifest_id",
        "evidence_batch_materialization_events",
        ["manifest_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_events_command_id",
        "evidence_batch_materialization_events",
        ["command_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_events_attempt_id",
        "evidence_batch_materialization_events",
        ["attempt_id"],
    )
    op.create_index(
        "ix_evidence_batch_materialization_event_attempt",
        "evidence_batch_materialization_events",
        ["attempt_id", "reconciliation_revision"],
    )


def downgrade() -> None:
    op.drop_table("evidence_batch_materialization_events")
    op.drop_table("evidence_batch_materialization_manifests")
