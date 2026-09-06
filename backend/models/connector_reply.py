"""Persistent trust boundary for external connector callbacks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.auth import crypto
from backend.models.base import TimestampMixin


class ConnectorInstallation(TimestampMixin):
    __tablename__ = "connector_installations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "tenant_key",
            "app_id",
            name="uq_connector_installation_provider_tenant_app",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="feishu")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    app_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    _app_secret_encrypted: Mapped[str] = mapped_column("app_secret", Text, nullable=False)
    _encrypt_key_encrypted: Mapped[str] = mapped_column("encrypt_key", Text, nullable=False)
    _verification_token_encrypted: Mapped[str] = mapped_column(
        "verification_token", Text, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_ready_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @property
    def app_secret(self) -> str:
        return crypto.decrypt(self._app_secret_encrypted)

    @app_secret.setter
    def app_secret(self, value: str) -> None:
        self._app_secret_encrypted = crypto.encrypt(value)

    @property
    def encrypt_key(self) -> str:
        return crypto.decrypt(self._encrypt_key_encrypted)

    @encrypt_key.setter
    def encrypt_key(self, value: str) -> None:
        self._encrypt_key_encrypted = crypto.encrypt(value)

    @property
    def verification_token(self) -> str:
        return crypto.decrypt(self._verification_token_encrypted)

    @verification_token.setter
    def verification_token(self, value: str) -> None:
        self._verification_token_encrypted = crypto.encrypt(value)


class ConnectorBindingChallenge(TimestampMixin):
    __tablename__ = "connector_binding_challenges"

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    challenge_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ConnectorPrincipalBinding(TimestampMixin):
    __tablename__ = "connector_principal_bindings"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "tenant_key", "open_id", name="uq_connector_binding_remote_principal"
        ),
        UniqueConstraint("installation_id", "user_id", name="uq_connector_binding_local_principal"),
    )

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    open_id: Mapped[str] = mapped_column(String(255), nullable=False)
    p2p_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ConnectorInboundReceipt(TimestampMixin):
    __tablename__ = "connector_inbound_receipts"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "provider_message_id", name="uq_connector_receipt_provider_message"
        ),
        CheckConstraint("lease_generation >= 0", name="ck_connector_receipt_lease_generation"),
        CheckConstraint(
            "grant_lease_generation >= 0", name="ck_connector_receipt_grant_lease_generation"
        ),
        Index("ix_connector_receipt_recovery", "status", "lease_expires_at"),
    )

    installation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_open_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safe_content_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stable_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_principal_bindings.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    reply_grant_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_reply_grants.id", ondelete="SET NULL"), nullable=True
    )
    artifact_grant_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_artifact_grants.id", ondelete="SET NULL"), nullable=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversation_turns.id", ondelete="SET NULL"), nullable=True
    )
    outbound_delivery_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_outbound_deliveries.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    grant_lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ConnectorReplyGrant(TimestampMixin):
    __tablename__ = "connector_reply_grants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "created_by_user_id",
            "create_request_id",
            name="uq_connector_reply_grant_creator_request",
        ),
        CheckConstraint("version >= 1", name="ck_connector_reply_grant_version"),
        CheckConstraint(
            "conversation_revision_cursor >= 0",
            name="ck_connector_reply_grant_revision_cursor",
        ),
        CheckConstraint(
            "execution_lease_generation >= 0",
            name="ck_connector_reply_grant_lease_generation",
        ),
        Index("ix_connector_reply_grant_expiry", "status", "expires_at"),
    )

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    create_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    create_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("connector_principal_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    bound_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    studio_workspace_id: Mapped[str] = mapped_column(
        ForeignKey("studio_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("studio_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("studio_workflows.id", ondelete="RESTRICT"), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_revision_cursor: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    p2p_chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active_slot: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    execution_receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_inbound_receipts.id", ondelete="SET NULL"), nullable=True
    )
    execution_lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    execution_lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class ConnectorOutboundDelivery(TimestampMixin):
    __tablename__ = "connector_outbound_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "provider_message_id",
            name="uq_connector_outbound_provider_message",
        ),
        CheckConstraint("lease_generation >= 0", name="ck_connector_outbound_lease_generation"),
        Index("ix_connector_outbound_recovery", "status", "lease_expires_at"),
    )

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sdk_uuid: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    dedupe_slot: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reply_grant_id: Mapped[str] = mapped_column(
        ForeignKey("connector_reply_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_grant_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_artifact_grants.id", ondelete="CASCADE"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ConnectorArtifactGrant(TimestampMixin):
    __tablename__ = "connector_artifact_grants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "created_by_user_id",
            "create_request_id",
            name="uq_connector_artifact_grant_creator_request",
        ),
        CheckConstraint("version >= 1", name="ck_connector_artifact_grant_version"),
        Index("ix_connector_artifact_grant_expiry", "status", "expires_at"),
    )

    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    reply_grant_id: Mapped[str] = mapped_column(
        ForeignKey("connector_reply_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    create_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    create_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    studio_workspace_id: Mapped[str] = mapped_column(
        ForeignKey("studio_workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("studio_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("studio_workflows.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_public_id: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("intelligence_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claim_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    claim_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    active_slot: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
