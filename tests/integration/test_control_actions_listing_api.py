"""Integration tests for GET /api/v1/control/actions (issue 07 — action
history listing): filters (source_id/mode/outcome) + pagination over the
control_actions Evidence Ledger. Read-only — no test here should observe a
row mutate.
"""

from datetime import UTC, datetime

import pytest

from backend.models.control_action import ControlActionRecord


async def _seed_action(db_session, **overrides):
    kwargs = dict(
        source_id="src-1",
        run_id=None,
        measurement_id=None,
        mode="advisory",
        state="auth_failed",
        action_type="pause_source",
        reason="auth failing",
        payload={},
        executed=False,
        evaluated_at=None,
        outcome=None,
        outcome_detail=None,
        measurement_before=None,
    )
    kwargs.update(overrides)
    row = ControlActionRecord(**kwargs)
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_list_control_actions_empty(client):
    response = await client.get("/api/v1/control/actions")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"] == []
    assert data["meta"]["total"] == 0
    assert data["meta"]["page"] == 1
    assert data["meta"]["limit"] == 20


@pytest.mark.asyncio
async def test_list_control_actions_returns_rows_newest_first(client, db_session):
    # Explicit, distinct created_at: two rows seeded back-to-back can land in
    # the same datetime tick (sqlite/Python resolution), which would make
    # "newest first" ordering flaky if left to insertion timing.
    older = datetime(2026, 7, 1, tzinfo=UTC)
    newer = datetime(2026, 7, 2, tzinfo=UTC)
    await _seed_action(
        db_session, source_id="src-1", action_type="pause_source", created_at=older
    )
    await _seed_action(
        db_session, source_id="src-1", action_type="require_auth_review", created_at=newer
    )
    await db_session.commit()

    response = await client.get("/api/v1/control/actions")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 2
    # newest first: the row created second (require_auth_review) leads.
    assert rows[0]["action_type"] == "require_auth_review"
    assert rows[0]["mode"] == "advisory"
    assert rows[0]["executed"] is False
    assert rows[0]["outcome"] is None


@pytest.mark.asyncio
async def test_list_control_actions_filters_by_source_id(client, db_session):
    await _seed_action(db_session, source_id="src-a")
    await _seed_action(db_session, source_id="src-b")
    await db_session.commit()

    response = await client.get("/api/v1/control/actions?source_id=src-a")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["source_id"] == "src-a"


@pytest.mark.asyncio
async def test_list_control_actions_filters_by_mode(client, db_session):
    await _seed_action(db_session, source_id="src-1", mode="advisory")
    await _seed_action(db_session, source_id="src-1", mode="automatic", executed=True)
    await db_session.commit()

    response = await client.get("/api/v1/control/actions?mode=automatic")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["mode"] == "automatic"
    assert rows[0]["executed"] is True


@pytest.mark.asyncio
async def test_list_control_actions_filters_by_outcome_verdict(client, db_session):
    await _seed_action(
        db_session,
        source_id="src-1",
        outcome="recovered",
        evaluated_at=datetime.now(UTC),
    )
    await _seed_action(
        db_session,
        source_id="src-1",
        outcome="persisted",
        evaluated_at=datetime.now(UTC),
    )
    await db_session.commit()

    response = await client.get("/api/v1/control/actions?outcome=recovered")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "recovered"


@pytest.mark.asyncio
async def test_list_control_actions_filters_by_outcome_pending(client, db_session):
    """"pending" is not a stored outcome value — it must select rows whose
    evaluated_at is still null, matching the advisory-report's pending tally."""
    await _seed_action(db_session, source_id="src-1", outcome=None, evaluated_at=None)
    await _seed_action(
        db_session,
        source_id="src-1",
        outcome="recovered",
        evaluated_at=datetime.now(UTC),
    )
    await db_session.commit()

    response = await client.get("/api/v1/control/actions?outcome=pending")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["outcome"] is None


@pytest.mark.asyncio
async def test_list_control_actions_pagination(client, db_session):
    for i in range(5):
        await _seed_action(db_session, source_id="src-1", action_type=f"action-{i}")
    await db_session.commit()

    response = await client.get("/api/v1/control/actions?page=1&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["meta"]["total"] == 5
    assert data["meta"]["page"] == 1
    assert data["meta"]["limit"] == 2
    assert data["meta"]["pages"] == 3

    response_page2 = await client.get("/api/v1/control/actions?page=2&limit=2")
    assert len(response_page2.json()["data"]) == 2

    response_page3 = await client.get("/api/v1/control/actions?page=3&limit=2")
    assert len(response_page3.json()["data"]) == 1


@pytest.mark.asyncio
async def test_list_control_actions_combined_filters(client, db_session):
    await _seed_action(
        db_session,
        source_id="src-1",
        mode="advisory",
        outcome="persisted",
        evaluated_at=datetime.now(UTC),
    )
    await _seed_action(
        db_session,
        source_id="src-1",
        mode="automatic",
        executed=True,
        outcome="persisted",
        evaluated_at=datetime.now(UTC),
    )
    await _seed_action(db_session, source_id="src-2", mode="advisory")
    await db_session.commit()

    response = await client.get(
        "/api/v1/control/actions?source_id=src-1&mode=automatic&outcome=persisted"
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["source_id"] == "src-1"
    assert rows[0]["mode"] == "automatic"
    assert rows[0]["outcome"] == "persisted"


@pytest.mark.asyncio
async def test_list_control_actions_is_read_only(client, db_session):
    """GET must never mutate ledger rows — mirrors the control-state
    zero-mutation guarantee (docs/control-closeout-PRD.md Testing Decisions)."""
    row = await _seed_action(db_session, source_id="src-1")
    await db_session.commit()
    before = (row.outcome, row.evaluated_at, row.executed)

    response = await client.get("/api/v1/control/actions")
    assert response.status_code == 200

    await db_session.refresh(row)
    after = (row.outcome, row.evaluated_at, row.executed)
    assert before == after
