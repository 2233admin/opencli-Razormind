# ruff: noqa: N815, UP045
"""Evidence batch and projection contracts for replayable workflow runs."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from backend.schemas.workflow_runtime import (
    WorkflowRunBlockReason,
    WorkflowRunNodeState,
    WorkflowRunStatus,
    legacy_workflow_node_location,
    normalize_workflow_node_path,
)


class EvidenceBatchSummary(BaseModel):
    runId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    nodePath: list[str] = Field(default_factory=list)
    packageNodeId: Optional[str] = None
    internalNodeId: Optional[str] = None
    sourceGroup: Optional[str] = None
    adapterTaskId: Optional[str] = None
    traceId: str = Field(..., min_length=1)
    batchId: str = Field(..., min_length=1)
    manifestUri: Optional[str] = None
    odpRef: Optional[str] = None
    itemCount: int = Field(..., ge=0)
    recordCount: int = Field(..., ge=0)
    status: WorkflowRunStatus

    @model_validator(mode="after")
    def normalize_node_location(self) -> EvidenceBatchSummary:
        self.nodePath = normalize_workflow_node_path(
            node_id=self.nodeId,
            node_path=self.nodePath,
            package_node_id=self.packageNodeId,
            internal_node_id=self.internalNodeId,
        )
        package_node_id, internal_node_id = legacy_workflow_node_location(self.nodePath)
        self.packageNodeId = package_node_id
        self.internalNodeId = internal_node_id
        return self


class WorkflowEvidenceBatchListResponse(BaseModel):
    runId: str = Field(..., min_length=1)
    batches: list[EvidenceBatchSummary] = Field(default_factory=list)
    nextCursor: Optional[str] = None


class WorkflowSourceCoverage(BaseModel):
    sourceGroup: Optional[str] = None
    status: WorkflowRunStatus
    batchCount: int = Field(..., ge=0)
    itemCount: int = Field(..., ge=0)
    recordCount: int = Field(..., ge=0)


class WorkflowEvidenceBatchDetail(BaseModel):
    runId: str = Field(..., min_length=1)
    batch: EvidenceBatchSummary
    manifestUri: Optional[str] = None
    odpRef: Optional[str] = None
    recordCount: int = Field(..., ge=0)
    itemCount: int = Field(..., ge=0)
    sourceCoverage: WorkflowSourceCoverage


class WorkflowMissingSource(BaseModel):
    nodeId: str = Field(..., min_length=1)
    sourceGroup: Optional[str] = None
    status: WorkflowRunStatus
    reasons: list[WorkflowRunBlockReason] = Field(default_factory=list)


class WorkflowEvidenceSummary(BaseModel):
    summaryId: str = Field(..., min_length=1)
    sourceGroup: Optional[str] = None
    status: WorkflowRunStatus
    batchIds: list[str] = Field(default_factory=list)
    itemCount: int = Field(..., ge=0)
    recordCount: int = Field(..., ge=0)


class WorkflowProjectionArtifact(BaseModel):
    artifactId: str = Field(..., min_length=1)
    batchId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    manifestUri: Optional[str] = None
    odpRef: Optional[str] = None


class WorkflowEvidenceProjection(BaseModel):
    runId: str = Field(..., min_length=1)
    traceId: str = Field(..., min_length=1)
    status: WorkflowRunStatus
    nodes: list[WorkflowRunNodeState] = Field(default_factory=list)
    clusters: list[dict[str, Any]] = Field(default_factory=list)
    missingSources: list[WorkflowMissingSource] = Field(default_factory=list)
    summaries: list[WorkflowEvidenceSummary] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[WorkflowProjectionArtifact] = Field(default_factory=list)
