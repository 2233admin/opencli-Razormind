"""Public redacted contracts for on-demand Analysis Snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from backend.analysis_runtime.questdb import (
    RuntimeCapabilityReasonCode,
    RuntimeCapabilityState,
)
from backend.models.analysis_snapshot import (
    AnalysisSnapshotFailureCode,
    AnalysisSnapshotReceipt,
    AnalysisSnapshotStatus,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class AnalysisSnapshotRangeV1(_SnapshotModel):
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Analysis Snapshot ranges must use UTC timestamps")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_range(self) -> AnalysisSnapshotRangeV1:
        if self.start_at >= self.end_at:
            raise ValueError("Analysis Snapshot startAt must be before endAt")
        return self


class AnalysisSnapshotCapabilityV1(_SnapshotModel):
    runtime: Literal["questdb"] = "questdb"
    state: RuntimeCapabilityState
    reason_code: RuntimeCapabilityReasonCode


class AnalysisSnapshotRowCountsV1(_SnapshotModel):
    workflow_trace_events: int = Field(ge=0)
    acquisition_execution_metrics: int = Field(ge=0)
    total: int = Field(ge=0)


class AnalysisSnapshotPreviewV1(_SnapshotModel):
    run_id: str
    source_range: AnalysisSnapshotRangeV1
    row_counts: AnalysisSnapshotRowCountsV1
    eligible: bool


class AnalysisSnapshotReceiptV1(_SnapshotModel):
    snapshot_id: str
    run_id: str
    runtime: Literal["questdb"] = "questdb"
    status: AnalysisSnapshotStatus
    schema_version: Literal[1] = 1
    redaction_version: Literal[1] = 1
    source_range: AnalysisSnapshotRangeV1
    row_counts: AnalysisSnapshotRowCountsV1
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    failure_code: AnalysisSnapshotFailureCode | None

    @classmethod
    def from_receipt(cls, receipt: AnalysisSnapshotReceipt) -> AnalysisSnapshotReceiptV1:
        return cls(
            snapshot_id=receipt.id,
            run_id=receipt.run_id,
            status=receipt.status,
            source_range=AnalysisSnapshotRangeV1(
                start_at=_as_utc(receipt.source_start_at),
                end_at=_as_utc(receipt.source_end_at),
            ),
            row_counts=AnalysisSnapshotRowCountsV1(
                workflow_trace_events=receipt.workflow_trace_event_count,
                acquisition_execution_metrics=receipt.acquisition_execution_metric_count,
                total=receipt.total_row_count,
            ),
            created_at=_as_utc(receipt.created_at),
            completed_at=_as_utc(receipt.completed_at) if receipt.completed_at else None,
            expires_at=_as_utc(receipt.expires_at),
            failure_code=receipt.failure_code,
        )


class AnalysisSnapshotThroughputV1(_SnapshotModel):
    total: int = Field(ge=0)
    per_minute: float = Field(ge=0)


class AnalysisSnapshotLatencyV1(_SnapshotModel):
    sample_count: int = Field(ge=0)
    average_ms: float | None = Field(default=None, ge=0)
    p95_ms: float | None = Field(default=None, ge=0)
    max_ms: float | None = Field(default=None, ge=0)


class AnalysisSnapshotFailureRateV1(_SnapshotModel):
    failed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float = Field(ge=0, le=1)


class AnalysisSnapshotEventTypeV1(_SnapshotModel):
    event_type: str
    count: int = Field(ge=0)


class AnalysisSnapshotNodeV1(_SnapshotModel):
    node_id: str | None
    event_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)


class AnalysisSnapshotSummaryV1(_SnapshotModel):
    snapshot_id: str
    source_range: AnalysisSnapshotRangeV1
    throughput: AnalysisSnapshotThroughputV1
    latency: AnalysisSnapshotLatencyV1
    failure_rate: AnalysisSnapshotFailureRateV1
    event_types: list[AnalysisSnapshotEventTypeV1]
    nodes: list[AnalysisSnapshotNodeV1]


__all__ = [
    "AnalysisSnapshotCapabilityV1",
    "AnalysisSnapshotEventTypeV1",
    "AnalysisSnapshotFailureRateV1",
    "AnalysisSnapshotLatencyV1",
    "AnalysisSnapshotNodeV1",
    "AnalysisSnapshotPreviewV1",
    "AnalysisSnapshotRangeV1",
    "AnalysisSnapshotReceiptV1",
    "AnalysisSnapshotRowCountsV1",
    "AnalysisSnapshotSummaryV1",
    "AnalysisSnapshotThroughputV1",
]
