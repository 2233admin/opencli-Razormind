import pytest

from backend.schemas.research_graph import WorkflowResearchGraphMutationRequest
from backend.schemas.workflow_runtime import WorkflowNodeRunEvent
from backend.workflow.research_graph import (
    ResearchGraphValidationError,
    ResearchGraphWorkflowPlugin,
    build_research_graph_mutation_event,
    build_research_graph_projection,
)
from backend.workflow.research_graph_lineage import (
    build_research_operator_lineage_events,
)

RUN_ID = "run-research-graph"
TRACE_ID = "trace-research-graph"
NODE_ID = "research-node"


def _event(sequence: int, event_type: str, entity: dict, *, revision_id: str = "rev-1"):
    event_id = f"research-event-{sequence}"
    return WorkflowNodeRunEvent(
        id=event_id,
        sequence=sequence,
        workflowId="workflow-research-graph",
        workflowRunId=RUN_ID,
        traceId=TRACE_ID,
        nodeId=NODE_ID,
        eventType="partial",
        createdAt="2026-08-29T00:00:00Z",
        details={
            "researchGraph": {
                "schemaVersion": 1,
                "eventId": event_id,
                "idempotencyKey": event_id,
                "eventType": event_type,
                "runId": RUN_ID,
                "traceId": TRACE_ID,
                "nodeId": NODE_ID,
                "revisionId": revision_id,
                "lineage": {"evidenceBatchId": "batch-1"},
                "entity": entity,
            }
        },
    )


def _transcript() -> list[WorkflowNodeRunEvent]:
    return [
        _event(1, "source/recorded", {"id": "source-1", "kind": "source"}),
        _event(
            2,
            "evidence/linked",
            {"id": "evidence-1", "kind": "evidence", "sourceIds": ["source-1"]},
        ),
        _event(
            3,
            "claim/projected",
            {"id": "claim-1", "kind": "claim", "evidenceIds": ["evidence-1"]},
        ),
        _event(
            4,
            "relation/projected",
            {
                "id": "relation-1",
                "kind": "relation",
                "evidenceIds": ["evidence-1"],
                "subjectId": "claim-1",
                "objectId": "evidence-1",
            },
        ),
    ]


def _fold(events: list[WorkflowNodeRunEvent], **kwargs):
    return build_research_graph_projection(
        events,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        **kwargs,
    )


def test_fold_is_deterministic_across_out_of_order_input_and_prefix_replay():
    events = _transcript()

    full = _fold(list(reversed(events)))
    recovered = _fold(events)
    prefix = _fold(events, up_to_sequence=3)

    assert full == recovered
    assert [record.id for record in full.entities] == [
        "source-1",
        "evidence-1",
        "claim-1",
    ]
    assert [record.id for record in full.relations] == ["relation-1"]
    assert full.lastSequence == 4
    assert prefix.lastSequence == 3
    assert [record.id for record in prefix.entities] == [
        "source-1",
        "evidence-1",
        "claim-1",
    ]
    assert prefix.relations == []


def test_fold_preserves_event_and_evidence_provenance_and_local_scope():
    graph = _fold(_transcript(), entity_id="claim-1")

    claim = next(record for record in graph.entities if record.id == "claim-1")
    assert claim.eventId == "research-event-3"
    assert claim.runId == RUN_ID
    assert claim.traceId == TRACE_ID
    assert claim.nodeId == NODE_ID
    assert claim.revisionId == "rev-1"
    assert claim.lineage == {"evidenceBatchId": "batch-1"}
    assert claim.evidenceIds == ["evidence-1"]
    assert [record.id for record in graph.relations] == ["relation-1"]


def test_fold_rejects_forward_or_unknown_references_before_projection():
    events = [
        _event(
            1,
            "claim/projected",
            {"id": "claim-1", "kind": "claim", "evidenceIds": ["evidence-missing"]},
        )
    ]

    with pytest.raises(ResearchGraphValidationError, match="unknown evidence"):
        _fold(events)


def test_fold_rejects_duplicate_entity_ids():
    events = [
        _event(1, "source/recorded", {"id": "source-1", "kind": "source"}),
        _event(2, "source/recorded", {"id": "source-1", "kind": "source"}),
    ]

    with pytest.raises(ResearchGraphValidationError, match="declared more than once"):
        _fold(events)


def test_plugin_validation_rejects_cross_run_and_unsupported_envelopes():
    plugin = ResearchGraphWorkflowPlugin()
    event = _event(1, "source/recorded", {"id": "source-1", "kind": "source"})
    cross_run_event = event.model_copy(
        update={
            "details": {
                "researchGraph": {
                    **event.details["researchGraph"],
                    "runId": "other-run",
                }
            }
        },
        deep=True,
    )
    with pytest.raises(ResearchGraphValidationError, match="runId"):
        plugin.validate_events([cross_run_event], run_id=RUN_ID, trace_id=TRACE_ID)

    unsupported_event = event.model_copy(
        update={
            "details": {
                "researchGraph": {
                    **event.details["researchGraph"],
                    "schemaVersion": 2,
                }
            }
        },
        deep=True,
    )
    with pytest.raises(ResearchGraphValidationError, match="invalid ResearchGraph envelope"):
        plugin.validate_events([unsupported_event], run_id=RUN_ID, trace_id=TRACE_ID)


def test_legacy_events_produce_an_empty_graph_projection():
    legacy_event = WorkflowNodeRunEvent(
        id="legacy-event",
        sequence=1,
        workflowId="workflow-research-graph",
        workflowRunId=RUN_ID,
        traceId=TRACE_ID,
        nodeId=NODE_ID,
        eventType="completed",
        createdAt="2026-08-29T00:00:00Z",
    )

    graph = _fold([legacy_event])

    assert graph.eventCount == 0
    assert graph.entities == []
    assert graph.relations == []
    assert graph.lastSequence == 1


def test_fold_tracks_proposal_verification_and_retraction_dependencies():
    source = _event(1, "source/recorded", {"id": "source-1", "kind": "source"})
    evidence = _event(
        2,
        "evidence/linked",
        {"id": "evidence-1", "kind": "evidence", "sourceIds": ["source-1"]},
    )
    proposal = _event(
        3,
        "claim/projected",
        {"id": "claim-1", "kind": "claim", "evidenceIds": ["evidence-1"]},
    ).model_copy(
        update={
            "details": {
                "researchGraph": {
                    **_event(
                        3,
                        "claim/projected",
                        {
                            "id": "claim-1",
                            "kind": "claim",
                            "evidenceIds": ["evidence-1"],
                        },
                    ).details["researchGraph"],
                    "action": "propose",
                }
            }
        },
        deep=True,
    )

    def transition(sequence: int, action: str) -> WorkflowNodeRunEvent:
        event_id = f"research-event-{sequence}"
        return WorkflowNodeRunEvent(
            id=event_id,
            sequence=sequence,
            workflowId="workflow-research-graph",
            workflowRunId=RUN_ID,
            traceId=TRACE_ID,
            nodeId=NODE_ID,
            eventType="partial",
            createdAt="2026-08-29T00:00:00Z",
            details={
                "researchGraph": {
                    "schemaVersion": 1,
                    "eventId": event_id,
                    "idempotencyKey": event_id,
                    "eventType": "claim/projected",
                    "runId": RUN_ID,
                    "traceId": TRACE_ID,
                    "nodeId": NODE_ID,
                    "revisionId": "rev-1",
                    "lineage": {"evidenceBatchId": "batch-1"},
                    "action": action,
                    "targetId": "claim-1",
                }
            },
        )

    relation = _event(
        4,
        "relation/projected",
        {
            "id": "relation-1",
            "kind": "relation",
            "evidenceIds": ["evidence-1"],
            "subjectId": "claim-1",
            "objectId": "evidence-1",
        },
    )
    graph = _fold(
        [source, evidence, proposal, relation, transition(5, "verify"), transition(6, "retract")]
    )

    claim = next(record for record in graph.entities if record.id == "claim-1")
    relation = next(record for record in graph.relations if record.id == "relation-1")
    assert claim.state == relation.state == "retracted"
    assert claim.authoritative is relation.authoritative is False
    assert [entry.action for entry in graph.history] == [
        "record",
        "record",
        "propose",
        "record",
        "verify",
        "retract",
    ]

def test_operator_lineage_emits_complete_evidence_only_once_and_skips_incomplete_refs():
    reference = {
        "sourceId": "source-1",
        "evidenceId": "evidence-1",
        "itemKey": "item-1",
        "batchId": "batch-1",
        "runId": RUN_ID,
        "nodeId": "collector-node",
        "manifestUri": "odp://manifest-1",
    }
    complete_items = [
        {
            "normalizedData": {
                "claim": {
                    "claimId": "claim-operator",
                    "statement": "A sourced claim.",
                    "evidenceRefs": [
                        reference,
                        {**reference, "evidenceId": "evidence-2", "itemKey": "item-2"},
                    ],
                }
            }
        }
    ]

    events = build_research_operator_lineage_events(
        complete_items,
        run_id=RUN_ID,
        workflow_id="workflow-research-graph",
        trace_id=TRACE_ID,
        node_id=NODE_ID,
        sequence=0,
    )

    graph = _fold(events)
    assert [record.id for record in graph.entities] == [
        "source:source-1",
        "evidence:evidence-1",
        "evidence:evidence-2",
        "claim:claim-operator",
    ]
    assert all(record.authoritative for record in graph.entities)
    assert build_research_operator_lineage_events(
        [
            {
                "normalizedData": {
                    "claim": {
                        **complete_items[0]["normalizedData"]["claim"],
                        "evidenceRefs": [
                            {key: value for key, value in reference.items() if key != "manifestUri"}
                        ],
                    }
                }
            }
        ],
        run_id=RUN_ID,
        workflow_id="workflow-research-graph",
        trace_id=TRACE_ID,
        node_id=NODE_ID,
        sequence=0,
    ) == []

def test_mutation_builder_rejects_unknown_retracted_duplicate_stale_and_cross_run():
    base_graph = _fold(_transcript())
    proposal_request = WorkflowResearchGraphMutationRequest(
        schemaVersion=1,
        idempotencyKey="proposal-candidate",
        expectedRevision="rev-1",
        expectedSequence=base_graph.lastSequence,
        action="propose",
        traceId=TRACE_ID,
        nodeId=NODE_ID,
        entity={"id": "candidate", "kind": "claim", "evidenceIds": ["evidence-1"]},
    )
    proposal = build_research_graph_mutation_event(
        proposal_request,
        run_id=RUN_ID,
        workflow_id="workflow-research-graph",
        trace_id=TRACE_ID,
        graph=base_graph,
    )
    proposed_graph = _fold([*_transcript(), proposal])
    verified = build_research_graph_mutation_event(
        proposal_request.model_copy(
            update={
                "idempotencyKey": "verify-candidate",
                "expectedSequence": proposed_graph.lastSequence,
                "action": "verify",
                "entity": None,
                "targetId": "candidate",
            }
        ),
        run_id=RUN_ID,
        workflow_id="workflow-research-graph",
        trace_id=TRACE_ID,
        graph=proposed_graph,
    )
    verified_graph = _fold([*_transcript(), proposal, verified])
    retraction = build_research_graph_mutation_event(
        proposal_request.model_copy(
            update={
                "idempotencyKey": "retract-candidate",
                "expectedSequence": verified_graph.lastSequence,
                "action": "retract",
                "entity": None,
                "targetId": "candidate",
            }
        ),
        run_id=RUN_ID,
        workflow_id="workflow-research-graph",
        trace_id=TRACE_ID,
        graph=verified_graph,
    )
    graph = _fold([*_transcript(), proposal, verified, retraction])

    def request(**updates):
        return proposal_request.model_copy(
            update={"expectedSequence": graph.lastSequence, **updates},
            deep=True,
        )

    with pytest.raises(ResearchGraphValidationError, match="unknown entity"):
        build_research_graph_mutation_event(
            request(
                idempotencyKey="unknown-target",
                action="reject",
                entity=None,
                targetId="unknown",
            ),
            run_id=RUN_ID,
            workflow_id="workflow-research-graph",
            trace_id=TRACE_ID,
            graph=graph,
        )
    with pytest.raises(ResearchGraphValidationError, match="already resolved"):
        build_research_graph_mutation_event(
            request(
                idempotencyKey="verify-retracted",
                action="verify",
                entity=None,
                targetId="candidate",
            ),
            run_id=RUN_ID,
            workflow_id="workflow-research-graph",
            trace_id=TRACE_ID,
            graph=graph,
        )
    with pytest.raises(ResearchGraphValidationError, match="declared more than once"):
        build_research_graph_mutation_event(
            request(idempotencyKey="duplicate-candidate"),
            run_id=RUN_ID,
            workflow_id="workflow-research-graph",
            trace_id=TRACE_ID,
            graph=graph,
        )
    with pytest.raises(ResearchGraphValidationError, match="expectedSequence is stale"):
        build_research_graph_mutation_event(
            request(idempotencyKey="stale", expectedSequence=0),
            run_id=RUN_ID,
            workflow_id="workflow-research-graph",
            trace_id=TRACE_ID,
            graph=graph,
        )
    with pytest.raises(ResearchGraphValidationError, match="traceId does not belong"):
        build_research_graph_mutation_event(
            request(idempotencyKey="cross-run", traceId="other-trace"),
            run_id=RUN_ID,
            workflow_id="workflow-research-graph",
            trace_id=TRACE_ID,
            graph=graph,
        )
    with pytest.raises(ValueError, match="schemaVersion"):
        WorkflowResearchGraphMutationRequest.model_validate(
            {**proposal_request.model_dump(mode="json"), "schemaVersion": 2}
        )
