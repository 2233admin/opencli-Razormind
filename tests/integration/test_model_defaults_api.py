"""Integration tests for model-provider runtime PR-C's ``GET|PUT /model-defaults`` (decision
#10): role closed-set validation and candidate (provider_id, model_id)
existence validation, both enforced before a row is ever stored.
"""

import pytest


@pytest.fixture
def provider_data():
    return {
        "name": "Test Provider",
        "provider_type": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-defaults-test-key",
        "default_model": "gpt-4o-mini",
        "enabled": True,
    }


async def _create_provider(client, provider_data) -> str:
    resp = await client.post("/api/v1/providers", json=provider_data)
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


async def _register_catalog_model(client, provider_id: str, model_id: str) -> None:
    resp = await client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": model_id}
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_model_defaults_empty(client):
    resp = await client.get("/api/v1/model-defaults")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_put_model_default_rejects_invalid_role(client):
    resp = await client.put(
        "/api/v1/model-defaults/summarizer", json={"candidates": []}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_model_default_rejects_candidate_with_nonexistent_provider(client):
    resp = await client.put(
        "/api/v1/model-defaults/chat",
        json={"candidates": [{"provider_id": "nonexistent-provider", "model_id": "m1"}]},
    )
    assert resp.status_code == 400
    assert "nonexistent-provider" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_model_default_rejects_candidate_model_not_in_catalog(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    # Provider exists but no provider_models row for "unregistered-model".
    resp = await client.put(
        "/api/v1/model-defaults/chat",
        json={"candidates": [{"provider_id": provider_id, "model_id": "unregistered-model"}]},
    )
    assert resp.status_code == 400
    assert "unregistered-model" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_model_default_valid_candidates_stored_and_ordered(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    await _register_catalog_model(client, provider_id, "primary-model")
    await _register_catalog_model(client, provider_id, "backup-model")

    put_resp = await client.put(
        "/api/v1/model-defaults/chat",
        json={
            "candidates": [
                {"provider_id": provider_id, "model_id": "primary-model"},
                {"provider_id": provider_id, "model_id": "backup-model"},
            ]
        },
    )
    assert put_resp.status_code == 200
    data = put_resp.json()["data"]
    assert data["role"] == "chat"
    assert data["candidates"] == [
        {"provider_id": provider_id, "model_id": "primary-model"},
        {"provider_id": provider_id, "model_id": "backup-model"},
    ]

    list_resp = await client.get("/api/v1/model-defaults")
    rows = list_resp.json()["data"]
    assert len(rows) == 1
    assert rows[0]["role"] == "chat"
    assert rows[0]["candidates"][0]["model_id"] == "primary-model"


@pytest.mark.asyncio
async def test_put_model_default_upserts_same_role(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    await _register_catalog_model(client, provider_id, "m1")
    await _register_catalog_model(client, provider_id, "m2")

    first = await client.put(
        "/api/v1/model-defaults/executor",
        json={"candidates": [{"provider_id": provider_id, "model_id": "m1"}]},
    )
    assert first.status_code == 200
    first_id = first.json()["data"]["id"]

    second = await client.put(
        "/api/v1/model-defaults/executor",
        json={"candidates": [{"provider_id": provider_id, "model_id": "m2"}]},
    )
    assert second.status_code == 200
    assert second.json()["data"]["id"] == first_id  # same row, upserted
    assert second.json()["data"]["candidates"] == [{"provider_id": provider_id, "model_id": "m2"}]

    list_resp = await client.get("/api/v1/model-defaults")
    rows = list_resp.json()["data"]
    assert len(rows) == 1  # not duplicated


@pytest.mark.asyncio
async def test_put_model_default_empty_candidates_allowed(client):
    """An empty candidate list is a legitimate way to clear a role's
    defaults (no candidates to validate, nothing rejected)."""
    resp = await client.put("/api/v1/model-defaults/enrichment", json={"candidates": []})
    assert resp.status_code == 200
    assert resp.json()["data"]["candidates"] == []


@pytest.mark.asyncio
async def test_put_model_default_all_three_roles_independent(client, provider_data):
    provider_id = await _create_provider(client, provider_data)
    await _register_catalog_model(client, provider_id, "m1")

    for role in ("chat", "executor", "enrichment"):
        resp = await client.put(
            f"/api/v1/model-defaults/{role}",
            json={"candidates": [{"provider_id": provider_id, "model_id": "m1"}]},
        )
        assert resp.status_code == 200

    list_resp = await client.get("/api/v1/model-defaults")
    rows = list_resp.json()["data"]
    assert {r["role"] for r in rows} == {"chat", "executor", "enrichment"}
