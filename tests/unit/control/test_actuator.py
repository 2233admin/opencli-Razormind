"""Unit tests for backend.control.actuator (issue 03 / PR-Control-4).

Covers: resolve_action_type mapping/aliases/downgrade, increase_interval
bounded backoff on CronSchedule step expressions, pause (+TTL) and its
inverse (resume), require_review flagging, and auto_resume_expired_pauses.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.control import actuator
from backend.control.models import ControlAction
from backend.models.schedule import CronSchedule
from backend.models.source import DataSource

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


async def _make_source(session, **overrides) -> DataSource:
    source = DataSource(
        name=overrides.get("name", "Test Source"),
        channel_type=overrides.get("channel_type", "rss"),
        channel_config=overrides.get("channel_config", {"feed_url": "https://x/feed"}),
        enabled=overrides.get("enabled", True),
    )
    session.add(source)
    await session.flush()
    return source


async def _make_schedule(session, source_id: str, cron_expression: str, enabled=True) -> CronSchedule:
    sched = CronSchedule(
        source_id=source_id,
        name="test schedule",
        cron_expression=cron_expression,
        enabled=enabled,
    )
    session.add(sched)
    await session.flush()
    return sched


def _action(action_type: str, payload=None) -> ControlAction:
    return ControlAction(
        action_type=action_type,
        source_id="src-1",
        reason="test",
        payload=payload or {},
    )


# ── resolve_action_type ──────────────────────────────────────────────────

def test_whitelisted_action_types_resolve_to_themselves():
    for name in (actuator.INCREASE_INTERVAL, actuator.PAUSE, actuator.REQUIRE_REVIEW):
        resolved, downgraded = actuator.resolve_action_type(name)
        assert resolved == name
        assert downgraded is False


def test_pause_aliases_resolve_to_pause_without_downgrade():
    for alias in ("pause_source", "pause_low_priority"):
        resolved, downgraded = actuator.resolve_action_type(alias)
        assert resolved == actuator.PAUSE
        assert downgraded is False


def test_require_auth_review_alias_resolves_without_downgrade():
    resolved, downgraded = actuator.resolve_action_type("require_auth_review")
    assert resolved == actuator.REQUIRE_REVIEW
    assert downgraded is False


def test_dangerous_suggestions_downgrade_to_require_review():
    for dangerous in ("force_cursor_rescan", "switch_write_strategy"):
        resolved, downgraded = actuator.resolve_action_type(dangerous)
        assert resolved == actuator.REQUIRE_REVIEW
        assert downgraded is True


# ── increase_interval ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_increase_interval_widens_step_cron(db_session):
    source = await _make_source(db_session)
    sched = await _make_schedule(db_session, source.id, "*/5 * * * *")

    result = await actuator.execute_action(
        db_session,
        source=source,
        action=_action(actuator.INCREASE_INTERVAL),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    assert result.executed_action_type == actuator.INCREASE_INTERVAL
    assert result.downgraded is False
    await db_session.refresh(sched)
    assert sched.cron_expression == "*/10 * * * *"
    assert result.detail["widened"][0]["before"] == "*/5 * * * *"
    assert result.detail["widened"][0]["after"] == "*/10 * * * *"


@pytest.mark.asyncio
async def test_increase_interval_bounded_by_max_minutes(db_session):
    source = await _make_source(db_session)
    sched = await _make_schedule(db_session, source.id, "*/1000 * * * *")

    await actuator.execute_action(
        db_session,
        source=source,
        action=_action(actuator.INCREASE_INTERVAL),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    await db_session.refresh(sched)
    assert sched.cron_expression == "*/1440 * * * *"


@pytest.mark.asyncio
async def test_increase_interval_skips_unparseable_cron(db_session):
    source = await _make_source(db_session)
    sched = await _make_schedule(db_session, source.id, "0 9 * * *")

    result = await actuator.execute_action(
        db_session,
        source=source,
        action=_action(actuator.INCREASE_INTERVAL),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    await db_session.refresh(sched)
    assert sched.cron_expression == "0 9 * * *"  # untouched
    assert sched.id in result.detail["skipped_unparseable"]


@pytest.mark.asyncio
async def test_increase_interval_ignores_disabled_schedules(db_session):
    source = await _make_source(db_session)
    sched = await _make_schedule(db_session, source.id, "*/5 * * * *", enabled=False)

    await actuator.execute_action(
        db_session,
        source=source,
        action=_action(actuator.INCREASE_INTERVAL),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    await db_session.refresh(sched)
    assert sched.cron_expression == "*/5 * * * *"  # untouched


# ── pause / resume ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pause_disables_source_and_sets_ttl(db_session):
    source = await _make_source(db_session)

    result = await actuator.execute_action(
        db_session,
        source=source,
        action=_action("pause_source"),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    assert result.executed_action_type == actuator.PAUSE
    assert result.downgraded is False
    assert source.enabled is False
    assert source.paused_until == NOW + timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_auto_resume_expired_pauses_reenables_and_clears_ttl(db_session):
    source = await _make_source(db_session, enabled=False)
    source.paused_until = NOW - timedelta(seconds=1)
    await db_session.flush()

    resumed = await actuator.auto_resume_expired_pauses(db_session, now=NOW)

    assert len(resumed) == 1
    resumed_source, inverse = resumed[0]
    assert resumed_source.id == source.id
    assert resumed_source.enabled is True
    assert resumed_source.paused_until is None
    assert inverse.executed_action_type == "resume"


@pytest.mark.asyncio
async def test_auto_resume_ignores_not_yet_expired_pauses(db_session):
    source = await _make_source(db_session, enabled=False)
    source.paused_until = NOW + timedelta(seconds=3600)
    await db_session.flush()

    resumed = await actuator.auto_resume_expired_pauses(db_session, now=NOW)

    assert resumed == []
    assert source.enabled is False


@pytest.mark.asyncio
async def test_auto_resume_ignores_sources_never_paused(db_session):
    await _make_source(db_session, enabled=True)

    resumed = await actuator.auto_resume_expired_pauses(db_session, now=NOW)

    assert resumed == []


# ── require_review + downgrade ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_require_review_sets_flag(db_session):
    source = await _make_source(db_session)

    result = await actuator.execute_action(
        db_session,
        source=source,
        action=_action("require_review"),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    assert result.executed_action_type == actuator.REQUIRE_REVIEW
    assert result.downgraded is False
    assert source.review_required is True


@pytest.mark.asyncio
async def test_dangerous_suggestion_downgrades_and_preserves_original(db_session):
    source = await _make_source(db_session)

    result = await actuator.execute_action(
        db_session,
        source=source,
        action=_action("force_cursor_rescan", payload={"cursor": "abc"}),
        now=NOW,
        increase_interval_factor=2.0,
        increase_interval_max_minutes=1440,
        pause_ttl_seconds=3600,
    )

    assert result.executed_action_type == actuator.REQUIRE_REVIEW
    assert result.downgraded is True
    assert result.original_action_type == "force_cursor_rescan"
    assert result.original_payload == {"cursor": "abc"}
    assert source.review_required is True
    # Source was NOT paused or otherwise mutated beyond the review flag —
    # the downgrade never performs the originally-suggested action.
    assert source.enabled is True


# ── pause_source_for_captcha (captcha governance, PR-captcha-governance) ──


@pytest.mark.asyncio
async def test_pause_source_for_captcha_pauses_and_flags_review(db_session):
    source = await _make_source(db_session)

    detail = await actuator.pause_source_for_captcha(
        db_session, source=source, now=NOW, ttl_seconds=900
    )

    assert source.enabled is False
    assert source.paused_until == NOW + timedelta(seconds=900)
    assert source.review_required is True
    assert detail["paused_until"] == source.paused_until.isoformat()
    assert detail["review_required"] is True
    assert detail["was_enabled"] is True


@pytest.mark.asyncio
async def test_pause_source_for_captcha_refreshes_ttl_when_already_paused(db_session):
    source = await _make_source(db_session, enabled=False)
    source.paused_until = NOW - timedelta(seconds=1)
    source.review_required = True
    await db_session.flush()

    detail = await actuator.pause_source_for_captcha(
        db_session, source=source, now=NOW, ttl_seconds=1800
    )

    assert source.paused_until == NOW + timedelta(seconds=1800)
    assert source.review_required is True
    assert detail["was_enabled"] is False
