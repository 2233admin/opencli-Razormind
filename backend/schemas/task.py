from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel


class TaskTriggerRequest(BaseModel):
    source_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    agent_id: str | None = None


class TaskRecoveryRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["recollect"] = "recollect"
    initiating_actor: str = Field(default="operator", min_length=1, max_length=255)


class TaskRecoveryRead(BaseModel):
    task_id: str
    retry_of_task_id: str
    status: str
    recovery_mode: str
    idempotency_replayed: bool = False


class CollectionTaskRead(UTCModel):
    id: str
    source_id: str
    source_name: str | None = None
    agent_id: str | None = None
    trigger_type: str
    parameters: dict[str, Any]
    priority: int
    status: str
    error_message: str | None = None
    retry_of_task_id: str | None = None
    recovery_mode: str | None = None
    recovery_reason: str | None = None
    initiating_actor: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskRunRead(UTCModel):
    id: str
    task_id: str
    status: str
    worker_id: str | None
    celery_task_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    records_collected: int
    error_message: str | None
    error_detail: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
