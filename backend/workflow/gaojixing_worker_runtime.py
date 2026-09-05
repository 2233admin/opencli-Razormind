"""Local/Celery composition for durable Gaojixing collection work."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select, update

from backend.models.gaojixing_collection import (
    GaojixingCollectionRun,
    GaojixingCollectionRunStatus,
)

logger = logging.getLogger(__name__)
_local_tasks: dict[str, asyncio.Task[Any]] = {}


def dispatch_collection_job(job_id: str) -> None:
    """Dispatch on the configured durable mode; Hermes claims queued jobs itself."""

    from backend.config import get_settings

    if get_settings().task_executor == "celery":
        from backend.worker.tasks import run_gaojixing_collection

        run_gaojixing_collection.apply_async(
            kwargs={"job_id": job_id},
            task_id=f"gaojixing-collection:{job_id}",
        )
        return
    if get_settings().task_executor == "hermes":
        logger.info(
            "Gaojixing collection queued for Hermes",
            extra={"jobId": job_id},
        )
        return
    current = _local_tasks.get(job_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(_run_local_until_stable(job_id))
    _local_tasks[job_id] = task

    def completed(done: asyncio.Task[Any]) -> None:
        if _local_tasks.get(job_id) is done:
            _local_tasks.pop(job_id, None)
        if not done.cancelled() and done.exception() is not None:
            logger.exception(
                "Local Gaojixing collection task failed",
                exc_info=done.exception(),
                extra={"jobId": job_id},
            )

    task.add_done_callback(completed)


async def _run_local_until_stable(job_id: str) -> str:
    while True:
        outcome = await execute_collection_job(job_id)
        if outcome not in {"busy", "resume_pending"}:
            return outcome
        await asyncio.sleep(1)


async def execute_collection_job(job_id: str, *, driver_factory: Any = None) -> str:
    """Compose DB, production driver and same-run HDA resume."""

    from backend.database import AsyncSessionLocal
    from backend.workflow.gaojixing_collection_runner import run_collection_job
    from backend.config import get_settings
    from backend.workflow.plugin_registry import build_workflow_plugin_registry

    plugins = build_workflow_plugin_registry(get_settings())

    if driver_factory is None:
        from backend.workflow.gaojixing_doubao_driver import (
            build_opencli_doubao_evidence_driver,
        )

        def driver_factory(attempt_root: Path):
            return build_opencli_doubao_evidence_driver(project_root=attempt_root)

    async def resume(run_id: str) -> None:
        from backend.workflow.opencli_hda_tracer import resume_gaojixing_workflow_run

        await resume_gaojixing_workflow_run(run_id, plugins=plugins)

    outcome = await run_collection_job(
        job_id,
        session_factory=AsyncSessionLocal,
        driver_factory=driver_factory,
        schedule_resume=resume,
    )
    if outcome in {
        "waiting_verification",
        "waiting_reconciliation",
        "blocked",
        "failed",
    }:
        from backend.workflow.opencli_hda_tracer import refresh_gaojixing_workflow_run

        async with AsyncSessionLocal() as session:
            job = await session.get(GaojixingCollectionRun, job_id)
            run_id = job.workflow_run_id if job is not None else None
        if run_id is not None:
            await refresh_gaojixing_workflow_run(run_id, plugins=plugins)
    return outcome


async def recover_collection_jobs() -> list[str]:
    """Requeue durable work after API restart or an expired worker lease."""

    from datetime import UTC, datetime

    from backend.database import AsyncSessionLocal

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(GaojixingCollectionRun)
                    .where(
                        or_(
                            GaojixingCollectionRun.status.in_(
                                [
                                    GaojixingCollectionRunStatus.QUEUED.value,
                                    GaojixingCollectionRunStatus.REVIEWING.value,
                                ]
                            ),
                            and_(
                                GaojixingCollectionRun.status
                                == GaojixingCollectionRunStatus.RUNNING.value,
                                or_(
                                    GaojixingCollectionRun.lease_expires_at.is_(None),
                                    GaojixingCollectionRun.lease_expires_at <= now,
                                ),
                            ),
                        )
                    )
                    .order_by(GaojixingCollectionRun.created_at)
                )
            )
            .scalars()
            .all()
        )
        ids: list[str] = []
        for row in rows:
            if row.status == GaojixingCollectionRunStatus.RUNNING.value:
                recovered = await session.execute(
                    update(GaojixingCollectionRun)
                    .where(
                        GaojixingCollectionRun.id == row.id,
                        GaojixingCollectionRun.status
                        == GaojixingCollectionRunStatus.RUNNING.value,
                        or_(
                            GaojixingCollectionRun.lease_expires_at.is_(None),
                            GaojixingCollectionRun.lease_expires_at <= now,
                        ),
                    )
                    .values(
                        status=GaojixingCollectionRunStatus.QUEUED.value,
                        lease_owner=None,
                        lease_fencing_token=None,
                        heartbeat_at=None,
                        lease_expires_at=None,
                    )
                )
                if recovered.rowcount != 1:
                    continue
            ids.append(row.id)
        await session.commit()
    for job_id in ids:
        dispatch_collection_job(job_id)
    return ids


__all__ = [
    "dispatch_collection_job",
    "execute_collection_job",
    "recover_collection_jobs",
]
