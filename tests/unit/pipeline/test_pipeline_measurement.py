"""pipeline.py wires backend.control.recorder.record_run_measurement so every
run (success or failure) leaves a truthful SourceMeasurement row — C1 (GOAL:
"先让系统诚实"). These tests drive run_pipeline end to end (real db_session,
real AsyncSessionLocal patched to it) and assert on the persisted row rather
than mocking the recorder, so a wiring regression (wrong field, wrong branch)
shows up as a real assertion failure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.channels.base import ChannelResult
from backend.models.source_measurement import SourceMeasurement
from backend.pipeline.pipeline import run_pipeline
from backend.pipeline.sinks import SinkResult


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(db_session):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Measurement Source", channel_type="rss",
        channel_config={"feed_url": "https://x/f"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()
    return source, task


async def _measurement_for(engine, run_id):
    async with _sessionmaker(engine)() as session:
        return (
            await session.execute(
                select(SourceMeasurement).where(SourceMeasurement.run_id == run_id)
            )
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_successful_run_records_measurement_with_real_counts(db_engine, db_session):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok(
            [{"title": "a", "url": "https://x/a"}, {"title": "b", "url": "https://x/b"}]
        )
        record_a, record_b = MagicMock(normalized_data={}), MagicMock(normalized_data={})
        sink = MagicMock()
        sink.write_batch = AsyncMock(
            return_value=SinkResult(
                accepted=2, duplicates=1, rejected=0, records=[record_a, record_b]
            )
        )

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-success-1", sink=sink,
            )

        assert result.success is True

        row = await _measurement_for(db_engine, "run-success-1")
        assert row is not None
        assert row.accepted == 2
        assert row.duplicates == 1
        assert row.rejected == 0
        assert row.cursor_advanced is False  # non-incremental path here: no cursor staged
        assert row.error_kinds == {}
        # No published_at on either record → honest observed_fallback, not a
        # fabricated source timestamp.
        assert row.source_ts_quality == "observed_fallback"
        assert row.newest_observed_at is not None


@pytest.mark.asyncio
async def test_failed_collect_records_measurement_with_error_kind(db_engine, db_session):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        failing = ChannelResult.fail("boom", error_type="ValueError")
        with patch("backend.pipeline.collector.collect", return_value=failing):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-fail-collect",
            )

        assert result.success is False

        row = await _measurement_for(db_engine, "run-fail-collect")
        assert row is not None
        assert row.error_kinds == {"validation": 1}
        assert row.accepted == 0


@pytest.mark.asyncio
async def test_failed_sink_records_measurement_with_rejected_count(db_engine, db_session):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok([{"title": "x"}, {"title": "y"}])
        failing_sink = MagicMock()
        failing_sink.write_batch = AsyncMock(side_effect=ValueError("bad batch"))

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-fail-sink", sink=failing_sink,
            )

        assert result.success is False

        row = await _measurement_for(db_engine, "run-fail-sink")
        assert row is not None
        assert row.rejected == 2  # channel_result.count carried through as rejected
        assert row.error_kinds == {"validation": 1}  # ValueError -> VALIDATION


@pytest.mark.asyncio
async def test_no_run_id_skips_measurement_recording(db_engine, db_session):
    """run_pipeline is also called without a run_id (e.g. some direct-call
    test paths); recording must not be attempted without one since the
    measurement's identity requires it."""
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)
        channel_result = ChannelResult.ok([{"title": "x"}])
        sink = MagicMock()
        sink.write_batch = AsyncMock(
            return_value=SinkResult(accepted=1, records=[MagicMock(normalized_data={})])
        )

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False, sink=sink,
            )

        assert result.success is True
        async with _sessionmaker(db_engine)() as session:
            count = (
                await session.execute(select(SourceMeasurement))
            ).scalars().all()
        assert count == []


@pytest.mark.asyncio
async def test_cursor_advanced_flows_from_real_commit_result(db_engine, db_session):
    """cursor_advanced on the measurement must reflect the REAL CommitResult
    from CursorStore.save(), not just 'a cursor was staged'."""
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok(
            [{"title": "x"}],
            __cursor_pending__={"etag": "v2"},
            __cursor_source_id__=source.id,
        )
        sink = MagicMock()
        sink.write_batch = AsyncMock(
            return_value=SinkResult(accepted=1, records=[MagicMock(normalized_data={})])
        )

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-cursor-1", sink=sink,
            )

        assert result.success is True
        row = await _measurement_for(db_engine, "run-cursor-1")
        assert row.cursor_advanced is True  # brand-new cursor row: a real advance


@pytest.mark.asyncio
async def test_freshness_source_quality_from_parseable_published_at(db_engine, db_session):
    """When a normalized record carries a parseable published_at (RSS's RFC-822
    'published' string, as normalizer.py already maps it), freshness quality
    is 'source', not a fabricated fallback."""
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok(
            [{"title": "x", "published": "Wed, 01 Jul 2026 00:00:00 GMT"}]
        )
        record = MagicMock(normalized_data={"published_at": "Wed, 01 Jul 2026 00:00:00 GMT"})
        sink = MagicMock()
        sink.write_batch = AsyncMock(return_value=SinkResult(accepted=1, records=[record]))

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-fresh-1", sink=sink,
            )

        assert result.success is True
        row = await _measurement_for(db_engine, "run-fresh-1")
        assert row.source_ts_quality == "source"
        assert row.newest_source_ts is not None
        assert row.freshness_lag_seconds is not None


@pytest.mark.asyncio
async def test_freshness_invalid_quality_when_unparseable(db_engine, db_session):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok([{"title": "x"}])
        record = MagicMock(normalized_data={"published_at": "not-a-real-date"})
        sink = MagicMock()
        sink.write_batch = AsyncMock(return_value=SinkResult(accepted=1, records=[record]))

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-fresh-invalid", sink=sink,
            )

        assert result.success is True
        row = await _measurement_for(db_engine, "run-fresh-invalid")
        assert row.source_ts_quality == "invalid"
        assert row.newest_source_ts is None


@pytest.mark.asyncio
async def test_freshness_missing_quality_when_zero_records(db_engine, db_session):
    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        source, task = await _seed(db_session)

        channel_result = ChannelResult.ok([])
        sink = MagicMock()
        sink.write_batch = AsyncMock(return_value=SinkResult(accepted=0, records=[]))

        with patch("backend.pipeline.collector.collect", return_value=channel_result):
            result = await run_pipeline(
                task.id, source, enable_ai=False, enable_notifications=False,
                run_id="run-fresh-missing", sink=sink,
            )

        assert result.success is True
        row = await _measurement_for(db_engine, "run-fresh-missing")
        assert row.source_ts_quality == "missing"
