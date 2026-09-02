"""Authenticated run-scoped routes for disposable Analysis Snapshots."""

from __future__ import annotations

from typing import Never

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analysis_runtime import (
    QuestDBSnapshotRuntime,
    RuntimeCapabilityReasonCode,
    RuntimeCapabilityState,
    RuntimeCapabilityStatus,
)
from backend.config import get_settings
from backend.database import get_db
from backend.schemas.analysis_snapshot import (
    AnalysisSnapshotCapabilityV1,
    AnalysisSnapshotPreviewV1,
    AnalysisSnapshotRangeV1,
    AnalysisSnapshotReceiptV1,
    AnalysisSnapshotSummaryV1,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspaceAccess,
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services.analysis_snapshot_service import (
    AnalysisSnapshotError,
    AnalysisSnapshotErrorCode,
    AnalysisSnapshotScope,
    build_projection,
    create_snapshot,
    get_receipt,
    list_receipts,
    read_summary,
    resolve_scope,
)

router = APIRouter(tags=["analysis-snapshots"])
_BASE_ROUTE = (
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
    "/runs/{run_id}/analysis-snapshots"
)

_ERROR_STATUS = {
    AnalysisSnapshotErrorCode.PROJECT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.WORKFLOW_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.RUN_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.RUN_NOT_COMPLETED: status.HTTP_409_CONFLICT,
    AnalysisSnapshotErrorCode.RANGE_OUTSIDE_RUN: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisSnapshotErrorCode.SOURCE_TIMESTAMP_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisSnapshotErrorCode.EMPTY: status.HTTP_409_CONFLICT,
    AnalysisSnapshotErrorCode.RECEIPT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.RECEIPT_NOT_COMPLETED: status.HTTP_409_CONFLICT,
    AnalysisSnapshotErrorCode.RECEIPT_EXPIRED: status.HTTP_410_GONE,
    AnalysisSnapshotErrorCode.SUMMARY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_analysis_snapshot_runtime() -> QuestDBSnapshotRuntime:
    """Construct the optional runtime at request time so tests can replace the boundary."""

    return QuestDBSnapshotRuntime(get_settings())


def _raise_http_error(exc: AnalysisSnapshotError) -> Never:
    raise HTTPException(_ERROR_STATUS[exc.code], exc.code.value) from exc


async def _scoped_access(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    identity: RequestIdentity,
    permission: WorkspacePermission,
) -> tuple[AnalysisSnapshotScope, WorkspaceAccess]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, permission)
    try:
        scope = await resolve_scope(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
        )
    except AnalysisSnapshotError as exc:
        _raise_http_error(exc)
    return scope, access


@router.get(
    f"{_BASE_ROUTE}/capability",
    response_model=ApiResponse[AnalysisSnapshotCapabilityV1],
)
async def read_capability(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    runtime: QuestDBSnapshotRuntime = Depends(get_analysis_snapshot_runtime),
) -> ApiResponse[AnalysisSnapshotCapabilityV1]:
    await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        capability = await runtime.get_status()
    except Exception:  # noqa: BLE001
        capability = RuntimeCapabilityStatus(
            state=RuntimeCapabilityState.UNAVAILABLE,
            reason_code=RuntimeCapabilityReasonCode.CONNECTION_FAILED,
        )
    return ApiResponse.ok(AnalysisSnapshotCapabilityV1.model_validate(capability.model_dump()))


@router.post(
    f"{_BASE_ROUTE}/preview",
    response_model=ApiResponse[AnalysisSnapshotPreviewV1],
)
async def preview_snapshot(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: AnalysisSnapshotRangeV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[AnalysisSnapshotPreviewV1]:
    scope, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        projection = await build_projection(db, scope=scope, source_range=body)
    except AnalysisSnapshotError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(projection.preview)


@router.post(
    _BASE_ROUTE,
    response_model=ApiResponse[AnalysisSnapshotReceiptV1],
    status_code=status.HTTP_201_CREATED,
)
async def export_snapshot(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: AnalysisSnapshotRangeV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    runtime: QuestDBSnapshotRuntime = Depends(get_analysis_snapshot_runtime),
) -> ApiResponse[AnalysisSnapshotReceiptV1]:
    scope, access = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.EXPORT_ANALYSIS,
    )
    try:
        receipt = await create_snapshot(
            db,
            scope=scope,
            source_range=body,
            requested_by_user_id=access.user_id,
            runtime=runtime,
        )
    except AnalysisSnapshotError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(receipt)


@router.get(
    _BASE_ROUTE,
    response_model=ApiResponse[list[AnalysisSnapshotReceiptV1]],
)
async def list_run_snapshots(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[list[AnalysisSnapshotReceiptV1]]:
    scope, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    return ApiResponse.ok(await list_receipts(db, scope=scope))


@router.get(
    f"{_BASE_ROUTE}/{{snapshot_id}}/summary",
    response_model=ApiResponse[AnalysisSnapshotSummaryV1],
)
async def read_snapshot_summary(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    runtime: QuestDBSnapshotRuntime = Depends(get_analysis_snapshot_runtime),
) -> ApiResponse[AnalysisSnapshotSummaryV1]:
    scope, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        summary = await read_summary(
            db,
            scope=scope,
            snapshot_id=snapshot_id,
            runtime=runtime,
        )
    except AnalysisSnapshotError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(summary)


@router.get(
    f"{_BASE_ROUTE}/{{snapshot_id}}",
    response_model=ApiResponse[AnalysisSnapshotReceiptV1],
)
async def read_snapshot_receipt(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[AnalysisSnapshotReceiptV1]:
    scope, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        receipt = await get_receipt(db, scope=scope, snapshot_id=snapshot_id)
    except AnalysisSnapshotError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(AnalysisSnapshotReceiptV1.from_receipt(receipt))


__all__ = ["get_analysis_snapshot_runtime", "router"]
