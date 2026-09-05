"""ledger: persist suggested ControlActions as evidence (PR-Control-3.5).

See docs/CONTROL_THEORY_ARCHITECTURE.md §4-5. The feedback law
(``backend.control.policies``) is only trustworthy if we can measure how
often its suggestions turn out to be justified — this module writes each
surfaced suggestion to the ``control_actions`` table
(``backend.models.control_action.ControlActionRecord``) so the outcome pass
(``backend.control.outcomes``) can judge it against the source's subsequent
sensor readings.

HARD RULE: recording evidence is NOT acting. Nothing here mutates a
``DataSource``, calls the scheduler, or executes anything — every row is
written with ``executed=False``. PR-Control-4's actuator is intended to call
:func:`record_advisory_actions` too (with ``mode="automatic"``) so both
advisory and executed decisions share one auditable ledger.

Dedup: the control-state endpoint is polled by the frontend, so the same
unresolved state re-surfaces the same suggestions every few seconds. A
suggestion is skipped when the latest ledger row for the same
``(source_id, action_type)`` carries the same state and is younger than the
dedup window — re-observing an unchanged error signal adds no information,
while a state CHANGE (different verdict for the same action) is always worth
a fresh row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control.measurements import SourceMeasurement
from backend.control.models import ControlAction, SourceControlState
from backend.models.control_action import ControlActionRecord


def ensure_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC (SQLite round-trips tz-aware columns as
    naive) so age math never raises on aware-vs-naive comparison. Shared with
    ``backend.control.outcomes``, which does the same age math on the same
    columns."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def record_advisory_actions(
    session: AsyncSession,
    *,
    source_id: str,
    state: SourceControlState,
    actions: list[ControlAction],
    measurement: SourceMeasurement,
    measurement_row_id: str | None,
    run_id: str | None,
    mode: str,
    dedup_seconds: int,
) -> int:
    """Write one ``control_actions`` row per suggested action; return how many
    rows were actually written after dedup.

    ``measurement_row_id``/``run_id`` are the provenance of the decision's
    sensor reading — pass ``None`` for both when the measurement came from the
    PR-Control-2 TaskRunEvent fallback path (no persisted row to point at);
    ``measurement_before`` keeps the full reading either way, so the decision
    stays replayable even without a row to join.

    Never commits — flushes into the caller's session/transaction so the
    write shares the request's commit/rollback fate. Callers treat failures
    as best-effort (see ``backend.api.v1.sources.get_source_control_state``).
    """
    now = datetime.now(UTC)
    written = 0

    for action in actions:
        latest = (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.source_id == source_id)
                .where(ControlActionRecord.action_type == action.action_type)
                .order_by(ControlActionRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if (
            latest is not None
            and latest.state == state.value
            and (now - ensure_utc(latest.created_at)).total_seconds() < dedup_seconds
        ):
            continue

        session.add(
            ControlActionRecord(
                source_id=source_id,
                run_id=run_id,
                measurement_id=measurement_row_id,
                mode=mode,
                state=state.value,
                action_type=action.action_type,
                reason=action.reason,
                payload=dict(action.payload),
                # This module records suggestions; it never acts. PR-Control-4's
                # actuator flips this to True for rows it actually performs.
                executed=False,
                measurement_before=measurement.model_dump(mode="json"),
            )
        )
        written += 1

    if written:
        await session.flush()
    return written


async def record_executed_action(
    session: AsyncSession,
    *,
    source_id: str,
    state: SourceControlState,
    action_type: str,
    reason: str,
    payload: dict,
    measurement: SourceMeasurement,
    measurement_row_id: str | None,
    run_id: str | None,
) -> ControlActionRecord:
    """Write one ``control_actions`` row for an action the actuator ACTUALLY
    performed (issue 03 / PR-Control-4) — ``mode="automatic"``,
    ``executed=True``. Same table as :func:`record_advisory_actions`;
    outcome judgment (``backend.control.outcomes``) applies to these rows
    identically since it has no ``mode``/``executed`` filter.

    Unlike the advisory recorder, this never dedups: an executed action is
    itself the record of a real mutation, not a repeatable suggestion, so
    every execution gets its own row. Callers (the Control Cycle) are
    responsible for their OWN idempotency check (an unresolved identical
    executed row for the same (source, action_type, state)) before calling
    this — see ``backend.control.gate``.

    Never commits — flushes into the caller's session, same as the advisory
    recorder.
    """
    row = ControlActionRecord(
        source_id=source_id,
        run_id=run_id,
        measurement_id=measurement_row_id,
        mode="automatic",
        state=state.value,
        action_type=action_type,
        reason=reason,
        payload=dict(payload),
        executed=True,
        measurement_before=measurement.model_dump(mode="json"),
    )
    session.add(row)
    await session.flush()
    return row
