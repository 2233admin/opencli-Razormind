from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel


class NotificationRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_id: str | None = None
    # WIRING_GAP_LEDGER W2 (resolved): dispatch_notifications() used to only
    # query/fire rules with trigger_event == "on_new_record" with no producer
    # for any other value. Producers now exist for all three values:
    #   - on_new_record   — pipeline step 5 (every successful collect)
    #   - on_ai_processed — pipeline step 5, when AI enrichment ran (ai_count>0)
    #   - on_task_failed  — pipeline failure paths (collect exception / fail),
    #                       with a synthetic failure payload
    trigger_event: Literal["on_new_record", "on_ai_processed", "on_task_failed"]
    notifier_type: str
    notifier_config: dict[str, Any] = Field(default_factory=dict)
    filter_conditions: dict[str, Any] | None = None
    enabled: bool = True


class NotificationRuleUpdate(BaseModel):
    name: str | None = None
    trigger_event: Literal["on_new_record", "on_ai_processed", "on_task_failed"] | None = None
    notifier_type: str | None = None
    notifier_config: dict[str, Any] | None = None
    filter_conditions: dict[str, Any] | None = None
    enabled: bool | None = None


class NotificationRuleRead(UTCModel):
    id: str
    name: str
    source_id: str | None
    trigger_event: str
    notifier_type: str
    notifier_config: dict[str, Any]
    filter_conditions: dict[str, Any] | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationLogRead(UTCModel):
    id: str
    rule_id: str
    record_id: str | None
    status: str
    response_data: dict[str, Any] | None
    error_message: str | None
    ack_status: str
    ack_data: dict[str, Any] | None
    acked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationAckRequest(BaseModel):
    status: Literal["acked", "failed"]
    ack_data: dict[str, Any] = Field(default_factory=dict)
