"""Versioned, deterministic contracts for the native intelligence lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ARTIFACT_PAYLOAD_BYTES = 1_048_576
MAX_COMMAND_PAYLOAD_BYTES = 65_536
MAX_EVENT_PAYLOAD_BYTES = 16_384
MAX_IDEMPOTENCY_KEY_LENGTH = 255


class ArtifactKind(StrEnum):
    RESEARCH = "research"
    ONTOLOGY = "ontology"
    GRAPH = "graph"
    PERSONA = "persona"
    SIMULATION = "simulation"
    INTERVIEW = "interview"
    REPORT = "report"
    REPORT_ANSWER = "report_answer"
    CLOSE = "close"


ARTIFACT_SIMULATION_EXPECTATIONS: dict[ArtifactKind, bool] = {
    ArtifactKind.RESEARCH: False,
    ArtifactKind.ONTOLOGY: False,
    ArtifactKind.GRAPH: False,
    ArtifactKind.PERSONA: True,
    ArtifactKind.SIMULATION: True,
    ArtifactKind.INTERVIEW: True,
    ArtifactKind.REPORT: True,
    ArtifactKind.REPORT_ANSWER: True,
    ArtifactKind.CLOSE: False,
}


class IntelligenceCommandName(StrEnum):
    RESEARCH = "research"
    RESEARCH_COMPLETE = "research_complete"
    BUILD_ONTOLOGY = "build_ontology"
    BUILD_GRAPH = "build_graph"
    PREPARE = "prepare"
    START = "start"
    STEP = "step"
    STOP = "stop"
    SIMULATION_COMPLETE = "simulation_complete"
    INTERVIEW = "interview"
    INTERVIEW_COMPLETE = "interview_complete"
    REPORT = "report"
    REPORT_PROGRESS = "report_progress"
    REPORT_COMPLETE = "report_complete"
    ASK_REPORT = "ask_report"
    FAIL = "fail"
    RESUME = "resume"
    RENEW = "renew"
    RECOVER = "recover"
    CANCEL = "cancel"
    CLOSE = "close"


def canonical_json(value: Any) -> str:
    """Return stable JSON suitable for hashes, IDs, and idempotency checks."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def deterministic_id(namespace: str, value: Any) -> str:
    return f"{namespace}_{canonical_hash(value)[:32]}"


def deterministic_seed(value: Any) -> int:
    return int(canonical_hash(value)[:16], 16) & ((1 << 63) - 1)


def payload_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


class ArtifactProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=255)
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=10_000)
    collected_at: datetime | None = None
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
        exclude_if=lambda value: value is None,
    )


class WorkflowConversationOrigin(BaseModel):
    """Server-authorized Agent conversation bound to one workflow run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str = Field(min_length=1, max_length=36)
    conversation_revision: int = Field(ge=0)
    governed_workspace_id: str = Field(min_length=1, max_length=36)
    studio_workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=36)


class NativeIntelligenceArtifact(BaseModel):
    """Base envelope shared by all persisted native artifacts."""

    model_config = ConfigDict(extra="forbid")
    expected_kind: ClassVar[ArtifactKind | None] = None

    schema_version: Literal["intelligence.artifact.v1"] = "intelligence.artifact.v1"
    artifact_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=36)
    kind: ArtifactKind
    payload: dict[str, Any] = Field(default_factory=dict)
    grounding_artifact_ids: list[str] = Field(default_factory=list, max_length=10_000)
    simulated: bool
    provenance: ArtifactProvenance
    algorithm_version: str = Field(min_length=1, max_length=100)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_contract(self) -> NativeIntelligenceArtifact:
        if self.expected_kind is not None and self.kind != self.expected_kind:
            _record_rejected_contract("artifact_contract")
            raise ValueError(f"artifact kind must be {self.expected_kind.value!r}")
        if self.simulated is not ARTIFACT_SIMULATION_EXPECTATIONS[self.kind]:
            _record_rejected_contract("artifact_invariant")
            raise ValueError("artifact_simulation_flag_invalid")
        if self.payload.get("simulated") is not self.simulated:
            _record_rejected_contract("artifact_invariant")
            raise ValueError("artifact_simulation_payload_mismatch")
        if payload_size(self.payload) > MAX_ARTIFACT_PAYLOAD_BYTES:
            _record_rejected_contract("artifact_contract")
            raise ValueError("artifact_payload_too_large")
        if len(set(self.grounding_artifact_ids)) != len(self.grounding_artifact_ids):
            _record_rejected_contract("artifact_contract")
            raise ValueError("duplicate grounding artifact reference")
        return self


class ResearchArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.RESEARCH
    kind: Literal[ArtifactKind.RESEARCH] = ArtifactKind.RESEARCH


class OntologyArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.ONTOLOGY
    kind: Literal[ArtifactKind.ONTOLOGY] = ArtifactKind.ONTOLOGY


class GraphArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.GRAPH
    kind: Literal[ArtifactKind.GRAPH] = ArtifactKind.GRAPH


class PersonaArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.PERSONA
    kind: Literal[ArtifactKind.PERSONA] = ArtifactKind.PERSONA


class SimulationArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.SIMULATION
    kind: Literal[ArtifactKind.SIMULATION] = ArtifactKind.SIMULATION


class InterviewArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.INTERVIEW
    kind: Literal[ArtifactKind.INTERVIEW] = ArtifactKind.INTERVIEW


class ReportArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.REPORT
    kind: Literal[ArtifactKind.REPORT] = ArtifactKind.REPORT


class ReportAnswerArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.REPORT_ANSWER
    kind: Literal[ArtifactKind.REPORT_ANSWER] = ArtifactKind.REPORT_ANSWER


class CloseArtifact(NativeIntelligenceArtifact):
    expected_kind = ArtifactKind.CLOSE
    kind: Literal[ArtifactKind.CLOSE] = ArtifactKind.CLOSE


class OperationLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=255)
    owner: str = Field(min_length=1, max_length=255)
    expires_at: datetime
    attempt: int = Field(default=1, ge=1)
    checkpoint_manifest: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("lease expiry must be timezone-aware")
        return value

    @field_validator("checkpoint_manifest")
    @classmethod
    def bound_manifest(cls, value: dict[str, Any]) -> dict[str, Any]:
        if payload_size(value) > MAX_COMMAND_PAYLOAD_BYTES:
            raise ValueError("checkpoint_manifest_too_large")
        return value


class IntelligenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["intelligence.command.v1"] = "intelligence.command.v1"
    command: IntelligenceCommandName
    session_id: str = Field(min_length=1, max_length=36)
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    request: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=36)
    workflow_id: str | None = Field(default=None, max_length=255)
    trace_id: str | None = Field(default=None, max_length=255)
    node_id: str | None = Field(default=None, max_length=255)
    lease: OperationLease | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> IntelligenceCommand:
        if payload_size(self.request) > MAX_COMMAND_PAYLOAD_BYTES:
            _record_rejected_contract("command_contract")
            raise ValueError("command_payload_too_large")
        run_context = (self.run_id, self.workflow_id, self.trace_id, self.node_id)
        if any(run_context) and not all(run_context):
            _record_rejected_contract("command_contract")
            raise ValueError("run context must provide run, workflow, trace, and node IDs")
        return self

    @property
    def request_hash(self) -> str:
        return canonical_hash(
            {
                "schema_version": self.schema_version,
                "command": self.command,
                "session_id": self.session_id,
                "request": self.request,
            }
        )


def _record_rejected_contract(reason: str) -> None:
    from backend.workflow.native_intelligence_metrics import record_rejected_contract

    record_rejected_contract(reason)


ARTIFACT_CONTRACTS: dict[ArtifactKind, type[NativeIntelligenceArtifact]] = {
    ArtifactKind.RESEARCH: ResearchArtifact,
    ArtifactKind.ONTOLOGY: OntologyArtifact,
    ArtifactKind.GRAPH: GraphArtifact,
    ArtifactKind.PERSONA: PersonaArtifact,
    ArtifactKind.SIMULATION: SimulationArtifact,
    ArtifactKind.INTERVIEW: InterviewArtifact,
    ArtifactKind.REPORT: ReportArtifact,
    ArtifactKind.REPORT_ANSWER: ReportAnswerArtifact,
    ArtifactKind.CLOSE: CloseArtifact,
}


__all__ = [
    "ARTIFACT_CONTRACTS",
    "ARTIFACT_SIMULATION_EXPECTATIONS",
    "MAX_ARTIFACT_PAYLOAD_BYTES",
    "MAX_COMMAND_PAYLOAD_BYTES",
    "MAX_EVENT_PAYLOAD_BYTES",
    "ArtifactKind",
    "ArtifactProvenance",
    "CloseArtifact",
    "GraphArtifact",
    "IntelligenceCommand",
    "IntelligenceCommandName",
    "InterviewArtifact",
    "NativeIntelligenceArtifact",
    "OntologyArtifact",
    "OperationLease",
    "PersonaArtifact",
    "ReportAnswerArtifact",
    "ReportArtifact",
    "ResearchArtifact",
    "SimulationArtifact",
    "canonical_hash",
    "canonical_json",
    "deterministic_id",
    "deterministic_seed",
    "payload_size",
]
