from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.automation_schedule import parse_automation_schedule, parse_automation_timezone
from backend.schemas.common import UTCModel

SessionMode = Literal["fresh", "reuse"]
ApprovalMode = Literal["observe_only", "suggest_changes", "low_risk_automatic"]
StarterKey = Literal["daily-run-brief", "weekly-system-review", "anomaly-follow-up"]
STARTER_KEYS: tuple[str, ...] = (
    "daily-run-brief",
    "weekly-system-review",
    "anomaly-follow-up",
)


class AutomationCreate(BaseModel):
    operations_agent_id: str | None = None
    operations_agent_version: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=20000)
    precheck: str | None = Field(default=None, max_length=4000)
    executor: str = Field(min_length=1, max_length=64)
    schedule: str = Field(min_length=1, max_length=255)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    session_mode: SessionMode = "fresh"
    approval_mode: ApprovalMode = "suggest_changes"
    project: dict = Field(default_factory=dict)
    enabled: bool = True
    starter_key: StarterKey | None = None

    @field_validator("schedule")
    @classmethod
    def schedule_is_supported(cls, value: str) -> str:
        parse_automation_schedule(value)
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_is_supported(cls, value: str) -> str:
        parse_automation_timezone(value)
        return value

    @model_validator(mode="after")
    def enabled_automation_has_pinned_agent(self):
        paired = self.operations_agent_id is not None and self.operations_agent_version is not None
        if (self.operations_agent_id is None) != (self.operations_agent_version is None):
            raise ValueError(
                "operations_agent_id and operations_agent_version must be set together"
            )
        if self.enabled and not paired:
            raise ValueError("enabled Automation requires a pinned published Operations Agent")
        return self

class AutomationUpdate(BaseModel):
    operations_agent_id: str | None = None
    operations_agent_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    precheck: str | None = Field(default=None, max_length=4000)
    executor: str | None = Field(default=None, min_length=1, max_length=64)
    schedule: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    session_mode: SessionMode | None = None
    approval_mode: ApprovalMode | None = None
    project: dict | None = None
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def updated_schedule_is_supported(cls, value: str | None) -> str | None:
        if value is not None:
            parse_automation_schedule(value)
        return value

    @field_validator("timezone")
    @classmethod
    def updated_timezone_is_supported(cls, value: str | None) -> str | None:
        if value is not None:
            parse_automation_timezone(value)
        return value

    @model_validator(mode="after")
    def updated_agent_binding_is_paired(self):
        fields = self.model_fields_set
        binding_fields = {"operations_agent_id", "operations_agent_version"}
        if fields & binding_fields and not binding_fields <= fields:
            raise ValueError(
                "operations_agent_id and operations_agent_version must be updated together"
            )
        if binding_fields <= fields and (
            (self.operations_agent_id is None) != (self.operations_agent_version is None)
        ):
            raise ValueError(
                "operations_agent_id and operations_agent_version must both be set or null"
            )
        return self


class AutomationRead(UTCModel):
    id: str
    workspace_id: str
    starter_key: StarterKey | None
    revision: int
    operations_agent_id: str | None
    operations_agent_version: int | None
    name: str
    prompt: str
    precheck: str | None
    executor: str
    schedule: str
    timezone: str
    session_mode: str
    approval_mode: str
    project: dict
    enabled: bool
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AutomationSchedulerTickRequest(BaseModel):
    fired_at: datetime

    @field_validator("fired_at")
    @classmethod
    def fired_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fired_at must include a timezone")
        return value


class AutomationSchedulerTickResult(BaseModel):
    run_ids: list[str]
    occurrence_references: list[str]
    queued_run_ids: list[str]


class StarterPreviewItem(BaseModel):
    key: StarterKey
    name: str
    installed: bool
    automation_id: str | None = None


class StarterInstallationPreview(BaseModel):
    workspace_id: str
    starters: list[StarterPreviewItem]
    missing_count: int
    installed_count: int


class StarterInstallationResult(StarterInstallationPreview):
    created_count: int
    skipped_count: int

    model_config = {"from_attributes": True}


AgentWorkKind = Literal["conversation", "run", "automation"]
AgentWorkState = Literal[
    "not_started",
    "ready",
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "blocked",
    "inactive",
    "cancelled",
]
AgentWorkActionKind = Literal[
    "open_conversation",
    "pause_run",
    "run_automation",
    "retry_automation",
    "configure",
]


class AgentWorkActionRead(BaseModel):
    kind: AgentWorkActionKind
    label: str
    conversation_id: str | None = None
    operations_agent_id: str | None = None
    run_id: str | None = None
    automation_id: str | None = None

    @model_validator(mode="after")
    def action_has_required_target(self):
        required = {
            "open_conversation": self.conversation_id,
            "pause_run": self.run_id and self.operations_agent_id,
            "run_automation": self.automation_id,
            "retry_automation": self.automation_id,
            "configure": self.operations_agent_id or self.automation_id,
        }
        if not required[self.kind]:
            raise ValueError(f"{self.kind} action requires its target identifier")
        return self


class AgentWorkBindingRead(BaseModel):
    operations_agent_id: str
    published_version: int
    profile_version: int
    automation_revision: int | None = None
    runtime: str | None = None
    agent_url: str | None = None


class AgentWorkLatestRunRead(UTCModel):
    id: str
    status: str
    trigger_type: str
    trigger_reference: str | None
    scheduled_for: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentWorkHealthItemRead(UTCModel):
    id: str
    kind: AgentWorkKind
    title: str
    state: AgentWorkState
    message: str
    reason_code: str | None = None
    recoverable: bool
    resume_supported: bool
    project_id: str | None = None
    conversation_id: str | None = None
    operations_agent_id: str | None = None
    run_id: str | None = None
    automation_id: str | None = None
    binding: AgentWorkBindingRead | None = None
    latest_run: AgentWorkLatestRunRead | None = None
    next_due_at: datetime | None = None
    actions: list[AgentWorkActionRead] = Field(default_factory=list)
    updated_at: datetime


class AgentWorkHealthPermissionsRead(BaseModel):
    can_run: bool
    can_manage: bool


class AgentWorkHealthRead(BaseModel):
    workspace_id: str
    studio_workspace_id: str | None = None
    project_id: str | None = None
    generated_at: datetime
    permissions: AgentWorkHealthPermissionsRead
    counts: dict[str, int]
    items: list[AgentWorkHealthItemRead]
