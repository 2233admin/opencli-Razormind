"""Canonical Studio project and draft operations shared by HTTP and Agent Control."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import (
    canonicalize_studio_graph,
    get_project,
    get_workflow,
    get_workspace,
)
from backend.api.v1.studio_schemas import DraftUpdate, ProjectBootstrapCreate, VersionCreate
from backend.models.image_studio import CanvasDocument, CanvasSnapshot
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowDraft,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
)
from backend.schemas import workflow as workflow_schemas
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.trigger_scope import scoped_project, select_active_union


@dataclass(frozen=True)
class CreatedProjectBundle:
    project: StudioProject
    workflow: StudioWorkflow
    draft: StudioWorkflowDraft


def _isolated_source_errors(
    project: workflow_schemas.WorkflowProject,
) -> list[workflow_schemas.WorkflowCompileError]:
    connected_sources = {edge.source for edge in project.edges}
    return [
        workflow_schemas.WorkflowCompileError(
            code="isolated_source_node",
            message=f'Workflow source node "{node.id}" is not connected to a downstream node',
            node_id=node.id,
            path=["nodes", node.id],
        )
        for node in project.nodes
        if node.kind == "source" and node.id not in connected_sources
    ]


def _parked_diagnostics(
    project: workflow_schemas.WorkflowProject,
    parked_ids: list[str],
) -> list[workflow_schemas.WorkflowCompileError]:
    parked_set = set(parked_ids)
    diagnostics = [
        workflow_schemas.WorkflowCompileError(
            code="parked_node",
            message=f'Workflow node "{node_id}" is not connected to a supported trigger.',
            node_id=node_id,
            path=["nodes", node_id],
        )
        for node_id in parked_ids
    ]
    if not parked_set:
        return diagnostics
    parked_project = workflow_schemas.WorkflowProject(
        id=project.id,
        name=project.name,
        profile=project.profile,
        version=project.version,
        nodes=[node for node in project.nodes if node.id in parked_set],
        edges=[],
        settings=project.settings,
        adapters=list(project.adapters),
        agentPermissions=project.agentPermissions,
    )
    for error in compile_workflow_project(parked_project).errors:
        if error.node_id and error.node_id in parked_set:
            diagnostics.append(
                workflow_schemas.WorkflowCompileError(
                    code=error.code,
                    message=error.message,
                    node_id=error.node_id,
                    path=error.path,
                )
            )
    return diagnostics


def _image_generation_nodes(nodes: object, *, path: list[str] | None = None):
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
                    message="Image generation requires a current, scope-owned Canvas snapshot",
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


async def validate_workflow_draft(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    expected_revision: int | None = None,
) -> StudioWorkflowValidationRun:
    """Persist validation for the exact current draft revision."""

    await get_workflow(db, workspace_id, project_id, workflow_id)
    draft = await db.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == workflow_id)
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow draft not found")
    if expected_revision is not None and draft.revision != expected_revision:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow draft revision conflict")

    resolved_graph, errors = await _resolve_image_canvas_snapshots(
        db,
        graph=draft.graph,
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    warnings: list[workflow_schemas.WorkflowCompileError] = []
    valid = False
    stored_graph: dict[str, Any] | None = None
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
    else:
        active_union = select_active_union(project)
        if not active_union.has_supported_trigger:
            errors.extend(_isolated_source_errors(project))
            if not errors:
                result = compile_workflow_project(project)
                errors = list(result.errors)
                if result.valid and result.plan is not None:
                    valid = True
                    stored_graph = resolved_graph
        else:
            scoped = scoped_project(
                project=project,
                active_ids=active_union.active_node_ids,
                external_ids={
                    node.id
                    for node in project.nodes
                    if isinstance(node.params.get("externalWorkflow"), dict)
                },
            )
            errors.extend(_isolated_source_errors(scoped))
            if not errors:
                scoped_result = compile_workflow_project(scoped)
                errors = list(scoped_result.errors)
                if scoped_result.valid and scoped_result.plan is not None:
                    valid = True
                    stored_graph = scoped.model_dump(mode="json")
                    warnings.extend(
                        _parked_diagnostics(project, active_union.parked_node_ids)
                    )
    row = StudioWorkflowValidationRun(
        workflow_id=workflow_id,
        draft_revision=draft.revision,
        status="completed" if valid else "failed",
        valid=valid,
        errors=[error.model_dump(mode="json") for error in errors],
        warnings=[warning.model_dump(mode="json") for warning in warnings],
        compile_version=workflow_schemas.WORKFLOW_COMPILE_VERSION,
        resolved_graph=stored_graph,
    )
    db.add(row)
    await db.flush()
    return row


async def publish_workflow_version(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    body: VersionCreate,
    actor_user_id: str,
    expected_current_version: int | None = None,
    enforce_current_version: bool = False,
) -> StudioWorkflowVersion:
    """Publish one validated revision while preserving its version CAS."""

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
    if enforce_current_version and workflow.current_published_version != expected_current_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published workflow version changed")
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
                published_by_user_id=actor_user_id,
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
    return row
