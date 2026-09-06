"""Creation and inspection of durable Gaojixing collection intent."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import queue_after_commit
from backend.models.gaojixing_collection import (
    GAOJIXING_GLOBAL_LEASE_ID,
    GaojixingCollectionRun,
    GaojixingCollectionRunStatus,
    GaojixingQuestionCheckpoint,
    GaojixingQuestionStatus,
    GaojixingRuntimeLease,
)
from backend.models.studio import StudioProject, StudioWorkflow
from backend.models.workflow_run import WorkflowRun
from backend.services.gaojixing_reconciliation import (
    FORMAL_CHAT_URL,
    append_server_reconciliation,
    build_reconciliation_record,
    read_managed_target_binding,
)
from backend.workflow.managed_gaojixing_question_batches import (
    ManagedQuestionBatchError,
    resolve_managed_question_batch,
)

DispatchCallback = Callable[[str], Any]


class GaojixingCollectionConflictError(ValueError):
    """A workflow Run is already bound to different collection intent."""


@dataclass(frozen=True)
class GaojixingReconciliationAuthorization:
    expected_chat_url: str
    governed_workspace_id: str
    studio_workspace_id: str
    project_id: str
    workflow_id: str
    actor_user_id: str
    actor_subject: str
    actor_auth_method: str


async def ensure_collection(
    session: AsyncSession,
    *,
    workflow_run_id: str,
    node_id: str,
    question_batch_ref: str,
    dispatch: DispatchCallback | None = None,
    storage_root: Path | str | None = None,
    signing_key: str | None = None,
) -> GaojixingCollectionRun:
    """Idempotently freeze checkpoints and publish work only after commit."""

    resolved = resolve_managed_question_batch(
        question_batch_ref,
        expected_run_id=workflow_run_id,
        storage_root=storage_root,
        signing_key=signing_key,
    )
    existing = await session.scalar(
        select(GaojixingCollectionRun).where(
            GaojixingCollectionRun.workflow_run_id == workflow_run_id
        )
    )
    if existing is not None:
        _validate_existing_intent(
            existing,
            node_id=node_id,
            question_batch_ref=question_batch_ref,
            digest=resolved.digest,
        )
        return existing

    document = json.loads(resolved.question_bank_path.read_text(encoding="utf-8"))
    rows = [
        (phase, row)
        for phase in ("phase1", "phase2")
        for row in document.get(phase, [])
    ]
    candidate = GaojixingCollectionRun(
        workflow_run_id=workflow_run_id,
        node_id=node_id,
        question_batch_ref=question_batch_ref,
        question_bank_digest=resolved.digest,
    )
    try:
        async with session.begin_nested():
            session.add(candidate)
            await session.flush()
    except IntegrityError:
        job = await session.scalar(
            select(GaojixingCollectionRun).where(
                GaojixingCollectionRun.workflow_run_id == workflow_run_id
            )
        )
        if job is None:
            raise
        _validate_existing_intent(
            job,
            node_id=node_id,
            question_batch_ref=question_batch_ref,
            digest=resolved.digest,
        )
        return job
    job = candidate
    session.add_all(
        [
            GaojixingQuestionCheckpoint(
                collection_run_id=job.id,
                question_id=str(row["id"]),
                question=str(row["question"]),
                phase=phase,
                position=position,
            )
            for position, (phase, row) in enumerate(rows, start=1)
        ]
    )
    if await session.get(GaojixingRuntimeLease, GAOJIXING_GLOBAL_LEASE_ID) is None:
        try:
            async with session.begin_nested():
                session.add(GaojixingRuntimeLease(id=GAOJIXING_GLOBAL_LEASE_ID))
                await session.flush()
        except IntegrityError:
            if (
                await session.get(GaojixingRuntimeLease, GAOJIXING_GLOBAL_LEASE_ID)
                is None
            ):
                raise
    await session.flush()

    callback = dispatch or _dispatch_collection

    async def publish() -> None:
        result = callback(job.id)
        if isawaitable(result):
            await result

    queue_after_commit(session, publish)
    return job


def _validate_existing_intent(
    job: GaojixingCollectionRun,
    *,
    node_id: str,
    question_batch_ref: str,
    digest: str,
) -> None:
    if (
        job.question_bank_digest != digest
        or job.node_id != node_id
        or job.question_batch_ref != question_batch_ref
    ):
        raise GaojixingCollectionConflictError(
            "Workflow Run is already bound to another question collection"
        )


async def resume_collection(
    session: AsyncSession,
    *,
    job_id: str,
    dispatch: DispatchCallback | None = None,
    reconciliation: GaojixingReconciliationAuthorization | None = None,
) -> GaojixingCollectionRun | None:
    """Explicitly requeue a human-cleared checkpoint without permitting a new ask."""

    job = await session.scalar(
        select(GaojixingCollectionRun)
        .where(GaojixingCollectionRun.id == job_id)
        .with_for_update()
    )
    if job is None:
        return None
    if job.status not in {
        GaojixingCollectionRunStatus.WAITING_VERIFICATION.value,
        GaojixingCollectionRunStatus.WAITING_RECONCILIATION.value,
    }:
        raise GaojixingCollectionConflictError(
            "Gaojixing collection is not waiting for human recovery"
        )
    checkpoint = await session.scalar(
        select(GaojixingQuestionCheckpoint).where(
            GaojixingQuestionCheckpoint.collection_run_id == job.id,
            GaojixingQuestionCheckpoint.question_id == job.current_question_id,
        )
    )
    if checkpoint is None or checkpoint.status not in {
        GaojixingQuestionStatus.WAITING_VERIFICATION.value,
        GaojixingQuestionStatus.WAITING_RECONCILIATION.value,
    }:
        raise GaojixingCollectionConflictError(
            "Waiting collection has no matching resumable checkpoint"
        )
    if reconciliation is not None:
        await _record_explicit_reconciliation(
            session,
            job=job,
            checkpoint=checkpoint,
            authorization=reconciliation,
        )
    checkpoint.status = GaojixingQuestionStatus.IN_PROGRESS.value
    job.status = GaojixingCollectionRunStatus.QUEUED.value
    job.waiting_kind = None
    job.waiting_artifact_ref = None
    job.lease_owner = None
    job.lease_fencing_token = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    await session.flush()

    callback = dispatch or _dispatch_collection

    async def publish() -> None:
        result = callback(job.id)
        if isawaitable(result):
            await result

    queue_after_commit(session, publish)
    return job


async def _record_explicit_reconciliation(
    session: AsyncSession,
    *,
    job: GaojixingCollectionRun,
    checkpoint: GaojixingQuestionCheckpoint,
    authorization: GaojixingReconciliationAuthorization,
) -> None:
    expected_chat_url = authorization.expected_chat_url.strip()
    if not FORMAL_CHAT_URL.fullmatch(expected_chat_url):
        raise GaojixingCollectionConflictError("Expected Doubao chat URL is invalid")
    workflow_run = await session.get(WorkflowRun, job.workflow_run_id)
    if workflow_run is None or workflow_run.id != job.workflow_run_id:
        raise GaojixingCollectionConflictError("Workflow Run is not available")
    workflow = await session.scalar(
        select(StudioWorkflow)
        .join(StudioProject, StudioProject.id == StudioWorkflow.project_id)
        .where(
            StudioWorkflow.id == authorization.workflow_id,
            StudioWorkflow.project_id == authorization.project_id,
            StudioProject.workspace_id == authorization.studio_workspace_id,
        )
    )
    if workflow is None or workflow_run.workflow_id != workflow.id:
        raise GaojixingCollectionConflictError(
            "Workflow Run is not owned by the requested Studio scope"
        )
    request_payload = (
        workflow_run.request.get("input", {}).get("payload", {})
        if isinstance(workflow_run.request, dict)
        else {}
    )
    if not isinstance(request_payload, dict) or (
        request_payload.get("questionBatchRef") != job.question_batch_ref
    ):
        raise GaojixingCollectionConflictError(
            "Workflow Run question package does not match the collection"
        )
    try:
        resolved = resolve_managed_question_batch(
            job.question_batch_ref,
            expected_run_id=workflow_run.id,
        )
        question_bank = json.loads(resolved.question_bank_path.read_text(encoding="utf-8"))
    except (ManagedQuestionBatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise GaojixingCollectionConflictError("Managed question package is unavailable") from exc
    if not isinstance(question_bank, dict):
        raise GaojixingCollectionConflictError("Managed question package is unavailable")
    matching_questions = [
        row
        for phase in ("phase1", "phase2")
        for row in question_bank.get(phase, [])
        if isinstance(row, dict) and row.get("id") == checkpoint.question_id
    ]
    if len(matching_questions) != 1 or matching_questions[0].get("question") != checkpoint.question:
        raise GaojixingCollectionConflictError(
            "Current checkpoint does not match the managed question package"
        )
    target = read_managed_target_binding(
        resolved.project_root,
        question_id=checkpoint.question_id,
        question=checkpoint.question,
    )
    if target is None:
        raise GaojixingCollectionConflictError(
            "Current checkpoint has no valid managed target journal"
        )
    existing_url = target.get("conversation_url")
    if existing_url is not None and existing_url != expected_chat_url:
        raise GaojixingCollectionConflictError(
            "Expected Doubao chat URL conflicts with the managed target journal"
        )
    record = build_reconciliation_record(
        governed_workspace_id=authorization.governed_workspace_id,
        studio_workspace_id=authorization.studio_workspace_id,
        project_id=authorization.project_id,
        workflow_id=authorization.workflow_id,
        run_id=workflow_run.id,
        collection_run_id=job.id,
        question_id=checkpoint.question_id,
        question=checkpoint.question,
        target_id=str(target["target_id"]),
        expected_chat_url=expected_chat_url,
        actor_user_id=authorization.actor_user_id,
        actor_subject=authorization.actor_subject,
        actor_auth_method=authorization.actor_auth_method,
    )
    try:
        workflow_run.request = append_server_reconciliation(workflow_run.request, record)
    except ValueError as exc:
        raise GaojixingCollectionConflictError(str(exc)) from exc
    await session.flush()


async def mark_collection_succeeded(
    session: AsyncSession,
    *,
    workflow_run_id: str,
) -> bool:
    """Acknowledge that the same workflow Run committed HDA certification."""

    job = await session.scalar(
        select(GaojixingCollectionRun)
        .where(GaojixingCollectionRun.workflow_run_id == workflow_run_id)
        .with_for_update()
    )
    if job is None:
        return False
    if job.status == GaojixingCollectionRunStatus.SUCCEEDED.value:
        return True
    if job.status != GaojixingCollectionRunStatus.REVIEWING.value:
        return False
    from datetime import UTC, datetime

    job.status = GaojixingCollectionRunStatus.SUCCEEDED.value
    job.finished_at = datetime.now(UTC)
    await session.flush()
    return True


async def mark_collection_review_failed(
    session: AsyncSession,
    *,
    workflow_run_id: str,
    code: str,
) -> bool:
    """Persist a terminal HDA rejection after the same-run replay."""

    job = await session.scalar(
        select(GaojixingCollectionRun)
        .where(GaojixingCollectionRun.workflow_run_id == workflow_run_id)
        .with_for_update()
    )
    if job is None or job.status != GaojixingCollectionRunStatus.REVIEWING.value:
        return False
    from datetime import UTC, datetime

    job.status = GaojixingCollectionRunStatus.FAILED.value
    job.failure = {"code": code}
    job.finished_at = datetime.now(UTC)
    await session.flush()
    return True


async def _dispatch_collection(job_id: str) -> None:
    """Hand committed work to the configured local or Celery runtime."""

    from backend.workflow.gaojixing_worker_runtime import dispatch_collection_job

    dispatch_collection_job(job_id)


__all__ = [
    "GaojixingCollectionConflictError",
    "GaojixingReconciliationAuthorization",
    "ensure_collection",
    "mark_collection_succeeded",
    "mark_collection_review_failed",
    "resume_collection",
]
