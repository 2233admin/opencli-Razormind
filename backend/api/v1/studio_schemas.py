"""HTTP schemas for persistent Studio authoring APIs."""

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_serializer

from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import UTCModel
from backend.schemas.workflow_compile import WorkflowCompileError
from backend.schemas.workflow_runtime import (
    WorkflowRunProjection,
    WorkflowRunStatus,
    WorkflowRunTraceResponse,
)

ProjectAppType = Literal["chatbot", "agent", "chatflow", "workflow", "text-generator"]


class WorkspaceRead(UTCModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    slug: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100)
    description: str | None = None
    app_type: ProjectAppType = "workflow"


class ProjectRead(UTCModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str
    slug: str
    description: str | None
    app_type: ProjectAppType
    primary_workflow_id: str | None
    created_by_user_id: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    graph: workflow_schemas.WorkflowProject


class BootstrapWorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    graph: workflow_schemas.WorkflowProject


class ProjectBootstrapCreate(BaseModel):
    project: ProjectCreate
    workflow: BootstrapWorkflowCreate


class WorkflowRead(UTCModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    name: str
    description: str | None
    current_published_version: int | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class DraftUpdate(BaseModel):
    graph: workflow_schemas.WorkflowProject
    revision: int = Field(ge=1)


class DraftRead(UTCModel):
    revision: int
    graph: workflow_schemas.WorkflowProject
    updated_by_user_id: str
    updated_at: datetime

    @field_serializer("graph")
    def serialize_graph(self, graph: workflow_schemas.WorkflowProject) -> dict[str, object]:
        return graph.model_dump(mode="json", exclude_none=True)


class VersionCreate(BaseModel):
    reason: str = Field(min_length=1)
    expected_revision: int = Field(ge=1, alias="expectedRevision")
    validation_run_id: str = Field(min_length=1, alias="validationRunId")


class VersionRead(UTCModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workflow_id: str
    version: int
    draft_revision: int
    graph: dict
    compile_version: str
    published_by_user_id: str
    reason: str
    created_at: datetime


class ProjectBootstrapRead(BaseModel):
    project: ProjectRead
    primary_workflow: WorkflowRead
    draft: DraftRead


class ValidationRunRead(WorkflowRunProjection):
    model_config = ConfigDict(populate_by_name=True)
    draft_revision: int = Field(alias="draftRevision")
    compile_version: str = Field(alias="compileVersion")
    warnings: list[WorkflowCompileError] = Field(default_factory=list)


class ProjectRuntimeLogRead(UTCModel):
    run_id: str
    workflow_id: str
    workflow_name: str
    workflow_version: int | None
    trace_id: str
    status: WorkflowRunStatus
    trigger: str
    response_mode: workflow_schemas.WorkflowRunResponseMode
    event_count: int
    node_count: int
    error_count: int
    duration_ms: int
    started_at: datetime
    updated_at: datetime


class ProjectRuntimeSummaryRead(BaseModel):
    total_runs: int
    successful_runs: int
    failed_runs: int
    blocked_runs: int
    running_runs: int
    total_events: int
    recent_logs: list[ProjectRuntimeLogRead] = Field(default_factory=list)


class ProjectRuntimeTraceRead(BaseModel):
    workflow_version: int | None
    inputs: dict = Field(default_factory=dict)
    user: str | None = None
    response_mode: workflow_schemas.WorkflowRunResponseMode
    trace: WorkflowRunTraceResponse


class PublishedWorkflowRunStart(BaseModel):
    """Public project-run input without allowing callers to replace the graph."""

    inputs: dict = Field(default_factory=dict)
    response_mode: Literal["async"] = "async"
    user: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("user", "initiated_by"),
    )
    request_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=255)
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
        validation_alias=AliasChoices("conversation_id", "conversationId"),
    )
    trigger_kind: workflow_schemas.WorkflowRunTriggerKind | None = Field(
        default=None,
        validation_alias=AliasChoices("trigger_kind", "triggerKind"),
    )
    trigger_node_id: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("trigger_node_id", "triggerNodeId"),
    )
