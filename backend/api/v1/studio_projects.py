"""Workspace, Project, and transactional bootstrap routes for Studio."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    LOCAL_USER_ID,
    get_workspace,
)
from backend.api.v1.studio_schemas import (
    DraftRead,
    ProjectBootstrapCreate,
    ProjectBootstrapRead,
    ProjectRead,
    WorkflowRead,
    WorkspaceRead,
)
from backend.database import get_db
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowDraft,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
    StudioWorkspace,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas.common import ApiResponse
from backend.services.agent_project_service import create_project_bundle

router = APIRouter()


@router.get("/workspaces", response_model=ApiResponse[list[WorkspaceRead]])
async def list_workspaces(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    rows = (
        (await db.execute(select(StudioWorkspace).order_by(StudioWorkspace.name)))
        .scalars()
        .all()
    )
    if not rows:
        default = StudioWorkspace(name="OpenCLI 工作区", slug="opencli-default")
        db.add(default)
        await db.flush()
        rows = [default]
    return ApiResponse.ok([WorkspaceRead.model_validate(row) for row in rows if row.active])


@router.get(
    "/workspaces/{workspace_id}/projects", response_model=ApiResponse[list[ProjectRead]]
)
async def list_projects(workspace_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    await get_workspace(db, workspace_id)
    rows = (
        (
            await db.execute(
                select(StudioProject)
                .where(
                    StudioProject.workspace_id == workspace_id,
                    StudioProject.archived.is_(False),
                )
                .order_by(StudioProject.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse.ok([ProjectRead.model_validate(row) for row in rows])


@router.post(
    "/workspaces/{workspace_id}/projects/bootstrap",
    response_model=ApiResponse[ProjectBootstrapRead],
    status_code=201,
)
async def bootstrap_project(
    workspace_id: str,
    body: ProjectBootstrapCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    created = await create_project_bundle(
        db,
        workspace_id=workspace_id,
        body=body,
        actor_user_id=LOCAL_USER_ID,
    )

    return ApiResponse.ok(
        ProjectBootstrapRead(
            project=ProjectRead.model_validate(created.project),
            primary_workflow=WorkflowRead.model_validate(created.workflow),
            draft=DraftRead.model_validate(created.draft, from_attributes=True),
        )
    )


@router.delete(
    "/workspaces/{workspace_id}/projects/{project_id}",
    response_model=ApiResponse[None],
)
async def delete_project(
    workspace_id: str,
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Delete a project and every Studio/runtime artifact owned by its workflows."""

    await get_workspace(db, workspace_id)
    project = await db.scalar(
        select(StudioProject).where(
            StudioProject.id == project_id,
            StudioProject.workspace_id == workspace_id,
        )
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    workflow_ids = list(
        (
            await db.execute(
                select(StudioWorkflow.id).where(StudioWorkflow.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    if workflow_ids:
        # Break the project → primary workflow cycle before removing children.
        await db.execute(
            update(StudioProject)
            .where(StudioProject.id == project_id)
            .values(primary_workflow_id=None)
        )
        # Runtime rows RESTRICT their published Studio version, so remove them first.
        await db.execute(
            delete(WorkflowRun).where(WorkflowRun.workflow_id.in_(workflow_ids))
        )
        # Versions RESTRICT their validation run, so remove them in dependency order.
        await db.execute(
            delete(StudioWorkflowVersion).where(
                StudioWorkflowVersion.workflow_id.in_(workflow_ids)
            )
        )
        await db.execute(
            delete(StudioWorkflowValidationRun).where(
                StudioWorkflowValidationRun.workflow_id.in_(workflow_ids)
            )
        )
        await db.execute(
            delete(StudioWorkflowDraft).where(
                StudioWorkflowDraft.workflow_id.in_(workflow_ids)
            )
        )
        await db.execute(
            delete(StudioWorkflow).where(StudioWorkflow.id.in_(workflow_ids))
        )
    await db.execute(delete(StudioProject).where(StudioProject.id == project_id))
    return ApiResponse.ok(None)
