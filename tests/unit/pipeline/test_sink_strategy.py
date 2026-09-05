"""write_strategy -> sink selection. The state machine that routes the write.

Default/unknown collapses to legacy (DB-only, no ODP forward — P1-1); the
odp_* states pick DualSink/OdpSink with the right require_odp posture and no
double-send.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.pipeline.sinks import DualSink, LegacyDbSink, OdpSink, SinkResult
from backend.pipeline.sinks.strategy import select_sink


def test_legacy_does_not_forward_to_odp():
    # P1-1 strangler collapse: legacy must NOT forward to ODP just because a
    # bare ODP_INGEST_URL env var happens to be set — that was the backdoor
    # that let an unmigrated source leak into ODP, bypassing write_strategy.
    s = select_sink("legacy")
    assert isinstance(s, LegacyDbSink)
    assert s.forward_to_odp is False


def test_none_falls_back_to_legacy():
    assert isinstance(select_sink(None), LegacyDbSink)


def test_unknown_falls_back_to_legacy():
    assert isinstance(select_sink("bogus"), LegacyDbSink)


def test_odp_shadow_is_best_effort_dual():
    s = select_sink("odp_shadow")
    assert isinstance(s, DualSink)
    assert s.require_odp is False
    assert isinstance(s.legacy, LegacyDbSink)
    # No double-send: the legacy leg does not forward; OdpSink is the sole sender.
    assert s.legacy.forward_to_odp is False
    assert isinstance(s.odp, OdpSink)


def test_odp_dual_required_requires_odp():
    s = select_sink("odp_dual_required")
    assert isinstance(s, DualSink)
    assert s.require_odp is True


def test_odp_primary_requires_odp():
    s = select_sink("odp_primary")
    assert isinstance(s, DualSink)
    assert s.require_odp is True


def test_odp_only_is_forward_only():
    assert isinstance(select_sink("odp_only"), OdpSink)


@pytest.mark.asyncio
async def test_legacy_strategy_does_not_forward_even_with_env_url_set(monkeypatch):
    """End-to-end characterization of the P1-1 fix: a source on the (default)
    ``legacy`` write_strategy must not forward to ODP even when
    ODP_INGEST_URL is set in the environment — closing the bare-env-var
    backdoor that bypassed the write_strategy state machine."""
    monkeypatch.setenv("ODP_INGEST_URL", "http://odp:8040")

    store_mock = AsyncMock(return_value=([], 0))
    sess = AsyncMock()
    sess.commit = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=False)

    sink = select_sink("legacy")
    from backend.pipeline.sinks.base import RunContext

    with (
        patch("backend.pipeline.storer.store_records", new=store_mock),
        patch("backend.database.AsyncSessionLocal", return_value=cm),
    ):
        await sink.write_batch(
            RunContext(task_id="t1", source_id="s1", provider="rss"),
            [{"title": "A", "url": "https://x/a"}],
        )

    # The legacy sink still called store_records, but with forward_to_odp
    # explicitly False — ODP_INGEST_URL being set must not matter here.
    assert store_mock.call_args.kwargs["forward_to_odp"] is False


@pytest.mark.asyncio
async def test_run_pipeline_selects_sink_by_strategy(db_session):
    """Without an injected sink, run_pipeline routes through select_sink(strategy)."""
    from backend.channels.base import ChannelResult
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask
    from backend.pipeline.pipeline import run_pipeline

    source = DataSource(
        name="Strategy Source",
        channel_type="rss",
        channel_config={"feed_url": "https://x/f"},
        write_strategy="odp_only",
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    fake = MagicMock()
    fake.write_batch = AsyncMock(return_value=SinkResult(accepted=1, records=[MagicMock()]))

    with (
        patch(
            "backend.pipeline.collector.collect",
            return_value=ChannelResult.ok([{"title": "x"}]),
        ),
        patch("backend.pipeline.sinks.strategy.select_sink", return_value=fake) as sel,
    ):
        result = await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False
        )

    sel.assert_called_once_with("odp_only")
    fake.write_batch.assert_awaited_once()
    assert result.success is True


@pytest.mark.asyncio
async def test_run_pipeline_injected_sink_overrides_strategy(db_session):
    """An explicitly injected sink wins over write_strategy selection."""
    from backend.channels.base import ChannelResult
    from backend.models.source import DataSource
    from backend.models.task import CollectionTask
    from backend.pipeline.pipeline import run_pipeline

    source = DataSource(
        name="Override Source",
        channel_type="rss",
        channel_config={"feed_url": "https://x/f"},
        write_strategy="odp_only",
    )
    db_session.add(source)
    await db_session.flush()
    task = CollectionTask(source_id=source.id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    injected = MagicMock()
    injected.write_batch = AsyncMock(return_value=SinkResult(accepted=0, records=[]))

    with (
        patch(
            "backend.pipeline.collector.collect",
            return_value=ChannelResult.ok([{"title": "x"}]),
        ),
        patch("backend.pipeline.sinks.strategy.select_sink") as sel,
    ):
        await run_pipeline(
            task.id, source, enable_ai=False, enable_notifications=False, sink=injected
        )

    sel.assert_not_called()
    injected.write_batch.assert_awaited_once()
