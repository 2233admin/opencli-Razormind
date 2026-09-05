"""Incremental cursor commit: the pipeline advances the persisted cursor ONLY
after the write sink accepts the batch — never during fetch, never on sink failure."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.channels.base import ChannelResult
from backend.pipeline.sinks import SinkResult


async def _seed(db_session):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Cursor Source", channel_type="rss", channel_config={"feed_url": "https://x/f"}
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()
    return source, task


def _ok_sink():
    sink = MagicMock()
    sink.write_batch = AsyncMock(return_value=SinkResult(accepted=1, records=[MagicMock()]))
    return sink


@pytest.mark.asyncio
async def test_cursor_committed_after_durable_write(db_session):
    from backend.pipeline.pipeline import run_pipeline

    source, task = await _seed(db_session)
    save_mock = AsyncMock()
    cr = ChannelResult.ok(
        [{"title": "x"}], __cursor_pending__={"etag": "v2"}, __cursor_source_id__=source.id
    )

    with (
        patch("backend.pipeline.collector.collect", return_value=cr),
        patch("backend.pipeline.cursor_store.DBCursorStore") as db_cursor_store,
    ):
        db_cursor_store.return_value.save = save_mock
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=_ok_sink()
        )

    assert result.success is True
    save_mock.assert_awaited_once_with(source.id, {"etag": "v2"})


@pytest.mark.asyncio
async def test_cursor_not_committed_when_absent(db_session):
    from backend.pipeline.pipeline import run_pipeline

    source, task = await _seed(db_session)
    save_mock = AsyncMock()
    cr = ChannelResult.ok([{"title": "x"}])  # non-incremental: no cursor_pending

    with (
        patch("backend.pipeline.collector.collect", return_value=cr),
        patch("backend.pipeline.cursor_store.DBCursorStore") as db_cursor_store,
    ):
        db_cursor_store.return_value.save = save_mock
        await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=_ok_sink()
        )

    save_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cursor_not_committed_when_sink_fails(db_session):
    from backend.pipeline.pipeline import run_pipeline

    source, task = await _seed(db_session)
    save_mock = AsyncMock()
    failing = MagicMock()
    failing.write_batch = AsyncMock(side_effect=RuntimeError("sink boom"))
    cr = ChannelResult.ok(
        [{"title": "x"}], __cursor_pending__={"etag": "v2"}, __cursor_source_id__=source.id
    )

    with (
        patch("backend.pipeline.collector.collect", return_value=cr),
        patch("backend.pipeline.cursor_store.DBCursorStore") as db_cursor_store,
    ):
        db_cursor_store.return_value.save = save_mock
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=failing
        )

    # Sink raised → cursor must NOT advance past unwritten data.
    assert result.success is False
    save_mock.assert_not_awaited()


# ── AUDIT C11: cursor save must not turn a durably-written batch into a failed run ──

@pytest.mark.asyncio
async def test_cursor_save_failure_keeps_run_successful(db_session, caplog):
    """DBCursorStore().save() used to be the only post-sink-write step with
    no error handling — if it raised, the sink had already durably committed
    this batch's records, so failing the whole run here would be a false
    failure (and Celery would re-collect the same window on retry, re-storing
    already-stored records). A save() failure must log loud (task_id + the
    cursor value that failed to persist) and keep the run successful."""
    from backend.pipeline.pipeline import run_pipeline

    source, task = await _seed(db_session)
    save_mock = AsyncMock(side_effect=RuntimeError("disk full"))
    cr = ChannelResult.ok(
        [{"title": "x"}], __cursor_pending__={"etag": "v2"}, __cursor_source_id__=source.id
    )

    with (
        patch("backend.pipeline.collector.collect", return_value=cr),
        patch("backend.pipeline.cursor_store.DBCursorStore") as db_cursor_store,
        caplog.at_level(logging.ERROR),
    ):
        db_cursor_store.return_value.save = save_mock
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=_ok_sink()
        )

    assert result.success is True
    save_mock.assert_awaited_once_with(source.id, {"etag": "v2"})

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    message = error_records[0].getMessage()
    assert task.id in message
    assert source.id in message
    assert "v2" in message


@pytest.mark.asyncio
async def test_cursor_save_failure_emits_warning_event_when_run_id_present(db_session):
    """Same failure, but with a run_id: a warning event must reach the run's
    own trace (not just a worker log line), without flipping the run to
    failed."""
    from backend.pipeline.pipeline import run_pipeline

    source, task = await _seed(db_session)
    save_mock = AsyncMock(side_effect=RuntimeError("disk full"))
    cr = ChannelResult.ok(
        [{"title": "x"}], __cursor_pending__={"etag": "v2"}, __cursor_source_id__=source.id
    )
    emitted = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        emitted.append({"run_id": run_id, "step": step, "level": level, "detail": detail})

    with (
        patch("backend.pipeline.collector.collect", return_value=cr),
        patch("backend.pipeline.cursor_store.DBCursorStore") as db_cursor_store,
        patch("backend.pipeline.events.emit", new=fake_emit),
    ):
        db_cursor_store.return_value.save = save_mock
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False,
            sink=_ok_sink(), run_id="run-cursor-fail-1",
        )

    assert result.success is True

    warnings = [e for e in emitted if e["level"] == "warning" and e["step"] == "store"]
    assert len(warnings) == 1
    assert warnings[0]["detail"]["source_id"] == source.id
    assert warnings[0]["detail"]["cursor"] == {"etag": "v2"}
