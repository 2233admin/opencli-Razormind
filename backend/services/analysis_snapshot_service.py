"""Authoritative selection and disposable projection for Analysis Snapshots."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.analysis_runtime import (
    AcquisitionExecutionSnapshotRow,
    QuestDBSnapshotOperationError,
    QuestDBSnapshotRuntime,
    RuntimeCapabilityState,
    WorkflowTraceSnapshotRow,
)
from backend.models.acquisition import AcquisitionExecution
from backend.models.analysis_snapshot import (
    AnalysisSnapshotFailureCode,
    AnalysisSnapshotReceipt,
    AnalysisSnapshotStatus,
)
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.analysis_snapshot import (
    AnalysisSnapshotEventTypeV1,
    AnalysisSnapshotFailureRateV1,
    AnalysisSnapshotLatencyV1,
    AnalysisSnapshotNodeV1,
    AnalysisSnapshotPreviewV1,
    AnalysisSnapshotRangeV1,
    AnalysisSnapshotReceiptV1,
    AnalysisSnapshotRowCountsV1,
    AnalysisSnapshotSummaryV1,
    AnalysisSnapshotThroughputV1,
)
from backend.schemas.workflow import WorkflowNodeRunEvent

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_REDACTION_VERSION = 1
SNAPSHOT_RETENTION = timedelta(days=30)
RANGE_PRECISION_TOLERANCE = timedelta(milliseconds=1)
ACQUISITION_RUNTIME_KIND = "managed-opencli"


class AnalysisSnapshotErrorCode(StrEnum):
    PROJECT_NOT_FOUND = "analysis_project_not_found"
    WORKFLOW_NOT_FOUND = "analysis_workflow_not_found"
    RUN_NOT_FOUND = "analysis_run_not_found"
    RUN_NOT_COMPLETED = "analysis_run_not_completed"
    RANGE_OUTSIDE_RUN = "analysis_range_outside_run"
    SOURCE_TIMESTAMP_INVALID = "analysis_source_timestamp_invalid"
    EMPTY = "analysis_snapshot_empty"
    RECEIPT_NOT_FOUND = "analysis_snapshot_not_found"
    RECEIPT_NOT_COMPLETED = "analysis_snapshot_not_completed"
    RECEIPT_EXPIRED = "analysis_snapshot_expired"
    SUMMARY_UNAVAILABLE = "analysis_summary_unavailable"


class AnalysisSnapshotError(RuntimeError):
    def __init__(self, code: AnalysisSnapshotErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class AnalysisSnapshotScope:
    workspace_id: str
    project_id: str
    workflow_id: str
    studio_workflow_version_id: str
    run: WorkflowRun


@dataclass(frozen=True)
class AnalysisSnapshotProjection:
    preview: AnalysisSnapshotPreviewV1
    trace_rows: tuple[WorkflowTraceSnapshotRow, ...]
    acquisition_rows: tuple[AcquisitionExecutionSnapshotRow, ...]


async def resolve_scope(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
) -> AnalysisSnapshotScope:
    project = await db.get(StudioProject, project_id)
    if project is None or project.workspace_id != workspace_id or project.archived:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.PROJECT_NOT_FOUND)
    workflow = await db.get(StudioWorkflow, workflow_id)
    if workflow is None or workflow.project_id != project_id or workflow.archived:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.WORKFLOW_NOT_FOUND)
    run = await db.get(WorkflowRun, run_id)
    if run is None or run.workflow_id != workflow_id or run.studio_workflow_version_id is None:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RUN_NOT_FOUND)
    version = await db.get(StudioWorkflowVersion, run.studio_workflow_version_id)
    if version is None or version.workflow_id != workflow_id:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RUN_NOT_FOUND)
    if run.status != "completed":
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RUN_NOT_COMPLETED)
    return AnalysisSnapshotScope(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        studio_workflow_version_id=version.id,
        run=run,
    )


async def build_projection(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    source_range: AnalysisSnapshotRangeV1,
) -> AnalysisSnapshotProjection:
    source_range = _normalize_run_range(scope.run, source_range)
    event_rows = list(
        (
            await db.execute(
                select(WorkflowRunEvent)
                .where(WorkflowRunEvent.run_id == scope.run.id)
                .order_by(WorkflowRunEvent.sequence)
            )
        )
        .scalars()
        .all()
    )
    trace_rows: list[WorkflowTraceSnapshotRow] = []
    for event_row in event_rows:
        try:
            event = WorkflowNodeRunEvent.model_validate(event_row.payload)
            occurred_at = _parse_source_utc(event.createdAt)
        except (ValidationError, ValueError, TypeError) as exc:
            raise AnalysisSnapshotError(
                AnalysisSnapshotErrorCode.SOURCE_TIMESTAMP_INVALID
            ) from exc
        if source_range.start_at <= occurred_at < source_range.end_at:
            trace_rows.append(
                WorkflowTraceSnapshotRow(
                    source_id=event.id,
                    run_id=scope.run.id,
                    workflow_id=scope.workflow_id,
                    trace_id=event.traceId,
                    node_id=event.nodeId,
                    sequence=event.sequence,
                    event_type=event.eventType,
                    occurred_at=occurred_at,
                )
            )

    execution_rows = list(
        (
            await db.execute(
                select(AcquisitionExecution)
                .where(
                    AcquisitionExecution.workspace_id == scope.workspace_id,
                    AcquisitionExecution.project_id == scope.project_id,
                    AcquisitionExecution.workflow_id == scope.workflow_id,
                    AcquisitionExecution.run_id == scope.run.id,
                )
                .order_by(AcquisitionExecution.started_at, AcquisitionExecution.id)
            )
        )
        .scalars()
        .all()
    )
    acquisition_rows: list[AcquisitionExecutionSnapshotRow] = []
    for execution in execution_rows:
        if execution.started_at is None:
            continue
        started_at = _database_utc(execution.started_at)
        if not source_range.start_at <= started_at < source_range.end_at:
            continue
        finished_at = (
            _database_utc(execution.finished_at) if execution.finished_at else None
        )
        duration_ms = (
            max(0, int((finished_at - started_at).total_seconds() * 1000))
            if finished_at is not None
            else None
        )
        acquisition_rows.append(
            AcquisitionExecutionSnapshotRow(
                source_id=execution.id,
                run_id=scope.run.id,
                capability_id=execution.capability_id,
                capability_version=execution.capability_version,
                output_schema_version=execution.output_schema_version,
                status=execution.status.value,
                runtime_kind=ACQUISITION_RUNTIME_KIND,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
        )

    counts = AnalysisSnapshotRowCountsV1(
        workflow_trace_events=len(trace_rows),
        acquisition_execution_metrics=len(acquisition_rows),
        total=len(trace_rows) + len(acquisition_rows),
    )
    return AnalysisSnapshotProjection(
        preview=AnalysisSnapshotPreviewV1(
            run_id=scope.run.id,
            source_range=source_range,
            row_counts=counts,
            eligible=counts.total > 0,
        ),
        trace_rows=tuple(trace_rows),
        acquisition_rows=tuple(acquisition_rows),
    )


async def create_snapshot(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    source_range: AnalysisSnapshotRangeV1,
    requested_by_user_id: str,
    runtime: QuestDBSnapshotRuntime,
) -> AnalysisSnapshotReceiptV1:
    projection = await build_projection(db, scope=scope, source_range=source_range)
    if not projection.preview.eligible:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.EMPTY)

    source_range = projection.preview.source_range
    selection_hash = _selection_hash(scope, source_range)
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, selection_hash))
    receipt = await db.get(AnalysisSnapshotReceipt, snapshot_id)
    now = datetime.now(UTC)
    if receipt is not None and receipt.status != AnalysisSnapshotStatus.FAILED:
        return AnalysisSnapshotReceiptV1.from_receipt(receipt)
    if receipt is None:
        receipt = AnalysisSnapshotReceipt(
            id=snapshot_id,
            runtime="questdb",
            selection_hash=selection_hash,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            workflow_id=scope.workflow_id,
            studio_workflow_version_id=scope.studio_workflow_version_id,
            run_id=scope.run.id,
            requested_by_user_id=requested_by_user_id,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            redaction_version=SNAPSHOT_REDACTION_VERSION,
            source_start_at=source_range.start_at,
            source_end_at=source_range.end_at,
            workflow_trace_event_count=projection.preview.row_counts.workflow_trace_events,
            acquisition_execution_metric_count=(
                projection.preview.row_counts.acquisition_execution_metrics
            ),
            total_row_count=projection.preview.row_counts.total,
            status=AnalysisSnapshotStatus.EXPORTING,
            failure_code=None,
            attempt_count=1,
            last_attempt_at=now,
            completed_at=None,
            expires_at=now + SNAPSHOT_RETENTION,
        )
        db.add(receipt)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            concurrent = await db.get(AnalysisSnapshotReceipt, snapshot_id)
            if concurrent is None:
                raise
            return AnalysisSnapshotReceiptV1.from_receipt(concurrent)
    else:
        receipt.status = AnalysisSnapshotStatus.EXPORTING
        receipt.failure_code = None
        receipt.attempt_count += 1
        receipt.last_attempt_at = now
        await db.commit()

    try:
        capability = await runtime.get_status()
        failure_code = _runtime_failure_code(capability.state)
        if failure_code is not None:
            await _mark_failed(db, receipt, failure_code)
            return AnalysisSnapshotReceiptV1.from_receipt(receipt)
        await runtime.export_snapshot(
            snapshot_id=receipt.id,
            trace_rows=projection.trace_rows,
            acquisition_rows=projection.acquisition_rows,
        )
    except Exception:  # noqa: BLE001
        # The primary receipt is the failure boundary; runtime internals never cross it.
        await _mark_failed(db, receipt, AnalysisSnapshotFailureCode.EXPORT_FAILED)
        return AnalysisSnapshotReceiptV1.from_receipt(receipt)

    receipt.status = AnalysisSnapshotStatus.COMPLETED
    receipt.completed_at = datetime.now(UTC)
    receipt.failure_code = None
    await db.commit()
    return AnalysisSnapshotReceiptV1.from_receipt(receipt)


async def list_receipts(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
) -> list[AnalysisSnapshotReceiptV1]:
    rows = list(
        (
            await db.execute(
                select(AnalysisSnapshotReceipt)
                .where(*_scope_filters(scope))
                .order_by(
                    AnalysisSnapshotReceipt.created_at.desc(),
                    AnalysisSnapshotReceipt.id.desc(),
                )
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [AnalysisSnapshotReceiptV1.from_receipt(row) for row in rows]


async def get_receipt(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    snapshot_id: str,
) -> AnalysisSnapshotReceipt:
    receipt = await db.scalar(
        select(AnalysisSnapshotReceipt).where(
            AnalysisSnapshotReceipt.id == snapshot_id,
            *_scope_filters(scope),
        )
    )
    if receipt is None:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RECEIPT_NOT_FOUND)
    return receipt


async def read_summary(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    snapshot_id: str,
    runtime: QuestDBSnapshotRuntime,
) -> AnalysisSnapshotSummaryV1:
    receipt = await get_receipt(db, scope=scope, snapshot_id=snapshot_id)
    if receipt.status == AnalysisSnapshotStatus.EXPIRED:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RECEIPT_EXPIRED)
    if receipt.status != AnalysisSnapshotStatus.COMPLETED:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RECEIPT_NOT_COMPLETED)
    try:
        summary = await runtime.read_summary(
            snapshot_id=receipt.id,
            start_at=_database_utc(receipt.source_start_at),
            end_at=_database_utc(receipt.source_end_at),
        )
    except QuestDBSnapshotOperationError as exc:
        raise AnalysisSnapshotError(
            AnalysisSnapshotErrorCode.SUMMARY_UNAVAILABLE
        ) from exc
    total = summary.total
    return AnalysisSnapshotSummaryV1(
        snapshot_id=receipt.id,
        source_range=AnalysisSnapshotRangeV1(
            start_at=_database_utc(receipt.source_start_at),
            end_at=_database_utc(receipt.source_end_at),
        ),
        throughput=AnalysisSnapshotThroughputV1(
            total=total,
            per_minute=summary.per_minute,
        ),
        latency=AnalysisSnapshotLatencyV1(
            sample_count=len(summary.latency_values_ms),
            average_ms=summary.average_ms,
            p95_ms=summary.p95_ms,
            max_ms=summary.max_ms,
        ),
        failure_rate=AnalysisSnapshotFailureRateV1(
            failed=summary.failed,
            total=total,
            rate=summary.failed / total if total else 0,
        ),
        event_types=[
            AnalysisSnapshotEventTypeV1(event_type=event_type, count=count)
            for event_type, count in summary.event_types
        ],
        nodes=[
            AnalysisSnapshotNodeV1(
                node_id=node_id,
                event_count=event_count,
                failure_count=failure_count,
            )
            for node_id, event_count, failure_count in summary.nodes
        ],
    )


def _normalize_run_range(
    run: WorkflowRun,
    source_range: AnalysisSnapshotRangeV1,
) -> AnalysisSnapshotRangeV1:
    run_start = _database_utc(run.created_at)
    run_end = _database_utc(run.updated_at)
    start_at = source_range.start_at
    end_at = source_range.end_at
    if start_at < run_start:
        if run_start - start_at > RANGE_PRECISION_TOLERANCE:
            raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RANGE_OUTSIDE_RUN)
        start_at = run_start
    if end_at > run_end:
        if end_at - run_end > RANGE_PRECISION_TOLERANCE:
            raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RANGE_OUTSIDE_RUN)
        end_at = run_end
    if start_at >= end_at:
        raise AnalysisSnapshotError(AnalysisSnapshotErrorCode.RANGE_OUTSIDE_RUN)
    return AnalysisSnapshotRangeV1(start_at=start_at, end_at=end_at)


def _parse_source_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("source timestamp is not UTC")
    return parsed.astimezone(UTC)


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _selection_hash(
    scope: AnalysisSnapshotScope,
    source_range: AnalysisSnapshotRangeV1,
) -> str:
    canonical = json.dumps(
        {
            "projectId": scope.project_id,
            "redactionVersion": SNAPSHOT_REDACTION_VERSION,
            "runId": scope.run.id,
            "runtime": "questdb",
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "sourceRange": source_range.model_dump(mode="json", by_alias=True),
            "workflowId": scope.workflow_id,
            "workspaceId": scope.workspace_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _runtime_failure_code(
    state: RuntimeCapabilityState,
) -> AnalysisSnapshotFailureCode | None:
    return {
        RuntimeCapabilityState.DISABLED: AnalysisSnapshotFailureCode.RUNTIME_DISABLED,
        RuntimeCapabilityState.UNAVAILABLE: AnalysisSnapshotFailureCode.RUNTIME_UNAVAILABLE,
        RuntimeCapabilityState.UNHEALTHY: AnalysisSnapshotFailureCode.RUNTIME_UNHEALTHY,
        RuntimeCapabilityState.READY: None,
    }[state]


async def _mark_failed(
    db: AsyncSession,
    receipt: AnalysisSnapshotReceipt,
    failure_code: AnalysisSnapshotFailureCode,
) -> None:
    receipt.status = AnalysisSnapshotStatus.FAILED
    receipt.failure_code = failure_code
    receipt.completed_at = None
    await db.commit()


def _scope_filters(scope: AnalysisSnapshotScope) -> tuple[ColumnElement[bool], ...]:
    return (
        AnalysisSnapshotReceipt.workspace_id == scope.workspace_id,
        AnalysisSnapshotReceipt.project_id == scope.project_id,
        AnalysisSnapshotReceipt.workflow_id == scope.workflow_id,
        AnalysisSnapshotReceipt.run_id == scope.run.id,
    )


__all__ = [
    "AnalysisSnapshotError",
    "AnalysisSnapshotErrorCode",
    "AnalysisSnapshotProjection",
    "AnalysisSnapshotScope",
    "build_projection",
    "create_snapshot",
    "get_receipt",
    "list_receipts",
    "read_summary",
    "resolve_scope",
]
