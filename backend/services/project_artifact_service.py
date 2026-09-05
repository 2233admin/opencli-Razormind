"""Authorized reads over the persisted Studio/native-intelligence artifact graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent_conversation import AgentConversation
from backend.models.intelligence import (
    IntelligenceArtifact,
    IntelligenceSession,
    IntelligenceTransition,
)
from backend.models.studio import StudioProject, StudioWorkflow
from backend.models.workflow_run import WorkflowRun
from backend.schemas.project_artifact import ProjectArtifactDetail, ProjectArtifactSummary
from backend.schemas.workflow_evidence import EvidenceBatchSummary, WorkflowEvidenceBatchDetail
from backend.schemas.workflow_runtime import WorkflowRunProjection
from backend.workflow.evidence_projection import get_evidence_batch, list_evidence_batches
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


@dataclass(frozen=True)
class _EvidenceRow:
    batch: EvidenceBatchSummary
    detail: WorkflowEvidenceBatchDetail
    run: WorkflowRun
    workflow: StudioWorkflow
    project: StudioProject


_ArtifactLike = _ArtifactRow | _EvidenceRow


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
    return query


async def _rows(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
    artifact_id: str | None = None,
) -> list[_ArtifactRow]:
    rows = (await db.execute(_base_query(scope))).all()
    if not rows:
        return []

    session_ids = {row[1].id for row in rows}
    transitions = (
        await db.scalars(
            select(IntelligenceTransition)
            .where(IntelligenceTransition.session_id.in_(session_ids))
            .order_by(IntelligenceTransition.sequence.asc(), IntelligenceTransition.id.asc())
        )
    ).all()
    producer_by_artifact: dict[tuple[str, str], str] = {}
    conflicting_artifacts: set[tuple[str, str]] = set()
    for transition in transitions:
        if not isinstance(transition.run_id, str) or not transition.run_id:
            continue
        metadata = transition.metadata_json if isinstance(transition.metadata_json, dict) else {}
        artifact_ids = metadata.get("artifact_ids")
        if not isinstance(artifact_ids, list):
            continue
        for raw_artifact_id in artifact_ids:
            if not isinstance(raw_artifact_id, str) or not raw_artifact_id:
                continue
            key = (transition.session_id, raw_artifact_id)
            prior = producer_by_artifact.get(key)
            if prior is not None and prior != transition.run_id:
                conflicting_artifacts.add(key)
            else:
                producer_by_artifact[key] = transition.run_id

    producer_run_ids = set(producer_by_artifact.values())
    if not producer_run_ids:
        return []
    producer_runs = (
        await db.execute(
            select(WorkflowRun, StudioWorkflow, StudioProject)
            .join(StudioWorkflow, StudioWorkflow.id == WorkflowRun.workflow_id)
            .join(StudioProject, StudioProject.id == StudioWorkflow.project_id)
            .where(
                WorkflowRun.id.in_(producer_run_ids),
                StudioProject.id == scope.project_id,
                StudioProject.workspace_id == scope.workspace_id,
                StudioProject.archived.is_(False),
                StudioWorkflow.archived.is_(False),
            )
        )
    ).all()
    producer_by_id = {
        run.id: (run, workflow, project)
        for run, workflow, project in producer_runs
    }

    result: list[_ArtifactRow] = []
    for artifact, session, _creator_run, _creator_workflow, _creator_project in rows:
        key = (session.id, artifact.artifact_id)
        if key in conflicting_artifacts:
            continue
        producer_id = producer_by_artifact.get(key)
        producer = producer_by_id.get(producer_id or "")
        if producer is None:
            continue
        run, workflow, project = producer
        if scope.workflow_id is not None and run.workflow_id != scope.workflow_id:
            continue
        if scope.run_id is not None and run.id != scope.run_id:
            continue
        candidate = _ArtifactRow(
            artifact=artifact,
            session=session,
            run=run,
            workflow=workflow,
            project=project,
        )
        if artifact_id is not None and artifact_id not in {
            artifact.artifact_id,
            _artifact_public_id(candidate),
        }:
            continue
        result.append(candidate)
    return sorted(result, key=lambda row: (row.artifact.created_at, row.artifact.id), reverse=True)


async def _evidence_rows(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
) -> list[_EvidenceRow]:
    query = (
        select(WorkflowRun, StudioWorkflow, StudioProject)
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

    result: list[_EvidenceRow] = []
    for run, workflow, project in (await db.execute(query)).all():
        try:
            projection = WorkflowRunProjection.model_validate(run.projection)
            batches = list_evidence_batches(projection, limit=100_000).batches
        except (TypeError, ValueError):
            continue
        for batch in batches:
            detail = get_evidence_batch(projection, batch.batchId)
            if detail is not None:
                result.append(
                    _EvidenceRow(
                        batch=batch,
                        detail=detail,
                        run=run,
                        workflow=workflow,
                        project=project,
                    )
                )
    return sorted(
        result,
        key=lambda row: (row.run.updated_at, row.batch.batchId),
        reverse=True,
    )


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


def _row_key(row: _ArtifactRow) -> tuple[str, str]:
    return row.session.id, row.artifact.artifact_id


def _artifact_public_id(row: _ArtifactRow) -> str:
    return f"{row.session.id}:{row.artifact.artifact_id}"


def _conversation_candidates(row: _ArtifactRow) -> set[str]:
    candidates: set[str] = set()
    provenance = row.artifact.provenance
    if isinstance(provenance, dict):
        provenance_run_id = provenance.get("run_id") or provenance.get("runId")
        if provenance_run_id in {None, row.run.id}:
            for key in ("conversation_id", "conversationId"):
                value = provenance.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.add(value.strip())
    return candidates


def _conversation_matches(
    conversation: AgentConversation,
    *,
    row: _ArtifactRow,
    studio_workspace_id: str,
) -> bool:
    binding = conversation.context_binding if isinstance(conversation.context_binding, dict) else {}
    if binding.get("project_id") != row.project.id:
        return False
    if binding.get("workflow_id") not in {None, row.workflow.id}:
        return False
    if binding.get("run_id") not in {None, row.run.id}:
        return False
    if binding.get("studio_workspace_id") not in {None, studio_workspace_id}:
        return False
    return True


async def _trusted_conversations(
    db: AsyncSession,
    rows: list[_ArtifactRow],
    *,
    studio_workspace_id: str,
    conversation_workspace_id: str,
) -> dict[tuple[str, str], str | None]:
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
    conversation_by_id = {conversation.id: conversation for conversation in conversations}
    trusted: dict[tuple[str, str], str | None] = {}
    for row in rows:
        valid = sorted(
            candidate
            for candidate in _conversation_candidates(row)
            if candidate in conversation_by_id
            and _conversation_matches(
                conversation_by_id[candidate],
                row=row,
                studio_workspace_id=studio_workspace_id,
            )
        )
        # A row with competing valid conversation provenance is ambiguous.  Do
        # not select by set/database order or claim ownership we cannot prove.
        trusted[_row_key(row)] = valid[0] if len(valid) == 1 else None
    return trusted


def _summary(
    row: _ArtifactRow,
    *,
    conversation_id: str | None,
) -> ProjectArtifactSummary:
    artifact = row.artifact
    provenance = artifact.provenance if isinstance(artifact.provenance, dict) else {}
    return ProjectArtifactSummary(
        id=_artifact_public_id(row),
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


def _evidence_payload(row: _EvidenceRow) -> dict[str, Any]:
    return row.detail.model_dump(mode="json", by_alias=True)


def _evidence_id(row: _EvidenceRow) -> str:
    return f"evidence-batch:{row.batch.batchId}"


def _evidence_summary(row: _EvidenceRow) -> ProjectArtifactSummary:
    payload = _evidence_payload(row)
    content_hash = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ProjectArtifactSummary(
        id=_evidence_id(row),
        artifact_id=_evidence_id(row),
        title=f"Evidence batch {row.batch.batchId}",
        media_type="application/json",
        kind="evidence_batch",
        content_hash=content_hash,
        workspace_id=row.project.workspace_id,
        project_id=row.project.id,
        workflow_id=row.workflow.id,
        run_id=row.run.id,
        session_id=None,
        conversation_id=None,
        source="workflow_run_projection",
        simulated=False,
        created_at=row.run.created_at,
        updated_at=row.run.updated_at,
    )


def _evidence_detail(row: _EvidenceRow) -> ProjectArtifactDetail:
    payload = _evidence_payload(row)
    if _content_size(payload) > MAX_ARTIFACT_PAYLOAD_BYTES:
        raise ProjectArtifactError(ProjectArtifactErrorCode.CONTENT_TOO_LARGE)
    provenance = {
        "source": "workflow_run_projection",
        "workflow_id": row.workflow.id,
        "run_id": row.run.id,
        "batch_id": row.batch.batchId,
        "manifest_uri": row.batch.manifestUri,
        "odp_ref": row.batch.odpRef,
    }
    summary = _evidence_summary(row)
    return ProjectArtifactDetail(
        **summary.model_dump(),
        schema_version="workflow.evidence-batch.v1",
        content=payload,
        payload=payload,
        provenance=provenance,
        grounding_artifact_ids=[],
        algorithm_version="workflow-run-projection-v1",
        seed=None,
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
    studio_workspace_id: str | None = None,
) -> list[ProjectArtifactSummary]:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("artifact_query_bounds_invalid")
    intelligence_rows = await _rows(db, scope=scope)
    evidence_rows = await _evidence_rows(db, scope=scope)
    combined: list[tuple[datetime, str, _ArtifactLike]] = [
        (row.artifact.created_at, row.artifact.id, row) for row in intelligence_rows
    ] + [
        (row.run.updated_at, _evidence_id(row), row) for row in evidence_rows
    ]
    combined.sort(key=lambda item: (item[0], item[1]), reverse=True)
    page = [item[2] for item in combined[offset : offset + limit]]
    page_intelligence_rows = [row for row in page if isinstance(row, _ArtifactRow)]
    conversations = await _trusted_conversations(
        db,
        page_intelligence_rows,
        studio_workspace_id=studio_workspace_id or scope.workspace_id,
        conversation_workspace_id=conversation_workspace_id or scope.workspace_id,
    )
    summaries: list[ProjectArtifactSummary] = []
    for row in page:
        if isinstance(row, _ArtifactRow):
            summaries.append(
                _summary(row, conversation_id=conversations.get(_row_key(row)))
            )
        else:
            summaries.append(_evidence_summary(row))
    return summaries


async def get_project_artifact(
    db: AsyncSession,
    *,
    scope: ProjectArtifactScope,
    artifact_id: str,
    conversation_workspace_id: str | None = None,
    studio_workspace_id: str | None = None,
) -> ProjectArtifactDetail:
    rows = await _rows(db, scope=scope, artifact_id=artifact_id)
    evidence_rows = [
        row
        for row in await _evidence_rows(db, scope=scope)
        if _evidence_id(row) == artifact_id
    ]
    if len(rows) + len(evidence_rows) == 0:
        raise ProjectArtifactError(ProjectArtifactErrorCode.ARTIFACT_NOT_FOUND)
    if len(rows) + len(evidence_rows) > 1:
        # The domain key is only unique inside a native session.  Do not choose
        # one row and accidentally expose another run when a caller omitted the
        # run scope.
        raise ProjectArtifactError(ProjectArtifactErrorCode.ARTIFACT_SCOPE_REQUIRED)
    if evidence_rows:
        return _evidence_detail(evidence_rows[0])
    row = rows[0]
    conversations = await _trusted_conversations(
        db,
        [row],
        studio_workspace_id=studio_workspace_id or scope.workspace_id,
        conversation_workspace_id=conversation_workspace_id or scope.workspace_id,
    )
    return _detail(row, conversation_id=conversations.get(_row_key(row)))


__all__ = [
    "ProjectArtifactError",
    "ProjectArtifactErrorCode",
    "ProjectArtifactScope",
    "get_project_artifact",
    "list_project_artifacts",
    "resolve_scope",
]
