"""Execution of one frozen delivery authorization through receiver v2."""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.delivery_authorization import (
    DeliveryAuthorizationDecisionV1,
    DeliveryTarget,
    DeliveryTargetRevision,
)
from backend.models.delivery_execution import (
    DeliveryExecution,
    DeliveryExecutionReconciliation,
    DeliveryExecutionResult,
)
from backend.schemas.delivery_execution import (
    DeliveryExecutionAttemptEvidenceV1,
    DeliveryExecutionListV1,
    DeliveryExecutionReadV1,
    DeliveryExecutionReconciliationEvidenceV1,
)
from backend.security.controlled_receiver import (
    ControlledReceiverEndpoint,
    ControlledReceiverSecurityError,
    canonical_hash,
    canonical_json,
    endpoint_config_hash,
    pinned_post,
    request_headers,
    resolve_endpoint,
    verify_receipt,
)
from backend.workflow.delivery_authorization import (
    DeliveryAuthorizationScope,
    _canonical_hash,
    _current_policy,
)

logger = logging.getLogger(__name__)


class DeliveryExecutionConflictError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _payload(decision: DeliveryAuthorizationDecisionV1) -> dict[str, Any]:
    try:
        projection = {
            "schemaVersion": decision.payload_schema_version,
            "claims": decision.selected_claims,
            "manifestHashes": [item["manifestHash"] for item in decision.manifest_set],
        }
    except (KeyError, TypeError) as exc:
        raise DeliveryExecutionConflictError(
            "Frozen authorization payload cannot be reconstructed"
        ) from exc
    if canonical_hash(projection) != decision.payload_hash:
        raise DeliveryExecutionConflictError("Frozen authorization payload hash mismatch")
    return projection


def _binding(decision: DeliveryAuthorizationDecisionV1) -> str:
    return canonical_hash(
        {
            "decisionId": decision.id,
            "decisionHash": decision.decision_hash,
            "targetRevisionId": decision.target_revision_id,
            "targetRevision": decision.target_revision,
            "payloadHash": decision.payload_hash,
            "policyVersion": decision.policy_version,
            "policyHash": decision.policy_hash,
        }
    )


def _read(
    execution: DeliveryExecution,
    results: list[DeliveryExecutionResult],
    reconciliations: list[DeliveryExecutionReconciliation] | None = None,
) -> DeliveryExecutionReadV1:
    return DeliveryExecutionReadV1(
        execution_id=execution.id,
        decision_id=execution.decision_id,
        operation_id=execution.operation_id,
        decision_hash=execution.decision_hash,
        payload_hash=execution.payload_hash,
        state=execution.state,
        outcome=execution.final_outcome,
        attempt_count=len(results),
        attempts=[
            DeliveryExecutionAttemptEvidenceV1(
                attempt_number=result.attempt_number,
                transport=result.transport_classification,
                http_status=result.http_status,
                receipt=result.receipt_classification,
                protocol=result.protocol_classification,
                outcome=result.outcome,
                receipt_id=result.receipt_id,
                receipt_hash=result.receipt_hash,
                observed_at=result.observed_at,
            )
            for result in results
        ],
        reconciliations=[
            DeliveryExecutionReconciliationEvidenceV1(
                outcome=observation.outcome,
                receipt_hash=observation.receipt_hash,
                observed_at=observation.observed_at,
            )
            for observation in reconciliations or []
        ],
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


async def _results(
    db: AsyncSession,
    execution_id: str,
) -> list[DeliveryExecutionResult]:
    statement = (
        select(DeliveryExecutionResult)
        .where(DeliveryExecutionResult.execution_id == execution_id)
        .order_by(DeliveryExecutionResult.attempt_number)
    )
    return list((await db.execute(statement)).scalars())


async def _reconciliations(
    db: AsyncSession, execution_id: str
) -> list[DeliveryExecutionReconciliation]:
    return list(
        (
            await db.execute(
                select(DeliveryExecutionReconciliation)
                .where(DeliveryExecutionReconciliation.execution_id == execution_id)
                .order_by(DeliveryExecutionReconciliation.observed_at)
            )
        ).scalars()
    )


async def _scoped_decision(
    db: AsyncSession,
    scope: DeliveryAuthorizationScope,
    decision_id: str,
    *,
    lock: bool = True,
) -> DeliveryAuthorizationDecisionV1:
    statement = select(DeliveryAuthorizationDecisionV1).where(
        DeliveryAuthorizationDecisionV1.id == decision_id,
        DeliveryAuthorizationDecisionV1.workspace_id == scope.workspace_id,
        DeliveryAuthorizationDecisionV1.project_id == scope.project_id,
        DeliveryAuthorizationDecisionV1.workflow_id == scope.workflow_id,
        DeliveryAuthorizationDecisionV1.studio_workflow_version_id
        == scope.studio_workflow_version_id,
        DeliveryAuthorizationDecisionV1.run_id == scope.run_id,
    )
    decision = await db.scalar(statement.with_for_update() if lock else statement)
    if decision is None:
        raise DeliveryExecutionConflictError("Scoped frozen delivery authorization was not found")
    await _validated_target(db, scope=scope, decision=decision, lock=lock)
    return decision


def _approval(
    decision: DeliveryAuthorizationDecisionV1,
    scope: DeliveryAuthorizationScope,
) -> dict[str, str]:
    authority = {
        "scope": scope.__dict__,
        "actorId": decision.approver_actor_id,
        "principal": decision.approver_principal,
        "capability": decision.approver_capability,
        "policyVersion": decision.approval_policy_version,
    }
    return {
        "policyDecisionId": _canonical_hash(authority),
        "evidenceReference": "workspace-rbac-v1",
        "actorType": decision.approver_actor_type,
        "actorId": decision.approver_actor_id,
        "principal": decision.approver_principal,
        "capability": decision.approver_capability,
        "policyVersion": decision.approval_policy_version,
        "decidedAt": decision.approved_at.isoformat(),
    }


def _decision_binding(
    decision: DeliveryAuthorizationDecisionV1,
    scope: DeliveryAuthorizationScope,
) -> dict[str, Any]:
    return {
        "scope": scope.__dict__,
        "operationId": decision.operation_id,
        "idempotencyKey": decision.idempotency_key,
        "nodeId": decision.node_id,
        "target": {
            "id": decision.target_id,
            "revision": decision.target_revision,
            "endpointIdentity": decision.endpoint_identity,
            "configHash": decision.non_secret_config_hash,
            "policyVersion": decision.policy_version,
            "policyHash": decision.policy_hash,
            "policySnapshot": decision.policy_snapshot,
        },
        "pin": {
            "sequence": decision.pin_sequence,
            "researchRevisionId": decision.research_revision_id,
            "manifestSetHash": decision.manifest_set_hash,
        },
        "claims": decision.selected_claims,
        "manifests": decision.manifest_set,
        "payload": decision.sanitized_payload_manifest,
        "approval": {
            key: value for key, value in _approval(decision, scope).items() if key != "decidedAt"
        },
    }


def _validate_frozen_authority(
    decision: DeliveryAuthorizationDecisionV1,
    target: DeliveryTargetRevision,
    receiver: DeliveryTarget,
    endpoint: ControlledReceiverEndpoint,
    scope: DeliveryAuthorizationScope,
) -> None:
    """Reconstruct every persisted authority component before outbound I/O."""
    projection = _payload(decision)
    payload_manifest = {
        "payloadSchemaVersion": decision.payload_schema_version,
        "payloadReference": decision.payload_reference,
        "payloadHash": decision.payload_hash,
        "sanctionedReferenceHashes": sorted(
            [claim["contentHash"] for claim in decision.selected_claims]
            + [manifest["manifestHash"] for manifest in decision.manifest_set]
        ),
        "redactionProfileVersion": decision.redaction_profile_version,
    }
    current_version, current_snapshot, current_hash = _current_policy()
    if (
        decision.policy_version != current_version
        or decision.policy_snapshot != current_snapshot
        or decision.policy_hash != current_hash
        or decision.sanitized_payload_manifest != payload_manifest
        or (
            target.target_id,
            target.revision,
            target.endpoint_identity,
            target.non_secret_config_hash,
            target.credential_reference,
            target.policy_version,
            target.policy_snapshot,
            target.policy_hash,
        )
        != (
            decision.target_id,
            decision.target_revision,
            decision.endpoint_identity,
            decision.non_secret_config_hash,
            endpoint.credential_reference,
            decision.policy_version,
            decision.policy_snapshot,
            decision.policy_hash,
        )
        or receiver.receiver_identity != endpoint.receiver_identity
        or endpoint_config_hash(endpoint) != decision.non_secret_config_hash
        or _approval(decision, scope)
        != (decision.approval_evidence[0] if len(decision.approval_evidence) == 1 else None)
    ):
        raise DeliveryExecutionConflictError("Frozen delivery authority drifted")
    binding = _decision_binding(decision, scope)
    if (
        _canonical_hash(binding) != decision.binding_hash
        or _canonical_hash(
            {
                "binding": binding,
                "approvalEvidence": decision.approval_evidence,
                "decisionedAt": decision.decisioned_at.isoformat(),
            }
        )
        != decision.decision_hash
        or projection["schemaVersion"] != "delivery-claim-manifest-v1"
    ):
        raise DeliveryExecutionConflictError("Frozen delivery decision integrity drifted")


async def _validated_target(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    decision: DeliveryAuthorizationDecisionV1,
    lock: bool = False,
) -> tuple[DeliveryTargetRevision, ControlledReceiverEndpoint]:
    target = await db.get(
        DeliveryTargetRevision,
        decision.target_revision_id,
        with_for_update=lock,
    )
    receiver = await db.get(DeliveryTarget, decision.target_id, with_for_update=lock)
    if target is None or receiver is None:
        raise DeliveryExecutionConflictError("Frozen delivery target revision is missing")
    try:
        endpoint = resolve_endpoint(target.endpoint_identity, target.credential_reference)
    except ControlledReceiverSecurityError as exc:
        raise DeliveryExecutionConflictError(
            "Controlled receiver registry authority drifted"
        ) from exc
    _validate_frozen_authority(decision, target, receiver, endpoint, scope)
    return target, endpoint


async def _claim(
    db: AsyncSession,
    scope: DeliveryAuthorizationScope,
    decision: DeliveryAuthorizationDecisionV1,
    *,
    allow_existing_pending: bool = False,
) -> tuple[DeliveryExecution, bool]:
    binding = _binding(decision)
    existing = await db.scalar(
        select(DeliveryExecution)
        .where(DeliveryExecution.decision_id == decision.id)
        .with_for_update()
    )
    if existing is not None:
        if existing.execution_binding_hash != binding:
            raise DeliveryExecutionConflictError(
                "Execution binding conflicts with existing frozen decision execution"
            )
        return existing, allow_existing_pending
    execution = DeliveryExecution(
        decision_id=decision.id,
        target_revision_id=decision.target_revision_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        workflow_id=scope.workflow_id,
        studio_workflow_version_id=scope.studio_workflow_version_id,
        run_id=scope.run_id,
        operation_id=decision.operation_id,
        decision_hash=decision.decision_hash,
        payload_hash=decision.payload_hash,
        execution_binding_hash=binding,
        state="pending",
    )
    try:
        async with db.begin_nested():
            db.add(execution)
            await db.flush()
    except OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        await db.rollback()
        existing = await db.scalar(
            select(DeliveryExecution).where(DeliveryExecution.decision_id == decision.id)
        )
        if existing is None or existing.execution_binding_hash != binding:
            raise DeliveryExecutionConflictError(
                "Concurrent execution claim was not recoverable"
            ) from exc
        await db.refresh(existing)
        return existing, False
    except IntegrityError:
        existing = await db.scalar(
            select(DeliveryExecution).where(DeliveryExecution.decision_id == decision.id)
        )
        if existing is None or existing.execution_binding_hash != binding:
            raise DeliveryExecutionConflictError("Concurrent execution claim conflicts")
        return existing, False
    return execution, True


async def _record(
    db: AsyncSession,
    execution: DeliveryExecution,
    *,
    attempt: int,
    transport: str,
    http_status: int | None,
    receipt: str,
    protocol: str,
    outcome: str,
    receipt_id: str | None = None,
    receipt_hash: str | None = None,
) -> DeliveryExecutionResult:
    result = DeliveryExecutionResult(
        execution_id=execution.id,
        attempt_number=attempt,
        transport_classification=transport,
        http_status=http_status,
        receipt_classification=receipt,
        protocol_classification=protocol,
        outcome=outcome,
        receipt_id=receipt_id,
        receipt_hash=receipt_hash,
        observed_at=_now(),
    )
    db.add(result)
    await db.flush()
    return result


def _retry_policy(snapshot: dict[str, Any]) -> tuple[float, int]:
    try:
        timeout = snapshot["timeout"]["perAttemptSeconds"]
        retry = snapshot["retry"]
        delays = retry["backoff"]
        if (
            not isinstance(timeout, (int, float))
            or timeout <= 0
            or retry["maxAttempts"] != 3
            or retry["retryOn"] != ["transport-timeout", "network-error", "http-5xx"]
            or delays["initialDelaySeconds"] != 1
            or delays["multiplier"] != 2
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliveryExecutionConflictError("Frozen delivery retry policy is invalid") from exc
    return float(timeout), 3


async def _before_send_start(*, execution_id: str, attempt: int) -> None:
    """A narrow observability seam between durable reservation and send start."""
    return None


async def execute_delivery(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    decision_id: str,
    _allow_existing_pending: bool = False,
) -> DeliveryExecutionReadV1:
    decision = await _scoped_decision(db, scope, decision_id)
    payload = _payload(decision)
    timeout, max_attempts = _retry_policy(decision.policy_snapshot)
    execution, owns_claim = await _claim(
        db,
        scope,
        decision,
        allow_existing_pending=_allow_existing_pending,
    )
    prior = await _results(db, execution.id)
    if not owns_claim:
        if execution.final_outcome is not None or not execution.lease_token:
            read = _read(execution, prior)
            await db.commit()
            return read
    if execution.final_outcome is not None:
        return _read(execution, prior)
    if execution.lease_token:
        acquired_at = execution.lease_acquired_at
        if acquired_at is not None and acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=UTC)
        if acquired_at is None or acquired_at <= _now() - timedelta(seconds=60):
            reserved = execution.reserved_attempt_number or len(prior) + 1
            if execution.state == "reserved" and execution.send_started_at is None:
                execution.state = "pending"
                (
                    execution.lease_token,
                    execution.lease_acquired_at,
                    execution.reserved_attempt_number,
                ) = None, None, None
                await db.commit()
                return await execute_delivery(
                    db,
                    scope=scope,
                    decision_id=decision_id,
                    _allow_existing_pending=True,
                )
            result = await _record(
                db,
                execution,
                attempt=reserved,
                transport="crash-ambiguous",
                http_status=None,
                receipt="missing",
                protocol="unknown",
                outcome="unknown",
            )
            execution.state, execution.final_outcome, execution.final_result_id = (
                "blocked",
                "unknown",
                result.id,
            )
            (
                execution.lease_token,
                execution.lease_acquired_at,
                execution.send_started_at,
                execution.reserved_attempt_number,
            ) = None, None, None, None
            await db.flush()
        read = _read(execution, await _results(db, execution.id))
        if not owns_claim:
            await db.commit()
        return read
    if execution.cancel_requested_at:
        execution.state, execution.final_outcome = "cancelled", "unknown"
        await db.flush()
        return _read(execution, prior)

    for attempt in range(len(prior) + 1, max_attempts + 1):
        await db.refresh(execution)
        if execution.cancel_requested_at:
            execution.state, execution.final_outcome = "cancelled", "unknown"
            await db.flush()
            return _read(execution, await _results(db, execution.id))
        _, endpoint = await _validated_target(db, scope=scope, decision=decision)
        body = canonical_json(
            {
                "version": "v2",
                "receiverIdentity": endpoint.receiver_identity,
                "operationId": decision.operation_id,
                "decisionHash": decision.decision_hash,
                "payloadHash": decision.payload_hash,
                "payload": payload,
            }
        )
        lease_token = secrets.token_urlsafe(24)
        execution.state = "reserved"
        execution.lease_token = lease_token
        execution.lease_acquired_at = _now()
        execution.send_started_at = None
        execution.reserved_attempt_number = attempt
        await db.commit()  # The pre-send reservation is durable and cancellation-visible.
        try:
            headers = request_headers(
                body=body,
                endpoint=endpoint,
                operation_id=decision.operation_id,
                decision_hash=decision.decision_hash,
                payload_hash=decision.payload_hash,
            )
        except ControlledReceiverSecurityError:
            await db.refresh(execution)
            if execution.final_outcome is not None or execution.lease_token != lease_token:
                return _read(execution, await _results(db, execution.id))
            result = await _record(
                db,
                execution,
                attempt=attempt,
                transport="protocol-error",
                http_status=None,
                receipt="missing",
                protocol="invalid",
                outcome="unknown",
            )
            (
                execution.lease_token,
                execution.lease_acquired_at,
                execution.send_started_at,
                execution.reserved_attempt_number,
            ) = None, None, None, None
            execution.state, execution.final_outcome, execution.final_result_id = (
                "blocked",
                "unknown",
                result.id,
            )
            await db.flush()
            return _read(execution, await _results(db, execution.id))
        await _before_send_start(execution_id=execution.id, attempt=attempt)
        execution = await db.scalar(
            select(DeliveryExecution).where(DeliveryExecution.id == execution.id).with_for_update()
        )
        if execution is None:
            raise DeliveryExecutionConflictError("Delivery execution disappeared before send")
        await db.refresh(execution)
        if execution.final_outcome is not None or execution.lease_token != lease_token:
            return _read(execution, await _results(db, execution.id))
        if execution.cancel_requested_at:
            execution.state, execution.final_outcome = "cancelled", "unknown"
            (
                execution.lease_token,
                execution.lease_acquired_at,
                execution.send_started_at,
                execution.reserved_attempt_number,
            ) = None, None, None, None
            await db.commit()
            return _read(execution, await _results(db, execution.id))
        execution.state, execution.send_started_at = "in-flight", _now()
        await db.commit()  # Cancellation before this locked boundary cannot cause a post.
        (
            transport,
            http_status,
            receipt_classification,
            protocol,
            outcome,
            receipt_id,
            receipt_hash,
        ) = (
            "network-error",
            None,
            "missing",
            "unknown",
            "unknown",
            None,
            None,
        )
        try:
            response = await pinned_post(endpoint, body, headers, timeout_seconds=timeout)
            http_status = response.status_code
            transport = (
                "http-5xx"
                if response.status_code >= 500
                else "http-4xx"
                if response.status_code >= 400
                else "http-success"
                if 200 <= response.status_code < 300
                else "http-other"
            )
            try:
                receipt_value = response.json().get("receipt")
                status = verify_receipt(
                    receipt=receipt_value,
                    endpoint=endpoint,
                    operation_id=decision.operation_id,
                    decision_hash=decision.decision_hash,
                    payload_hash=decision.payload_hash,
                )
                receipt_classification, protocol, outcome = "verified", "v2", status
                receipt_id, receipt_hash = receipt_value["receiptId"], canonical_hash(receipt_value)
            except (ValueError, ControlledReceiverSecurityError, AttributeError):
                receipt_classification = "invalid-or-missing"
                retry = response.status_code >= 500
        except httpx.TimeoutException:
            transport, receipt_classification, retry = "transport-timeout", "missing", True
        except ControlledReceiverSecurityError:
            transport, receipt_classification, protocol = "protocol-error", "missing", "invalid"
        except httpx.HTTPError:
            logger.warning(
                "Controlled receiver delivery transport failed",
                exc_info=True,
            )
            transport, receipt_classification, retry = "network-error", "missing", True
        await db.refresh(execution)
        if execution.final_outcome is not None or execution.lease_token != lease_token:
            return _read(execution, await _results(db, execution.id))
        result = await _record(
            db,
            execution,
            attempt=attempt,
            transport=transport,
            http_status=http_status,
            receipt=receipt_classification,
            protocol=protocol,
            outcome=outcome,
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
        )
        (
            execution.lease_token,
            execution.lease_acquired_at,
            execution.send_started_at,
            execution.reserved_attempt_number,
        ) = None, None, None, None
        if outcome in {"accepted", "rejected"}:
            execution.state, execution.final_outcome, execution.final_result_id = (
                "completed",
                outcome,
                result.id,
            )
            await db.flush()
            return _read(execution, await _results(db, execution.id))
        if execution.cancel_requested_at:
            execution.state, execution.final_outcome, execution.final_result_id = (
                "cancelled",
                "unknown",
                result.id,
            )
            await db.flush()
            return _read(execution, await _results(db, execution.id))
        if not retry or attempt == max_attempts:
            execution.state, execution.final_outcome, execution.final_result_id = (
                "blocked",
                "unknown",
                result.id,
            )
            await db.flush()
            return _read(execution, await _results(db, execution.id))
        await db.commit()
        await asyncio.sleep(1 if attempt == 1 else 2)
    raise AssertionError("delivery retry loop exhausted unexpectedly")


async def get_delivery_execution(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    execution_id: str,
) -> DeliveryExecutionReadV1:
    statement = select(DeliveryExecution).where(
        DeliveryExecution.id == execution_id,
        DeliveryExecution.workspace_id == scope.workspace_id,
        DeliveryExecution.project_id == scope.project_id,
        DeliveryExecution.workflow_id == scope.workflow_id,
        DeliveryExecution.studio_workflow_version_id == scope.studio_workflow_version_id,
        DeliveryExecution.run_id == scope.run_id,
    )
    execution = await db.scalar(statement)
    if execution is None:
        raise DeliveryExecutionConflictError("Scoped delivery execution was not found")
    return _read(
        execution,
        await _results(db, execution.id),
        await _reconciliations(db, execution.id),
    )


def _decode_execution_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        decoded = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True).decode(
            "ascii"
        )
    except (UnicodeEncodeError, ValueError) as exc:
        raise DeliveryExecutionConflictError("Invalid delivery execution cursor") from exc
    if (
        not decoded
        or len(decoded) > 36
        or base64.urlsafe_b64encode(decoded.encode("ascii")).decode("ascii") != cursor
    ):
        raise DeliveryExecutionConflictError("Invalid delivery execution cursor")
    return decoded


async def _read_page(
    db: AsyncSession, rows: list[DeliveryExecution]
) -> list[DeliveryExecutionReadV1]:
    if not rows:
        return []
    results = list(
        (
            await db.execute(
                select(DeliveryExecutionResult)
                .where(DeliveryExecutionResult.execution_id.in_([row.id for row in rows]))
                .order_by(
                    DeliveryExecutionResult.execution_id,
                    DeliveryExecutionResult.attempt_number,
                )
            )
        ).scalars()
    )
    reconciliations = list(
        (
            await db.execute(
                select(DeliveryExecutionReconciliation)
                .where(DeliveryExecutionReconciliation.execution_id.in_([row.id for row in rows]))
                .order_by(
                    DeliveryExecutionReconciliation.execution_id,
                    DeliveryExecutionReconciliation.observed_at,
                )
            )
        ).scalars()
    )
    grouped: dict[str, list[DeliveryExecutionResult]] = {row.id: [] for row in rows}
    reconciliation_groups: dict[str, list[DeliveryExecutionReconciliation]] = {
        row.id: [] for row in rows
    }
    for result in results:
        grouped[result.execution_id].append(result)
    for observation in reconciliations:
        reconciliation_groups[observation.execution_id].append(observation)
    return [_read(row, grouped[row.id], reconciliation_groups[row.id]) for row in rows]


async def list_delivery_executions(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    cursor: str | None = None,
    limit: int = 50,
) -> DeliveryExecutionListV1:
    after = _decode_execution_cursor(cursor)
    page_limit = max(1, min(limit, 200))
    stmt = (
        select(DeliveryExecution)
        .where(
            DeliveryExecution.workspace_id == scope.workspace_id,
            DeliveryExecution.project_id == scope.project_id,
            DeliveryExecution.workflow_id == scope.workflow_id,
            DeliveryExecution.studio_workflow_version_id == scope.studio_workflow_version_id,
            DeliveryExecution.run_id == scope.run_id,
        )
        .order_by(DeliveryExecution.id)
    )
    if after:
        stmt = stmt.where(DeliveryExecution.id > after)
    rows = list((await db.execute(stmt.limit(page_limit + 1))).scalars())
    page = rows[:page_limit]
    next_cursor = (
        base64.urlsafe_b64encode(page[-1].id.encode()).decode()
        if len(rows) > len(page) and page
        else None
    )
    return DeliveryExecutionListV1(
        items=await _read_page(db, page),
        next_cursor=next_cursor,
    )


async def cancel_delivery_execution(
    db: AsyncSession,
    *,
    scope: DeliveryAuthorizationScope,
    execution_id: str,
) -> DeliveryExecutionReadV1:
    statement = (
        select(DeliveryExecution)
        .where(
            DeliveryExecution.id == execution_id,
            DeliveryExecution.workspace_id == scope.workspace_id,
        )
        .with_for_update()
    )
    execution = await db.scalar(statement)
    if execution is None or (
        execution.project_id,
        execution.workflow_id,
        execution.studio_workflow_version_id,
        execution.run_id,
    ) != (
        scope.project_id,
        scope.workflow_id,
        scope.studio_workflow_version_id,
        scope.run_id,
    ):
        raise DeliveryExecutionConflictError("Scoped delivery execution was not found")
    if execution.final_outcome is None:
        execution.cancel_requested_at = _now()
        if execution.state != "in-flight":
            execution.state, execution.final_outcome = "cancelled", "unknown"
        await db.flush()
    return _read(execution, await _results(db, execution.id))


async def reconcile_delivery_execution(
    db: AsyncSession, *, scope: DeliveryAuthorizationScope, execution_id: str
) -> DeliveryExecutionReadV1:
    """Query durable status without resending or changing delivery attempt count."""
    statement = select(DeliveryExecution).where(
        DeliveryExecution.id == execution_id,
        DeliveryExecution.workspace_id == scope.workspace_id,
        DeliveryExecution.project_id == scope.project_id,
        DeliveryExecution.workflow_id == scope.workflow_id,
        DeliveryExecution.studio_workflow_version_id == scope.studio_workflow_version_id,
        DeliveryExecution.run_id == scope.run_id,
    )
    execution = await db.scalar(statement)
    if execution is None:
        raise DeliveryExecutionConflictError("Scoped delivery execution was not found")
    if execution.final_outcome != "unknown":
        return _read(
            execution,
            await _results(db, execution.id),
            await _reconciliations(db, execution.id),
        )
    decision = await _scoped_decision(db, scope, execution.decision_id, lock=False)
    payload = _payload(decision)
    timeout, _ = _retry_policy(decision.policy_snapshot)
    _, endpoint = await _validated_target(db, scope=scope, decision=decision)
    body = canonical_json(
        {
            "version": "v2",
            "receiverIdentity": endpoint.receiver_identity,
            "operationId": decision.operation_id,
            "decisionHash": decision.decision_hash,
            "payloadHash": decision.payload_hash,
            "payload": payload,
        }
    )
    try:
        headers = request_headers(
            body=body,
            endpoint=endpoint,
            operation_id=decision.operation_id,
            decision_hash=decision.decision_hash,
            payload_hash=decision.payload_hash,
        )
        response = await pinned_post(
            endpoint, body, headers, timeout_seconds=timeout, status_query=True
        )
        receipt_value = response.json().get("receipt")
        outcome = verify_receipt(
            receipt=receipt_value,
            endpoint=endpoint,
            operation_id=decision.operation_id,
            decision_hash=decision.decision_hash,
            payload_hash=decision.payload_hash,
        )
    except (
        httpx.HTTPError,
        ControlledReceiverSecurityError,
        ValueError,
        AttributeError,
    ) as exc:
        raise DeliveryExecutionConflictError(
            "Controlled receiver reconciliation remains unknown"
        ) from exc
    await db.refresh(execution)
    if execution.final_outcome != "unknown":
        return _read(
            execution,
            await _results(db, execution.id),
            await _reconciliations(db, execution.id),
        )
    observation = DeliveryExecutionReconciliation(
        execution_id=execution.id,
        receipt_hash=canonical_hash(receipt_value),
        outcome=outcome,
        observed_at=_now(),
    )
    db.add(observation)
    await db.flush()
    execution.state, execution.final_outcome = "completed", outcome
    execution.final_reconciliation_id = observation.id
    await db.flush()
    return _read(
        execution,
        await _results(db, execution.id),
        await _reconciliations(db, execution.id),
    )
