"""add controlled receiver delivery execution ledger

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_executions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("decision_id", sa.String(36), nullable=False),
        sa.Column("target_revision_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column(
            "studio_workflow_version_id",
            sa.String(36),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "execution_binding_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("final_outcome", sa.String(16)),
        sa.Column("final_result_id", sa.String(36)),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["delivery_authorization_decisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_revision_id"],
            ["delivery_target_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id",
            name="uq_delivery_execution_decision",
        ),
        sa.UniqueConstraint(
            "execution_binding_hash",
            name="uq_delivery_execution_binding",
        ),
    )
    op.create_index(
        "ix_delivery_execution_scope",
        "delivery_executions",
        ["workspace_id", "project_id", "workflow_id", "run_id", "id"],
    )
    op.create_table(
        "delivery_execution_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "transport_classification",
            sa.String(32),
            nullable=False,
        ),
        sa.Column("http_status", sa.Integer()),
        sa.Column(
            "receipt_classification",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "protocol_classification",
            sa.String(32),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("receipt_id", sa.String(128)),
        sa.Column("receipt_hash", sa.String(64)),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["delivery_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id",
            "attempt_number",
            name="uq_delivery_execution_result_attempt",
        ),
    )
    op.create_index(
        "ix_delivery_execution_result_execution",
        "delivery_execution_results",
        ["execution_id", "attempt_number"],
    )
    op.create_table(
        "controlled_receiver_deliveries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("receiver_identity", sa.String(255), nullable=False),
        sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("durable_status", sa.String(16), nullable=False),
        sa.Column("receipt_id", sa.String(128), nullable=False),
        sa.Column(
            "receipt_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("receipt_key_id", sa.String(128), nullable=False),
        sa.Column("receipt_signature", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "decision_hash",
            name="uq_controlled_receiver_delivery",
        ),
        sa.UniqueConstraint("receipt_id"),
    )
    op.create_index(
        "ix_controlled_receiver_delivery_receiver",
        "controlled_receiver_deliveries",
        ["receiver_identity", "created_at", "id"],
    )
    op.create_table(
        "controlled_receiver_nonces",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("receiver_identity", sa.String(255), nullable=False),
        sa.Column("key_id", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "receiver_identity",
            "key_id",
            "nonce",
            name="uq_controlled_receiver_nonce",
        ),
    )


def downgrade() -> None:
    op.drop_table("controlled_receiver_nonces")
    op.drop_table("controlled_receiver_deliveries")
    op.drop_table("delivery_execution_results")
    op.drop_table("delivery_executions")
