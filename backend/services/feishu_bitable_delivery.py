"""Fixed-host Feishu Bitable client.  Errors never include response bodies or tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import commit_session, rollback_session
from backend.models.delivery_connection import DeliveryAttempt

FEISHU_API_HOST = "https://open.feishu.cn"
_DELIVERY_ATTEMPT_LEASE = timedelta(minutes=5)


def _pending_attempt_is_stale(attempt: DeliveryAttempt) -> bool:
    updated_at = attempt.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at >= _DELIVERY_ATTEMPT_LEASE


def _mark_attempt_pending(
    attempt: DeliveryAttempt,
    *,
    workflow_run_id: str,
    evidence_digest: str | None,
    field_map: dict[str, str],
) -> None:
    attempt.workflow_run_id = workflow_run_id
    attempt.evidence_digest = evidence_digest
    attempt.field_map = field_map
    attempt.status = "pending"
    attempt.error_code = None
    attempt.updated_at = datetime.now(timezone.utc)


class FeishuDeliveryError(RuntimeError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def _response_object(response: httpx.Response, *, failure_kind: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise FeishuDeliveryError("invalid_response") from exc
    if not isinstance(data, dict):
        raise FeishuDeliveryError("invalid_response")
    if response.status_code == 429:
        raise FeishuDeliveryError("rate_limited")
    if response.status_code in {401, 403}:
        raise FeishuDeliveryError("permission_denied")
    if response.status_code >= 500:
        raise FeishuDeliveryError("service_unavailable")
    if response.status_code >= 400 or data.get("code", 0) != 0:
        raise FeishuDeliveryError(failure_kind)
    return data


async def _tenant_token(app_id: str, app_secret: str) -> str:
    try:
        async with httpx.AsyncClient(base_url=FEISHU_API_HOST, timeout=15.0) as client:
            response = await client.post(
                "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
    except httpx.TimeoutException as exc:
        raise FeishuDeliveryError("timeout") from exc
    except httpx.HTTPError as exc:
        raise FeishuDeliveryError("network_error") from exc
    data = _response_object(response, failure_kind="authentication_failed")
    if (
        not isinstance(data.get("tenant_access_token"), str)
    ):
        raise FeishuDeliveryError("invalid_response")
    return data["tenant_access_token"]


async def probe_bitable(connection: Any, app_token: str, table_id: str) -> dict[str, Any]:
    token = await _tenant_token(connection.app_id, connection.app_secret)
    try:
        async with httpx.AsyncClient(base_url=FEISHU_API_HOST, timeout=15.0) as client:
            response = await client.get(
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.TimeoutException as exc:
        raise FeishuDeliveryError("timeout") from exc
    except httpx.HTTPError as exc:
        raise FeishuDeliveryError("network_error") from exc
    data = _response_object(response, failure_kind="target_unavailable")
    payload = data.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
        raise FeishuDeliveryError("invalid_response")
    return {"ok": True, "field_count": len(payload.get("items", []))}


async def create_record(
    connection: Any, app_token: str, table_id: str, fields: dict[str, Any]
) -> str:
    token = await _tenant_token(connection.app_id, connection.app_secret)
    try:
        async with httpx.AsyncClient(base_url=FEISHU_API_HOST, timeout=15.0) as client:
            response = await client.post(
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                headers={"Authorization": f"Bearer {token}"},
                json={"records": [{"fields": fields}]},
            )
    except httpx.TimeoutException as exc:
        raise FeishuDeliveryError("timeout") from exc
    except httpx.HTTPError as exc:
        raise FeishuDeliveryError("network_error") from exc
    data = _response_object(response, failure_kind="delivery_failed")
    payload = data.get("data")
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if (
        not records
        or not isinstance(records[0], dict)
        or not isinstance(records[0].get("record_id"), str)
    ):
        raise FeishuDeliveryError("invalid_response")
    return records[0]["record_id"]


async def deliver_record_once(
    session: AsyncSession,
    *,
    connection: Any,
    app_token: str,
    table_id: str,
    record_id: str,
    workflow_run_id: str,
    evidence_digest: str | None,
    fields: dict[str, Any],
    field_map: dict[str, str],
) -> DeliveryAttempt:
    """Persist the target/Record idempotency key before making an outbound call."""
    if connection.provider != "feishu_bitable":
        raise FeishuDeliveryError("provider_mismatch")
    # The Record and workflow state must be durable before an external write.
    await commit_session(session)
    query = select(DeliveryAttempt).where(
        DeliveryAttempt.connection_id == connection.id,
        DeliveryAttempt.app_token == app_token,
        DeliveryAttempt.table_id == table_id,
        DeliveryAttempt.record_id == record_id,
    ).with_for_update()
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing and existing.status == "succeeded":
        if existing.evidence_digest != evidence_digest or existing.field_map != field_map:
            raise FeishuDeliveryError("idempotency_conflict")
        return existing
    if existing and existing.status == "pending" and not _pending_attempt_is_stale(existing):
        raise FeishuDeliveryError("delivery_in_progress")
    attempt = existing or DeliveryAttempt(
        connection_id=connection.id,
        app_token=app_token,
        table_id=table_id,
        record_id=record_id,
    )
    _mark_attempt_pending(
        attempt,
        workflow_run_id=workflow_run_id,
        evidence_digest=evidence_digest,
        field_map=field_map,
    )
    if existing is None:
        session.add(attempt)
    try:
        await session.flush()
        await commit_session(session)
    except IntegrityError:
        await rollback_session(session)
        winner = (await session.execute(query)).scalar_one_or_none()
        if winner is None:
            raise
        if winner.status == "succeeded":
            if winner.evidence_digest != evidence_digest or winner.field_map != field_map:
                raise FeishuDeliveryError("idempotency_conflict")
            return winner
        if winner.status == "pending" and not _pending_attempt_is_stale(winner):
            raise FeishuDeliveryError("delivery_in_progress")
        attempt = winner
        _mark_attempt_pending(
            attempt,
            workflow_run_id=workflow_run_id,
            evidence_digest=evidence_digest,
            field_map=field_map,
        )
        await session.flush()
        await commit_session(session)
    try:
        attempt.remote_record_id = await create_record(connection, app_token, table_id, fields)
        attempt.status = "succeeded"
        attempt.error_code = None
    except FeishuDeliveryError as exc:
        attempt.status = "failed"
        attempt.error_code = exc.kind
        await commit_session(session)
        raise
    await commit_session(session)
    return attempt
