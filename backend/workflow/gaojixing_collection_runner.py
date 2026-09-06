"""Durable, globally serialized worker for governed Doubao evidence collection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from inspect import isawaitable
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.gaojixing_collection import (
    GAOJIXING_GLOBAL_LEASE_ID,
    GaojixingCollectionRun,
    GaojixingCollectionRunStatus,
    GaojixingQuestionCheckpoint,
    GaojixingQuestionStatus,
    GaojixingRuntimeLease,
)
from backend.models.workflow_run import WorkflowRun
from backend.services.gaojixing_reconciliation import validated_driver_reconciliation
from backend.workflow.gaojixing_archive import (
    finalize_archive,
    promote_capture_artifacts,
    promote_verification_artifact,
    read_question_capture,
    write_question_capture,
)
from backend.workflow.gaojixing_doubao import audit_gaojixing_question_evidence
from backend.workflow.managed_gaojixing_question_batches import (
    resolve_managed_question_batch,
)

logger = logging.getLogger(__name__)

LEASE_DURATION = timedelta(seconds=30)
HEARTBEAT_INTERVAL_SECONDS = 5.0
_PUBLIC_CAPTURE_FAILURE_CODES = frozenset({
    "cdp-http-endpoint-required", "cdp-targets-unavailable", "doubao-target-missing",
    "doubao-target-ambiguous", "doubao-target-lost", "doubao-target-websocket-missing",
    "doubao-new-session-unproven",
    "doubao-new-failed", "doubao-ask-failed", "doubao-status-failed",
    "formal-chat-url-missing", "page-question-not-proven",
    "opencli-json-invalid", "opencli-runtime-unavailable", "opencli-runtime-timeout",
    "doubao-local-runtime-required", "playwright-unavailable",
    "doubao-browser-context-missing", "full-answer-missing",
    "opencli-page-answer-mismatch", "answer-changed-during-capture",
})


def _capture_failure_code(exc: Exception) -> str:
    # Never persist raw exception text: subprocess/browser errors can contain
    # account details, page content, or credentials. Only our fixed codes cross
    # the durable diagnostic boundary.
    from backend.workflow.gaojixing_doubao_driver import DoubaoDriverUnavailableError

    if isinstance(exc, DoubaoDriverUnavailableError) and exc.code in _PUBLIC_CAPTURE_FAILURE_CODES:
        return exc.code
    return "doubao-capture-failed"


WorkerOutcome = Literal[
    "workflow_resume_scheduled",
    "resume_pending",
    "waiting_verification",
    "waiting_reconciliation",
    "blocked",
    "failed",
    "busy",
    "missing",
    "lease_lost",
]


class DriverPort(Protocol):
    async def preflight(self) -> None: ...

    async def collect(self, *, question_id: str, question: str) -> dict[str, Any]: ...

    async def inspect_current(
        self,
        *,
        question_id: str,
        question: str,
        reconciliation: dict[str, str] | None = None,
    ) -> dict[str, Any] | None: ...


DriverFactory = Callable[[Path], DriverPort]


class _LeaseLostError(RuntimeError):
    pass


async def run_collection_job(
    job_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    driver_factory: DriverFactory,
    schedule_resume: Callable[[str], Any],
    storage_root: Path | str | None = None,
    signing_key: str | None = None,
) -> WorkerOutcome:
    owner = str(uuid4())
    claim = await _claim(session_factory, job_id, owner)
    if claim is None:
        async with session_factory() as session:
            job = await session.get(GaojixingCollectionRun, job_id)
            if job is None:
                return "missing"
            if job.status == GaojixingCollectionRunStatus.REVIEWING.value:
                try:
                    result = schedule_resume(job.workflow_run_id)
                    if isawaitable(result):
                        await result
                except Exception:
                    logger.exception(
                        "failed to resume Gaojixing workflow run",
                        extra={"run_id": job.workflow_run_id},
                    )
                    return "resume_pending"
                return "workflow_resume_scheduled"
            return "busy"
    fencing_token, question_batch_ref, workflow_run_id = claim
    resolved = resolve_managed_question_batch(
        question_batch_ref,
        expected_run_id=workflow_run_id,
        storage_root=storage_root,
        signing_key=signing_key,
    )
    attempt_root = (
        resolved.project_root
        / ".worker-staging"
        # Keep attempt paths below Windows' legacy path limit. The durable DB
        # fence, not a verbose directory name, is the ownership boundary.
        / f"{fencing_token}-{uuid4().hex[:8]}"
    )
    lease_lost = asyncio.Event()
    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat(
            session_factory,
            job_id,
            owner,
            fencing_token,
            heartbeat_stop,
            lease_lost,
        )
    )
    try:
        try:
            attempt_root.mkdir(parents=True, exist_ok=False)
            driver = driver_factory(attempt_root)
            await _await_driver(driver.preflight(), lease_lost)
        except _LeaseLostError:
            return "lease_lost"
        except Exception as exc:
            await _finish_job(
                session_factory,
                job_id,
                owner,
                fencing_token,
                status=GaojixingCollectionRunStatus.BLOCKED.value,
                failure={
                    "code": str(getattr(exc, "code", "doubao-driver-unavailable")),
                    "message": "Certified Doubao driver binding is unavailable",
                },
            )
            return "blocked"

        expected_rows = json.loads(
            resolved.question_bank_path.read_text(encoding="utf-8")
        )
        expected_ids = {
            phase: {str(row["id"]) for row in expected_rows.get(phase, [])}
            for phase in ("phase1", "phase2")
        }
        while True:
            async with session_factory() as session:
                workflow_run = await session.get(WorkflowRun, workflow_run_id)
                checkpoints = list(
                    (
                        await session.execute(
                            select(GaojixingQuestionCheckpoint)
                            .where(GaojixingQuestionCheckpoint.collection_run_id == job_id)
                            .order_by(GaojixingQuestionCheckpoint.position)
                        )
                    )
                    .scalars()
                    .all()
                )
            actual_ids = {
                phase: {row.question_id for row in checkpoints if row.phase == phase}
                for phase in ("phase1", "phase2")
            }
            if actual_ids != expected_ids:
                await _finish_job(
                    session_factory,
                    job_id,
                    owner,
                    fencing_token,
                    status=GaojixingCollectionRunStatus.FAILED.value,
                    failure={"code": "question-checkpoint-set-mismatch"},
                )
                return "failed"
            pending = next(
                (row for row in checkpoints if row.status != GaojixingQuestionStatus.PASSED.value),
                None,
            )
            if pending is None:
                final_outcome = await _finalize_job(
                    session_factory,
                    job_id,
                    owner,
                    fencing_token,
                    resolved.project_root,
                    checkpoints,
                )
                if final_outcome is not None:
                    return final_outcome
                try:
                    result = schedule_resume(workflow_run_id)
                    if isawaitable(result):
                        await result
                except Exception:
                    logger.exception(
                        "failed to resume Gaojixing workflow run",
                        extra={"run_id": workflow_run_id},
                    )
                    return "resume_pending"
                return "workflow_resume_scheduled"
            reconciliation = validated_driver_reconciliation(
                workflow_run.request if workflow_run is not None else None,
                workflow_run_id=workflow_run_id,
                collection_run_id=job_id,
                question_id=pending.question_id,
                question=pending.question,
            )
            if pending.phase == "phase2":
                passed_phase1 = {
                    row.question_id
                    for row in checkpoints
                    if row.phase == "phase1"
                    and row.status == GaojixingQuestionStatus.PASSED.value
                }
                if passed_phase1 != expected_ids["phase1"]:
                    await _finish_job(
                        session_factory,
                        job_id,
                        owner,
                        fencing_token,
                        status=GaojixingCollectionRunStatus.FAILED.value,
                        failure={"code": "phase2-before-exact-phase1-set"},
                    )
                    return "failed"
                invalid_phase1 = next(
                    (
                        row.question_id
                        for row in checkpoints
                        if row.phase == "phase1"
                        and _validated_passed_capture(resolved.project_root, row) is None
                    ),
                    None,
                )
                if invalid_phase1 is not None:
                    await _finish_job(
                        session_factory,
                        job_id,
                        owner,
                        fencing_token,
                        status=GaojixingCollectionRunStatus.FAILED.value,
                        failure={
                            "code": "phase1-evidence-invalid",
                            "questionId": invalid_phase1,
                        },
                    )
                    return "failed"
            outcome = await _advance_question(
                session_factory,
                job_id,
                pending,
                driver,
                resolved.project_root,
                attempt_root,
                owner,
                fencing_token,
                lease_lost,
                reconciliation,
            )
            if outcome is not None:
                return outcome
    finally:
        heartbeat_stop.set()
        await heartbeat
        await _release(session_factory, job_id, owner, fencing_token)
        _remove_attempt_root(attempt_root, resolved.project_root)


async def _advance_question(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: str,
    checkpoint: GaojixingQuestionCheckpoint,
    driver: DriverPort,
    project_root: Path,
    attempt_root: Path,
    owner: str,
    fencing_token: int,
    lease_lost: asyncio.Event,
    reconciliation: dict[str, str] | None,
) -> WorkerOutcome | None:
    if checkpoint.status in {
        GaojixingQuestionStatus.WAITING_VERIFICATION.value,
        GaojixingQuestionStatus.WAITING_RECONCILIATION.value,
        GaojixingQuestionStatus.FAILED.value,
    }:
        return {
            GaojixingQuestionStatus.WAITING_VERIFICATION.value: "waiting_verification",
            GaojixingQuestionStatus.WAITING_RECONCILIATION.value: "waiting_reconciliation",
            GaojixingQuestionStatus.FAILED.value: "failed",
        }[checkpoint.status]  # type: ignore[return-value]

    recover_only = checkpoint.status != GaojixingQuestionStatus.PENDING.value
    if not recover_only:
        updated = await _mark_in_progress(
            session_factory, job_id, checkpoint.id, owner, fencing_token
        )
        if not updated:
            return "lease_lost"
    else:
        recovered_raw = read_question_capture(project_root, checkpoint.question_id)
        if recovered_raw is not None and not _capture_violations(
            recovered_raw, checkpoint, project_root
        ):
            return await _accept_capture(
                session_factory,
                job_id,
                checkpoint,
                recovered_raw,
                project_root,
                None,
                owner,
                fencing_token,
            )

    try:
        if recover_only:
            inspection = (
                driver.inspect_current(
                    question_id=checkpoint.question_id,
                    question=checkpoint.question,
                    reconciliation=reconciliation,
                )
                if reconciliation is not None
                else driver.inspect_current(
                    question_id=checkpoint.question_id,
                    question=checkpoint.question,
                )
            )
            capture = await _await_driver(inspection, lease_lost)
        else:
            capture = await _await_driver(
                driver.collect(
                    question_id=checkpoint.question_id,
                    question=checkpoint.question,
                ),
                lease_lost,
            )
    except _LeaseLostError:
        return "lease_lost"
    except Exception as exc:
        waiting_saved = await _mark_waiting(
            session_factory,
            job_id,
            checkpoint.id,
            owner,
            fencing_token,
            kind="reconciliation",
            artifact_ref=None,
            failure={"code": _capture_failure_code(exc)},
        )
        return "waiting_reconciliation" if waiting_saved else "lease_lost"
    if capture is None:
        waiting_saved = await _mark_waiting(
            session_factory,
            job_id,
            checkpoint.id,
            owner,
            fencing_token,
            kind="reconciliation",
            artifact_ref=None,
            failure={"code": "doubao-answer-not-proven"},
        )
        return "waiting_reconciliation" if waiting_saved else "lease_lost"
    if capture.get("status") == "verification_required":
        kind = str(capture.get("verification", {}).get("kind") or "")
        waiting_saved = await _mark_verification_waiting(
            session_factory,
            job_id,
            checkpoint.id,
            owner,
            fencing_token,
            kind=kind,
            capture=capture,
            attempt_root=attempt_root,
            project_root=project_root,
        )
        if waiting_saved == "invalid":
            failed = await _fail_question(
                session_factory,
                job_id,
                checkpoint.id,
                owner,
                fencing_token,
                "verification-evidence-missing",
            )
            return "failed" if failed else "lease_lost"
        return "waiting_verification" if waiting_saved == "saved" else "lease_lost"
    violations = _capture_violations(capture, checkpoint, attempt_root)
    if violations:
        failed = await _fail_question(
            session_factory,
            job_id,
            checkpoint.id,
            owner,
            fencing_token,
            "evidence-quality-failed",
            details={"violations": violations},
        )
        return "failed" if failed else "lease_lost"
    return await _accept_capture(
        session_factory,
        job_id,
        checkpoint,
        capture,
        project_root,
        attempt_root,
        owner,
        fencing_token,
    )


def _capture_violations(
    capture: dict[str, Any],
    checkpoint: GaojixingQuestionCheckpoint,
    project_root: Path,
) -> list[str]:
    violations = []
    if capture.get("id") != checkpoint.question_id:
        violations.append("question-id-mismatch")
    if capture.get("question") != checkpoint.question:
        violations.append("original-question-mismatch")
    violations.extend(audit_gaojixing_question_evidence(capture, project_root=project_root))
    return sorted(set(violations))


async def _accept_capture(
    session_factory,
    job_id,
    checkpoint,
    capture,
    project_root,
    attempt_root: Path | None,
    owner,
    fencing_token,
) -> WorkerOutcome | None:
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, fencing_token):
            await session.rollback()
            return "lease_lost"
        try:
            canonical_capture = (
                promote_capture_artifacts(
                    attempt_root,
                    project_root,
                    capture,
                )
                if attempt_root is not None
                else capture
            )
        except (OSError, ValueError):
            await _fail_capture_locked(
                session,
                job_id,
                checkpoint.id,
                code="artifact-promotion-failed",
            )
            await session.commit()
            return "failed"
        canonical_violations = _capture_violations(
            canonical_capture,
            checkpoint,
            project_root,
        )
        if canonical_violations:
            await _fail_capture_locked(
                session,
                job_id,
                checkpoint.id,
                code="canonical-evidence-quality-failed",
                details={"violations": canonical_violations},
            )
            await session.commit()
            return "failed"
        raw_digest = write_question_capture(project_root, canonical_capture)
        updated = await session.execute(
            update(GaojixingQuestionCheckpoint)
            .where(
                GaojixingQuestionCheckpoint.id == checkpoint.id,
                GaojixingQuestionCheckpoint.collection_run_id == job_id,
            )
            .values(
                status=GaojixingQuestionStatus.PASSED.value,
                chat_url=str(canonical_capture.get("chat_url") or ""),
                raw_digest=raw_digest,
                failure=None,
            )
        )
        job_updated = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(current_question_id=None)
        )
        if updated.rowcount != 1 or job_updated.rowcount != 1:
            await session.rollback()
            return "lease_lost"
        await session.commit()
    return None


async def _fail_capture_locked(
    session: AsyncSession,
    job_id: str,
    checkpoint_id: str,
    *,
    code: str,
    details: dict[str, Any] | None = None,
) -> None:
    failure = {"code": code, **(details or {})}
    await session.execute(
        update(GaojixingCollectionRun)
        .where(GaojixingCollectionRun.id == job_id)
        .values(
            status=GaojixingCollectionRunStatus.FAILED.value,
            failure=failure,
            finished_at=datetime.now(UTC),
            lease_owner=None,
            lease_fencing_token=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
    )
    await session.execute(
        update(GaojixingQuestionCheckpoint)
        .where(GaojixingQuestionCheckpoint.id == checkpoint_id)
        .values(status=GaojixingQuestionStatus.FAILED.value, failure=failure)
    )


async def _claim(session_factory, job_id: str, owner: str):
    now = datetime.now(UTC)
    async with session_factory() as session:
        job = await session.get(GaojixingCollectionRun, job_id)
        if job is None:
            return None
        lease = await session.get(GaojixingRuntimeLease, GAOJIXING_GLOBAL_LEASE_ID)
        if lease is None:
            lease = GaojixingRuntimeLease(id=GAOJIXING_GLOBAL_LEASE_ID)
            session.add(lease)
            await session.flush()
        acquired = await session.execute(
            update(GaojixingRuntimeLease)
            .where(
                GaojixingRuntimeLease.id == GAOJIXING_GLOBAL_LEASE_ID,
                or_(
                    GaojixingRuntimeLease.owner.is_(None),
                    GaojixingRuntimeLease.expires_at.is_(None),
                    GaojixingRuntimeLease.expires_at <= now,
                ),
            )
            .values(
                owner=owner,
                collection_run_id=job_id,
                fencing_token=GaojixingRuntimeLease.fencing_token + 1,
                heartbeat_at=now,
                expires_at=now + LEASE_DURATION,
            )
            .execution_options(synchronize_session=False)
        )
        if acquired.rowcount != 1:
            await session.rollback()
            return None
        token = await session.scalar(
            select(GaojixingRuntimeLease.fencing_token).where(
                GaojixingRuntimeLease.id == GAOJIXING_GLOBAL_LEASE_ID
            )
        )
        claimed = await session.execute(
            update(GaojixingCollectionRun)
            .where(
                GaojixingCollectionRun.id == job_id,
                or_(
                    GaojixingCollectionRun.status
                    == GaojixingCollectionRunStatus.QUEUED.value,
                    and_(
                        GaojixingCollectionRun.status
                        == GaojixingCollectionRunStatus.RUNNING.value,
                        GaojixingCollectionRun.lease_expires_at <= now,
                    ),
                ),
            )
            .values(
                status=GaojixingCollectionRunStatus.RUNNING.value,
                lease_owner=owner,
                lease_fencing_token=token,
                heartbeat_at=now,
                lease_expires_at=now + LEASE_DURATION,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return None
        await session.commit()
        return int(token), job.question_batch_ref, job.workflow_run_id


async def _mark_in_progress(session_factory, job_id, checkpoint_id, owner, token) -> bool:
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return False
        result = await session.execute(
            update(GaojixingQuestionCheckpoint)
            .where(
                GaojixingQuestionCheckpoint.id == checkpoint_id,
                GaojixingQuestionCheckpoint.status == GaojixingQuestionStatus.PENDING.value,
            )
            .values(
                status=GaojixingQuestionStatus.IN_PROGRESS.value,
                attempt=GaojixingQuestionCheckpoint.attempt + 1,
            )
        )
        checkpoint = await session.get(GaojixingQuestionCheckpoint, checkpoint_id)
        if checkpoint is not None:
            await session.execute(
                update(GaojixingCollectionRun)
                .where(GaojixingCollectionRun.id == job_id)
                .values(current_question_id=checkpoint.question_id)
            )
        await session.commit()
        return result.rowcount == 1


async def _mark_waiting(
    session_factory,
    job_id,
    checkpoint_id,
    owner,
    token,
    *,
    kind,
    artifact_ref,
    failure=None,
) -> bool:
    checkpoint_status = (
        GaojixingQuestionStatus.WAITING_RECONCILIATION.value
        if kind == "reconciliation"
        else GaojixingQuestionStatus.WAITING_VERIFICATION.value
    )
    run_status = (
        GaojixingCollectionRunStatus.WAITING_RECONCILIATION.value
        if kind == "reconciliation"
        else GaojixingCollectionRunStatus.WAITING_VERIFICATION.value
    )
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return False
        fenced = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(
                status=run_status,
                waiting_kind=kind,
                waiting_artifact_ref=artifact_ref,
                failure=failure,
                lease_owner=None,
                lease_fencing_token=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        if fenced.rowcount != 1:
            await session.rollback()
            return False
        await session.execute(
            update(GaojixingQuestionCheckpoint)
            .where(GaojixingQuestionCheckpoint.id == checkpoint_id)
            .values(
                status=checkpoint_status,
                artifact_refs=[artifact_ref] if artifact_ref else [],
                failure=failure,
            )
        )
        await session.commit()
        return True


async def _mark_verification_waiting(
    session_factory,
    job_id,
    checkpoint_id,
    owner,
    token,
    *,
    kind,
    capture,
    attempt_root,
    project_root,
) -> Literal["saved", "invalid", "lease_lost"]:
    if kind not in {"captcha", "login", "access"}:
        return "invalid"
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return "lease_lost"
        try:
            artifact_ref = promote_verification_artifact(
                attempt_root,
                project_root,
                capture,
            )
        except (OSError, ValueError):
            await session.rollback()
            return "invalid"
        fenced = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(
                status=GaojixingCollectionRunStatus.WAITING_VERIFICATION.value,
                waiting_kind=kind,
                waiting_artifact_ref=artifact_ref,
                lease_owner=None,
                lease_fencing_token=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        checkpoint = await session.execute(
            update(GaojixingQuestionCheckpoint)
            .where(GaojixingQuestionCheckpoint.id == checkpoint_id)
            .values(
                status=GaojixingQuestionStatus.WAITING_VERIFICATION.value,
                artifact_refs=[artifact_ref],
            )
        )
        if fenced.rowcount != 1 or checkpoint.rowcount != 1:
            await session.rollback()
            return "lease_lost"
        await session.commit()
        return "saved"


async def _fail_question(
    session_factory,
    job_id,
    checkpoint_id,
    owner,
    token,
    code,
    *,
    details=None,
) -> bool:
    failure = {"code": code, **(details or {})}
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return False
        fenced = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(
                status=GaojixingCollectionRunStatus.FAILED.value,
                failure=failure,
                finished_at=datetime.now(UTC),
                lease_owner=None,
                lease_fencing_token=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        if fenced.rowcount == 1:
            await session.execute(
                update(GaojixingQuestionCheckpoint)
                .where(GaojixingQuestionCheckpoint.id == checkpoint_id)
                .values(status=GaojixingQuestionStatus.FAILED.value, failure=failure)
            )
            await session.commit()
            return True
        else:
            await session.rollback()
            return False


async def _finish_job(
    session_factory,
    job_id,
    owner,
    token,
    *,
    status,
    failure=None,
) -> bool:
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return False
        result = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(
                status=status,
                failure=failure,
                finished_at=datetime.now(UTC),
                lease_owner=None,
                lease_fencing_token=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def _finalize_job(
    session_factory,
    job_id,
    owner,
    token,
    project_root,
    checkpoints,
) -> WorkerOutcome | None:
    async with session_factory() as session:
        if not await _lock_fence(session, job_id, owner, token):
            await session.rollback()
            return "lease_lost"
        captures: list[dict[str, Any]] = []
        invalid_checkpoint = None
        for checkpoint in checkpoints:
            capture = _validated_passed_capture(project_root, checkpoint)
            if capture is None:
                invalid_checkpoint = checkpoint
                break
            captures.append(capture)
        if invalid_checkpoint is not None:
            await _fail_capture_locked(
                session,
                job_id,
                invalid_checkpoint.id,
                code="passed-evidence-invalid",
                details={"questionId": invalid_checkpoint.question_id},
            )
            await session.commit()
            return "failed"
        finalize_archive(project_root, captures)
        result = await session.execute(
            update(GaojixingCollectionRun)
            .where(GaojixingCollectionRun.id == job_id)
            .values(
                status=GaojixingCollectionRunStatus.REVIEWING.value,
                failure=None,
                finished_at=None,
                lease_owner=None,
                lease_fencing_token=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
        )
        await session.commit()
        return None if result.rowcount == 1 else "lease_lost"


def _validated_passed_capture(
    project_root: Path,
    checkpoint: GaojixingQuestionCheckpoint,
) -> dict[str, Any] | None:
    if (
        checkpoint.status != GaojixingQuestionStatus.PASSED.value
        or not checkpoint.raw_digest
    ):
        return None
    path = project_root / "raw" / f"{checkpoint.question_id}.json"
    try:
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != checkpoint.raw_digest:
            return None
        capture = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(capture, dict) or _capture_violations(
        capture,
        checkpoint,
        project_root,
    ):
        return None
    return capture


async def _lock_fence(session, job_id: str, owner: str, token: int) -> bool:
    """Lock and validate both the global side-effect lease and its job fence."""

    dialect = session.get_bind().dialect.name
    global_predicates = (
        GaojixingRuntimeLease.id == GAOJIXING_GLOBAL_LEASE_ID,
        GaojixingRuntimeLease.owner == owner,
        GaojixingRuntimeLease.collection_run_id == job_id,
        GaojixingRuntimeLease.fencing_token == token,
    )
    job_predicates = (
        GaojixingCollectionRun.id == job_id,
        GaojixingCollectionRun.lease_owner == owner,
        GaojixingCollectionRun.lease_fencing_token == token,
        GaojixingCollectionRun.status == GaojixingCollectionRunStatus.RUNNING.value,
    )
    if dialect == "sqlite":
        global_lock = await session.execute(
            update(GaojixingRuntimeLease)
            .where(*global_predicates)
            .values(fencing_token=GaojixingRuntimeLease.fencing_token)
            .execution_options(synchronize_session=False)
        )
        if global_lock.rowcount != 1:
            return False
        job_lock = await session.execute(
            update(GaojixingCollectionRun)
            .where(*job_predicates)
            .values(lease_fencing_token=GaojixingCollectionRun.lease_fencing_token)
            .execution_options(synchronize_session=False)
        )
        return job_lock.rowcount == 1
    global_id = await session.scalar(
        select(GaojixingRuntimeLease.id)
        .where(*global_predicates)
        .with_for_update()
    )
    if global_id is None:
        return False
    job_id_value = await session.scalar(
        select(GaojixingCollectionRun.id)
        .where(*job_predicates)
        .with_for_update()
    )
    return job_id_value is not None


async def _heartbeat(session_factory, job_id, owner, token, stop, lost) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        now = datetime.now(UTC)
        async with session_factory() as session:
            global_result = await session.execute(
                update(GaojixingRuntimeLease)
                .where(
                    GaojixingRuntimeLease.id == GAOJIXING_GLOBAL_LEASE_ID,
                    GaojixingRuntimeLease.owner == owner,
                    GaojixingRuntimeLease.collection_run_id == job_id,
                    GaojixingRuntimeLease.fencing_token == token,
                )
                .values(heartbeat_at=now, expires_at=now + LEASE_DURATION)
            )
            job_result = await session.execute(
                update(GaojixingCollectionRun)
                .where(
                    GaojixingCollectionRun.id == job_id,
                    GaojixingCollectionRun.lease_owner == owner,
                    GaojixingCollectionRun.lease_fencing_token == token,
                    GaojixingCollectionRun.status == GaojixingCollectionRunStatus.RUNNING.value,
                )
                .values(heartbeat_at=now, lease_expires_at=now + LEASE_DURATION)
            )
            await session.commit()
        if global_result.rowcount != 1 or job_result.rowcount != 1:
            lost.set()
            return


async def _release(session_factory, job_id, owner, token) -> None:
    async with session_factory() as session:
        await session.execute(
            update(GaojixingRuntimeLease)
            .where(
                GaojixingRuntimeLease.id == GAOJIXING_GLOBAL_LEASE_ID,
                GaojixingRuntimeLease.owner == owner,
                GaojixingRuntimeLease.collection_run_id == job_id,
                GaojixingRuntimeLease.fencing_token == token,
            )
            .values(
                owner=None,
                collection_run_id=None,
                heartbeat_at=None,
                expires_at=None,
            )
        )
        await session.commit()


async def _await_driver(coro, lease_lost: asyncio.Event):
    work = asyncio.create_task(coro)
    lost = asyncio.create_task(lease_lost.wait())
    try:
        done, _ = await asyncio.wait({work, lost}, return_when=asyncio.FIRST_COMPLETED)
        if lost in done:
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work
            raise _LeaseLostError
        return await work
    finally:
        if not lost.done():
            lost.cancel()
            with suppress(asyncio.CancelledError):
                await lost


def _remove_attempt_root(attempt_root: Path, project_root: Path) -> None:
    staging_parent = (project_root / ".worker-staging").resolve()
    resolved = attempt_root.resolve()
    try:
        resolved.relative_to(staging_parent)
    except ValueError:
        return
    shutil.rmtree(resolved, ignore_errors=True)
    with suppress(OSError):
        staging_parent.rmdir()


__all__ = ["DriverFactory", "DriverPort", "WorkerOutcome", "run_collection_job"]
