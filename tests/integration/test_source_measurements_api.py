"""Integration tests for GET /api/v1/sources/{source_id}/measurements (Source
Control Room tracer bullet 1): paginated, newest-first listing over the
source_measurements table for one source. Read-only — mirrors
tests/integration/test_control_actions_listing_api.py's style for the
sibling control_actions listing.
"""

from datetime import UTC, datetime

import pytest

from backend.models.source_measurement import SourceMeasurement


async def _seed_measurement(db_session, source_id: str, **overrides):
    kwargs = dict(
        source_id=source_id,
        run_id="run-1",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC),
        accepted=1,
        duplicates=0,
        rejected=0,
        error_rate=0.0,
        duplicate_rate=0.0,
        error_kinds={},
        fetch_latency_ms=10,
        cursor_advanced=True,
        source_ts_quality="missing",
        raw={},
    )
    kwargs.update(overrides)
    row = SourceMeasurement(**kwargs)
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_list_measurements_empty_for_pre_measurement_source(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.get(f"/api/v1/sources/{source_id}/measurements")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"] == []
    assert data["meta"]["total"] == 0
    assert data["meta"]["page"] == 1
    assert data["meta"]["limit"] == 20


@pytest.mark.asyncio
async def test_list_measurements_returns_rows_newest_first(client, db_session, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    older = datetime(2026, 7, 1, tzinfo=UTC)
    newer = datetime(2026, 7, 2, tzinfo=UTC)
    await _seed_measurement(db_session, source_id, run_id="run-old", created_at=older)
    await _seed_measurement(db_session, source_id, run_id="run-new", created_at=newer)
    await db_session.commit()

    response = await client.get(f"/api/v1/sources/{source_id}/measurements")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run-new"
    assert rows[1]["run_id"] == "run-old"
    assert rows[0]["source_id"] == source_id


@pytest.mark.asyncio
async def test_list_measurements_only_returns_rows_for_this_source(
    client, db_session, sample_source_data
):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement(db_session, source_id, run_id="mine")
    await _seed_measurement(db_session, "other-source-id", run_id="not-mine")
    await db_session.commit()

    response = await client.get(f"/api/v1/sources/{source_id}/measurements")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["run_id"] == "mine"


@pytest.mark.asyncio
async def test_list_measurements_pagination(client, db_session, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    for i in range(5):
        await _seed_measurement(db_session, source_id, run_id=f"run-{i}")
    await db_session.commit()

    response = await client.get(f"/api/v1/sources/{source_id}/measurements?page=1&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["meta"]["total"] == 5
    assert data["meta"]["page"] == 1
    assert data["meta"]["limit"] == 2
    assert data["meta"]["pages"] == 3

    response_page3 = await client.get(f"/api/v1/sources/{source_id}/measurements?page=3&limit=2")
    assert len(response_page3.json()["data"]) == 1


@pytest.mark.asyncio
async def test_list_measurements_404_for_unknown_source(client):
    response = await client.get("/api/v1/sources/does-not-exist/measurements")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_measurements_response_shape(client, db_session, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement(
        db_session,
        source_id,
        accepted=3,
        duplicates=1,
        rejected=2,
        error_rate=0.33,
        duplicate_rate=0.17,
        error_kinds={"timeout": 2},
        freshness_lag_seconds=42,
        source_ts_quality="source",
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/sources/{source_id}/measurements")
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["accepted"] == 3
    assert row["duplicates"] == 1
    assert row["rejected"] == 2
    assert row["error_rate"] == 0.33
    assert row["error_kinds"] == {"timeout": 2}
    assert row["freshness_lag_seconds"] == 42
    assert row["source_ts_quality"] == "source"
    assert "id" in row
    assert "created_at" in row
    assert "updated_at" in row
