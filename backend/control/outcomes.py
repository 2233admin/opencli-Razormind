"""outcomes: judge recorded advisory suggestions against later evidence
(PR-Control-3.5).

See docs/CONTROL_THEORY_ARCHITECTURE.md §4-5. The ledger
(``backend.control.ledger``) records what the feedback law suggested; this
module closes the loop by re-reading the plant AFTER the decision and asking
one question per row: did the triggering state clear?

    * "recovered"          — re-classifying the source from its post-decision
      ``source_measurements`` rows no longer yields the state that triggered
      the suggestion. (Deliberately NOT "post state is healthy": an
      auth_failed source that is now merely degraded still recovered FROM
      auth_failed — each judgment is scoped to its own trigger.)
    * "persisted"          — the post-decision evidence still classifies to
      the same state; the problem outlived the suggestion window.
    * "insufficient_data"  — the source never produced another measurement
      within the stale window. An honest "we never got to see", not a verdict.

Re-classification reuses the EXACT live pipeline — ``aggregation.
row_to_measurement`` + ``aggregation.trend_from_rows`` + ``evaluator.
evaluate`` with the same resolved :class:`SourceObjective` the control-state
endpoint applies — the source's stored objective override (if any), merged
over defaults through ``backend.control.objectives.resolve_objective`` — so
a judgment can never disagree with the endpoint merely because it read the
sensor, or the setpoints, differently.

Advisory-only, like everything in PR-Control-3.x: this module writes ONLY the
judgment columns (evaluated_at / outcome / outcome_detail) back onto
``control_actions`` rows. It never touches a ``DataSource`` and never
executes anything. Deterministic given ``now``: tests inject it explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.control import aggregation, evaluator
from backend.control.ledger import ensure_utc
from backend.control.objectives import resolve_objective
from backend.models.control_action import ControlActionRecord
from backend.models.source import DataSource
from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow


async def evaluate_pending_outcomes(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    min_age_seconds: int | None = None,
    stale_after_seconds: int | None = None,
    window: int = 5,
) -> dict:
    """Judge every ripe pending ledger row; return the count per verdict.

    A row is ripe once it has aged at least ``min_age_seconds`` — the plant
    needs time to produce a post-decision reading before "no new evidence"
    means anything. Younger rows and rows with no post-decision measurement
    yet (but not stale) are left pending, untouched, for a later pass.

    Returns ``{"evaluated", "recovered", "persisted", "insufficient_data",
    "still_pending"}`` — ``evaluated`` is the sum of the three verdict
    buckets from THIS pass; ``still_pending`` counts unripe/awaiting rows.

    Flushes but never commits — the caller's session/transaction decides.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    if min_age_seconds is None:
        min_age_seconds = settings.control_outcome_min_age_seconds
    if stale_after_seconds is None:
        stale_after_seconds = settings.control_outcome_stale_seconds

    pending_rows = (
        (
            await session.execute(
                select(ControlActionRecord)
                .where(ControlActionRecord.evaluated_at.is_(None))
                .order_by(ControlActionRecord.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    counts = {
        "evaluated": 0,
        "recovered": 0,
        "persisted": 0,
        "insufficient_data": 0,
        "still_pending": 0,
    }

    for row in pending_rows:
        decided_at = ensure_utc(row.created_at)
        age_seconds = (now - decided_at).total_seconds()
        # Age filtering happens in Python, not SQL: pending rows are few, and
        # comparing datetimes Python-side sidesteps SQLite's string-typed
        # datetime comparison entirely.
        if age_seconds < min_age_seconds:
            counts["still_pending"] += 1
            continue

        post_rows = (
            (
                await session.execute(
                    select(SourceMeasurementRow)
                    .where(SourceMeasurementRow.source_id == row.source_id)
                    .where(SourceMeasurementRow.measured_at > decided_at)
                    .order_by(SourceMeasurementRow.measured_at.desc())
                    .limit(window)
                )
            )
            .scalars()
            .all()
        )

        if not post_rows:
            if age_seconds < stale_after_seconds:
                # No post-decision reading yet, but not stale — the source may
                # simply not have run again. Keep waiting.
                counts["still_pending"] += 1
                continue
            row.outcome = "insufficient_data"
            row.outcome_detail = {
                "post_state": None,
                "post_measurements": 0,
                "window": window,
            }
            row.evaluated_at = now
            counts["insufficient_data"] += 1
            counts["evaluated"] += 1
            continue

        # Re-classify from the post-decision evidence with the live pipeline:
        # newest post row as the measurement, trend over the post window.
        # system_context is deliberately omitted — the shared ODP plane's
        # CURRENT state says nothing about conditions during this row's
        # post-decision window, and fabricating it would corrupt judgments
        # of non-ODP states.
        measurement = aggregation.row_to_measurement(post_rows[0])
        trend = aggregation.trend_from_rows(list(post_rows))
        # Same resolve helper the control-state endpoint applies (issue 02) —
        # a stored per-source override merges over defaults identically on
        # both sides, so a judgment can never disagree with the endpoint
        # merely because it applied different setpoints. A source row that
        # no longer exists (deleted after the suggestion was recorded)
        # degrades to plain defaults rather than raising mid-pass.
        source_row = await session.get(DataSource, row.source_id)
        objective = resolve_objective(
            source_row.objective_override if source_row is not None else None
        )
        post_state = evaluator.evaluate(
            measurement,
            objective,
            trend=(
                {
                    "window": trend.window,
                    "zero_accepted_streak": trend.zero_accepted_streak,
                    "avg_error_rate": trend.avg_error_rate,
                    "rate_limited_runs": trend.rate_limited_runs,
                }
                if trend is not None
                else None
            ),
            system_context=None,
        )

        row.outcome = "recovered" if post_state.value != row.state else "persisted"
        row.outcome_detail = {
            "post_state": post_state.value,
            "post_measurements": len(post_rows),
            "window": window,
        }
        row.evaluated_at = now
        counts[row.outcome] += 1
        counts["evaluated"] += 1

    if counts["evaluated"]:
        await session.flush()
    return counts
