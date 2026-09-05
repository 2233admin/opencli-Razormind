"""Persistent trust boundary for external connector callbacks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
