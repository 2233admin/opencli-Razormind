from unittest.mock import AsyncMock

import pytest
import sqlalchemy.ext.asyncio as _sqlalchemy_asyncio

from backend.browser_pool import init_pool
from backend.channels.base import ChannelResult
from backend.schemas.acquisition import AcquisitionSubmission
from backend.services import acquisition_service

AsyncSession = _sqlalchemy_asyncio.AsyncSession
async_sessionmaker = _sqlalchemy_asyncio.async_sessionmaker


def _doubao_submission(request_id: str, idempotency_key: str) -> AcquisitionSubmission:
    return AcquisitionSubmission.model_validate(
        {
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "capability": {"id": "chat-ai.capture", "version": "1.0.0"},
            "output_schema_version": "1",
            "input": {
                "target": "doubao",
                "prompt": "黑白调电竞椅值得买吗？",
            },
            "environment": {"locale": "zh-CN", "region": "CN"},
            "required_artifacts": ["trace"],
            "geo_refs": {"attempt_id": idempotency_key},
        }
    )


@pytest.mark.asyncio
async def test_doubao_execution_uses_authenticated_profile_and_frozen_prompt(
    db_engine,
    monkeypatch,
):
    from backend.acquisition import capabilities
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission = _doubao_submission("doubao-request-1", "doubao-attempt-1")
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    endpoint = "http://doubao-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_mode(endpoint, "cdp")
    pool.set_profile_kind(endpoint, "authenticated")
    payload = {
        "capabilityId": "chat-ai.capture",
        "capabilityVersion": "1.0.0",
        "outputSchemaVersion": "1",
        "target": "doubao",
        "prompt": "黑白调电竞椅值得买吗？",
        "completionState": "complete",
        "answer": {"text": "真实回答", "sha256": "a" * 64},
        "citations": [],
        "displayedUrl": "https://www.doubao.com/chat/1",
        "finalUrl": "https://www.doubao.com/chat/1",
        "pageState": "answer",
        "artifacts": [],
    }
    channel = AsyncMock()
    session_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(capabilities, "_session_is_ready", session_probe)

    async def collect_while_leased(*_args, **_kwargs):
        assert pool.available_for(endpoint) is False
        return ChannelResult.ok(
            [payload],
            trace_artifact="artifact://trace/doubao-1",
            trace_sha256="f" * 64,
        )

    channel.collect.side_effect = collect_while_leased

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    channel.collect.assert_awaited_once_with(
        {"site": "doubao", "command": "capture", "format": "json"},
        {
            "prompt": "黑白调电竞椅值得买吗？",
            "chrome_endpoint": endpoint,
            "required_profile_kind": "authenticated",
            "_endpoint_preacquired": True,
            "trace": "on",
        },
    )
    assert session_probe.await_args.args[2] == endpoint
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status.value == "succeeded"
        assert execution.result_payload["payload"] == payload
        assert execution.result_payload["operational"]["browser"] == {
            "endpoint": endpoint,
            "profile_kind": "authenticated",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"), [("target", "chatgpt"), ("prompt", "a different prompt")]
)
async def test_doubao_rejects_target_or_prompt_drift(
    db_engine,
    monkeypatch,
    field,
    value,
):
    from backend.acquisition import capabilities
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission = _doubao_submission(
        f"doubao-drift-{field}",
        f"doubao-drift-{field}",
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    endpoint = "http://doubao-drift-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "authenticated")
    monkeypatch.setattr(
        capabilities,
        "_session_is_ready",
        AsyncMock(return_value=True),
    )
    payload = {
        "capabilityId": "chat-ai.capture",
        "capabilityVersion": "1.0.0",
        "outputSchemaVersion": "1",
        "target": "doubao",
        "prompt": "黑白调电竞椅值得买吗？",
    }
    payload[field] = value
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok([payload])

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status.value == "failed"
        assert execution.failure["code"] == "invalid_capability_envelope"


@pytest.mark.asyncio
async def test_doubao_execution_fails_closed_without_authenticated_profile(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission = _doubao_submission("doubao-request-2", "doubao-attempt-2")
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    endpoint = "http://anonymous-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "anonymous")
    channel = AsyncMock()

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    channel.collect.assert_not_awaited()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.failure == {
            "code": "no_authenticated_profile",
            "message": "no_authenticated_profile",
        }


@pytest.mark.asyncio
async def test_doubao_rechecks_the_selected_session_before_prompt_submission(
    db_engine,
    monkeypatch,
):
    from backend.acquisition import capabilities
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission = _doubao_submission("doubao-request-3", "doubao-attempt-3")
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    endpoint = "http://expired-doubao-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "authenticated")
    session_probe = AsyncMock(return_value=False)
    monkeypatch.setattr(capabilities, "_session_is_ready", session_probe)
    channel = AsyncMock()

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    session_probe.assert_awaited_once()
    assert session_probe.await_args.args[2] == endpoint
    channel.collect.assert_not_awaited()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.failure == {
            "code": "session_not_qualified",
            "message": "doubao session failed the execution-time readiness probe",
        }
