"""Unit tests for notifier_dispatch."""

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_dispatch_empty_records(db_session):
    await dispatch_notifications(db_session, "src-1", [], "on_new_record")
    # Should return without doing anything


@pytest.mark.asyncio
async def test_dispatch_no_matching_rules(db_session):
    record = MagicMock()
    record.id = "rec-1"
    record.normalized_data = {"title": "Test"}
    record.ai_enrichment = None

    # No rules in DB - should succeed silently
    await dispatch_notifications(db_session, "src-1", [record], "on_new_record")


@pytest.mark.asyncio
async def test_dispatch_with_matching_rule(db_session):
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Notif Source",
        channel_type="rss",
        channel_config={"feed_url": "https://ex.com/feed"},
    )
    db_session.add(source)
    await db_session.flush()

    rule = NotificationRule(
        name="Test Rule",
        trigger_event="on_new_record",
        notifier_type="webhook",
        notifier_config={"url": "https://hooks.ex.com"},
        enabled=True,
    )
    db_session.add(rule)
    await db_session.flush()

    record = MagicMock()
    record.id = "rec-1"
    record.normalized_data = {"title": "Test"}
    record.ai_enrichment = None

    mock_notifier = AsyncMock()
    mock_notifier.send = AsyncMock(return_value=True)

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
    ):
        outcome = await dispatch_notifications(db_session, source.id, [record], "on_new_record")

    mock_notifier.send.assert_awaited_once()
    assert outcome == {"sent": 1, "failed": 0}

    from backend.models.notification import NotificationLog

    logs = (await db_session.execute(select(NotificationLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "sent"


# ── trigger_event producers: on_ai_processed / on_task_failed ──────────────


@pytest.mark.asyncio
async def test_dispatch_on_ai_processed_matches_event_scoped_rule(db_session):
    """A rule with trigger_event='on_ai_processed' fires only when dispatch is
    called with that event — and an on_new_record rule does NOT fire for it."""
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="AI Source", channel_type="rss", channel_config={"feed_url": "https://ex.com/feed"}
    )
    db_session.add(source)
    await db_session.flush()

    db_session.add_all([
        NotificationRule(
            name="AI rule", trigger_event="on_ai_processed",
            notifier_type="webhook", notifier_config={"url": "https://hooks.ex.com/ai"},
            enabled=True,
        ),
        NotificationRule(
            name="New rule", trigger_event="on_new_record",
            notifier_type="webhook", notifier_config={"url": "https://hooks.ex.com/new"},
            enabled=True,
        ),
    ])
    await db_session.flush()

    record = MagicMock()
    record.id = "rec-ai-1"
    record.normalized_data = {"title": "Enriched"}
    record.ai_enrichment = {"summary": "s"}

    mock_notifier = AsyncMock()
    mock_notifier.send = AsyncMock(return_value=True)

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
    ):
        outcome = await dispatch_notifications(
            db_session, source.id, [record], "on_ai_processed"
        )

    assert outcome == {"sent": 1, "failed": 0}
    sent_payload = mock_notifier.send.await_args.args[1]
    assert sent_payload.event == "on_ai_processed"


@pytest.mark.asyncio
async def test_dispatch_on_task_failed_with_synthetic_payload(db_session):
    """on_task_failed fires with NO collected records via failure_payload — the
    synthetic record carries error/error_type into the notification payload."""
    from backend.models.notification import NotificationRule
    from backend.models.source import DataSource

    source = DataSource(
        name="Fail Source", channel_type="rss", channel_config={"feed_url": "https://ex.com/f"}
    )
    db_session.add(source)
    await db_session.flush()

    db_session.add(
        NotificationRule(
            name="Fail rule", trigger_event="on_task_failed",
            notifier_type="webhook", notifier_config={"url": "https://hooks.ex.com/fail"},
            enabled=True,
        )
    )
    await db_session.flush()

    mock_notifier = AsyncMock()
    mock_notifier.send = AsyncMock(return_value=True)

    with (
        patch("backend.pipeline.notifier_dispatch.get_notifier", return_value=mock_notifier),
        patch("backend.database.AsyncSessionLocal", return_value=_session_cm(db_session)),
    ):
        outcome = await dispatch_notifications(
            db_session, source.id, [],
            "on_task_failed",
            failure_payload={
                "error": "opencli doubao ask exited with code 1: captcha",
                "error_type": "captcha_challenge",
                "task_id": "task-9",
            },
        )

    assert outcome == {"sent": 1, "failed": 0}
    sent_payload = mock_notifier.send.await_args.args[1]
    assert sent_payload.event == "on_task_failed"
    assert "captcha" in sent_payload.data["error"]
    assert sent_payload.data["error_type"] == "captcha_challenge"
