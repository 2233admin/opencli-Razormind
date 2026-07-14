"""Durable execution of runtime-probed acquisition capabilities."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.acquisition.registry import get_capability_registration
from backend.browser_pool import (
    LocalBrowserPool,
    NoCleanProfileError,
    NoMatchingProfileError,
    RedisBrowserPool,
)
from backend.models.acquisition import AcquisitionExecution, AcquisitionExecutionStatus
from backend.models.browser import BrowserInstance

_LEASE_DURATION = timedelta(seconds=30)
_HEARTBEAT_INTERVAL_SECONDS = 5
logger = logging.getLogger(__name__)

async def _managed_browser_pool(
    session_factory: async_sessionmaker[AsyncSession],
):
    """Initialize and hydrate the pool inside API or Celery worker processes."""
    from backend import browser_pool

    try:
        pool = browser_pool.get_pool()
    except RuntimeError:
        from backend.config import get_settings

        settings = get_settings()
        pool = browser_pool.init_pool(
            endpoints=settings.cdp_endpoints,
            use_redis=settings.task_executor == "celery",
            redis_url=settings.redis_url,
        )
        await browser_pool.ensure_ready()

    async with session_factory() as db:
        result = await db.execute(select(BrowserInstance))
        instances = list(result.scalars().all())

    for instance in instances:
        if isinstance(pool, RedisBrowserPool):
            await pool.register_endpoint(instance.endpoint)
        elif instance.endpoint not in pool.endpoints:
            if isinstance(pool, LocalBrowserPool) and instance.agent_url:
                pool.add_endpoint(instance.endpoint)
            else:
                continue
        pool.set_mode(instance.endpoint, instance.mode)
        pool.set_profile_kind(instance.endpoint, instance.profile_kind)
        if isinstance(pool, LocalBrowserPool):
            pool.set_agent_url(instance.endpoint, instance.agent_url)
            pool.set_agent_protocol(instance.endpoint, instance.agent_protocol)
    return pool


async def recover_acquisition_executions(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    executor: Any = None,
    include_queued: bool = True,
) -> list[str]:
    """Requeue every durable non-terminal acquisition after process restart."""
    if session_factory is None:
        from backend.database import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    if executor is None:
        from backend.executor import get_executor

        executor = get_executor()

    async with session_factory() as db:
        now = datetime.now(UTC)
        result = await db.execute(
            select(AcquisitionExecution)
            .where(
                or_(
                    AcquisitionExecution.status.in_(
                        [AcquisitionExecutionStatus.ACCEPTED]
                        + (
                            [AcquisitionExecutionStatus.QUEUED]
                            if include_queued
                            else []
                        )
                    ),
                    and_(
                        AcquisitionExecution.status
                        == AcquisitionExecutionStatus.RUNNING,
                        or_(
                            AcquisitionExecution.lease_expires_at.is_(None),
                            AcquisitionExecution.lease_expires_at <= now,
                        ),
                    ),
                )
            )
            .order_by(AcquisitionExecution.created_at)
        )
        executions = list(result.scalars().all())
        execution_ids: list[str] = []
        for execution in executions:
            if execution.status == AcquisitionExecutionStatus.QUEUED:
                execution_ids.append(execution.id)
                continue
            eligibility = [AcquisitionExecution.id == execution.id]
            if execution.status == AcquisitionExecutionStatus.RUNNING:
                eligibility.extend(
                    [
                        AcquisitionExecution.status
                        == AcquisitionExecutionStatus.RUNNING,
                        or_(
                            AcquisitionExecution.lease_expires_at.is_(None),
                            AcquisitionExecution.lease_expires_at <= now,
                        ),
                    ]
                )
            else:
                eligibility.append(
                    AcquisitionExecution.status
                    == AcquisitionExecutionStatus.ACCEPTED
                )
            requeued = await db.execute(
                update(AcquisitionExecution)
                .where(*eligibility)
                .values(
                    status=AcquisitionExecutionStatus.QUEUED,
                    started_at=None,
                    finished_at=None,
                    lease_owner=None,
                    heartbeat_at=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if requeued.rowcount == 1:
                execution_ids.append(execution.id)
        await db.commit()

    for execution_id in execution_ids:
        await executor.dispatch_acquisition(execution_id)
    return execution_ids


async def sweep_acquisition_executions(
    *,
    stop: asyncio.Event,
    interval_seconds: float = 5.0,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    executor: Any = None,
) -> None:
    """Continuously recover expired leases; CAS in recovery fences live workers."""
    while not stop.is_set():
        try:
            await recover_acquisition_executions(
                session_factory=session_factory,
                executor=executor,
                include_queued=False,
            )
        except Exception:
            logger.exception("Managed acquisition sweeper iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def _fail_execution(
    execution_id: str,
    failure: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    lease_owner: str,
) -> None:
    async with session_factory() as db:
        await db.execute(
            update(AcquisitionExecution)
            .where(
                AcquisitionExecution.id == execution_id,
                AcquisitionExecution.status == AcquisitionExecutionStatus.RUNNING,
                AcquisitionExecution.lease_owner == lease_owner,
            )
            .values(
                status=AcquisitionExecutionStatus.FAILED,
                failure=failure,
                finished_at=datetime.now(UTC),
                lease_owner=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        await db.commit()


async def _heartbeat_execution(
    execution_id: str,
    lease_owner: str,
    session_factory: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    lease_lost: asyncio.Event,
) -> None:
    try:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                )
                return
            except TimeoutError:
                pass
            now = datetime.now(UTC)
            async with session_factory() as db:
                heartbeat = await db.execute(
                    update(AcquisitionExecution)
                    .where(
                        AcquisitionExecution.id == execution_id,
                        AcquisitionExecution.status
                        == AcquisitionExecutionStatus.RUNNING,
                        AcquisitionExecution.lease_owner == lease_owner,
                    )
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now + _LEASE_DURATION,
                    )
                )
                await db.commit()
            if heartbeat.rowcount != 1:
                lease_lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Managed acquisition heartbeat failed")
        lease_lost.set()


async def run_acquisition_execution(
    execution_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    channel: Any = None,
) -> None:
    if session_factory is None:
        from backend.database import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    lease_owner = str(uuid4())
    claimed_at = datetime.now(UTC)
    async with session_factory() as db:
        claim = await db.execute(
            update(AcquisitionExecution)
            .where(
                AcquisitionExecution.id == execution_id,
                AcquisitionExecution.status == AcquisitionExecutionStatus.QUEUED,
            )
            .values(
                status=AcquisitionExecutionStatus.RUNNING,
                started_at=claimed_at,
                lease_owner=lease_owner,
                heartbeat_at=claimed_at,
                lease_expires_at=claimed_at + _LEASE_DURATION,
            )
        )
        if claim.rowcount != 1:
            await db.rollback()
            return
        await db.commit()
        execution = await db.get(AcquisitionExecution, execution_id)
        if execution is None:
            return
        input_payload = dict(execution.input_payload)
        capability_id = execution.capability_id
        capability_version = execution.capability_version
        output_schema_version = execution.output_schema_version
        required_artifacts = list(execution.required_artifacts)

    registration = get_capability_registration(
        capability_id, capability_version, output_schema_version
    )
    if registration is None:
        await _fail_execution(
            execution_id,
            {
                "code": "unsupported_capability_invocation",
                "message": (
                    f"No invocation is registered for {capability_id}"
                    f"@{capability_version} schema {output_schema_version}"
                ),
            },
            session_factory,
            lease_owner,
        )
        return

    from backend.security.url_guard import SSRFValidationError, avalidate_public_url

    if "url" in input_payload:
        try:
            input_payload["url"] = await avalidate_public_url(input_payload["url"])
        except SSRFValidationError as exc:
            await _fail_execution(
                execution_id,
                {"code": "ssrf_rejected", "message": str(exc)},
                session_factory,
                lease_owner,
            )
            return

    heartbeat_stop = asyncio.Event()
    lease_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_execution(
            execution_id,
            lease_owner,
            session_factory,
            heartbeat_stop,
            lease_lost,
        )
    )
    collection_task = None
    lease_lost_task = None
    try:
        pool = await _managed_browser_pool(session_factory)
        endpoint = pool.select_endpoint(registration.required_profile_kind)
        if channel is None:
            from backend.channels.opencli_channel import OpenCLIChannel

            channel = OpenCLIChannel()

        parameters = {
            **input_payload,
            "chrome_endpoint": endpoint,
            "required_profile_kind": registration.required_profile_kind,
        }
        from backend.config import get_settings

        if get_settings().collection_mode == "agent":
            parameters["execution_id"] = execution_id
        if required_artifacts:
            parameters["trace"] = "on"
        collection_task = asyncio.create_task(
            channel.collect(registration.invocation, parameters)
        )
        lease_lost_task = asyncio.create_task(lease_lost.wait())
        done, _ = await asyncio.wait(
            {collection_task, lease_lost_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if lease_lost_task in done:
            collection_task.cancel()
            with suppress(asyncio.CancelledError):
                await collection_task
            return
        result = await collection_task
    except (NoCleanProfileError, NoMatchingProfileError) as exc:
        await _fail_execution(
            execution_id,
            {"code": exc.code, "message": str(exc)},
            session_factory,
            lease_owner,
        )
        return
    except Exception as exc:
        await _fail_execution(
            execution_id,
            {
                "code": "capability_execution_failed",
                "message": str(exc),
                "error_type": type(exc).__name__,
            },
            session_factory,
            lease_owner,
        )
        return
    finally:
        if collection_task is not None and not collection_task.done():
            collection_task.cancel()
            with suppress(asyncio.CancelledError):
                await collection_task
        if lease_lost_task is not None and not lease_lost_task.done():
            lease_lost_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_lost_task
        heartbeat_stop.set()
        await heartbeat_task

    if result.success and result.items:
        payload = result.items[0]
        redirect_urls = [payload.get("finalUrl")]
        endpoints = payload.get("observedRedirectEndpoints", [])
        if isinstance(endpoints, list):
            redirect_urls.extend(endpoints)
        try:
            for redirect_url in dict.fromkeys(url for url in redirect_urls if url):
                await avalidate_public_url(redirect_url)
        except SSRFValidationError as exc:
            await _fail_execution(
                execution_id,
                {"code": "ssrf_redirect_rejected", "message": str(exc)},
                session_factory,
                lease_owner,
            )
            return

    async with session_factory() as db:
        execution = await db.get(AcquisitionExecution, execution_id)
        if (
            execution is None
            or execution.status != AcquisitionExecutionStatus.RUNNING
            or execution.lease_owner != lease_owner
        ):
            return
        execution.finished_at = datetime.now(UTC)
        execution.lease_owner = None
        execution.heartbeat_at = None
        execution.lease_expires_at = None
        execution.trace_ref = result.metadata.get("trace_artifact")
        if not result.success or not result.items:
            execution.status = AcquisitionExecutionStatus.FAILED
            message = result.error or "Capability returned no payload"
            execution.failure = {
                "code": (
                    "browser_route_unavailable"
                    if "CDP not reachable" in message
                    else "capability_execution_failed"
                ),
                "message": message,
            }
        else:
            payload = result.items[0]
            from backend.config import get_settings

            if get_settings().collection_mode == "agent":
                actual_runtime = result.metadata.get("runtime")
                expected_runtime = registration.runtime_identity()
                if actual_runtime != expected_runtime:
                    execution.status = AcquisitionExecutionStatus.FAILED
                    execution.failure = {
                        "code": "runtime_lineage_mismatch",
                        "message": "Agent-reported runtime lineage does not match audited pins",
                        "expected": expected_runtime,
                        "actual": actual_runtime,
                    }
                    await db.commit()
                    return
            identity_matches = (
                payload.get("capabilityId") == capability_id
                and payload.get("capabilityVersion") == capability_version
                and payload.get("outputSchemaVersion") == output_schema_version
            )
            if not identity_matches:
                execution.status = AcquisitionExecutionStatus.FAILED
                execution.failure = {
                    "code": "invalid_capability_envelope",
                    "message": (
                        "Capability payload identity does not match the requested contract"
                    ),
                }
                await db.commit()
                return

            returned_artifact_kinds = {
                "trace" for value in [result.metadata.get("trace_artifact")] if value
            }
            missing_artifacts = [
                kind for kind in required_artifacts if kind not in returned_artifact_kinds
            ]
            if missing_artifacts:
                execution.status = AcquisitionExecutionStatus.FAILED
                execution.failure = {
                    "code": "required_artifact_missing",
                    "message": (
                        "Capability did not return required artifacts: "
                        + ", ".join(missing_artifacts)
                    ),
                }
                await db.commit()
                return

            execution.status = AcquisitionExecutionStatus.SUCCEEDED
            execution.result_payload = {
                "capability": {
                    "id": capability_id,
                    "version": capability_version,
                },
                "output_schema_version": output_schema_version,
                "payload": payload,
                "operational": {
                    "runtime": (
                        result.metadata["runtime"]
                        if get_settings().collection_mode == "agent"
                        else registration.runtime_identity()
                    ),
                    "browser": {
                        "endpoint": endpoint,
                        "profile_kind": registration.required_profile_kind,
                    },
                    "channel_metadata": result.metadata,
                },
            }
            artifacts = payload.get("artifacts", [])
            artifact_refs = artifacts if isinstance(artifacts, list) else []
            if result.metadata.get("trace_artifact"):
                artifact_refs = [
                    *artifact_refs,
                    {"kind": "trace", "ref": result.metadata["trace_artifact"]},
                ]
            execution.artifact_refs = artifact_refs
            execution.failure = None
        await db.commit()
