"""Dataflow triggering (issue 05, ADR-0009, docs/plan-ir-issues/05): the
glue between "a source's own collection just delivered new data" and "every
runnable Plan containing that source runs its downstream shared segment
incrementally."

Schedules stay attached to sources (zero migration, PRD Implementation
Decisions) — this module adds no cron of its own. It is called from exactly
one place, ``backend.pipeline.runner.run_collection_pipeline``'s Phase 4
(after a run's outcome is already durably finalized), so both scheduled and
manually-triggered collection go through the identical trigger path (both
``LocalExecutor`` and the Celery ``run_collection``/``run_scheduled_
collection`` tasks call ``run_collection_pipeline`` — see that module's
docstring precedent).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.plan import Plan
from backend.models.plan_source_index import PlanSourceIndex
from backend.plan_ir.executor import IncrementalTriggerResult, run_plan_shared_segment_incremental

logger = logging.getLogger(__name__)


async def _runnable_plan_memberships(
    session: AsyncSession, source_id: str
) -> list[PlanSourceIndex]:
    """The efficient plan-membership check (issue 05 acceptance criterion:
    "Sources not part of any runnable Plan ... zero extra queries in the hot
    path beyond an efficient plan-membership check"). One indexed query on
    ``plan_source_index.source_id`` (see that model's docstring) joined
    against ``plans`` to filter to runnable, non-draft Plans only — a source
    wired into a draft or otherwise non-runnable Plan triggers nothing.
    Returns an empty list (zero further work) for a source in no Plan at
    all, which is the overwhelmingly common case today.
    """
    result = await session.execute(
        select(PlanSourceIndex)
        .join(Plan, Plan.id == PlanSourceIndex.plan_id)
        .where(
            PlanSourceIndex.source_id == source_id,
            Plan.draft.is_(False),
            Plan.runnable.is_(True),
        )
    )
    return result.scalars().all()


async def trigger_incremental_shared_segments(
    session: AsyncSession,
    *,
    source_id: str,
    task_id: str,
    parameters: dict | None = None,
) -> list[IncrementalTriggerResult]:
    """Entry point called after a source's own collection run has already
    completed and durably stored its outcome (``run_collection_pipeline``
    Phase 4). For every runnable Plan that has ``source_id`` wired in as a
    materialized source node, runs that Plan's downstream shared segment
    incrementally over just ``task_id``'s freshly-stored records.

    Never raises: each Plan's incremental run is isolated from every other
    Plan's (one Plan's shared-segment blowing up must not stop a second
    Plan sharing the same source from also running), and from the caller
    entirely (failure isolation, Two-Tier Attribution) — ``run_plan_shared_
    segment_incremental`` itself never raises (see its docstring); this
    loop additionally guards against a Plan having disappeared between the
    index read and this call (deleted mid-flight) or any other unexpected
    error, logging and continuing rather than ever propagating.
    """
    memberships = await _runnable_plan_memberships(session, source_id)
    if not memberships:
        return []

    results: list[IncrementalTriggerResult] = []
    for membership in memberships:
        try:
            plan = await session.get(Plan, membership.plan_id)
            if plan is None:
                continue
            result = await run_plan_shared_segment_incremental(
                session,
                plan,
                source_id=source_id,
                source_node_id=membership.source_node_id,
                task_id=task_id,
                parameters=parameters,
            )
            results.append(result)
            if not result.success:
                logger.warning(
                    "[plan:%s] incremental shared-segment trigger failed | "
                    "source_id=%s source_node_id=%s error=%s",
                    membership.plan_id, source_id, membership.source_node_id, result.error,
                )
        except Exception:
            # Belt-and-braces: run_plan_shared_segment_incremental already
            # catches everything into IncrementalTriggerResult.success=False,
            # but this loop must survive even a failure in session.get() or
            # membership bookkeeping itself — one Plan's trouble must never
            # stop the next Plan's trigger, nor reach the caller.
            logger.exception(
                "[plan:%s] dataflow trigger raised unexpectedly | source_id=%s",
                membership.plan_id, source_id,
            )
    return results
