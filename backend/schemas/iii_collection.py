"""Versioned, redacted contracts for the Admin-to-III collection vertical."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from backend.schemas.evidence_manifest import EvidenceManifestModel, ResearchGraphV2ManifestRef


class _V1Model(EvidenceManifestModel):
    pass


class IIICollectionRequestV1(_V1Model):
    """Canonical collection intent, with no credential or endpoint fields."""

    site: str = Field(min_length=1, max_length=255)
    command: str = Field(min_length=1, max_length=255)
    args: dict = Field(default_factory=dict)
    output_format: str = Field(default="json", min_length=1, max_length=32)
    mode: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, min_length=1, max_length=36)
    source_binding_id: str | None = Field(default=None, min_length=1, max_length=36)
    source_binding_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    source_binding_revision_number: int | None = Field(default=None, ge=1)


class IIICollectionSubmitV1(_V1Model):
    version: Literal["v1"] = "v1"
    idempotency_key: str = Field(min_length=1, max_length=255)
    node_id: str = Field(min_length=1, max_length=255)
    collection: IIICollectionRequestV1


class IIICollectionLifecycleSummaryV1(_V1Model):
    """Execution-only summary; ODP ingress receipts are deliberately out of scope."""

    items_fetched: int | None = Field(default=None, ge=0)


class IIICollectionLifecycleV1(_V1Model):
    version: Literal["v1"] = "v1"
    workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    studio_workflow_version_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    node_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=36)
    attempt_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=36)
    trace_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=36)
    source_binding_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_number: int | None = Field(default=None, ge=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    sequence: int = Field(ge=1)
    event_type: Literal["bridge_accepted", "collector_started", "collector_returned"]
    summary: IIICollectionLifecycleSummaryV1 = Field(
        default_factory=IIICollectionLifecycleSummaryV1
    )


class IIICollectionSubmitReadV1(_V1Model):
    version: Literal["v1"] = "v1"
    command_id: str
    attempt_id: str
    attempt_number: int
    task_id: str
    trace_id: str
    payload_sha256: str
    created: bool
    dispatch_state: str


class IIICollectionLifecycleReadV1(_V1Model):
    version: Literal["v1"] = "v1"
    command_id: str
    attempt_id: str
    sequence: int
    event_type: str
    event_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    duplicate: bool


class VerticalEvidenceReferenceV1(_V1Model):
    """Immutable, redacted lifecycle/report/receipt evidence for one attempt."""

    kind: Literal["lifecycle", "expected_key_report", "ingress_receipt"]
    hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    event_type: Literal["bridge_accepted", "collector_started", "collector_returned"] | None = None


class VerticalStatusV1(_V1Model):
    """Operator-safe command projection: no raw collection input or bridge location."""

    version: Literal["v1"] = "v1"
    command_id: str
    attempt_id: str
    state: str
    blocking_stage: str | None = None
    evidence_references: list[VerticalEvidenceReferenceV1] = Field(default_factory=list)
    recovery_action: str
    side_effect_uncertainty: bool
    updated_at: datetime


class CollectorExpectedKeyV1(_V1Model):
    source_id: str = Field(min_length=1, max_length=36)
    event_id: str = Field(min_length=1, max_length=512)


class CollectorFinalExpectedKeyReportV1(_V1Model):
    """Bounded collector completion fact, never a claim of ODP persistence."""

    version: Literal["v1"] = "v1"
    workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    studio_workflow_version_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    node_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=36)
    attempt_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=36)
    trace_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=36)
    source_binding_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_number: int | None = Field(default=None, ge=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    report_id: str = Field(min_length=1, max_length=128)
    report_sequence: int = Field(ge=1)
    expected_keys: list[CollectorExpectedKeyV1] = Field(min_length=0, max_length=1000)
    expected_key_set_sha256: str = Field(min_length=64, max_length=64)
    item_count: int = Field(ge=0, le=1000)
    zero_count: int = Field(ge=0, le=1)
    rejected_count: int = Field(ge=0, le=1000)
    reported_at: datetime
    report_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_declared_counts(self) -> CollectorFinalExpectedKeyReportV1:
        if self.item_count != len(self.expected_keys):
            raise ValueError("item_count must equal the expected key set")
        if self.zero_count != int(self.item_count == 0):
            raise ValueError("zero_count must be one exactly for a declared zero result")
        return self


class ODPIngressOutcomeV1(_V1Model):
    source_id: str = Field(min_length=1, max_length=36)
    event_id: str = Field(min_length=1, max_length=512)
    outcome: Literal["accepted", "duplicate", "rejected"]
    rejection_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_rejection_reason(self) -> ODPIngressOutcomeV1:
        if self.outcome == "rejected" and not self.rejection_reason:
            raise ValueError("rejected outcome requires a bounded rejection_reason")
        if self.outcome != "rejected" and self.rejection_reason is not None:
            raise ValueError("only rejected outcomes may carry rejection_reason")
        return self


class ODPIngressOutcomeReceiptV1(_V1Model):
    """Authoritative signed odp-ingest ingress observation, never a store receipt."""

    version: Literal["v1"] = "v1"
    receipt_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)
    producer_id: str = Field(min_length=1, max_length=255)
    producer_key_id: str = Field(min_length=1, max_length=255)
    workspace_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=36)
    workflow_id: str = Field(min_length=1, max_length=36)
    studio_workflow_version_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    node_id: str = Field(min_length=1, max_length=255)
    command_id: str = Field(min_length=1, max_length=36)
    attempt_id: str = Field(min_length=1, max_length=36)
    attempt_number: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=36)
    trace_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=36)
    source_binding_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_id: str | None = Field(default=None, max_length=36)
    source_binding_revision_number: int | None = Field(default=None, ge=1)
    payload_sha256: str = Field(min_length=64, max_length=64)
    expected_key_set_sha256: str = Field(min_length=64, max_length=64)
    outcomes: list[ODPIngressOutcomeV1] = Field(max_length=1000)
    issued_at: datetime
    receipt_hash: str = Field(min_length=64, max_length=64)
    signature: str = Field(min_length=1, max_length=128)


class CollectorFinalExpectedKeyReportReadV1(_V1Model):
    version: Literal["v1"] = "v1"
    command_id: str
    attempt_id: str
    report_id: str
    report_sequence: int
    duplicate: bool


class ODPIngressOutcomeReceiptReadV1(_V1Model):
    version: Literal["v1"] = "v1"
    command_id: str
    attempt_id: str
    receipt_id: str
    duplicate: bool


MaterializationStatusV1 = Literal[
    "awaiting_final_report",
    "reconciling",
    "completed",
    "completed_empty",
    "partial",
    "failed_definitive",
    "indeterminate",
]


class EvidenceBatchRecordReferenceV1(_V1Model):
    source_id: str = Field(min_length=1, max_length=36)
    event_id: str = Field(min_length=1, max_length=512)
    odp_record_id: int = Field(ge=1)
    committed_at: str = Field(min_length=1, max_length=64)


class EvidenceBatchMaterializationReadV1(_V1Model):
    """Scoped, redacted materialization projection; never includes ODP payloads."""

    version: Literal["v1"] = "v1"
    batch_id: str = Field(min_length=1, max_length=36)
    reconciliation_revision: int = Field(ge=1)
    materialization_status: MaterializationStatusV1
    legacy_status: Literal["running", "completed", "partial", "failed", "blocked"]
    item_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    manifest_hash: str = Field(min_length=64, max_length=64)
    counts: dict[str, int]
    record_references: list[EvidenceBatchRecordReferenceV1] = Field(
        default_factory=list, max_length=1000
    )
    blocker: str | None = Field(default=None, max_length=256)
    recovery_action: str
    query_fingerprint: str | None = Field(default=None, max_length=64)
    page_snapshot_as_of: str | None = Field(default=None, max_length=64)
    redaction_profile_version: str | None = Field(default=None, max_length=64)
    finalized_at: datetime | None = None
    research_graph_manifest_ref: ResearchGraphV2ManifestRef | None = None


class EvidenceBatchMaterializationSummaryV1(_V1Model):
    """Bounded latest-state projection for evidence-batch listings."""

    version: Literal["v1"] = "v1"
    batch_id: str = Field(min_length=1, max_length=36)
    reconciliation_revision: int = Field(ge=1)
    materialization_status: MaterializationStatusV1
    legacy_status: Literal["running", "completed", "partial", "failed", "blocked"]
    item_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    counts: dict[str, int] = Field(max_length=7)
    blocker: str | None = Field(default=None, max_length=256)
    recovery_action: str
    query_fingerprint: str | None = Field(default=None, max_length=64)
    page_snapshot_as_of: str | None = Field(default=None, max_length=64)
    redaction_profile_version: str | None = Field(default=None, max_length=64)
    finalized_at: datetime | None = None


class StudioEvidenceBatchMaterializationListV1(_V1Model):
    """Bounded latest immutable materializations visible from one Studio run."""

    version: Literal["v1"] = "v1"
    run_id: str = Field(min_length=1, max_length=36)
    evidence_batches: list[EvidenceBatchMaterializationSummaryV1] = Field(
        default_factory=list, max_length=200
    )
    next_cursor: str | None = Field(default=None, max_length=36)
