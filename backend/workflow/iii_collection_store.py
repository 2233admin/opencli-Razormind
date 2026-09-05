"""Transactional Admin ledger operations for III collection commands."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models.iii_collection import (
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionExpectedKeyReportV1,
    IIICollectionIngressReceiptV1,
    IIICollectionLifecycleObservationV1,
    IIICollectionOutboundV1,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas.iii_collection import (
    CollectorFinalExpectedKeyReportReadV1,
    CollectorFinalExpectedKeyReportV1,
    IIICollectionLifecycleReadV1,
    IIICollectionLifecycleV1,
    IIICollectionRequestV1,
    IIICollectionSubmitReadV1,
    ODPIngressOutcomeReceiptReadV1,
    ODPIngressOutcomeReceiptV1,
    VerticalEvidenceReferenceV1,
    VerticalStatusV1,
)
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.workflow.workflow_run_events import append_workflow_run_events

OPENCLI_FUNCTION_ID = "odp.collect::opencli_snapshot"


def _utc_timestamp(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive values before comparing ledger evidence."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class IIICollectionConflictError(ValueError):
    """A replay changed immutable command or lifecycle content."""


class IIICollectionNotFoundError(LookupError):
    """The scoped command or attempt does not exist."""


@dataclass(frozen=True)
class CollectionScope:
    workspace_id: str
    project_id: str
    workflow_id: str
    studio_workflow_version_id: str
    run_id: str


@dataclass(frozen=True)
class CollectionSubmission:
    command: IIICollectionCommandV1
    attempt: IIICollectionAttemptV1
    outbound: IIICollectionOutboundV1
    created: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _expected_key_set_hash(report: CollectorFinalExpectedKeyReportV1) -> str:
    keys = sorted(
        {(key.source_id, key.event_id) for key in report.expected_keys},
        key=lambda key: (key[0], key[1]),
    )
    if len(keys) != len(report.expected_keys):
        raise IIICollectionConflictError("Expected source/event keys must be unique")
    return _sha256(
        {
            "expected_keys": [
                {"source_id": source_id, "event_id": event_id}
                for source_id, event_id in keys
            ]
        }
    )


def _report_hash(report: CollectorFinalExpectedKeyReportV1) -> str:
    return _sha256(report.model_dump(mode="json", exclude={"report_hash"}))


def _receipt_hash(receipt: ODPIngressOutcomeReceiptV1) -> str:
    return _sha256(receipt.model_dump(mode="json", exclude={"receipt_hash", "signature"}))


def _validate_fact_identity(
    command: IIICollectionCommandV1,
    attempt: IIICollectionAttemptV1,
    fact: CollectorFinalExpectedKeyReportV1 | ODPIngressOutcomeReceiptV1,
) -> None:
    if not (
        command.workspace_id == fact.workspace_id
        and command.project_id == fact.project_id
        and command.workflow_id == fact.workflow_id
        and command.studio_workflow_version_id == fact.studio_workflow_version_id
        and command.run_id == fact.run_id
        and command.node_id == fact.node_id
        and command.odp_source_id == fact.source_id
        and command.source_binding_id == fact.source_binding_id
        and command.source_binding_revision_id == fact.source_binding_revision_id
        and command.source_binding_revision_number == fact.source_binding_revision_number
        and command.payload_sha256 == fact.payload_sha256
        and attempt.command_id == command.id
        and attempt.attempt_number == fact.attempt_number
        and attempt.task_id == fact.task_id
        and attempt.trace_id == fact.trace_id
        and command.trace_id == fact.trace_id
    ):
        raise IIICollectionConflictError("Fact identity, scope, or payload hash does not match")


async def _fact_target(
    db: AsyncSession, *, command_id: str, attempt_id: str
) -> tuple[IIICollectionCommandV1, IIICollectionAttemptV1]:
    command = await db.get(IIICollectionCommandV1, command_id)
    attempt = await db.get(IIICollectionAttemptV1, attempt_id)
    if command is None or attempt is None or attempt.command_id != command.id:
        raise IIICollectionNotFoundError("Collection fact target not found")
    return command, attempt


async def _report_for_attempt(
    db: AsyncSession, attempt_id: str
) -> IIICollectionExpectedKeyReportV1 | None:
    return (
        await db.execute(
            select(IIICollectionExpectedKeyReportV1).where(
                IIICollectionExpectedKeyReportV1.attempt_id == attempt_id
            )
        )
    ).scalar_one_or_none()


def _outcomes_match_report(
    receipt: ODPIngressOutcomeReceiptV1, report: IIICollectionExpectedKeyReportV1
) -> bool:
    expected = {(key["source_id"], key["event_id"]) for key in report.expected_keys}
    outcomes = [(outcome.source_id, outcome.event_id) for outcome in receipt.outcomes]
    return (
        receipt.expected_key_set_sha256 == report.key_set_sha256
        and len(outcomes) == len(expected)
        and len(set(outcomes)) == len(outcomes)
        and set(outcomes) == expected
        and sum(
            outcome.outcome == "rejected" for outcome in receipt.outcomes
        )
        == report.rejected_count
    )


def canonical_collection_payload(collection: IIICollectionRequestV1) -> tuple[dict, str]:
    """Canonicalize once, before persisting immutable intent or invoking III."""

    site = collection.site.strip()
    command = collection.command.strip()
    if not site or not command:
        raise IIICollectionConflictError("site and command must contain non-whitespace characters")
    source_id = (collection.source_id or "").strip() or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/opencli/{site}/{command}")
    )
    payload: dict = {
        "site": site,
        "command": command,
        "args": collection.args,
        "format": collection.output_format,
        "source_id": source_id,
    }
    if collection.mode is not None:
        payload["mode"] = collection.mode
    return payload, source_id




def _same_submission(
    command: IIICollectionCommandV1,
    *,
    scope: CollectionScope,
    node_id: str,
    collection: IIICollectionRequestV1,
    payload_sha256: str,
) -> bool:
    return (
        command.workspace_id == scope.workspace_id
        and command.project_id == scope.project_id
        and command.workflow_id == scope.workflow_id
        and command.studio_workflow_version_id == scope.studio_workflow_version_id
        and command.run_id == scope.run_id
        and command.node_id == node_id
        and command.source_binding_id == collection.source_binding_id
        and command.source_binding_revision_id == collection.source_binding_revision_id
        and command.source_binding_revision_number == collection.source_binding_revision_number
        and command.payload_sha256 == payload_sha256
    )


async def _attempt_and_outbound(
    db: AsyncSession, command_id: str
) -> tuple[IIICollectionAttemptV1, IIICollectionOutboundV1]:
    attempt = (
        await db.execute(
            select(IIICollectionAttemptV1)
            .where(IIICollectionAttemptV1.command_id == command_id)
            .order_by(IIICollectionAttemptV1.attempt_number.desc())
        )
    ).scalars().first()
    if attempt is None:
        raise IIICollectionNotFoundError("Collection attempt not found")
    outbound = (
        await db.execute(
            select(IIICollectionOutboundV1).where(IIICollectionOutboundV1.attempt_id == attempt.id)
        )
    ).scalar_one_or_none()
    if outbound is None:
        raise IIICollectionNotFoundError("Collection outbound record not found")
    return attempt, outbound


async def _append_admin_event(
    db: AsyncSession,
    *,
    command: IIICollectionCommandV1,
    attempt: IIICollectionAttemptV1,
    stage: str,
    sequence: int | None,
) -> None:
    event_id = f"iii-collection:{command.id}:{attempt.id}:{stage}:{sequence or 0}"
    event_type = {
        "admin_requested": "queued",
        "bridge_accepted": "waiting",
        "collector_started": "started",
        "collector_returned": "waiting",
        "cancel_requested": "blocked",
    }[stage]
    await append_workflow_run_events(
        db,
        run_id=command.run_id,
        events=[
            WorkflowNodeRunEvent(
                id=event_id,
                sequence=1,
                workflowId=command.workflow_id,
                workflowRunId=command.run_id,
                traceId=command.trace_id,
                nodeId=command.node_id,
                eventType=event_type,
                createdAt=_now().isoformat(),
                details={
                    "iiiCollection": {
                        "commandId": command.id,
                        "attemptId": attempt.id,
                        "attemptNumber": attempt.attempt_number,
                        "taskId": attempt.task_id,
                        "payloadSha256": command.payload_sha256,
                        "stage": stage,
                    }
                },
            )
        ],
    )


async def submit_collection(
    db: AsyncSession,
    *,
    scope: CollectionScope,
    run: WorkflowRun,
    node_id: str,
    idempotency_key: str,
    collection: IIICollectionRequestV1,
) -> CollectionSubmission:
    """Commit command, attempt, outbound record, and ``admin_requested`` before dispatch."""

    payload, source_id = canonical_collection_payload(collection)
    payload_sha256 = _sha256(payload)
    existing = (
        await db.execute(
            select(IIICollectionCommandV1).where(
                IIICollectionCommandV1.run_id == scope.run_id,
                IIICollectionCommandV1.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not _same_submission(
            existing,
            scope=scope,
            node_id=node_id,
            collection=collection,
            payload_sha256=payload_sha256,
        ):
            raise IIICollectionConflictError(
                "Idempotency key was reused with a different collection"
            )
        attempt, outbound = await _attempt_and_outbound(db, existing.id)
        return CollectionSubmission(existing, attempt, outbound, created=False)

    command = IIICollectionCommandV1(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        node_id=node_id,
        source_binding_id=collection.source_binding_id,
        source_binding_revision_id=collection.source_binding_revision_id,
        source_binding_revision_number=collection.source_binding_revision_number,
        odp_source_id=source_id,
        collector_function_id=OPENCLI_FUNCTION_ID,
        collector_payload=payload,
        payload_sha256=payload_sha256,
        trace_id=run.trace_id,
        idempotency_key=idempotency_key,
    )
    db.add(command)
    try:
        await db.flush()
        attempt = IIICollectionAttemptV1(
            command_id=command.id,
            attempt_number=1,
            task_id=str(uuid.uuid4()),
            trace_id=run.trace_id,
        )
        db.add(attempt)
        await db.flush()
        outbound = IIICollectionOutboundV1(
            attempt_id=attempt.id,
            state="pending",
            dispatch_count=0,
            available_at=_now(),
        )
        db.add(outbound)
        await _append_admin_event(
            db,
            command=command,
            attempt=attempt,
            stage="admin_requested",
            sequence=None,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(IIICollectionCommandV1).where(
                    IIICollectionCommandV1.run_id == scope.run_id,
                    IIICollectionCommandV1.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if not _same_submission(
            existing,
            scope=scope,
            node_id=node_id,
            collection=collection,
            payload_sha256=payload_sha256,
        ):
            raise IIICollectionConflictError(
                "Idempotency key was reused with a different collection"
            )
        attempt, outbound = await _attempt_and_outbound(db, existing.id)
        return CollectionSubmission(existing, attempt, outbound, created=False)
    await db.refresh(command)
    await db.refresh(attempt)
    await db.refresh(outbound)
    return CollectionSubmission(command, attempt, outbound, created=True)


async def get_scoped_command(
    db: AsyncSession, *, scope: CollectionScope, command_id: str
) -> IIICollectionCommandV1:
    command = await db.get(IIICollectionCommandV1, command_id)
    if command is None or not _same_scope(command, scope):
        raise IIICollectionNotFoundError("Collection command not found")
    return command


def _same_scope(command: IIICollectionCommandV1, scope: CollectionScope) -> bool:
    return (
        command.workspace_id == scope.workspace_id
        and command.project_id == scope.project_id
        and command.workflow_id == scope.workflow_id
        and command.studio_workflow_version_id == scope.studio_workflow_version_id
        and command.run_id == scope.run_id
    )


async def cancel_collection(
    db: AsyncSession, *, command: IIICollectionCommandV1
) -> IIICollectionOutboundV1:
    """Persist cancellation before a dispatcher can invoke III."""

    attempt, outbound = await _attempt_and_outbound(db, command.id)
    if outbound.cancel_requested_at is None:
        outbound.cancel_requested_at = _now()
        if outbound.state in {"pending", "bridge_unavailable"}:
            outbound.cancelled_at = outbound.cancel_requested_at
            outbound.state = "cancelled"
        await _append_admin_event(
            db,
            command=command,
            attempt=attempt,
            stage="cancel_requested",
            sequence=None,
        )
        await db.commit()
        await db.refresh(outbound)
    return outbound


def _lifecycle_hash(event: IIICollectionLifecycleV1) -> str:
    return _sha256(event.model_dump(mode="json"))


async def ingest_lifecycle(
    db: AsyncSession, *, event: IIICollectionLifecycleV1
) -> IIICollectionLifecycleReadV1:
    """Validate complete immutable identity, then deduplicate the lifecycle event key."""

    command = await db.get(IIICollectionCommandV1, event.command_id)
    attempt = await db.get(IIICollectionAttemptV1, event.attempt_id)
    if command is None or attempt is None or attempt.command_id != command.id:
        raise IIICollectionNotFoundError("Collection lifecycle target not found")
    if not (
        command.workspace_id == event.workspace_id
        and command.project_id == event.project_id
        and command.workflow_id == event.workflow_id
        and command.studio_workflow_version_id == event.studio_workflow_version_id
        and command.run_id == event.run_id
        and command.node_id == event.node_id
        and command.odp_source_id == event.source_id
        and command.source_binding_id == event.source_binding_id
        and command.source_binding_revision_id == event.source_binding_revision_id
        and command.source_binding_revision_number == event.source_binding_revision_number
        and command.payload_sha256 == event.payload_sha256
        and attempt.attempt_number == event.attempt_number
        and attempt.task_id == event.task_id
        and attempt.trace_id == event.trace_id
        and command.trace_id == event.trace_id
    ):
        raise IIICollectionConflictError(
            "Lifecycle identity, scope, or payload hash does not match"
        )
    expected = {
        1: "bridge_accepted",
        2: "collector_started",
        3: "collector_returned",
    }.get(event.sequence)
    if expected != event.event_type:
        raise IIICollectionConflictError("Lifecycle sequence and event type are invalid")


    content_hash = _lifecycle_hash(event)
    existing = (
        await db.execute(
            select(IIICollectionLifecycleObservationV1).where(
                IIICollectionLifecycleObservationV1.command_id == event.command_id,
                IIICollectionLifecycleObservationV1.attempt_id == event.attempt_id,
                IIICollectionLifecycleObservationV1.sequence == event.sequence,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.canonical_content_hash != content_hash:
            raise IIICollectionConflictError("Lifecycle sequence was replayed with changed content")
        return IIICollectionLifecycleReadV1(
            command_id=event.command_id,
            attempt_id=event.attempt_id,
            sequence=event.sequence,
            event_type=event.event_type,
            event_hash=existing.canonical_content_hash,
            duplicate=True,
        )
    prior_sequence = await db.scalar(
        select(IIICollectionLifecycleObservationV1.sequence)
        .where(IIICollectionLifecycleObservationV1.attempt_id == attempt.id)
        .order_by(IIICollectionLifecycleObservationV1.sequence.desc())
        .limit(1)
    )
    if (prior_sequence or 0) != event.sequence - 1:
        raise IIICollectionConflictError("Lifecycle observations must arrive in order")


    observation = IIICollectionLifecycleObservationV1(
        version=event.version,
        command_id=command.id,
        attempt_id=attempt.id,
        sequence=event.sequence,
        event_type=event.event_type,
        payload_sha256=event.payload_sha256,
        canonical_content_hash=content_hash,
        details=event.summary.model_dump(mode="json", exclude_none=True),
    )
    db.add(observation)
    _, outbound = await _attempt_and_outbound(db, command.id)
    lifecycle_rank = {
        "bridge_accepted": 1,
        "collector_started": 2,
        "collector_returned": 3,
    }
    if (
        outbound.cancel_requested_at is None
        and lifecycle_rank.get(outbound.state, 0) < lifecycle_rank[event.event_type]
    ):
        outbound.state = event.event_type
    try:
        await _append_admin_event(
            db,
            command=command,
            attempt=attempt,
            stage=event.event_type,
            sequence=event.sequence,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(IIICollectionLifecycleObservationV1).where(
                    IIICollectionLifecycleObservationV1.command_id == event.command_id,
                    IIICollectionLifecycleObservationV1.attempt_id == event.attempt_id,
                    IIICollectionLifecycleObservationV1.sequence == event.sequence,
                )
            )
        ).scalar_one_or_none()
        if existing is None or existing.canonical_content_hash != content_hash:
            raise IIICollectionConflictError("Lifecycle sequence was replayed with changed content")
        return IIICollectionLifecycleReadV1(
            command_id=event.command_id,
            attempt_id=event.attempt_id,
            sequence=event.sequence,
            event_type=event.event_type,
            event_hash=existing.canonical_content_hash,
            duplicate=True,
        )
    return IIICollectionLifecycleReadV1(
        command_id=event.command_id,
        attempt_id=event.attempt_id,
        sequence=event.sequence,
        event_type=event.event_type,
        event_hash=content_hash,
        duplicate=False,
    )

async def ingest_expected_key_report(
    db: AsyncSession, *, report: CollectorFinalExpectedKeyReportV1
) -> CollectorFinalExpectedKeyReportReadV1:
    """Retain one immutable collector boundary without inferring ODP persistence."""

    if report.report_sequence != 1:
        raise IIICollectionConflictError(
            "Only one final expected-key report is permitted per attempt"
        )
    if _expected_key_set_hash(report) != report.expected_key_set_sha256:
        raise IIICollectionConflictError("Expected-key set hash does not match canonical keys")
    if _report_hash(report) != report.report_hash:
        raise IIICollectionConflictError(
            "Expected-key report hash does not match canonical content"
        )
    command, attempt = await _fact_target(
        db, command_id=report.command_id, attempt_id=report.attempt_id
    )
    _validate_fact_identity(command, attempt, report)
    existing = await _report_for_attempt(db, attempt.id)
    if existing is not None:
        if existing.report_hash != report.report_hash or existing.report_id != report.report_id:
            raise IIICollectionConflictError("Expected-key report replay has changed content")
        return CollectorFinalExpectedKeyReportReadV1(
            command_id=command.id,
            attempt_id=attempt.id,
            report_id=existing.report_id,
            report_sequence=existing.report_sequence,
            duplicate=True,
        )
    receipts = list(
        (
            await db.execute(
                select(IIICollectionIngressReceiptV1).where(
                    IIICollectionIngressReceiptV1.attempt_id == attempt.id
                )
            )
        )
        .scalars()
        .all()
    )
    expected = {(key.source_id, key.event_id) for key in report.expected_keys}
    for receipt in receipts:
        outcomes = [(outcome["source_id"], outcome["event_id"]) for outcome in receipt.outcomes]
        if (
            receipt.expected_key_set_sha256 != report.expected_key_set_sha256
            or len(outcomes) != len(expected)
            or len(set(outcomes)) != len(outcomes)
            or set(outcomes) != expected
            or sum(outcome["outcome"] == "rejected" for outcome in receipt.outcomes)
            != report.rejected_count
        ):
            raise IIICollectionConflictError(
                "Expected-key report does not match retained ingress receipt evidence"
            )
    observation = IIICollectionExpectedKeyReportV1(
        version=report.version,
        report_id=report.report_id,
        command_id=command.id,
        attempt_id=attempt.id,
        report_sequence=report.report_sequence,
        payload_sha256=report.payload_sha256,
        key_set_sha256=report.expected_key_set_sha256,
        item_count=report.item_count,
        zero_count=report.zero_count,
        rejected_count=report.rejected_count,
        expected_keys=[key.model_dump() for key in report.expected_keys],
        reported_at=report.reported_at,
        report_hash=report.report_hash,
    )
    db.add(observation)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _report_for_attempt(db, attempt.id)
        if (
            existing is None
            or existing.report_hash != report.report_hash
            or existing.report_id != report.report_id
        ):
            raise IIICollectionConflictError("Expected-key report replay has changed content")
        return CollectorFinalExpectedKeyReportReadV1(
            command_id=command.id,
            attempt_id=attempt.id,
            report_id=existing.report_id,
            report_sequence=existing.report_sequence,
            duplicate=True,
        )
    return CollectorFinalExpectedKeyReportReadV1(
        command_id=command.id,
        attempt_id=attempt.id,
        report_id=observation.report_id,
        report_sequence=observation.report_sequence,
        duplicate=False,
    )


async def ingest_ingress_receipt(
    db: AsyncSession, *, receipt: ODPIngressOutcomeReceiptV1
) -> ODPIngressOutcomeReceiptReadV1:
    """Verify and append one signed odp-ingest ingress observation."""

    if _receipt_hash(receipt) != receipt.receipt_hash:
        raise IIICollectionConflictError("Ingress receipt hash does not match canonical content")
    if receipt.producer_id != "odp-ingest":
        raise IIICollectionConflictError("Ingress receipt producer is not authoritative")
    secret = get_settings().iii_ingress_receipt_secret
    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"), receipt.receipt_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not secret or not hmac.compare_digest(expected_signature, receipt.signature):
        raise IIICollectionConflictError("Ingress receipt signature is invalid or unconfigured")
    command, attempt = await _fact_target(
        db, command_id=receipt.command_id, attempt_id=receipt.attempt_id
    )
    _validate_fact_identity(command, attempt, receipt)
    report = await _report_for_attempt(db, attempt.id)
    if report is not None and not _outcomes_match_report(receipt, report):
        raise IIICollectionConflictError("Ingress receipt does not match the expected-key report")
    existing = (
        await db.execute(
            select(IIICollectionIngressReceiptV1).where(
                or_(
                    IIICollectionIngressReceiptV1.receipt_id == receipt.receipt_id,
                    (
                        IIICollectionIngressReceiptV1.producer_id == receipt.producer_id
                    )
                    & (IIICollectionIngressReceiptV1.idempotency_key == receipt.idempotency_key),
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.receipt_hash != receipt.receipt_hash
            or existing.signature != receipt.signature
            or existing.receipt_id != receipt.receipt_id
            or existing.producer_id != receipt.producer_id
            or existing.idempotency_key != receipt.idempotency_key
        ):
            raise IIICollectionConflictError("Ingress receipt replay has changed content")
        return ODPIngressOutcomeReceiptReadV1(
            command_id=command.id,
            attempt_id=attempt.id,
            receipt_id=existing.receipt_id,
            duplicate=True,
        )
    observation = IIICollectionIngressReceiptV1(
        version=receipt.version,
        receipt_id=receipt.receipt_id,
        idempotency_key=receipt.idempotency_key,
        producer_id=receipt.producer_id,
        producer_key_id=receipt.producer_key_id,
        command_id=command.id,
        attempt_id=attempt.id,
        payload_sha256=receipt.payload_sha256,
        expected_key_set_sha256=receipt.expected_key_set_sha256,
        outcomes=[outcome.model_dump() for outcome in receipt.outcomes],
        issued_at=receipt.issued_at,
        receipt_hash=receipt.receipt_hash,
        signature=receipt.signature,
    )
    db.add(observation)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(IIICollectionIngressReceiptV1).where(
                    IIICollectionIngressReceiptV1.receipt_id == receipt.receipt_id
                )
            )
        ).scalar_one_or_none()
        if existing is None or existing.receipt_hash != receipt.receipt_hash:
            raise IIICollectionConflictError("Ingress receipt replay has changed content")
        return ODPIngressOutcomeReceiptReadV1(
            command_id=command.id,
            attempt_id=attempt.id,
            receipt_id=existing.receipt_id,
            duplicate=True,
        )
    return ODPIngressOutcomeReceiptReadV1(
        command_id=command.id,
        attempt_id=attempt.id,
        receipt_id=observation.receipt_id,
        duplicate=False,
    )


async def collection_status(
    db: AsyncSession, *, command: IIICollectionCommandV1
) -> VerticalStatusV1:
    attempt, outbound = await _attempt_and_outbound(db, command.id)
    observations = list(
        (
            await db.execute(
                select(IIICollectionLifecycleObservationV1)
                .where(IIICollectionLifecycleObservationV1.attempt_id == attempt.id)
                .order_by(IIICollectionLifecycleObservationV1.sequence)
            )
        )
        .scalars()
        .all()
    )
    report = await _report_for_attempt(db, attempt.id)
    receipts = list(
        (
            await db.execute(
                select(IIICollectionIngressReceiptV1)
                .where(IIICollectionIngressReceiptV1.attempt_id == attempt.id)
                .order_by(IIICollectionIngressReceiptV1.issued_at)
            )
        )
        .scalars()
        .all()
    )
    state = outbound.state
    if outbound.cancel_requested_at is not None and state != "cancelled":
        state = "cancel_requested"
    if state == "cancelled":
        blocking_stage, action, uncertain = None, "none", False
    elif state == "cancel_requested":
        blocking_stage, action, uncertain = "cancellation", "await_lifecycle", True
    elif receipts and report is not None:
        blocking_stage, action, uncertain = "reconciliation", "await_reconciliation", True
    elif receipts:
        blocking_stage, action, uncertain = "collector_report", "await_collector_report", True
    elif report is not None and report.zero_count == 1:
        blocking_stage, action, uncertain = "reconciliation", "await_reconciliation", True
    elif report is not None:
        blocking_stage, action, uncertain = "ingress", "await_ingress_receipt", True
    elif state == "pending":
        blocking_stage, action, uncertain = "dispatch", "resume_dispatch", False
    elif state == "bridge_unavailable":
        blocking_stage, action, uncertain = "bridge", "resume_dispatch", True
    elif state == "bridge_accepted":
        blocking_stage, action, uncertain = "collector", "await_lifecycle", True
    elif state == "collector_started":
        blocking_stage, action, uncertain = "collector", "await_lifecycle", True
    elif report is None:
        blocking_stage, action, uncertain = "collector_report", "await_collector_report", True
    elif not receipts:
        blocking_stage, action, uncertain = "ingress", "await_ingress_receipt", True
    else:  # Ingress and collector facts are still nonterminal pending #32 reconciliation.
        blocking_stage, action, uncertain = "reconciliation", "await_reconciliation", True

    evidence = [
        *[
            VerticalEvidenceReferenceV1(
                kind="lifecycle",
                hash=observation.canonical_content_hash,
                event_type=observation.event_type,
            )
            for observation in observations
        ],
        *(
            [
                VerticalEvidenceReferenceV1(
                    kind="expected_key_report",
                    hash=report.report_hash,
                )
            ]
            if report is not None
            else []
        ),
        *[
            VerticalEvidenceReferenceV1(
                kind="ingress_receipt",
                hash=receipt.receipt_hash,
            )
            for receipt in receipts
        ],
    ]
    return VerticalStatusV1(
        command_id=command.id,
        attempt_id=attempt.id,
        state=state,
        blocking_stage=blocking_stage,
        evidence_references=evidence,
        recovery_action=action,
        side_effect_uncertainty=uncertain,
        updated_at=max(
            _utc_timestamp(value)
            for value in (
                command.updated_at,
                outbound.updated_at,
                *(observation.created_at for observation in observations),
                *((report.created_at,) if report is not None else ()),
                *(receipt.created_at for receipt in receipts),
            )
        ),
    )


def submission_read(submission: CollectionSubmission) -> IIICollectionSubmitReadV1:
    return IIICollectionSubmitReadV1(
        command_id=submission.command.id,
        attempt_id=submission.attempt.id,
        attempt_number=submission.attempt.attempt_number,
        task_id=submission.attempt.task_id,
        trace_id=submission.attempt.trace_id,
        payload_sha256=submission.command.payload_sha256,
        created=submission.created,
        dispatch_state=submission.outbound.state,
    )
