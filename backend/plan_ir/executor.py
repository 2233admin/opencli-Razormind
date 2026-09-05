"""Plan executor: the run body for Plans (issue 03 degenerate/single-source,
issue 04 shared segments + Two-Tier Attribution, ADR-0009, docs/plan-ir-PRD.md).

``run_plan_once`` is the test seam this issue introduces — a plain async
function taking a session and a ``Plan`` row, directly invocable in tests
without any scheduler/asyncio-wrapper timing. Precedent: the Control Cycle
body (``backend.control.cycle.run_control_cycle_once``) — same shape, same
"inject session + explicit inputs, return a result dataclass" contract.

Two execution shapes, dispatched on source-node count:

- **Degenerate (1 source node, issue 03)**: dispatches to the EXISTING
  channel/runner machinery — ``backend.services.task_service.create_task`` +
  ``backend.pipeline.runner.run_collection_pipeline`` — the same two calls
  ``POST /api/v1/tasks/trigger`` (``backend.api.v1.tasks.trigger_task``)
  makes today. Unchanged from issue 03: this module does not reimplement
  task creation, pipeline dispatch, TaskRun bookkeeping, or measurement/
  control-state recording.

- **Shared segments (2+ source nodes, issue 04)**: every source node still
  dispatches through the IDENTICAL per-source call pair above — producing
  normal per-source TaskRuns/measurements exactly as today (PRD story 14,
  zero-regression). A source segment's failure does not stop the others
  (PRD "partial failure" acceptance criterion). Once source segments finish,
  each successful source's freshly-stored ``CollectedRecord`` rows become
  source-tagged ``ProvenancedItem``s that flow through the Plan's shared
  segment — merge, then dedupe, then store (``backend.plan_ir.transforms``)
  — walked in topological order. Every shared node's outcome (success/
  failure/duration/item counts) is recorded as its own Plan Health row
  (``backend.services.plan_health_service``), NEVER as a
  ``SourceMeasurement`` or any ``DataSource`` column write — the Two-Tier
  Attribution contract (ADR-0009): a shared-segment failure marks Plan
  Health, never a source's measurement/control-state.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.plan import Plan
from backend.models.record import CollectedRecord
from backend.plan_ir.transforms import ProvenancedItem, dedupe_items, merge_items
from backend.schemas.plan_ir import PlanGraph, PlanNode
from backend.services import plan_health_service, source_service, task_service


class PlanExecutionError(Exception):
    """Refusal to execute a Plan (draft / non-runnable / missing source).
    Callers (the HTTP router) map this to a 4xx; the executor-seam tests
    assert on ``.args[0]`` directly."""


@dataclass
class PlanRunResult:
    """Everything one degenerate (single-source) ``run_plan_once`` pass did
    — mirrors ``backend.control.cycle.CycleResult``'s role as the seam's
    return value for both tests and the HTTP router's response body.

    Fields below ``error`` are issue-04 additions for the multi-source shape
    (``source_results``/``shared_segment``); a degenerate-plan run leaves
    them at their empty defaults, so every issue-03 assertion on this
    dataclass (``result.plan_id``, ``.source_id``, ``.task_id``, ...) is
    unaffected byte-for-byte.
    """

    plan_id: str
    source_id: str
    task_id: str
    run_id: str | None
    success: bool
    collected: int
    stored: int
    skipped: int
    error: str | None
    #: Multi-source (issue 04) only: one entry per source node, in graph
    #: order. Empty for a degenerate plan.
    source_results: list[SourceSegmentResult] = field(default_factory=list)
    #: Multi-source (issue 04) only: the shared segment's outcome. ``None``
    #: for a degenerate plan (there is no shared segment to run) or when a
    #: multi-source plan has no shared nodes at all.
    shared_segment: SharedSegmentResult | None = None


@dataclass
class SourceSegmentResult:
    """One source node's dispatch outcome within a multi-source Plan run.
    Shape mirrors ``PlanRunResult``'s per-source fields so a caller can
    treat each entry the same way it would a degenerate run's top-level
    result."""

    node_id: str
    source_id: str | None
    task_id: str | None
    run_id: str | None
    success: bool
    collected: int
    stored: int
    skipped: int
    error: str | None


@dataclass
class SharedSegmentResult:
    """The shared segment's overall outcome for one Plan run: which node (if
    any) failed, and the final store outcome. Node-level detail lives in the
    persisted Plan Health rows (``backend.services.plan_health_service``) —
    this is just the run-scoped summary returned to the caller."""

    run_key: str
    success: bool
    #: The node_id where execution stopped, if any node failed.
    failed_node_id: str | None
    error: str | None
    items_in: int
    stored: int
    skipped: int


def _source_nodes(graph: PlanGraph) -> list[PlanNode]:
    return [n for n in graph.nodes if n.kind == "source"]


def _nodes_by_id(graph: PlanGraph) -> dict[str, PlanNode]:
    return {n.id: n for n in graph.nodes}


def _downstream_of(graph: PlanGraph, node_id: str) -> list[str]:
    return [e.target_node for e in graph.edges if e.source_node == node_id]


def _upstream_of(graph: PlanGraph, node_id: str) -> list[str]:
    return [e.source_node for e in graph.edges if e.target_node == node_id]


async def run_plan_once(
    session: AsyncSession,
    plan: Plan,
    *,
    parameters: dict | None = None,
    priority: int = 5,
    agent_id: str | None = None,
) -> PlanRunResult:
    """Run ``plan`` once, synchronously, to completion.

    Refuses (raises ``PlanExecutionError``) when:
    - ``plan.draft`` is True (an unmaterialized Draft Source Node is present
      — nothing safe to execute yet, PRD story 9/10).
    - ``plan.runnable`` is False (e.g. zero source nodes at all).

    For a single source node (degenerate Plan, issue 03), also refuses when
    that one source node's ``source_id`` doesn't resolve to an existing,
    enabled ``DataSource`` — same 404/400 semantics ``trigger_task`` applies
    today. For 2+ source nodes (issue 04), an individual source failing that
    same check does NOT raise — it is recorded as that source's own failed
    ``SourceSegmentResult`` while the rest of the Plan proceeds (PRD "partial
    failure" acceptance criterion); ``run_plan_once`` only raises for
    Plan-level refusals (draft/not-runnable), never for a single source's
    trouble.
    """
    if plan.draft:
        raise PlanExecutionError(f"Plan {plan.id!r} is draft and cannot be run.")
    if not plan.runnable:
        raise PlanExecutionError(f"Plan {plan.id!r} is not runnable.")

    graph = PlanGraph.model_validate(plan.graph)
    source_nodes = _source_nodes(graph)

    if len(source_nodes) == 1:
        return await _run_degenerate(session, plan, source_nodes[0], parameters, priority, agent_id)

    _require_merge_shape(plan, graph, source_nodes)
    return await _run_shared_segments(
        session, plan, graph, source_nodes, parameters, priority, agent_id
    )


def _require_merge_shape(plan: Plan, graph: PlanGraph, source_nodes: list[PlanNode]) -> None:
    """A multi-source Plan's shared segment (issue 04) is defined as source
    nodes wired THROUGH a merge into sequential transforms and a sink
    (docs/plan-ir-issues/04, PRD "Solution"). Two or more source nodes wired
    directly to a sink/transform with no merge combining them is not that
    shape — it is a malformed graph the structural validator's orphan-merge
    check doesn't happen to catch (a sink is not a merge node), so this is
    the executor's own guard: refuse clearly rather than silently running
    one branch or fabricating a merge that was never authored.
    """
    merge_ids = {n.id for n in graph.nodes if n.kind == "merge"}
    if not merge_ids:
        raise PlanExecutionError(
            f"Plan {plan.id!r} has {len(source_nodes)} source node(s) but no "
            "merge node combining them; a multi-source Plan's shared segment "
            "must wire source nodes through a merge node."
        )

    # At least one merge node must be reachable from at least 2 distinct
    # source nodes — otherwise the "merge" is decorative and this is still
    # not a real shared segment.
    reachable_sources_by_merge: dict[str, set[str]] = {mid: set() for mid in merge_ids}
    for node in source_nodes:
        frontier = list(_downstream_of(graph, node.id))
        seen: set[str] = set()
        while frontier:
            nid = frontier.pop()
            if nid in seen:
                continue
            seen.add(nid)
            if nid in merge_ids:
                reachable_sources_by_merge[nid].add(node.id)
            frontier.extend(_downstream_of(graph, nid))

    if not any(len(srcs) >= 2 for srcs in reachable_sources_by_merge.values()):
        raise PlanExecutionError(
            f"Plan {plan.id!r} has {len(source_nodes)} source node(s) but no "
            "merge node combining them; a multi-source Plan's shared segment "
            "must wire source nodes through a merge node."
        )


# ── degenerate (issue 03, unchanged) ────────────────────────────────────────


async def _dispatch_source_segment(
    session: AsyncSession,
    node: PlanNode,
    *,
    parameters: dict,
    priority: int,
    agent_id: str | None,
) -> SourceSegmentResult:
    """Dispatch one source node through the existing channel/runner
    machinery. Never raises: any failure (missing source_id, dangling
    reference, disabled source, or a raised/returned pipeline error) is
    captured into the returned ``SourceSegmentResult.error`` so a caller can
    let one source's trouble not block the rest of a multi-source Plan."""
    if not node.source_id:
        return SourceSegmentResult(
            node_id=node.id, source_id=None, task_id=None, run_id=None,
            success=False, collected=0, stored=0, skipped=0,
            error=f"source node {node.id!r} has no source_id.",
        )

    source = await source_service.get_source(session, node.source_id)
    if source is None:
        return SourceSegmentResult(
            node_id=node.id, source_id=node.source_id, task_id=None, run_id=None,
            success=False, collected=0, stored=0, skipped=0,
            error=f"source node {node.id!r} references unknown source {node.source_id!r}.",
        )
    if not source.enabled:
        return SourceSegmentResult(
            node_id=node.id, source_id=source.id, task_id=None, run_id=None,
            success=False, collected=0, stored=0, skipped=0,
            error=f"source {node.source_id!r} is disabled.",
        )

    task = await task_service.create_task(
        session,
        source_id=source.id,
        trigger_type="plan",
        parameters=parameters,
        priority=priority,
        agent_id=agent_id,
    )
    # Commit before dispatching so run_collection_pipeline's own fresh
    # sessions (backend.database.AsyncSessionLocal) can see the new task row
    # — same ordering trigger_task uses (backend/api/v1/tasks.py).
    await session.commit()

    from backend.pipeline.runner import run_collection_pipeline

    try:
        outcome = await run_collection_pipeline(task.id, parameters)
    except Exception as exc:
        return SourceSegmentResult(
            node_id=node.id, source_id=source.id, task_id=task.id, run_id=None,
            success=False, collected=0, stored=0, skipped=0, error=str(exc),
        )

    if "error" in outcome and "success" not in outcome:
        return SourceSegmentResult(
            node_id=node.id, source_id=source.id, task_id=task.id, run_id=None,
            success=False, collected=0, stored=0, skipped=0,
            error=str(outcome["error"]),
        )

    return SourceSegmentResult(
        node_id=node.id,
        source_id=source.id,
        task_id=task.id,
        run_id=outcome.get("run_id"),
        success=bool(outcome.get("success")),
        collected=outcome.get("collected", 0),
        stored=outcome.get("stored", 0),
        skipped=outcome.get("skipped", 0),
        error=outcome.get("error"),
    )


async def _run_degenerate(
    session: AsyncSession,
    plan: Plan,
    node: PlanNode,
    parameters: dict | None,
    priority: int,
    agent_id: str | None,
) -> PlanRunResult:
    """issue 03's exact behavior: a single source-node failure RAISES
    (rather than being captured into a partial result) — a degenerate Plan
    has no "other sources" to keep running, so its one failure is the
    Plan's failure."""
    if not node.source_id:
        raise PlanExecutionError(f"Plan {plan.id!r} source node {node.id!r} has no source_id.")

    source = await source_service.get_source(session, node.source_id)
    if source is None:
        raise PlanExecutionError(
            f"Plan {plan.id!r} source node {node.id!r} references unknown "
            f"source {node.source_id!r}."
        )
    if not source.enabled:
        raise PlanExecutionError(f"Plan {plan.id!r} source {node.source_id!r} is disabled.")

    task = await task_service.create_task(
        session,
        source_id=source.id,
        trigger_type="plan",
        parameters=parameters or {},
        priority=priority,
        agent_id=agent_id,
    )
    await session.commit()

    from backend.pipeline.runner import run_collection_pipeline

    outcome = await run_collection_pipeline(task.id, parameters or {})

    if "error" in outcome and "success" not in outcome:
        raise PlanExecutionError(
            f"Plan {plan.id!r} run failed before pipeline execution: {outcome['error']}"
        )

    return PlanRunResult(
        plan_id=plan.id,
        source_id=source.id,
        task_id=task.id,
        run_id=outcome.get("run_id"),
        success=bool(outcome.get("success")),
        collected=outcome.get("collected", 0),
        stored=outcome.get("stored", 0),
        skipped=outcome.get("skipped", 0),
        error=outcome.get("error"),
    )


# ── shared segments (issue 04) ──────────────────────────────────────────────


async def _load_provenanced_items(
    session: AsyncSession, task_id: str, source_id: str, source_node_id: str
) -> list[ProvenancedItem]:
    """Rebuild the ``ProvenancedItem`` list for one source segment's freshly-
    stored records (this run's TaskRun only). Reads the SAME rows the
    per-source pipeline just wrote via ``backend.pipeline.storer.
    store_records`` — the shared segment never re-collects or re-normalizes,
    it only tags and forwards what the source segment already durably
    stored."""
    result = await session.execute(
        select(CollectedRecord).where(CollectedRecord.task_id == task_id)
    )
    records = result.scalars().all()
    return [
        ProvenancedItem(
            raw=r.raw_data,
            normalized=r.normalized_data,
            content_hash=r.content_hash,
            source_id=source_id,
            source_node_id=source_node_id,
        )
        for r in records
    ]


def _shared_segment_topo_order(graph: PlanGraph, start_node_ids: set[str]) -> list[str]:
    """Kahn's-algorithm topological order over the shared-segment subgraph
    reachable from ``start_node_ids`` (the nodes directly downstream of
    source nodes) — merge/transform/sink nodes only. The full-graph
    validator (``backend.plan_ir.validation``) already rejects cycles at
    save time, so this never needs cycle handling of its own; it only needs
    a deterministic run order for a DAG that's already known-acyclic."""
    nodes_by_id = _nodes_by_id(graph)
    reachable: set[str] = set()
    frontier = list(start_node_ids)
    while frontier:
        nid = frontier.pop()
        if nid in reachable or nid not in nodes_by_id:
            continue
        reachable.add(nid)
        frontier.extend(_downstream_of(graph, nid))

    shared_kinds = ("merge", "transform", "sink")
    shared_ids = {nid for nid in reachable if nodes_by_id[nid].kind in shared_kinds}

    in_degree = {
        nid: len([u for u in _upstream_of(graph, nid) if u in shared_ids or u in start_node_ids])
        for nid in shared_ids
    }
    ready = [nid for nid in shared_ids if in_degree[nid] == 0]
    ready.sort()  # deterministic order for nodes with no shared-segment predecessor
    ordered: list[str] = []
    while ready:
        nid = ready.pop(0)
        ordered.append(nid)
        for nxt in sorted(_downstream_of(graph, nid)):
            if nxt not in in_degree:
                continue
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)
    return ordered


async def _run_shared_segments(
    session: AsyncSession,
    plan: Plan,
    graph: PlanGraph,
    source_nodes: list[PlanNode],
    parameters: dict | None,
    priority: int,
    agent_id: str | None,
) -> PlanRunResult:
    params = parameters or {}
    run_key = str(uuid.uuid4())

    # ── source segments: identical per-source dispatch as the degenerate
    #    path, but a failure here never stops the others (PRD partial-
    #    failure acceptance criterion). ──────────────────────────────────
    source_results: list[SourceSegmentResult] = []
    for node in source_nodes:
        result = await _dispatch_source_segment(
            session, node, parameters=params, priority=priority, agent_id=agent_id
        )
        source_results.append(result)

    # ── shared segment: gather source-tagged items from every source that
    #    actually stored something, then walk merge -> dedupe -> store in
    #    topological order. NEVER writes into source_measurements/DataSource
    #    control-state — only Plan Health + collected_records (the store
    #    sink's own destination), the Two-Tier Attribution contract. ──────
    branches: list[list[ProvenancedItem]] = []
    for node, result in zip(source_nodes, source_results):
        if not result.task_id:
            continue
        items = await _load_provenanced_items(
            session, result.task_id, result.source_id or "", node.id
        )
        branches.append(items)

    start_node_ids = {nid for node in source_nodes for nid in _downstream_of(graph, node.id)}
    shared_segment = await _run_shared_segment_over_branches(
        session, plan, graph, run_key, start_node_ids, branches, params
    )

    overall_success = all(r.success for r in source_results) and (
        shared_segment is None or shared_segment.success
    )
    total_collected = sum(r.collected for r in source_results)
    total_stored = (
        shared_segment.stored if shared_segment else sum(r.stored for r in source_results)
    )
    total_skipped = (
        shared_segment.skipped if shared_segment else sum(r.skipped for r in source_results)
    )
    error = shared_segment.error if (shared_segment and not shared_segment.success) else next(
        (r.error for r in source_results if not r.success and r.error), None
    )

    return PlanRunResult(
        plan_id=plan.id,
        source_id=",".join(r.source_id for r in source_results if r.source_id),
        task_id=",".join(r.task_id for r in source_results if r.task_id),
        run_id=None,
        success=overall_success,
        collected=total_collected,
        stored=total_stored,
        skipped=total_skipped,
        error=error,
        source_results=source_results,
        shared_segment=shared_segment,
    )


async def _run_shared_segment_over_branches(
    session: AsyncSession,
    plan: Plan,
    graph: PlanGraph,
    run_key: str,
    start_node_ids: set[str],
    branches: list[list[ProvenancedItem]],
    params: dict,
) -> SharedSegmentResult | None:
    """Shared plumbing for both the manual whole-plan run (``_run_shared_
    segments``, issue 04, one branch per source node run this pass) and the
    incremental dataflow-triggered run (``run_plan_shared_segment_
    incremental``, issue 05, exactly one branch: the triggering delivery's
    items). Computes the shared-segment topological order downstream of
    ``start_node_ids`` and walks it via ``_run_shared_pipeline`` — the ONE
    node-execution engine both callers share, never duplicated. Returns
    ``None`` when the Plan has no shared nodes downstream of the given
    starting point (nothing to run)."""
    order = _shared_segment_topo_order(graph, start_node_ids)
    if not order:
        return None
    nodes_by_id = _nodes_by_id(graph)
    return await _run_shared_pipeline(session, plan, run_key, order, nodes_by_id, branches, params)


async def _run_shared_pipeline(
    session: AsyncSession,
    plan: Plan,
    run_key: str,
    order: list[str],
    nodes_by_id: dict[str, PlanNode],
    branches: list[list[ProvenancedItem]],
    params: dict,
) -> SharedSegmentResult:
    """Execute the shared segment's node chain in topological order,
    recording one Plan Health row per node. Stops at the first node that
    raises, recording that node's failure — later nodes in the chain never
    run, and nothing written so far by an EARLIER shared node (e.g. dedupe's
    survivors) touches any source's own state, because shared nodes never
    write source state at all."""
    current: list[ProvenancedItem] = []
    total_in = sum(len(b) for b in branches)
    stored_count = 0
    skipped_count = 0

    for node_id in order:
        node = nodes_by_id[node_id]
        started = time.monotonic()
        try:
            if node.kind == "merge":
                items_in = sum(len(b) for b in branches)
                current = merge_items(branches)
                items_out = len(current)
                duration_ms = int((time.monotonic() - started) * 1000)
                await plan_health_service.record_node_health(
                    session, plan_id=plan.id, run_key=run_key, node_id=node.id,
                    node_type=node.type, success=True, duration_ms=duration_ms,
                    items_in=items_in, items_out=items_out,
                )
                await session.commit()

            elif node.type == "dedupe":
                items_in = len(current)
                current, dropped = dedupe_items(current)
                items_out = len(current)
                duration_ms = int((time.monotonic() - started) * 1000)
                await plan_health_service.record_node_health(
                    session, plan_id=plan.id, run_key=run_key, node_id=node.id,
                    node_type=node.type, success=True, duration_ms=duration_ms,
                    items_in=items_in, items_out=items_out, detail={"dropped": dropped},
                )
                await session.commit()

            elif node.kind == "sink":
                items_in = len(current)
                stored_count, skipped_count = await _store_shared_items(
                    session, plan, current, params
                )
                items_out = stored_count
                duration_ms = int((time.monotonic() - started) * 1000)
                await plan_health_service.record_node_health(
                    session, plan_id=plan.id, run_key=run_key, node_id=node.id,
                    node_type=node.type, success=True, duration_ms=duration_ms,
                    items_in=items_in, items_out=items_out,
                    detail={"skipped": skipped_count},
                )
                await session.commit()

            else:
                # Unknown transform kind: issue 04 ships merge/dedupe/store
                # only; any other node type in a shared segment is refused
                # explicitly rather than silently passed through unchanged.
                raise PlanExecutionError(
                    f"Plan {plan.id!r} shared node {node.id!r} has unsupported "
                    f"type {node.type!r}; only merge/dedupe/store are implemented."
                )

        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            await session.rollback()
            await plan_health_service.record_node_health(
                session, plan_id=plan.id, run_key=run_key, node_id=node.id,
                node_type=node.type, success=False, duration_ms=duration_ms,
                items_in=len(current), items_out=0, error_message=str(exc),
            )
            await session.commit()
            return SharedSegmentResult(
                run_key=run_key, success=False, failed_node_id=node.id,
                error=str(exc), items_in=total_in, stored=0, skipped=0,
            )

    return SharedSegmentResult(
        run_key=run_key, success=True, failed_node_id=None, error=None,
        items_in=total_in, stored=stored_count, skipped=skipped_count,
    )


async def _store_shared_items(
    session: AsyncSession,
    plan: Plan,
    items: list[ProvenancedItem],
    params: dict,
) -> tuple[int, int]:
    """The shared segment's store sink: reuses the EXISTING
    ``backend.pipeline.storer.store_records`` write path — the same
    function every per-source ``LegacyDbSink`` write goes through — so a
    Plan's merged output lands in ``collected_records`` through the one
    write seam the codebase already has, not a parallel table.

    Anchored to a synthetic ``CollectionTask`` (``trigger_type="plan_shared"``)
    because ``CollectedRecord.task_id`` is a NOT NULL foreign key — every
    stored record must belong to SOME task. The anchor's ``source_id`` is
    the first item's originating source purely to satisfy
    ``CollectionTask.source_id``'s FK to ``data_sources``; the true
    per-item provenance (which source/node actually produced it) is carried
    in ``normalized_data['_plan_provenance']`` on every record, independent
    of which source the anchor task happens to point at. This anchor task
    is never read back as if it were that source's own segment — it only
    exists so the shared store has a row to attach to.
    """
    from backend.pipeline import storer

    if not items:
        return 0, 0

    anchor_source_id = items[0].source_id
    anchor_task = await task_service.create_task(
        session,
        source_id=anchor_source_id,
        trigger_type="plan_shared",
        parameters=params,
        priority=5,
    )
    await session.flush()

    triples: list[tuple[dict, dict, str]] = []
    for item in items:
        normalized = {
            **item.normalized,
            "_plan_provenance": {
                "plan_id": plan.id,
                "source_id": item.source_id,
                "source_node_id": item.source_node_id,
            },
        }
        triples.append((item.raw, normalized, item.content_hash))

    new_records, skipped = await storer.store_records(
        session, anchor_task.id, plan.id, triples, channel_type="plan_shared"
    )
    await session.flush()
    return len(new_records), skipped


# ── dataflow triggering (issue 05) ──────────────────────────────────────────


@dataclass
class IncrementalTriggerResult:
    """One incremental shared-segment run's outcome — triggered by a single
    source's delivery, not a manual whole-plan run (issue 05,
    ``PlanRunResult``'s counterpart for the dataflow-triggered path). Never
    raised to the caller: ``backend.services.plan_trigger_service`` catches
    everything and turns a failure into ``success=False`` here, because a
    shared-segment failure on this path must never propagate back into
    ``run_collection_pipeline`` and fail the triggering source's own run
    (failure isolation, PRD "a broken dedupe node never marks my healthy
    sources DEGRADED")."""

    plan_id: str
    source_id: str
    source_node_id: str
    run_key: str
    success: bool
    error: str | None
    shared_segment: SharedSegmentResult | None


async def run_plan_shared_segment_incremental(
    session: AsyncSession,
    plan: Plan,
    *,
    source_id: str,
    source_node_id: str,
    task_id: str,
    parameters: dict | None = None,
) -> IncrementalTriggerResult:
    """Run ``plan``'s downstream shared segment incrementally over JUST the
    records ``task_id`` (one source's one delivery) just stored — the
    dataflow-triggering entrypoint (issue 05, PRD story 12: "any upstream
    delivery runs the downstream shared segment incrementally with
    source-tagged provenance"). Reuses the IDENTICAL node-execution engine
    (``_run_shared_segment_over_branches`` / ``_run_shared_pipeline``) the
    manual whole-plan run uses — the only difference is ``branches`` has
    exactly one entry (this delivery's items) and the topological walk
    starts from this one source node's downstream neighbors, not every
    source node's.

    Two sources on different cadences in one Plan each call this
    independently, one delivery at a time — there is no whole-plan lockstep
    here at all (this function never looks at any OTHER source node in the
    graph).

    Never raises: any failure (bad graph shape, node not found, a shared
    node raising) is caught and returned as ``success=False`` so a caller
    (the pipeline runner, right after a source's own run already completed
    successfully) can never have this trigger fail the source's own
    TaskRun/measurements — Plan Health already recorded the shared node's
    own failure via the same ``_run_shared_pipeline`` path manual runs use.
    """
    run_key = str(uuid.uuid4())
    try:
        if plan.draft or not plan.runnable:
            # A Plan can go draft/non-runnable between the index write and
            # this trigger firing (e.g. an in-flight edit); skip silently —
            # this is not a shared-segment failure, there is nothing to run.
            return IncrementalTriggerResult(
                plan_id=plan.id, source_id=source_id, source_node_id=source_node_id,
                run_key=run_key, success=True, error=None, shared_segment=None,
            )

        graph = PlanGraph.model_validate(plan.graph)
        items = await _load_provenanced_items(session, task_id, source_id, source_node_id)
        # start_node_ids must be the nodes DOWNSTREAM of the source node
        # (mirrors _run_shared_segments' start_node_ids computation) — the
        # source node itself is never part of the shared segment's own
        # topo-ordered walk.
        start_node_ids = set(_downstream_of(graph, source_node_id))
        shared_segment = await _run_shared_segment_over_branches(
            session, plan, graph, run_key, start_node_ids, [items], parameters or {}
        )
        success = shared_segment is None or shared_segment.success
        error = shared_segment.error if shared_segment and not shared_segment.success else None
        return IncrementalTriggerResult(
            plan_id=plan.id, source_id=source_id, source_node_id=source_node_id,
            run_key=run_key, success=success, error=error, shared_segment=shared_segment,
        )
    except Exception as exc:  # noqa: BLE001 - failure isolation boundary, see docstring
        return IncrementalTriggerResult(
            plan_id=plan.id, source_id=source_id, source_node_id=source_node_id,
            run_key=run_key, success=False, error=str(exc), shared_segment=None,
        )
