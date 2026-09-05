# ruff: noqa: N815, UP045
"""Versioned ResearchGraph semantic contracts."""

from typing import Any, Literal, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.workflow_runtime import WorkflowNodeRunEvent

WorkflowResearchGraphEventType = Literal[
    "source/recorded",
    "evidence/linked",
    "claim/projected",
    "relation/projected",
]
WorkflowResearchGraphEntityKind = Literal["source", "evidence", "claim", "relation"]
WorkflowResearchGraphMutationAction = Literal["propose", "verify", "reject", "retract"]
WorkflowResearchGraphState = Literal[
    "recorded",
    "proposed",
    "verified",
    "rejected",
    "retracted",
]


class WorkflowResearchGraphEntity(BaseModel):
    id: str = Field(..., min_length=1, max_length=255)
    kind: WorkflowResearchGraphEntityKind
    label: Optional[str] = Field(default=None, min_length=1, max_length=500)
    sourceIds: list[str] = Field(default_factory=list)
    evidenceIds: list[str] = Field(default_factory=list)
    subjectId: Optional[str] = Field(default=None, min_length=1, max_length=255)
    objectId: Optional[str] = Field(default=None, min_length=1, max_length=255)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_research_graph_entity(self) -> Self:
        for name, values in (
            ("sourceIds", self.sourceIds),
            ("evidenceIds", self.evidenceIds),
        ):
            if any(not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique non-empty IDs")
        if self.kind == "evidence" and not self.sourceIds:
            raise ValueError("evidence entities require at least one source ID")
        if self.kind == "claim" and not self.evidenceIds:
            raise ValueError("claim entities require at least one evidence ID")
        if self.kind == "relation":
            if not self.subjectId or not self.objectId:
                raise ValueError("relation entities require subjectId and objectId")
            if self.subjectId == self.objectId:
                raise ValueError("relation entities cannot self-reference")
            if not self.evidenceIds:
                raise ValueError("relation entities require at least one evidence ID")
        elif self.subjectId is not None or self.objectId is not None:
            raise ValueError("only relation entities may declare subjectId or objectId")
        return self


class WorkflowResearchGraphEvent(BaseModel):
    """One semantic fact or state transition persisted in a workflow transcript."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    eventId: str = Field(..., min_length=1, max_length=255)
    idempotencyKey: str = Field(..., min_length=1, max_length=255)
    eventType: WorkflowResearchGraphEventType
    runId: str = Field(..., min_length=1)
    traceId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    revisionId: Optional[str] = Field(default=None, min_length=1, max_length=255)
    expectedRevision: Optional[str] = Field(default=None, min_length=1, max_length=255)
    expectedSequence: Optional[int] = Field(default=None, ge=0)
    lineage: dict[str, str] = Field(default_factory=dict)
    action: Literal["record", "propose", "verify", "reject", "retract"] = "record"
    targetId: Optional[str] = Field(default=None, min_length=1, max_length=255)
    entity: Optional[WorkflowResearchGraphEntity] = None

    @model_validator(mode="after")
    def validate_research_graph_event(self) -> Self:
        if self.action in {"record", "propose"}:
            if self.entity is None:
                raise ValueError(f"{self.action} ResearchGraph events require an entity")
            expected_kind = self.eventType.split("/", maxsplit=1)[0]
            if self.entity.kind != expected_kind:
                raise ValueError(
                    f"eventType {self.eventType!r} does not match entity kind "
                    f"{self.entity.kind!r}"
                )
            if self.targetId is not None:
                raise ValueError(f"{self.action} ResearchGraph events cannot declare targetId")
        elif self.targetId is None:
            raise ValueError(f"{self.action} ResearchGraph events require targetId")
        elif self.entity is not None:
            raise ValueError(f"{self.action} ResearchGraph events cannot declare an entity")
        if self.idempotencyKey != self.eventId:
            raise ValueError("idempotencyKey must equal eventId")
        if any(not key or not value for key, value in self.lineage.items()):
            raise ValueError("lineage must contain non-empty string keys and values")
        return self


class WorkflowResearchGraphMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1]
    idempotencyKey: str = Field(..., min_length=1, max_length=255)
    expectedRevision: Optional[str] = Field(..., min_length=1, max_length=255)
    expectedSequence: int = Field(..., ge=0)
    action: WorkflowResearchGraphMutationAction
    traceId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    revisionId: Optional[str] = Field(default=None, min_length=1, max_length=255)
    lineage: dict[str, str] = Field(default_factory=dict)
    entity: Optional[WorkflowResearchGraphEntity] = None
    targetId: Optional[str] = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_mutation_shape(self) -> Self:
        if self.action == "propose":
            if self.entity is None or self.targetId is not None:
                raise ValueError("propose requests require entity and forbid targetId")
        elif self.entity is not None or self.targetId is None:
            raise ValueError(f"{self.action} requests require targetId and forbid entity")
        if any(not key or not value for key, value in self.lineage.items()):
            raise ValueError("lineage must contain non-empty string keys and values")
        return self


class WorkflowResearchGraphEntityProjection(WorkflowResearchGraphEntity):
    eventId: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    runId: str = Field(..., min_length=1)
    traceId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    revisionId: Optional[str] = None
    lineage: dict[str, str] = Field(default_factory=dict)
    state: WorkflowResearchGraphState = "recorded"
    authoritative: bool = False


class WorkflowResearchGraphHistoryEntry(BaseModel):
    eventId: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=1)
    action: Literal["record", "propose", "verify", "reject", "retract"]
    targetId: Optional[str] = None
    entityId: Optional[str] = None
    revisionId: Optional[str] = None
    runId: str = Field(..., min_length=1)
    traceId: str = Field(..., min_length=1)
    nodeId: str = Field(..., min_length=1)
    lineage: dict[str, str] = Field(default_factory=dict)


class WorkflowResearchGraphProjection(BaseModel):
    runId: str = Field(..., min_length=1)
    traceId: str = Field(..., min_length=1)
    eventCount: int = Field(0, ge=0)
    lastSequence: int = Field(0, ge=0)
    currentRevision: Optional[str] = None
    entities: list[WorkflowResearchGraphEntityProjection] = Field(default_factory=list)
    relations: list[WorkflowResearchGraphEntityProjection] = Field(default_factory=list)
    history: list[WorkflowResearchGraphHistoryEntry] = Field(default_factory=list)


class WorkflowResearchGraphMutationResponse(BaseModel):
    events: list[WorkflowNodeRunEvent]
    graph: WorkflowResearchGraphProjection
