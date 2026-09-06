"""Validation and immutable Version lifecycle routes for Studio Workflows."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    LOCAL_USER_ID,
    get_workflow,
    validation_projection,
)
from backend.api.v1.studio_schemas import ValidationRunRead, VersionCreate, VersionRead
from backend.database import get_db
from backend.models.studio import StudioWorkflowVersion
from backend.schemas.common import ApiResponse
from backend.services.agent_project_service import (
    publish_workflow_version,
    validate_workflow_draft,
)

router = APIRouter()


@router.post(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/draft/validation-runs"
    ),
    response_model=ApiResponse[ValidationRunRead],
    status_code=201,
)
async def validate_draft(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    row = await validate_workflow_draft(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    return ApiResponse.ok(validation_projection(row))

@router.get(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/versions",
    response_model=ApiResponse[list[VersionRead]],
)
async def list_versions(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    rows = (
        (
            await db.execute(
                select(StudioWorkflowVersion)
                .where(StudioWorkflowVersion.workflow_id == workflow_id)
                .order_by(StudioWorkflowVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([VersionRead.model_validate(row) for row in rows])


@router.post(
    "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/versions",
    response_model=ApiResponse[VersionRead],
    status_code=201,
)
async def publish_version(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    body: VersionCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    row = await publish_workflow_version(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        body=body,
        actor_user_id=LOCAL_USER_ID,
    )
    return ApiResponse.ok(VersionRead.model_validate(row))
