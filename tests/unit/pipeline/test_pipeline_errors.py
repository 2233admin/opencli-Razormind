"""Tests for pipeline error handling branches."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.channels.base import ChannelFetchError, ChannelResult
from backend.pipeline.pipeline import run_pipeline


@pytest.mark.asyncio
async def test_pipeline_storage_error(db_session):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Storage Error Source",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    items = [{"title": "Item"}]
    channel_result = ChannelResult.ok(items)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.storer.store_records", side_effect=Exception("DB error")),
    ):
        result = await run_pipeline(db_session, source, task.id)

    assert result.success is False
    assert "DB error" in result.error


@pytest.mark.asyncio
async def test_pipeline_collect_exception(db_session):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Collect Exc Source",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    with patch("backend.pipeline.collector.collect", side_effect=RuntimeError("network down")):
        result = await run_pipeline(db_session, source, task.id)

    assert result.success is False
    assert "network down" in result.error


# ── captcha governance wiring (PR-captcha-governance) ────────────────────


@pytest.mark.asyncio
async def test_pipeline_captcha_failure_pauses_source_for_review(db_session):
    """A collect failure classified as captcha_challenge pauses the source
    (enabled=False + review_required=True) instead of failing permanently or
    retrying automatically."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Captcha Source",
        channel_type="doubao_research",
        channel_config={"question": "x"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.fail("verification challenge", error_type="captcha_challenge")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=source)
    mock_session.commit = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.database.AsyncSessionLocal", return_value=mock_session_cm),
        patch("backend.control.actuator.pause_source_for_captcha", new_callable=AsyncMock) as mock_pause,
        patch("backend.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.control_pause_ttl_seconds = 900
        result = await run_pipeline(task.id, source)

    assert result.success is False
    assert "verification challenge" in result.error
    assert result.metadata.get("captcha_paused") is True
    mock_pause.assert_awaited_once()
    assert mock_pause.await_args.kwargs["source"].id == source.id
    assert mock_pause.await_args.kwargs["ttl_seconds"] == 900
    assert source.enabled is True  # the real pause happens in the actuator via the mocked call


@pytest.mark.asyncio
async def test_pipeline_ordinary_failure_does_not_pause_source(db_session):
    """Non-captcha failures keep the existing permanent-failure path and never
    touch the actuator."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Plain Fail Source",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    channel_result = ChannelResult.fail("feed malformed", error_type="JSONDecodeError")

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.control.actuator.pause_source_for_captcha", new_callable=AsyncMock) as mock_pause,
    ):
        result = await run_pipeline(db_session, source, task.id)

    assert result.success is False
    assert "feed malformed" in result.error
    assert "captcha_paused" not in (result.metadata or {})
    mock_pause.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_with_ai_failure_still_returns_success(db_session):
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="AI Fail Source",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
        ai_config={"processor_type": "claude"},
    )
    db_session.add(source)
    await db_session.flush()

    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    items = [{"title": "Test Item"}]
    channel_result = ChannelResult.ok(items)
    mock_records = [MagicMock(ai_enrichment=None)]

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.storer.store_records", new=AsyncMock(return_value=(mock_records, 0))),
        patch("backend.database.AsyncSessionLocal", return_value=mock_session_cm),
        patch(
            "backend.pipeline.ai_processor.process_with_ai",
            side_effect=Exception("AI service down"),
        ),
    ):
        result = await run_pipeline(
            task.id, source, enable_ai=True, enable_notifications=False
        )

    # AI failure is a warning, pipeline still succeeds
    assert result.success is True


# ── runtime-hardening phase PR-B: retryable errors propagate instead of being swallowed ──────────

@pytest.mark.asyncio
async def test_pipeline_collect_retryable_exception_propagates(db_session):
    """A retryable exception type (ConnectionError) out of collect() must
    reach the celery task boundary, not get swallowed into a failed
    PipelineResult — otherwise autoretry_for never sees it."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Retryable Collect Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    with patch("backend.pipeline.collector.collect", side_effect=ConnectionError("dial tcp: refused")):
        with pytest.raises(ConnectionError, match="dial tcp"):
            await run_pipeline(db_session, source, task.id)


@pytest.mark.asyncio
async def test_pipeline_collect_permanent_exception_still_swallowed(db_session):
    """Unchanged behaviour for a permanent/unclassified exception type:
    stays a returned failed PipelineResult, no propagation."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Permanent Collect Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    with patch("backend.pipeline.collector.collect", side_effect=ValueError("bad config")):
        result = await run_pipeline(db_session, source, task.id)

    assert result.success is False
    assert "bad config" in result.error


@pytest.mark.asyncio
async def test_pipeline_collect_result_fail_retryable_error_type_raises(db_session):
    """A collect()-only channel's ChannelResult.fail(error_type="TimeoutException")
    (no live exception object — the collect()-path shape) must also propagate,
    as a ChannelFetchError, not just the raise-based fetch() path."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Retryable Result Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    failing_result = ChannelResult.fail("timed out", error_type="TimeoutException")
    with patch("backend.pipeline.collector.collect", return_value=failing_result):
        with pytest.raises(ChannelFetchError, match="timed out"):
            await run_pipeline(db_session, source, task.id)


@pytest.mark.asyncio
async def test_pipeline_sink_retryable_exception_propagates(db_session):
    """Same taxonomy applies to the step2-3/sink write path."""
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask

    source = DataSource(
        name="Retryable Sink Source", channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    items = [{"title": "Item"}]
    channel_result = ChannelResult.ok(items)

    with (
        patch("backend.pipeline.collector.collect", return_value=channel_result),
        patch("backend.pipeline.storer.store_records", side_effect=ConnectionError("pool exhausted")),
    ):
        with pytest.raises(ConnectionError, match="pool exhausted"):
            await run_pipeline(db_session, source, task.id)
