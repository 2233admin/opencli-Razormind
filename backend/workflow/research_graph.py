"""Replayable ResearchGraph projection over authoritative workflow run events."""

from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from backend.schemas.research_graph import (
    WorkflowResearchGraphEntityProjection,
    WorkflowResearchGraphEvent,
    WorkflowResearchGraphHistoryEntry,
    WorkflowResearchGraphMutationRequest,
    WorkflowResearchGraphProjection,
)
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent
from backend.workflow.research_graph_lineage import build_research_operator_lineage_events
from backend.workflow.workflow_plugins import (
    WorkflowPluginCapability,
    WorkflowPluginEventContext,
)


class ResearchGraphValidationError(ValueError):
    """A semantic graph event cannot participate in an authoritative fold."""


def build_research_graph_projection(
    events: list[WorkflowNodeRunEvent],
    *,
    run_id: str,
    trace_id: str,
    up_to_sequence: int | None = None,
    revision_id: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
) -> WorkflowResearchGraphProjection:
    """Fold persisted event rows into a deterministic, non-authoritative graph."""

    if up_to_sequence is not None and up_to_sequence < 0:
        raise ResearchGraphValidationError("up_to_sequence must be non-negative")
    if limit is not None and limit < 1:
        raise ResearchGraphValidationError("limit must be positive")

    ordered_events = sorted(events, key=lambda event: event.sequence)
    seen_sequences: set[int] = set()
    records: dict[str, WorkflowResearchGraphEntityProjection] = {}
    relations: dict[str, WorkflowResearchGraphEntityProjection] = {}
    history: list[WorkflowResearchGraphHistoryEntry] = []
    last_sequence = 0
    current_revision: str | None = None

    for event in ordered_events:
        if event.workflowRunId != run_id:
            raise ResearchGraphValidationError(
                f"workflow event {event.id!r} does not belong to run {run_id!r}"
            )
        if event.traceId != trace_id:
            raise ResearchGraphValidationError(
                f"workflow event {event.id!r} does not belong to trace {trace_id!r}"
            )
        if event.sequence in seen_sequences:
            raise ResearchGraphValidationError(
                f"workflow event sequence {event.sequence} is duplicated"
            )
        seen_sequences.add(event.sequence)
        if up_to_sequence is not None and event.sequence > up_to_sequence:
            break
        last_sequence = event.sequence

        raw_envelope = event.details.get("researchGraph")
        if raw_envelope is None:
            continue
        try:
            envelope = WorkflowResearchGraphEvent.model_validate(raw_envelope)
        except ValueError as exc:
            raise ResearchGraphValidationError(
                f"workflow event {event.id!r} has an invalid ResearchGraph envelope"
            ) from exc
        _validate_event_context(event, envelope)
        history.append(
            WorkflowResearchGraphHistoryEntry(
                eventId=event.id,
                sequence=event.sequence,
                action=envelope.action,
                targetId=envelope.targetId,
                entityId=envelope.entity.id if envelope.entity else None,
                revisionId=envelope.revisionId,
                runId=event.workflowRunId,
                traceId=event.traceId,
                nodeId=event.nodeId,
                lineage=envelope.lineage,
            )
        )
        if envelope.revisionId is not None:
            current_revision = envelope.revisionId
        if envelope.action in {"record", "propose"}:
            assert envelope.entity is not None
            if envelope.entity.id in records or envelope.entity.id in relations:
                raise ResearchGraphValidationError(
                    f"ResearchGraph entity {envelope.entity.id!r} is declared more than once"
                )
            record = WorkflowResearchGraphEntityProjection(
                **envelope.entity.model_dump(mode="json"),
                eventId=event.id,
                sequence=event.sequence,
                runId=event.workflowRunId,
                traceId=event.traceId,
                nodeId=event.nodeId,
                revisionId=envelope.revisionId,
                lineage=envelope.lineage,
                state="proposed" if envelope.action == "propose" else "recorded",
                authoritative=envelope.action == "record",
            )
            _validate_references(record, records, require_active=envelope.action == "propose")
            if record.kind == "relation":
                relations[record.id] = record
            else:
                records[record.id] = record
            continue

        assert envelope.targetId is not None
        target = records.get(envelope.targetId) or relations.get(envelope.targetId)
        if target is None:
            raise ResearchGraphValidationError(
                f"ResearchGraph mutation targets unknown entity {envelope.targetId!r}"
            )
        if envelope.action in {"verify", "reject"}:
            if target.state != "proposed":
                raise ResearchGraphValidationError(
                    f"ResearchGraph proposal {target.id!r} is already resolved"
                )
            target.state = "verified" if envelope.action == "verify" else "rejected"
            target.authoritative = envelope.action == "verify"
        elif envelope.action == "retract":
            if target.state not in {"proposed", "verified"}:
                raise ResearchGraphValidationError(
                    f"ResearchGraph entity {target.id!r} cannot be retracted"
                )
            _downgrade_retracted(target.id, records, relations)

    selected_entities, selected_relations = _select_records(
        records,
        relations,
        revision_id=revision_id,
        entity_id=entity_id,
        limit=limit,
    )
    return WorkflowResearchGraphProjection(
        runId=run_id,
        traceId=trace_id,
        eventCount=len(selected_entities) + len(selected_relations),
        lastSequence=last_sequence,
        currentRevision=current_revision,
        entities=selected_entities,
        relations=selected_relations,
        history=history,
    )


def validate_research_graph_transcript(
    events: list[WorkflowNodeRunEvent],
    *,
    run_id: str,
    trace_id: str,
) -> None:
    """Fail before append when a candidate semantic transcript is inconsistent."""

    build_research_graph_projection(events, run_id=run_id, trace_id=trace_id)

class ResearchGraphWorkflowPlugin:
    """ResearchGraph's explicit adapter for generic workflow plugin hooks."""

    key = "research-graph"
    capabilities = frozenset(
        {
            WorkflowPluginCapability.EVENT_CONTRIBUTION,
            WorkflowPluginCapability.EVENT_VALIDATION,
            WorkflowPluginCapability.ROUTES,
            WorkflowPluginCapability.LIFECYCLE,
        }
    )

    def __init__(self) -> None:
        self.started = False
        self.start_count = 0
        self.stop_count = 0
        self.contribution_count = 0
        self.validation_count = 0

    def contribute_events(self, context: WorkflowPluginEventContext):
        self.contribution_count += 1
        claim_projection_items = [
            item
            for item in context.output_items
            if _is_research_claim_projection(item)
        ]
        return build_research_operator_lineage_events(
            claim_projection_items,
            run_id=context.run_id,
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            node_id=context.node_id,
            sequence=context.sequence,
        )

    def validate_events(self, events, *, run_id: str, trace_id: str) -> None:
        self.validation_count += 1
        validate_research_graph_transcript(events, run_id=run_id, trace_id=trace_id)

    def register_routes(self, v1_router: object, studio_router: object) -> None:
        route_module = import_module("backend.api.v1.research_graph_routes")
        v1_router.include_router(route_module.router)
        studio_router.include_router(route_module.studio_router)

    async def start(self) -> None:
        if not self.started:
            self.started = True
            self.start_count += 1

    async def stop(self) -> None:
        if self.started:
            self.started = False
            self.stop_count += 1



def _is_research_claim_projection(item: dict[str, Any]) -> bool:
    lineage = item.get("lineage")
    return (
        isinstance(lineage, list)
        and bool(lineage)
        and isinstance(lineage[-1], dict)
        and lineage[-1].get("step") == "research.claim-project"
    )

def build_research_graph_mutation_event(
    request: WorkflowResearchGraphMutationRequest,
    *,
    run_id: str,
    workflow_id: str,
    trace_id: str,
    graph: WorkflowResearchGraphProjection,
) -> WorkflowNodeRunEvent:
    """Validate a guarded mutation against a folded transcript and construct its row."""

    if request.traceId != trace_id:
        raise ResearchGraphValidationError("mutation traceId does not belong to workflow run")
    if request.expectedSequence != graph.lastSequence:
        raise ResearchGraphValidationError("mutation expectedSequence is stale")
    if request.expectedRevision != graph.currentRevision:
        raise ResearchGraphValidationError("mutation expectedRevision is stale")

    records = {record.id: record for record in [*graph.entities, *graph.relations]}
    revision_id = request.revisionId
    target = records.get(request.targetId or "")
    if request.action == "propose":
        assert request.entity is not None
        if request.entity.id in records:
            raise ResearchGraphValidationError(
                f"ResearchGraph entity {request.entity.id!r} is declared more than once"
            )
        if revision_id is None:
            revision_id = request.expectedRevision
    else:
        if target is None:
            raise ResearchGraphValidationError(
                f"ResearchGraph mutation targets unknown entity {request.targetId!r}"
            )
        if request.nodeId != target.nodeId:
            raise ResearchGraphValidationError("mutation nodeId does not match target entity")
        if request.action in {"verify", "reject"} and target.state != "proposed":
            raise ResearchGraphValidationError(
                f"ResearchGraph proposal {target.id!r} is already resolved"
            )
        if request.action == "retract" and target.state not in {"proposed", "verified"}:
            raise ResearchGraphValidationError(
                f"ResearchGraph entity {target.id!r} cannot be retracted"
            )
        revision_id = target.revisionId

    event_type = (
        _event_type_for_entity(request.entity)
        if request.entity is not None
        else _event_type_for_entity(target)
    )
    event_id = request.idempotencyKey
    envelope: dict[str, Any] = {
        "schemaVersion": 1,
        "eventId": event_id,
        "idempotencyKey": event_id,
        "eventType": event_type,
        "runId": run_id,
        "traceId": trace_id,
        "nodeId": request.nodeId,
        "expectedRevision": request.expectedRevision,
        "expectedSequence": request.expectedSequence,
        "revisionId": revision_id,
        "lineage": request.lineage,
        "action": request.action,
    }
    if request.entity is not None:
        envelope["entity"] = request.entity.model_dump(mode="json")
    else:
        envelope["targetId"] = request.targetId
    return WorkflowNodeRunEvent(
        id=event_id,
        sequence=graph.lastSequence + 1,
        workflowId=workflow_id,
        workflowRunId=run_id,
        traceId=trace_id,
        nodeId=request.nodeId,
        eventType="partial",
        createdAt=datetime.now(UTC).isoformat(),
        nodePath=[request.nodeId],
        details={"researchGraph": envelope},
    )




def _event_type_for_entity(record: Any) -> str:
    kind = record.kind
    return {
        "source": "source/recorded",
        "evidence": "evidence/linked",
        "claim": "claim/projected",
        "relation": "relation/projected",
    }[kind]



def _validate_event_context(
    event: WorkflowNodeRunEvent, envelope: WorkflowResearchGraphEvent
) -> None:
    if envelope.eventId != event.id:
        raise ResearchGraphValidationError("ResearchGraph eventId does not match workflow event ID")
    if envelope.runId != event.workflowRunId:
        raise ResearchGraphValidationError("ResearchGraph runId does not match workflow run")
    if envelope.traceId != event.traceId:
        raise ResearchGraphValidationError("ResearchGraph traceId does not match workflow trace")
    if envelope.nodeId != event.nodeId:
        raise ResearchGraphValidationError("ResearchGraph nodeId does not match workflow node")


def _validate_references(
    record: WorkflowResearchGraphEntityProjection,
    entities: dict[str, WorkflowResearchGraphEntityProjection],
    *,
    require_active: bool,
) -> None:
    missing_sources = [source_id for source_id in record.sourceIds if source_id not in entities]
    if missing_sources:
        raise ResearchGraphValidationError(
            f"ResearchGraph entity {record.id!r} references unknown sources {missing_sources!r}"
        )
    missing_evidence = [
        evidence_id for evidence_id in record.evidenceIds if evidence_id not in entities
    ]
    if missing_evidence:
        raise ResearchGraphValidationError(
            f"ResearchGraph entity {record.id!r} references unknown evidence {missing_evidence!r}"
        )
    referenced_ids = [*record.sourceIds, *record.evidenceIds]
    if record.kind == "relation":
        assert record.subjectId is not None and record.objectId is not None
        missing_endpoints = [
            entity_id
            for entity_id in (record.subjectId, record.objectId)
            if entity_id not in entities
        ]
        if missing_endpoints:
            raise ResearchGraphValidationError(
                f"ResearchGraph relation {record.id!r} references unknown entities "
                f"{missing_endpoints!r}"
            )
        referenced_ids.extend((record.subjectId, record.objectId))
    if require_active:
        retracted = [
            entity_id
            for entity_id in referenced_ids
            if entities[entity_id].state == "retracted"
        ]
        if retracted:
            raise ResearchGraphValidationError(
                f"ResearchGraph entity {record.id!r} references retracted entities {retracted!r}"
            )


def _downgrade_retracted(
    entity_id: str,
    entities: dict[str, WorkflowResearchGraphEntityProjection],
    relations: dict[str, WorkflowResearchGraphEntityProjection],
) -> None:
    all_records = {**entities, **relations}
    pending = [entity_id]
    while pending:
        current_id = pending.pop()
        current = all_records[current_id]
        if current.state == "retracted":
            continue
        current.state = "retracted"
        current.authoritative = False
        for candidate in all_records.values():
            dependencies = [*candidate.sourceIds, *candidate.evidenceIds]
            if candidate.kind == "relation":
                dependencies.extend((candidate.subjectId, candidate.objectId))
            if current_id in dependencies:
                pending.append(candidate.id)


def _select_records(
    entities: dict[str, WorkflowResearchGraphEntityProjection],
    relations: dict[str, WorkflowResearchGraphEntityProjection],
    *,
    revision_id: str | None,
    entity_id: str | None,
    limit: int | None,
) -> tuple[
    list[WorkflowResearchGraphEntityProjection],
    list[WorkflowResearchGraphEntityProjection],
]:
    selected_entities = list(entities.values())
    selected_relations = list(relations.values())
    if revision_id is not None:
        selected_entities = [
            record for record in selected_entities if record.revisionId == revision_id
        ]
        selected_relations = [
            record for record in selected_relations if record.revisionId == revision_id
        ]
    if entity_id is not None:
        matching_relations = [
            relation
            for relation in selected_relations
            if entity_id in {relation.id, relation.subjectId, relation.objectId}
        ]
        selected_ids = {entity_id}
        for relation in matching_relations:
            assert relation.subjectId is not None and relation.objectId is not None
            selected_ids.update((relation.subjectId, relation.objectId))
        selected_entities = [
            record for record in selected_entities if record.id in selected_ids
        ]
        selected_relations = matching_relations
    combined = sorted(
        [*selected_entities, *selected_relations],
        key=lambda record: (record.sequence, record.id),
    )
    if limit is not None:
        combined = combined[:limit]
    return (
        [record for record in combined if record.kind != "relation"],
        [record for record in combined if record.kind == "relation"],
    )
