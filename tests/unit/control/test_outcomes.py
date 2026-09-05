"""Unit tests for backend.control.outcomes.evaluate_pending_outcomes
(PR-Control-3.5).

Deterministic via explicit ``now``: every test pins ``now`` and backdates
``created_at``/``measured_at`` directly rather than relying on wall-clock
timing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.control.outcomes import evaluate_pending_outcomes
from backend.models.control_action import ControlActionRecord
from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
MIN_AGE = 3600
STALE_AFTER = 86400


def _measurement_dump(**overrides) -> dict:
    base = dict(
        source_id="src-1",
        run_id="run-1",
        accepted=0,
        duplicates=0,
        rejected=1,
        fetch_latency_ms=100,
        ingest_latency_ms=None,
        store_latency_ms=None,
        error_rate=1.0,
        duplicate_rate=0.0,
        freshness_lag_seconds=None,
        cursor_advanced=False,
        odp_stream_lag=None,
        odp_pending=None,
        dlq_count=0,
        error_kinds={"auth_failed": 1},
        source_ts_quality=None,
        observed_at=NOW.isoformat(),
    )
    base.update(overrides)
    return base


async def _seed_ledger_row(session, *, created_at, state="auth_failed", action_type="pause_source"):
    row = ControlActionRecord(
        source_id="src-1",
        run_id="run-1",
        measurement_id="row-1",
        mode="advisory",
        state=state,
        action_type=action_type,
        reason="auth failing",
        payload={},
        executed=False,
        measurement_before=_measurement_dump(),
    )
    session.add(row)
    await session.flush()
    row.created_at = created_at
    await session.flush()
    return row


async def _seed_post_measurement(session, *, measured_at, **overrides):
    kwargs = dict(
        source_id="src-1",
        run_id="run-post",
        measured_at=measured_at,
        accepted=5,
        duplicates=0,
        rejected=0,
        error_rate=0.0,
        duplicate_rate=0.0,
        error_kinds={},
        fetch_latency_ms=10,
        cursor_advanced=True,
        freshness_lag_seconds=3,
        source_ts_quality="source",
        raw={},
    )
    kwargs.update(overrides)
    row = SourceMeasurementRow(**kwargs)
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_too_young_row_is_left_pending(db_session):
    await _seed_ledger_row(db_session, created_at=NOW - timedelta(seconds=MIN_AGE - 60))

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["still_pending"] == 1
    assert counts["evaluated"] == 0

    rows = (await db_session.execute(select(ControlActionRecord))).scalars().all()
    assert rows[0].outcome is None
    assert rows[0].evaluated_at is None


@pytest.mark.asyncio
async def test_ripe_row_with_no_post_measurement_and_not_stale_stays_pending(db_session):
    await _seed_ledger_row(db_session, created_at=NOW - timedelta(seconds=MIN_AGE + 60))

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["still_pending"] == 1
    assert counts["evaluated"] == 0


@pytest.mark.asyncio
async def test_ripe_row_past_stale_window_with_no_evidence_is_insufficient_data(db_session):
    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=STALE_AFTER + 60)
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["insufficient_data"] == 1
    assert counts["evaluated"] == 1
    assert counts["still_pending"] == 0

    await db_session.refresh(row)
    assert row.outcome == "insufficient_data"
    # SQLite round-trips tz-aware DateTime columns as naive (see
    # backend.control.ledger.ensure_utc) — compare naive-vs-aware safely.
    assert row.evaluated_at.replace(tzinfo=UTC) == NOW
    assert row.outcome_detail["post_measurements"] == 0


@pytest.mark.asyncio
async def test_recovered_when_post_state_differs(db_session):
    """A pause_source suggestion made while AUTH_FAILED, followed by clean
    post-decision measurements, must classify as recovered (post state !=
    trigger state).

    Re-classification omits system_context (see backend.control.outcomes
    module docstring), and a single post-decision row never has ``odp``
    sensor coverage — so the honest post state here is UNKNOWN (coverage-
    gated), not a confident HEALTHY. UNKNOWN != "auth_failed" either way, so
    the verdict is still "recovered" — this test pins that exact honest
    value rather than assuming HEALTHY.
    """
    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=MIN_AGE + 60), state="auth_failed"
    )
    await _seed_post_measurement(
        db_session,
        measured_at=NOW - timedelta(seconds=MIN_AGE // 2),
        accepted=5,
        error_kinds={},
        error_rate=0.0,
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["recovered"] == 1
    assert counts["evaluated"] == 1

    await db_session.refresh(row)
    assert row.outcome == "recovered"
    assert row.outcome_detail["post_state"] == "unknown"
    assert row.outcome_detail["post_measurements"] == 1
    assert row.evaluated_at.replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_persisted_when_post_state_matches_trigger(db_session):
    """Post-decision evidence still shows the same auth_failed problem ->
    persisted (the suggestion's premise held up)."""
    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=MIN_AGE + 60), state="auth_failed"
    )
    await _seed_post_measurement(
        db_session,
        measured_at=NOW - timedelta(seconds=MIN_AGE // 2),
        accepted=0,
        rejected=1,
        error_rate=1.0,
        error_kinds={"auth_failed": 1},
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["persisted"] == 1
    await db_session.refresh(row)
    assert row.outcome == "persisted"
    assert row.outcome_detail["post_state"] == "auth_failed"


@pytest.mark.asyncio
async def test_pre_decision_measurements_are_never_considered(db_session):
    """A source_measurements row measured BEFORE the ledger row's created_at
    must not count as post-decision evidence — otherwise a row could be
    "judged" purely from the same evidence that triggered the suggestion."""
    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=STALE_AFTER + 60), state="auth_failed"
    )
    await _seed_post_measurement(
        db_session,
        measured_at=row.created_at - timedelta(seconds=10),
        accepted=5,
        error_kinds={},
        error_rate=0.0,
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    # No POST-decision row exists, and the row is past the stale window ->
    # insufficient_data, not "recovered" from the stale pre-decision row.
    assert counts["insufficient_data"] == 1
    await db_session.refresh(row)
    assert row.outcome == "insufficient_data"


@pytest.mark.asyncio
async def test_settings_defaults_used_when_not_explicit(db_session, monkeypatch):
    """Omitting min_age_seconds/stale_after_seconds falls back to
    Settings.control_outcome_min_age_seconds / control_outcome_stale_seconds."""
    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CONTROL_OUTCOME_MIN_AGE_SECONDS", "10")
    monkeypatch.setenv("CONTROL_OUTCOME_STALE_SECONDS", "20")
    get_settings.cache_clear()
    try:
        await _seed_ledger_row(db_session, created_at=NOW - timedelta(seconds=5))

        counts = await evaluate_pending_outcomes(db_session, now=NOW)

        assert counts["still_pending"] == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_judgment_uses_resolved_per_source_objective_override(db_session):
    """A post-decision measurement that would classify as 'persisted' under
    the global default max_error_rate must classify as 'recovered' once the
    source's stored objective_override raises max_error_rate above the
    observed rate — proving evaluate_pending_outcomes resolves the source's
    override (backend.control.objectives.resolve_objective), not a bare
    SourceObjective() default.
    """
    from backend.models.source import DataSource

    source = DataSource(
        id="src-1",
        name="Source with override",
        channel_type="rss",
        channel_config={"feed_url": "https://example.com/feed.xml"},
        objective_override={"max_error_rate": 0.5},
    )
    db_session.add(source)
    await db_session.flush()

    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=MIN_AGE + 60), state="degraded"
    )
    # Post-decision error_rate = 0.3: exceeds the global default (0.05) so a
    # bare SourceObjective() would still classify DEGRADED (-> "persisted"),
    # but 0.3 is under the override's max_error_rate=0.5, so the override-
    # resolved evaluation no longer trips the error-rate rule for this
    # measurement -> the post state differs from the trigger -> "recovered".
    await _seed_post_measurement(
        db_session,
        measured_at=NOW - timedelta(seconds=MIN_AGE // 2),
        accepted=7,
        rejected=3,
        error_rate=0.3,
        error_kinds={},
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["recovered"] == 1
    assert counts["persisted"] == 0

    await db_session.refresh(row)
    assert row.outcome == "recovered"


@pytest.mark.asyncio
async def test_judgment_without_override_still_uses_default_objective(db_session):
    """Sanity companion to the override test above: with NO objective_override
    stored (or no DataSource row at all), the same 0.3 error-rate post
    measurement classifies as DEGRADED under the global default
    (max_error_rate=0.05) -> "persisted", not "recovered" — proving the
    override test's flip is genuinely explained by the override, not by some
    other change in the judgment path.
    """
    from backend.models.source import DataSource

    source = DataSource(
        id="src-1",
        name="Source without override",
        channel_type="rss",
        channel_config={"feed_url": "https://example.com/feed.xml"},
    )
    db_session.add(source)
    await db_session.flush()

    row = await _seed_ledger_row(
        db_session, created_at=NOW - timedelta(seconds=MIN_AGE + 60), state="degraded"
    )
    await _seed_post_measurement(
        db_session,
        measured_at=NOW - timedelta(seconds=MIN_AGE // 2),
        accepted=7,
        rejected=3,
        error_rate=0.3,
        error_kinds={},
    )

    counts = await evaluate_pending_outcomes(
        db_session, now=NOW, min_age_seconds=MIN_AGE, stale_after_seconds=STALE_AFTER
    )

    assert counts["persisted"] == 1
    assert counts["recovered"] == 0
    await db_session.refresh(row)
    assert row.outcome == "persisted"
