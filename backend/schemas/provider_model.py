from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.llm import is_valid_model_source, is_valid_model_type
from backend.schemas.common import UTCModel


class ProviderModelCreate(BaseModel):
    """Body for registering a provider model catalog entry.

    Minimal shape for PR-A: PR-C wires the actual sync/CRUD endpoints (decision
    #10) that construct/consume this; here it's just the validated payload.
    """

    provider_id: str
    model_id: str = Field(..., min_length=1, max_length=255)
    model_type: str = "llm"
    capabilities: dict[str, Any] | None = None
    source: str = "manual"
    enabled: bool = True

    @field_validator("model_type")
    @classmethod
    def _validate_model_type(cls, v: str) -> str:
        if not is_valid_model_type(v):
            raise ValueError(f"invalid model_type: {v!r}")
        return v

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if not is_valid_model_source(v):
            raise ValueError(f"invalid source: {v!r}")
        return v


class ProviderModelManualCreate(BaseModel):
    """Body for ``POST /providers/{id}/models`` (decision #10) — the manual
    catalog-entry endpoint.

    ``provider_id`` comes from the URL path, not repeated in the body.
    ``source`` is always forced to ``"manual"`` server-side
    (``backend.services.provider_model_service.add_manual_model``) — this is
    the only way a ``source="manual"`` row gets created; sync (decision #3)
    never writes one.
    """

    model_id: str = Field(..., min_length=1, max_length=255)
    model_type: str = "llm"
    capabilities: dict[str, Any] | None = None
    enabled: bool = True

    @field_validator("model_type")
    @classmethod
    def _validate_model_type(cls, v: str) -> str:
        if not is_valid_model_type(v):
            raise ValueError(f"invalid model_type: {v!r}")
        return v


class ProviderModelUpdate(BaseModel):
    """Body for ``PATCH /providers/{id}/models/{model_row_id}`` (decision
    #10). All fields optional (only supplied ones change). ``model_id`` /
    ``provider_id`` / ``source`` are immutable via this endpoint — flipping a
    row between "discovered" and "manual" isn't a partial-update concern,
    it's decided entirely by which endpoint created the row (decision #3).
    """

    model_type: str | None = None
    capabilities: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("model_type")
    @classmethod
    def _validate_model_type(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_model_type(v):
            raise ValueError(f"invalid model_type: {v!r}")
        return v


class ProviderModelRead(UTCModel):
    id: str
    provider_id: str
    model_id: str
    model_type: str
    capabilities: dict[str, Any] | None
    source: str
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
