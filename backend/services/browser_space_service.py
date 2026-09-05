from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.browser import BrowserBinding, BrowserInstance
from backend.models.browser_space import (
    BrowserSpace,
    BrowserSpaceEvent,
    BrowserSpaceEventKind,
    BrowserSpaceStatus,
    BrowserSpaceTask,
    BrowserSpaceTaskStatus,
)
from backend.models.identity import Workspace
from backend.security.identity import RequestIdentity, is_platform_admin
from backend.security.workspace_rbac import get_workspace_access
from backend.services import browser_capability_service
from backend.services.browser_service import BrowserRuntimeError, get_runtime_bundle

_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_ERROR_MESSAGE = 2_000
_SENSITIVE_KEY = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|url|cookie|token|secret|password|"
    r"credential|authorization|header|profile|html|endpoint)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?ix)(?:"
    r"(?:api[_ -]?key|access[_ -]?token|authorization|password|secret|credential|"
    r"connection[_ -]?string|token)\s*(?:[:=]|is)\s*(?:bearer\s+)?[^\s,;]+"
    r"|bearer\s+[^\s,;]+"
    r")"
)


class BrowserSpaceError(RuntimeError):
    """Stable, router-neutral error raised by Browser Space operations."""

    def __init__(self, code: str, detail: str, status_code: int = 409) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class BrowserSpaceExecutor(Protocol):
    async def execute(
        self,
        *,
        instance: BrowserInstance,
        capability: str,
        args: dict[str, Any],
        timeout_seconds: int,
        task_id: str,
    ) -> Any: ...

    async def cancel(self, task_id: str) -> bool: ...

    async def release(self, space_id: str) -> None: ...


class CapabilityExecutor:
    """Adapter that keeps all browser side effects behind the capability service."""

    def __init__(self, gate: str | None = None, gate_authorized: bool = False) -> None:
        self.gate = gate
        self.gate_authorized = gate_authorized

    async def execute(
        self,
        *,
        instance: BrowserInstance,
        capability: str,
        args: dict[str, Any],
        timeout_seconds: int,
        task_id: str,
    ) -> Any:
        from backend.database import AsyncSessionLocal
        from backend.services.browser_capability_service import invoke_capability

        # Runtime work gets a separate session so the task state transaction is
        # closed before the potentially long agent call starts.
        async with AsyncSessionLocal() as runtime_session:
            runtime_instance = await runtime_session.get(BrowserInstance, instance.id)
            if runtime_instance is None:
                raise BrowserSpaceError(
                    "isolation_unavailable", "browser instance is unavailable", 409
                )
            try:
                invocation = await invoke_capability(
                    runtime_session,
                    runtime_instance,
                    capability,
                    args,
                    gate=self.gate,
                    gate_authorized=self.gate_authorized,
                    audit_input_payload=_safe_result(args),
                    commit_before_dispatch=True,
                )
            finally:
                await runtime_session.commit()
            return invocation.output_payload or {}

    async def cancel(self, task_id: str) -> bool:
        # The current Agent protocols have no acknowledged runtime cancellation.
        # Keep the lease until the original call returns instead of claiming cleanup.
        return False

    async def release(self, space_id: str) -> None:
        return None


_cancel_requested: set[str] = set()


def _body_values(body: Any) -> dict[str, Any]:
    if isinstance(body, Mapping):
        return dict(body)
    if hasattr(body, "model_dump"):
        return body.model_dump(mode="python")
    return {
        name: getattr(body, name)
        for name in (
            "browser_instance_id",
            "binding_id",
            "owner_type",
            "owner_id",
            "granted_capabilities",
            "request_id",
            "capability",
            "args",
            "timeout_seconds",
        )
        if hasattr(body, name)
    }


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _bounded(value: Any, *, fallback_reason: str) -> Any:
    if len(_json_bytes(value)) <= _MAX_PAYLOAD_BYTES:
        return value
    return {"truncated": True, "reason": fallback_reason}


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return {"truncated": True, "reason": "nested_value_too_deep"}
    if isinstance(value, Mapping):
        return {
            str(key): _redact(item, depth=depth + 1)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
            and _redact(str(key), depth=depth + 1) == str(key)
        }
    if isinstance(value, list):
        return [_redact(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, tuple):
        return [_redact(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        value = re.sub(
            r"(?im)(?:cookie|set-cookie|profile[_ -]?path|cdp[_ -]?endpoint)\s*[:=][^\r\n]*",
            "[REDACTED]",
            value,
        )
        value = _SENSITIVE_VALUE.sub("[REDACTED]", value)
        value = re.sub(r"(?:https?|wss?)://[^\s]+", "[redacted-url]", value)
        value = re.sub(
            r"(?is)<[^>]+>.*|(?:[A-Za-z]:[\\/]|/(?:home|Users|tmp|var)/)\S*", "[REDACTED]", value
        )
        return value[:4_096]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact(str(value), depth=depth + 1)


def _safe_result(value: Any) -> dict[str, Any]:
    if len(_json_bytes(value)) > _MAX_PAYLOAD_BYTES:
        return {"truncated": True, "reason": "result_too_large"}
    redacted = _redact(value)
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}
    return _bounded(redacted, fallback_reason="result_too_large")


def _safe_error(value: Any) -> str:
    return str(_redact(str(value)))[:_MAX_ERROR_MESSAGE]


def _fingerprint(
    capability: str,
    args: dict[str, Any],
    timeout_seconds: int,
    gate: str | None = None,
) -> str:
    return hashlib.sha256(
        _json_bytes(
            {
                "capability": capability,
                "args": args,
                "timeout_seconds": timeout_seconds,
                "gate": gate,
            }
        )
    ).hexdigest()


async def _validate_access(
    db: AsyncSession,
    workspace_id: str,
    identity: RequestIdentity | None,
    *,
    owner_type: str | None = None,
    owner_id: str | None = None,
) -> None:
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None or not workspace.active:
        raise BrowserSpaceError("not_found", "workspace not found", 404)
    if identity is None:
        return
    try:
        access = await get_workspace_access(db, workspace_id, identity)
    except Exception as exc:
        raise BrowserSpaceError(
            "workspace_forbidden", "Workspace membership required", 403
        ) from exc
    if owner_type == "operator" and owner_id not in {identity.subject, access.user_id}:
        raise BrowserSpaceError("owner_forbidden", "owner identity is not authorized", 403)
    if owner_type == "runtime_agent" and owner_id != identity.subject:
        raise BrowserSpaceError("owner_forbidden", "owner identity is not authorized", 403)


async def list_spaces(
    db: AsyncSession,
    workspace_id: str,
    identity: RequestIdentity | None = None,
    limit: int = 20,
) -> list[BrowserSpace]:
    await _validate_access(db, workspace_id, identity)
    if limit < 1 or limit > 100:
        raise BrowserSpaceError("invalid_limit", "limit must be between 1 and 100", 422)
    statement = select(BrowserSpace).where(BrowserSpace.workspace_id == workspace_id)
    if identity is not None:
        access = await get_workspace_access(db, workspace_id, identity)
        if access.role not in {"admin", "maintainer"}:
            statement = statement.where(
                BrowserSpace.owner_id.in_((identity.subject, access.user_id))
            )
    result = await db.execute(statement.order_by(BrowserSpace.created_at.desc()).limit(limit))
    return list(result.scalars().all())


def _has_agent_route(instance: BrowserInstance) -> bool:
    endpoint_host = urlparse(instance.endpoint).hostname
    return bool(
        endpoint_host
        and instance.agent_url
        and instance.agent_protocol in {"http", "ws"}
        and urlparse(instance.agent_url).hostname == endpoint_host
        and instance.runtime_bundle_id
    )


async def available_instances(db: AsyncSession) -> list[dict[str, Any]]:
    """Project globally managed runtime slots without disclosing connection data."""
    reserved = select(BrowserSpace.browser_instance_id).where(BrowserSpace.status != "closed")
    instances = (
        await db.scalars(
            select(BrowserInstance)
            .where(BrowserInstance.id.not_in(reserved))
            .order_by(BrowserInstance.id)
        )
    ).all()
    result = []
    for instance in instances:
        if not _has_agent_route(instance):
            continue
        bundle = await get_runtime_bundle(db, instance.runtime_bundle_id)
        if bundle is not None:
            manifest = browser_capability_service.RuntimeBundleManifest.model_validate(
                bundle.manifest
            )
            result.append(
                {"id": instance.id, "capabilities": [c.name for c in manifest.capabilities]}
            )
    return result


async def create_space(
    db: AsyncSession,
    workspace_id: str,
    body: Any | None = None,
    identity: RequestIdentity | None = None,
    *,
    browser_instance_id: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    granted_capabilities: list[str] | None = None,
    binding_id: str | None = None,
) -> BrowserSpace:
    values = (
        _body_values(body)
        if body is not None
        else {
            "browser_instance_id": browser_instance_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "granted_capabilities": granted_capabilities,
            "binding_id": binding_id,
        }
    )
    owner_type = str(values.get("owner_type") or "")
    owner_id = str(values.get("owner_id") or "")
    instance_id = str(values.get("browser_instance_id") or "")
    binding_id = values.get("binding_id")
    grants = values.get("granted_capabilities") or []
    if owner_type not in {"operator", "runtime_agent"} or not owner_id:
        raise BrowserSpaceError("invalid_owner", "owner_type and owner_id are required", 422)
    if (
        not instance_id
        or not isinstance(grants, list)
        or any(not isinstance(item, str) or not item for item in grants)
    ):
        raise BrowserSpaceError(
            "invalid_request", "browser instance and capabilities are required", 422
        )
    await _validate_access(db, workspace_id, identity, owner_type=owner_type, owner_id=owner_id)
    if identity is not None and not is_platform_admin(identity):
        raise BrowserSpaceError(
            "platform_admin_required", "global slot allocation requires admin", 403
        )
    instance = await db.get(BrowserInstance, instance_id)
    if instance is None:
        raise BrowserSpaceError("not_found", "browser instance not found", 404)
    if not _has_agent_route(instance):
        raise BrowserSpaceError("isolation_unavailable", "managed runtime slot required", 409)
    if binding_id:
        binding = await db.get(BrowserBinding, binding_id)
        if binding is None or binding.browser_endpoint != instance.endpoint:
            raise BrowserSpaceError("not_found", "browser binding not found", 404)
    active = await db.scalar(
        select(BrowserSpace.id)
        .where(BrowserSpace.browser_instance_id == instance_id)
        .where(BrowserSpace.status != BrowserSpaceStatus.CLOSED.value)
    )
    if active is not None:
        raise BrowserSpaceError(
            "browser_instance_in_use", "browser instance is already reserved", 409
        )
    space = BrowserSpace(
        workspace_id=workspace_id,
        browser_instance_id=instance_id,
        binding_id=binding_id,
        owner_type=owner_type,
        owner_id=owner_id,
        status=BrowserSpaceStatus.IDLE.value,
        granted_capabilities=sorted(set(grants)),
    )
    db.add(space)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise BrowserSpaceError(
            "browser_instance_in_use", "browser instance is already reserved", 409
        ) from exc
    await db.refresh(space)
    return space


async def get_space(
    db: AsyncSession,
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity | None = None,
    *,
    for_update: bool = False,
) -> BrowserSpace:
    await _validate_access(db, workspace_id, identity)
    statement = (
        select(BrowserSpace)
        .where(BrowserSpace.workspace_id == workspace_id)
        .where(BrowserSpace.id == space_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    space = await db.scalar(statement)
    if space is None:
        raise BrowserSpaceError("not_found", "browser space not found", 404)
    if identity is not None:
        await _validate_access(
            db, workspace_id, identity, owner_type=space.owner_type, owner_id=space.owner_id
        )
    return space


async def get_latest_task(
    db: AsyncSession,
    space_id: str,
    *,
    active_only: bool = True,
) -> BrowserSpaceTask | None:
    statement = select(BrowserSpaceTask).where(BrowserSpaceTask.space_id == space_id)
    if active_only:
        statement = statement.where(BrowserSpaceTask.status.in_(("queued", "running")))
    return await db.scalar(
        statement.order_by(BrowserSpaceTask.created_at.desc(), BrowserSpaceTask.id.desc()).limit(1)
    )


async def _next_event_sequence(db: AsyncSession, space_id: str) -> int:
    result = await db.execute(
        text(
            "INSERT INTO browser_space_event_counters (space_id, sequence) "
            "VALUES (:space_id, 1) "
            "ON CONFLICT (space_id) DO UPDATE SET "
            "sequence = browser_space_event_counters.sequence + 1 "
            "RETURNING sequence"
        ),
        {"space_id": space_id},
    )
    return int(result.scalar_one())


async def _append_event(
    db: AsyncSession,
    space_id: str,
    kind: BrowserSpaceEventKind,
    task_id: str | None = None,
    payload: Any = None,
) -> BrowserSpaceEvent:
    async with _event_lock(space_id):
        event = BrowserSpaceEvent(
            space_id=space_id,
            task_id=task_id,
            sequence=await _next_event_sequence(db, space_id),
            kind=kind.value,
            payload=_safe_result(payload or {}),
        )
        db.add(event)
        await db.flush()
        return event


_submission_locks: dict[Any, asyncio.Lock] = {}
_finish_locks: dict[Any, dict[str, asyncio.Lock]] = {}
_event_locks: dict[Any, dict[str, asyncio.Lock]] = {}
_execution_tasks: dict[str, asyncio.Task[Any]] = {}


def _finish_lock(task_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _finish_locks.setdefault(loop, {})
    return locks.setdefault(task_id, asyncio.Lock())


def _submission_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _submission_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _submission_locks[loop] = lock
    return lock


def _event_lock(space_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _event_locks.setdefault(loop, {})
    return locks.setdefault(space_id, asyncio.Lock())


async def _new_session(db: AsyncSession) -> AsyncSession | None:
    bind = db.bind
    if bind is None:
        return None
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(bind=bind, expire_on_commit=False)()


async def submit_task(
    db: AsyncSession,
    workspace_id: str,
    space_id: str,
    body: Any | None = None,
    identity: RequestIdentity | None = None,
    executor: BrowserSpaceExecutor | Any | None = None,
    *,
    execute: bool = True,
    request_id: str | None = None,
    capability: str | None = None,
    args: dict[str, Any] | None = None,
) -> tuple[BrowserSpaceTask, bool] | BrowserSpaceTask:
    legacy_call = body is None
    values = (
        _body_values(body)
        if body is not None
        else {
            "request_id": request_id,
            "capability": capability,
            "args": args,
        }
    )
    request_id = str(values.get("request_id") or "")
    capability = str(values.get("capability") or "")
    args = values.get("args", {})
    if args is None and legacy_call:
        args = {}
    timeout_seconds = values.get("timeout_seconds", 60)
    if not request_id or len(request_id) > 64 or not capability or len(capability) > 255:
        raise BrowserSpaceError("invalid_request", "request_id and capability are required", 422)
    if (
        not isinstance(args, dict)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 600
    ):
        raise BrowserSpaceError("invalid_request", "invalid capability args or timeout", 422)
    try:
        encoded_args = json.dumps(args, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise BrowserSpaceError("invalid_request", "invalid JSON args", 422) from exc
    if len(encoded_args) > _MAX_PAYLOAD_BYTES:
        raise BrowserSpaceError("invalid_request", "args exceed 64 KiB", 422)

    async with _submission_lock():
        space = await get_space(db, workspace_id, space_id, identity, for_update=True)
        fingerprint = _fingerprint(capability, args, timeout_seconds, values.get("gate"))
        existing = await db.scalar(
            select(BrowserSpaceTask).where(
                BrowserSpaceTask.space_id == space_id,
                BrowserSpaceTask.request_id == request_id,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise BrowserSpaceError("idempotency_conflict", "request_id was already used", 409)
            return existing if legacy_call else (existing, False)
        if space.status == BrowserSpaceStatus.CLOSED.value:
            raise BrowserSpaceError("closed_space", "closed browser space rejects new tasks", 409)
        if space.last_error_code == "runtime_cleanup_unconfirmed":
            raise BrowserSpaceError(
                "runtime_cleanup_unconfirmed", "runtime slot is quarantined", 409
            )
        if capability not in set(space.granted_capabilities or []):
            raise BrowserSpaceError(
                "capability_not_granted", "capability is not granted to this space", 422
            )
        active = await db.scalar(
            select(BrowserSpaceTask.id)
            .where(BrowserSpaceTask.space_id == space_id)
            .where(BrowserSpaceTask.status.in_(("queued", "running")))
        )
        if active is not None:
            raise BrowserSpaceError(
                "space_task_in_progress", "browser space already has an active task", 409
            )
        instance = await db.get(BrowserInstance, space.browser_instance_id)
        if instance is None:
            raise BrowserSpaceError("not_found", "browser instance not found", 404)
        if not _has_agent_route(instance):
            raise BrowserSpaceError("isolation_unavailable", "managed runtime slot required", 409)
        if space.binding_id:
            binding = await db.get(BrowserBinding, space.binding_id)
            if binding is None or binding.browser_endpoint != instance.endpoint:
                raise BrowserSpaceError("not_found", "browser binding not found", 404)
        bundle = (
            await get_runtime_bundle(db, instance.runtime_bundle_id)
            if instance.runtime_bundle_id
            else None
        )
        definition = (
            browser_capability_service._capability_for(bundle, capability) if bundle else None
        )
        if definition is None:
            raise BrowserSpaceError("unknown_capability", "capability is not in the bundle", 422)
        try:
            browser_capability_service.validate_capability_args(definition, args)
        except BrowserRuntimeError as exc:
            raise BrowserSpaceError(exc.code, "invalid capability arguments", 422) from exc
        task = BrowserSpaceTask(
            space_id=space_id,
            workspace_id=workspace_id,
            request_id=request_id,
            operation_id=str(uuid.uuid4()),
            request_fingerprint=fingerprint,
            capability=capability,
            args={"count": len(args)},
            status=BrowserSpaceTaskStatus.QUEUED.value,
        )
        db.add(task)
        try:
            await db.flush()
            await _append_event(
                db, space_id, BrowserSpaceEventKind.QUEUED, task.id, {"capability": capability}
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            existing = await db.scalar(
                select(BrowserSpaceTask).where(
                    BrowserSpaceTask.space_id == space_id,
                    BrowserSpaceTask.request_id == request_id,
                )
            )
            if existing is not None and existing.request_fingerprint == fingerprint:
                return existing if legacy_call else (existing, False)
            raise BrowserSpaceError(
                "space_task_in_progress", "browser space already has an active task", 409
            ) from exc
        task._created = True
        created = True

    if legacy_call:
        execute = executor is not None
    if not execute:
        return task if legacy_call else (task, created)

    runtime_session = await _new_session(db)
    if runtime_session is None:
        await execute_task(db, task, args, timeout_seconds, executor=executor)
    else:
        try:
            runtime_task = await runtime_session.get(BrowserSpaceTask, task.id)
            if runtime_task is None:
                raise BrowserSpaceError("not_found", "browser space task not found", 404)
            await execute_task(
                runtime_session,
                runtime_task,
                args,
                timeout_seconds,
                executor=executor,
            )
        finally:
            await runtime_session.close()
        await db.refresh(task)
    return task if legacy_call else (task, created)


async def execute_task_in_background(
    task_id: str,
    raw_args: dict[str, Any],
    timeout_seconds: int = 60,
    gate: str | None = None,
    gate_authorized: bool = False,
) -> None:
    """Run a committed task after the HTTP response has been sent."""
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as runtime_session:
        task = await runtime_session.get(BrowserSpaceTask, task_id)
        if task is None:
            return
        await execute_task(
            runtime_session,
            task,
            raw_args,
            timeout_seconds,
            executor=CapabilityExecutor(gate, gate_authorized),
        )


async def execute_task(
    db: AsyncSession,
    task: BrowserSpaceTask | None = None,
    raw_args: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
    *,
    executor: BrowserSpaceExecutor | Any | None = None,
    task_id: str | None = None,
    invocation_args: dict[str, Any] | None = None,
) -> BrowserSpaceTask:
    if task is None:
        if task_id is None:
            raise BrowserSpaceError("invalid_request", "task is required", 422)
        task = await db.get(BrowserSpaceTask, task_id)
        if task is None:
            raise BrowserSpaceError("not_found", "browser space task not found", 404)
        raw_args = invocation_args if invocation_args is not None else task.args
    if raw_args is None:
        raw_args = {}

    started_at = datetime.now(UTC)
    claim = await db.execute(
        update(BrowserSpaceTask)
        .where(BrowserSpaceTask.id == task.id)
        .where(BrowserSpaceTask.status == BrowserSpaceTaskStatus.QUEUED.value)
        .where(BrowserSpaceTask.cancel_requested.is_(False))
        .values(
            status=BrowserSpaceTaskStatus.RUNNING.value,
            started_at=started_at,
        )
    )
    if claim.rowcount != 1:
        current = await db.get(BrowserSpaceTask, task.id)
        if current is not None and (
            current.status == BrowserSpaceTaskStatus.QUEUED.value and current.cancel_requested
        ):
            await _finish_task(db, current.id, BrowserSpaceTaskStatus.CANCELLED)
            await db.refresh(current)
        return current or task
    await db.refresh(task)
    space = await db.get(BrowserSpace, task.space_id)
    if space is None:
        raise BrowserSpaceError("not_found", "browser space not found", 404)
    space.status = BrowserSpaceStatus.RUNNING.value
    space.revision += 1
    await _append_event(db, task.space_id, BrowserSpaceEventKind.STARTED, task.id, {})
    await db.commit()
    instance = await db.get(BrowserInstance, space.browser_instance_id)
    if instance is None:
        await _finish_task(
            db,
            task.id,
            BrowserSpaceTaskStatus.FAILED,
            error_code="isolation_unavailable",
            error_message="browser instance is unavailable",
        )
        raise BrowserSpaceError("isolation_unavailable", "browser instance is unavailable", 409)
    # The runtime adapter owns its own session and runs after task-state
    # persistence has committed; never hold this transaction over agent I/O.
    await db.commit()
    runner = executor or CapabilityExecutor()
    managed_runtime = isinstance(runner, CapabilityExecutor)
    try:
        invocation = (
            runner(instance, task.capability, raw_args)
            if callable(runner)
            else runner.execute(
                instance=instance,
                capability=task.capability,
                args=raw_args,
                timeout_seconds=timeout_seconds,
                task_id=task.id,
            )
        )
        # A local transport cancellation is not proof of remote browser cleanup.
        # Only in-process executors can acknowledge cancellation by unwinding.
        execution = asyncio.ensure_future(invocation)
        if not managed_runtime:
            _execution_tasks[task.id] = execution
        result = await asyncio.wait_for(execution, timeout=timeout_seconds)
    except TimeoutError:
        await _finish_task(
            db,
            task.id,
            BrowserSpaceTaskStatus.FAILED,
            error_code="timeout",
            error_message="browser capability timed out",
            quarantine=managed_runtime,
        )
    except asyncio.CancelledError:
        if managed_runtime:
            await _finish_task(
                db,
                task.id,
                BrowserSpaceTaskStatus.FAILED,
                error_code="runtime_cleanup_unconfirmed",
                error_message="runtime cleanup could not be confirmed",
                quarantine=True,
            )
        else:
            await _finish_task(db, task.id, BrowserSpaceTaskStatus.CANCELLED)
    except Exception as exc:
        code = getattr(exc, "code", None) or "capability_invocation_failed"
        await _finish_task(
            db,
            task.id,
            BrowserSpaceTaskStatus.FAILED,
            error_code=code,
            error_message="browser capability invocation failed",
            quarantine=managed_runtime
            and code
            not in {
                "slot_not_ready",
                "unknown_capability",
                "invalid_capability_args",
                "host_not_allowed",
                "capability_component_unavailable",
                "gate_not_satisfied",
                "agent_route_unavailable",
                "untrusted_agent_route",
                "isolation_unavailable",
            },
        )
    else:
        await db.refresh(task)
        if task.cancel_requested or task.id in _cancel_requested:
            await _finish_task(db, task.id, BrowserSpaceTaskStatus.CANCELLED)
        else:
            await _finish_task(
                db, task.id, BrowserSpaceTaskStatus.COMPLETED, result=_safe_result(result)
            )
    finally:
        _execution_tasks.pop(task.id, None)
        _cancel_requested.discard(task.id)
    await db.refresh(task)
    return task


async def _finish_task(
    db: AsyncSession,
    task_id: str,
    status: BrowserSpaceTaskStatus,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    quarantine: bool = False,
) -> BrowserSpaceTask | None:
    async with _finish_lock(task_id):
        # Lifecycle writers acquire Space before Task, including cancel/close.
        space_id = await db.scalar(
            select(BrowserSpaceTask.space_id).where(BrowserSpaceTask.id == task_id)
        )
        if space_id is None:
            return None
        await db.scalar(select(BrowserSpace).where(BrowserSpace.id == space_id).with_for_update())
        task = await db.scalar(
            select(BrowserSpaceTask)
            .where(BrowserSpaceTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            return None
        if task.status in {
            BrowserSpaceTaskStatus.COMPLETED.value,
            BrowserSpaceTaskStatus.FAILED.value,
            BrowserSpaceTaskStatus.CANCELLED.value,
        }:
            return task
        task.status = status.value
        task.result = result
        task.error_code = error_code[:64] if error_code else None
        task.error_message = _safe_error(error_message) if error_message else None
        task.finished_at = datetime.now(UTC)
        space = await db.get(BrowserSpace, task.space_id)
        if space is not None and space.status != BrowserSpaceStatus.CLOSED.value:
            space.status = (
                BrowserSpaceStatus.ERROR.value
                if status == BrowserSpaceTaskStatus.FAILED
                and error_code
                in {
                    "isolation_unavailable",
                    "slot_not_ready",
                    "agent_route_unavailable",
                    "untrusted_agent_route",
                    "capability_component_unavailable",
                }
                else BrowserSpaceStatus.IDLE.value
            )
            space.last_error_code = error_code if status == BrowserSpaceTaskStatus.FAILED else None
            if quarantine:
                space.status = BrowserSpaceStatus.ERROR.value
                space.last_error_code = "runtime_cleanup_unconfirmed"
            space.revision += 1
        kind = {
            BrowserSpaceTaskStatus.COMPLETED: BrowserSpaceEventKind.COMPLETED,
            BrowserSpaceTaskStatus.FAILED: BrowserSpaceEventKind.FAILED,
            BrowserSpaceTaskStatus.CANCELLED: BrowserSpaceEventKind.CANCELLED,
        }[status]
        payload = (
            {"result": result}
            if status == BrowserSpaceTaskStatus.COMPLETED
            else {
                "error_code": error_code,
                "error_message": _safe_error(error_message) if error_message else None,
            }
        )
        await _append_event(db, task.space_id, kind, task.id, payload)
        await db.commit()
        return task


async def cancel_task(
    db: AsyncSession,
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity | None = None,
    executor: BrowserSpaceExecutor | None = None,
) -> BrowserSpaceTask | None:
    space = await get_space(db, workspace_id, space_id, identity, for_update=True)
    task = await db.scalar(
        select(BrowserSpaceTask)
        .where(BrowserSpaceTask.space_id == space.id)
        .where(BrowserSpaceTask.status.in_(("queued", "running")))
        .order_by(BrowserSpaceTask.created_at.desc())
    )
    if task is None:
        terminal = await get_latest_task(db, space.id, active_only=False)
        if terminal is not None:
            return terminal
        raise BrowserSpaceError("no_active_task", "browser space has no active task to cancel", 409)
    requested = await db.execute(
        update(BrowserSpaceTask)
        .where(BrowserSpaceTask.id == task.id)
        .where(BrowserSpaceTask.cancel_requested.is_(False))
        .where(BrowserSpaceTask.status.in_(("queued", "running")))
        .values(cancel_requested=True)
    )
    if requested.rowcount:
        _cancel_requested.add(task.id)
        await _append_event(db, space.id, BrowserSpaceEventKind.CANCEL_REQUESTED, task.id, {})
    await db.commit()
    if executor is not None:
        if not await executor.cancel(task.id):
            raise BrowserSpaceError(
                "cancellation_unavailable", "executor did not acknowledge cancellation", 409
            )
        await db.refresh(task)
        return task

    execution_task = _execution_tasks.get(task.id)
    if execution_task is not None and execution_task is not asyncio.current_task():
        execution_task.cancel()
        try:
            await execution_task
        except asyncio.CancelledError:
            pass
        await db.refresh(task)
        return task

    await db.refresh(task)
    return task


# API-facing name retained as a thin alias; cancellation always targets the
# current task lease owned by the requested Space.
cancel_space = cancel_task


async def close_space(
    db: AsyncSession,
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity | None = None,
    executor: BrowserSpaceExecutor | None = None,
) -> BrowserSpace:
    space = await get_space(db, workspace_id, space_id, identity, for_update=True)
    if space.status == BrowserSpaceStatus.CLOSED.value:
        return space
    if space.last_error_code == "runtime_cleanup_unconfirmed":
        raise BrowserSpaceError("runtime_cleanup_unconfirmed", "runtime slot is quarantined", 409)
    if (
        await db.scalar(
            select(BrowserSpaceTask.id)
            .where(BrowserSpaceTask.space_id == space.id)
            .where(BrowserSpaceTask.status.in_(("queued", "running")))
        )
        is not None
    ):
        await cancel_task(db, workspace_id, space_id, identity, executor)
        space = await get_space(db, workspace_id, space_id, identity)
        if (
            await db.scalar(
                select(BrowserSpaceTask.id)
                .where(BrowserSpaceTask.space_id == space.id)
                .where(BrowserSpaceTask.status.in_(("queued", "running")))
            )
            is not None
        ):
            raise BrowserSpaceError(
                "cancellation_pending",
                "browser space cannot close until its active task is cancelled",
                409,
            )
    if executor is not None:
        await executor.release(space.id)
    space.status = BrowserSpaceStatus.CLOSED.value
    space.revision += 1
    await _append_event(
        db, space.id, BrowserSpaceEventKind.CANCELLED, None, {"reason": "space_closed"}
    )
    await db.commit()
    await db.refresh(space)
    return space


async def list_events(
    db: AsyncSession,
    workspace_id: str,
    space_id: str,
    identity: RequestIdentity | None = None,
    after_sequence: int = 0,
    limit: int = 100,
) -> list[BrowserSpaceEvent]:
    await get_space(db, workspace_id, space_id, identity)
    if after_sequence < 0 or limit < 1 or limit > 100:
        raise BrowserSpaceError("invalid_pagination", "invalid event pagination", 422)
    result = await db.execute(
        select(BrowserSpaceEvent)
        .where(BrowserSpaceEvent.space_id == space_id)
        .where(BrowserSpaceEvent.sequence > after_sequence)
        .order_by(BrowserSpaceEvent.sequence.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
