"""Aggregate existing run evidence into a SourceMeasurement (read-only).

PR-Control-3: prefer the rich, truthful per-run rows C1's
``backend.control.recorder.record_run_measurement`` now persists to the
``source_measurements`` TABLE (``backend.models.source_measurement.
SourceMeasurement`` — accepted/duplicates/rejected/error_rate/duplicate_rate/
error_kinds/cursor_advanced/freshness_lag_seconds/source_ts_quality/...).
That table is the truthful sensor history; this module's job is to read its
latest row for a source and map it onto the pure
:class:`backend.control.measurements.SourceMeasurement` contract.

Falls back to the PR-Control-2 path (deriving a measurement from
TaskRun/TaskRunEvent evidence) only when the source has never had a
``source_measurements`` row recorded yet — e.g. a source that ran before C1
landed, or in a test that seeds TaskRun/TaskRunEvent directly without going
through the real pipeline/recorder. Every query here is a SELECT — this
module never writes, and it does not change collection/pipeline/runner
behavior.

See docs/CONTROL_THEORY_ARCHITECTURE.md §4-5. The measurement contract and the
safe rate derivation live in PR-Control-1
(:class:`backend.control.measurements.SourceMeasurement`) and are reused, not
redefined.

Where the TaskRunEvent-fallback numbers come from
--------------------------------------------------
A run's per-sink breakdown (accepted/duplicates/rejected) is NOT persisted to
its own columns anywhere on TaskRun — ``SinkResult`` flows transiently through
the pipeline and only its aggregate lands in the ``complete`` TaskRunEvent's
``detail`` JSON as ``{"collected", "stored", "skipped", "duration_ms"}`` (see
backend/pipeline/pipeline.py). So the ``complete`` event detail is the single
durable place a collected/stored/skipped breakdown survives in that fallback
path, and we map:

    accepted   := stored   (new records the sink committed)
    duplicates := skipped  (items recognized as already-seen)
    rejected   := max(0, collected - stored - skipped)

A failed run never emits a ``complete`` event; we still build a measurement
from ``TaskRun.records_collected`` (which the runner sets to ``stored``) rather
than returning ``None`` — a failed run is still evidence.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control.measurements import SourceMeasurement
from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow
from backend.models.task import CollectionTask, TaskRun, TaskRunEvent

#: Trend provenance values (issue 06 — closeout PRD "Trend fallback").
#: ``measurements`` = summarized over persisted ``source_measurements`` rows
#: (the rich, truthful sensor history). ``run_history`` = derived from
#: TaskRun/TaskRunEvent evidence via the PR-Control-2 fallback path, used only
#: when a source has NO measurement rows at all — so a pre-measurement source
#: still gets a trend without the fallback ever masquerading as full sensor
#: coverage.
TREND_PROVENANCE_MEASUREMENTS = "measurements"
TREND_PROVENANCE_RUN_HISTORY = "run_history"


class SourceTrend:
    """A small rolling-window summary over a source's recent
    ``source_measurements`` rows — PR-Control-3's evaluator input.

    Not a Pydantic model (kept internal to control/): the API-facing shape is
    ``backend.schemas.control.TrendRead``, built from this by the endpoint.

    ``provenance`` records which evidence produced the summary — see
    :data:`TREND_PROVENANCE_MEASUREMENTS` / :data:`TREND_PROVENANCE_RUN_HISTORY`.
    It exists so the API response can keep coverage honest (a run-history
    fallback trend must never be presented as measurement-backed); it does NOT
    feed the evaluator, and it never changes coverage/confidence math.
    """

    def __init__(
        self,
        *,
        window: int,
        zero_accepted_streak: int,
        avg_error_rate: float,
        rate_limited_runs: int,
        provenance: str = TREND_PROVENANCE_MEASUREMENTS,
    ) -> None:
        self.window = window
        self.zero_accepted_streak = zero_accepted_streak
        self.avg_error_rate = avg_error_rate
        self.rate_limited_runs = rate_limited_runs
        self.provenance = provenance


def row_to_measurement(row: SourceMeasurementRow) -> SourceMeasurement:
    """Map a persisted ``source_measurements`` row onto the pure contract.

    Public (PR-Control-3.5): the outcome-judgment pass
    (``backend.control.outcomes``) re-classifies a source from its
    post-decision rows and must use THIS mapping — not a re-derived copy —
    so a decision and its later judgment read the sensor identically.
    """
    return SourceMeasurement.derive(
        source_id=row.source_id,
        run_id=row.run_id,
        accepted=row.accepted,
        duplicates=row.duplicates,
        rejected=row.rejected,
        fetch_latency_ms=row.fetch_latency_ms or 0,
        observed_at=row.measured_at,
        cursor_advanced=row.cursor_advanced,
        ingest_latency_ms=row.ingest_latency_ms,
        store_latency_ms=row.store_latency_ms,
        freshness_lag_seconds=row.freshness_lag_seconds,
        # ODP metrics are system-level (see backend.control.collectors.
        # odp_metrics), not per-run — source_measurements rows never carry
        # them, so this contract field stays unpopulated from the DB row.
        # The endpoint fills system_context separately.
        odp_stream_lag=None,
        odp_pending=None,
        dlq_count=0,
        error_kinds=dict(row.error_kinds or {}),
        source_ts_quality=row.source_ts_quality,
    )


async def latest_measurement_row(
    session: AsyncSession, source_id: str
) -> SourceMeasurementRow | None:
    """Return the newest persisted ``source_measurements`` row for a source,
    or ``None`` when the source has never had one recorded.

    Public (PR-Control-3.5): the control-state endpoint needs the ROW (its
    ``id``) — not just the mapped contract — to stamp provenance
    (``measurement_id``) onto advisory-ledger entries. Read-only.
    """
    return (
        await session.execute(
            select(SourceMeasurementRow)
            .where(SourceMeasurementRow.source_id == source_id)
            .order_by(SourceMeasurementRow.measured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def build_measurement(
    session: AsyncSession, source_id: str
) -> SourceMeasurement | None:
    """Build a :class:`SourceMeasurement` for a source, preferring the latest
    persisted ``source_measurements`` row (rich C1 signals) and falling back
    to the TaskRun/TaskRunEvent-derived path only when no such row exists yet.

    Read-only. Returns ``None`` if the source has never run at all (neither a
    ``source_measurements`` row nor a TaskRun row).
    """
    latest_row = await latest_measurement_row(session, source_id)

    if latest_row is not None:
        return row_to_measurement(latest_row)

    return await _build_measurement_from_task_events(session, source_id)


async def build_trend(
    session: AsyncSession, source_id: str, *, window: int = 5
) -> SourceTrend | None:
    """Summarize the last ``window`` ``source_measurements`` rows for a source.

    Returns ``None`` when there is no persisted row at all for this source
    (nothing to trend over — a fully pre-C1 source using only the
    TaskRunEvent fallback has no trend, since that path never populates this
    table). Deterministic: same rows in -> same summary out.

    * ``zero_accepted_streak`` — how many of the most-recent rows (starting
      from the newest) have ``accepted == 0``, stopping at the first non-zero
      one. Capped at ``window``.
    * ``avg_error_rate`` — mean of ``error_rate`` across the window rows.
    * ``rate_limited_runs`` — how many of the window rows recorded a
      ``rate_limited`` entry in ``error_kinds`` (reuses the same taxonomy
      backend.control.error_kinds already assigns — no new classification).
    """
    rows = (
        await session.execute(
            select(SourceMeasurementRow)
            .where(SourceMeasurementRow.source_id == source_id)
            .order_by(SourceMeasurementRow.measured_at.desc())
            .limit(window)
        )
    ).scalars().all()

    return trend_from_rows(rows)


async def build_trend_with_fallback(
    session: AsyncSession, source_id: str, *, window: int = 5
) -> SourceTrend | None:
    """:func:`build_trend`, falling back to recent TaskRun/TaskRunEvent
    evidence when the source has NO ``source_measurements`` row at all
    (issue 06 — pre-measurement sources).

    The fallback fires only in the zero-rows case — a source with even one
    persisted row keeps its measurement-backed trend untouched (provenance
    ``measurements``). The fallback summary is computed with the exact same
    streak/avg/count math as :func:`trend_from_rows`, over per-run
    measurements derived by the same PR-Control-2 mapping
    :func:`build_measurement` already falls back to — no second run-history
    reader. Returns ``None`` when the source has never run at all.
    """
    trend = await build_trend(session, source_id, window=window)
    if trend is not None:
        return trend
    return await _build_trend_from_run_history(session, source_id, window=window)


def trend_from_rows(
    rows: list[SourceMeasurementRow],
) -> SourceTrend | None:
    """Summarize an already-fetched, newest-first row window into a
    :class:`SourceTrend`. Pure — no I/O; returns ``None`` for an empty window.

    Factored out of :func:`build_trend` (PR-Control-3.5) so the outcome pass
    (``backend.control.outcomes``) can trend over a POST-decision row window
    with the exact same math the live endpoint uses — the streak/avg/count
    semantics documented on :func:`build_trend` apply unchanged.
    """
    return _summarize_window(
        [
            (row.accepted or 0, row.error_rate or 0.0, row.error_kinds or {})
            for row in rows
        ],
        provenance=TREND_PROVENANCE_MEASUREMENTS,
    )


def _summarize_window(
    entries: list[tuple[int, float, dict[str, int]]], *, provenance: str
) -> SourceTrend | None:
    """The one streak/avg/count summary over a newest-first window of
    ``(accepted, error_rate, error_kinds)`` readings. Pure — no I/O; returns
    ``None`` for an empty window.

    Shared by :func:`trend_from_rows` (measurement-backed) and the issue-06
    run-history fallback so both provenances trend with identical math.
    """
    if not entries:
        return None

    zero_accepted_streak = 0
    for accepted, _, _ in entries:
        if accepted == 0:
            zero_accepted_streak += 1
        else:
            break

    avg_error_rate = sum(error_rate for _, error_rate, _ in entries) / len(entries)
    rate_limited_runs = sum(
        1 for _, _, error_kinds in entries if error_kinds.get("rate_limited", 0) > 0
    )

    return SourceTrend(
        window=len(entries),
        zero_accepted_streak=zero_accepted_streak,
        avg_error_rate=avg_error_rate,
        rate_limited_runs=rate_limited_runs,
        provenance=provenance,
    )


async def _recent_runs(
    session: AsyncSession, source_id: str, limit: int
) -> list[TaskRun]:
    """The source's newest-first TaskRun window (PR-Control-2 evidence).

    Latest runs for this source: TaskRun -> task_id -> CollectionTask.source_id.
    TaskRun has no direct source_id column, so we join through the task.
    """
    return list(
        (
            await session.execute(
                select(TaskRun)
                .join(CollectionTask, TaskRun.task_id == CollectionTask.id)
                .where(CollectionTask.source_id == source_id)
                .order_by(TaskRun.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    )


async def _build_measurement_from_task_events(
    session: AsyncSession, source_id: str
) -> SourceMeasurement | None:
    """PR-Control-2 fallback path: derive a measurement from TaskRun +
    TaskRunEvent evidence when no ``source_measurements`` row exists yet."""
    runs = await _recent_runs(session, source_id, 1)
    if not runs:
        # Source has never run — no sensor reading to report.
        return None
    return await _measurement_from_run(session, source_id, runs[0])


async def _build_trend_from_run_history(
    session: AsyncSession, source_id: str, *, window: int = 5
) -> SourceTrend | None:
    """Issue 06: derive a fallback trend for a pre-measurement source from its
    recent TaskRun/TaskRunEvent window — the SAME evidence and per-run mapping
    :func:`_build_measurement_from_task_events` reads, widened from the latest
    run to the last ``window`` runs. Read-only; returns ``None`` when the
    source has never run. Callers must only reach this when no
    ``source_measurements`` row exists (see :func:`build_trend_with_fallback`).
    """
    runs = await _recent_runs(session, source_id, window)
    entries: list[tuple[int, float, dict[str, int]]] = []
    for run in runs:
        m = await _measurement_from_run(session, source_id, run)
        entries.append((m.accepted, m.error_rate, m.error_kinds or {}))
    return _summarize_window(entries, provenance=TREND_PROVENANCE_RUN_HISTORY)


async def _measurement_from_run(
    session: AsyncSession, source_id: str, latest_run: TaskRun
) -> SourceMeasurement:
    """Map ONE TaskRun (+ its events) onto the measurement contract — the
    per-run body of the PR-Control-2 fallback path, factored out (issue 06) so
    the run-history trend fallback reuses this exact mapping instead of
    growing a second reader."""
    # Prefer the durable collected/stored/skipped breakdown from the run's
    # `complete` event detail. Fall back to TaskRun.records_collected (== stored)
    # for runs that never emitted one (failed runs, or a run without a run_id).
    complete_event = (
        await session.execute(
            select(TaskRunEvent)
            .where(TaskRunEvent.run_id == latest_run.id)
            .where(TaskRunEvent.step == "complete")
            .order_by(TaskRunEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    detail = (complete_event.detail if complete_event else None) or {}
    if detail:
        collected = int(detail.get("collected", 0) or 0)
        stored = int(detail.get("stored", 0) or 0)
        skipped = int(detail.get("skipped", 0) or 0)
    else:
        # No complete event (e.g. failed run): records_collected is the runner's
        # `stored`; we have no collected/skipped breakdown, so treat those as 0.
        collected = int(latest_run.records_collected or 0)
        stored = int(latest_run.records_collected or 0)
        skipped = 0

    accepted = stored
    duplicates = skipped
    # `collected` is total items fetched; anything not stored and not a duplicate
    # was dropped for validation / a permanent error. Clamp at 0 so a stale or
    # inconsistent detail can never produce a negative count.
    rejected = max(0, collected - stored - skipped)

    # fetch_latency_ms: the `collect` event records step1's elapsed_ms
    # (backend/pipeline/pipeline.py). Fall back to the run's total duration_ms,
    # then 0, when no collect event carries an elapsed value.
    fetch_latency_ms = await _collect_elapsed_ms(session, latest_run.id)
    if fetch_latency_ms is None:
        fetch_latency_ms = int(latest_run.duration_ms or 0)

    observed_at = latest_run.finished_at or latest_run.created_at or datetime.now(
        UTC
    )

    return SourceMeasurement.derive(
        source_id=source_id,
        run_id=latest_run.id,
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        fetch_latency_ms=fetch_latency_ms,
        observed_at=observed_at,
        # cursor_advanced: no boolean is persisted per-run today, and deriving it
        # precisely would require touching the pipeline/cursor path (out of scope
        # for this read-only PR). Report a conservative False; PR-Control-3+ can
        # thread a real signal once cursor advancement is recorded on the run.
        cursor_advanced=False,
        # ingest/store latency are not persisted separately today — leave None.
        ingest_latency_ms=None,
        store_latency_ms=None,
        # freshness_lag is not computed from existing tables in this PR.
        freshness_lag_seconds=None,
        # ODP metrics (stream lag / pending / DLQ) live in odp-rs's own
        # Postgres/Redis and are not reachable from this service without a
        # cross-service call, which is out of scope for PR-Control-2. Leave them
        # unpopulated (None / 0) — the contract already allows it — rather than
        # inventing values.
        odp_stream_lag=None,
        odp_pending=None,
        dlq_count=0,
    )


async def _collect_elapsed_ms(
    session: AsyncSession, run_id: str
) -> int | None:
    """Return the ``collect`` step's elapsed_ms for a run, if recorded."""
    event = (
        await session.execute(
            select(TaskRunEvent)
            .where(TaskRunEvent.run_id == run_id)
            .where(TaskRunEvent.step == "collect")
            .where(TaskRunEvent.elapsed_ms.is_not(None))
            .order_by(TaskRunEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(event.elapsed_ms) if event and event.elapsed_ms is not None else None
