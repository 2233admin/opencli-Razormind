"""API contracts for Workspace-scoped Browser Spaces."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

OwnerType = Literal["operator", "runtime_agent"]
SpaceStatus = Literal["idle", "running", "closed", "error"]
TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
EventKind = Literal["queued", "started", "completed", "failed", "cancel_requested", "cancelled"]


class BrowserSpaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser_instance_id: str = Field(min_length=1, max_length=64)
    binding_id: str | None = Field(default=None, min_length=1, max_length=64)
    owner_type: OwnerType
    owner_id: str = Field(min_length=1, max_length=255)
    granted_capabilities: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("granted_capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("granted_capabilities must not contain duplicates")
        if any(not value.strip() or len(value) > 255 for value in values):
            raise ValueError("capability names must be non-empty and at most 255 characters")
        return values


class BrowserSpaceTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64)
    capability: str = Field(min_length=1, max_length=255)
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    gate: str | None = Field(default=None, max_length=100)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("capability must not be blank")
        return value

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("args must be JSON-serializable") from exc
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("args exceeds the 64 KiB limit")
        return value


class BrowserSpaceTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    space_id: str
    task_id: str
    operation_id: str
    capability: str | None = None
    status: TaskStatus
    result: Any | None = None
    error: str | None = None


class BrowserSpaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    browser_instance_id: str
    binding_id: str | None
    owner_type: OwnerType
    owner_id: str
    status: SpaceStatus
    granted_capabilities: list[str]
    revision: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    active_task: BrowserSpaceTaskRead | None = None
    latest_task: BrowserSpaceTaskRead | None = None


class BrowserSpaceEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    space_id: str
    task_id: str | None
    sequence: int
    kind: EventKind
    payload: dict[str, Any]
    created_at: datetime


class BrowserSpaceTaskEnvelope(BaseModel):
    """Task response with the same shape for accepted and idempotent requests."""

    data: BrowserSpaceTaskRead
