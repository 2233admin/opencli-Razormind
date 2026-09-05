"""Unit tests for backend.control.ledger.record_advisory_actions (PR-Control-3.5).

Uses the in-memory sqlite db_session fixture — control_actions is registered
in backend/models/__init__.py so conftest's create_all provisions it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.control.ledger import record_advisory_actions
from backend.control.measurements import SourceMeasurement
from backend.control.models import ControlAction, SourceControlState
from backend.models.control_action import ControlActionRecord


def _measurement(**overrides) -> SourceMeasurement:
    kwargs = dict(
        source_id="src-1",
        run_id="run-1",
        accepted=0,
        duplicates=0,
        rejected=1,
        fetch_latency_ms=100,
        error_rate=1.0,
        duplicate_rate=0.0,
        cursor_advanced=False,
        error_kinds={"auth_failed": 1},
        observed_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    kwargs.update(overrides)
    return SourceMeasurement(**kwargs)


def _action(action_type: str = "pause_source") -> ControlAction:
    return ControlAction(
        action_type=action_type,
        source_id="src-1",
        reason="auth failing",
        payload={"error_kinds": {"auth_failed": 1}},
    )


@pytest.mark.asyncio
async def test_writes_advisory_rows_with_executed_false(db_session):
    written = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.AUTH_FAILED,
        actions=[_action("pause_source"), _action("require_auth_review")],
        measurement=_measurement(),
        measurement_row_id="row-1",
        run_id="run-1",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()

    assert written == 2

    rows = (
        (await db_session.execute(select(ControlActionRecord))).scalars().all()
    )
    assert len(rows) == 2
    action_types = {r.action_type for r in rows}
    assert action_types == {"pause_source", "require_auth_review"}
    for row in rows:
        assert row.mode == "advisory"
        assert row.state == "auth_failed"
        assert row.executed is False
        assert row.source_id == "src-1"
        assert row.run_id == "run-1"
        assert row.measurement_id == "row-1"
        assert row.measurement_before is not None
        assert row.measurement_before["source_id"] == "src-1"
        assert row.outcome is None
        assert row.evaluated_at is None


@pytest.mark.asyncio
async def test_dedup_skips_same_state_within_window(db_session):
    first = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.AUTH_FAILED,
        actions=[_action("pause_source")],
        measurement=_measurement(),
        measurement_row_id="row-1",
        run_id="run-1",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()
    assert first == 1

    # Same (source_id, action_type, state) re-observed immediately — well
    # inside the dedup window — must not write a second row.
    second = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.AUTH_FAILED,
        actions=[_action("pause_source")],
        measurement=_measurement(),
        measurement_row_id="row-2",
        run_id="run-2",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()
    assert second == 0

    rows = (
        (await db_session.execute(select(ControlActionRecord))).scalars().all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dedup_writes_when_state_changes(db_session):
    written = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.AUTH_FAILED,
        actions=[_action("require_review")],
        measurement=_measurement(),
        measurement_row_id="row-1",
        run_id="run-1",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()
    assert written == 1

    # Same action_type, but a DIFFERENT triggering state — a genuine change
    # in verdict is always worth a fresh row, regardless of timing.
    written_again = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.DEGRADED,
        actions=[_action("require_review")],
        measurement=_measurement(error_kinds={}, error_rate=0.5),
        measurement_row_id="row-2",
        run_id="run-2",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()
    assert written_again == 1

    rows = (
        (await db_session.execute(select(ControlActionRecord))).scalars().all()
    )
    assert len(rows) == 2
    states = {r.state for r in rows}
    assert states == {"auth_failed", "degraded"}


@pytest.mark.asyncio
async def test_dedup_writes_when_outside_window(db_session):
    stale = ControlActionRecord(
        source_id="src-1",
        run_id="run-0",
        measurement_id="row-0",
        mode="advisory",
        state="auth_failed",
        action_type="pause_source",
        reason="stale",
        payload={},
        executed=False,
        measurement_before=_measurement().model_dump(mode="json"),
    )
    db_session.add(stale)
    await db_session.flush()
    # Backdate created_at past the dedup window (TimestampMixin sets it to
    # "now" on construction — override directly, same idiom as the
    # integration tests' backdating of created_at for outcome-eval fixtures).
    stale.created_at = datetime.now(UTC) - timedelta(seconds=1200)
    await db_session.commit()

    written = await record_advisory_actions(
        db_session,
        source_id="src-1",
        state=SourceControlState.AUTH_FAILED,
        actions=[_action("pause_source")],
        measurement=_measurement(),
        measurement_row_id="row-1",
        run_id="run-1",
        mode="advisory",
        dedup_seconds=600,
    )
    await db_session.commit()
    assert written == 1

    rows = (
        (await db_session.execute(select(ControlActionRecord))).scalars().all()
    )
    assert len(rows) == 2
