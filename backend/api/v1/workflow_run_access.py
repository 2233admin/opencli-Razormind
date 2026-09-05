"""Ownership guards shared by generic workflow-run routes."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.studio import StudioWorkflow
from backend.models.workflow_run import WorkflowRun


async def reject_workspace_scoped_run(db: AsyncSession, run_id: str) -> None:
    """Keep project-owned runs behind workspace-scoped API routes."""

    run = await db.get(WorkflowRun, run_id)
    if run is not None and (
        run.workflow_version_id is not None
        or run.studio_workflow_version_id is not None
        or await db.get(StudioWorkflow, run.workflow_id) is not None
    ):
        raise HTTPException(status_code=404, detail="Workflow run not found")
