import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.acquisition.registry import (
    OFFICIAL_SITE_CAPABILITY_COMMIT,
    OHMYOPENCLI_COMMIT,
)
from backend.browser_pool import init_pool
from backend.channels.base import ChannelResult
from backend.models.acquisition import AcquisitionExecutionStatus
from backend.models.browser import BrowserInstance
from backend.schemas.acquisition import AcquisitionSubmission
from backend.services import acquisition_service


@pytest.fixture(autouse=True)
def _public_url_guard(monkeypatch):
    async def validate(url, **_kwargs):
        return url

    monkeypatch.setattr("backend.security.url_guard.avalidate_public_url", validate)


def _submission() -> AcquisitionSubmission:
    return AcquisitionSubmission.model_validate(
        {
            "request_id": "request-1",
            "idempotency_key": "attempt-1",
            "capability": {"id": "official-site.observe", "version": "1.0.0"},
            "output_schema_version": "1",
            "input": {"url": "https://example.com"},
            "environment": {"locale": "zh-CN", "region": "CN"},
            "required_artifacts": ["trace"],
            "geo_refs": {"attempt_id": "attempt-1"},
        }
    )


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("DOUBAO_CAPTURE_LOGIN_WALL", "login_required"),
        ("DOUBAO_CAPTURE_CAPTCHA", "captcha_required"),
        ("DOUBAO_CAPTURE_TIMEOUT", "capture_timeout"),
        ("DOUBAO_CAPTURE_REFUSAL", "model_refusal"),
        ("DOUBAO_CAPTURE_EMPTY_ANSWER", "empty_answer"),
        ("DOUBAO_CAPTURE_PAGE_DRIFT", "page_contract_drift"),
    ],
)
def test_doubao_typed_failures_are_preserved(message, code):
    from backend.acquisition.runner import _capability_failure_code

    assert _capability_failure_code(message) == code
    assert _capability_failure_code("generic adapter failure", message) == code


@pytest.mark.asyncio
async def test_official_site_execution_rejects_private_target_before_opencli(
    db_engine, monkeypatch
):
    from backend.acquisition.runner import run_acquisition_execution
    from backend.security.url_guard import SSRFValidationError

    async def reject(_url, **_kwargs):
        raise SSRFValidationError("non-public address")

    monkeypatch.setattr("backend.security.url_guard.avalidate_public_url", reject)
    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission = _submission().model_copy(update={"input": {"url": "http://127.0.0.1"}})
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id
    channel = AsyncMock()

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    channel.collect.assert_not_awaited()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.failure["code"] == "ssrf_rejected"


@pytest.mark.asyncio
async def test_official_site_execution_fails_without_calling_opencli_when_no_clean_profile(
    db_engine,
):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    init_pool(["http://signed-in-profile:9222"], use_redis=False)
    channel = AsyncMock()

    await run_acquisition_execution(
        execution_id,
        session_factory=sessions,
        channel=channel,
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "no_clean_profile",
            "message": "no_clean_profile",
        }
        assert execution.result_payload is None

    channel.collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_delivery_claims_a_queued_execution_only_once(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def collect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        if calls == 1:
            await release.wait()
        return ChannelResult.ok(
            [
                {
                    "capabilityId": "official-site.observe",
                    "capabilityVersion": "1.0.0",
                    "outputSchemaVersion": "1",
                }
            ],
            trace_artifact="artifact://trace/1",
            trace_sha256="f" * 64,
        )

    channel = AsyncMock()
    channel.collect.side_effect = collect
    first = asyncio.create_task(
        run_acquisition_execution(
            execution_id, session_factory=sessions, channel=channel
        )
    )
    await started.wait()
    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )
    release.set()
    await first

    channel.collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_worker_cannot_overwrite_a_new_lease_owner(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    started = asyncio.Event()
    release = asyncio.Event()

    async def collect(*_args, **_kwargs):
        started.set()
        await release.wait()
        return ChannelResult.ok(
            [
                {
                    "capabilityId": "official-site.observe",
                    "capabilityVersion": "1.0.0",
                    "outputSchemaVersion": "1",
                }
            ],
            trace_artifact="artifact://trace/1",
            trace_sha256="f" * 64,
        )

    task = asyncio.create_task(
        run_acquisition_execution(
            execution_id,
            session_factory=sessions,
            channel=AsyncMock(collect=AsyncMock(side_effect=collect)),
        )
    )
    await started.wait()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.lease_owner is not None
        execution.lease_owner = "replacement-worker"
        execution.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        await db.commit()

    release.set()
    await task

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.RUNNING
        assert execution.lease_owner == "replacement-worker"
        assert execution.result_payload is None


@pytest.mark.asyncio
async def test_running_execution_renews_its_lease_while_collecting(
    db_engine, monkeypatch
):
    from backend.acquisition.runner import run_acquisition_execution

    monkeypatch.setattr(
        "backend.acquisition.runner._HEARTBEAT_INTERVAL_SECONDS", 0.01
    )
    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    started = asyncio.Event()
    release = asyncio.Event()

    async def collect(*_args, **_kwargs):
        started.set()
        await release.wait()
        return ChannelResult.fail("cancelled by test")

    channel = AsyncMock()
    channel.collect.side_effect = collect
    task = asyncio.create_task(
        run_acquisition_execution(
            execution_id, session_factory=sessions, channel=channel
        )
    )
    await started.wait()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        first_heartbeat = execution.heartbeat_at

    await asyncio.sleep(0.04)
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.heartbeat_at != first_heartbeat
        assert execution.lease_expires_at is not None

    release.set()
    await task


@pytest.mark.asyncio
async def test_durable_cancellation_stops_the_inflight_channel(db_engine, monkeypatch):
    from backend.acquisition.runner import run_acquisition_execution

    monkeypatch.setattr(
        "backend.acquisition.runner._HEARTBEAT_INTERVAL_SECONDS", 0.01
    )
    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    started = asyncio.Event()
    channel_cancelled = asyncio.Event()

    async def collect(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            channel_cancelled.set()
            raise

    channel = AsyncMock()
    channel.collect.side_effect = collect
    task = asyncio.create_task(
        run_acquisition_execution(
            execution_id, session_factory=sessions, channel=channel
        )
    )
    await started.wait()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        await acquisition_service.cancel_execution(db, execution)

    try:
        # SQLite scheduling and Windows CI can delay the heartbeat transaction;
        # keep the assertion below the production 5s cancellation bound without
        # making the test depend on a sub-second scheduler turn.
        await asyncio.wait_for(channel_cancelled.wait(), timeout=2.0)
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_pool_initialization_error_is_persisted_as_terminal_failure(
    db_engine, monkeypatch
):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    monkeypatch.setattr(
        "backend.acquisition.runner._managed_browser_pool",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    await run_acquisition_execution(execution_id, session_factory=sessions)

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "capability_execution_failed",
            "message": "redis unavailable",
            "error_type": "RuntimeError",
        }


@pytest.mark.asyncio
async def test_required_trace_must_be_returned_by_the_channel(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [
            {
                "capabilityId": "official-site.observe",
                "capabilityVersion": "1.0.0",
                "outputSchemaVersion": "1",
            }
        ]
    )

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "required_artifact_missing",
            "message": "Capability did not return required artifacts: trace",
        }
        assert execution.trace_ref is None


@pytest.mark.asyncio
async def test_required_trace_must_include_a_content_hash(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    endpoint = "http://unhashed-trace-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "anonymous")
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [
            {
                "capabilityId": "official-site.observe",
                "capabilityVersion": "1.0.0",
                "outputSchemaVersion": "1",
            }
        ],
        trace_artifact="artifact://trace/unhashed",
    )

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure["code"] == "required_artifact_missing"


@pytest.mark.asyncio
async def test_unknown_capability_invocation_fails_closed(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    submission_data = _submission().model_dump()
    submission_data["capability"] = {
        "id": "chat-ai.capture",
        "version": "1.0.0",
    }
    submission = AcquisitionSubmission.model_validate(submission_data)
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, submission)
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    channel = AsyncMock()

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "unsupported_capability_invocation",
            "message": "No invocation is registered for chat-ai.capture@1.0.0 schema 1",
        }
    channel.collect.assert_not_awaited()


def test_dispatch_registry_contains_only_real_versioned_capabilities():
    from backend.acquisition.registry import list_capability_registrations

    registrations = list_capability_registrations()

    assert [registration.identity for registration in registrations] == [
        ("official-site.observe", "1.0.0", "1", None),
        ("chat-ai.capture", "1.0.0", "1", "doubao"),
    ]
    assert registrations[0].invocation == {
        "site": "official-site",
        "command": "observe",
        "format": "json",
    }
    assert registrations[1].invocation == {
        "site": "doubao",
        "command": "capture",
        "format": "json",
    }
    assert registrations[1].required_profile_kind == "authenticated"


@pytest.mark.asyncio
async def test_worker_process_hydrates_anonymous_profile_before_dispatch(
    db_engine,
    monkeypatch,
):
    from backend import browser_pool
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id
        db.add(
            BrowserInstance(
                endpoint="http://clean-profile:9222",
                mode="cdp",
                profile_kind="anonymous",
                agent_url="http://clean-profile:9222",
            )
        )
        await db.commit()

    monkeypatch.setattr(browser_pool, "_pool", None)
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [
            {
                "capabilityId": "official-site.observe",
                "capabilityVersion": "1.0.0",
                "outputSchemaVersion": "1",
            }
        ]
    )

    await run_acquisition_execution(
        execution_id,
        session_factory=sessions,
        channel=channel,
    )

    channel.collect.assert_awaited_once()
    assert browser_pool.get_pool().get_profile_kind(
        "http://clean-profile:9222"
    ) == "anonymous"


@pytest.mark.asyncio
async def test_redis_worker_registers_a_dynamic_anonymous_profile_before_dispatch(
    db_engine, monkeypatch
):
    from backend import browser_pool
    from backend.acquisition.runner import run_acquisition_execution
    from backend.browser_pool import RedisBrowserPool

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id
        db.add(
            BrowserInstance(
                endpoint="http://dynamic-clean-profile:9222",
                mode="cdp",
                profile_kind="anonymous",
                agent_url="http://dynamic-clean-profile:9222",
            )
        )
        await db.commit()

    pool = RedisBrowserPool([], "redis://localhost:6379")
    monkeypatch.setattr(browser_pool, "_pool", pool)
    redis = AsyncMock()
    redis.sadd = AsyncMock(return_value=1)
    redis.rpush = AsyncMock()
    redis_cm = AsyncMock()
    redis_cm.__aenter__ = AsyncMock(return_value=redis)
    redis_cm.__aexit__ = AsyncMock(return_value=False)
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [
            {
                "capabilityId": "official-site.observe",
                "capabilityVersion": "1.0.0",
                "outputSchemaVersion": "1",
            }
        ],
        trace_artifact="artifact://trace/1",
        trace_sha256="f" * 64,
    )

    monkeypatch.setattr(pool, "_client", lambda: redis_cm)
    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    channel.collect.assert_awaited_once()
    assert pool.select_anonymous_endpoint() == "http://dynamic-clean-profile:9222"


@pytest.mark.asyncio
async def test_official_site_execution_preserves_payload_in_versioned_envelope(
    db_engine,
):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_mode("http://clean-profile:9222", "cdp")
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    payload = {
        "capabilityId": "official-site.observe",
        "capabilityVersion": "1.0.0",
        "outputSchemaVersion": "1",
        "finalUrl": "https://example.com/",
        "accessState": "accessible",
        "personalizationDetected": False,
        "domLength": 380997,
        "domSha256": "d" * 64,
        "artifacts": [
            {"kind": "dom", "ref": "artifact://dom/1", "sha256": "d" * 64}
        ],
    }
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [payload],
        site="official-site",
        command="observe",
        chrome_mode="cdp",
        trace_artifact="artifact://trace/1",
        trace_sha256="f" * 64,
    )

    await run_acquisition_execution(
        execution_id,
        session_factory=sessions,
        channel=channel,
    )

    channel.collect.assert_awaited_once_with(
        {"site": "official-site", "command": "observe", "format": "json"},
        {
            "url": "https://example.com",
            "chrome_endpoint": "http://clean-profile:9222",
            "required_profile_kind": "anonymous",
            "_endpoint_preacquired": True,
            "trace": "on",
        },
    )
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.SUCCEEDED
        assert execution.failure is None
        assert execution.result_payload == {
            "capability": {"id": "official-site.observe", "version": "1.0.0"},
            "output_schema_version": "1",
            "payload": payload,
            "operational": {
                "runtime": {
                    "ohmyopencli_repo_commit": OHMYOPENCLI_COMMIT,
                    "capability_source_commit": OFFICIAL_SITE_CAPABILITY_COMMIT,
                    "opencli_version": "1.8.5",
                },
                "browser": {
                    "endpoint": "http://clean-profile:9222",
                    "profile_kind": "anonymous",
                },
                "channel_metadata": {
                    "site": "official-site",
                    "command": "observe",
                    "chrome_mode": "cdp",
                    "trace_artifact": "artifact://trace/1",
                    "trace_sha256": "f" * 64,
                },
            },
        }
        assert execution.artifact_refs == [
            *payload["artifacts"],
            {
                "kind": "trace",
                "ref": "artifact://trace/1",
                "sha256": "f" * 64,
            },
        ]
        assert execution.trace_ref == "artifact://trace/1"


@pytest.mark.asyncio
async def test_startup_requeues_durable_inflight_acquisitions(db_engine):
    from backend.acquisition.runner import recover_acquisition_executions

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    execution_ids = []
    async with sessions() as db:
        for index, status in enumerate(
            [
                AcquisitionExecutionStatus.ACCEPTED,
                AcquisitionExecutionStatus.QUEUED,
                AcquisitionExecutionStatus.RUNNING,
            ]
        ):
            body = _submission().model_copy(
                update={
                    "request_id": f"request-{index}",
                    "idempotency_key": f"attempt-{index}",
                }
            )
            outcome = await acquisition_service.submit_execution(db, body)
            outcome.execution.status = status
            if status == AcquisitionExecutionStatus.RUNNING:
                outcome.execution.lease_owner = "expired-worker"
                outcome.execution.lease_expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
            execution_ids.append(outcome.execution.id)
        await db.commit()

    executor = AsyncMock()
    await recover_acquisition_executions(
        session_factory=sessions,
        executor=executor,
    )

    assert [
        call.args[0] for call in executor.dispatch_acquisition.await_args_list
    ] == execution_ids
    async with sessions() as db:
        for execution_id in execution_ids:
            execution = await acquisition_service.get_execution(db, execution_id)
            assert execution is not None
            assert execution.status == AcquisitionExecutionStatus.QUEUED


@pytest.mark.asyncio
async def test_startup_does_not_requeue_a_running_execution_with_an_active_lease(
    db_engine,
):
    from backend.acquisition.runner import recover_acquisition_executions

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        execution = outcome.execution
        execution.status = AcquisitionExecutionStatus.RUNNING
        execution.lease_owner = "live-worker"
        execution.heartbeat_at = datetime.now(UTC)
        execution.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        execution_id = execution.id
        await db.commit()

    executor = AsyncMock()
    recovered = await recover_acquisition_executions(
        session_factory=sessions,
        executor=executor,
    )

    assert recovered == []
    executor.dispatch_acquisition.assert_not_awaited()
    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.RUNNING
        assert execution.lease_owner == "live-worker"


@pytest.mark.asyncio
async def test_periodic_sweeper_requeues_execution_that_expires_after_start(
    db_engine,
):
    from backend.acquisition.runner import sweep_acquisition_executions

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        execution = outcome.execution
        execution.status = AcquisitionExecutionStatus.RUNNING
        execution.lease_owner = "dead-worker"
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    executor = AsyncMock()
    stop = asyncio.Event()
    task = asyncio.create_task(
        sweep_acquisition_executions(
            stop=stop,
            interval_seconds=0.01,
            session_factory=sessions,
            executor=executor,
        )
    )
    try:
        for _ in range(50):
            if executor.dispatch_acquisition.await_count:
                break
            await asyncio.sleep(0.01)
        executor.dispatch_acquisition.assert_awaited_once_with(execution.id)
    finally:
        stop.set()
        await task


@pytest.mark.asyncio
async def test_mismatched_capability_payload_is_persisted_as_failure(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    channel = AsyncMock()
    channel.collect.return_value = ChannelResult.ok(
        [
            {
                "capabilityId": "official-site.observe",
                "capabilityVersion": "2.0.0",
                "outputSchemaVersion": "1",
            }
        ]
    )

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "invalid_capability_envelope",
            "message": "Capability payload identity does not match the requested contract",
        }


@pytest.mark.asyncio
async def test_opencli_exception_becomes_observable_terminal_failure(db_engine):
    from backend.acquisition.runner import run_acquisition_execution

    sessions = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sessions() as db:
        outcome = await acquisition_service.submit_execution(db, _submission())
        await acquisition_service.queue_execution(db, outcome.execution)
        execution_id = outcome.execution.id

    pool = init_pool(["http://clean-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://clean-profile:9222", "anonymous")
    channel = AsyncMock()
    channel.collect.side_effect = RuntimeError("opencli crashed")

    await run_acquisition_execution(
        execution_id, session_factory=sessions, channel=channel
    )

    async with sessions() as db:
        execution = await acquisition_service.get_execution(db, execution_id)
        assert execution is not None
        assert execution.status == AcquisitionExecutionStatus.FAILED
        assert execution.failure == {
            "code": "capability_execution_failed",
            "message": "opencli crashed",
            "error_type": "RuntimeError",
        }
        assert execution.trace_ref is None
