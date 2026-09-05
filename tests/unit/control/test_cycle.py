"""Unit tests for backend.control.cycle.run_control_cycle_once
(issue 03 / PR-Control-4).

The cycle-body seam: injected session + now, no asyncio wrapper. Covers the
acceptance-criteria checklist: advisory-mode zero-mutation mirror, gate-pass
execution end-to-end, Require-Review Downgrade preserving the original
suggestion, TTL pause + auto-resume + inverse ledger row, kill-switch
short-circuit, and outcome evaluation running every tick.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.control import kill_switch
from backend.control.cycle import run_control_cycle_once
from backend.models.control_action import ControlActionRecord
from backend.models.source import DataSource
from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    kill_switch.reset()
    yield
    kill_switch.reset()


def _clear_odp_env(monkeypatch):
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)


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


async def _seed_measurement_row(session, source_id: str, **overrides) -> SourceMeasurementRow:
    kwargs = dict(
        source_id=source_id,
        run_id="row-run-1",
        measured_at=NOW - timedelta(minutes=1),
        accepted=0,
        duplicates=0,
        rejected=1,
        error_rate=1.0,
        duplicate_rate=0.0,
        error_kinds={"auth_failed": 1},
        fetch_latency_ms=10,
        cursor_advanced=False,
        freshness_lag_seconds=3,
        source_ts_quality="source",
        raw={},
    )
    kwargs.update(overrides)
    row = SourceMeasurementRow(**kwargs)
    session.add(row)
    await session.flush()
    return row


async def _seed_evidence(session, *, samples, recovered, action_type, state):
    for i in range(samples):
        outcome = "recovered" if i < recovered else "persisted"
        row = ControlActionRecord(
            source_id="evidence-seed",
            run_id="run-x",
            mode="advisory",
            state=state,
            action_type=action_type,
            reason="seed",
            payload={},
            executed=False,
            measurement_before={},
            outcome=outcome,
            evaluated_at=NOW,
        )
        session.add(row)
    await session.flush()


@pytest.mark.asyncio
async def test_advisory_mode_writes_ledger_but_never_mutates_source(db_session, monkeypatch):
    """Mirror of the control-state endpoint's zero-mutation test: with
    CONTROL_MODE left at its default ("advisory"), the cycle records
    suggestions to the ledger but the DataSource row stays byte-identical."""
    _clear_odp_env(monkeypatch)
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session)
        await _seed_measurement_row(db_session, source.id, error_kinds={"auth_failed": 1})
        await db_session.commit()

        before = DataSourceSnapshot.from_source(source)

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        await db_session.refresh(source)
        after = DataSourceSnapshot.from_source(source)
        assert before == after

        rows = (
            (await db_session.execute(select(ControlActionRecord))).scalars().all()
        )
        assert len(rows) > 0
        assert all(r.mode == "advisory" for r in rows)
        assert all(r.executed is False for r in rows)
        assert result.executions == []
    finally:
        get_settings.cache_clear()


class DataSourceSnapshot:
    """Plain-value snapshot of the mutable fields the actuator could touch —
    equality comparison instead of the ORM object itself (which mutates in
    place)."""

    def __init__(self, enabled, paused_until, review_required):
        self.enabled = enabled
        self.paused_until = paused_until
        self.review_required = review_required

    @classmethod
    def from_source(cls, source: DataSource) -> "DataSourceSnapshot":
        return cls(source.enabled, source.paused_until, source.review_required)

    def __eq__(self, other):
        return (
            self.enabled == other.enabled
            and self.paused_until == other.paused_until
            and self.review_required == other.review_required
        )


@pytest.mark.asyncio
async def test_automatic_mode_executes_when_gate_passes(db_session, monkeypatch):
    _clear_odp_env(monkeypatch)
    monkeypatch.setenv("CONTROL_MODE", "automatic")
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session)
        await _seed_measurement_row(db_session, source.id, error_kinds={"auth_failed": 1})
        # Pre-load evidence for BOTH suggestions auth_failed produces
        # (policies.suggest_actions: pause_source -> pause, require_auth_review
        # -> require_review), so the gate passes on this very first tick.
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=8,
            action_type="pause",
            state="auth_failed",
        )
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=8,
            action_type="require_review",
            state="auth_failed",
        )
        await db_session.commit()

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        assert len(result.executions) == 2
        await db_session.refresh(source)
        assert source.enabled is False  # pause executed
        assert source.review_required is True  # require_review executed

        executed_rows = (
            (
                await db_session.execute(
                    select(ControlActionRecord).where(ControlActionRecord.executed.is_(True))
                )
            )
            .scalars()
            .all()
        )
        assert len(executed_rows) == 2
        assert all(r.mode == "automatic" for r in executed_rows)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_dangerous_suggestion_downgrades_and_preserves_original_in_ledger(
    db_session, monkeypatch
):
    """schema_drift suggests pause_source + require_review — both already
    whitelist-mapped. To exercise a genuine Require-Review Downgrade we seed
    a DEAD-state source (policies.suggest_actions -> require_review only)
    and monkeypatch the suggestion through the actuator's alias table
    directly is out of scope; instead we drive the downgrade path via
    actuator.execute_action's contract, verified against a source whose
    suggested action_type is not in the whitelist/alias set by constructing
    the ControlAction directly and invoking execute_action through the same
    code path the cycle uses — see test_actuator.py for the unit-level
    downgrade proof. This test proves the CYCLE preserves the original
    suggestion end-to-end when policies.suggest_actions produces an
    out-of-whitelist action_type."""
    _clear_odp_env(monkeypatch)
    monkeypatch.setenv("CONTROL_MODE", "automatic")
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session)
        await _seed_measurement_row(db_session, source.id, error_kinds={"auth_failed": 1})
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=8,
            action_type="pause",
            state="auth_failed",
        )
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=8,
            action_type="require_review",
            state="auth_failed",
        )
        await db_session.commit()

        # Monkeypatch suggest_actions used by decide_for_source (imported
        # locally inside backend.control.service) to return a genuinely
        # out-of-whitelist action_type, proving the cycle's downgrade path
        # (not just the actuator unit) preserves the original suggestion.
        import backend.control.policies as policies_module
        from backend.control.models import ControlAction

        def _fake_suggest(state, measurement, objective):
            return [
                ControlAction(
                    action_type="force_cursor_rescan",
                    source_id=measurement.source_id,
                    reason="dangerous suggestion for downgrade test",
                    payload={"cursor": "abc123"},
                )
            ]

        monkeypatch.setattr(policies_module, "suggest_actions", _fake_suggest)

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        assert len(result.executions) == 1
        exec_info = result.executions[0]
        assert exec_info["action_type"] == "require_review"
        assert exec_info["original_action_type"] == "force_cursor_rescan"
        assert exec_info["downgraded"] is True

        row = (
            await db_session.execute(
                select(ControlActionRecord).where(ControlActionRecord.executed.is_(True))
            )
        ).scalar_one()
        assert row.action_type == "require_review"
        assert row.payload["original_action_type"] == "force_cursor_rescan"
        assert row.payload["original_payload"] == {"cursor": "abc123"}
        assert row.payload["downgraded"] is True

        await db_session.refresh(source)
        assert source.review_required is True
        assert source.enabled is True  # the DOWNGRADE never pauses/rescans
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ttl_pause_auto_resumes_and_records_inverse_action(db_session, monkeypatch):
    _clear_odp_env(monkeypatch)
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session, enabled=False)
        source.paused_until = NOW - timedelta(seconds=1)  # already expired
        await db_session.commit()

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        assert source.id in result.auto_resumed
        await db_session.refresh(source)
        assert source.enabled is True
        assert source.paused_until is None

        inverse_rows = (
            (
                await db_session.execute(
                    select(ControlActionRecord)
                    .where(ControlActionRecord.source_id == source.id)
                    .where(ControlActionRecord.action_type == "resume")
                )
            )
            .scalars()
            .all()
        )
        assert len(inverse_rows) == 1
        assert inverse_rows[0].executed is True
        assert inverse_rows[0].mode == "automatic"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_kill_switch_blocks_execution_even_in_automatic_mode(db_session, monkeypatch):
    _clear_odp_env(monkeypatch)
    monkeypatch.setenv("CONTROL_MODE", "automatic")
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session)
        await _seed_measurement_row(db_session, source.id, error_kinds={"auth_failed": 1})
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=10,
            action_type="pause",
            state="auth_failed",
        )
        await _seed_evidence(
            db_session,
            samples=10,
            recovered=10,
            action_type="require_review",
            state="auth_failed",
        )
        await db_session.commit()

        kill_switch.set_override(True)

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        assert result.executions == []
        assert all(b["blocked_by"] == "kill_switch" for b in result.blocked)
        await db_session.refresh(source)
        assert source.enabled is True
        assert source.review_required is False
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_outcome_evaluation_runs_every_tick(db_session, monkeypatch):
    """The previously-lazy evaluate_pending_outcomes now runs as part of
    every cycle tick — a ripe pending row gets judged without anyone
    requesting the advisory report."""
    _clear_odp_env(monkeypatch)
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        source = await _make_source(db_session)
        row = ControlActionRecord(
            source_id=source.id,
            run_id="run-1",
            mode="advisory",
            state="auth_failed",
            action_type="pause",
            reason="ripe for judgment",
            payload={},
            executed=False,
            measurement_before={},
        )
        db_session.add(row)
        await db_session.flush()
        row.created_at = NOW - timedelta(seconds=7200)  # past default min_age
        await db_session.commit()

        # A clean post-decision measurement so re-classification differs
        # from the trigger state -> "recovered".
        await _seed_measurement_row(
            db_session,
            source.id,
            run_id="row-run-post",
            measured_at=NOW - timedelta(seconds=60),
            accepted=5,
            rejected=0,
            error_rate=0.0,
            error_kinds={},
        )
        await db_session.commit()

        result = await run_control_cycle_once(db_session, now=NOW)
        await db_session.commit()

        assert result.outcome_counts["evaluated"] >= 1
        await db_session.refresh(row)
        assert row.outcome is not None
        assert row.evaluated_at is not None
    finally:
        get_settings.cache_clear()
