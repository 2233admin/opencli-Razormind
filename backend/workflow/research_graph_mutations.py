"""Guarded ResearchGraph mutations persisted through the shared event allocator."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.workflow_run import WorkflowRunEvent
from backend.schemas.research_graph import (
    WorkflowResearchGraphEvent,
    WorkflowResearchGraphMutationRequest,
    WorkflowResearchGraphProjection,
)
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent
from backend.workflow.opencli_hda_tracer import (
    get_workflow_run_projection,
    list_workflow_run_events,
)
from backend.workflow.research_graph import (
    ResearchGraphValidationError,
    build_research_graph_mutation_event,
    build_research_graph_projection,
)
from backend.workflow.workflow_plugins import WorkflowPluginRegistry
from backend.workflow.workflow_run_events import (
    WorkflowRunEventAppendError,
    WorkflowRunEventAppendResult,
    WorkflowRunEventConflictError,
    _lock_run_event_allocator,
    append_workflow_run_events,
)


class ResearchGraphReadError(ValueError):
    """A persisted run cannot be folded into a ResearchGraph projection."""


class ResearchGraphMutationError(ValueError):
    """A guarded ResearchGraph mutation conflicts with the persisted run."""


async def read_workflow_research_graph(
    session: AsyncSession,
    *,
    run_id: str,
    up_to_sequence: int | None = None,
    revision_id: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
) -> WorkflowResearchGraphProjection | None:
    """Load and fold one run's authoritative transcript."""

    projection = await get_workflow_run_projection(run_id, session=session)
    events = await list_workflow_run_events(run_id, session=session)
    if projection is None or events is None:
        return None
    try:
        return build_research_graph_projection(
            events,
            run_id=projection.runId,
            trace_id=projection.traceId,
            up_to_sequence=up_to_sequence,
            revision_id=revision_id,
            entity_id=entity_id,
            limit=limit,
        )
    except ResearchGraphValidationError as exc:
        raise ResearchGraphReadError(str(exc)) from exc


async def append_workflow_research_graph_mutation(
    session: AsyncSession,
    *,
    run_id: str,
    workflow_id: str,
    trace_id: str,
    request: WorkflowResearchGraphMutationRequest,
    plugins: WorkflowPluginRegistry | None = None,
) -> WorkflowRunEventAppendResult:
    """Append one guarded graph mutation through the shared event allocator."""

    await _lock_run_event_allocator(session, run_id)
    existing = await session.scalar(
        select(WorkflowRunEvent).where(WorkflowRunEvent.event_id == request.idempotencyKey)
    )
    if existing is not None:
        if existing.run_id != run_id:
            raise WorkflowRunEventConflictError(
                f"event_id {request.idempotencyKey!r} already belongs to another run"
            )
        event = WorkflowNodeRunEvent.model_validate(existing.payload)
        raw = event.details.get("researchGraph")
        try:
            envelope = WorkflowResearchGraphEvent.model_validate(raw)
        except ValueError as exc:
            raise WorkflowRunEventConflictError(
                f"event_id {request.idempotencyKey!r} is not a graph mutation"
            ) from exc
        if (
            envelope.action != request.action
            or envelope.traceId != request.traceId
            or envelope.nodeId != request.nodeId
            or envelope.expectedRevision != request.expectedRevision
            or envelope.expectedSequence != request.expectedSequence
            or envelope.lineage != request.lineage
            or envelope.targetId != request.targetId
            or (
                (envelope.entity.model_dump(mode="json") if envelope.entity else None)
                != (request.entity.model_dump(mode="json") if request.entity else None)
            )
        ):
            raise WorkflowRunEventConflictError(
                f"event_id {request.idempotencyKey!r} already exists with different mutation"
            )
        return WorkflowRunEventAppendResult(events=[event], appended_events=[])

    persisted_rows = (
        (
            await session.execute(
                select(WorkflowRunEvent)
                .where(WorkflowRunEvent.run_id == run_id)
                .order_by(WorkflowRunEvent.sequence)
            )
        )
        .scalars()
        .all()
    )
    persisted = [WorkflowNodeRunEvent.model_validate(row.payload) for row in persisted_rows]
    try:
        graph = build_research_graph_projection(
            persisted,
            run_id=run_id,
            trace_id=trace_id,
        )
        event = build_research_graph_mutation_event(
            request,
            run_id=run_id,
            workflow_id=workflow_id,
            trace_id=trace_id,
            graph=graph,
        )
    except ResearchGraphValidationError as exc:
        raise WorkflowRunEventConflictError(str(exc)) from exc
    return await append_workflow_run_events(
        session,
        run_id=run_id,
        events=[event],
        plugins=plugins,
    )


async def mutate_workflow_research_graph(
    session: AsyncSession,
    *,
    run_id: str,
    request: WorkflowResearchGraphMutationRequest,
    plugins: WorkflowPluginRegistry | None = None,
) -> tuple[list[WorkflowNodeRunEvent], WorkflowResearchGraphProjection] | None:
    """Append a guarded mutation and return the authoritative updated graph."""

    projection = await get_workflow_run_projection(run_id, session=session)
    if projection is None:
        return None
    try:
        appended = await append_workflow_research_graph_mutation(
            session,
            run_id=run_id,
            workflow_id=projection.workflowId,
            trace_id=projection.traceId,
            request=request,
            plugins=plugins,
        )
        graph = await read_workflow_research_graph(session, run_id=run_id)
        assert graph is not None
    except (ResearchGraphReadError, WorkflowRunEventAppendError) as exc:
        raise ResearchGraphMutationError(str(exc)) from exc
    return appended.events, graph
