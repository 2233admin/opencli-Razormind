from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel
from backend.schemas.provider_capacity import ProviderCapacityRead, project_provider_capacity


class ModelProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider_type: str = "openai"
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    notes: str | None = None
    enabled: bool = True


class ModelProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    notes: str | None = None
    enabled: bool | None = None


class ProviderModelDiscoveryRequest(BaseModel):
    """Unsaved provider settings used to discover model IDs before create.

    The API key is write-only just like ModelProviderCreate.api_key: it is
    passed directly to the adapter for this request and is never persisted or
    returned in the response.
    """

    provider_type: str = "openai"
    base_url: str | None = None
    api_key: str | None = None


class ModelProviderRead(UTCModel):
    """Response shape for GET/POST/PATCH /providers.

    ``api_key`` is intentionally NOT included — the raw key is write-only
    (accepted by ModelProviderCreate/Update, never echoed back). Instead this
    exposes ``has_api_key`` (a boolean) and ``api_key_preview`` (a masked
    ``sk-...last4``-style hint) so the UI can show "a key is configured"
    without the plaintext ever leaving the server after creation. See AUDIT
    item B3 — GET /providers previously returned ModelProvider.api_key in the
    clear to any caller on the LAN.
    """

    id: str
    name: str
    provider_type: str
    base_url: str | None
    has_api_key: bool
    api_key_preview: str | None
    default_model: str | None
    notes: str | None
    enabled: bool
    capacity: ProviderCapacityRead
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, provider: Any) -> "ModelProviderRead":
        """Build the masked response from a ModelProvider ORM row.

        Pydantic's ``from_attributes`` can't derive ``has_api_key`` /
        ``api_key_preview`` from the model directly (the model only has a
        plaintext ``api_key`` column) — this does the masking explicitly
        before validation instead of exposing that column in the schema.
        """
        raw_key = getattr(provider, "api_key", None)
        return cls.model_validate(
            {
                "id": provider.id,
                "name": provider.name,
                "provider_type": provider.provider_type,
                "base_url": provider.base_url,
                "has_api_key": bool(raw_key),
                "api_key_preview": _mask_api_key(raw_key),
                "default_model": provider.default_model,
                "notes": provider.notes,
                "enabled": provider.enabled,
                "capacity": project_provider_capacity(provider),
                "created_at": provider.created_at,
                "updated_at": provider.updated_at,
            }
        )


def _mask_api_key(raw_key: str | None) -> str | None:
    """``sk-abcd1234...wxyz`` -> ``sk-...wxyz`` (last 4 chars); None when unset
    or too short to safely preview."""
    if not raw_key:
        return None
    tail = raw_key[-4:]
    prefix = raw_key[:3] if len(raw_key) >= 8 else ""
    return f"{prefix}...{tail}" if prefix else "...." + tail
