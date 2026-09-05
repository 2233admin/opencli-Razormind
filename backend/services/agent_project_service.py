"""Canonical Studio project and draft operations shared by HTTP and Agent Control."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    canonicalize_studio_graph,
    get_project,
    get_workflow,
    get_workspace,
)
from backend.api.v1.studio_schemas import DraftUpdate, ProjectBootstrapCreate
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowDraft,
)


@dataclass(frozen=True)
class CreatedProjectBundle:
    project: StudioProject
    workflow: StudioWorkflow
    draft: StudioWorkflowDraft


async def list_projects(
    db: AsyncSession,
    *,
    workspace_id: str,
) -> list[StudioProject]:
    return list(
        await db.scalars(
            select(StudioProject)
            .where(
                StudioProject.workspace_id == workspace_id,
                StudioProject.archived.is_(False),
            )
            .order_by(StudioProject.updated_at.desc())
        )
    )


async def list_workflows(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
) -> list[StudioWorkflow]:
    await get_project(db, workspace_id, project_id)
    return list(
        await db.scalars(
            select(StudioWorkflow)
            .where(
                StudioWorkflow.project_id == project_id,
                StudioWorkflow.archived.is_(False),
            )
            .order_by(StudioWorkflow.updated_at.desc())
        )
    )


async def get_workflow_draft(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
) -> StudioWorkflowDraft:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    return row


async def create_project_bundle(
    db: AsyncSession,
    *,
    workspace_id: str,
    body: ProjectBootstrapCreate,
    actor_user_id: str,
) -> CreatedProjectBundle:
    """Create the Project, primary Workflow, and Draft as one canonical unit."""

    await get_workspace(db, workspace_id)
    existing = await db.scalar(
        select(StudioProject.id).where(
            StudioProject.workspace_id == workspace_id,
            StudioProject.slug == body.project.slug,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project slug already exists")

    try:
        async with db.begin_nested():
            project = StudioProject(
                workspace_id=workspace_id,
                name=body.project.name,
                slug=body.project.slug,
                description=body.project.description,
                app_type=body.project.app_type,
                created_by_user_id=actor_user_id,
            )
            db.add(project)
            await db.flush()

            workflow = StudioWorkflow(
                project_id=project.id,
                name=body.workflow.name,
                description=body.workflow.description,
            )
            db.add(workflow)
            await db.flush()

            draft = StudioWorkflowDraft(
                workflow_id=workflow.id,
                graph=canonicalize_studio_graph(body.workflow.graph, workflow_id=workflow.id),
                updated_by_user_id=actor_user_id,
            )
            db.add(draft)
            project.primary_workflow_id = workflow.id
            await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Project or primary workflow already exists",
        ) from exc

    return CreatedProjectBundle(project=project, workflow=workflow, draft=draft)


async def update_workflow_draft(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    body: DraftUpdate,
    actor_user_id: str,
) -> StudioWorkflowDraft:
    """Conditionally advance one Draft revision to prevent lost updates."""

    await get_project(db, workspace_id, project_id)
    workflow = await db.scalar(
        select(StudioWorkflow.id).where(
            StudioWorkflow.id == workflow_id,
            StudioWorkflow.project_id == project_id,
        )
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    graph = canonicalize_studio_graph(body.graph, workflow_id=workflow_id)
    result = await db.execute(
        update(StudioWorkflowDraft)
        .where(
            StudioWorkflowDraft.workflow_id == workflow_id,
            StudioWorkflowDraft.revision == body.revision,
        )
        .values(
            graph=graph,
            revision=body.revision + 1,
            updated_by_user_id=actor_user_id,
        )
    )
    if getattr(result, "rowcount", None) != 1:
        exists = await db.scalar(
            select(StudioWorkflowDraft.id).where(
                StudioWorkflowDraft.workflow_id == workflow_id
            )
        )
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow draft revision conflict")

    row = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow_id)
    )
    if row is None:  # Defensive: the conditional update above proved the row existed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    await db.refresh(row)
    return row
