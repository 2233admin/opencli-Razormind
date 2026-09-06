"""Add connector reply grants, durable delivery, and artifact claims."""

import sqlalchemy as sa
from alembic import op

revision = "cr125p2a"
down_revision = "cr125w1a"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.create_table(
        "connector_reply_grants",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("create_request_id", sa.String(64), nullable=False),
        sa.Column("create_request_hash", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("binding_id", sa.String(36), nullable=False),
        sa.Column("bound_user_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("studio_workspace_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("conversation_context_hash", sa.String(64), nullable=False),
        sa.Column("conversation_revision_cursor", sa.Integer(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("p2p_chat_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_user_id", sa.String(36)),
        sa.Column("active_slot", sa.String(64)),
        sa.Column("execution_receipt_id", sa.String(36)),
        sa.Column("execution_lease_owner", sa.String(64)),
        sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("execution_lease_generation", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("version >= 1", name="ck_connector_reply_grant_version"),
        sa.CheckConstraint(
            "conversation_revision_cursor >= 0", name="ck_connector_reply_grant_revision_cursor"
        ),
        sa.CheckConstraint(
            "execution_lease_generation >= 0", name="ck_connector_reply_grant_lease_generation"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["connector_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"], ["connector_principal_bindings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["bound_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["studio_workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["studio_projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_id"], ["studio_workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["execution_receipt_id"], ["connector_inbound_receipts.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("active_slot"),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "created_by_user_id",
            "create_request_id",
            name="uq_connector_reply_grant_creator_request",
        ),
    )
    for name, columns in (
        ("ix_connector_reply_grants_public_id", ["public_id"]),
        ("ix_connector_reply_grants_created_by_user_id", ["created_by_user_id"]),
        ("ix_connector_reply_grants_installation_id", ["installation_id"]),
        ("ix_connector_reply_grants_workspace_id", ["workspace_id"]),
        ("ix_connector_reply_grants_project_id", ["project_id"]),
        ("ix_connector_reply_grants_conversation_id", ["conversation_id"]),
        ("ix_connector_reply_grant_expiry", ["status", "expires_at"]),
    ):
        op.create_index(name, "connector_reply_grants", columns)

    op.create_table(
        "connector_artifact_grants",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("reply_grant_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("create_request_id", sa.String(64), nullable=False),
        sa.Column("create_request_hash", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("studio_workspace_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("artifact_public_id", sa.String(255), nullable=False),
        sa.Column("artifact_id", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("claim_digest", sa.String(64), nullable=False),
        sa.Column("claim_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(96)),
        sa.Column("active_slot", sa.String(64)),
        *_timestamps(),
        sa.CheckConstraint("version >= 1", name="ck_connector_artifact_grant_version"),
        sa.ForeignKeyConstraint(
            ["reply_grant_id"], ["connector_reply_grants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["studio_workspace_id"], ["studio_workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["studio_projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_id"], ["studio_workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["intelligence_sessions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("claim_digest"),
        sa.UniqueConstraint("active_slot"),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "created_by_user_id",
            "create_request_id",
            name="uq_connector_artifact_grant_creator_request",
        ),
    )
    for name, columns in (
        ("ix_connector_artifact_grants_public_id", ["public_id"]),
        ("ix_connector_artifact_grants_reply_grant_id", ["reply_grant_id"]),
        ("ix_connector_artifact_grants_project_id", ["project_id"]),
        ("ix_connector_artifact_grant_expiry", ["status", "expires_at"]),
    ):
        op.create_index(name, "connector_artifact_grants", columns)

    op.create_table(
        "connector_outbound_deliveries",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column("sdk_uuid", sa.String(50), nullable=False),
        sa.Column("dedupe_slot", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("reply_grant_id", sa.String(36), nullable=False),
        sa.Column("artifact_grant_id", sa.String(36)),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("chat_id", sa.String(255), nullable=False),
        sa.Column("reply_to_message_id", sa.String(255)),
        sa.Column("payload_kind", sa.String(32), nullable=False),
        sa.Column("safe_text", sa.Text()),
        sa.Column("payload_bytes", sa.LargeBinary()),
        sa.Column("file_name", sa.String(255)),
        sa.Column("media_type", sa.String(255)),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("template_version", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("error_code", sa.String(96)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("lease_generation >= 0", name="ck_connector_outbound_lease_generation"),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["connector_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reply_grant_id"], ["connector_reply_grants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_grant_id"], ["connector_artifact_grants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint("sdk_uuid"),
        sa.UniqueConstraint("dedupe_slot"),
        sa.UniqueConstraint(
            "installation_id",
            "provider_message_id",
            name="uq_connector_outbound_provider_message",
        ),
    )
    for name, columns in (
        ("ix_connector_outbound_deliveries_public_id", ["public_id"]),
        ("ix_connector_outbound_deliveries_installation_id", ["installation_id"]),
        ("ix_connector_outbound_deliveries_reply_grant_id", ["reply_grant_id"]),
        ("ix_connector_outbound_recovery", ["status", "lease_expires_at"]),
    ):
        op.create_index(name, "connector_outbound_deliveries", columns)

    with op.batch_alter_table("connector_inbound_receipts") as batch:
        batch.add_column(sa.Column("artifact_grant_id", sa.String(36)))
        batch.add_column(sa.Column("turn_id", sa.String(36)))
        batch.add_column(sa.Column("outbound_delivery_id", sa.String(36)))
        batch.add_column(sa.Column("lease_owner", sa.String(64)))
        batch.add_column(
            sa.Column("lease_generation", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(
            sa.Column("grant_lease_generation", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("processing_started_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_connector_receipt_lease_generation", "lease_generation >= 0"
        )
        batch.create_check_constraint(
            "ck_connector_receipt_grant_lease_generation", "grant_lease_generation >= 0"
        )
        batch.create_unique_constraint(
            "uq_connector_inbound_receipts_outbound_delivery", ["outbound_delivery_id"]
        )
        batch.create_foreign_key(
            "fk_connector_receipt_conversation",
            "agent_conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_connector_receipt_reply_grant",
            "connector_reply_grants",
            ["reply_grant_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_connector_receipt_artifact_grant",
            "connector_artifact_grants",
            ["artifact_grant_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_connector_receipt_turn",
            "agent_conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_connector_receipt_outbound",
            "connector_outbound_deliveries",
            ["outbound_delivery_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_connector_receipt_recovery", ["status", "lease_expires_at"])


def downgrade():
    with op.batch_alter_table("connector_inbound_receipts") as batch:
        batch.drop_index("ix_connector_receipt_recovery")
        for name in (
            "fk_connector_receipt_outbound",
            "fk_connector_receipt_turn",
            "fk_connector_receipt_artifact_grant",
            "fk_connector_receipt_reply_grant",
            "fk_connector_receipt_conversation",
        ):
            batch.drop_constraint(name, type_="foreignkey")
        batch.drop_constraint("uq_connector_inbound_receipts_outbound_delivery", type_="unique")
        batch.drop_constraint("ck_connector_receipt_lease_generation", type_="check")
        batch.drop_constraint("ck_connector_receipt_grant_lease_generation", type_="check")
        for column in (
            "processing_started_at",
            "lease_expires_at",
            "lease_generation",
            "grant_lease_generation",
            "lease_owner",
            "outbound_delivery_id",
            "turn_id",
            "artifact_grant_id",
        ):
            batch.drop_column(column)

    for table, indexes in (
        (
            "connector_outbound_deliveries",
            (
                "ix_connector_outbound_recovery",
                "ix_connector_outbound_deliveries_reply_grant_id",
                "ix_connector_outbound_deliveries_installation_id",
                "ix_connector_outbound_deliveries_public_id",
            ),
        ),
        (
            "connector_artifact_grants",
            (
                "ix_connector_artifact_grant_expiry",
                "ix_connector_artifact_grants_project_id",
                "ix_connector_artifact_grants_reply_grant_id",
                "ix_connector_artifact_grants_public_id",
            ),
        ),
        (
            "connector_reply_grants",
            (
                "ix_connector_reply_grant_expiry",
                "ix_connector_reply_grants_conversation_id",
                "ix_connector_reply_grants_project_id",
                "ix_connector_reply_grants_workspace_id",
                "ix_connector_reply_grants_installation_id",
                "ix_connector_reply_grants_created_by_user_id",
                "ix_connector_reply_grants_public_id",
            ),
        ),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
