"""Integration tests for model-provider runtime PR-C's provider-scoped API (decision #10):
``POST /providers/{id}/test``, ``POST /providers/{id}/models/sync``, and
``GET|POST|PATCH|DELETE /providers/{id}/models``.

The adapter (``backend.services.provider_model_service.get_adapter``) is
mocked in every test — nothing here makes a real network call. Two
security-critical properties get dedicated coverage: the stored ``api_key``
never appears anywhere in a response body, and sync never overwrites or
deletes a ``source="manual"`` catalog row (decision #3).
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.models.provider_model import ProviderModel

SECRET_KEY = "sk-test-super-secret-do-not-leak-98765"


@pytest.fixture
def provider_data():
    return {
        "name": "Test Provider",
        "provider_type": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key": SECRET_KEY,
        "default_model": "gpt-4o-mini",
        "enabled": True,
    }


async def _create_provider(client, provider_data) -> str:
    resp = await client.post("/api/v1/providers", json=provider_data)
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


def _patch_adapter(**method_results):
    """Patch provider_model_service.get_adapter to return a mock adapter
    whose async methods return the given results.

    Usage: ``_patch_adapter(test_connection={"ok": True, ...})`` or
    ``_patch_adapter(list_models=["m1", "m2"])``.
    """
    mock_adapter = AsyncMock()
    for method_name, result in method_results.items():
        getattr(mock_adapter, method_name).return_value = result
    return patch(
        "backend.services.provider_model_service.get_adapter",
        return_value=mock_adapter,
    )


# ---------------------------------------------------------------------------
# POST /providers/discover-models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_models_before_provider_is_saved(client):
    mock_adapter = AsyncMock()
    mock_adapter.list_models.return_value = ["model-a", "model-b"]

    with patch("backend.api.v1.providers.get_adapter", return_value=mock_adapter) as factory:
        resp = await client.post(
            "/api/v1/providers/discover-models",
            json={
                "provider_type": "local",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": SECRET_KEY,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["data"] == ["model-a", "model-b"]
    assert SECRET_KEY not in resp.text
    provider_view = factory.call_args.args[0]
    assert provider_view.provider_type == "local"
    assert provider_view.base_url == "http://127.0.0.1:11434/v1"
    assert provider_view.api_key == SECRET_KEY


# ---------------------------------------------------------------------------
# POST /providers/{id}/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_success(client, provider_data):
    provider_id = await _create_provider(client, provider_data)

    with _patch_adapter(
        test_connection={
            "ok": True,
            "latency_ms": 12.5,
            "error": None,
            "models_sample": ["gpt-4o-mini"],
        }
    ):
        resp = await client.post(f"/api/v1/providers/{provider_id}/test")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["latency_ms"] == 12.5
    assert data["models_sample"] == ["gpt-4o-mini"]
    assert SECRET_KEY not in resp.text


@pytest.mark.asyncio
async def test_test_connection_failure(client, provider_data):
    provider_id = await _create_provider(client, provider_data)

    with _patch_adapter(
        test_connection={"ok": False, "error": "connection refused", "latency_ms": None}
    ):
        resp = await client.post(f"/api/v1/providers/{provider_id}/test")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["error"] == "connection refused"
    assert SECRET_KEY not in resp.text


@pytest.mark.asyncio
async def test_test_connection_provider_not_found(client):
    resp = await client.post("/api/v1/providers/nonexistent-id/test")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /providers/{id}/models/sync — decision #3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_discovered_rows(client, provider_data):
    provider_id = await _create_provider(client, provider_data)

    with _patch_adapter(list_models=["m1", "m2"]):
        resp = await client.post(f"/api/v1/providers/{provider_id}/models/sync")

    assert resp.status_code == 200
    assert resp.json()["data"] == {"added": 2, "updated": 0, "kept_manual": 0, "pruned": 0}

    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    rows = list_resp.json()["data"]
    assert {r["model_id"] for r in rows} == {"m1", "m2"}
    assert all(r["source"] == "discovered" for r in rows)


@pytest.mark.asyncio
async def test_sync_idempotent_and_manual_preserved(client, provider_data):
    """The scenario from model-provider runtime PR-C's spec: seed provider, sync [m1, m2] ->
    2 discovered rows; add manual m3; sync again with only [m1] -> m3
    (manual) survives untouched, m2 (stale discovered) is pruned, m1 is not
    duplicated (idempotent)."""
    provider_id = await _create_provider(client, provider_data)

    with _patch_adapter(list_models=["m1", "m2"]):
        first = await client.post(f"/api/v1/providers/{provider_id}/models/sync")
    assert first.json()["data"] == {"added": 2, "updated": 0, "kept_manual": 0, "pruned": 0}

    manual_resp = await client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": "m3"}
    )
    assert manual_resp.status_code == 201
    assert manual_resp.json()["data"]["source"] == "manual"

    with _patch_adapter(list_models=["m1"]):
        second = await client.post(f"/api/v1/providers/{provider_id}/models/sync")
    assert second.json()["data"] == {"added": 0, "updated": 1, "kept_manual": 0, "pruned": 1}

    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    rows = {r["model_id"]: r for r in list_resp.json()["data"]}
    assert set(rows) == {"m1", "m3"}
    assert rows["m1"]["source"] == "discovered"
    assert rows["m3"]["source"] == "manual"

    # Idempotency: re-running the identical [m1] sync doesn't duplicate m1
    # or touch the manual m3 row again.
    with _patch_adapter(list_models=["m1"]):
        third = await client.post(f"/api/v1/providers/{provider_id}/models/sync")
    assert third.json()["data"] == {"added": 0, "updated": 1, "kept_manual": 0, "pruned": 0}
    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    assert {r["model_id"] for r in list_resp.json()["data"]} == {"m1", "m3"}


@pytest.mark.asyncio
async def test_sync_discovering_same_model_id_as_manual_row_counts_kept_manual(
    client, provider_data
):
    """If discovery reports a model_id that already has a manual row, sync
    must not touch/duplicate it — counted as kept_manual, not added."""
    provider_id = await _create_provider(client, provider_data)

    manual_resp = await client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": "m1"}
    )
    assert manual_resp.status_code == 201

    with _patch_adapter(list_models=["m1"]):
        resp = await client.post(f"/api/v1/providers/{provider_id}/models/sync")

    assert resp.json()["data"] == {"added": 0, "updated": 0, "kept_manual": 1, "pruned": 0}
    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    rows = list_resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_sync_provider_not_found(client):
    with _patch_adapter(list_models=["m1"]):
        resp = await client.post("/api/v1/providers/nonexistent-id/models/sync")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_adapter_failure_returns_502_not_500(client, provider_data):
    """A genuine discovery failure -- the adapter contract says list_models()
    raises LlmAdapterError -- surfaces as 502 (sanitized message already,
    per the adapter's own guarantee), not a raw 500."""
    from backend.llm.base import LlmAdapterError

    provider_id = await _create_provider(client, provider_data)
    mock_adapter = AsyncMock()
    mock_adapter.list_models.side_effect = LlmAdapterError("connection timed out")
    with patch(
        "backend.services.provider_model_service.get_adapter", return_value=mock_adapter
    ):
        resp = await client.post(f"/api/v1/providers/{provider_id}/models/sync")
    assert resp.status_code == 502
    assert "connection timed out" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_manual_model_then_list_shows_source_manual(client, provider_data):
    provider_id = await _create_provider(client, provider_data)

    resp = await client.post(
        f"/api/v1/providers/{provider_id}/models",
        json={"model_id": "custom-model", "model_type": "llm", "enabled": True},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["source"] == "manual"

    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    rows = list_resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["model_id"] == "custom-model"
    assert rows[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_add_manual_model_provider_not_found(client):
    resp = await client.post(
        "/api/v1/providers/nonexistent-id/models", json={"model_id": "m1"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_model_updates_enabled(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    create_resp = await client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": "m1"}
    )
    model_row_id = create_resp.json()["data"]["id"]

    patch_resp = await client.patch(
        f"/api/v1/providers/{provider_id}/models/{model_row_id}",
        json={"enabled": False},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["enabled"] is False

    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    assert list_resp.json()["data"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_patch_model_not_found(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    resp = await client.patch(
        f"/api/v1/providers/{provider_id}/models/nonexistent-row",
        json={"enabled": False},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_model_removes_row(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    create_resp = await client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": "m1"}
    )
    model_row_id = create_resp.json()["data"]["id"]

    delete_resp = await client.delete(f"/api/v1/providers/{provider_id}/models/{model_row_id}")
    assert delete_resp.status_code == 200

    list_resp = await client.get(f"/api/v1/providers/{provider_id}/models")
    assert list_resp.json()["data"] == []


@pytest.mark.asyncio
async def test_delete_model_not_found(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    resp = await client.delete(f"/api/v1/providers/{provider_id}/models/nonexistent-row")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_model_row_scoped_to_its_own_provider(client, provider_data):
    """A model row created under provider A is not reachable through
    provider B's URL subtree (ownership check, not just existence)."""
    provider_a = await _create_provider(client, provider_data)
    provider_b = await _create_provider(client, {**provider_data, "name": "Provider B"})

    create_resp = await client.post(
        f"/api/v1/providers/{provider_a}/models", json={"model_id": "m1"}
    )
    model_row_id = create_resp.json()["data"]["id"]

    cross_patch = await client.patch(
        f"/api/v1/providers/{provider_b}/models/{model_row_id}", json={"enabled": False}
    )
    assert cross_patch.status_code == 404

    cross_delete = await client.delete(f"/api/v1/providers/{provider_b}/models/{model_row_id}")
    assert cross_delete.status_code == 404

    # Still present, untouched, under its real owner.
    list_resp = await client.get(f"/api/v1/providers/{provider_a}/models")
    assert list_resp.json()["data"][0]["enabled"] is True


# ---------------------------------------------------------------------------
# Provider-delete cleanup (PR-A FK-cascade note: sqlite won't cascade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_provider_cleans_up_catalog_rows(client, db_session, provider_data):
    provider_id = await _create_provider(client, provider_data)
    await client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "m1"})
    await client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "m2"})

    rows_before = (
        (
            await db_session.execute(
                select(ProviderModel).where(ProviderModel.provider_id == provider_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_before) == 2

    delete_resp = await client.delete(f"/api/v1/providers/{provider_id}")
    assert delete_resp.status_code == 200

    rows_after = (
        (
            await db_session.execute(
                select(ProviderModel).where(ProviderModel.provider_id == provider_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows_after == []
