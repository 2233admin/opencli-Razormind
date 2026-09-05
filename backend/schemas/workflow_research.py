"""Research ledger, continuation, and graph-mutation workflow contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.workflow_runtime import (
    WorkflowResearchStatus,
    WorkflowRunProjection,
)

# Public API contracts intentionally preserve camelCase wire-field names.
# ruff: noqa: N815


class WorkflowResearchContinuationRequest(BaseModel):
    expectedRevisionId: str = Field(..., min_length=1)
    proposalId: str = Field(..., min_length=1)
    idempotencyKey: str = Field(..., min_length=1, max_length=255)
    sourceOutputs: dict[str, list[dict[str, Any]]] = Field(..., min_length=1)


class WorkflowResearchLedgerEntry(BaseModel):
    runId: str = Field(..., min_length=1)
    parentRunId: str | None = None
    rootRunId: str = Field(..., min_length=1)
    iteration: int = Field(..., ge=1, le=5)
    additionalCollectionCount: int = Field(..., ge=0, le=3)
    revisionId: str | None = None
    parentRevisionId: str | None = None
    claimSetHash: str | None = None
    semanticClaimSetHash: str | None = None
    scenarioSetHash: str | None = None
    decision: Literal["finalize", "collect_more", "stop_incomplete"] | None = None
    researchStatus: WorkflowResearchStatus
    stopReason: str | None = None
    proposal: dict[str, Any] | None = None
    gaps: list[str] = Field(default_factory=list)
    publishAllowed: bool | None = None
    gateReasons: list[str] = Field(default_factory=list)
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)
    createdAt: str = Field(..., min_length=1)


class WorkflowResearchLedgerResponse(BaseModel):
    ledgerId: str = Field(..., min_length=1)
    rootRunId: str = Field(..., min_length=1)
    currentRunId: str = Field(..., min_length=1)
    entries: list[WorkflowResearchLedgerEntry] = Field(default_factory=list)


class WorkflowResearchContinuationResponse(BaseModel):
    ledgerId: str = Field(..., min_length=1)
    parentRunId: str = Field(..., min_length=1)
    childRunId: str = Field(..., min_length=1)
    iteration: int = Field(..., ge=2, le=5)
    additionalCollectionCount: int = Field(..., ge=1, le=3)
    researchStatus: WorkflowResearchStatus
    replayed: bool
    projectionPath: str = Field(..., min_length=1)
    eventsPath: str = Field(..., min_length=1)
    projection: WorkflowRunProjection
