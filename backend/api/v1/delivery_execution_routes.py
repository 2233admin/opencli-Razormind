"""Scoped, redacted Studio control plane for frozen delivery execution."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.delivery_authorization_routes import _scoped_access
from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.delivery_execution import (
    DeliveryExecutionCreateV1,
    DeliveryExecutionListV1,
    DeliveryExecutionReadV1,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import WorkspacePermission
from backend.workflow.delivery_execution import (
    DeliveryExecutionConflictError,
    cancel_delivery_execution,
    execute_delivery,
    get_delivery_execution,
    list_delivery_executions,
    reconcile_delivery_execution,
)

router = APIRouter(tags=["delivery-execution"])
_PATH = (
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
    "/runs/{run_id}/delivery-executions"
)


@router.post(
    _PATH,
    response_model=ApiResponse[DeliveryExecutionReadV1],
    status_code=status.HTTP_201_CREATED,
)
async def execute(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: DeliveryExecutionCreateV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryExecutionReadV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.APPROVE_ACTIONS,
    )
    try:
        result = await execute_delivery(db, scope=scope, decision_id=body.decision_id)
        await db.commit()
        return ApiResponse.ok(result)
    except DeliveryExecutionConflictError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(_PATH, response_model=ApiResponse[DeliveryExecutionListV1])
async def list_executions(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryExecutionListV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        return ApiResponse.ok(
            await list_delivery_executions(db, scope=scope, cursor=cursor, limit=limit)
        )
    except (DeliveryExecutionConflictError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Invalid delivery execution cursor",
        ) from exc


@router.get(f"{_PATH}/{{execution_id}}", response_model=ApiResponse[DeliveryExecutionReadV1])
async def read_execution(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryExecutionReadV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    try:
        return ApiResponse.ok(
            await get_delivery_execution(db, scope=scope, execution_id=execution_id)
        )
    except DeliveryExecutionConflictError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    f"{_PATH}/{{execution_id}}/cancel",
    response_model=ApiResponse[DeliveryExecutionReadV1],
)
async def cancel_execution(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryExecutionReadV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.APPROVE_ACTIONS,
    )
    try:
        result = await cancel_delivery_execution(db, scope=scope, execution_id=execution_id)
        await db.commit()
        return ApiResponse.ok(result)
    except DeliveryExecutionConflictError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    f"{_PATH}/{{execution_id}}/reconcile",
    response_model=ApiResponse[DeliveryExecutionReadV1],
)
async def reconcile_execution(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryExecutionReadV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.APPROVE_ACTIONS,
    )
    try:
        result = await reconcile_delivery_execution(db, scope=scope, execution_id=execution_id)
        await db.commit()
        return ApiResponse.ok(result)
    except DeliveryExecutionConflictError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
