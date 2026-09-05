"""backend.control.recorder.record_run_measurement: persists one truthful
SourceMeasurement row per run, reusing SourceMeasurement.derive() for the rate
math. Uses the in-memory sqlite db_session fixture — source_measurements is
registered in backend/models/__init__.py so conftest's create_all provisions it.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.control.error_kinds import ErrorKind
from backend.control.recorder import FreshnessInfo, record_run_measurement
from backend.models.source_measurement import SourceMeasurement


@pytest.mark.asyncio
async def test_records_successful_run_with_real_counts(db_session):
    row = await record_run_measurement(
        db_session,
        source_id="src-1",
        run_id="run-1",
        accepted=10,
        duplicates=2,
        rejected=1,
        fetch_latency_ms=500,
        cursor_advanced=True,
    )
    await db_session.commit()

    assert row.id is not None
    assert row.error_rate == pytest.approx(1 / 13)
    assert row.duplicate_rate == pytest.approx(2 / 13)
    assert row.cursor_advanced is True
    assert row.error_kinds == {}  # no terminal error on a successful run

    fetched = (
        await db_session.execute(
            select(SourceMeasurement).where(SourceMeasurement.run_id == "run-1")
        )
    ).scalar_one()
    assert fetched.source_id == "src-1"
    assert fetched.accepted == 10


@pytest.mark.asyncio
async def test_zero_items_gives_zero_rates(db_session):
    row = await record_run_measurement(
        db_session,
        source_id="src-1",
        run_id="run-empty",
        accepted=0,
        duplicates=0,
        rejected=0,
        fetch_latency_ms=10,
        cursor_advanced=False,
    )
    assert row.error_rate == 0.0
    assert row.duplicate_rate == 0.0


@pytest.mark.asyncio
async def test_failed_run_is_evidence_too_records_error_kind(db_session):
    """A failed run must still leave a row — the point of this PR is that
    failures are observable, not silently skipped."""
    row = await record_run_measurement(
        db_session,
        source_id="src-1",
        run_id="run-fail",
        accepted=0,
        duplicates=0,
        rejected=0,
        fetch_latency_ms=100,
        cursor_advanced=False,
        error_type="TimeoutException",
    )
    assert row.error_kinds == {"timeout": 1}


@pytest.mark.asyncio
async def test_explicit_error_kind_wins_over_error_type(db_session):
    row = await record_run_measurement(
        db_session,
        source_id="src-1",
        run_id="run-fail-2",
        error_kind=ErrorKind.AUTH_FAILED,
        error_type="TimeoutException",  # should be ignored
    )
    assert row.error_kinds == {"auth_failed": 1}


@pytest.mark.asyncio
async def test_unmapped_error_type_records_unknown_not_dropped(db_session):
    row = await record_run_measurement(
        db_session,
        source_id="src-1",
        run_id="run-fail-3",
        error_type="SomeWeirdException",
    )
    assert row.error_kinds == {"unknown": 1}


@pytest.mark.asyncio
async def test_freshness_quality_missing_by_default(db_session):
    row = await record_run_measurement(
        db_session, source_id="src-1", run_id="run-2",
    )
    assert row.source_ts_quality == "missing"
    assert row.newest_source_ts is None
    assert row.freshness_lag_seconds is None


@pytest.mark.asyncio
async def test_freshness_quality_invalid_value_falls_back_to_missing(db_session):
    """A caller passing a bogus quality string (not in the fixed vocabulary)
    must not silently persist it — falls back to the honest 'missing'."""
    row = await record_run_measurement(
        db_session, source_id="src-1", run_id="run-3",
        freshness=FreshnessInfo(quality="totally_made_up"),
    )
    assert row.source_ts_quality == "missing"


@pytest.mark.asyncio
async def test_freshness_source_quality_persists_lag_and_timestamps(db_session):
    ts = datetime(2026, 7, 1, tzinfo=UTC)
    observed = datetime(2026, 7, 2, tzinfo=UTC)
    row = await record_run_measurement(
        db_session, source_id="src-1", run_id="run-4",
        freshness=FreshnessInfo(
            newest_source_ts=ts, newest_observed_at=observed,
            freshness_lag_seconds=86400, quality="source",
        ),
    )
    assert row.source_ts_quality == "source"
    assert row.newest_source_ts == ts
    assert row.newest_observed_at == observed
    assert row.freshness_lag_seconds == 86400


@pytest.mark.asyncio
async def test_raw_payload_persisted(db_session):
    row = await record_run_measurement(
        db_session, source_id="src-1", run_id="run-5",
        raw={"stage": "collect", "note": "debug context"},
    )
    assert row.raw == {"stage": "collect", "note": "debug context"}


@pytest.mark.asyncio
async def test_does_not_commit_caller_owns_transaction(db_session):
    """record_run_measurement flushes but does not commit — mirrors every
    other write in this pipeline (sinks, cursor_store, events own their own
    commit boundary; this one lets the caller batch it with other writes)."""
    await record_run_measurement(db_session, source_id="src-1", run_id="run-6")
    # Still visible within the same uncommitted session (flushed).
    fetched = (
        await db_session.execute(
            select(SourceMeasurement).where(SourceMeasurement.run_id == "run-6")
        )
    ).scalar_one_or_none()
    assert fetched is not None
