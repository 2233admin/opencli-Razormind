import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.source import DataSource
from backend.models.task import TaskRun, TaskRunEvent
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.schemas.task import (
    CollectionTaskRead,
    TaskRecoveryRead,
    TaskRecoveryRequest,
    TaskRunRead,
    TaskTriggerRequest,
)
from backend.services import source_service, task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _serialize_run_event(event: TaskRunEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "level": event.level,
        "step": event.step,
        "message": event.message,
        "detail": event.detail,
        "elapsed_ms": event.elapsed_ms,
        "created_at": event.created_at.isoformat(),
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("", response_model=ApiResponse[list[CollectionTaskRead]])
async def list_tasks(
    source_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    tasks, total = await task_service.list_tasks(
        db, source_id=source_id, status=status, page=page, limit=limit
    )
    source_ids = list({t.source_id for t in tasks})
    sources = (
        await db.execute(select(DataSource).where(DataSource.id.in_(source_ids)))
    ).scalars().all()
    name_map = {s.id: s.name for s in sources}
    data = []
    for task in tasks:
        item = CollectionTaskRead.model_validate(task)
        item.source_name = name_map.get(task.source_id)
        data.append(item)
    return ApiResponse.ok(
        data=data,
        meta=PaginationMeta(total=total, page=page, limit=limit, pages=max(1, -(-total // limit))),
    )


@router.post("/trigger", response_model=ApiResponse[dict], status_code=202)
async def trigger_task(
    body: TaskTriggerRequest, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, body.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.enabled:
        raise HTTPException(status_code=400, detail="Source is disabled")

    task = await task_service.create_task(
        db,
        source_id=body.source_id,
        trigger_type="manual",
        parameters=body.parameters,
        priority=body.priority,
        agent_id=body.agent_id,
    )
    # Commit before dispatching so the background runner's new session can find the task.
    await db.commit()

    from backend.executor import get_executor

    result = await get_executor().dispatch_collection(task.id, body.parameters)

    return ApiResponse.ok(result)


@router.get("/{task_id}", response_model=ApiResponse[CollectionTaskRead])
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    task = await task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ApiResponse.ok(CollectionTaskRead.model_validate(task))


@router.post("/{task_id}/recover", response_model=ApiResponse[TaskRecoveryRead], status_code=202)
async def recover_task(
    task_id: str,
    body: TaskRecoveryRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Start a new, notification-free recovery task for one failed task."""
    task = await task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    existing = await task_service.get_recovery_by_idempotency_key(db, body.idempotency_key)
    if existing:
        if existing.retry_of_task_id != task_id:
            raise HTTPException(status_code=409, detail="Idempotency key belongs to another task")
        return ApiResponse.ok(
            TaskRecoveryRead(
                task_id=existing.id,
                retry_of_task_id=task_id,
                status=existing.status,
                recovery_mode=existing.recovery_mode or body.mode,
                idempotency_replayed=True,
            )
        )

    if task.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed tasks can be recovered")

    source = await source_service.get_source(db, task.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.enabled:
        raise HTTPException(status_code=400, detail="Source is disabled")

    in_flight = await task_service.get_inflight_recovery(db, task_id)
    if in_flight:
        raise HTTPException(status_code=409, detail="A recovery is already in progress")

    try:
        recovery = await task_service.create_task(
            db,
            source_id=task.source_id,
            trigger_type="recovery",
            parameters=dict(task.parameters),
            priority=task.priority,
            agent_id=task.agent_id,
            retry_of_task_id=task.id,
            recovery_mode=body.mode,
            recovery_reason=body.reason,
            initiating_actor=body.initiating_actor,
            recovery_idempotency_key=body.idempotency_key,
        )
        await db.commit()
    except IntegrityError:
        # A concurrent request may win the unique idempotency constraint after
        # the read above. Re-read it and return the durable winner.
        await db.rollback()
        existing = await task_service.get_recovery_by_idempotency_key(db, body.idempotency_key)
        if not existing or existing.retry_of_task_id != task_id:
            raise HTTPException(status_code=409, detail="Recovery request conflicts")
        return ApiResponse.ok(
            TaskRecoveryRead(
                task_id=existing.id,
                retry_of_task_id=task_id,
                status=existing.status,
                recovery_mode=existing.recovery_mode or body.mode,
                idempotency_replayed=True,
            )
        )

    from backend.executor import get_executor

    try:
        await get_executor().dispatch_collection(recovery.id, dict(recovery.parameters))
    except Exception as exc:
        failed_recovery = await task_service.get_task(db, recovery.id)
        if failed_recovery:
            failed_recovery.status = "failed"
            failed_recovery.error_message = f"Recovery dispatch failed: {exc}"
            await db.commit()
        raise HTTPException(status_code=503, detail="Recovery could not be dispatched") from exc

    return ApiResponse.ok(
        TaskRecoveryRead(
            task_id=recovery.id,
            retry_of_task_id=task_id,
            status=recovery.status,
            recovery_mode=body.mode,
        )
    )


@router.get("/{task_id}/runs", response_model=ApiResponse[list[TaskRunRead]])
async def list_task_runs(
    task_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    task = await task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    runs, total = await task_service.list_task_runs(db, task_id, page=page, limit=limit)
    return ApiResponse.ok(
        data=[TaskRunRead.model_validate(r) for r in runs],
        meta=PaginationMeta(total=total, page=page, limit=limit, pages=max(1, -(-total // limit))),
    )


async def _get_run_for_task(db: AsyncSession, task_id: str, run_id: str) -> TaskRun:
    result = await db.execute(
        select(TaskRun).where(TaskRun.id == run_id, TaskRun.task_id == task_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{task_id}/runs/{run_id}/events", response_model=ApiResponse[list[dict]])
async def list_run_events(
    task_id: str,
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    await _get_run_for_task(db, task_id, run_id)
    result = await db.execute(
        select(TaskRunEvent)
        .where(TaskRunEvent.run_id == run_id)
        .order_by(TaskRunEvent.created_at, TaskRunEvent.id)
    )
    events_list = result.scalars().all()
    return ApiResponse.ok([_serialize_run_event(event) for event in events_list])


@router.get("/{task_id}/runs/{run_id}/events/stream")
async def stream_run_events(
    task_id: str,
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _get_run_for_task(db, task_id, run_id)

    async def event_generator():
        seen_ids: set[str] = set()
        heartbeat_count = 0

        while not await request.is_disconnected():
            result = await db.execute(
                select(TaskRunEvent)
                .where(TaskRunEvent.run_id == run_id)
                .order_by(TaskRunEvent.created_at, TaskRunEvent.id)
            )
            events = result.scalars().all()
            for event in events:
                if event.id in seen_ids:
                    continue
                seen_ids.add(event.id)
                yield _sse("run_event", _serialize_run_event(event))

            run = await _get_run_for_task(db, task_id, run_id)
            if run.status not in {"pending", "running", "ai_processing", "queued"}:
                yield _sse("run_status", {"run_id": run.id, "status": run.status})
                break

            heartbeat_count += 1
            if heartbeat_count % 10 == 0:
                yield _sse("heartbeat", {"run_id": run_id})
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
