from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel


class CronScheduleCreate(BaseModel):
    source_id: str
    name: str = Field(..., min_length=1, max_length=255)
    cron_expression: str = Field(..., description="5-field cron expression")
    timezone: str = "UTC"
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_one_time: bool = False
    agent_id: str | None = None


class CronScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    timezone: str | None = None
    parameters: dict[str, Any] | None = None
    enabled: bool | None = None
    is_one_time: bool | None = None
    agent_id: str | None = None


class CronScheduleRead(UTCModel):
    id: str
    source_id: str
    agent_id: str | None = None

    name: str
    cron_expression: str
    timezone: str
    parameters: dict[str, Any]
    enabled: bool
    is_one_time: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
