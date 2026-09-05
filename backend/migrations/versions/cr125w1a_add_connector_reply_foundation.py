"""Add strict connector installation, binding, and receipt foundation."""

import sqlalchemy as sa
from alembic import op

revision = "cr125w1a"
down_revision = "acc20260905c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connector_installations",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("app_id", sa.String(255), nullable=False),
        sa.Column("tenant_key", sa.String(255), nullable=False),
        sa.Column("app_secret", sa.Text(), nullable=False),
        sa.Column("encrypt_key", sa.Text(), nullable=False),
        sa.Column("verification_token", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("last_ready_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(96)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "tenant_key",
            "app_id",
            name="uq_connector_installation_provider_tenant_app",
        ),
    )
    op.create_index(
        "ix_connector_installations_public_id", "connector_installations", ["public_id"]
    )
    op.create_index(
        "ix_connector_installations_workspace_id", "connector_installations", ["workspace_id"]
    )
    op.create_table(
        "connector_binding_challenges",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("challenge_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["connector_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("challenge_digest"),
    )
    op.create_index(
        "ix_connector_binding_challenges_public_id", "connector_binding_challenges", ["public_id"]
    )
    op.create_index(
        "ix_connector_binding_challenges_installation_id",
        "connector_binding_challenges",
        ["installation_id"],
    )
    op.create_index(
        "ix_connector_binding_challenges_workspace_id",
        "connector_binding_challenges",
        ["workspace_id"],
    )
    op.create_index(
        "ix_connector_binding_challenges_user_id", "connector_binding_challenges", ["user_id"]
    )
    op.create_table(
        "connector_principal_bindings",
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_key", sa.String(255), nullable=False),
        sa.Column("open_id", sa.String(255), nullable=False),
        sa.Column("p2p_chat_id", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_user_id", sa.String(36)),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["connector_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint(
            "installation_id", "tenant_key", "open_id", name="uq_connector_binding_remote_principal"
        ),
        sa.UniqueConstraint(
            "installation_id", "user_id", name="uq_connector_binding_local_principal"
        ),
    )
    op.create_index(
        "ix_connector_principal_bindings_public_id", "connector_principal_bindings", ["public_id"]
    )
    op.create_index(
        "ix_connector_principal_bindings_installation_id",
        "connector_principal_bindings",
        ["installation_id"],
    )
    op.create_index(
        "ix_connector_principal_bindings_workspace_id",
        "connector_principal_bindings",
        ["workspace_id"],
    )
    op.create_index(
        "ix_connector_principal_bindings_user_id", "connector_principal_bindings", ["user_id"]
    )
    op.create_table(
        "connector_inbound_receipts",
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=False),
        sa.Column("provider_event_id", sa.String(255)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("tenant_key", sa.String(255), nullable=False),
        sa.Column("sender_open_id", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.String(255), nullable=False),
        sa.Column("reply_to_message_id", sa.String(255)),
        sa.Column("safe_content_text", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stable_error_code", sa.String(96)),
        sa.Column("binding_id", sa.String(36)),
        sa.Column("conversation_id", sa.String(36)),
        sa.Column("reply_grant_id", sa.String(36)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["connector_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"], ["connector_principal_bindings.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "installation_id", "provider_message_id", name="uq_connector_receipt_provider_message"
        ),
    )
    op.create_index(
        "ix_connector_inbound_receipts_installation_id",
        "connector_inbound_receipts",
        ["installation_id"],
    )


def downgrade():
    op.drop_index(
        "ix_connector_inbound_receipts_installation_id", table_name="connector_inbound_receipts"
    )
    op.drop_table("connector_inbound_receipts")
    for index in (
        "ix_connector_principal_bindings_user_id",
        "ix_connector_principal_bindings_workspace_id",
        "ix_connector_principal_bindings_installation_id",
        "ix_connector_principal_bindings_public_id",
    ):
        op.drop_index(index, table_name="connector_principal_bindings")
    op.drop_table("connector_principal_bindings")
    for index in (
        "ix_connector_binding_challenges_user_id",
        "ix_connector_binding_challenges_workspace_id",
        "ix_connector_binding_challenges_installation_id",
        "ix_connector_binding_challenges_public_id",
    ):
        op.drop_index(index, table_name="connector_binding_challenges")
    op.drop_table("connector_binding_challenges")
    op.drop_index("ix_connector_installations_workspace_id", table_name="connector_installations")
    op.drop_index("ix_connector_installations_public_id", table_name="connector_installations")
    op.drop_table("connector_installations")
