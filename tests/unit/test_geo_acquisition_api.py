"""Black-box contract tests for GEO managed acquisition executions."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.acquisition.registry import (
    OFFICIAL_SITE_CAPABILITY_COMMIT,
    OHMYOPENCLI_COMMIT,
)
from backend.database import get_db
from backend.main import create_app
from backend.schemas.acquisition import CapabilityDescriptor

BASE = "/api/v1/internal/geo-acquisition"


@pytest.fixture(autouse=True)
def _official_site_runtime_is_probed(monkeypatch):
    async def probed_capabilities():
        return [
            CapabilityDescriptor(
                capability_id="official-site.observe",
                capability_version="1.0.0",
                output_schema_version="1",
                ready=True,
                runtime={
                    "ohmyopencli_repo_commit": OHMYOPENCLI_COMMIT,
                    "capability_source_commit": OFFICIAL_SITE_CAPABILITY_COMMIT,
                    "opencli_version": "1.8.5",
                },
            )
        ]

    monkeypatch.setattr(
        "backend.api.v1.geo_acquisition.probe_capabilities",
        probed_capabilities,
    )


@pytest.fixture
def acquisition_executor(monkeypatch):
    executor = SimpleNamespace(
        dispatch_acquisition=AsyncMock(return_value=None),
        cancel_acquisition=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "backend.api.v1.geo_acquisition.get_executor", lambda: executor
    )
    return executor


def _request(**overrides) -> dict:
    body = {
        "request_id": "request-1",
        "idempotency_key": "attempt-1",
        "capability": {
            "id": "official-site.observe",
            "version": "1.0.0",
        },
        "output_schema_version": "1",
        "input": {"url": "https://example.com"},
        "environment": {"locale": "zh-CN", "region": "CN"},
        "required_artifacts": ["trace"],
        "geo_refs": {"trial_id": "trial-1", "attempt_id": "attempt-1"},
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_geo_can_discover_submit_observe_and_cancel_an_execution(
    client, acquisition_executor
):
    capabilities = await client.get(f"{BASE}/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "capabilities": [
            {
                "capability_id": "official-site.observe",
                "capability_version": "1.0.0",
                "output_schema_version": "1",
                "target": None,
                "ready": True,
                "runtime": {
                    "ohmyopencli_repo_commit": OHMYOPENCLI_COMMIT,
                    "capability_source_commit": OFFICIAL_SITE_CAPABILITY_COMMIT,
                    "opencli_version": "1.8.5",
                },
                "unavailable_reason": None,
            }
        ]
    }

    submitted = await client.post(f"{BASE}/executions", json=_request())
    assert submitted.status_code == 202
    assert '"created_at":"' in submitted.text
    assert "+00:00" in submitted.text
    execution = submitted.json()
    assert execution["request_id"] == "request-1"
    assert execution["capability_id"] == "official-site.observe"
    assert execution["capability_version"] == "1.0.0"
    assert execution["output_schema_version"] == "1"
    assert execution["status"] == "queued"
    assert execution["result"] is None
    assert execution["failure"] is None
    assert execution["trace_ref"] is None
    assert execution["artifact_refs"] == []
    acquisition_executor.dispatch_acquisition.assert_awaited_once_with(
        execution["execution_id"]
    )

    observed = await client.get(f"{BASE}/executions/{execution['execution_id']}")
    assert observed.status_code == 200
    assert observed.json() == execution

    cancelled = await client.post(
        f"{BASE}/executions/{execution['execution_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    acquisition_executor.cancel_acquisition.assert_awaited_once_with(
        execution["execution_id"]
    )


@pytest.mark.asyncio
async def test_cancel_stays_successful_when_best_effort_revoke_fails(
    client, acquisition_executor
):
    submitted = await client.post(f"{BASE}/executions", json=_request())
    execution_id = submitted.json()["execution_id"]
    acquisition_executor.cancel_acquisition.side_effect = RuntimeError("broker unavailable")

    cancelled = await client.post(f"{BASE}/executions/{execution_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_submission_rejects_a_capability_without_a_clean_profile(
    client,
    monkeypatch,
    acquisition_executor,
):
    async def unavailable_capabilities():
        return [
            CapabilityDescriptor(
                capability_id="official-site.observe",
                capability_version="1.0.0",
                output_schema_version="1",
                ready=False,
                unavailable_reason="no_clean_profile",
            )
        ]

    monkeypatch.setattr(
        "backend.api.v1.geo_acquisition.probe_capabilities",
        unavailable_capabilities,
    )

    response = await client.post(f"{BASE}/executions", json=_request())

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "no_clean_profile",
        "message": "Capability is not usable",
    }
    acquisition_executor.dispatch_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_rejects_unsupported_required_artifact(
    client, acquisition_executor
):
    response = await client.post(
        f"{BASE}/executions",
        json=_request(required_artifacts=["dom"]),
    )

    assert response.status_code == 422
    acquisition_executor.dispatch_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_idempotency_returns_the_existing_execution(
    client, acquisition_executor
):
    first = await client.post(f"{BASE}/executions", json=_request())
    repeated = await client.post(f"{BASE}/executions", json=_request())

    assert first.status_code == 202
    assert repeated.status_code == 200
    assert repeated.json()["execution_id"] == first.json()["execution_id"]
    assert acquisition_executor.dispatch_acquisition.await_count == 2


@pytest.mark.asyncio
async def test_queued_idempotent_retry_repairs_a_failed_dispatch(
    client, acquisition_executor
):
    acquisition_executor.dispatch_acquisition.side_effect = [
        RuntimeError("queue unavailable"),
        None,
    ]

    first = await client.post(f"{BASE}/executions", json=_request())
    repeated = await client.post(f"{BASE}/executions", json=_request())

    assert first.status_code == 503
    assert first.json()["detail"]["code"] == "acquisition_dispatch_failed"
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "queued"
    assert acquisition_executor.dispatch_acquisition.await_count == 2


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_different_work(
    client, acquisition_executor
):
    first = await client.post(f"{BASE}/executions", json=_request())
    conflicting = await client.post(
        f"{BASE}/executions",
        json=_request(request_id="request-2", input={"probe": "different"}),
    )

    assert first.status_code == 202
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "idempotency_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "schema", "code"),
    [
        (
            {"id": "unknown", "version": "1.0.0"},
            "1",
            "unsupported_capability",
        ),
        (
            {"id": "official-site.observe", "version": "2.0.0"},
            "1",
            "unsupported_capability_version",
        ),
        (
            {"id": "official-site.observe", "version": "1.0.0"},
            "2",
            "unsupported_output_schema_version",
        ),
    ],
)
async def test_unknown_or_mismatched_versions_are_rejected(
    client, acquisition_executor, capability, schema, code
):
    response = await client.post(
        f"{BASE}/executions",
        json=_request(capability=capability, output_schema_version=schema),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


@pytest.mark.asyncio
async def test_chat_ai_target_must_match_a_registered_adapter(
    client,
    monkeypatch,
    acquisition_executor,
):
    async def doubao_capability():
        return [
            CapabilityDescriptor(
                capability_id="chat-ai.capture",
                capability_version="1.0.0",
                output_schema_version="1",
                target="doubao",
                ready=True,
            )
        ]

    monkeypatch.setattr(
        "backend.api.v1.geo_acquisition.probe_capabilities",
        doubao_capability,
    )
    request = _request(
        capability={"id": "chat-ai.capture", "version": "1.0.0"},
        input={"target": "chatgpt", "prompt": "prompt"},
    )

    response = await client.post(f"{BASE}/executions", json=request)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "target_not_registered",
        "message": "chatgpt",
    }
    acquisition_executor.dispatch_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_is_observable_from_a_new_app_after_restart(
    db_engine, acquisition_executor
):
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    first_app = create_app()
    first_app.dependency_overrides[get_db] = sessions
    async with AsyncClient(
        transport=ASGITransport(app=first_app), base_url="http://first-process"
    ) as first_client:
        submitted = await first_client.post(f"{BASE}/executions", json=_request())
        assert submitted.status_code == 202
        execution_id = submitted.json()["execution_id"]

    restarted_app = create_app()
    restarted_app.dependency_overrides[get_db] = sessions
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://restarted-process"
    ) as restarted_client:
        observed = await restarted_client.get(f"{BASE}/executions/{execution_id}")

    assert observed.status_code == 200
    assert observed.json()["execution_id"] == execution_id
    assert observed.json()["status"] == "queued"
