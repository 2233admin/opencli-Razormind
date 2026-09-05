"""Integration tests for the advisory evidence ledger + report endpoints
(PR-Control-3.5): GET /sources/{id}/control-state persisting control_actions
rows, POST /control/outcomes/evaluate, and GET /control/advisory-report.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.control import kill_switch
from backend.models.control_action import ControlActionRecord


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    kill_switch.reset()
    yield
    kill_switch.reset()


async def _seed_measurement_row(session, source_id: str, **overrides):
    from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

    kwargs = dict(
        source_id=source_id,
        run_id="row-run-1",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC),
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


def _clear_odp_env(monkeypatch):
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)


@pytest.mark.asyncio
async def test_control_state_persists_ledger_rows(
    client, db_session, sample_source_data, monkeypatch
):
    _clear_odp_env(monkeypatch)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement_row(db_session, source_id, error_kinds={"auth_failed": 1})
    await db_session.commit()

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    assert response.json()["data"]["control_state"] == "auth_failed"

    rows = (
        (
            await db_session.execute(
                select(ControlActionRecord).where(ControlActionRecord.source_id == source_id)
            )
        )
        .scalars()
        .all()
    )
    action_types = {r.action_type for r in rows}
    assert action_types == {"pause_source", "require_auth_review"}
    for row in rows:
        assert row.mode == "advisory"
        assert row.executed is False
        assert row.state == "auth_failed"
        assert row.measurement_before is not None
        assert row.measurement_before["source_id"] == source_id


@pytest.mark.asyncio
async def test_control_state_polling_twice_does_not_duplicate_ledger_rows(
    client, db_session, sample_source_data, monkeypatch
):
    _clear_odp_env(monkeypatch)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement_row(db_session, source_id, error_kinds={"auth_failed": 1})
    await db_session.commit()

    first = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert first.status_code == 200
    second = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert second.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(ControlActionRecord).where(ControlActionRecord.source_id == source_id)
            )
        )
        .scalars()
        .all()
    )
    # Same unresolved auth_failed state, well inside the default dedup
    # window (600s) — polling twice must not double the ledger.
    assert len(rows) == 2  # pause_source + require_auth_review, written once


@pytest.mark.asyncio
async def test_evaluate_and_advisory_report_recovery_flow(
    client, db_session, sample_source_data, monkeypatch
):
    """End-to-end: an auth_failed suggestion is recorded, backdated past the
    min-age window, a subsequent healthy measurement is seeded, then the
    explicit evaluate endpoint + advisory-report both reflect a recovered
    bucket with a non-null recovery_rate."""
    _clear_odp_env(monkeypatch)
    monkeypatch.setenv("CONTROL_OUTCOME_MIN_AGE_SECONDS", "1")
    monkeypatch.setenv("CONTROL_OUTCOME_STALE_SECONDS", "3600")
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        create_resp = await client.post("/api/v1/sources", json=sample_source_data)
        source_id = create_resp.json()["data"]["id"]
        await _seed_measurement_row(
            db_session, source_id, error_kinds={"auth_failed": 1}
        )
        await db_session.commit()

        state_resp = await client.get(f"/api/v1/sources/{source_id}/control-state")
        assert state_resp.status_code == 200
        assert state_resp.json()["data"]["control_state"] == "auth_failed"

        # Backdate the ledger rows well past min_age_seconds so the outcome
        # pass treats them as ripe.
        rows = (
            (
                await db_session.execute(
                    select(ControlActionRecord).where(ControlActionRecord.source_id == source_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows
        backdated = datetime.now(UTC) - timedelta(seconds=120)
        for row in rows:
            row.created_at = backdated
        await db_session.commit()

        # Seed a clean post-decision measurement (measured_at after the
        # backdated created_at) so re-classification no longer yields
        # auth_failed — any different state is a "recovered" verdict.
        await _seed_measurement_row(
            db_session,
            source_id,
            run_id="row-run-post",
            measured_at=datetime.now(UTC),
            accepted=5,
            duplicates=0,
            rejected=0,
            error_rate=0.0,
            error_kinds={},
        )
        await db_session.commit()

        evaluate_resp = await client.post("/api/v1/control/outcomes/evaluate")
        assert evaluate_resp.status_code == 200
        eval_data = evaluate_resp.json()["data"]
        assert eval_data["recovered"] >= 1
        assert eval_data["evaluated"] >= 1

        report_resp = await client.get("/api/v1/control/advisory-report")
        assert report_resp.status_code == 200
        report = report_resp.json()["data"]

        assert report["totals"]["recovered"] >= 1
        assert report["totals"]["recovery_rate"] is not None
        assert report["totals"]["recovery_rate"] > 0

        auth_failed_buckets = [b for b in report["buckets"] if b["state"] == "auth_failed"]
        assert auth_failed_buckets
        for bucket in auth_failed_buckets:
            verdict_sum = (
                bucket["recovered"] + bucket["persisted"] + bucket["insufficient_data"]
            )
            assert verdict_sum == bucket["evaluated"]
            assert bucket["total"] == bucket["pending"] + bucket["evaluated"]

        assert report["mode_breakdown"].get("advisory", 0) >= 1
        # The GET endpoint itself must also lazily evaluate — running the
        # report a second time with nothing new pending should not error
        # and should keep reporting the same recovered count.
        report_resp_2 = await client.get("/api/v1/control/advisory-report")
        assert report_resp_2.status_code == 200
        assert (
            report_resp_2.json()["data"]["totals"]["recovered"]
            == report["totals"]["recovered"]
        )
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_advisory_report_empty_ledger_has_null_recovery_rate(client):
    response = await client.get("/api/v1/control/advisory-report")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["totals"]["total"] == 0
    assert data["totals"]["recovery_rate"] is None
    assert data["buckets"] == []


@pytest.mark.asyncio
async def test_outcomes_evaluate_endpoint_with_no_pending_rows(client):
    response = await client.post("/api/v1/control/outcomes/evaluate")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "evaluated": 0,
        "recovered": 0,
        "persisted": 0,
        "insufficient_data": 0,
        "still_pending": 0,
    }


# ── Kill switch (issue 03 / PR-Control-4) ────────────────────────────────


@pytest.mark.asyncio
async def test_get_kill_switch_defaults_to_config(client):
    response = await client.get("/api/v1/control/kill-switch")
    assert response.status_code == 200
    data = response.json()["data"]
    # Shipped default: config_default is False, no runtime override set yet
    # in this test process -> effective engaged state follows config.
    assert data["runtime_override"] is None
    assert data["engaged"] == data["config_default"]


@pytest.mark.asyncio
async def test_post_kill_switch_sets_runtime_override(client):
    response = await client.post("/api/v1/control/kill-switch", json={"engaged": True})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["engaged"] is True
    assert data["runtime_override"] is True

    # A subsequent GET reflects the same override.
    get_resp = await client.get("/api/v1/control/kill-switch")
    assert get_resp.json()["data"]["engaged"] is True

    # Disengage again.
    off_resp = await client.post("/api/v1/control/kill-switch", json={"engaged": False})
    assert off_resp.json()["data"]["engaged"] is False


@pytest.mark.asyncio
async def test_kill_switch_engaged_short_circuits_control_cycle(
    client, db_session, sample_source_data, monkeypatch
):
    """Integration-level proof the runtime toggle actually blocks execution:
    seed a source + enough evidence that every other gate would pass, engage
    the kill switch via the API, run one cycle tick directly, and assert
    nothing executed."""
    _clear_odp_env(monkeypatch)
    monkeypatch.setenv("CONTROL_MODE", "automatic")
    from backend.config import get_settings

    get_settings.cache_clear()
    try:
        create_resp = await client.post("/api/v1/sources", json=sample_source_data)
        source_id = create_resp.json()["data"]["id"]
        await _seed_measurement_row(db_session, source_id, error_kinds={"auth_failed": 1})

        for action_type in ("pause", "require_review"):
            for i in range(10):
                db_session.add(
                    ControlActionRecord(
                        source_id="evidence-seed",
                        run_id="run-x",
                        mode="advisory",
                        state="auth_failed",
                        action_type=action_type,
                        reason="seed",
                        payload={},
                        executed=False,
                        measurement_before={},
                        outcome="recovered" if i < 8 else "persisted",
                        evaluated_at=datetime.now(UTC),
                    )
                )
        await db_session.commit()

        toggle_resp = await client.post("/api/v1/control/kill-switch", json={"engaged": True})
        assert toggle_resp.json()["data"]["engaged"] is True

        from backend.control.cycle import run_control_cycle_once

        result = await run_control_cycle_once(db_session, now=datetime.now(UTC))
        await db_session.commit()

        assert result.executions == []
        assert all(b["blocked_by"] == "kill_switch" for b in result.blocked)
    finally:
        get_settings.cache_clear()
