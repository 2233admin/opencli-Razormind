"""Create and read durable findings from fixed Analysis Snapshot summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.analysis_runtime import QuestDBSnapshotRuntime
from backend.models.analysis_finding import (
    AnalysisFinding,
    AnalysisFindingEvidenceMetric,
    AnalysisFindingEvidenceUnit,
    AnalysisFindingSelectorKind,
)
from backend.models.analysis_snapshot import AnalysisSnapshotReceipt, AnalysisSnapshotStatus
from backend.schemas.analysis_finding import AnalysisFindingCreateV1, AnalysisFindingV1
from backend.schemas.analysis_snapshot import AnalysisSnapshotSummaryV1
from backend.services.analysis_snapshot_service import (
    AnalysisSnapshotError,
    AnalysisSnapshotErrorCode,
    AnalysisSnapshotScope,
    get_receipt,
    read_summary,
)

_SAFE_SELECTOR_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class AnalysisFindingErrorCode(StrEnum):
    SNAPSHOT_NOT_FOUND = "analysis_finding_snapshot_not_found"
    SNAPSHOT_NOT_COMPLETED = "analysis_finding_snapshot_not_completed"
    SNAPSHOT_EXPIRED = "analysis_finding_snapshot_expired"
    SELECTOR_UNSUPPORTED = "analysis_finding_selector_unsupported"
    SELECTOR_KEY_REQUIRED = "analysis_finding_selector_key_required"
    SELECTOR_KEY_UNSUPPORTED = "analysis_finding_selector_key_unsupported"
    SELECTED_RESULT_UNAVAILABLE = "analysis_finding_selected_result_unavailable"
    NARRATIVE_EMPTY = "analysis_finding_narrative_empty"
    SUMMARY_UNAVAILABLE = "analysis_finding_summary_unavailable"
    FINDING_NOT_FOUND = "analysis_finding_not_found"


class AnalysisFindingError(RuntimeError):
    def __init__(self, code: AnalysisFindingErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class _FindingInput:
    selector_kind: AnalysisFindingSelectorKind
    selector_key: str | None
    observation: str
    interpretation: str
    recommendation: str


@dataclass(frozen=True)
class _ScalarCitation:
    metric: AnalysisFindingEvidenceMetric
    value: float
    unit: AnalysisFindingEvidenceUnit


async def create_finding(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    author_user_id: str,
    body: AnalysisFindingCreateV1,
    runtime: QuestDBSnapshotRuntime,
) -> AnalysisFindingV1:
    validated = _validate_input(body)
    receipt = await _finding_receipt(
        db,
        scope=scope,
        snapshot_receipt_id=body.snapshot_receipt_id,
    )
    _require_current_receipt(receipt)
    try:
        summary = await read_summary(
            db,
            scope=scope,
            snapshot_id=receipt.id,
            runtime=runtime,
        )
    except AnalysisSnapshotError as exc:
        if exc.code == AnalysisSnapshotErrorCode.RECEIPT_EXPIRED:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SNAPSHOT_EXPIRED) from exc
        if exc.code == AnalysisSnapshotErrorCode.RECEIPT_NOT_COMPLETED:
            raise AnalysisFindingError(
                AnalysisFindingErrorCode.SNAPSHOT_NOT_COMPLETED
            ) from exc
        if exc.code == AnalysisSnapshotErrorCode.RECEIPT_NOT_FOUND:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SNAPSHOT_NOT_FOUND) from exc
        raise AnalysisFindingError(AnalysisFindingErrorCode.SUMMARY_UNAVAILABLE) from exc
    except Exception as exc:  # noqa: BLE001
        raise AnalysisFindingError(AnalysisFindingErrorCode.SUMMARY_UNAVAILABLE) from exc

    await db.refresh(receipt)
    _require_current_receipt(receipt)
    citation = _derive_citation(
        summary,
        selector_kind=validated.selector_kind,
        selector_key=validated.selector_key,
    )
    finding = AnalysisFinding(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run.id,
        snapshot_receipt_id=receipt.id,
        author_user_id=author_user_id,
        source_start_at=_database_utc(receipt.source_start_at),
        source_end_at=_database_utc(receipt.source_end_at),
        observation=validated.observation,
        interpretation=validated.interpretation,
        recommendation=validated.recommendation,
        selector_kind=validated.selector_kind,
        selector_key=validated.selector_key,
        evidence_metric=citation.metric,
        evidence_value=citation.value,
        evidence_unit=citation.unit,
    )
    db.add(finding)
    await db.commit()
    await db.refresh(finding)
    return AnalysisFindingV1.from_finding(finding)


async def list_findings(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
) -> list[AnalysisFindingV1]:
    findings = list(
        (
            await db.execute(
                select(AnalysisFinding)
                .where(*_scope_filters(scope))
                .order_by(AnalysisFinding.created_at.desc(), AnalysisFinding.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [AnalysisFindingV1.from_finding(finding) for finding in findings]


async def get_finding(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    finding_id: str,
) -> AnalysisFindingV1:
    finding = await db.scalar(
        select(AnalysisFinding).where(
            AnalysisFinding.id == finding_id,
            *_scope_filters(scope),
        )
    )
    if finding is None:
        raise AnalysisFindingError(AnalysisFindingErrorCode.FINDING_NOT_FOUND)
    return AnalysisFindingV1.from_finding(finding)


async def _finding_receipt(
    db: AsyncSession,
    *,
    scope: AnalysisSnapshotScope,
    snapshot_receipt_id: str,
) -> AnalysisSnapshotReceipt:
    try:
        return await get_receipt(db, scope=scope, snapshot_id=snapshot_receipt_id)
    except AnalysisSnapshotError as exc:
        raise AnalysisFindingError(AnalysisFindingErrorCode.SNAPSHOT_NOT_FOUND) from exc


def _validate_input(body: AnalysisFindingCreateV1) -> _FindingInput:
    try:
        selector_kind = AnalysisFindingSelectorKind(body.selector.kind.strip())
    except ValueError as exc:
        raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_UNSUPPORTED) from exc
    selector_key = body.selector.key.strip() if body.selector.key is not None else None
    if selector_kind in {
        AnalysisFindingSelectorKind.EVENT_TYPE,
        AnalysisFindingSelectorKind.NODE,
    }:
        if not selector_key:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_KEY_REQUIRED)
        if _SAFE_SELECTOR_KEY.fullmatch(selector_key) is None:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_KEY_UNSUPPORTED)
    elif selector_key is not None:
        raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_KEY_UNSUPPORTED)

    narratives = (
        body.observation.strip(),
        body.interpretation.strip(),
        body.recommendation.strip(),
    )
    if not all(narratives):
        raise AnalysisFindingError(AnalysisFindingErrorCode.NARRATIVE_EMPTY)
    return _FindingInput(
        selector_kind=selector_kind,
        selector_key=selector_key,
        observation=narratives[0],
        interpretation=narratives[1],
        recommendation=narratives[2],
    )


def _require_current_receipt(receipt: AnalysisSnapshotReceipt) -> None:
    if (
        receipt.status == AnalysisSnapshotStatus.EXPIRED
        or _database_utc(receipt.expires_at) <= datetime.now(UTC)
    ):
        raise AnalysisFindingError(AnalysisFindingErrorCode.SNAPSHOT_EXPIRED)
    if receipt.status != AnalysisSnapshotStatus.COMPLETED:
        raise AnalysisFindingError(AnalysisFindingErrorCode.SNAPSHOT_NOT_COMPLETED)


def _derive_citation(
    summary: AnalysisSnapshotSummaryV1,
    *,
    selector_kind: AnalysisFindingSelectorKind,
    selector_key: str | None,
) -> _ScalarCitation:
    if selector_kind == AnalysisFindingSelectorKind.OVERALL_THROUGHPUT:
        return _ScalarCitation(
            metric=AnalysisFindingEvidenceMetric.EVENTS_PER_MINUTE,
            value=summary.throughput.per_minute,
            unit=AnalysisFindingEvidenceUnit.EVENTS_PER_MINUTE,
        )
    if selector_kind == AnalysisFindingSelectorKind.LATENCY:
        if summary.latency.p95_ms is None:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTED_RESULT_UNAVAILABLE)
        return _ScalarCitation(
            metric=AnalysisFindingEvidenceMetric.P95_MS,
            value=summary.latency.p95_ms,
            unit=AnalysisFindingEvidenceUnit.MILLISECONDS,
        )
    if selector_kind == AnalysisFindingSelectorKind.FAILURE_RATE:
        return _ScalarCitation(
            metric=AnalysisFindingEvidenceMetric.FAILURE_RATE,
            value=summary.failure_rate.rate,
            unit=AnalysisFindingEvidenceUnit.RATIO,
        )
    if selector_kind == AnalysisFindingSelectorKind.EVENT_TYPE:
        match = next(
            (item for item in summary.event_types if item.event_type == selector_key),
            None,
        )
        if match is None:
            raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_KEY_UNSUPPORTED)
        return _ScalarCitation(
            metric=AnalysisFindingEvidenceMetric.EVENT_COUNT,
            value=float(match.count),
            unit=AnalysisFindingEvidenceUnit.COUNT,
        )
    match = next((item for item in summary.nodes if item.node_id == selector_key), None)
    if match is None:
        raise AnalysisFindingError(AnalysisFindingErrorCode.SELECTOR_KEY_UNSUPPORTED)
    return _ScalarCitation(
        metric=AnalysisFindingEvidenceMetric.EVENT_COUNT,
        value=float(match.event_count),
        unit=AnalysisFindingEvidenceUnit.COUNT,
    )


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _scope_filters(scope: AnalysisSnapshotScope) -> tuple[ColumnElement[bool], ...]:
    return (
        AnalysisFinding.workspace_id == scope.workspace_id,
        AnalysisFinding.project_id == scope.project_id,
        AnalysisFinding.workflow_id == scope.workflow_id,
        AnalysisFinding.run_id == scope.run.id,
    )


__all__ = [
    "AnalysisFindingError",
    "AnalysisFindingErrorCode",
    "create_finding",
    "get_finding",
    "list_findings",
]
