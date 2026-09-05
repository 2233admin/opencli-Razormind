"""Celery tasks for async pipeline execution."""

import asyncio
from datetime import UTC, datetime
import logging
from typing import Any

from celery import Task

from backend.worker.control_plane_client import post_control_plane
from backend.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="run_gaojixing_collection")
def run_gaojixing_collection(job_id: str) -> str:
    """Advance durable GJX work and explicitly requeue global-lease contention."""

    from backend.workflow.gaojixing_worker_runtime import execute_collection_job

    outcome = _run_async(execute_collection_job(job_id))
    if outcome in {"busy", "resume_pending"}:
        run_gaojixing_collection.apply_async(kwargs={"job_id": job_id}, countdown=2)
    return outcome


@celery_app.task(name="run_acquisition")
def run_acquisition(execution_id: str) -> None:
    """Execute one durable managed-acquisition record."""
    from backend.acquisition.runner import run_acquisition_execution

    _run_async(run_acquisition_execution(execution_id))


@celery_app.task(name="resume_workflow_image_generation")
def resume_workflow_image_generation(
    job_id: str,
    run_id: str,
    node_id: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resume one waiting image node after final assets are platform-owned.

    Invoke queue completion alone must not call this task. The image adapter
    schedules it only after asset ingestion has committed successfully.
    """

    return _run_async(_resume_workflow_image_generation(job_id, run_id, node_id, assets))


@celery_app.task(
    name="run_image_generation_job",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)
def run_image_generation_job(job_id: str) -> str:
    """Advance one durable ImageStudio job step and arrange later polling."""

    from backend.image_studio.worker_runtime import execute_image_generation_job

    def schedule_poll(pending_job_id: str) -> None:
        run_image_generation_job.apply_async(args=[pending_job_id], countdown=2)

    def schedule_resume(
        run_id: str, node_id: str, assets: list[dict[str, Any]]
    ) -> None:
        resume_workflow_image_generation.delay(job_id, run_id, node_id, assets)

    return _run_async(
        execute_image_generation_job(
            job_id,
            schedule_poll=schedule_poll,
            schedule_resume=schedule_resume,
        )
    )


@celery_app.task(name="cancel_image_generation_job")
def cancel_image_generation_job(job_id: str) -> str:
    """Reconcile cancellation with the private sidecar in a worker."""

    from backend.image_studio.worker_runtime import (
        cancel_image_generation_job as cancel_job,
    )

    return _run_async(cancel_job(job_id))


async def _resume_workflow_image_generation(
    job_id: str,
    run_id: str,
    node_id: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    from backend.database import AsyncSessionLocal
    from backend.schemas.workflow import WorkflowRunSourceOutputsRequest
    from backend.services import image_studio_service
    from backend.workflow.opencli_hda_tracer import (
        continue_workflow_run_with_source_outputs,
    )

    async with AsyncSessionLocal() as session:
        try:
            canonical_assets = await image_studio_service.prepare_workflow_image_resume(
                session,
                job_id=job_id,
                run_id=run_id,
                node_id=node_id,
                assets=assets,
            )
        except image_studio_service.ImageStudioError:
            logger.warning(
                "Rejected image workflow resume for job=%s run=%s node=%s",
                job_id,
                run_id,
                node_id,
                exc_info=True,
            )
            return None
        if canonical_assets is None:
            return None
        from backend.config import get_settings
        from backend.workflow.plugin_registry import build_workflow_plugin_registry

        projection = await continue_workflow_run_with_source_outputs(
            run_id,
            WorkflowRunSourceOutputsRequest(sourceOutputs={node_id: canonical_assets}),
            session=session,
            plugins=build_workflow_plugin_registry(get_settings()),
        )
        if projection is None:
            return None
        await session.commit()
        return projection.model_dump(mode="json")


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in a Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _AlertOnRetriesExhaustedTask(Task):
    """Emits a distinct, observable signal when a task's retries are exhausted.

    ``autoretry_for=(Exception,)`` already retries any failure; Celery's own
    ``Task.retry()`` re-raises the original exception (instead of scheduling
    another attempt) once ``request.retries >= max_retries``, which is what
    finally lands here in ``on_failure``. Without this, a source going dark
    (every attempt fails) just ends as one more ``failed`` TaskRun row,
    indistinguishable from a single one-off failure — a fleet operator has no
    signal that a source needs attention, just N separately-failed runs (P1,
    错误上浮契约/轮子5). This does not change retry/failure behavior at all,
    it only adds a marker at the terminal-failure point.
    """

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
        retries = getattr(self.request, "retries", 0)
        max_retries = self.max_retries or 0
        if retries >= max_retries:
            collection_task_id = args[0] if args else kwargs.get("task_id")
            logger.error(
                "[task:%s] retries exhausted | celery_task_id=%s retries=%d/%d error=%s",
                collection_task_id,
                task_id,
                retries,
                max_retries,
                exc,
            )
            try:
                _run_async(
                    _mark_retries_exhausted(
                        collection_task_id,
                        retries,
                        max_retries,
                        str(exc),
                    )
                )
            except Exception:
                # Best-effort signal: a failure recording the signal must not
                # mask the original task failure or crash celery's own
                # failure-handling path.
                logger.exception(
                    "[task:%s] failed to record retries-exhausted signal", collection_task_id
                )
        super().on_failure(exc, task_id, args, kwargs, einfo)


async def _mark_retries_exhausted(
    collection_task_id: str | None, retries: int, max_retries: int, error: str
) -> None:
    """Record the retries-exhausted signal on the most recent TaskRun for this
    task, and emit an event so it surfaces in the run's trace/UI too."""
    if not collection_task_id:
        return

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.task import TaskRun
    from backend.pipeline import events

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TaskRun)
            .where(TaskRun.task_id == collection_task_id)
            .order_by(TaskRun.created_at.desc())
            .limit(1)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return
        run.error_detail = {
            **(run.error_detail or {}),
            "retries_exhausted": True,
            "retries": retries,
            "max_retries": max_retries,
        }
        run_id = run.id
        await session.commit()

    await events.emit(
        run_id,
        "complete",
        f"重试已耗尽 | {retries}/{max_retries} 次后仍失败: {error}",
        level="error",
        detail={"retries_exhausted": True, "retries": retries, "max_retries": max_retries},
    )


@celery_app.task(
    bind=True,
    base=_AlertOnRetriesExhaustedTask,
    name="run_collection",
    max_retries=3,
    # run_pipeline() only re-raises exceptions its error taxonomy classified
    # as retryable (backend.pipeline.error_taxonomy.is_retryable) — anything
    # deterministic is already swallowed into a returned PipelineResult
    # before it gets here. So catching broadly at this boundary is correct:
    # the filtering already happened one layer down, not duplicated here.
    autoretry_for=(Exception,),
    # AUDIT C14: a fixed 60s delay for every retry means every failed run on
    # a source retries in lockstep (same delay, same jitter-free instant) —
    # a source down for minutes hammers it 3x on a synchronous 60s/60s/60s
    # cadence instead of backing off. retry_backoff=True + retry_jitter=True
    # is celery's standard autoretry_for pairing: countdown = min(
    # retry_backoff_max, 1 * 2**retries), then full-jittered — 1st retry
    # ~0-1s, 2nd ~0-2s, 3rd ~0-4s, capped at 600s. default_retry_delay is
    # unused once retry_backoff is set (celery computes countdown from the
    # backoff formula instead), so it's dropped rather than left as
    # never-consulted dead config.
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_collection(self: Task, task_id: str, parameters: dict | None = None) -> dict:
    """Execute the full collection pipeline for a task."""
    from backend.pipeline.runner import run_collection_pipeline

    return _run_async(
        run_collection_pipeline(
            task_id,
            parameters or {},
            celery_task_id=self.request.id,
            worker_id=self.request.hostname,
        )
    )


@celery_app.task(
    bind=True,
    name="run_scheduled_collection",
    max_retries=3,
    # AUDIT C14: run_scheduled_pipeline() funnels into the same
    # run_collection_pipeline() -> run_pipeline() as run_collection above, so
    # the same contract applies — run_pipeline() only re-raises exceptions
    # already classified retryable (backend.pipeline.error_taxonomy.
    # is_retryable), everything else is swallowed into a returned dict before
    # it gets here. Without autoretry_for here, those retryable exceptions
    # just failed the celery task outright: a scheduled run had zero retries
    # where a manually-triggered one (run_collection) got 3 with backoff,
    # contradicting pipeline.py's own "let this propagate ... so its
    # autoretry_for policy applies" comment. Same backoff+jitter shape as
    # run_collection for the same reason (see there).
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_scheduled_collection(
    self: Task,
    schedule_id: str,
    source_id: str,
    parameters: dict | None = None,
) -> dict:
    """Execute one idempotently identified scheduled occurrence."""
    from backend.pipeline.runner import run_scheduled_pipeline

    return _run_async(
        run_scheduled_pipeline(
            schedule_id,
            source_id,
            parameters or {},
            occurrence_id=self.request.id,
            celery_task_id=self.request.id,
            worker_id=self.request.hostname,
        )
    )


@celery_app.task(
    name="dispatch_scheduled_operations_agent_run",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def dispatch_scheduled_operations_agent_run(run_id: str) -> dict:
    """Durably ask the API process that owns Fleet WS state to execute one run."""
    return post_control_plane(
        f"/api/v1/internal/operations-agent-runs/{run_id}/dispatch"
    )


@celery_app.task(name="run_automation_scheduler_tick")
def run_automation_scheduler_tick() -> dict:
    """Claim due Automations and enqueue every queued scheduled run for dispatch."""
    result = post_control_plane(
        "/api/v1/internal/automations/scheduler/tick",
        {"fired_at": datetime.now(UTC).isoformat()},
    )
    queued_run_ids = result.get("data", {}).get("queued_run_ids", [])
    for run_id in queued_run_ids:
        dispatch_scheduled_operations_agent_run.delay(run_id)
    result["dispatch_enqueued_run_ids"] = queued_run_ids
    return result


@celery_app.task(name="send_notification")
def send_notification(rule_id: str, record_id: str) -> dict:
    """Send a single notification for a rule/record pair."""
    return _run_async(_send_notification_async(rule_id, record_id))


async def _send_notification_async(rule_id: str, record_id: str) -> dict:
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.notification import NotificationRule
    from backend.models.record import CollectedRecord
    from backend.notifiers.base import NotificationPayload
    from backend.notifiers.registry import get_notifier
    from backend.pipeline.notifier_dispatch import _normalize_send_result

    async with AsyncSessionLocal() as session:
        rule_result = await session.execute(
            select(NotificationRule).where(NotificationRule.id == rule_id)
        )
        rule = rule_result.scalar_one_or_none()
        if not rule:
            return {"error": f"Rule {rule_id} not found"}

        record_result = await session.execute(
            select(CollectedRecord).where(CollectedRecord.id == record_id)
        )
        record = record_result.scalar_one_or_none()
        if not record:
            return {"error": f"Record {record_id} not found"}

        notifier = get_notifier(rule.notifier_type)
        payload = NotificationPayload(
            event=rule.trigger_event,
            source_id=record.source_id,
            record_id=record.id,
            lineage=record.lineage,
            data=record.normalized_data,
            ai_enrichment=record.ai_enrichment,
        )
        success, _ = _normalize_send_result(await notifier.send(rule.notifier_config, payload))
        return {"success": success, "rule_id": rule_id, "record_id": record_id}
