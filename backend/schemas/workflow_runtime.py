"""Replayable workflow-run contracts shared by runtime, research, and evidence APIs."""


from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.workflow_compile import WorkflowCompileError


class WorkflowRuntimeModel(BaseModel):
    """Base model preserving camelCase wire names with Pythonic attributes."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    def __getattr__(self, name: str) -> Any:
        for field_name, field_info in type(self).model_fields.items():
            if field_info.alias == name:
                return super().__getattribute__(field_name)
        return super().__getattr__(name)


WORKFLOW_NODE_PATH_SEPARATOR = "::"


def normalize_workflow_node_path(
    *,
    node_id: str,
    node_path: list[str],
    package_node_id: str | None,
    internal_node_id: str | None,
) -> list[str]:
    if node_path:
        return node_path
    if package_node_id and internal_node_id:
        return [
            *package_node_id.split(WORKFLOW_NODE_PATH_SEPARATOR),
            *internal_node_id.split(WORKFLOW_NODE_PATH_SEPARATOR),
        ]
    if WORKFLOW_NODE_PATH_SEPARATOR in node_id:
        return node_id.split(WORKFLOW_NODE_PATH_SEPARATOR)
    return [node_id]


def legacy_workflow_node_location(node_path: list[str]) -> tuple[str | None, str | None]:
    if len(node_path) <= 1:
        return None, None
    return WORKFLOW_NODE_PATH_SEPARATOR.join(node_path[:-1]), node_path[-1]


WorkflowRunStatus = Literal[
    "queued",
    "running",
    "partial",
    "partial_success",
    "waiting",
    "blocked",
    "completed",
    "failed",
]
WorkflowResearchStatus = Literal[
    "running",
    "needs_evidence",
    "final",
    "incomplete",
    "blocked",
    "failed",
]
WorkflowNodeRunEventType = Literal[
    "queued",
    "started",
    "waiting",
    "blocked",
    "batch_ready",
    "tool_call_started",
    "tool_call_completed",
    "partial",
    "completed",
    "failed",
]

class WorkflowRunBlockReason(WorkflowRuntimeModel):
    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowRuntimeResourceRequirement(WorkflowRuntimeModel):
    node_id: str = Field(..., min_length=1, alias="nodeId")
    source_group: str = Field(..., min_length=1, alias="sourceGroup")
    site: str = Field(..., min_length=1)
    mutation_mode: Literal["read", "write"] = Field(alias="mutationMode")
    requested_capability: str = Field(..., min_length=1, alias="requestedCapability")
    adapter_node_id: str | None = Field(default=None, alias="adapterNodeId")


class WorkflowRuntimeResourceResolution(WorkflowRuntimeModel):
    status: Literal["resolved", "blocked"]
    adapter_node_id: str | None = Field(default=None, alias="adapterNodeId")
    command: str | None = None
    worker_slot_id: str | None = Field(default=None, alias="workerSlotId")
    profile_binding_id: str | None = Field(default=None, alias="profileBindingId")
    session_snapshot_id: str | None = Field(default=None, alias="sessionSnapshotId")
    lock_id: str | None = Field(default=None, alias="lockId")
    concurrency_limit: int | None = Field(default=None, ge=1, alias="concurrencyLimit")
    block_reason: WorkflowRunBlockReason | None = Field(default=None, alias="blockReason")


class WorkflowRunBatchReference(WorkflowRuntimeModel):
    batch_id: str = Field(..., min_length=1, alias="batchId")
    item_count: int = Field(..., ge=0, alias="itemCount")
    record_count: int = Field(..., ge=0, alias="recordCount")
    source_group: str | None = Field(default=None, alias="sourceGroup")
    adapter_task_id: str | None = Field(default=None, alias="adapterTaskId")
    odp_ref: str | None = Field(default=None, alias="odpRef")
    manifest_uri: str | None = Field(default=None, alias="manifestUri")


class WorkflowNodeRunEvent(WorkflowRuntimeModel):
    id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    workflow_id: str = Field(..., min_length=1, alias="workflowId")
    workflow_run_id: str = Field(..., min_length=1, alias="workflowRunId")
    trace_id: str = Field(..., min_length=1, alias="traceId")
    node_id: str = Field(..., min_length=1, alias="nodeId")
    source_id: str | None = Field(default=None, alias="sourceId")
    event_type: WorkflowNodeRunEventType = Field(alias="eventType")
    created_at: str = Field(..., min_length=1, alias="createdAt")
    node_path: list[str] = Field(default_factory=list, alias="nodePath")
    package_node_id: str | None = Field(default=None, alias="packageNodeId")
    internal_node_id: str | None = Field(default=None, alias="internalNodeId")
    source_group: str | None = Field(default=None, alias="sourceGroup")
    message: str | None = None
    block_reason: WorkflowRunBlockReason | None = Field(default=None, alias="blockReason")
    batch: WorkflowRunBatchReference | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> WorkflowNodeRunEvent:
        self.node_path = normalize_workflow_node_path(
            node_id=self.node_id,
            node_path=self.node_path,
            package_node_id=self.package_node_id,
            internal_node_id=self.internal_node_id,
        )
        package_node_id, internal_node_id = legacy_workflow_node_location(self.node_path)
        self.package_node_id = package_node_id
        self.internal_node_id = internal_node_id
        return self


class WorkflowRunNodeState(WorkflowRuntimeModel):
    node_id: str = Field(..., min_length=1, alias="nodeId")
    status: WorkflowRunStatus = "queued"
    node_path: list[str] = Field(default_factory=list, alias="nodePath")
    package_node_id: str | None = Field(default=None, alias="packageNodeId")
    internal_node_id: str | None = Field(default=None, alias="internalNodeId")
    source_groups: list[str] = Field(default_factory=list, alias="sourceGroups")
    latest_event_id: str | None = Field(default=None, alias="latestEventId")
    event_count: int = Field(0, ge=0, alias="eventCount")
    block_reasons: list[WorkflowRunBlockReason] = Field(
        default_factory=list, alias="blockReasons"
    )
    batches: list[WorkflowRunBatchReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_node_location(self) -> WorkflowRunNodeState:
        self.node_path = normalize_workflow_node_path(
            node_id=self.node_id,
            node_path=self.node_path,
            package_node_id=self.package_node_id,
            internal_node_id=self.internal_node_id,
        )
        package_node_id, internal_node_id = legacy_workflow_node_location(self.node_path)
        self.package_node_id = package_node_id
        self.internal_node_id = internal_node_id
        return self


class WorkflowRunProjection(WorkflowRuntimeModel):
    workflow_id: str = Field(..., min_length=1, alias="workflowId")
    run_id: str = Field(..., min_length=1, alias="runId")
    trace_id: str = Field(..., min_length=1, alias="traceId")
    valid: bool
    status: WorkflowRunStatus
    package_node_id: str | None = Field(default=None, alias="packageNodeId")
    started_at: str = Field(..., min_length=1, alias="startedAt")
    updated_at: str = Field(..., min_length=1, alias="updatedAt")
    event_count: int = Field(..., ge=0, alias="eventCount")
    node_states: list[WorkflowRunNodeState] = Field(default_factory=list, alias="nodeStates")
    errors: list[WorkflowCompileError] = Field(default_factory=list)


class WorkflowRunCheckpoint(WorkflowRuntimeModel):
    checkpoint_id: str = Field(..., min_length=1, alias="checkpointId")
    workflow_id: str = Field(..., min_length=1, alias="workflowId")
    run_id: str = Field(..., min_length=1, alias="runId")
    trace_id: str = Field(..., min_length=1, alias="traceId")
    status: WorkflowRunStatus
    valid: bool
    event_count: int = Field(..., ge=0, alias="eventCount")
    last_sequence: int = Field(0, ge=0, alias="lastSequence")
    updated_at: str = Field(..., min_length=1, alias="updatedAt")
    node_states: list[WorkflowRunNodeState] = Field(default_factory=list, alias="nodeStates")
    source_output_node_ids: list[str] = Field(
        default_factory=list, alias="sourceOutputNodeIds"
    )
    source_output_item_count: int = Field(0, ge=0, alias="sourceOutputItemCount")
    waiting_node_ids: list[str] = Field(default_factory=list, alias="waitingNodeIds")
    pending_jobs: list[dict[str, Any]] = Field(default_factory=list, alias="pendingJobs")
    can_continue_with_source_outputs: bool = Field(
        True, alias="canContinueWithSourceOutputs"
    )
    continuation_path: str = Field(..., min_length=1, alias="continuationPath")
    trace_path: str = Field(..., min_length=1, alias="tracePath")


class WorkflowRunTraceResponse(WorkflowRuntimeModel):
    projection: WorkflowRunProjection
    checkpoint: WorkflowRunCheckpoint
    events: list[WorkflowNodeRunEvent] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    next_after_sequence: int = Field(0, ge=0, alias="nextAfterSequence")
