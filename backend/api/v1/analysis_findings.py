"""Authenticated run-scoped routes for durable Analysis Findings."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Never

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analysis_runtime import QuestDBSnapshotRuntime
from backend.api.v1.analysis_snapshots import get_analysis_snapshot_runtime
from backend.database import get_db
from backend.schemas.analysis_finding import AnalysisFindingCreateV1, AnalysisFindingV1
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspaceAccess,
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services.analysis_finding_service import (
    AnalysisFindingError,
    AnalysisFindingErrorCode,
    create_finding,
    get_finding,
    list_findings,
)
from backend.services.analysis_snapshot_service import (
    AnalysisSnapshotError,
    AnalysisSnapshotErrorCode,
    AnalysisSnapshotScope,
    resolve_scope,
)


class _BoundedValidationRoute(APIRoute):
    """Prevent rejected request content from being reflected to clients."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "analysis_finding_request_invalid",
                ) from exc

        return bounded_handler


router = APIRouter(
    tags=["analysis-findings"],
    route_class=_BoundedValidationRoute,
)
_BASE_ROUTE = (
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
    "/runs/{run_id}/analysis-findings"
)

_ERROR_STATUS = {
    AnalysisFindingErrorCode.SNAPSHOT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisFindingErrorCode.SNAPSHOT_NOT_COMPLETED: status.HTTP_409_CONFLICT,
    AnalysisFindingErrorCode.SNAPSHOT_EXPIRED: status.HTTP_410_GONE,
    AnalysisFindingErrorCode.SELECTOR_UNSUPPORTED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisFindingErrorCode.SELECTOR_KEY_REQUIRED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisFindingErrorCode.SELECTOR_KEY_UNSUPPORTED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisFindingErrorCode.SELECTED_RESULT_UNAVAILABLE: status.HTTP_409_CONFLICT,
    AnalysisFindingErrorCode.NARRATIVE_EMPTY: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AnalysisFindingErrorCode.SUMMARY_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    AnalysisFindingErrorCode.FINDING_NOT_FOUND: status.HTTP_404_NOT_FOUND,
}

_SCOPE_ERROR_STATUS = {
    AnalysisSnapshotErrorCode.PROJECT_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.WORKFLOW_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.RUN_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    AnalysisSnapshotErrorCode.RUN_NOT_COMPLETED: status.HTTP_409_CONFLICT,
}


def _raise_http_error(exc: AnalysisFindingError) -> Never:
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
        raise HTTPException(_SCOPE_ERROR_STATUS[exc.code], exc.code.value) from exc
    return scope, access


@router.post(
    _BASE_ROUTE,
    response_model=ApiResponse[AnalysisFindingV1],
    status_code=status.HTTP_201_CREATED,
)
async def create_analysis_finding(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: AnalysisFindingCreateV1,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    runtime: QuestDBSnapshotRuntime = Depends(get_analysis_snapshot_runtime),
) -> ApiResponse[AnalysisFindingV1]:
    scope, access = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.CREATE_ANALYSIS_FINDINGS,
    )
    try:
        finding = await create_finding(
            db,
            scope=scope,
            author_user_id=access.user_id,
            body=body,
            runtime=runtime,
        )
    except AnalysisFindingError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(finding)


@router.get(
    _BASE_ROUTE,
    response_model=ApiResponse[list[AnalysisFindingV1]],
)
async def list_analysis_findings(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[list[AnalysisFindingV1]]:
    scope, _ = await _scoped_access(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
        identity=identity,
        permission=WorkspacePermission.READ,
    )
    return ApiResponse.ok(await list_findings(db, scope=scope))


@router.get(
    f"{_BASE_ROUTE}/{{finding_id}}",
    response_model=ApiResponse[AnalysisFindingV1],
)
async def read_analysis_finding(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    finding_id: str,
    db: AsyncSession = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
) -> ApiResponse[AnalysisFindingV1]:
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
        finding = await get_finding(db, scope=scope, finding_id=finding_id)
    except AnalysisFindingError as exc:
        _raise_http_error(exc)
    return ApiResponse.ok(finding)


__all__ = ["router"]
