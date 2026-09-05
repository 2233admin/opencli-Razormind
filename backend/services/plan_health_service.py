"""Plan Health: record + read shared-segment node health (issue 04, ADR-0009
Two-Tier Attribution).

Writing is owned by ``backend.plan_ir.executor`` (one row per shared node per
run). This module is the read side plus the one write helper, kept together
so both the executor and the HTTP router go through the same narrow surface
— mirrors ``backend/services/control_ledger_service.py``'s "list" precedent,
plus a single ``record_node_health`` write helper alongside it (Plan Health
has no separate recorder module the way ``backend.control.recorder`` is
split out, because — unlike per-source measurements — nothing outside the
executor ever writes a Plan Health row).
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.plan_health import PlanHealthRecord


async def record_node_health(
    session: AsyncSession,
    *,
    plan_id: str,
    run_key: str,
    node_id: str,
    node_type: str,
    success: bool,
    duration_ms: int = 0,
    items_in: int = 0,
    items_out: int = 0,
    error_message: str | None = None,
    detail: dict | None = None,
) -> PlanHealthRecord:
    """Persist one shared-segment node's health for one run. Never raises on
    the caller's behalf — a failing node's own error is recorded as data
    here, not propagated as a second exception from the recorder itself."""
    row = PlanHealthRecord(
        plan_id=plan_id,
        run_key=run_key,
        node_id=node_id,
        node_type=node_type,
        success=success,
        duration_ms=duration_ms,
        items_in=items_in,
        items_out=items_out,
        error_message=error_message,
        detail=detail or {},
        recorded_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def list_plan_health(
    session: AsyncSession,
    plan_id: str,
    run_key: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[PlanHealthRecord], int]:
    """List Plan Health rows for ``plan_id``, newest first. ``run_key``
    narrows to a single run (PRD story 16 — "see Plan Health per shared
    node" for a given run); omitted, every recorded run's rows are returned
    so an operator can see the node's health trend across runs."""
    query = (
        select(PlanHealthRecord)
        .where(PlanHealthRecord.plan_id == plan_id)
        .order_by(PlanHealthRecord.recorded_at.desc())
    )
    count_query = (
        select(func.count())
        .select_from(PlanHealthRecord)
        .where(PlanHealthRecord.plan_id == plan_id)
    )

    if run_key:
        query = query.where(PlanHealthRecord.run_key == run_key)
        count_query = count_query.where(PlanHealthRecord.run_key == run_key)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total
