"""Tests for error paths in notifier_dispatch."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from backend.pipeline.notifier_dispatch import dispatch_notifications


def _session_cm(session):
    """Wrap an already-open (test-fixture) AsyncSession in the async context
    manager shape ``backend.database.AsyncSessionLocal()`` normally returns,
    so phase C (a fresh internally-opened session) transparently reuses the
    real ``db_session`` fixture instead of hitting the module-level
    production engine. Same pattern as ``tests/unit/pipeline/test_ai_processor.py``."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_dispatch_unknown_notifier_type_skipped(db_session, caplog):
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Src",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(source)
    await db_session.flush()

    rule = NotificationRule(
        name="Unknown Notifier",
        trigger_event="on_new_record",
        notifier_type="nonexistent_notifier",
        notifier_config={},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    record = AsyncMock()
    record.id = "rec-1"
    record.normalized_data = {"title": "Test"}
    record.ai_enrichment = None

    # Should silently skip unknown notifier (ValueError from get_notifier),
    # but (AUDIT C18) it must no longer be silent in the logs.
    with caplog.at_level(logging.WARNING):
        outcome = await dispatch_notifications(db_session, source.id, [record], "on_new_record")

    assert outcome == {"sent": 0, "failed": 0}
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(rule.id in msg and "nonexistent_notifier" in msg for msg in warnings)


@pytest.mark.asyncio
async def test_dispatch_notifier_send_exception_logged(db_session):
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Exc Src",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(source)
    await db_session.flush()

    rule = NotificationRule(
        name="Failing Rule",
        trigger_event="on_new_record",
        notifier_type="webhook",
        notifier_config={"url": "https://hooks.ex.com"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    record = AsyncMock()
    record.id = "rec-2"
    record.normalized_data = {}
    record.ai_enrichment = None

    mock_notifier = AsyncMock()
    mock_notifier.send = AsyncMock(side_effect=Exception("connection refused"))

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
    ):
        # Should catch exception and log as failed, not raise
        outcome = await dispatch_notifications(db_session, source.id, [record], "on_new_record")

    assert outcome == {"sent": 0, "failed": 1}


@pytest.mark.asyncio
async def test_dispatch_aggregate_counts_partial_failure(db_session):
    """One rule, two records: one send succeeds, one fails — the aggregate
    must reflect both, not just report unconditional success (AUDIT C12)."""
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Partial Fail Src",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(source)
    await db_session.flush()

    rule = NotificationRule(
        name="Partial Rule",
        trigger_event="on_new_record",
        notifier_type="webhook",
        notifier_config={"url": "https://hooks.ex.com"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    record_ok = AsyncMock()
    record_ok.id = "rec-ok"
    record_ok.normalized_data = {}
    record_ok.ai_enrichment = None

    record_bad = AsyncMock()
    record_bad.id = "rec-bad"
    record_bad.normalized_data = {}
    record_bad.ai_enrichment = None

    mock_notifier = AsyncMock()

    async def _send(config, payload):
        if payload.record_id == "rec-bad":
            raise Exception("timeout")
        return True

    mock_notifier.send = AsyncMock(side_effect=_send)

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
    ):
        outcome = await dispatch_notifications(
            db_session, source.id, [record_ok, record_bad], "on_new_record"
        )

    assert outcome == {"sent": 1, "failed": 1}


@pytest.mark.asyncio
async def test_dispatch_pending_rows_committed_before_sends_start(db_session):
    """Structural regression test for AUDIT C1/C23: the NotificationLog rows
    must already be committed as "pending" (phase A) BEFORE any network send
    (phase B) begins — i.e. no write transaction/lock is held across the
    send. Asserted via call order: session.commit() must fire before
    notifier.send()."""
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Order Src",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(source)
    await db_session.flush()

    rule = NotificationRule(
        name="Order Rule",
        trigger_event="on_new_record",
        notifier_type="webhook",
        notifier_config={"url": "https://hooks.ex.com"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    record = AsyncMock()
    record.id = "rec-order"
    record.normalized_data = {}
    record.ai_enrichment = None

    call_order: list[str] = []
    original_commit = db_session.commit

    async def _tracked_commit(*args, **kwargs):
        call_order.append("commit")
        return await original_commit(*args, **kwargs)

    mock_notifier = AsyncMock()

    async def _send(config, payload):
        call_order.append("send")
        return True

    mock_notifier.send = AsyncMock(side_effect=_send)

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
        patch.object(db_session, "commit", side_effect=_tracked_commit),
    ):
        await dispatch_notifications(db_session, source.id, [record], "on_new_record")

    # Phase A's commit (the caller-session pending-rows commit) must precede
    # the send — proving the row was durably "pending" with no open write
    # transaction before the network call started.
    assert "commit" in call_order
    assert "send" in call_order
    assert call_order.index("commit") < call_order.index("send")
