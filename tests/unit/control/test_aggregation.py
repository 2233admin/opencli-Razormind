"""Unit tests for backend.control.aggregation.build_measurement (read-only).

Uses the in-memory sqlite db_session fixture from tests/conftest.py. These tests
construct DataSource / CollectionTask / TaskRun / TaskRunEvent rows directly and
assert the aggregated SourceMeasurement — no pipeline/runner is invoked.

PR-Control-3 adds: build_measurement now PREFERS the latest persisted
``source_measurements`` row (rich C1 signals) over the TaskRunEvent-derived
fallback, and a new ``build_trend`` rolling-window query.
"""

from datetime import UTC, datetime

import pytest

from backend.control.aggregation import (
    build_measurement,
    build_trend,
    build_trend_with_fallback,
)
from backend.models.source import DataSource
from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow
from backend.models.task import CollectionTask, TaskRun, TaskRunEvent


async def _make_source(session, **overrides) -> DataSource:
    source = DataSource(
        name=overrides.get("name", "Test Source"),
        channel_type=overrides.get("channel_type", "rss"),
        channel_config=overrides.get("channel_config", {"feed_url": "https://x/feed"}),
        enabled=True,
        tags=[],
    )
    session.add(source)
    await session.flush()
    return source


async def _make_task(session, source_id: str) -> CollectionTask:
    task = CollectionTask(source_id=source_id, trigger_type="manual", parameters={})
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_returns_none_when_source_never_ran(db_session):
    source = await _make_source(db_session)
    # A task with no runs still counts as "never ran".
    await _make_task(db_session, source.id)
    await db_session.flush()

    result = await build_measurement(db_session, source.id)
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_for_unknown_source(db_session):
    result = await build_measurement(db_session, "does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_completed_run_with_complete_event(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    run = TaskRun(
        task_id=task.id,
        status="completed",
        started_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 2, 10, 1, tzinfo=UTC),
        duration_ms=60_000,
        records_collected=8,  # == stored
    )
    db_session.add(run)
    await db_session.flush()

    # collect event carries step1 elapsed_ms
    db_session.add(
        TaskRunEvent(run_id=run.id, level="info", step="collect", message="done", elapsed_ms=250)
    )
    # complete event carries the durable breakdown
    db_session.add(
        TaskRunEvent(
            run_id=run.id,
            level="info",
            step="complete",
            message="done",
            detail={"duration_ms": 60_000, "collected": 10, "stored": 8, "skipped": 1},
        )
    )
    await db_session.flush()

    m = await build_measurement(db_session, source.id)
    assert m is not None
    assert m.source_id == source.id
    assert m.run_id == run.id
    # accepted=stored, duplicates=skipped, rejected=collected-stored-skipped
    assert m.accepted == 8
    assert m.duplicates == 1
    assert m.rejected == 1  # 10 - 8 - 1
    assert m.fetch_latency_ms == 250  # from collect event elapsed_ms
    # derived rates: total_seen = 8 + 1 + 1 = 10
    assert m.error_rate == pytest.approx(1 / 10)
    assert m.duplicate_rate == pytest.approx(1 / 10)
    # PR-Control-2 leaves these unpopulated
    assert m.odp_stream_lag is None
    assert m.odp_pending is None
    assert m.dlq_count == 0
    assert m.cursor_advanced is False
    assert m.observed_at == run.finished_at


@pytest.mark.asyncio
async def test_failed_run_without_complete_event_still_returns_measurement(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    run = TaskRun(
        task_id=task.id,
        status="failed",
        started_at=datetime(2026, 7, 2, 11, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 2, 11, 0, 5, tzinfo=UTC),
        duration_ms=5_000,
        records_collected=0,
        error_message="boom",
    )
    db_session.add(run)
    await db_session.flush()

    m = await build_measurement(db_session, source.id)
    assert m is not None  # a failed run is still evidence
    assert m.run_id == run.id
    assert m.accepted == 0
    assert m.duplicates == 0
    assert m.rejected == 0
    # no collect event -> falls back to run.duration_ms for fetch latency
    assert m.fetch_latency_ms == 5_000
    assert m.error_rate == 0.0
    assert m.duplicate_rate == 0.0


@pytest.mark.asyncio
async def test_picks_most_recent_run(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    old = TaskRun(
        task_id=task.id,
        status="completed",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        finished_at=datetime(2026, 7, 1, tzinfo=UTC),
        duration_ms=1000,
        records_collected=1,
    )
    db_session.add(old)
    await db_session.flush()
    db_session.add(
        TaskRunEvent(
            run_id=old.id, level="info", step="complete", message="old",
            detail={"collected": 1, "stored": 1, "skipped": 0},
        )
    )

    new = TaskRun(
        task_id=task.id,
        status="completed",
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        finished_at=datetime(2026, 7, 2, tzinfo=UTC),
        duration_ms=2000,
        records_collected=5,
    )
    db_session.add(new)
    await db_session.flush()
    db_session.add(
        TaskRunEvent(
            run_id=new.id, level="info", step="complete", message="new",
            detail={"collected": 7, "stored": 5, "skipped": 2},
        )
    )
    await db_session.flush()

    m = await build_measurement(db_session, source.id)
    assert m is not None
    assert m.run_id == new.id
    assert m.accepted == 5
    assert m.duplicates == 2


# ---------------------------------------------------------------------------
# PR-Control-3: prefer the persisted source_measurements row
# ---------------------------------------------------------------------------


async def _make_measurement_row(session, source_id: str, **overrides) -> SourceMeasurementRow:
    kwargs = dict(
        source_id=source_id,
        run_id=overrides.pop("run_id", "run-row-1"),
        measured_at=datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
        accepted=5,
        duplicates=1,
        rejected=0,
        error_rate=0.0,
        duplicate_rate=1 / 6,
        error_kinds={},
        fetch_latency_ms=42,
        ingest_latency_ms=None,
        store_latency_ms=None,
        cursor_advanced=True,
        freshness_lag_seconds=10,
        source_ts_quality="source",
        raw={},
    )
    kwargs.update(overrides)
    row = SourceMeasurementRow(**kwargs)
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_build_measurement_prefers_source_measurements_row_over_task_events(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    # A completed TaskRun/TaskRunEvent exists too — build_measurement must
    # prefer the richer persisted row instead.
    run = TaskRun(
        task_id=task.id,
        status="completed",
        finished_at=datetime(2026, 7, 1, tzinfo=UTC),
        duration_ms=1000,
        records_collected=1,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        TaskRunEvent(
            run_id=run.id, level="info", step="complete", message="done",
            detail={"collected": 1, "stored": 1, "skipped": 0},
        )
    )
    await db_session.flush()

    await _make_measurement_row(
        db_session, source.id, run_id="row-run-1", accepted=99, error_kinds={"rate_limited": 1},
    )

    m = await build_measurement(db_session, source.id)
    assert m is not None
    assert m.run_id == "row-run-1"
    assert m.accepted == 99  # from the row, not the TaskRunEvent (1)
    assert m.error_kinds == {"rate_limited": 1}
    assert m.source_ts_quality == "source"
    assert m.cursor_advanced is True


@pytest.mark.asyncio
async def test_build_measurement_falls_back_to_task_events_when_no_row(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)
    run = TaskRun(
        task_id=task.id,
        status="completed",
        finished_at=datetime(2026, 7, 1, tzinfo=UTC),
        duration_ms=1000,
        records_collected=4,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        TaskRunEvent(
            run_id=run.id, level="info", step="complete", message="done",
            detail={"collected": 4, "stored": 4, "skipped": 0},
        )
    )
    await db_session.flush()

    m = await build_measurement(db_session, source.id)
    assert m is not None
    assert m.run_id == run.id
    assert m.accepted == 4
    # fallback path never sets these — distinguishes it from a real row
    assert m.source_ts_quality is None
    assert m.error_kinds == {}


@pytest.mark.asyncio
async def test_build_measurement_picks_latest_row_by_measured_at(db_session):
    source = await _make_source(db_session)
    await _make_measurement_row(
        db_session, source.id, run_id="old-row",
        measured_at=datetime(2026, 7, 1, tzinfo=UTC), accepted=1,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="new-row",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC), accepted=9,
    )

    m = await build_measurement(db_session, source.id)
    assert m is not None
    assert m.run_id == "new-row"
    assert m.accepted == 9


# ---------------------------------------------------------------------------
# PR-Control-3: build_trend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_trend_none_when_no_rows(db_session):
    source = await _make_source(db_session)
    trend = await build_trend(db_session, source.id)
    assert trend is None


@pytest.mark.asyncio
async def test_build_trend_zero_accepted_streak_from_newest(db_session):
    source = await _make_source(db_session)
    # oldest -> newest: accepted=5 (not zero), then two zero-accepted rows.
    await _make_measurement_row(
        db_session, source.id, run_id="r1",
        measured_at=datetime(2026, 7, 1, tzinfo=UTC), accepted=5,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r2",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC), accepted=0,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r3",
        measured_at=datetime(2026, 7, 3, tzinfo=UTC), accepted=0,
    )

    trend = await build_trend(db_session, source.id, window=5)
    assert trend is not None
    assert trend.window == 3
    assert trend.zero_accepted_streak == 2  # stops at the first non-zero (r1)


@pytest.mark.asyncio
async def test_build_trend_streak_stops_at_first_nonzero_going_backwards(db_session):
    source = await _make_source(db_session)
    # newest is zero, then a non-zero, then zero again further back — streak
    # must stop counting at the first non-zero encountered from the newest.
    await _make_measurement_row(
        db_session, source.id, run_id="r1",
        measured_at=datetime(2026, 7, 1, tzinfo=UTC), accepted=0,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r2",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC), accepted=3,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r3",
        measured_at=datetime(2026, 7, 3, tzinfo=UTC), accepted=0,
    )

    trend = await build_trend(db_session, source.id, window=5)
    assert trend is not None
    assert trend.zero_accepted_streak == 1  # only r3 (newest); r2 breaks it


@pytest.mark.asyncio
async def test_build_trend_avg_error_rate(db_session):
    source = await _make_source(db_session)
    await _make_measurement_row(
        db_session, source.id, run_id="r1",
        measured_at=datetime(2026, 7, 1, tzinfo=UTC), error_rate=0.2,
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r2",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC), error_rate=0.4,
    )

    trend = await build_trend(db_session, source.id, window=5)
    assert trend is not None
    assert trend.avg_error_rate == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_build_trend_rate_limited_runs_count(db_session):
    source = await _make_source(db_session)
    await _make_measurement_row(
        db_session, source.id, run_id="r1",
        measured_at=datetime(2026, 7, 1, tzinfo=UTC), error_kinds={"rate_limited": 1},
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r2",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC), error_kinds={},
    )
    await _make_measurement_row(
        db_session, source.id, run_id="r3",
        measured_at=datetime(2026, 7, 3, tzinfo=UTC), error_kinds={"rate_limited": 1},
    )

    trend = await build_trend(db_session, source.id, window=5)
    assert trend is not None
    assert trend.rate_limited_runs == 2


@pytest.mark.asyncio
async def test_build_trend_respects_window_size(db_session):
    source = await _make_source(db_session)
    for i in range(10):
        await _make_measurement_row(
            db_session, source.id, run_id=f"r{i}",
            measured_at=datetime(2026, 7, 1 + i, tzinfo=UTC), accepted=1,
        )

    trend = await build_trend(db_session, source.id, window=5)
    assert trend is not None
    assert trend.window == 5


# ---------------------------------------------------------------------------
# Issue 06: build_trend_with_fallback — run-history trend for pre-measurement
# sources (zero source_measurements rows), same math, honest provenance.
# ---------------------------------------------------------------------------


async def _make_run_with_complete(
    session,
    task_id: str,
    *,
    created_at: datetime,
    collected: int,
    stored: int,
    skipped: int = 0,
) -> TaskRun:
    run = TaskRun(
        task_id=task_id,
        status="completed",
        created_at=created_at,
        finished_at=created_at,
        duration_ms=1000,
        records_collected=stored,
    )
    session.add(run)
    await session.flush()
    session.add(
        TaskRunEvent(
            run_id=run.id, level="info", step="complete", message="done",
            detail={"collected": collected, "stored": stored, "skipped": skipped},
        )
    )
    await session.flush()
    return run


@pytest.mark.asyncio
async def test_fallback_trend_none_when_source_never_ran(db_session):
    source = await _make_source(db_session)
    await _make_task(db_session, source.id)

    trend = await build_trend_with_fallback(db_session, source.id)
    assert trend is None


@pytest.mark.asyncio
async def test_fallback_trend_derived_from_run_history_when_no_rows(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    # oldest -> newest: a healthy run, then two zero-accepted runs with errors.
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        collected=10, stored=8, skipped=2,  # accepted=8, error_rate=0.0
    )
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        collected=10, stored=0, skipped=0,  # accepted=0, error_rate=1.0
    )
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
        collected=10, stored=0, skipped=5,  # accepted=0, error_rate=0.5
    )

    trend = await build_trend_with_fallback(db_session, source.id, window=5)
    assert trend is not None
    assert trend.provenance == "run_history"
    assert trend.window == 3
    # streak counts newest-first, stopping at the first accepting run.
    assert trend.zero_accepted_streak == 2
    assert trend.avg_error_rate == pytest.approx((0.0 + 1.0 + 0.5) / 3)
    # the TaskRunEvent fallback path has no error-kind taxonomy — never
    # fabricated, so rate_limited_runs stays 0 in the fallback trend.
    assert trend.rate_limited_runs == 0


@pytest.mark.asyncio
async def test_fallback_trend_streak_stops_at_first_nonzero_run(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        collected=3, stored=0,  # zero-accepted, but older than the accepting run
    )
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        collected=3, stored=3,
    )
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
        collected=3, stored=0,
    )

    trend = await build_trend_with_fallback(db_session, source.id, window=5)
    assert trend is not None
    assert trend.zero_accepted_streak == 1  # newest only; 7/2 run breaks it


@pytest.mark.asyncio
async def test_fallback_trend_respects_window_size(db_session):
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)
    for i in range(7):
        await _make_run_with_complete(
            db_session, task.id,
            created_at=datetime(2026, 7, 1 + i, tzinfo=UTC),
            collected=1, stored=1,
        )

    trend = await build_trend_with_fallback(db_session, source.id, window=5)
    assert trend is not None
    assert trend.window == 5
    assert trend.provenance == "run_history"


@pytest.mark.asyncio
async def test_fallback_trend_not_used_when_measurement_rows_exist(db_session):
    """Even one source_measurements row keeps the trend measurement-backed —
    run history present alongside must NOT leak into the summary."""
    source = await _make_source(db_session)
    task = await _make_task(db_session, source.id)

    # Run history that would trend very differently (zero accepted).
    await _make_run_with_complete(
        db_session, task.id,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        collected=10, stored=0,
    )
    # One real measurement row: accepted=5 (from _make_measurement_row defaults).
    await _make_measurement_row(db_session, source.id, run_id="row-1")

    trend = await build_trend_with_fallback(db_session, source.id, window=5)
    assert trend is not None
    assert trend.provenance == "measurements"
    assert trend.window == 1
    assert trend.zero_accepted_streak == 0  # from the row (accepted=5), not runs


@pytest.mark.asyncio
async def test_measurement_backed_build_trend_reports_measurements_provenance(db_session):
    source = await _make_source(db_session)
    await _make_measurement_row(db_session, source.id, run_id="row-1")

    trend = await build_trend(db_session, source.id)
    assert trend is not None
    assert trend.provenance == "measurements"
