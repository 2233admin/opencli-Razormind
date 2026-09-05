from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.schemas.record import CollectedRecordRead
from backend.services import record_service

router = APIRouter(prefix="/records", tags=["records"])


class BatchDeleteRequest(BaseModel):
    ids: list[str]


RecordSortField = Literal[
    "created_at",
    "updated_at",
    "status",
    "source_id",
    "workflow_id",
    "workflow_run_id",
]
RecordSortOrder = Literal["asc", "desc"]


@router.get("", response_model=ApiResponse[list[CollectedRecordRead]])
async def list_records(
    source_id: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    workflow_id: str | None = None,
    workflow_run_id: str | None = Query(default=None, alias="workflowRunId"),
    workflow_run_id_snake: str | None = Query(
        default=None,
        alias="workflow_run_id",
        include_in_schema=False,
    ),
    run_id: str | None = Query(default=None, alias="run_id"),
    status: str | None = None,
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: RecordSortField = Query("created_at"),
    sort_order: RecordSortOrder = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    records, total = await record_service.list_records(
        db,
        source_id=source_id,
        task_id=task_id,
        project_id=project_id,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id or workflow_run_id_snake,
        run_id=run_id,
        status=status,
        search=search,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ApiResponse.ok(
        data=[CollectedRecordRead.model_validate(r) for r in records],
        meta=PaginationMeta(total=total, page=page, limit=limit, pages=max(1, -(-total // limit))),
    )


@router.get("/{record_id}", response_model=ApiResponse[CollectedRecordRead])
async def get_record(record_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    record = await record_service.get_record(db, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return ApiResponse.ok(CollectedRecordRead.model_validate(record))


@router.delete("/{record_id}", response_model=ApiResponse[None])
async def delete_record(record_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    deleted = await record_service.delete_records(db, [record_id])
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return ApiResponse.ok(None)


@router.post("/batch-delete", response_model=ApiResponse[dict])
async def batch_delete_records(
    body: BatchDeleteRequest, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    if not body.ids:
        return ApiResponse.ok({"deleted": 0})
    deleted = await record_service.delete_records(db, body.ids)
    return ApiResponse.ok({"deleted": deleted})


@router.delete("", response_model=ApiResponse[dict])
async def clear_all_records(
    source_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    deleted = await record_service.delete_all_records(db, source_id=source_id)
    return ApiResponse.ok({"deleted": deleted})
