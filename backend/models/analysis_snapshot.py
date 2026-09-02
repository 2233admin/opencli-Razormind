"""Authoritative receipts for disposable external analysis snapshots."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class AnalysisSnapshotStatus(StrEnum):
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class AnalysisSnapshotFailureCode(StrEnum):
    RUNTIME_DISABLED = "runtime_disabled"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_UNHEALTHY = "runtime_unhealthy"
    EXPORT_FAILED = "export_failed"


class AnalysisSnapshotReceipt(TimestampMixin):
    """Primary-database truth for one deterministic QuestDB projection."""

    __tablename__ = "analysis_snapshot_receipts"
    __table_args__ = (
        UniqueConstraint("selection_hash", name="uq_analysis_snapshot_selection_hash"),
        CheckConstraint("runtime = 'questdb'", name="ck_analysis_snapshot_runtime"),
        CheckConstraint(
            "workflow_trace_event_count >= 0 AND acquisition_execution_metric_count >= 0 "
            "AND total_row_count = workflow_trace_event_count + acquisition_execution_metric_count",
            name="ck_analysis_snapshot_row_counts",
        ),
        CheckConstraint("attempt_count >= 1", name="ck_analysis_snapshot_attempt_count"),
        Index(
            "ix_analysis_snapshot_scope_created",
            "workspace_id",
            "project_id",
            "workflow_id",
            "run_id",
            "created_at",
            "id",
        ),
    )

    runtime: Mapped[str] = mapped_column(
        String(32), nullable=False, default="questdb", server_default="questdb"
    )
    selection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    studio_workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redaction_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workflow_trace_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    acquisition_execution_metric_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AnalysisSnapshotStatus] = mapped_column(
        Enum(
            AnalysisSnapshotStatus,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    failure_code: Mapped[AnalysisSnapshotFailureCode | None] = mapped_column(
        Enum(
            AnalysisSnapshotFailureCode,
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "AnalysisSnapshotFailureCode",
    "AnalysisSnapshotReceipt",
    "AnalysisSnapshotStatus",
]
