from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.plan import Plan
from backend.models.plan_source_index import PlanSourceIndex
from backend.plan_ir.validation import PlanValidationResult, validate_plan_graph
from backend.schemas.plan import PlanCreate, PlanUpdate
from backend.schemas.plan_ir import PlanGraph


def derive_flags(graph: PlanGraph) -> tuple[bool, bool]:
    """Derive (draft, runnable) from a validated ``PlanGraph`` (issue 02
    acceptance criterion / PRD story 9-10, ADR-0009 draft semantics).

    - ``draft``: True if any source node is an unmaterialized Draft Source
      Node (``draft=True``, no ``source_id``). A Plan may be saved with
      draft source nodes; it is just flagged.
    - ``runnable``: True only when there is at least one source node and
      every source node is materialized (``source_id`` set, ``draft=False``).
      A graph with zero source nodes is not runnable — there is nothing for
      the backend executor to run end-to-end.
    """
    source_nodes = [n for n in graph.nodes if n.kind == "source"]
    draft = any(n.draft for n in source_nodes)
    runnable = bool(source_nodes) and all(not n.draft for n in source_nodes)
    return draft, runnable


def validate_graph_dict(graph: dict[str, Any]) -> tuple[PlanGraph, PlanValidationResult]:
    """Parse ``graph`` into the issue-01 ``PlanGraph`` shape and run the
    issue-01 structural validator against it. Never raises on a
    structurally-valid-but-semantically-wrong graph (cycles, orphan merges,
    etc. — that's what the returned ``PlanValidationResult`` is for); a
    router translates a non-empty result into a 422.

    A graph that doesn't even parse into the IR shape (wrong types, missing
    required IR fields such as a node with no ``id``) raises
    ``pydantic.ValidationError`` — the caller catches it and maps it to the
    same 422 contract, mirroring
    ``backend.api.v1.sources.set_source_objective``'s
    ``except ValidationError`` precedent."""
    parsed = PlanGraph.model_validate(graph)
    result = validate_plan_graph(parsed)
    return parsed, result


async def list_plans(
    session: AsyncSession,
    draft: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Plan], int]:
    query = select(Plan).order_by(Plan.created_at.desc())
    count_query = select(func.count()).select_from(Plan)

    if draft is not None:
        query = query.where(Plan.draft == draft)
        count_query = count_query.where(Plan.draft == draft)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * limit
    result = await session.execute(query.offset(offset).limit(limit))
    return result.scalars().all(), total


async def get_plan(session: AsyncSession, plan_id: str) -> Plan | None:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()


async def _reindex_plan_sources(session: AsyncSession, plan: Plan, graph: PlanGraph) -> None:
    """Rebuild ``plan_source_index`` rows for ``plan`` from ``graph``'s
    materialized source nodes (issue 05, dataflow triggering). Delete +
    reinsert on every save — the table is small (one row per materialized
    source node per Plan) and this keeps the index trivially consistent with
    whatever graph shape was just validated and persisted, no diffing logic
    required. Draft source nodes (no ``source_id``) are never indexed: they
    cannot trigger anything, matching ``Plan.runnable`` semantics.
    """
    await session.execute(delete(PlanSourceIndex).where(PlanSourceIndex.plan_id == plan.id))
    for node in graph.nodes:
        if node.kind == "source" and node.source_id and not node.draft:
            session.add(
                PlanSourceIndex(
                    plan_id=plan.id, source_id=node.source_id, source_node_id=node.id
                )
            )
    await session.flush()


async def create_plan(session: AsyncSession, data: PlanCreate, draft: bool, runnable: bool) -> Plan:
    """Persist ``data.graph`` verbatim (byte-faithful round-trip) alongside
    the caller-derived draft/runnable flags. Callers must validate and
    derive flags first (``validate_graph_dict`` / ``derive_flags``) — this
    function performs no validation itself, matching the create_source
    precedent where the router/service split keeps validation ahead of the
    write."""
    plan = Plan(name=data.name, graph=data.graph, version=1, draft=draft, runnable=runnable)
    session.add(plan)
    await session.flush()
    await session.refresh(plan)
    await _reindex_plan_sources(session, plan, PlanGraph.model_validate(data.graph))
    return plan


async def update_plan(
    session: AsyncSession,
    plan: Plan,
    data: PlanUpdate,
    draft: bool | None = None,
    runnable: bool | None = None,
) -> Plan:
    """Apply only the fields the caller set. When ``graph`` is part of the
    update, ``version`` increments and the caller-supplied ``draft``/
    ``runnable`` (re-derived from the new graph) replace the stored flags;
    a name-only update leaves version/graph/flags untouched. The
    ``plan_source_index`` (issue 05) is rebuilt whenever the graph changes,
    so a source added/removed/rewired from the canvas is reflected in the
    trigger index immediately."""
    updates = data.model_dump(exclude_unset=True)
    if "name" in updates:
        plan.name = updates["name"]
    if "graph" in updates:
        plan.graph = updates["graph"]
        plan.version += 1
        plan.draft = bool(draft)
        plan.runnable = bool(runnable)
    await session.flush()
    await session.refresh(plan)
    if "graph" in updates:
        await _reindex_plan_sources(session, plan, PlanGraph.model_validate(plan.graph))
    return plan


async def delete_plan(session: AsyncSession, plan: Plan) -> None:
    await session.delete(plan)
    await session.flush()
