"""Validation and immutable Version lifecycle routes for Studio Workflows."""

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    LOCAL_USER_ID,
    canonicalize_studio_graph,
    get_project,
    get_workflow,
    validation_projection,
)
from backend.api.v1.studio_schemas import ValidationRunRead, VersionCreate, VersionRead
from backend.database import get_db
from backend.models.image_studio import CanvasDocument, CanvasSnapshot
from backend.models.studio import (
    StudioWorkflow,
    StudioWorkflowDraft,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
)
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse
from backend.workflow.compiler import compile_workflow_project

router = APIRouter()


def _isolated_source_errors(
    project: workflow_schemas.WorkflowProject,
) -> list[workflow_schemas.WorkflowCompileError]:
    """Reject Studio drafts whose root source cannot feed any downstream node.

    The generic compiler intentionally permits standalone nodes for capability
    previews. A Studio draft, however, is publishable and runnable, so accepting
    an unconnected source would silently discard every record it collects.
    """

    connected_sources = {edge.source for edge in project.edges}
    return [
        workflow_schemas.WorkflowCompileError(
            code="isolated_source_node",
            message=(
                f'Workflow source node "{node.id}" is not connected to a downstream node'
            ),
            node_id=node.id,
            path=["nodes", node.id],
        )
        for node in project.nodes
        if node.kind == "source" and node.id not in connected_sources
    ]


def _image_generation_nodes(
    nodes: object,
    *,
    path: list[str] | None = None,
):
    if not isinstance(nodes, list):
        return
    base_path = path or ["nodes"]
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_path = [*base_path, str(index)]
        ui = node.get("ui") if isinstance(node.get("ui"), dict) else {}
        if (
            node.get("kind") == "media"
            and node.get("capability") == "generate"
            and ui.get("catalogId") in {None, "media.image-generation"}
        ):
            yield node, node_path
        internals = node.get("internals")
        if isinstance(internals, dict):
            yield from _image_generation_nodes(
                internals.get("nodes"), path=[*node_path, "internals", "nodes"]
            )


async def _resolve_image_canvas_snapshots(
    db: AsyncSession,
    *,
    graph: dict[str, Any],
    workspace_id: str,
    project_id: str,
    workflow_id: str,
) -> tuple[dict[str, Any], list[workflow_schemas.WorkflowCompileError]]:
    """Resolve editable Canvas document ids to immutable snapshot ids."""

    resolved = deepcopy(graph)
    errors: list[workflow_schemas.WorkflowCompileError] = []
    for node, path in _image_generation_nodes(resolved.get("nodes")):
        node_id = node.get("id") if isinstance(node.get("id"), str) else ""
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        document_id = params.get("canvasDocumentId")
        snapshot_id = params.get("canvasSnapshotId")
        snapshot: CanvasSnapshot | None = None
        if isinstance(document_id, str) and document_id:
            document = await db.scalar(
                select(CanvasDocument).where(
                    CanvasDocument.id == document_id,
                    CanvasDocument.workspace_id == workspace_id,
                    CanvasDocument.project_id == project_id,
                    CanvasDocument.workflow_id == workflow_id,
                    CanvasDocument.node_id == node_id,
                )
            )
            if document is not None:
                snapshot = await db.scalar(
                    select(CanvasSnapshot)
                    .where(
                        CanvasSnapshot.document_id == document.id,
                        CanvasSnapshot.workspace_id == workspace_id,
                        CanvasSnapshot.project_id == project_id,
                        CanvasSnapshot.workflow_id == workflow_id,
                        CanvasSnapshot.node_id == node_id,
                        CanvasSnapshot.document_revision == document.revision,
                    )
                    .order_by(CanvasSnapshot.created_at.desc(), CanvasSnapshot.id.desc())
                    .limit(1)
                )
        elif isinstance(snapshot_id, str) and snapshot_id:
            snapshot = await db.scalar(
                select(CanvasSnapshot).where(
                    CanvasSnapshot.id == snapshot_id,
                    CanvasSnapshot.workspace_id == workspace_id,
                    CanvasSnapshot.project_id == project_id,
                    CanvasSnapshot.workflow_id == workflow_id,
                    CanvasSnapshot.node_id == node_id,
                )
            )

        if snapshot is None:
            errors.append(
                workflow_schemas.WorkflowCompileError(
                    code="image_canvas_snapshot_unresolved",
                    message=(
                        "Image generation requires a current, scope-owned Canvas snapshot"
                    ),
                    node_id=node_id or None,
                    path=[*path, "params", "canvasDocumentId"],
                )
            )
            continue
        node["params"] = {
            key: value
            for key, value in params.items()
            if key not in {"canvasDocumentId", "canvasSnapshotId"}
        }
        node["params"]["canvasSnapshotId"] = snapshot.id
    return resolved, errors


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
    await get_workflow(db, workspace_id, project_id, workflow_id)
    draft = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow_id)
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")

    resolved_graph, errors = await _resolve_image_canvas_snapshots(
        db,
        graph=draft.graph,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    try:
        project = workflow_schemas.WorkflowProject.model_validate(resolved_graph)
    except ValidationError as exc:
        errors.extend(
            workflow_schemas.WorkflowCompileError(
                code="invalid_workflow_project",
                message=error["msg"],
                path=[str(part) for part in error["loc"]],
            )
            for error in exc.errors()
        )
        valid = False
    else:
        errors.extend(_isolated_source_errors(project))
        if errors:
            valid = False
        else:
            result = compile_workflow_project(project)
            errors = result.errors
            valid = result.valid

    row = StudioWorkflowValidationRun(
        workflow_id=workflow_id,
        draft_revision=draft.revision,
        status="completed" if valid else "failed",
        valid=valid,
        errors=[error.model_dump(mode="json") for error in errors],
        warnings=[],
        compile_version=workflow_schemas.WORKFLOW_COMPILE_VERSION,
        resolved_graph=resolved_graph if valid else None,
    )
    db.add(row)
    await db.flush()
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
    await get_project(db, workspace_id, project_id)
    workflow = await db.scalar(
        select(StudioWorkflow)
        .where(
            StudioWorkflow.id == workflow_id,
            StudioWorkflow.project_id == project_id,
        )
        .with_for_update()
    )
    if workflow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    draft = await db.scalar(
        select(StudioWorkflowDraft)
        .where(StudioWorkflowDraft.workflow_id == workflow_id)
        .with_for_update()
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    if body.expected_revision != draft.revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow draft revision conflict")

    validation = await db.scalar(
        select(StudioWorkflowValidationRun).where(
            StudioWorkflowValidationRun.id == body.validation_run_id,
            StudioWorkflowValidationRun.workflow_id == workflow_id,
        )
    )
    if validation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Validation run not found")
    if (
        not validation.valid
        or validation.status != "completed"
        or validation.draft_revision != draft.revision
        or validation.resolved_graph is None
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Current workflow draft revision has not passed validation",
        )

    try:
        async with db.begin_nested():
            row = StudioWorkflowVersion(
                workflow_id=workflow_id,
                version=(workflow.current_published_version or 0) + 1,
                draft_revision=draft.revision,
                graph=canonicalize_studio_graph(
                    validation.resolved_graph,
                    workflow_id=workflow_id,
                ),
                compile_version=validation.compile_version,
                validation_run_id=validation.id,
                published_by_user_id=LOCAL_USER_ID,
                reason=body.reason,
            )
            db.add(row)
            workflow.current_published_version = row.version
            await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow version publish conflict",
        ) from exc
    return ApiResponse.ok(VersionRead.model_validate(row))
