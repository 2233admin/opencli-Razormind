"""Generic, unscoped ResearchGraph projection and mutation routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import get_workflow
from backend.api.v1.workflow_run_access import reject_workspace_scoped_run
from backend.database import get_db
from backend.models.workflow_run import WorkflowRun
from backend.schemas.common import ApiResponse
from backend.schemas.research_graph import (
    WorkflowResearchGraphMutationRequest,
    WorkflowResearchGraphMutationResponse,
    WorkflowResearchGraphProjection,
)
from backend.workflow.research_graph_mutations import (
    ResearchGraphMutationError,
    ResearchGraphReadError,
    mutate_workflow_research_graph,
    read_workflow_research_graph,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])

studio_router = APIRouter()


@router.get(
    "/runs/{run_id}/research-graph",
    response_model=ApiResponse[WorkflowResearchGraphProjection],
)
async def get_run_research_graph(
    run_id: str,
    up_to_sequence: int | None = Query(default=None, ge=0, alias="upToSequence"),
    revision_id: str | None = Query(default=None, min_length=1, alias="revisionId"),
    entity_id: str | None = Query(default=None, min_length=1, alias="entityId"),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchGraphProjection]:
    """Fold an unscoped run transcript into its ResearchGraph view."""

    await reject_workspace_scoped_run(db, run_id)
    try:
        graph = await read_workflow_research_graph(
            db,
            run_id=run_id,
            up_to_sequence=up_to_sequence,
            revision_id=revision_id,
            entity_id=entity_id,
            limit=limit,
        )
    except ResearchGraphReadError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "research_graph_invalid_transcript", "message": str(exc)},
        ) from exc
    if graph is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return ApiResponse.ok(graph)


@router.post(
    "/runs/{run_id}/research-graph/mutations",
    response_model=ApiResponse[WorkflowResearchGraphMutationResponse],
)
async def mutate_run_research_graph(
    run_id: str,
    body: WorkflowResearchGraphMutationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchGraphMutationResponse]:
    """Append one versioned semantic mutation to an unscoped workflow run."""

    await reject_workspace_scoped_run(db, run_id)
    try:
        result = await mutate_workflow_research_graph(
            db,
            run_id=run_id,
            request=body,
            plugins=request.app.state.workflow_plugins,
        )
    except ResearchGraphMutationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "research_graph_mutation_conflict", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    events, graph = result
    return ApiResponse.ok(WorkflowResearchGraphMutationResponse(events=events, graph=graph))


async def _get_project_run(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
) -> WorkflowRun:
    await get_workflow(db, workspace_id, project_id, workflow_id)
    row = await db.get(WorkflowRun, run_id)
    if row is None or row.workflow_id != workflow_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return row


@studio_router.get(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/research-graph"
    ),
    response_model=ApiResponse[WorkflowResearchGraphProjection],
)
async def get_project_research_graph(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    up_to_sequence: int | None = Query(default=None, ge=0, alias="upToSequence"),
    revision_id: str | None = Query(default=None, min_length=1, alias="revisionId"),
    entity_id: str | None = Query(default=None, min_length=1, alias="entityId"),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchGraphProjection]:
    """Fold a project-owned run transcript into its ResearchGraph view."""

    await _get_project_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    try:
        graph = await read_workflow_research_graph(
            db,
            run_id=run_id,
            up_to_sequence=up_to_sequence,
            revision_id=revision_id,
            entity_id=entity_id,
            limit=limit,
        )
    except ResearchGraphReadError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "research_graph_invalid_transcript", "message": str(exc)},
        ) from exc
    if graph is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    return ApiResponse.ok(graph)


@studio_router.post(
    (
        "/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}"
        "/runs/{run_id}/research-graph/mutations"
    ),
    response_model=ApiResponse[WorkflowResearchGraphMutationResponse],
)
async def mutate_project_research_graph(
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    run_id: str,
    body: WorkflowResearchGraphMutationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[WorkflowResearchGraphMutationResponse]:
    """Append one versioned semantic mutation to a workspace-owned workflow run."""

    await _get_project_run(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    try:
        result = await mutate_workflow_research_graph(
            db,
            run_id=run_id,
            request=body,
            plugins=request.app.state.workflow_plugins,
        )
    except ResearchGraphMutationError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "research_graph_mutation_conflict", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow run not found")
    events, graph = result
    return ApiResponse.ok(WorkflowResearchGraphMutationResponse(events=events, graph=graph))
