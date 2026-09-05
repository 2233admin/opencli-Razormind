"""Scoped Studio routes for configuring controlled receivers and freezing authorization."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun
from backend.schemas.common import ApiResponse
from backend.schemas.delivery_authorization import (
    DeliveryAuthorizationCreateV1,
    DeliveryAuthorizationListV1,
    DeliveryAuthorizationReadV1,
    DeliveryTargetConfigureV1,
    DeliveryTargetListV1,
    DeliveryTargetReadV1,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.workflow.delivery_authorization import (
    DeliveryAuthorizationConflictError,
    DeliveryAuthorizationScope,
    authorize_delivery,
    configure_delivery_target,
    get_delivery_authorization,
    get_delivery_target,
    list_delivery_authorizations,
    list_delivery_targets,
)
from backend.workflow.research_graph_v2 import actor_evidence

router = APIRouter(tags=["delivery-authorization"])


async def _scope(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
) -> DeliveryAuthorizationScope:
    project = await db.get(StudioProject, project_id)
    if project is None or project.workspace_id != workspace_id or project.archived:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    workflow = await db.get(StudioWorkflow, workflow_id)
    if workflow is None or workflow.project_id != project_id or workflow.archived:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    run = await db.get(WorkflowRun, run_id)
    if run is None or run.workflow_id != workflow_id or run.studio_workflow_version_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    version = await db.get(StudioWorkflowVersion, run.studio_workflow_version_id)
    if version is None or version.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return DeliveryAuthorizationScope(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        studio_workflow_version_id=version.id,
        run_id=run.id,
    )


async def _scoped_access(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    identity: RequestIdentity,
    permission: WorkspacePermission,
) -> tuple[DeliveryAuthorizationScope, str, str]:
    access = await get_workspace_access(db, workspace_id, identity)
    require_permission(access, permission)
    scope = await _scope(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    return scope, access.user_id, identity.subject


@router.post(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-targets",
    response_model=ApiResponse[DeliveryTargetReadV1],
    status_code=status.HTTP_201_CREATED,
)
async def configure_target(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: DeliveryTargetConfigureV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryTargetReadV1]:
    scope, _, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.MANAGE_CONFIGURATION,
    )
    try:
        result = await configure_delivery_target(db, scope=scope, request=body)
        await db.commit()
    except DeliveryAuthorizationConflictError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ApiResponse.ok(result)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-targets",
    response_model=ApiResponse[DeliveryTargetListV1],
)
async def list_targets(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryTargetListV1]:
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
            await list_delivery_targets(db, scope=scope, cursor=cursor, limit=limit)
        )
    except DeliveryAuthorizationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-targets/{target_id}",
    response_model=ApiResponse[DeliveryTargetReadV1],
)
async def read_target(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    target_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryTargetReadV1]:
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
        return ApiResponse.ok(await get_delivery_target(db, scope=scope, target_id=target_id))
    except DeliveryAuthorizationConflictError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-authorizations",
    response_model=ApiResponse[DeliveryAuthorizationReadV1],
    status_code=status.HTTP_201_CREATED,
)
async def create_authorization(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: DeliveryAuthorizationCreateV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryAuthorizationReadV1]:
    scope, user_id, principal = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.APPROVE_ACTIONS,
    )
    try:
        result = await authorize_delivery(
            db,
            scope=scope,
            actor=actor_evidence(
                actor_id=user_id,
                principal=principal,
                capability=WorkspacePermission.APPROVE_ACTIONS.value,
            ),
            request=body,
        )
        await db.commit()
    except (DeliveryAuthorizationConflictError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ApiResponse.ok(result)


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-authorizations",
    response_model=ApiResponse[DeliveryAuthorizationListV1],
)
async def list_authorizations(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryAuthorizationListV1]:
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
            await list_delivery_authorizations(
                db, scope=scope, cursor=cursor, limit=limit
            )
        )
    except DeliveryAuthorizationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/delivery-authorizations/{decision_id}",
    response_model=ApiResponse[DeliveryAuthorizationReadV1],
)
async def read_authorization(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[DeliveryAuthorizationReadV1]:
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
            await get_delivery_authorization(db, scope=scope, decision_id=decision_id)
        )
    except DeliveryAuthorizationConflictError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
