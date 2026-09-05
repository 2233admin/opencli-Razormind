"""Authorized Studio reads for persisted project/run artifacts."""

from __future__ import annotations

from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse
from backend.schemas.project_artifact import ProjectArtifactDetail, ProjectArtifactSummary
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import WorkspacePermission, require_permission
from backend.services import project_artifact_service as service
from backend.services.studio_agent_session_access import resolve_agent_session_workspace

router = APIRouter(tags=["studio-artifacts"])

_BASE_ROUTE = "/workspaces/{workspace_id}/projects/{project_id}/artifacts"


def _raise_artifact_error(exc: service.ProjectArtifactError) -> Never:
    status_code = {
        service.ProjectArtifactErrorCode.PROJECT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        service.ProjectArtifactErrorCode.WORKFLOW_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        service.ProjectArtifactErrorCode.RUN_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        service.ProjectArtifactErrorCode.ARTIFACT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        service.ProjectArtifactErrorCode.ARTIFACT_SCOPE_REQUIRED: status.HTTP_409_CONFLICT,
        service.ProjectArtifactErrorCode.CONTENT_TOO_LARGE: (
            status.HTTP_413_CONTENT_TOO_LARGE
        ),
    }[exc.code]
    raise HTTPException(status_code, exc.code.value) from exc


async def _authorized_scope(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str | None,
    run_id: str | None,
    identity: RequestIdentity,
) -> tuple[service.ProjectArtifactScope, str, str]:
    context = {
        key: value
        for key, value in {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
        }.items()
        if value is not None
    }
    auth_scope = await resolve_agent_session_workspace(
        db,
        identity,
        workspace_id,
        context=context,
    )
    require_permission(auth_scope.access, WorkspacePermission.READ)
    try:
        scope = await service.resolve_scope(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
        )
    except service.ProjectArtifactError as exc:
        _raise_artifact_error(exc)
    return scope, auth_scope.studio_workspace_id or workspace_id, auth_scope.workspace_id


@router.get(
    _BASE_ROUTE,
    response_model=ApiResponse[list[ProjectArtifactSummary]],
)
async def list_project_artifacts(
    workspace_id: str,
    project_id: str,
    workflow_id: str | None = Query(default=None, min_length=1, max_length=255),
    run_id: str | None = Query(default=None, min_length=1, max_length=255),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[list[ProjectArtifactSummary]]:
    try:
        scope, studio_workspace_id, conversation_workspace_id = await _authorized_scope(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            identity=identity,
        )
        data = await service.list_project_artifacts(
            db,
            scope=scope,
            limit=limit,
            offset=offset,
            conversation_workspace_id=conversation_workspace_id,
            studio_workspace_id=studio_workspace_id,
        )
    except service.ProjectArtifactError as exc:
        _raise_artifact_error(exc)
    return ApiResponse.ok(data)


@router.get(
    f"{_BASE_ROUTE}/{{artifact_id}}",
    response_model=ApiResponse[ProjectArtifactDetail],
)
async def read_project_artifact(
    workspace_id: str,
    project_id: str,
    artifact_id: str,
    workflow_id: str | None = Query(default=None, min_length=1, max_length=255),
    run_id: str | None = Query(default=None, min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[ProjectArtifactDetail]:
    try:
        scope, studio_workspace_id, conversation_workspace_id = await _authorized_scope(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            identity=identity,
        )
        data = await service.get_project_artifact(
            db,
            scope=scope,
            artifact_id=artifact_id,
            conversation_workspace_id=conversation_workspace_id,
            studio_workspace_id=studio_workspace_id,
        )
    except service.ProjectArtifactError as exc:
        _raise_artifact_error(exc)
    return ApiResponse.ok(data)


__all__ = ["router"]
