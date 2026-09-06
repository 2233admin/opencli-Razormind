from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.common import UTCModel


class AgentConversationCreate(BaseModel):
    workspace_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, max_length=255)
    context: dict[str, Any] | None = None
    execution_target_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_id: str | None = Field(default=None, min_length=1, max_length=255)


class AgentConversationMessageCreate(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=20_000)
    context: dict[str, Any] | None = None
    execution_mode: Literal["synchronous", "background"] = "synchronous"


class AgentConversationTurnRead(UTCModel):
    id: str
    sequence: int
    request_id: str
    agent_run_id: str | None
    user_content: str
    response: dict[str, Any] | None
    context_binding: dict[str, Any]
    tool_trace: list[dict[str, Any]]
    status: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentConversationRead(UTCModel):
    id: str
    workspace_id: str
    title: str | None
    status: str
    created_by_user_id: str
    context_binding: dict[str, Any]
    execution_binding: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentConversationDetail(AgentConversationRead):
    turns: list[AgentConversationTurnRead] = Field(default_factory=list)


class AgentConversationMessageRead(UTCModel):
    conversation_id: str
    turn: AgentConversationTurnRead


class AgentExecutionReadiness(BaseModel):
    status: Literal["ready", "unverified", "blocked"]
    reason_code: str | None = None
    reason: str | None = None


class AgentExecutionTargetRead(BaseModel):
    id: str
    kind: Literal["provider", "native"]
    label: str
    agent: dict[str, Any]
    runtime: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None
    models: list[dict[str, str]] = Field(default_factory=list)
    default_model_id: str | None = None
    readiness: AgentExecutionReadiness
    setup_url: str


class AgentExecutionTargetsRead(BaseModel):
    workspace_id: str
    background_execution: AgentExecutionReadiness
    targets: list[AgentExecutionTargetRead] = Field(default_factory=list)


class AgentRunContinuationRead(BaseModel):
    mode: Literal["history_replay", "history_compacted", "runtime_resume"]
    resumed: bool = False


class AgentConversationRunRead(UTCModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    continuation: AgentRunContinuationRead


class AgentConversationBackgroundMessageRead(UTCModel):
    conversation_id: str
    turn: AgentConversationTurnRead
    run: AgentConversationRunRead


class AgentConversationRunEventRead(UTCModel):
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentConversationRunEventsRead(UTCModel):
    run: AgentConversationRunRead
    events: list[AgentConversationRunEventRead] = Field(default_factory=list)
    next_sequence: int
    terminal: bool
