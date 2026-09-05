"""Unit tests for backend.control.gate.evaluate_gate (issue 03 / PR-Control-4).

Gate math per bucket (below-samples, below-rate, both-pass), cooldown, hourly
cap, and idempotency dedup — each tested in isolation with directly-seeded
control_actions rows, deterministic via an injected ``now``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.control import kill_switch
from backend.control.gate import evaluate_gate
from backend.models.control_action import ControlActionRecord

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    kill_switch.reset()
    yield
    kill_switch.reset()


def _measurement_dump() -> dict:
    return {
        "source_id": "src-1",
        "run_id": "run-1",
        "accepted": 0,
        "duplicates": 0,
        "rejected": 1,
        "fetch_latency_ms": 10,
        "ingest_latency_ms": None,
        "store_latency_ms": None,
        "error_rate": 1.0,
        "duplicate_rate": 0.0,
        "freshness_lag_seconds": None,
        "cursor_advanced": False,
        "odp_stream_lag": None,
        "odp_pending": None,
        "dlq_count": 0,
        "error_kinds": {"auth_failed": 1},
        "source_ts_quality": None,
        "observed_at": NOW.isoformat(),
    }


async def _seed_row(
    session,
    *,
    source_id="src-1",
    action_type="pause",
    state="auth_failed",
    outcome=None,
    evaluated_at=None,
    executed=False,
    created_at=None,
):
    row = ControlActionRecord(
        source_id=source_id,
        run_id="run-1",
        measurement_id=None,
        mode="automatic" if executed else "advisory",
        state=state,
        action_type=action_type,
        reason="test",
        payload={},
        executed=executed,
        measurement_before=_measurement_dump(),
        outcome=outcome,
        evaluated_at=evaluated_at,
    )
    session.add(row)
    await session.flush()
    if created_at is not None:
        row.created_at = created_at
        await session.flush()
    return row


async def _seed_evidence(
    session, *, samples: int, recovered: int, action_type="pause", state="auth_failed"
):
    """Seed ``samples`` judged (evaluated) rows for (state, action_type),
    ``recovered`` of which are "recovered" and the rest "persisted"."""
    for i in range(samples):
        outcome = "recovered" if i < recovered else "persisted"
        await _seed_row(
            session,
            action_type=action_type,
            state=state,
            outcome=outcome,
            evaluated_at=NOW,
        )


DEFAULT_KWARGS = dict(
    min_samples=10,
    min_recovery_rate=0.6,
    cooldown_seconds=3600,
    max_actions_per_hour=20,
)


@pytest.mark.asyncio
async def test_below_min_samples_blocks(db_session):
    await _seed_evidence(db_session, samples=5, recovered=5)  # 100% recovery, too few samples

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "below_min_samples"


@pytest.mark.asyncio
async def test_below_min_recovery_rate_blocks(db_session):
    await _seed_evidence(db_session, samples=10, recovered=3)  # 30% recovery, enough samples

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "below_min_recovery_rate"


@pytest.mark.asyncio
async def test_both_pass_allows(db_session):
    await _seed_evidence(db_session, samples=10, recovered=7)  # 70% recovery, enough samples

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is True
    assert result.blocked_by is None


@pytest.mark.asyncio
async def test_no_evidence_at_all_blocks_on_samples(db_session):
    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "below_min_samples"


@pytest.mark.asyncio
async def test_control_mode_not_automatic_blocks(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="advisory",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "control_mode_not_automatic"


@pytest.mark.asyncio
async def test_kill_switch_short_circuits_before_any_other_gate(db_session):
    # Deliberately seed enough evidence to pass every other gate, so a
    # failure here can only be explained by the kill switch itself.
    await _seed_evidence(db_session, samples=10, recovered=10)
    kill_switch.set_override(True)

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "kill_switch"


@pytest.mark.asyncio
async def test_cooldown_blocks_recent_execution(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    await _seed_row(
        db_session,
        action_type="pause",
        state="auth_failed",
        executed=True,
        created_at=NOW - timedelta(seconds=60),
    )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "cooldown_active"


@pytest.mark.asyncio
async def test_cooldown_elapsed_allows(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    await _seed_row(
        db_session,
        action_type="pause",
        state="auth_failed",
        executed=True,
        created_at=NOW - timedelta(seconds=7200),
        outcome="recovered",
        evaluated_at=NOW - timedelta(seconds=3600),
    )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_hourly_cap_exhausted_blocks(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    # 20 executed rows across different sources/action_types in the last
    # hour, at the configured cap — the NEXT candidate must be blocked
    # regardless of which source/action_type it targets.
    for i in range(20):
        await _seed_row(
            db_session,
            source_id=f"other-src-{i}",
            action_type="require_review",
            state="degraded",
            executed=True,
            created_at=NOW - timedelta(minutes=30),
            outcome="recovered",
            evaluated_at=NOW - timedelta(minutes=20),
        )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by == "hourly_cap_exhausted"


@pytest.mark.asyncio
async def test_hourly_cap_only_counts_last_hour(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    for i in range(20):
        await _seed_row(
            db_session,
            source_id=f"other-src-{i}",
            action_type="require_review",
            state="degraded",
            executed=True,
            created_at=NOW - timedelta(hours=2),
            outcome="recovered",
            evaluated_at=NOW - timedelta(hours=1, minutes=50),
        )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_idempotency_dedup_blocks_unresolved_identical_executed_row(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    # An executed row for the EXACT same (source, action_type, state) with no
    # outcome verdict yet (evaluated_at is None) — still "in flight".
    await _seed_row(
        db_session,
        action_type="pause",
        state="auth_failed",
        executed=True,
        created_at=NOW - timedelta(seconds=7200),  # past cooldown
        outcome=None,
        evaluated_at=None,
    )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is False
    assert result.blocked_by in ("cooldown_active", "unresolved_in_flight")


@pytest.mark.asyncio
async def test_idempotency_dedup_allows_once_resolved(db_session):
    await _seed_evidence(db_session, samples=10, recovered=10)
    await _seed_row(
        db_session,
        action_type="pause",
        state="auth_failed",
        executed=True,
        created_at=NOW - timedelta(seconds=7200),
        outcome="recovered",
        evaluated_at=NOW - timedelta(seconds=3700),
    )

    result = await evaluate_gate(
        db_session,
        source_id="src-1",
        action_type="pause",
        state="auth_failed",
        now=NOW,
        control_mode="automatic",
        **DEFAULT_KWARGS,
    )
    assert result.allowed is True
