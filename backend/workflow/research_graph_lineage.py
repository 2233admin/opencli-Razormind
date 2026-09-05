"""Automatic ResearchGraph lineage events derived from complete operator output."""


from datetime import UTC, datetime
from typing import Any

from backend.schemas.workflow_runtime import WorkflowNodeRunEvent


def build_research_operator_lineage_events(
    items: list[dict[str, Any]],
    *,
    run_id: str,
    workflow_id: str,
    trace_id: str,
    node_id: str,
    sequence: int,
) -> list[WorkflowNodeRunEvent]:
    """Create source→evidence→claim rows only from complete operator provenance."""

    events: list[WorkflowNodeRunEvent] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.get("normalizedData")
        if not isinstance(normalized, dict):
            continue
        claim = normalized.get("claim")
        if not isinstance(claim, dict):
            continue
        claim_id = _nonempty(claim.get("claimId"))
        statement = _nonempty(claim.get("statement"))
        references = claim.get("evidenceRefs")
        if claim_id is None or statement is None or not isinstance(references, list):
            continue
        complete_refs = [
            reference
            for reference in references
            if isinstance(reference, dict)
            and all(
                _nonempty(reference.get(field)) is not None
                for field in (
                    "sourceId",
                    "evidenceId",
                    "itemKey",
                    "batchId",
                    "runId",
                    "nodeId",
                    "manifestUri",
                )
            )
            and reference["runId"] == run_id
        ]
        if not complete_refs or len(complete_refs) != len(references):
            continue
        evidence_ids: list[str] = []
        for reference in sorted(complete_refs, key=lambda value: str(value["evidenceId"])):
            source_id = str(reference["sourceId"])
            evidence_id = str(reference["evidenceId"])
            source_event = _lineage_event(
                entity_id=f"source:{source_id}",
                entity={"id": f"source:{source_id}", "kind": "source", "label": source_id},
                event_type="source/recorded",
                run_id=run_id,
                workflow_id=workflow_id,
                trace_id=trace_id,
                node_id=node_id,
                sequence=sequence + len(events) + 1,
                lineage={"sourceId": source_id},
            )
            if source_event.id not in seen:
                events.append(source_event)
                seen.add(source_event.id)
            evidence_entity_id = f"evidence:{evidence_id}"
            evidence_event = _lineage_event(
                entity_id=evidence_entity_id,
                entity={
                    "id": evidence_entity_id,
                    "kind": "evidence",
                    "sourceIds": [f"source:{source_id}"],
                    "attributes": {
                        "itemKey": reference["itemKey"],
                        "batchId": reference["batchId"],
                        "manifestUri": reference["manifestUri"],
                        "odpRef": reference.get("odpRef"),
                    },
                },
                event_type="evidence/linked",
                run_id=run_id,
                workflow_id=workflow_id,
                trace_id=trace_id,
                node_id=node_id,
                sequence=sequence + len(events) + 1,
                lineage={
                    "sourceId": source_id,
                    "evidenceId": evidence_id,
                    "itemKey": str(reference["itemKey"]),
                    "batchId": str(reference["batchId"]),
                    "manifestUri": str(reference["manifestUri"]),
                },
            )
            if evidence_event.id not in seen:
                events.append(evidence_event)
                seen.add(evidence_event.id)
            evidence_ids.append(evidence_entity_id)
        claim_event = _lineage_event(
            entity_id=f"claim:{claim_id}",
            entity={
                "id": f"claim:{claim_id}",
                "kind": "claim",
                "label": statement,
                "evidenceIds": evidence_ids,
                "attributes": {
                    "claimId": claim_id,
                    "disposition": claim.get("disposition"),
                },
            },
            event_type="claim/projected",
            run_id=run_id,
            workflow_id=workflow_id,
            trace_id=trace_id,
            node_id=node_id,
            sequence=sequence + len(events) + 1,
            lineage={"claimId": claim_id},
        )
        if claim_event.id not in seen:
            events.append(claim_event)
            seen.add(claim_event.id)
    return events


def _lineage_event(
    *,
    entity_id: str,
    entity: dict[str, Any],
    event_type: str,
    run_id: str,
    workflow_id: str,
    trace_id: str,
    node_id: str,
    sequence: int,
    lineage: dict[str, str],
) -> WorkflowNodeRunEvent:
    event_id = f"{run_id}:research-graph:{entity_id}"
    return WorkflowNodeRunEvent(
        id=event_id,
        sequence=sequence,
        workflowId=workflow_id,
        workflowRunId=run_id,
        traceId=trace_id,
        nodeId=node_id,
        eventType="partial",
        createdAt=datetime.now(UTC).isoformat(),
        nodePath=[node_id],
        details={
            "researchGraph": {
                "schemaVersion": 1,
                "eventId": event_id,
                "idempotencyKey": event_id,
                "eventType": event_type,
                "runId": run_id,
                "traceId": trace_id,
                "nodeId": node_id,
                "lineage": lineage,
                "entity": entity,
            }
        },
    )


def _nonempty(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
