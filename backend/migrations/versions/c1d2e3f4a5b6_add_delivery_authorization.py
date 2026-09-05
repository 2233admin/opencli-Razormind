"""add frozen delivery authorization decisions

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_targets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("receiver_identity", sa.String(length=255), nullable=False),
        sa.Column("target_kind", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "receiver_identity",
            name="uq_delivery_target_receiver",
        ),
    )
    op.create_index(
        "ix_delivery_targets_workspace", "delivery_targets", ["workspace_id", "id"]
    )
    op.create_index(
        "ix_delivery_targets_workspace_id", "delivery_targets", ["workspace_id"]
    )
    op.create_table(
        "delivery_target_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("studio_workflow_version_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint_identity", sa.String(length=255), nullable=False),
        sa.Column("non_secret_config_hash", sa.String(length=64), nullable=False),
        sa.Column("credential_reference", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"], ["delivery_targets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["studio_projects.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["studio_workflows.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["studio_workflow_version_id"],
            ["studio_workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_id", "revision", name="uq_delivery_target_revision"),
    )
    op.create_index(
        "ix_delivery_target_revisions_target_id",
        "delivery_target_revisions",
        ["target_id"],
    )
    op.create_index(
        "ix_delivery_target_revision_scope",
        "delivery_target_revisions",
        ["workspace_id", "project_id", "workflow_id", "run_id"],
    )
    op.create_table(
        "delivery_authorization_decisions",
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
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_revision_id", sa.String(length=36), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("endpoint_identity", sa.String(length=255), nullable=False),
        sa.Column("non_secret_config_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("pin_sequence", sa.Integer(), nullable=False),
        sa.Column("research_revision_id", sa.String(length=128), nullable=False),
        sa.Column("manifest_set_hash", sa.String(length=64), nullable=False),
        sa.Column("selected_claims", sa.JSON(), nullable=False),
        sa.Column("manifest_set", sa.JSON(), nullable=False),
        sa.Column("sanitized_payload_manifest", sa.JSON(), nullable=False),
        sa.Column("payload_schema_version", sa.String(length=64), nullable=False),
        sa.Column("payload_reference", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("redaction_profile_version", sa.String(length=64), nullable=False),
        sa.Column("approver_actor_id", sa.String(length=36), nullable=False),
        sa.Column("approver_actor_type", sa.String(length=32), nullable=False),
        sa.Column("approver_principal", sa.String(length=255), nullable=False),
        sa.Column("approver_capability", sa.String(length=64), nullable=False),
        sa.Column("approval_policy_version", sa.String(length=64), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_evidence", sa.JSON(), nullable=False),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("decisioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["studio_projects.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["studio_workflows.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["studio_workflow_version_id"],
            ["studio_workflow_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["delivery_targets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_revision_id"],
            ["delivery_target_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_hash", name="uq_delivery_authorization_decision_hash"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "workflow_id",
            "studio_workflow_version_id",
            "run_id",
            "operation_id",
            name="uq_delivery_authorization_operation",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "workflow_id",
            "studio_workflow_version_id",
            "run_id",
            "idempotency_key",
            name="uq_delivery_authorization_idempotency",
        ),
    )
    op.create_index(
        "ix_delivery_authorization_decisions_target_id",
        "delivery_authorization_decisions",
        ["target_id"],
    )
    op.create_index(
        "ix_delivery_authorization_scope_created",
        "delivery_authorization_decisions",
        ["workspace_id", "project_id", "workflow_id", "run_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("delivery_authorization_decisions")
    op.drop_table("delivery_target_revisions")
    op.drop_table("delivery_targets")
