"""Shared immutable Studio Workflow run lifecycle."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import get_workflow
from backend.database import rollback_session
from backend.models.studio import StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse
from backend.workflow.native_intelligence_contracts import WorkflowConversationOrigin
from backend.workflow.opencli_hda_tracer import get_workflow_run_projection

WorkflowStart = Callable[..., Awaitable[workflow_schemas.WorkflowRunProjection]]
AfterStart = Callable[[AsyncSession, str], Awaitable[None]]


def canonical_run_identity(*, inputs: dict, user: str) -> str:
    return json.dumps(
        {"inputs": inputs, "user": user},
        sort_keys=True,
        separators=(",", ":"),
    )


def default_published_trigger_kind(
    project: workflow_schemas.WorkflowProject,
    trigger_node_id: str | None,
) -> workflow_schemas.WorkflowRunTriggerKind:
    nodes = project.nodes
    if trigger_node_id:
        selected = next((node for node in nodes if node.id == trigger_node_id), None)
        if selected is not None:
            if selected.kind == "webhook":
                return "webhook"
            if selected.kind == "schedule":
                builder = selected.params.get("builder")
                if selected.params.get("mode") == "manual" or (
                    isinstance(builder, dict) and builder.get("nodeType") == "manual-trigger"
                ):
                    return "manual"
                return "schedule"
    for node in nodes:
        if node.kind == "schedule" and (
            node.params.get("mode") == "manual"
            or (
                isinstance(node.params.get("builder"), dict)
                and node.params["builder"].get("nodeType") == "manual-trigger"
            )
        ):
            return "manual"
    if any(node.kind == "schedule" for node in nodes):
        return "schedule"
    if any(node.kind == "webhook" for node in nodes):
        return "webhook"
    return "manual"


async def get_published_workflow_version(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
) -> StudioWorkflowVersion:
    workflow = await get_workflow(db, workspace_id, project_id, workflow_id)
    if workflow.current_published_version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Workflow must be published before API execution",
        )
    version = await db.scalar(
        select(StudioWorkflowVersion).where(
            StudioWorkflowVersion.workflow_id == workflow_id,
            StudioWorkflowVersion.version == workflow.current_published_version,
        )
    )
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published workflow version is unavailable",
        )
    return version


def published_run_id(
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    version_id: str,
    idempotency_key: str | None,
) -> str | None:
    if not idempotency_key:
        return None
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "opencli-admin:studio-run:"
                f"{workspace_id}:{project_id}:{workflow_id}:{version_id}:{idempotency_key}"
            ),
        )
    )


async def _existing_published_run_projection(
    db: AsyncSession,
    *,
    run_id: str,
    workflow_id: str,
    version_id: str,
    requested_identity: str,
    conversation_origin: WorkflowConversationOrigin | None,
) -> workflow_schemas.WorkflowRunProjection | None:
    existing = await db.get(WorkflowRun, run_id)
    if existing is None:
        return None
    existing_input = existing.request.get("input") if isinstance(existing.request, dict) else None
    existing_payload = existing_input.get("payload") if isinstance(existing_input, dict) else None
    existing_user = existing_input.get("sourceId") if isinstance(existing_input, dict) else None
    stored_origin = (
        existing.request.get("_serverConversationOrigin")
        if isinstance(existing.request, dict)
        else None
    )
    identity_matches = (
        isinstance(existing_payload, dict)
        and isinstance(existing_user, str)
        and canonical_run_identity(inputs=existing_payload, user=existing_user)
        == requested_identity
    )
    if (
        existing.workflow_id != workflow_id
        or existing.studio_workflow_version_id != version_id
        or not identity_matches
        or stored_origin
        != (conversation_origin.model_dump(mode="json") if conversation_origin else None)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Idempotency key collides with another workflow run",
        )
    projection = await get_workflow_run_projection(run_id, session=db)
    if projection is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Stored idempotent workflow run is unavailable",
        )
    return projection


async def start_published_version_run(
    *,
    db: AsyncSession,
    workspace_id: str,
    project_id: str,
    workflow_id: str,
    version: StudioWorkflowVersion,
    run_input: workflow_schemas.WorkflowRunInput,
    user: str,
    request_id: str,
    response_mode: workflow_schemas.WorkflowRunResponseMode,
    start_runner: WorkflowStart,
    after_start: AfterStart | None = None,
    trigger_kind: workflow_schemas.WorkflowRunTriggerKind | None = None,
    trigger_node_id: str | None = None,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    conversation_origin: WorkflowConversationOrigin | None = None,
    plugins: Any = None,
) -> ApiResponse:
    """Start a published graph with the existing deterministic run identity."""

    version_id = version.id
    resolved_run_id = run_id or published_run_id(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=workflow_id,
        version_id=version_id,
        idempotency_key=idempotency_key,
    )
    requested_identity = canonical_run_identity(inputs=run_input.payload, user=user)
    if idempotency_key:
        existing_projection = await _existing_published_run_projection(
            db,
            run_id=resolved_run_id,
            workflow_id=workflow_id,
            version_id=version_id,
            requested_identity=requested_identity,
            conversation_origin=conversation_origin,
        )
        if existing_projection is not None:
            return ApiResponse.ok(existing_projection)

    project = workflow_schemas.WorkflowProject.model_validate(version.graph)
    resolved_trigger_kind = trigger_kind or default_published_trigger_kind(
        project,
        trigger_node_id,
    )
    try:
        projection = await start_runner(
            workflow_schemas.WorkflowRunStartRequest(
                project=project,
                runId=resolved_run_id,
                trigger=workflow_schemas.WorkflowRunTrigger(
                    kind=resolved_trigger_kind,
                    triggerNodeId=trigger_node_id,
                    requestId=request_id,
                    idempotencyKey=idempotency_key,
                ),
                input=run_input,
                responseMode=response_mode,
            ),
            session=db,
            studio_workflow_version_id=version_id,
            conversation_origin=conversation_origin,
            plugins=plugins,
        )
    except IntegrityError:
        if not idempotency_key:
            raise
        await rollback_session(db)
        projection = await _existing_published_run_projection(
            db,
            run_id=resolved_run_id,
            workflow_id=workflow_id,
            version_id=version_id,
            requested_identity=requested_identity,
            conversation_origin=conversation_origin,
        )
        if projection is None:
            raise
        return ApiResponse.ok(projection)
    if after_start is not None:
        await after_start(db, projection.runId)
    return ApiResponse.ok(projection)


__all__ = [
    "default_published_trigger_kind",
    "get_published_workflow_version",
    "published_run_id",
    "start_published_version_run",
]
