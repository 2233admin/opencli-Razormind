"""Unit tests for the pipeline orchestrator."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.channels.base import ChannelResult
from backend.pipeline.pipeline import run_pipeline


def _make_source(db_session, name="Pipeline Test Source", channel_type="rss"):
    from backend.models.source import DataSource
    return DataSource(
        name=name,
        channel_type=channel_type,
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )


async def _setup_source_task(db_session, channel_type="rss"):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Pipeline Test Source",
        channel_type=channel_type,
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    return source, task


@pytest.mark.asyncio
async def test_run_pipeline_success(db_session):
    source, task = await _setup_source_task(db_session)

    mock_items = [
        {"title": "Item 1", "url": "https://ex.com/1"},
        {"title": "Item 2", "url": "https://ex.com/2"},
    ]
    channel_result = ChannelResult.ok(mock_items)
    mock_records = [MagicMock(), MagicMock()]

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=(mock_records, 0)),
        ),
    ):
        result = await run_pipeline(
            task.id,
            source,
            parameters={},
            enable_ai=False,
            enable_notifications=False,
        )

    assert result.success is True
    assert result.collected == 2
    assert result.stored == 2
    assert result.skipped == 0


@pytest.mark.asyncio
async def test_run_pipeline_channel_failure(db_session):
    source, task = await _setup_source_task(db_session)

    channel_result = ChannelResult.fail("Connection refused")

    with patch("backend.pipeline.collector.collect", return_value=channel_result):
        result = await run_pipeline(task.id, source)

    assert result.success is False
    assert result.error == "Connection refused"


@pytest.mark.asyncio
async def test_run_pipeline_empty_channel(db_session):
    source, task = await _setup_source_task(db_session)

    with patch("backend.pipeline.collector.collect", return_value=ChannelResult.ok([])):
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False
        )

    assert result.success is True
    assert result.collected == 0
    assert result.stored == 0


@pytest.mark.asyncio
async def test_run_pipeline_collector_exception(db_session):
    """Exception in collect step returns failed PipelineResult."""
    source, task = await _setup_source_task(db_session)

    with patch("backend.pipeline.collector.collect", side_effect=RuntimeError("collect crash")):
        result = await run_pipeline(task.id, source, enable_ai=False, enable_notifications=False)

    assert result.success is False
    assert "collect crash" in result.error


class _FakeScalarsResult:
    """Minimal stand-in for what `session.execute(select(...))` returns —
    supports the sync `.scalars().all()` chain the AUDIT C21 bulk-fetch uses
    (unlike a bare AsyncMock, whose auto-created child attributes are also
    async and break that chain — see the N+1 test below for the same fake)."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_run_pipeline_with_ai(db_session):
    """Pipeline with ai_config calls process_with_ai."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "AI Item", "url": "https://ex.com/ai"}]
    channel_result = ChannelResult.ok(mock_items)

    mock_record = MagicMock()
    mock_record.id = "rec-ai-1"
    mock_record.ai_enrichment = {"summary": "AI summary"}

    agent_config = {
        "processor_type": "claude",
        "model": "claude-3-haiku-20240307",
        "prompt_template": "Summarize: {{content}}",
    }

    # AUDIT C21: the persist step now does one bulk `session.execute(select(...
    # ).where(id.in_(...)))` instead of one `session.get` per record — the DB
    # row it should find and update.
    db_row = MagicMock()
    db_row.id = "rec-ai-1"

    # Mock the inner AsyncSessionLocal calls used for AI status update and enrichment save
    mock_inner_session = AsyncMock()
    mock_inner_session.get = AsyncMock(return_value=MagicMock())
    mock_inner_session.execute = AsyncMock(return_value=_FakeScalarsResult([db_row]))
    mock_inner_session.commit = AsyncMock()
    inner_cm = AsyncMock()
    inner_cm.__aenter__ = AsyncMock(return_value=mock_inner_session)
    inner_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch(
            "backend.pipeline.ai_processor.process_with_ai",
            new=AsyncMock(return_value=1),
        ),
        patch("backend.database.AsyncSessionLocal", return_value=inner_cm),
    ):
        result = await run_pipeline(
            task.id,
            source,
            agent_config=agent_config,
            enable_ai=True,
            enable_notifications=False,
        )

    assert result.success is True
    assert result.ai_processed == 1
    # same field writes as before the N+1 fix: enrichment + status landed on
    # the row the bulk SELECT found.
    assert db_row.ai_enrichment == {"summary": "AI summary"}
    assert db_row.status == "ai_processed"


@pytest.mark.asyncio
async def test_run_pipeline_ai_persist_is_bulk_not_n_plus_one(db_session):
    """AUDIT C21: persisting N enriched records must cost exactly one
    `session.execute` (bulk SELECT ... WHERE id IN (...)) + one commit, not N
    `session.get` calls — spies on how many times execute() is invoked."""
    source, task = await _setup_source_task(db_session)

    n = 5
    mock_items = [{"title": f"Item {i}", "url": f"https://ex.com/{i}"} for i in range(n)]
    channel_result = ChannelResult.ok(mock_items)

    mock_records = []
    for i in range(n):
        rec = MagicMock()
        rec.id = f"rec-{i}"
        rec.ai_enrichment = {"summary": f"s{i}"}
        mock_records.append(rec)

    db_rows = []
    for rec in mock_records:
        row = MagicMock()
        row.id = rec.id
        db_rows.append(row)

    agent_config = {"processor_type": "claude", "model": "claude-3-haiku-20240307"}

    execute_calls: list[Any] = []

    def _spy_execute(stmt):
        execute_calls.append(stmt)
        return _FakeScalarsResult(db_rows)

    mock_inner_session = AsyncMock()
    mock_inner_session.get = AsyncMock(return_value=MagicMock())  # ai_processing status row
    mock_inner_session.execute = AsyncMock(side_effect=_spy_execute)
    mock_inner_session.commit = AsyncMock()
    inner_cm = AsyncMock()
    inner_cm.__aenter__ = AsyncMock(return_value=mock_inner_session)
    inner_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=(mock_records, 0)),
        ),
        patch(
            "backend.pipeline.ai_processor.process_with_ai",
            new=AsyncMock(return_value=n),
        ),
        patch("backend.database.AsyncSessionLocal", return_value=inner_cm),
    ):
        result = await run_pipeline(
            task.id,
            source,
            agent_config=agent_config,
            enable_ai=True,
            enable_notifications=False,
        )

    assert result.success is True
    assert result.ai_processed == n

    # exactly one execute() call persists all n enrichments — not n (N+1).
    assert len(execute_calls) == 1

    # same field writes as before: every row got its enrichment + status set.
    for i, row in enumerate(db_rows):
        assert row.ai_enrichment == {"summary": f"s{i}"}
        assert row.status == "ai_processed"
    assert mock_inner_session.commit.await_count >= 1


@pytest.mark.asyncio
async def test_run_pipeline_ai_zero_enriched_warns_not_done(db_session):
    """AUDIT C3: process_with_ai returning 0 (unknown/misconfigured
    processor_type, no exception raised) must surface as a failed/skipped
    warning event, never the "AI 处理完成" success message — that used to be
    emitted unconditionally regardless of what process_with_ai actually did."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "AI Item", "url": "https://ex.com/ai"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()
    mock_record.ai_enrichment = None

    agent_config = {"processor_type": "bogus_processor", "model": "n/a"}

    mock_inner_session = AsyncMock()
    mock_inner_session.get = AsyncMock(return_value=MagicMock())
    mock_inner_session.commit = AsyncMock()
    # .add() is sync on a real Session (only .commit/.flush/.get etc. are
    # awaited) — pin it to a plain MagicMock so record_run_measurement's
    # (triggered below by run_id being set) internal session.add(row) doesn't
    # return an unawaited coroutine.
    mock_inner_session.add = MagicMock()
    inner_cm = AsyncMock()
    inner_cm.__aenter__ = AsyncMock(return_value=mock_inner_session)
    inner_cm.__aexit__ = AsyncMock(return_value=False)

    emitted: list[dict] = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        emitted.append({"step": step, "level": level, "message": message, "detail": detail})

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch("backend.pipeline.ai_processor.process_with_ai", new=AsyncMock(return_value=0)),
        patch("backend.database.AsyncSessionLocal", return_value=inner_cm),
        patch("backend.pipeline.events.emit", new=fake_emit),
    ):
        result = await run_pipeline(
            task.id,
            source,
            agent_config=agent_config,
            enable_ai=True,
            enable_notifications=False,
            run_id="run-ai-zero",
        )

    assert result.success is True
    assert result.ai_processed == 0

    ai_events = [e for e in emitted if e["step"] == "ai_process"]
    assert len(ai_events) == 1
    assert ai_events[0]["level"] == "warning"
    assert "完成" not in ai_events[0]["message"]
    assert "失败" in ai_events[0]["message"] or "跳过" in ai_events[0]["message"]


@pytest.mark.asyncio
async def test_run_pipeline_with_notifications(db_session):
    """Pipeline with new records dispatches notifications."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Notify Item", "url": "https://ex.com/n"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch(
            "backend.pipeline.notifier_dispatch.dispatch_notifications",
            new=AsyncMock(return_value={"sent": 1, "failed": 0}),
        ),
    ):
        result = await run_pipeline(
            task.id,
            source,
            enable_ai=False,
            enable_notifications=True,
        )

    assert result.success is True
    assert result.notifications_sent == 1


@pytest.mark.asyncio
async def test_run_pipeline_notifications_partial_failure_reported(db_session):
    """AUDIT C12: notifications_sent/failed must reflect the real aggregate,
    including the partial-failure case, not an unconditional success."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Notify Item", "url": "https://ex.com/n"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch(
            "backend.pipeline.notifier_dispatch.dispatch_notifications",
            new=AsyncMock(return_value={"sent": 1, "failed": 1}),
        ),
    ):
        result = await run_pipeline(
            task.id,
            source,
            enable_ai=False,
            enable_notifications=True,
        )

    assert result.success is True
    assert result.notifications_sent == 1


@pytest.mark.asyncio
async def test_run_pipeline_notifications_all_failed_warns(db_session):
    """AUDIT C12: when every attempted send failed (sent=0, failed>0), the
    emitted event and PipelineResult must say so at warning level, not the
    unconditional "通知发送完成" info-level message this replaces."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Notify Item", "url": "https://ex.com/n"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()

    emitted: list[dict] = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        emitted.append({"step": step, "level": level, "message": message, "detail": detail})

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch(
            "backend.pipeline.notifier_dispatch.dispatch_notifications",
            new=AsyncMock(return_value={"sent": 0, "failed": 2}),
        ),
        patch("backend.pipeline.events.emit", new=fake_emit),
    ):
        result = await run_pipeline(
            task.id,
            source,
            enable_ai=False,
            enable_notifications=True,
            run_id="run-notify-all-failed",
        )

    assert result.success is True
    assert result.notifications_sent == 0

    notify_events = [e for e in emitted if e["step"] == "notify"]
    assert len(notify_events) == 1
    assert notify_events[0]["level"] == "warning"
    assert "成功 0" in notify_events[0]["message"]
    assert "失败 2" in notify_events[0]["message"]


@pytest.mark.asyncio
async def test_run_pipeline_store_exception(db_session):
    """Exception in store step returns failed PipelineResult with collected count."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Item 1", "url": "https://ex.com/1"}]
    channel_result = ChannelResult.ok(mock_items)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(side_effect=RuntimeError("db error")),
        ),
    ):
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False
        )

    assert result.success is False
    assert result.collected == 1
    assert "db error" in result.error


@pytest.mark.asyncio
async def test_run_pipeline_ai_exception_continues(db_session):
    """AI processing exception is caught and pipeline still returns success."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Item", "url": "https://ex.com/x"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()
    mock_record.ai_enrichment = None

    agent_config = {"processor_type": "claude", "model": "claude-haiku"}

    # Mock the inner AsyncSessionLocal for AI status update
    mock_inner_session = AsyncMock()
    mock_inner_session.get = AsyncMock(return_value=MagicMock())
    mock_inner_session.commit = AsyncMock()
    inner_cm = AsyncMock()
    inner_cm.__aenter__ = AsyncMock(return_value=mock_inner_session)
    inner_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch(
            "backend.pipeline.ai_processor.process_with_ai",
            new=AsyncMock(side_effect=RuntimeError("ai crash")),
        ),
        patch("backend.database.AsyncSessionLocal", return_value=inner_cm),
    ):
        result = await run_pipeline(
            task.id,
            source,
            agent_config=agent_config,
            enable_ai=True,
            enable_notifications=False,
        )

    # AI failure is warned but pipeline still succeeds
    assert result.success is True
    assert result.ai_processed == 0


@pytest.mark.asyncio
async def test_run_pipeline_no_ai_config_skips_ai(db_session):
    """enable_ai=True but no ai_config logs debug and skips AI step."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Item", "url": "https://ex.com/x"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()

    mock_process_with_ai = AsyncMock()

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch("backend.pipeline.ai_processor.process_with_ai", mock_process_with_ai),
    ):
        result = await run_pipeline(
            task.id,
            source,
            agent_config=None,  # No AI config
            enable_ai=True,     # AI enabled but no config
            enable_notifications=False,
        )

    # AI should not be called
    mock_process_with_ai.assert_not_called()
    assert result.success is True
    assert result.ai_processed == 0


@pytest.mark.asyncio
async def test_run_pipeline_notification_exception_continues(db_session):
    """Exception in notification step is caught and pipeline still returns success."""
    source, task = await _setup_source_task(db_session)

    mock_items = [{"title": "Notify Item", "url": "https://ex.com/n"}]
    channel_result = ChannelResult.ok(mock_items)
    mock_record = MagicMock()

    mock_dispatch = AsyncMock(side_effect=RuntimeError("notifier crash"))
    mock_inner_session = AsyncMock()
    mock_inner_session.commit = AsyncMock()
    inner_cm = AsyncMock()
    inner_cm.__aenter__ = AsyncMock(return_value=mock_inner_session)
    inner_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch(
            "backend.pipeline.storer.store_records",
            new=AsyncMock(return_value=([mock_record], 0)),
        ),
        patch("backend.pipeline.notifier_dispatch.dispatch_notifications", mock_dispatch),
        patch("backend.database.AsyncSessionLocal", return_value=inner_cm),
    ):
        result = await run_pipeline(
            task.id,
            source,
            enable_ai=False,
            enable_notifications=True,
        )

    # Notification failure is warned but pipeline still succeeds
    assert result.success is True


@pytest.mark.asyncio
async def test_run_pipeline_opencli_auto_binding(db_session):
    """OpenCLI source auto-resolves chrome_endpoint from browser binding."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="OpenCLI Source",
        channel_type="opencli",
        channel_config={"site": "example.com", "command": "list"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    mock_binding = MagicMock()
    mock_binding.browser_endpoint = "http://chrome:9222"

    mock_channel_result = ChannelResult.ok([{"id": 1}])

    mock_browser_session = AsyncMock()
    mock_browser_session.commit = AsyncMock()
    browser_cm = AsyncMock()
    browser_cm.__aenter__ = AsyncMock(return_value=mock_browser_session)
    browser_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "backend.services.browser_service.get_binding_by_site",
            new=AsyncMock(return_value=mock_binding),
        ),
        patch("backend.database.AsyncSessionLocal", return_value=browser_cm),
        patch("backend.pipeline.collector.collect", return_value=mock_channel_result),
        patch("backend.pipeline.storer.store_records", new=AsyncMock(return_value=([], 0))),
    ):
        await run_pipeline(
            task.id,
            source,
            parameters={},
            enable_ai=False,
            enable_notifications=False,
        )


# ── C17: opencli display-string must not spawn --help on the hot path ──────

@pytest.mark.asyncio
async def test_run_pipeline_opencli_display_string_cold_cache_no_subprocess(db_session):
    """The event-log 'command' display string must not spawn an opencli
    --help subprocess on the collection hot path (C17). A cold cache falls
    back to exactly the same 'treat every arg as named' behavior a failed
    --help fetch always produced — just without ever spawning anything."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="OpenCLI Display Source",
        channel_type="opencli",
        channel_config={
            "site": "", "command": "cold-cache-observe-cmd",
            "args": {"query": "x"},
        },
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.ok([{"title": "t"}])
    captured: list[dict] = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        captured.append(detail or {})

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.storer.store_records", new=AsyncMock(return_value=([], 0))),
        patch("backend.pipeline.events.emit", new=fake_emit),
        patch("asyncio.create_subprocess_exec") as spawn,
    ):
        result = await run_pipeline(
            task.id, source, parameters={}, enable_ai=False,
            enable_notifications=False, run_id="run-cold",
        )

    assert result.success is True
    spawn.assert_not_called()
    commands = [d["command"] for d in captured if "command" in d]
    assert len(commands) == 1
    assert "--query x" in commands[0]


@pytest.mark.asyncio
async def test_run_pipeline_opencli_display_string_uses_warm_cache(db_session):
    """A warm --help cache (as populated by a real prior collection run
    through the channel itself) still produces the nicer named/positional
    split in the display string — C17 only removes the redundant spawn on
    a cache miss, it doesn't regress the warm-cache case."""
    import time

    import backend.channels.opencli_channel as oc
    from backend.channels.opencli_channel import _OPENCLI_BIN
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    site, command = "", "warm-cache-observe-cmd"
    oc._help_cache[(_OPENCLI_BIN, site, command)] = (
        time.monotonic(), frozenset({"query"})
    )

    source = DataSource(
        name="OpenCLI Display Source Warm",
        channel_type="opencli",
        channel_config={
            "site": site, "command": command,
            "args": {"query": "x"}, "positional_args": ["extra"],
        },
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.ok([{"title": "t"}])
    captured: list[dict] = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        captured.append(detail or {})

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.storer.store_records", new=AsyncMock(return_value=([], 0))),
        patch("backend.pipeline.events.emit", new=fake_emit),
        patch("asyncio.create_subprocess_exec") as spawn,
    ):
        result = await run_pipeline(
            task.id, source, parameters={}, enable_ai=False,
            enable_notifications=False, run_id="run-warm",
        )

    assert result.success is True
    spawn.assert_not_called()  # peek-only, never spawns regardless of cache state
    commands = [d["command"] for d in captured if "command" in d]
    assert len(commands) == 1
    assert "--query x" in commands[0]
    assert "extra" in commands[0]


# ── P1-7: DualSink shadow errors must surface, not vanish ──────────────────

@pytest.mark.asyncio
async def test_shadow_sink_errors_surface_but_run_still_succeeds(db_session):
    """A DualSink shadow(ODP)-write failure must be observable (an emitted
    warning event AND PipelineResult.metadata), while the run itself still
    succeeds — the legacy write landed, ODP is best-effort by design."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask
    from backend.pipeline.sinks import SinkResult

    source = DataSource(
        name="Shadow Error Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.ok([{"title": "Item", "url": "https://ex.com/1"}])
    mock_record = MagicMock()
    shadow_sink = MagicMock()
    shadow_sink.write_batch = AsyncMock(
        return_value=SinkResult(
            accepted=1,
            records=[mock_record],
            errors=["odp shadow: RuntimeError('odp down')"],
            shadow_meta={"accepted": 0, "duplicates": 0, "rejected": 0},
        )
    )

    emitted: list[dict] = []

    async def fake_emit(run_id, step, message, level="info", detail=None, elapsed_ms=None):
        emitted.append({"step": step, "level": level, "message": message, "detail": detail})

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.events.emit", new=fake_emit),
    ):
        result = await run_pipeline(
            task.id,
            source,
            enable_ai=False,
            enable_notifications=False,
            run_id="run-1",
            sink=shadow_sink,
        )

    # The run still succeeds — shadow failures are non-blocking by design.
    assert result.success is True
    assert result.stored == 1

    # Signal 1: an emitted warning event carries the shadow error.
    warning_events = [e for e in emitted if e["level"] == "warning" and e["step"] == "store"]
    assert len(warning_events) == 1
    assert "odp down" in str(warning_events[0]["detail"]["shadow_errors"])
    assert warning_events[0]["detail"]["shadow_meta"] == {
        "accepted": 0, "duplicates": 0, "rejected": 0,
    }

    # Signal 2: PipelineResult.metadata also carries it (for callers with no
    # run_id, and for runner.py to persist onto TaskRun.error_detail).
    assert "odp down" in str(result.metadata["shadow_errors"])
    assert result.metadata["shadow_meta"] == {"accepted": 0, "duplicates": 0, "rejected": 0}


@pytest.mark.asyncio
async def test_no_shadow_errors_means_no_shadow_metadata(db_session):
    """The absence-case: a sink with no errors must not synthesize any
    shadow_errors/shadow_meta noise onto the result."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask
    from backend.pipeline.sinks import SinkResult

    source = DataSource(
        name="No Shadow Error Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.ok([{"title": "Item", "url": "https://ex.com/1"}])
    clean_sink = MagicMock()
    clean_sink.write_batch = AsyncMock(
        return_value=SinkResult(accepted=1, records=[MagicMock()])
    )

    with patch("backend.pipeline.collector.collect", return_value=channel_result):
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=clean_sink,
        )

    assert result.success is True
    assert "shadow_errors" not in result.metadata

    assert result.success is True
