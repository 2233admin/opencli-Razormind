"""Durable operator findings derived from disposable analysis snapshots."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class AnalysisFindingSelectorKind(StrEnum):
    OVERALL_THROUGHPUT = "overall_throughput"
    LATENCY = "latency"
    FAILURE_RATE = "failure_rate"
    EVENT_TYPE = "event_type"
    NODE = "node"


class AnalysisFindingEvidenceMetric(StrEnum):
    EVENTS_PER_MINUTE = "events_per_minute"
    P95_MS = "p95_ms"
    FAILURE_RATE = "failure_rate"
    EVENT_COUNT = "event_count"


class AnalysisFindingEvidenceUnit(StrEnum):
    EVENTS_PER_MINUTE = "events_per_minute"
    MILLISECONDS = "milliseconds"
    RATIO = "ratio"
    COUNT = "count"


class AnalysisFinding(TimestampMixin):
    """Primary-database decision record with a scalar, allowlisted citation."""

    __tablename__ = "analysis_findings"
    __table_args__ = (
        CheckConstraint(
            "length(trim(observation)) > 0",
            name="ck_analysis_findings_observation_nonempty",
        ),
        CheckConstraint(
            "length(trim(interpretation)) > 0",
            name="ck_analysis_findings_interpretation_nonempty",
        ),
        CheckConstraint(
            "length(trim(recommendation)) > 0",
            name="ck_analysis_findings_recommendation_nonempty",
        ),
        CheckConstraint(
            "source_start_at < source_end_at",
            name="ck_analysis_findings_source_range",
        ),
        CheckConstraint(
            "((selector_kind IN ('event_type', 'node')) "
            "AND selector_key IS NOT NULL AND length(trim(selector_key)) > 0) "
            "OR ((selector_kind IN ('overall_throughput', 'latency', 'failure_rate')) "
            "AND selector_key IS NULL)",
            name="ck_analysis_findings_selector_key",
        ),
        CheckConstraint(
            "(selector_kind = 'overall_throughput' "
            "AND evidence_metric = 'events_per_minute' "
            "AND evidence_unit = 'events_per_minute') "
            "OR (selector_kind = 'latency' AND evidence_metric = 'p95_ms' "
            "AND evidence_unit = 'milliseconds') "
            "OR (selector_kind = 'failure_rate' AND evidence_metric = 'failure_rate' "
            "AND evidence_unit = 'ratio') "
            "OR (selector_kind IN ('event_type', 'node') "
            "AND evidence_metric = 'event_count' AND evidence_unit = 'count')",
            name="ck_analysis_findings_evidence_mapping",
        ),
        CheckConstraint(
            "evidence_value >= 0 "
            "AND (evidence_metric != 'failure_rate' OR evidence_value <= 1)",
            name="ck_analysis_findings_evidence_value",
        ),
        Index(
            "ix_analysis_findings_scope_created",
            "workspace_id",
            "project_id",
            "workflow_id",
            "run_id",
            "created_at",
            "id",
        ),
        Index("ix_analysis_findings_snapshot_receipt", "snapshot_receipt_id"),
    )

    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    studio_workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_receipt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("analysis_snapshot_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    author_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    selector_kind: Mapped[AnalysisFindingSelectorKind] = mapped_column(
        Enum(
            AnalysisFindingSelectorKind,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    selector_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_metric: Mapped[AnalysisFindingEvidenceMetric] = mapped_column(
        Enum(
            AnalysisFindingEvidenceMetric,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    evidence_value: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_unit: Mapped[AnalysisFindingEvidenceUnit] = mapped_column(
        Enum(
            AnalysisFindingEvidenceUnit,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )


__all__ = [
    "AnalysisFinding",
    "AnalysisFindingEvidenceMetric",
    "AnalysisFindingEvidenceUnit",
    "AnalysisFindingSelectorKind",
]
