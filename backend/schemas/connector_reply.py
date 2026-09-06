"""Public connector administration schemas; secrets are write-only."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.common import UTCModel


class ConnectorInstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    app_id: str = Field(min_length=1, max_length=255)
    tenant_key: str = Field(min_length=1, max_length=255)
    app_secret: str = Field(min_length=1, max_length=4096)
    encrypt_key: str = Field(min_length=1, max_length=4096)
    verification_token: str = Field(min_length=1, max_length=4096)


class ConnectorInstallationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    app_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    encrypt_key: str | None = Field(default=None, min_length=1, max_length=4096)
    verification_token: str | None = Field(default=None, min_length=1, max_length=4096)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_null_and_empty_updates(self):
        if not self.model_fields_set:
            raise ValueError("at least one installation field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("installation fields cannot be null")
        return self


class ConnectorInstallationRead(UTCModel):
    installation_public_id: str
    workspace_id: str
    provider: str
    name: str
    app_id: str
    tenant_key: str
    status: str
    config_revision: int
    has_app_secret: bool
    has_encrypt_key: bool
    has_verification_token: bool
    created_at: datetime
    updated_at: datetime


class ConnectorInstallationHealth(UTCModel):
    installation_public_id: str
    status: str
    callback_ready: bool
    binding_ready: bool
    reply_execution_ready: bool = False
    artifact_delivery_ready: bool = False
    last_ready_at: datetime | None
    last_error_code: str | None


class ConnectorBindingChallengeRead(UTCModel):
    challenge_public_id: str
    installation_public_id: str
    command_text: str
    expires_at: datetime


class ConnectorBindingRead(UTCModel):
    binding_public_id: str
    installation_public_id: str
    user_id: str
    active: bool
    revoked_at: datetime | None
    updated_at: datetime


class ConnectorReplyGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=64)
    installation_public_id: str = Field(min_length=1, max_length=36)
    binding_public_id: str = Field(min_length=1, max_length=36)
    conversation_id: str = Field(min_length=1, max_length=36)
    expires_in_seconds: int = Field(default=604800, ge=300, le=2_592_000)


class ConnectorReplyGrantRead(UTCModel):
    reply_grant_public_id: str
    installation_public_id: str
    binding_public_id: str
    workspace_id: str
    studio_workspace_id: str
    project_id: str
    workflow_id: str | None
    run_id: str | None
    conversation_id: str
    status: str
    version: int
    expires_at: datetime
    revoked_at: datetime | None
    activation_delivery_status: str | None
    created_at: datetime
    updated_at: datetime


class ConnectorReplyGrantCreated(ConnectorReplyGrantRead):
    created: bool


class ConnectorArtifactGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=64)
    artifact_public_id: str = Field(min_length=1, max_length=255)
    workflow_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    expires_in_seconds: int = Field(default=1800, ge=300, le=86_400)


class ConnectorArtifactGrantRead(UTCModel):
    artifact_grant_public_id: str
    reply_grant_public_id: str
    artifact_public_id: str
    project_id: str
    workflow_id: str
    run_id: str
    session_id: str
    content_hash: str
    title: str
    media_type: str
    simulated: bool
    status: str
    version: int
    expires_at: datetime
    offer_delivery_status: str | None
    delivery_status: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ConnectorArtifactGrantCreated(ConnectorArtifactGrantRead):
    created: bool
    claim_text: str | None
