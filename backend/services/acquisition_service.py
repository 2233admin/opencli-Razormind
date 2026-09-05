import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.acquisition import AcquisitionExecution, AcquisitionExecutionStatus
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkflowVersion
from backend.models.workflow_run import WorkflowRun
from backend.schemas.acquisition import AcquisitionRunCorrelation, AcquisitionSubmission


class AcquisitionRunCorrelationError(ValueError):
    code = "workflow_run_correlation_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True)
class SubmissionOutcome:
    execution: AcquisitionExecution
    idempotency_match: bool
    created: bool


def request_fingerprint(body: AcquisitionSubmission) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def get_by_idempotency_key(
    db: AsyncSession, idempotency_key: str
) -> AcquisitionExecution | None:
    result = await db.execute(
        select(AcquisitionExecution).where(
            AcquisitionExecution.idempotency_key == idempotency_key
        )
    )
    return result.scalar_one_or_none()


async def get_execution(
    db: AsyncSession, execution_id: str
) -> AcquisitionExecution | None:
    return await db.get(AcquisitionExecution, execution_id)


async def submit_execution(
    db: AsyncSession, body: AcquisitionSubmission
) -> SubmissionOutcome:
    fingerprint = request_fingerprint(body)
    existing = await get_by_idempotency_key(db, body.idempotency_key)
    if existing is not None:
        return SubmissionOutcome(
            execution=existing,
            idempotency_match=existing.request_fingerprint == fingerprint,
            created=False,
        )

    correlation = body.workflow_run_correlation
    if correlation is not None:
        await _validate_workflow_run_correlation(db, correlation)

    execution = AcquisitionExecution(
        request_id=body.request_id,
        idempotency_key=body.idempotency_key,
        request_fingerprint=fingerprint,
        capability_id=body.capability.id,
        capability_version=body.capability.version,
        output_schema_version=body.output_schema_version,
        input_payload=body.input,
        environment=body.environment,
        required_artifacts=body.required_artifacts,
        geo_refs=body.geo_refs,
        workspace_id=correlation.workspace_id if correlation is not None else None,
        project_id=correlation.project_id if correlation is not None else None,
        workflow_id=correlation.workflow_id if correlation is not None else None,
        run_id=correlation.run_id if correlation is not None else None,
        status=AcquisitionExecutionStatus.ACCEPTED,
        artifact_refs=[],
    )
    db.add(execution)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_by_idempotency_key(db, body.idempotency_key)
        if existing is None:
            raise
        return SubmissionOutcome(
            execution=existing,
            idempotency_match=existing.request_fingerprint == fingerprint,
            created=False,
        )
    await db.refresh(execution)
    return SubmissionOutcome(
        execution=execution, idempotency_match=True, created=True
    )


async def _validate_workflow_run_correlation(
    db: AsyncSession,
    correlation: AcquisitionRunCorrelation,
) -> None:
    project = await db.get(StudioProject, correlation.project_id)
    workflow = await db.get(StudioWorkflow, correlation.workflow_id)
    run = await db.get(WorkflowRun, correlation.run_id)
    if (
        project is None
        or project.workspace_id != correlation.workspace_id
        or project.archived
        or workflow is None
        or workflow.project_id != correlation.project_id
        or workflow.archived
        or run is None
        or run.workflow_id != correlation.workflow_id
        or run.studio_workflow_version_id is None
    ):
        raise AcquisitionRunCorrelationError()
    version = await db.get(StudioWorkflowVersion, run.studio_workflow_version_id)
    if version is None or version.workflow_id != correlation.workflow_id:
        raise AcquisitionRunCorrelationError()


async def cancel_execution(
    db: AsyncSession, execution: AcquisitionExecution
) -> AcquisitionExecution:
    next_status = execution.status.cancel()
    if next_status != execution.status:
        execution.status = next_status
        execution.lease_owner = None
        execution.heartbeat_at = None
        execution.lease_expires_at = None
        await db.commit()
        await db.refresh(execution)
    return execution


async def queue_execution(
    db: AsyncSession, execution: AcquisitionExecution
) -> AcquisitionExecution:
    if execution.status == AcquisitionExecutionStatus.ACCEPTED:
        execution.status = AcquisitionExecutionStatus.QUEUED
        await db.commit()
        await db.refresh(execution)
    return execution
