"""Public contracts for durable Analysis Findings."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from backend.models.analysis_finding import (
    AnalysisFinding,
    AnalysisFindingEvidenceMetric,
    AnalysisFindingEvidenceUnit,
    AnalysisFindingSelectorKind,
)
from backend.schemas.analysis_snapshot import AnalysisSnapshotRangeV1


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class _FindingModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class AnalysisFindingSelectorV1(_FindingModel):
    kind: str = Field(min_length=1, max_length=64)
    key: str | None = Field(default=None, max_length=255)


class AnalysisFindingCreateV1(_FindingModel):
    snapshot_receipt_id: str = Field(min_length=1, max_length=36)
    selector: AnalysisFindingSelectorV1
    observation: str = Field(max_length=4000)
    interpretation: str = Field(max_length=4000)
    recommendation: str = Field(max_length=4000)


class AnalysisFindingEvidenceV1(_FindingModel):
    selector_kind: AnalysisFindingSelectorKind
    selector_key: str | None
    metric: AnalysisFindingEvidenceMetric
    value: int | float
    unit: AnalysisFindingEvidenceUnit


class AnalysisFindingV1(_FindingModel):
    finding_id: str
    workspace_id: str
    project_id: str
    workflow_id: str
    workflow_version_id: str
    run_id: str
    snapshot_receipt_id: str
    source_range: AnalysisSnapshotRangeV1
    author_user_id: str
    observation: str
    interpretation: str
    recommendation: str
    evidence: AnalysisFindingEvidenceV1
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_finding(cls, finding: AnalysisFinding) -> AnalysisFindingV1:
        value: int | float
        if finding.evidence_unit == AnalysisFindingEvidenceUnit.COUNT:
            value = int(finding.evidence_value)
        else:
            value = float(finding.evidence_value)
        return cls(
            finding_id=finding.id,
            workspace_id=finding.workspace_id,
            project_id=finding.project_id,
            workflow_id=finding.workflow_id,
            workflow_version_id=finding.studio_workflow_version_id,
            run_id=finding.run_id,
            snapshot_receipt_id=finding.snapshot_receipt_id,
            source_range=AnalysisSnapshotRangeV1(
                start_at=_as_utc(finding.source_start_at),
                end_at=_as_utc(finding.source_end_at),
            ),
            author_user_id=finding.author_user_id,
            observation=finding.observation,
            interpretation=finding.interpretation,
            recommendation=finding.recommendation,
            evidence=AnalysisFindingEvidenceV1(
                selector_kind=finding.selector_kind,
                selector_key=finding.selector_key,
                metric=finding.evidence_metric,
                value=value,
                unit=finding.evidence_unit,
            ),
            created_at=_as_utc(finding.created_at),
            updated_at=_as_utc(finding.updated_at),
        )


__all__ = [
    "AnalysisFindingCreateV1",
    "AnalysisFindingEvidenceV1",
    "AnalysisFindingSelectorV1",
    "AnalysisFindingV1",
]
