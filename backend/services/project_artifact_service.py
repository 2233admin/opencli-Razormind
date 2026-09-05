"""Authorized reads over the persisted Studio/native-intelligence artifact graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent_conversation import AgentConversation
from backend.models.intelligence import IntelligenceArtifact, IntelligenceSession
from backend.models.studio import StudioProject, StudioWorkflow
from backend.models.workflow_run import WorkflowRun
from backend.schemas.project_artifact import ProjectArtifactDetail, ProjectArtifactSummary
from backend.workflow.native_intelligence_contracts import MAX_ARTIFACT_PAYLOAD_BYTES


class ProjectArtifactErrorCode(StrEnum):
    PROJECT_NOT_FOUND = "artifact_project_not_found"
    WORKFLOW_NOT_FOUND = "artifact_workflow_not_found"
    RUN_NOT_FOUND = "artifact_run_not_found"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    ARTIFACT_SCOPE_REQUIRED = "artifact_scope_required"
    CONTENT_TOO_LARGE = "artifact_content_too_large"


class ProjectArtifactError(RuntimeError):
    """A fail-closed error from the project/run ownership boundary."""

    def __init__(self, code: ProjectArtifactErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class ProjectArtifactScope:
    workspace_id: str
    project_id: str
    workflow_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class _ArtifactRow:
    artifact: IntelligenceArtifact
    session: IntelligenceSession
    run: WorkflowRun
    workflow: StudioWorkflow
    project: StudioProject


async def resolve_scope(
    db: AsyncSession,
    *,
    workspace_id: str,
    project_id: str,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> ProjectArtifactScope:
    """Resolve and cross-check every requested Studio resource.

    A run is never trusted as a free-standing identifier: it must resolve
    through its Studio workflow and project.  This is also why callers get a
    scoped not-found error before any artifact row is queried.
    """

    project = await db.scalar(
        select(StudioProject).where(
            StudioProject.id == project_id,
            StudioProject.workspace_id == workspace_id,
            StudioProject.archived.is_(False),
        )
    )
    if project is None:
        raise ProjectArtifactError(ProjectArtifactErrorCode.PROJECT_NOT_FOUND)

    resolved_workflow_id = workflow_id
    if workflow_id is not None:
        workflow = await db.scalar(
            select(StudioWorkflow).where(
                StudioWorkflow.id == workflow_id,
                StudioWorkflow.project_id == project_id,
                StudioWorkflow.archived.is_(False),
            )
        )
        if workflow is None:
            raise ProjectArtifactError(ProjectArtifactErrorCode.WORKFLOW_NOT_FOUND)

    if run_id is not None:
        run_query = (
            select(WorkflowRun)
            .join(StudioWorkflow, StudioWorkflow.id == WorkflowRun.workflow_id)
            .where(
                WorkflowRun.id == run_id,
                StudioWorkflow.project_id == project_id,
                StudioWorkflow.archived.is_(False),
            )
        )
        if resolved_workflow_id is not None:
            run_query = run_query.where(WorkflowRun.workflow_id == resolved_workflow_id)
        run = await db.scalar(run_query)
        if run is None:
            raise ProjectArtifactError(ProjectArtifactErrorCode.RUN_NOT_FOUND)
        resolved_workflow_id = run.workflow_id

    return ProjectArtifactScope(
        workspace_id=workspace_id,
        project_id=project_id,
        workflow_id=resolved_workflow_id,
        run_id=run_id,
    )


def _base_query(scope: ProjectArtifactScope):
    query = (
        select(
            IntelligenceArtifact,
            IntelligenceSession,
            WorkflowRun,
            StudioWorkflow,
            StudioProject,
        )
        .join(IntelligenceSession, IntelligenceSession.id == IntelligenceArtifact.session_id)
        .join(WorkflowRun, WorkflowRun.id == IntelligenceSession.created_by_run_id)
        .join(StudioWorkflow, StudioWorkflow.id == WorkflowRun.workflow_id)
        .join(StudioProject, StudioProject.id == StudioWorkflow.project_id)
        .where(
            StudioProject.id == scope.project_id,
            StudioProject.workspace_id == scope.workspace_id,
            StudioProject.archived.is_(False),
            StudioWorkflow.archived.is_(False),
        )
    )
    if scope.workflow_id is not None:
        query = query.where(WorkflowRun.workflow_id == scope.workflow_id)
    if scope.run_id is not None:
        query = query.where(WorkflowRun.id == scope.run_id)
    return query


async def _rows(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
    artifact_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[_ArtifactRow]:
    query = _base_query(scope).order_by(
        IntelligenceArtifact.created_at.desc(), IntelligenceArtifact.id.desc()
    )
    if artifact_id is not None:
        query = query.where(IntelligenceArtifact.artifact_id == artifact_id)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = (await db.execute(query)).all()
    return [
        _ArtifactRow(
            artifact=row[0],
            session=row[1],
            run=row[2],
            workflow=row[3],
            project=row[4],
        )
        for row in rows
    ]


def _payload_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _artifact_title(artifact: IntelligenceArtifact) -> str:
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    title = _payload_string(payload, "title", "name", "question")
    if title is not None:
        return title[:255]
    return artifact.kind.replace("_", " ").title()


def _artifact_media_type(artifact: IntelligenceArtifact) -> str:
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    media_type = _payload_string(payload, "media_type", "mediaType")
    if media_type is not None and "/" in media_type and len(media_type) <= 127:
        return media_type
    return "application/json"


def _conversation_candidates(row: _ArtifactRow) -> set[str]:
    candidates: set[str] = set()
    for envelope in (row.artifact.provenance, row.run.request):
        if not isinstance(envelope, dict):
            continue
        for key in ("conversation_id", "conversationId"):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip())
    return candidates


def _conversation_matches(
    conversation: AgentConversation,
    *,
    scope: ProjectArtifactScope,
    studio_workspace_id: str,
) -> bool:
    binding = conversation.context_binding if isinstance(conversation.context_binding, dict) else {}
    if binding.get("project_id") != scope.project_id:
        return False
    if scope.workflow_id is not None and binding.get("workflow_id") not in {
        None,
        scope.workflow_id,
    }:
        return False
    if scope.run_id is not None and binding.get("run_id") not in {None, scope.run_id}:
        return False
    if binding.get("studio_workspace_id") not in {None, studio_workspace_id}:
        return False
    return True


async def _trusted_conversations(
    db: AsyncSession,
    rows: list[_ArtifactRow],
    *,
    scope: ProjectArtifactScope,
    studio_workspace_id: str,
    conversation_workspace_id: str,
) -> dict[str, str]:
    candidates = {candidate for row in rows for candidate in _conversation_candidates(row)}
    if not candidates:
        return {}
    conversations = list(
        (
            await db.scalars(
                select(AgentConversation).where(
                    AgentConversation.id.in_(candidates),
                    AgentConversation.workspace_id == conversation_workspace_id,
                )
            )
        ).all()
    )
    return {
        conversation.id: conversation.id
        for conversation in conversations
        if _conversation_matches(
            conversation,
            scope=scope,
            studio_workspace_id=studio_workspace_id,
        )
    }


def _summary(
    row: _ArtifactRow,
    *,
    conversation_id: str | None,
) -> ProjectArtifactSummary:
    artifact = row.artifact
    provenance = artifact.provenance if isinstance(artifact.provenance, dict) else {}
    return ProjectArtifactSummary(
        id=artifact.artifact_id,
        artifact_id=artifact.artifact_id,
        title=_artifact_title(artifact),
        media_type=_artifact_media_type(artifact),
        kind=artifact.kind,
        content_hash=artifact.content_hash,
        workspace_id=row.project.workspace_id,
        project_id=row.project.id,
        workflow_id=row.workflow.id,
        run_id=row.run.id,
        session_id=row.session.id,
        conversation_id=conversation_id,
        source=_payload_string(provenance, "source"),
        simulated=artifact.simulated,
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


def _content_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _detail(
    row: _ArtifactRow,
    *,
    conversation_id: str | None,
) -> ProjectArtifactDetail:
    artifact = row.artifact
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    if _content_size(payload) > MAX_ARTIFACT_PAYLOAD_BYTES:
        raise ProjectArtifactError(ProjectArtifactErrorCode.CONTENT_TOO_LARGE)
    summary = _summary(row, conversation_id=conversation_id)
    return ProjectArtifactDetail(
        **summary.model_dump(),
        schema_version=artifact.schema_version,
        content=payload,
        payload=payload,
        provenance=artifact.provenance if isinstance(artifact.provenance, dict) else {},
        grounding_artifact_ids=list(
            artifact.provenance.get("evidence_artifact_ids", [])
            if isinstance(artifact.provenance, dict)
            and isinstance(artifact.provenance.get("evidence_artifact_ids"), list)
            else []
        ),
        algorithm_version=artifact.algorithm_version,
        seed=artifact.seed,
    )


async def list_project_artifacts(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
    limit: int = 100,
    offset: int = 0,
    conversation_workspace_id: str | None = None,
) -> list[ProjectArtifactSummary]:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("artifact_query_bounds_invalid")
    rows = await _rows(db, scope=scope, limit=limit, offset=offset)
    conversations = await _trusted_conversations(
        db,
        rows,
        scope=scope,
        studio_workspace_id=scope.workspace_id,
        conversation_workspace_id=conversation_workspace_id or scope.workspace_id,
    )
    return [
        _summary(
            row,
            conversation_id=next(
                (
                    candidate
                    for candidate in _conversation_candidates(row)
                    if candidate in conversations
                ),
                None,
            ),
        )
        for row in rows
    ]


async def get_project_artifact(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
    artifact_id: str,
    conversation_workspace_id: str | None = None,
) -> ProjectArtifactDetail:
    rows = await _rows(db, scope=scope, artifact_id=artifact_id)
    if not rows:
        raise ProjectArtifactError(ProjectArtifactErrorCode.ARTIFACT_NOT_FOUND)
    if len(rows) > 1:
        # The domain key is only unique inside a native session.  Do not choose
        # one row and accidentally expose another run when a caller omitted the
        # run scope.
        raise ProjectArtifactError(ProjectArtifactErrorCode.ARTIFACT_SCOPE_REQUIRED)
    row = rows[0]
    conversations = await _trusted_conversations(
        db,
        [row],
        scope=scope,
        studio_workspace_id=scope.workspace_id,
        conversation_workspace_id=conversation_workspace_id or scope.workspace_id,
    )
    conversation_id = next(
        (candidate for candidate in _conversation_candidates(row) if candidate in conversations),
        None,
    )
    return _detail(row, conversation_id=conversation_id)


__all__ = [
    "ProjectArtifactError",
    "ProjectArtifactErrorCode",
    "ProjectArtifactScope",
    "get_project_artifact",
    "list_project_artifacts",
    "resolve_scope",
]
