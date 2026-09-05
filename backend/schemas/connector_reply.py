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
