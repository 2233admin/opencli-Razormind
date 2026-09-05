"""Post-commit, claim-before-send delivery of Admin III collection commands."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models.iii_collection import (
    IIICollectionAttemptV1,
    IIICollectionCommandV1,
    IIICollectionOutboundV1,
)
from backend.workflow.iii_collection_store import _attempt_and_outbound


class IIITriggerUnsentError(RuntimeError):
    """III was not invoked (local configuration or process-spawn failure)."""


class IIIBridgeUnavailableError(RuntimeError):
    """III may have received work, but no durable bridge observation arrived."""


def _now() -> datetime:
    return datetime.now(UTC)


def collector_trigger_payload(
    command: IIICollectionCommandV1, attempt: IIICollectionAttemptV1
) -> dict:
    """Build the one supported collector invocation with immutable Admin correlation."""

    return {
        **command.collector_payload,
        "task_id": attempt.task_id,
        "trace_id": attempt.trace_id,
        "admin_collection": {
            "version": "v1",
            "workspace_id": command.workspace_id,
            "project_id": command.project_id,
            "workflow_id": command.workflow_id,
            "studio_workflow_version_id": command.studio_workflow_version_id,
            "run_id": command.run_id,
            "node_id": command.node_id,
            "command_id": command.id,
            "attempt_id": attempt.id,
            "attempt_number": attempt.attempt_number,
            "task_id": attempt.task_id,
            "trace_id": attempt.trace_id,
            "source_id": command.odp_source_id,
            "source_binding_id": command.source_binding_id,
            "source_binding_revision_id": command.source_binding_revision_id,
            "source_binding_revision_number": command.source_binding_revision_number,
            "payload_sha256": command.payload_sha256,
        },
    }


def _trigger_options(iii_url: str | None) -> list[str]:
    """Translate the only supported III 0.19 transport URL into CLI arguments."""

    if not iii_url:
        return []
    try:
        parsed = urlparse(iii_url)
        port = parsed.port or 49134
    except ValueError as exc:
        raise IIITriggerUnsentError("III URL has an invalid port") from exc
    if (
        parsed.scheme != "ws"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise IIITriggerUnsentError(
            "III URL must be a root ws endpoint without userinfo, query, or fragment"
        )
    return ["--address", parsed.hostname, "--port", str(port)]


async def invoke_iii_collection(payload: dict, *, function_id: str) -> None:
    """Use III 0.19's public direct trigger; its return is never a collection result."""

    settings = get_settings()
    if not settings.iii_lifecycle_url:
        raise IIITriggerUnsentError("III lifecycle callback is not configured")
    environment = dict(os.environ)
    trigger_options = _trigger_options(settings.iii_url)
    environment["ADMIN_III_LIFECYCLE_URL"] = settings.iii_lifecycle_url
    if settings.iii_lifecycle_token:
        environment["ADMIN_III_LIFECYCLE_TOKEN"] = settings.iii_lifecycle_token
    invocation_payload = json.dumps(
        {
            "admin_command_json": json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        process = await asyncio.create_subprocess_exec(
            settings.iii_cli_path,
            "trigger",
            *trigger_options,
            function_id,
            "--json",
            invocation_payload,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise IIITriggerUnsentError("III trigger could not be started") from exc
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.iii_trigger_timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise IIIBridgeUnavailableError("III bridge outcome is unknown") from exc
    if process.returncode != 0:
        raise IIIBridgeUnavailableError("III bridge outcome is unknown") from RuntimeError(
            (stderr or b"").decode(errors="replace")
        )


async def _claim_outbound(db: AsyncSession, outbound: IIICollectionOutboundV1) -> bool:
    settings = get_settings()
    now = _now()
    # The lease outlives the local trigger timeout, so an expired claim has no
    # still-running local trigger and can safely redeliver the same attempt.
    lease_seconds = max(
        settings.iii_dispatch_lease_seconds,
        settings.iii_trigger_timeout_seconds + 1,
    )
    claimed = await db.execute(
        update(IIICollectionOutboundV1)
        .where(
            IIICollectionOutboundV1.id == outbound.id,
            or_(
                IIICollectionOutboundV1.state.in_(("pending", "bridge_unavailable")),
                IIICollectionOutboundV1.state == "dispatching",
            ),
            IIICollectionOutboundV1.cancelled_at.is_(None),
            IIICollectionOutboundV1.available_at <= now,
        )
        .values(
            state="dispatching",
            available_at=now + timedelta(seconds=lease_seconds),
            last_error=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    await db.refresh(outbound)
    return claimed.rowcount == 1


async def _finish_claim(
    db: AsyncSession,
    *,
    outbound: IIICollectionOutboundV1,
    state: str,
    error: str | None,
    definitely_unsent: bool,
) -> IIICollectionOutboundV1:
    await db.refresh(outbound)
    outbound.dispatch_count += 1
    if outbound.cancel_requested_at is not None:
        outbound.state = "cancel_requested"
    elif outbound.state == "dispatching":
        outbound.state = state
    outbound.last_error = error
    if definitely_unsent:
        outbound.available_at = _now()
    else:
        outbound.dispatched_at = _now()
    await db.commit()
    await db.refresh(outbound)
    return outbound


async def dispatch_collection_attempt(
    db: AsyncSession, *, command: IIICollectionCommandV1
) -> IIICollectionOutboundV1:
    """Atomically claim one eligible outbox row before one external III call."""

    attempt, outbound = await _attempt_and_outbound(db, command.id)
    if not await _claim_outbound(db, outbound):
        return outbound
    payload = collector_trigger_payload(command, attempt)
    try:
        await invoke_iii_collection(payload, function_id=command.collector_function_id)
    except IIITriggerUnsentError:
        return await _finish_claim(
            db,
            outbound=outbound,
            state="pending",
            error="III trigger is unavailable before dispatch",
            definitely_unsent=True,
        )
    except IIIBridgeUnavailableError:
        return await _finish_claim(
            db,
            outbound=outbound,
            state="bridge_unavailable",
            error="III bridge outcome is unknown",
            definitely_unsent=False,
        )
    # A synchronous CLI return proves neither admission nor collection completion.
    return await _finish_claim(
        db,
        outbound=outbound,
        state="submitted_to_iii",
        error=None,
        definitely_unsent=False,
    )
