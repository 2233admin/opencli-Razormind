"""Append-only persistence for replayable WorkflowRun events."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.workflow import WorkflowNodeRunEvent
from backend.workflow.workflow_plugins import (
    WorkflowPluginCapability,
    WorkflowPluginRegistry,
)

MAX_SEQUENCE_RESERVATION_ATTEMPTS = 8
MAX_EVENT_APPEND_ATTEMPTS = 3


async def lock_scoped_workflow_run(
    session: AsyncSession,
    *,
    workflow_id: str,
    studio_workflow_version_id: str,
    run_id: str,
) -> WorkflowRun | None:
    """Lock one scoped run before reading or mutating freshness-sensitive facts.

    PostgreSQL uses a row write lock. SQLite ignores ``FOR UPDATE``, so a
    no-op update acquires its transaction-wide write barrier without changing
    the persisted run projection.
    """
    filters = (
        WorkflowRun.id == run_id,
        WorkflowRun.workflow_id == workflow_id,
        WorkflowRun.studio_workflow_version_id == studio_workflow_version_id,
    )
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = await session.execute(
            update(WorkflowRun).where(*filters).values(updated_at=WorkflowRun.updated_at)
        )
        if result.rowcount != 1:
            return None
        return await session.scalar(select(WorkflowRun).where(*filters))
    return await session.scalar(select(WorkflowRun).where(*filters).with_for_update())


class WorkflowRunEventAppendError(RuntimeError):
    """Base class for stable workflow event append failures."""

    code = "workflow_run_event_append_failed"


class WorkflowRunEventConflictError(WorkflowRunEventAppendError):
    """The same stable event ID was reused for different canonical content."""

    code = "workflow_run_event_conflict"


class WorkflowRunEventSequenceConflictError(WorkflowRunEventAppendError):
    """The per-run sequence allocator could not reserve a range."""

    code = "workflow_run_event_sequence_conflict"


class WorkflowRunEventMigrationError(WorkflowRunEventAppendError):
    """Stopped-writer reconciliation found unsafe legacy event state."""

    code = "workflow_run_event_migration_conflict"


@dataclass(frozen=True)
class WorkflowRunEventAppendResult:
    events: list[WorkflowNodeRunEvent]
    appended_events: list[WorkflowNodeRunEvent]


async def append_workflow_run_events(
    session: AsyncSession,
    *,
    run_id: str,
    events: list[WorkflowNodeRunEvent],
    plugins: WorkflowPluginRegistry | None = None,
) -> WorkflowRunEventAppendResult:
    """Append only the unseen suffix of a replayable event transcript.

    The caller owns the surrounding transaction. Event sequences are allocated
    here, not trusted from the input, so all writers targeting a run share one
    monotonic counter.
    """

    if not events:
        return WorkflowRunEventAppendResult(events=[], appended_events=[])

    _validate_input(run_id, events)
    await _lock_run_event_allocator(session, run_id)
    event_ids = [event.id for event in events]
    last_integrity_error: IntegrityError | None = None
    for _ in range(MAX_EVENT_APPEND_ATTEMPTS):
        existing_rows = (
            (
                await session.execute(
                    select(WorkflowRunEvent).where(
                        WorkflowRunEvent.event_id.in_(event_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        accepted, unseen = _classify_events(
            run_id,
            events,
            {row.event_id: row for row in existing_rows},
        )
        if not unseen:
            return WorkflowRunEventAppendResult(
                events=accepted,
                appended_events=[],
            )

        if (
            plugins is not None
            and plugins.has_capability(WorkflowPluginCapability.EVENT_VALIDATION)
        ):
            persisted_events = [
                WorkflowNodeRunEvent.model_validate(row.payload)
                for row in (
                    (
                        await session.execute(
                            select(WorkflowRunEvent)
                            .where(WorkflowRunEvent.run_id == run_id)
                            .order_by(WorkflowRunEvent.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )
            ]
            next_sequence = max(
                (event.sequence for event in persisted_events),
                default=0,
            )
            candidate_events = [
                *persisted_events,
                *[
                    event.model_copy(
                        update={"sequence": next_sequence + offset},
                        deep=True,
                    )
                    for offset, event in enumerate(unseen, start=1)
                ],
            ]
            try:
                plugins.validate_events(
                    candidate_events,
                    run_id=run_id,
                    trace_id=events[0].traceId,
                )
            except ValueError as exc:
                raise WorkflowRunEventConflictError(str(exc)) from exc

        try:
            async with session.begin_nested():
                first_sequence = await _reserve_sequence_range(
                    session,
                    run_id,
                    len(unseen),
                )
                appended = [
                    event.model_copy(
                        update={"sequence": first_sequence + offset},
                        deep=True,
                    )
                    for offset, event in enumerate(unseen)
                ]
                for event in appended:
                    session.add(
                        WorkflowRunEvent(
                            run_id=run_id,
                            workflow_id=event.workflowId,
                            trace_id=event.traceId,
                            event_id=event.id,
                            node_id=event.nodeId,
                            sequence=event.sequence,
                            event_type=event.eventType,
                            payload=event.model_dump(mode="json"),
                        )
                    )
                await session.flush()
        except IntegrityError as exc:
            last_integrity_error = exc
            continue
        return WorkflowRunEventAppendResult(
            events=[*accepted, *appended],
            appended_events=appended,
        )

    assert last_integrity_error is not None
    raise last_integrity_error




async def reconcile_workflow_run_event_counters(session: AsyncSession) -> int:
    """Reconcile all run counters while every legacy/new writer is stopped.

    This is the binding operation between the expand migration and deployment
    of the shared writer. Callers must enforce the stopped-writer window.
    """

    duplicate = (
        await session.execute(
            select(
                WorkflowRunEvent.run_id,
                WorkflowRunEvent.sequence,
                func.count().label("duplicate_count"),
            )
            .group_by(WorkflowRunEvent.run_id, WorkflowRunEvent.sequence)
            .having(func.count() > 1)
            .limit(1)
        )
    ).first()
    if duplicate is not None:
        raise WorkflowRunEventMigrationError(
            "cannot reconcile counters with duplicate legacy run sequences: "
            f"{tuple(duplicate)}"
        )

    next_sequence = (
        select(func.coalesce(func.max(WorkflowRunEvent.sequence), 0) + 1)
        .where(WorkflowRunEvent.run_id == WorkflowRun.id)
        .correlate(WorkflowRun)
        .scalar_subquery()
    )
    result = await session.execute(
        _counter_reconciliation_statement(next_sequence)
    )
    await session.flush()
    return result.rowcount


def _validate_input(run_id: str, events: list[WorkflowNodeRunEvent]) -> None:
    seen: dict[str, str] = {}
    for event in events:
        if event.workflowRunId != run_id:
            raise WorkflowRunEventConflictError(
                f"event_id {event.id!r} belongs to run {event.workflowRunId!r}, not {run_id!r}"
            )
        canonical = _canonical_payload(event.model_dump(mode="json"))
        prior = seen.get(event.id)
        if prior is not None and prior != canonical:
            raise WorkflowRunEventConflictError(
                f"event_id {event.id!r} is repeated with different canonical payload"
            )
        if prior is not None:
            raise WorkflowRunEventConflictError(
                f"event_id {event.id!r} is repeated within one append request"
            )
        seen[event.id] = canonical


def _classify_events(
    run_id: str,
    events: list[WorkflowNodeRunEvent],
    existing_by_id: dict[str, WorkflowRunEvent],
) -> tuple[list[WorkflowNodeRunEvent], list[WorkflowNodeRunEvent]]:
    accepted: list[WorkflowNodeRunEvent] = []
    unseen: list[WorkflowNodeRunEvent] = []
    reached_unseen_suffix = False
    for event in events:
        existing = existing_by_id.get(event.id)
        if existing is None:
            reached_unseen_suffix = True
            unseen.append(event)
            continue
        if reached_unseen_suffix:
            raise WorkflowRunEventConflictError(
                f"event transcript for run {run_id!r} is not an append-only suffix"
            )
        if existing.run_id != run_id or not _same_canonical_payload(event, existing):
            raise WorkflowRunEventConflictError(
                f"event_id {event.id!r} already exists with different canonical payload"
            )
        accepted.append(WorkflowNodeRunEvent.model_validate(existing.payload))
    return accepted, unseen


def _same_canonical_payload(
    event: WorkflowNodeRunEvent,
    existing: WorkflowRunEvent,
) -> bool:
    candidate = event.model_copy(update={"sequence": existing.sequence}, deep=True)
    return _canonical_payload(candidate.model_dump(mode="json")) == _canonical_payload(
        existing.payload
    )


def _canonical_payload(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


async def _reserve_sequence_range(
    session: AsyncSession,
    run_id: str,
    count: int,
) -> int:
    await session.flush()
    for _ in range(MAX_SEQUENCE_RESERVATION_ATTEMPTS):
        current = await session.scalar(
            select(WorkflowRun.next_event_sequence).where(WorkflowRun.id == run_id)
        )
        if current is None:
            raise WorkflowRunEventSequenceConflictError(
                f"workflow run {run_id!r} does not exist"
            )
        result = await session.execute(
            _sequence_reservation_statement(run_id, current, count)
        )
        if result.rowcount == 1:
            return current
    raise WorkflowRunEventSequenceConflictError(
        f"could not reserve {count} event sequences for run {run_id!r}"
    )


async def _lock_run_event_allocator(
    session: AsyncSession,
    run_id: str,
) -> None:
    await session.flush()
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(next_event_sequence=WorkflowRun.next_event_sequence)
            .execution_options(synchronize_session=False)
        )
        found = result.rowcount == 1
    elif bind.dialect.name == "postgresql":
        # A transition waiting to be flushed can already hold a KEY SHARE lock
        # on the run through its foreign key. Upgrading two such transactions
        # to FOR UPDATE deadlocks. A transaction-scoped advisory lock provides
        # the same per-run serialization without participating in row-lock
        # upgrades; the following lookup still fails closed for a missing run.
        # hashtextextended yields a 64-bit key. The stable seed namespaces these
        # allocator locks away from unrelated advisory-lock users.
        await session.scalar(_postgres_allocator_lock_statement(run_id))
        found = (
            await session.scalar(
                select(WorkflowRun.id).where(WorkflowRun.id == run_id)
            )
        ) is not None
    else:
        found = (
            await session.scalar(
                select(WorkflowRun.id)
                .where(WorkflowRun.id == run_id)
                .with_for_update()
            )
        ) is not None
    if not found:
        raise WorkflowRunEventSequenceConflictError(
            f"workflow run {run_id!r} does not exist"
        )


def _postgres_allocator_lock_statement(run_id: str):
    return select(
        func.pg_advisory_xact_lock(
            func.hashtextextended(run_id, 22363277192072265)
        )
    )


def _sequence_reservation_statement(run_id: str, current: int, count: int):
    return (
        update(WorkflowRun)
        .where(
            WorkflowRun.id == run_id,
            WorkflowRun.next_event_sequence == current,
        )
        .values(next_event_sequence=current + count)
        .execution_options(synchronize_session=False)
    )


def _counter_reconciliation_statement(next_sequence):
    return (
        update(WorkflowRun)
        .values(next_event_sequence=next_sequence)
        .execution_options(synchronize_session=False)
    )


__all__ = [
    "WorkflowRunEventAppendError",
    "WorkflowRunEventAppendResult",
    "WorkflowRunEventMigrationError",
    "WorkflowRunEventSequenceConflictError",
    "append_workflow_run_events",
    "reconcile_workflow_run_event_counters",
]
