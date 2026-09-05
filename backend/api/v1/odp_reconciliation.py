"""Scoped Admin proxy for ODP record reconciliation.

The browser supplies only a fixed reconciliation mode, exact event keys, or an
opaque page cursor. All ODP scope and predicates come from the Admin-owned III
command and current attempt ledger; this route never opens an ODP database
connection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.iii_collections import _scoped_run
from backend.database import get_db
from backend.models.iii_collection import IIICollectionAttemptV1
from backend.odp.query_client import (
    OdpQueryRejected,
    OdpQueryUnavailable,
    OdpReconciliationDelegation,
    build_attempt_page_request,
    post_reconciliation_query,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.workflow.iii_collection_store import (
    IIICollectionNotFoundError,
    get_scoped_command,
)

router = APIRouter(tags=["odp-reconciliation"])

ReconciliationMode = Literal["exact", "attempt_page", "dlq"]
_DELEGATION_TTL = timedelta(minutes=1)


async def _ledger_delegation(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    command_id: str,
    mode: ReconciliationMode,
) -> OdpReconciliationDelegation:
    """Resolve the only authority permitted to form an ODP query scope."""

    scope, _, _ = await _scoped_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    try:
        command = await get_scoped_command(db, scope=scope, command_id=command_id)
    except IIICollectionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection command not found") from exc
    attempt = (
        await db.execute(
            select(IIICollectionAttemptV1)
            .where(IIICollectionAttemptV1.command_id == command.id)
            .order_by(IIICollectionAttemptV1.attempt_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if attempt is None or attempt.trace_id != command.trace_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "ODP reconciliation is unavailable")
    try:
        task_id = UUID(attempt.task_id)
        trace_id = UUID(attempt.trace_id)
        source_id = UUID(command.odp_source_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "ODP reconciliation is unavailable") from exc
    batch_id = uuid5(
        NAMESPACE_URL,
        f"opencli-admin/workflow/{workflow_id}/run/{run_id}/batch/{task_id}",
    )
    return OdpReconciliationDelegation(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        batch_id=str(batch_id),
        attempt_id=attempt.id,
        task_id=task_id,
        trace_id=trace_id,
        allowed_source_ids=(source_id,),
        allowed_modes=(mode,),
        expires_at=datetime.now(UTC) + _DELEGATION_TTL,
    )


@router.get(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/iii-collections/{command_id}/odp-reconciliation/{mode}"
    ),
    response_model=ApiResponse[dict[str, Any]],
)
async def reconcile_iii_collection_odp(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    command_id: str,
    mode: ReconciliationMode,
    event_id: list[str] = Query(default=[]),
    cursor: str | None = Query(default=None, max_length=4096),
    page_size: int | None = Query(default=None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[dict[str, Any]]:
    """Read one scoped attempt page through the internal ODP reader."""
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, WorkspacePermission.READ)
    if mode != "attempt_page":
        # B1's immutable expected-key ledger has not landed. Exact/DLQ
        # reconciliation therefore has no server-derived key set and must not
        # turn browser-supplied keys into an ODP predicate.
        raise HTTPException(status.HTTP_409_CONFLICT, "ODP reconciliation is unavailable")

    delegation = await _ledger_delegation(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        command_id=command_id,
        mode=mode,
    )
    try:
        if event_id:
            raise OdpQueryRejected("ODP reconciliation request was rejected")
        request = build_attempt_page_request(
            delegation,
            cursor=cursor,
            page_size=page_size,
        )
        return ApiResponse.ok(await post_reconciliation_query(request))
    except OdpQueryRejected as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "ODP reconciliation request was rejected",
        ) from exc
    except OdpQueryUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ODP reconciliation is unavailable",
        ) from exc
