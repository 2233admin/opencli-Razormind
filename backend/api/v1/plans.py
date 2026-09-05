"""Plans CRUD (issue 02): persistence for authored Plan IR graphs.

Save (create/update) validates the caller-supplied graph through the
issue-01 structural validator (``backend.plan_ir.validation``) before any
write — an invalid graph never reaches the ``plans`` table, returned as 422
with the validator's node-anchored error details. A Plan containing
unmaterialized Draft Source Nodes saves fine but is flagged ``draft``;
draft Plans never enter any control loop (no scheduling hooks live here —
that's issue 05's scope). Execution (issues 03/04) is out of scope: nothing
in this router runs a Plan.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.plan_ir.executor import PlanExecutionError, run_plan_once
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.schemas.plan import PlanCreate, PlanHealthRead, PlanRead, PlanRunRead, PlanUpdate
from backend.services import plan_health_service, plan_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=ApiResponse[list[PlanRead]])
async def list_plans(
    draft: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    plans, total = await plan_service.list_plans(db, draft=draft, page=page, limit=limit)
    return ApiResponse.ok(
        data=[PlanRead.model_validate(p) for p in plans],
        meta=PaginationMeta(
            total=total, page=page, limit=limit, pages=max(1, -(-total // limit))
        ),
    )


@router.post("", response_model=ApiResponse[PlanRead], status_code=201)
async def create_plan(body: PlanCreate, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    try:
        _parsed, result = plan_service.validate_graph_dict(body.graph)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if not result.valid:
        raise HTTPException(status_code=422, detail=result.to_dict()["errors"])

    draft, runnable = plan_service.derive_flags(_parsed)
    plan = await plan_service.create_plan(db, body, draft=draft, runnable=runnable)
    return ApiResponse.ok(PlanRead.model_validate(plan))


@router.get("/{plan_id}", response_model=ApiResponse[PlanRead])
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    plan = await plan_service.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return ApiResponse.ok(PlanRead.model_validate(plan))


@router.patch("/{plan_id}", response_model=ApiResponse[PlanRead])
async def update_plan(
    plan_id: str, body: PlanUpdate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    plan = await plan_service.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    draft: bool | None = None
    runnable: bool | None = None
    if body.graph is not None:
        try:
            _parsed, result = plan_service.validate_graph_dict(body.graph)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        if not result.valid:
            raise HTTPException(status_code=422, detail=result.to_dict()["errors"])
        draft, runnable = plan_service.derive_flags(_parsed)

    updated = await plan_service.update_plan(db, plan, body, draft=draft, runnable=runnable)
    return ApiResponse.ok(PlanRead.model_validate(updated))


@router.delete("/{plan_id}", response_model=ApiResponse[None])
async def delete_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    plan = await plan_service.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    await plan_service.delete_plan(db, plan)
    return ApiResponse.ok(None)


@router.post("/{plan_id}/run", response_model=ApiResponse[PlanRunRead], status_code=202)
async def run_plan(
    plan_id: str,
    parameters: dict = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Manual whole-plan run (issue 03, PRD story 13 — "run whole plan" for
    debugging). Invokes ``backend.plan_ir.executor.run_plan_once``
    synchronously so the response already reflects the completed run (or
    its failure) — no polling required. Degenerate (single-source) Plans
    (issue 03) and multi-source Plans with shared segments (issue 04) both
    go through this one endpoint; the response shape's
    ``source_results``/``shared_segment`` fields are empty/null for a
    degenerate Plan, populated for a multi-source one.

    404 if the Plan doesn't exist. Draft Plans and non-runnable Plans are
    refused with 400 and a clear message. An individual source segment
    failing inside a multi-source Plan does NOT 400 the whole request — that
    is the "partial failure" acceptance criterion (PRD story 15): the
    response is still 202 with ``success=False`` and the failing source's
    detail in ``source_results``, so a caller can see exactly which source
    (or shared node) failed without the request itself erroring.
    """
    plan = await plan_service.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    try:
        result = await run_plan_once(db, plan, parameters=parameters)
    except PlanExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ApiResponse.ok(PlanRunRead.model_validate(result))


@router.get("/{plan_id}/health", response_model=ApiResponse[list[PlanHealthRead]])
async def get_plan_health(
    plan_id: str,
    run_key: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Plan Health: per-shared-node success/failure/duration, readable over
    HTTP (issue 04, PRD story 16). Read-only — nothing here writes a Plan
    Health row (that is ``run_plan_once``'s job, on every shared-segment
    run). ``run_key`` narrows to a single run; omitted, every recorded run's
    rows for this Plan are returned, newest first.

    404 if the Plan doesn't exist, matching every other ``/plans/{plan_id}``
    sub-resource in this router.
    """
    plan = await plan_service.get_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    rows, total = await plan_health_service.list_plan_health(
        db, plan_id, run_key=run_key, page=page, limit=limit
    )
    return ApiResponse.ok(
        data=[PlanHealthRead.model_validate(r) for r in rows],
        meta=PaginationMeta(
            total=total, page=page, limit=limit, pages=max(1, -(-total // limit))
        ),
    )
