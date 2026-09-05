"""Integration tests for the /api/v1/sources endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.auth import crypto

_KEY = Fernet.generate_key().decode()


def _sessionmaker(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_list_sources_empty(client):
    response = await client.get("/api/v1/sources")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_create_source(client, sample_source_data):
    response = await client.post("/api/v1/sources", json=sample_source_data)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["name"] == sample_source_data["name"]
    assert data["data"]["channel_type"] == "rss"
    assert "id" in data["data"]


@pytest.mark.asyncio
async def test_create_browser_act_source(client):
    response = await client.post(
        "/api/v1/sources",
        json={
            "name": "BrowserAct source",
            "channel_type": "browser_act",
            "channel_config": {"pack_id": "lead-generation/example"},
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["channel_type"] == "browser_act"


@pytest.mark.asyncio
async def test_get_source(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.get(f"/api/v1/sources/{source_id}")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == source_id


@pytest.mark.asyncio
async def test_get_source_not_found(client):
    response = await client.get("/api/v1/sources/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_source(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.patch(
        f"/api/v1/sources/{source_id}",
        json={"name": "Updated Name", "enabled": False},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "Updated Name"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_delete_source(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    delete_resp = await client.delete(f"/api/v1/sources/{source_id}")
    assert delete_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/sources/{source_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_sources_pagination(client, sample_source_data):
    # Create 3 sources
    for i in range(3):
        data = {**sample_source_data, "name": f"Source {i}"}
        await client.post("/api/v1/sources", json=data)

    response = await client.get("/api/v1/sources?page=1&limit=2")
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["meta"]["total"] == 3
    assert body["meta"]["pages"] == 2


@pytest.mark.asyncio
async def test_test_source_connectivity(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.post(f"/api/v1/sources/{source_id}/test")
    assert response.status_code == 200
    data = response.json()
    assert "connected" in data["data"]


# ── credentials: AuthManager reads/writes its own session (backend.database.
# AsyncSessionLocal), separate from the client fixture's injected get_db — point
# it at the same in-memory db_engine so a credential actually lands where the
# source lookup (via get_db) can see it. ──────────────────────────────────────

@pytest.mark.asyncio
async def test_store_and_list_source_credential(client, db_engine, sample_source_data, monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, _KEY)
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        store_resp = await client.post(
            f"/api/v1/sources/{source_id}/credentials",
            json={"key_name": "token", "secret": "s3cr3t"},
        )
        assert store_resp.status_code == 201
        assert store_resp.json()["success"] is True

        list_resp = await client.get(f"/api/v1/sources/{source_id}/credentials")
    assert list_resp.status_code == 200
    keys = [k["key_name"] for k in list_resp.json()["data"]]
    assert keys == ["token"]
    # The secret itself never appears in a response body.
    assert "s3cr3t" not in list_resp.text


@pytest.mark.asyncio
async def test_store_credential_key_name_too_long_rejected(client, sample_source_data):
    """key_name must fit the DB column (String(64)) — a value that passes
    Pydantic but doesn't fit the column would otherwise reach Postgres as an
    unhandled DataError (SQLite doesn't enforce VARCHAR length, so this only
    manifested in production before the max_length was aligned to 64)."""
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.post(
        f"/api/v1/sources/{source_id}/credentials",
        json={"key_name": "x" * 65, "secret": "s"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_store_credential_source_not_found(client):
    response = await client.post(
        "/api/v1/sources/nonexistent-id/credentials",
        json={"key_name": "token", "secret": "x"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_source_cascades_credentials(
    client, db_engine, sample_source_data, monkeypatch
):
    """Deleting a source must not orphan its encrypted credentials — there's no
    DB-level FK/cascade (AuthManager writes via a separate session), so
    delete_source() cleans up source_credentials itself."""
    from backend.auth.manager import AuthManager

    monkeypatch.setenv(crypto.ENV_KEY, _KEY)
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        await client.post(
            f"/api/v1/sources/{source_id}/credentials",
            json={"key_name": "token", "secret": "s3cr3t"},
        )

        delete_resp = await client.delete(f"/api/v1/sources/{source_id}")
        assert delete_resp.status_code == 200

        assert await AuthManager().resolve(source_id) == {}


@pytest.mark.asyncio
async def test_delete_source_credential(client, db_engine, sample_source_data, monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, _KEY)
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    with patch("backend.database.AsyncSessionLocal", _sessionmaker(db_engine)):
        await client.post(
            f"/api/v1/sources/{source_id}/credentials",
            json={"key_name": "token", "secret": "s3cr3t"},
        )
        delete_resp = await client.delete(f"/api/v1/sources/{source_id}/credentials/token")
        assert delete_resp.status_code == 200

        list_resp = await client.get(f"/api/v1/sources/{source_id}/credentials")
    assert list_resp.json()["data"] == []


# ── RSS onboarding: discover-feed + import-opml ─────────────────────────────────
@pytest.mark.asyncio
async def test_discover_feed_endpoint(client):
    with patch(
        "backend.api.v1.sources.source_service.discover_feeds",
        AsyncMock(return_value=[{"url": "https://example.com/feed.xml", "title": "Feed"}]),
    ):
        response = await client.post(
            "/api/v1/sources/discover-feed",
            json={"url": "https://example.com"},
        )
    assert response.status_code == 200
    assert response.json()["data"] == [{"url": "https://example.com/feed.xml", "title": "Feed"}]


@pytest.mark.asyncio
async def test_import_opml_endpoint_creates_disabled_sources(client):
    opml = b"""<?xml version="1.0"?><opml><body>
    <outline title="Feed A" xmlUrl="https://a.example.com/rss" />
    </body></opml>"""
    response = await client.post(
        "/api/v1/sources/import-opml",
        files={"file": ("feeds.opml", opml, "text/x-opml")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["created"]) == 1
    assert data["created"][0]["channel_type"] == "rss"
    assert data["created"][0]["enabled"] is False
    assert data["skipped_existing"] == []


@pytest.mark.asyncio
async def test_import_opml_endpoint_invalid_xml_returns_400(client):
    response = await client.post(
        "/api/v1/sources/import-opml",
        files={"file": ("feeds.opml", b"<not-xml", "text/x-opml")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_import_opml_url_endpoint_imports_github_catalog_with_groups(client):
    catalog_url = "https://raw.githubusercontent.com/example/catalog/main/business.opml"
    opml = """<?xml version="1.0"?><opml><body>
    <outline text="Business &amp; Economy">
      <outline title="Markets" xmlUrl="https://markets.example.com/rss" />
    </outline>
    </body></opml>"""
    with patch(
        "backend.api.v1.sources.source_service.fetch_remote_opml",
        AsyncMock(return_value=opml),
    ):
        response = await client.post(
            "/api/v1/sources/import-opml-url",
            json={"url": catalog_url},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["created"]) == 1
    assert data["created"][0]["enabled"] is False
    assert data["created"][0]["channel_config"] == {
        "feed_url": "https://markets.example.com/rss",
        "source_group": "business-economy",
        "catalog_url": catalog_url,
    }
    assert data["created"][0]["tags"] == [
        "rss",
        "source-group:business-economy",
        "catalog:opml",
    ]


@pytest.mark.asyncio
async def test_import_opml_url_endpoint_rejects_fetch_failure(client):
    with patch(
        "backend.api.v1.sources.source_service.fetch_remote_opml",
        AsyncMock(side_effect=ValueError("unable to fetch OPML catalog")),
    ):
        response = await client.post(
            "/api/v1/sources/import-opml-url",
            json={"url": "https://example.com/broken.opml"},
        )
    assert response.status_code == 400


# ── control-state (PR-Control-2): read-only sensor readings + derived state ────
@pytest.mark.asyncio
async def test_control_state_not_found(client):
    response = await client.get("/api/v1/sources/nonexistent-id/control-state")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_control_state_source_never_ran(client, sample_source_data, monkeypatch):
    # No ODP_* env vars configured in this test session by default -> the ODP
    # collector degrades every section to unavailable without any real I/O
    # (see backend.control.collectors.odp_metrics's early-return guards).
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source_id"] == source_id
    assert data["measurement"] is None
    assert data["control_state"] is None
    # C0: no measurement -> no coverage/confidence to compute either.
    assert data["confidence"] is None
    assert data["sensor_coverage"] is None
    assert data["missing_signals"] == []
    assert data["trend"] is None
    # objective defaults are always present
    assert data["objective"]["max_error_rate"] == 0.05
    assert data["objective"]["max_pending"] == 1000
    # PINNED CONTRACT: system_context/suggested_actions/control_mode are
    # always present, even when the source has never run.
    assert data["system_context"]["available"] is False
    assert data["system_context"]["odp_backpressured"] is False
    assert data["suggested_actions"] == []
    assert data["control_mode"] == "advisory"


@pytest.mark.asyncio
async def test_control_state_with_run_evidence(client, db_session, sample_source_data, monkeypatch):
    """A clean completed run yields a populated measurement — but C0 (Control
    Room v0) means it must NOT render as a confident HEALTHY: this test seeds
    only TaskRun/TaskRunEvent (the pre-C1 fallback path, no source_measurements
    row), so odp/error_kinds/cursor coverage is missing -> confidence "low" ->
    control_state is UNKNOWN, not healthy.

    The client fixture and db_session share the same injected session, so
    evidence inserted here is visible to the endpoint's aggregation query.
    """
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    from datetime import UTC, datetime

    from backend.models.task import CollectionTask, TaskRun, TaskRunEvent

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    task = CollectionTask(source_id=source_id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    run = TaskRun(
        task_id=task.id,
        status="completed",
        finished_at=datetime(2026, 7, 2, tzinfo=UTC),
        duration_ms=1000,
        records_collected=9,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        TaskRunEvent(
            run_id=run.id, level="info", step="complete", message="done",
            detail={"collected": 10, "stored": 9, "skipped": 1},
        )
    )
    await db_session.flush()

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["measurement"] is not None
    assert data["measurement"]["accepted"] == 9
    assert data["measurement"]["duplicates"] == 1
    assert data["measurement"]["rejected"] == 0  # 10 - 9 - 1
    # error_rate = 0/10 = 0.0 -> would be HEALTHY pre-C0, but odp + error_kinds
    # coverage is missing today -> gated to UNKNOWN, never a fake "healthy".
    assert data["control_state"] == "unknown"
    assert data["confidence"] == "low"
    assert data["sensor_coverage"] == {
        "run": True,
        "cursor": False,
        "freshness": False,
        "error_kinds": False,
        "odp": False,
    }
    assert set(data["missing_signals"]) == {"cursor", "freshness", "error_kinds", "odp"}
    # UNKNOWN has no first-version policy -> no suggestions.
    assert data["suggested_actions"] == []
    assert data["control_mode"] == "advisory"
    # Issue 06: no source_measurements row, but run history exists -> the
    # trend falls back to task-run evidence and says so via provenance.
    assert data["trend"] == {
        "window": 1,
        "zero_accepted_streak": 0,
        "avg_error_rate": 0.0,
        "rate_limited_runs": 0,
        "provenance": "run_history",
    }


@pytest.mark.asyncio
async def test_control_state_degraded_when_errors(
    client, db_session, sample_source_data, monkeypatch
):
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    from datetime import UTC, datetime

    from backend.models.task import CollectionTask, TaskRun, TaskRunEvent

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    task = CollectionTask(source_id=source_id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    run = TaskRun(
        task_id=task.id,
        status="completed",
        finished_at=datetime(2026, 7, 2, tzinfo=UTC),
        duration_ms=1000,
        records_collected=1,
    )
    db_session.add(run)
    await db_session.flush()
    # collected=10, stored=1, skipped=0 -> rejected=9 -> error_rate=9/10=0.9 > 0.05
    db_session.add(
        TaskRunEvent(
            run_id=run.id, level="info", step="complete", message="done",
            detail={"collected": 10, "stored": 1, "skipped": 0},
        )
    )
    await db_session.flush()

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["measurement"]["rejected"] == 9
    # DEGRADED is positive evidence (an observed error_rate over setpoint) —
    # C0's honesty gate only remaps HEALTHY, never downgrades a real problem.
    assert data["control_state"] == "degraded"
    assert data["confidence"] == "low"
    assert "odp" in data["missing_signals"]
    # DEGRADED has a first-version policy: require_review, advisory only.
    assert len(data["suggested_actions"]) == 1
    assert data["suggested_actions"][0]["action_type"] == "require_review"
    assert data["suggested_actions"][0]["reason"]


@pytest.mark.asyncio
async def test_control_state_trend_fallback_for_pre_measurement_source(
    client, db_session, sample_source_data, monkeypatch
):
    """Issue 06: a source with ZERO source_measurements rows but real task-run
    history gets a rolling trend derived from that history, explicitly marked
    provenance="run_history" — and the fallback trend must NOT upgrade
    coverage/confidence (no fake HEALTHY: the state stays gated to UNKNOWN
    because odp/error_kinds coverage is still missing)."""
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    from datetime import UTC, datetime

    from backend.models.task import CollectionTask, TaskRun, TaskRunEvent

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    task = CollectionTask(source_id=source_id, trigger_type="manual", parameters={})
    db_session.add(task)
    await db_session.flush()

    # oldest -> newest: one accepting run, then two clean-but-empty runs.
    for day, (collected, stored) in enumerate([(10, 10), (0, 0), (0, 0)], start=1):
        run = TaskRun(
            task_id=task.id,
            status="completed",
            created_at=datetime(2026, 7, day, tzinfo=UTC),
            finished_at=datetime(2026, 7, day, tzinfo=UTC),
            duration_ms=1000,
            records_collected=stored,
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(
            TaskRunEvent(
                run_id=run.id, level="info", step="complete", message="done",
                detail={"collected": collected, "stored": stored, "skipped": 0},
            )
        )
    await db_session.flush()

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]

    # The fallback trend exists, with honest run-history provenance and the
    # same streak/avg/count semantics as the measurement-backed path.
    assert data["trend"] == {
        "window": 3,
        "zero_accepted_streak": 2,
        "avg_error_rate": 0.0,
        "rate_limited_runs": 0,
        "provenance": "run_history",
    }
    # Coverage/confidence math is untouched by the fallback trend: the
    # TaskRunEvent path still misses odp/error_kinds/cursor/freshness, so
    # confidence stays "low" and the would-be HEALTHY stays gated to UNKNOWN.
    assert data["measurement"] is not None
    assert data["confidence"] == "low"
    assert data["sensor_coverage"]["error_kinds"] is False
    assert data["sensor_coverage"]["odp"] is False
    assert data["control_state"] == "unknown"


# ---------------------------------------------------------------------------
# PR-Control-3: pinned contract, rich source_measurements row, advisory-only
# guarantee, ODP-unavailable degrade
# ---------------------------------------------------------------------------


async def _seed_measurement_row(session, source_id: str, **overrides):
    from datetime import UTC, datetime

    from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

    kwargs = dict(
        source_id=source_id,
        run_id="row-run-1",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC),
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
async def test_control_state_pinned_contract_shape(
    client, db_session, sample_source_data, monkeypatch
):
    """Assert the exact top-level JSON shape the frontend agent builds
    against in parallel — every pinned key must be present."""
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement_row(db_session, source_id, error_kinds={"rate_limited": 1})

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]

    assert set(data.keys()) == {
        "source_id",
        "control_state",
        "confidence",
        "sensor_coverage",
        "missing_signals",
        "measurement",
        "objective",
        "trend",
        "system_context",
        "suggested_actions",
        "control_mode",
    }
    assert data["source_id"] == source_id
    assert data["control_state"] == "rate_limited"
    assert data["trend"] == {
        "window": 1,
        "zero_accepted_streak": 0,
        "avg_error_rate": 0.0,
        "rate_limited_runs": 1,
    }
    assert data["system_context"] == {
        "odp_backpressured": False,
        "stream_lag": None,
        "pending": None,
        "available": False,
    }
    assert data["suggested_actions"] == [
        {
            "action_type": "increase_interval",
            "reason": data["suggested_actions"][0]["reason"],
            "payload": {"error_rate": 0.0},
        }
    ]
    assert data["control_mode"] == "advisory"


@pytest.mark.asyncio
async def test_control_state_reads_rich_signals_from_source_measurements_row(
    client, db_session, sample_source_data, monkeypatch
):
    """The aggregation bridge must prefer the persisted source_measurements
    row (rich C1 signals) over the TaskRunEvent fallback."""
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement_row(
        db_session, source_id, error_kinds={"auth_failed": 1}, source_ts_quality="observed_fallback"
    )

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    data = response.json()["data"]
    assert data["measurement"]["error_kinds"] == {"auth_failed": 1}
    assert data["measurement"]["source_ts_quality"] == "observed_fallback"
    assert data["control_state"] == "auth_failed"
    # C0 coverage: a real row makes cursor/error_kinds genuinely observed.
    assert data["sensor_coverage"]["cursor"] is True
    assert data["sensor_coverage"]["error_kinds"] is True
    action_types = {a["action_type"] for a in data["suggested_actions"]}
    assert action_types == {"pause_source", "require_auth_review"}


@pytest.mark.asyncio
async def test_control_state_odp_unavailable_degrades_gracefully(
    client, db_session, sample_source_data, monkeypatch
):
    """Even when the ODP collector itself raises unexpectedly, the endpoint
    must still return 200 with system_context.available=False — never a 500."""
    monkeypatch.setenv("ODP_REDIS_URL", "redis://unreachable-host-for-test:6399/0")
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["system_context"]["available"] is False
    assert data["system_context"]["odp_backpressured"] is False


@pytest.mark.asyncio
async def test_control_state_odp_collector_exception_never_500s(
    client, sample_source_data, monkeypatch
):
    """A hard crash inside the ODP collector import/call path must degrade to
    unavailable, not bubble into a 500 — see _build_system_context's
    last-resort except clause."""
    from backend.control.collectors import odp_metrics

    async def _raise_collect(*args, **kwargs):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(odp_metrics, "collect", _raise_collect)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["system_context"]["available"] is False


@pytest.mark.asyncio
async def test_control_state_blocked_by_odp_when_backpressured(
    client, db_session, sample_source_data, monkeypatch
):
    from backend.control.collectors import odp_metrics

    async def _fake_collect():
        from datetime import UTC, datetime

        return odp_metrics.OdpMetricsSnapshot(
            stream=odp_metrics.StreamState(
                available=True, name="odp.ingest.raw", group="odp-store", lag=10, pending=5000,
            ),
            dlq=odp_metrics.DlqState(available=True, total=0, last_24h=0),
            ingest=odp_metrics.IngestHealthState(available=True, healthy=True),
            collected_at=datetime(2026, 7, 2, tzinfo=UTC),
        )

    monkeypatch.setattr(odp_metrics, "collect", _fake_collect)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    await _seed_measurement_row(db_session, source_id)

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    data = response.json()["data"]
    assert data["system_context"]["odp_backpressured"] is True
    assert data["system_context"]["pending"] == 5000
    assert data["control_state"] == "blocked_by_odp"
    assert data["suggested_actions"][0]["action_type"] == "pause_low_priority"


@pytest.mark.asyncio
async def test_control_state_never_mutates_the_data_source(
    client, db_session, sample_source_data, monkeypatch
):
    """HARD RULE: this endpoint is advisory-only. Even for a source in a
    "should pause" state, the DataSource row itself (enabled/schedule fields)
    must be byte-for-byte unchanged after calling the endpoint."""
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]
    before = (await client.get(f"/api/v1/sources/{source_id}")).json()["data"]

    await _seed_measurement_row(db_session, source_id, error_kinds={"auth_failed": 1})

    response = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert response.status_code == 200
    assert response.json()["data"]["control_state"] == "auth_failed"
    assert response.json()["data"]["suggested_actions"]  # pause_source suggested

    after = (await client.get(f"/api/v1/sources/{source_id}")).json()["data"]
    # Nothing executed: enabled/channel_config/name are untouched despite an
    # AUTH_FAILED verdict that suggests pausing.
    assert before["enabled"] == after["enabled"] == sample_source_data["enabled"]
    assert before["channel_config"] == after["channel_config"]
    assert before == after

    # PR-Control-3.5: recording evidence is NOT the same as acting on it —
    # the suggestion IS persisted to the advisory ledger (control_actions),
    # but that write targets a brand-new evidence table, never the
    # DataSource row asserted untouched above.
    from sqlalchemy import select

    from backend.models.control_action import ControlActionRecord

    ledger_rows = (
        (
            await db_session.execute(
                select(ControlActionRecord).where(ControlActionRecord.source_id == source_id)
            )
        )
        .scalars()
        .all()
    )
    assert ledger_rows
    assert all(row.executed is False for row in ledger_rows)
    assert all(row.mode == "advisory" for row in ledger_rows)


# ── per-source objective override (issue 02) ────────────────────────────────
@pytest.mark.asyncio
async def test_set_objective_override(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": 0.2}},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["objective_override"] == {"max_error_rate": 0.2}


@pytest.mark.asyncio
async def test_update_objective_override(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": 0.2}},
    )
    response = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": 0.35, "max_pending": 500}},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # A subsequent PATCH replaces the stored override wholesale (not a deep
    # merge across calls) — max_error_rate from the first call is gone.
    assert data["objective_override"] == {"max_error_rate": 0.35, "max_pending": 500}


@pytest.mark.asyncio
async def test_clear_objective_override(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": 0.2}},
    )
    response = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": None},
    )
    assert response.status_code == 200
    assert response.json()["data"]["objective_override"] is None


@pytest.mark.asyncio
async def test_objective_override_roundtrip_set_update_clear(client, sample_source_data):
    """Full roundtrip in one flow: unset -> set -> update -> clear, verifying
    the stored value via GET after each step."""
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    initial = await client.get(f"/api/v1/sources/{source_id}")
    assert initial.json()["data"]["objective_override"] is None

    set_resp = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_pending": 42}},
    )
    assert set_resp.status_code == 200
    after_set = await client.get(f"/api/v1/sources/{source_id}")
    assert after_set.json()["data"]["objective_override"] == {"max_pending": 42}

    update_resp = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_pending": 99}},
    )
    assert update_resp.status_code == 200
    after_update = await client.get(f"/api/v1/sources/{source_id}")
    assert after_update.json()["data"]["objective_override"] == {"max_pending": 99}

    clear_resp = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": None},
    )
    assert clear_resp.status_code == 200
    after_clear = await client.get(f"/api/v1/sources/{source_id}")
    assert after_clear.json()["data"]["objective_override"] is None


@pytest.mark.asyncio
async def test_set_objective_override_unknown_field_rejected_422(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"not_a_real_field": 1}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_set_objective_override_wrong_type_rejected_422(client, sample_source_data):
    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    response = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": "not-a-float"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_set_objective_override_source_not_found(client):
    response = await client.patch(
        "/api/v1/sources/nonexistent-id/objective",
        json={"objective_override": {"max_error_rate": 0.2}},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_control_state_classification_flips_with_objective_override(
    client, db_session, sample_source_data, monkeypatch
):
    """A borderline error_rate (0.09) is HEALTHY-eligible under the global
    default (max_error_rate=0.05 would make it DEGRADED... but here we prove
    the reverse direction: an override that TIGHTENS the threshold below a
    rate that the default would tolerate flips DEGRADED where the default
    alone would not).

    error_rate = 1/10 = 0.1. Default max_error_rate=0.05 -> already DEGRADED
    under defaults. So to prove the override (not the default) explains the
    flip, this test uses a rate the DEFAULT tolerates (max_error_rate=0.05,
    rate 0.03 -> healthy-eligible) and an override that TIGHTENS
    max_error_rate to 0.02, which the same 0.03 rate now exceeds -> DEGRADED.
    Only the override explains the flip.
    """
    monkeypatch.delenv("ODP_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ODP_DATABASE_URL", raising=False)
    monkeypatch.delenv("ODP_INGEST_URL", raising=False)

    create_resp = await client.post("/api/v1/sources", json=sample_source_data)
    source_id = create_resp.json()["data"]["id"]

    from datetime import UTC, datetime

    from backend.models.source_measurement import SourceMeasurement as SourceMeasurementRow

    row = SourceMeasurementRow(
        source_id=source_id,
        run_id="row-run-1",
        measured_at=datetime(2026, 7, 2, tzinfo=UTC),
        accepted=97,
        duplicates=0,
        rejected=3,
        error_rate=0.03,
        duplicate_rate=0.0,
        error_kinds={},
        fetch_latency_ms=10,
        cursor_advanced=True,
        freshness_lag_seconds=3,
        source_ts_quality="source",
        raw={},
    )
    db_session.add(row)
    await db_session.flush()

    # Baseline: under the global default (max_error_rate=0.05), 0.03 does not
    # exceed the threshold -> not DEGRADED via the error-rate rule.
    baseline = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert baseline.status_code == 200
    assert baseline.json()["data"]["control_state"] != "degraded"
    assert baseline.json()["data"]["objective"]["max_error_rate"] == 0.05

    # Tighten the override so the SAME measurement now exceeds max_error_rate.
    override_resp = await client.patch(
        f"/api/v1/sources/{source_id}/objective",
        json={"objective_override": {"max_error_rate": 0.02}},
    )
    assert override_resp.status_code == 200

    flipped = await client.get(f"/api/v1/sources/{source_id}/control-state")
    assert flipped.status_code == 200
    flipped_data = flipped.json()["data"]
    assert flipped_data["control_state"] == "degraded"
    # The response's resolved objective reflects the override, not the default.
    assert flipped_data["objective"]["max_error_rate"] == 0.02
    # Every other field stays the default (a partial override, not a full
    # replacement of the objective).
    assert flipped_data["objective"]["max_pending"] == 1000
