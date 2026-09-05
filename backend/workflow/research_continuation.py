"""Governed cross-run continuation and ledger projection for research workflows."""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.workflow_run import WorkflowRun, WorkflowRunEvent
from backend.schemas.workflow import (
    WorkflowProject,
    WorkflowProjectNode,
    WorkflowRunInput,
    WorkflowRunStartRequest,
    WorkflowRunTrigger,
)
from backend.schemas.workflow_research import (
    WorkflowResearchContinuationRequest,
    WorkflowResearchContinuationResponse,
    WorkflowResearchLedgerEntry,
    WorkflowResearchLedgerResponse,
)
from backend.schemas.workflow_runtime import (
    WorkflowNodeRunEvent,
    WorkflowRunProjection,
)
from backend.workflow.opencli_hda_tracer import start_workflow_run
from backend.workflow.workflow_plugins import WorkflowPluginRegistry

_MAX_CONTINUATION_ITEMS = 200
_MAX_LEDGER_ITEMS = 1000
_MAX_CONTINUATION_BYTES = 2 * 1024 * 1024
_MAX_LEDGER_BYTES = 8 * 1024 * 1024


class ResearchContinuationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def continue_research_workflow_run(
    parent_run_id: str,
    body: WorkflowResearchContinuationRequest,
    *,
    session: AsyncSession,
    plugins: WorkflowPluginRegistry | None = None,
) -> WorkflowResearchContinuationResponse | None:
    parent = await _load_run(parent_run_id, session)
    if parent is None:
        return None
    ledger = await get_research_ledger(parent_run_id, session=session)
    assert ledger is not None
    latest = ledger.entries[-1]
    if latest.researchStatus != "needs_evidence" or not latest.proposal:
        raise ResearchContinuationError(
            "research_continuation_not_available",
            "The latest research revision does not accept more evidence.",
        )
    if latest.revisionId != body.expectedRevisionId:
        raise ResearchContinuationError(
            "stale_research_revision",
            "expectedRevisionId is not the latest research revision.",
        )
    if latest.proposal.get("proposalId") != body.proposalId:
        raise ResearchContinuationError(
            "stale_research_proposal",
            "proposalId is not the latest collection proposal.",
        )

    parent_request, _, parent_events = parent
    report = _latest_coverage_report(parent_events)
    if report is None or report.get("decision") != "collect_more":
        raise ResearchContinuationError(
            "research_continuation_not_available",
            "The parent run has no executable collect_more decision.",
        )
    iteration = _int(report, "iteration") + 1
    additional_count = _int(report, "additionalCollectionCount") + 1
    if iteration > _int(report, "maxIterations") or additional_count > _int(
        report, "maxAdditionalCollections"
    ):
        raise ResearchContinuationError(
            "research_budget_exhausted",
            "The research continuation budget is exhausted.",
        )

    incoming_items = sum(len(items) for items in body.sourceOutputs.values())
    incoming_bytes = len(_canonical(body.sourceOutputs).encode("utf-8"))
    if incoming_items > _MAX_CONTINUATION_ITEMS or incoming_bytes > _MAX_CONTINUATION_BYTES:
        raise ResearchContinuationError(
            "research_continuation_too_large",
            "One continuation accepts at most 200 items and 2 MiB.",
        )
    source_node_ids = {
        node.id
        for node in _walk_project_nodes(parent_request.project.nodes)
        if node.kind == "source"
    }
    unknown_sources = sorted(set(body.sourceOutputs) - source_node_ids)
    if unknown_sources:
        raise ResearchContinuationError(
            "unknown_research_source",
            f"sourceOutputs contains unknown source nodes: {', '.join(unknown_sources)}",
        )

    existing_outputs = _restart_source_outputs(parent_request)
    merged_outputs, new_item_count = _merge_unique_source_outputs(
        existing_outputs, body.sourceOutputs
    )
    if new_item_count == 0:
        raise ResearchContinuationError(
            "no_new_evidence",
            "The continuation did not add any new evidence.",
        )
    merged_items = sum(len(items) for items in merged_outputs.values())
    merged_bytes = len(_canonical(merged_outputs).encode("utf-8"))
    if merged_items > _MAX_LEDGER_ITEMS or merged_bytes > _MAX_LEDGER_BYTES:
        raise ResearchContinuationError(
            "research_ledger_budget_exhausted",
            "The research ledger accepts at most 1000 items and 8 MiB.",
        )

    project_hash = _research_project_hash(parent_request.project)
    parent_context = _dict(parent_request.input.payload.get("researchLedger"))
    root_run_id = str(parent_context.get("rootRunId") or ledger.rootRunId)
    expected_project_hash = parent_context.get("projectHash")
    if expected_project_hash not in {None, project_hash}:
        raise ResearchContinuationError(
            "research_project_changed",
            "The parent workflow graph no longer matches the research ledger.",
        )
    continuation_input_hash = _digest(body.sourceOutputs)
    child_run_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            ":".join(
                (
                    "opencli-admin",
                    "research-continuation",
                    root_run_id,
                    body.expectedRevisionId,
                    body.proposalId,
                    body.idempotencyKey,
                )
            ),
        )
    )
    existing_child = await _load_run(child_run_id, session)
    if existing_child is not None:
        existing_context = _dict(existing_child[0].input.payload.get("researchLedger"))
        if existing_context.get("continuationInputHash") != continuation_input_hash:
            raise ResearchContinuationError(
                "research_idempotency_conflict",
                "idempotencyKey was already used with different evidence.",
            )
        return await _continuation_response(
            ledger_id=root_run_id,
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            iteration=iteration,
            additional_count=additional_count,
            replayed=True,
            session=session,
        )

    revision = _latest_revision(parent_events)
    project = _project_for_next_iteration(
        parent_request.project,
        iteration=iteration,
        additional_count=additional_count,
        previous_claims=_dict_list(revision.get("currentClaims")),
        previous_scenarios=_dict_list(revision.get("currentScenarios")),
    )
    research_context = {
        "schemaVersion": "research-ledger.v1",
        "ledgerId": root_run_id,
        "rootRunId": root_run_id,
        "parentRunId": parent_run_id,
        "parentRevisionId": body.expectedRevisionId,
        "proposalId": body.proposalId,
        "projectHash": project_hash,
        "iteration": iteration,
        "additionalCollectionCount": additional_count,
        "continuationInputHash": continuation_input_hash,
        "cumulativeEvidenceItems": merged_items,
        "cumulativeInputBytes": merged_bytes,
    }
    child_input = parent_request.input.model_copy(
        update={
            "source": "agent",
            "payload": {**parent_request.input.payload, "researchLedger": research_context},
        },
        deep=True,
    )
    child_request = parent_request.model_copy(
        update={
            "project": project,
            "runId": child_run_id,
            "traceId": str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"opencli-admin:research-trace:{child_run_id}",
                )
            ),
            "sourceOutputs": merged_outputs,
            "trigger": WorkflowRunTrigger(
                kind="ai",
                requestId=body.idempotencyKey,
                idempotencyKey=body.idempotencyKey,
            ),
            "input": WorkflowRunInput.model_validate(child_input),
        },
        deep=True,
    )
    await start_workflow_run(child_request, session=session, plugins=plugins)
    return await _continuation_response(
        ledger_id=root_run_id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        iteration=iteration,
        additional_count=additional_count,
        replayed=False,
        session=session,
    )


async def get_research_ledger(
    run_id: str,
    *,
    session: AsyncSession,
) -> WorkflowResearchLedgerResponse | None:
    chain: list[
        tuple[str, WorkflowRunStartRequest, WorkflowRunProjection, list[WorkflowNodeRunEvent]]
    ] = []
    current_run_id: str | None = run_id
    seen: set[str] = set()
    while current_run_id and current_run_id not in seen and len(chain) < 5:
        seen.add(current_run_id)
        loaded = await _load_run(current_run_id, session)
        if loaded is None:
            return None if not chain else None
        request, projection, events = loaded
        chain.append((current_run_id, request, projection, events))
        context = _dict(request.input.payload.get("researchLedger"))
        current_run_id = _text(context.get("parentRunId"))

    chain.reverse()
    root_run_id = chain[0][0]
    entries: list[WorkflowResearchLedgerEntry] = []
    for current_id, request, projection, events in chain:
        context = _dict(request.input.payload.get("researchLedger"))
        entry = _ledger_entry(
            current_id,
            request,
            projection,
            events,
            root_run_id=str(context.get("rootRunId") or root_run_id),
        )
        if (
            entries
            and entry.decision == "collect_more"
            and entry.semanticClaimSetHash == entries[-1].semanticClaimSetHash
            and entry.gaps == entries[-1].gaps
        ):
            entry = entry.model_copy(
                update={"researchStatus": "incomplete", "stopReason": "no_progress"}
            )
        entries.append(entry)
    return WorkflowResearchLedgerResponse(
        ledgerId=root_run_id,
        rootRunId=root_run_id,
        currentRunId=run_id,
        entries=entries,
    )


async def _continuation_response(
    *,
    ledger_id: str,
    parent_run_id: str,
    child_run_id: str,
    iteration: int,
    additional_count: int,
    replayed: bool,
    session: AsyncSession,
    plugins: WorkflowPluginRegistry | None = None,
) -> WorkflowResearchContinuationResponse:
    ledger = await get_research_ledger(child_run_id, session=session)
    loaded = await _load_run(child_run_id, session)
    assert ledger is not None and loaded is not None
    return WorkflowResearchContinuationResponse(
        ledgerId=ledger_id,
        parentRunId=parent_run_id,
        childRunId=child_run_id,
        iteration=iteration,
        additionalCollectionCount=additional_count,
        researchStatus=ledger.entries[-1].researchStatus,
        replayed=replayed,
        projectionPath=f"/api/v1/workflows/runs/{child_run_id}",
        eventsPath=f"/api/v1/workflows/runs/{child_run_id}/events",
        projection=loaded[1],
    )


async def _load_run(
    run_id: str,
    session: AsyncSession,
) -> tuple[WorkflowRunStartRequest, WorkflowRunProjection, list[WorkflowNodeRunEvent]] | None:
    row = await session.get(WorkflowRun, run_id)
    if row is None:
        return None
    event_rows = (
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
    return (
        WorkflowRunStartRequest.model_validate(row.request),
        WorkflowRunProjection.model_validate(row.projection),
        [WorkflowNodeRunEvent.model_validate(event.payload) for event in event_rows],
    )


def _ledger_entry(
    run_id: str,
    request: WorkflowRunStartRequest,
    projection: WorkflowRunProjection,
    events: list[WorkflowNodeRunEvent],
    *,
    root_run_id: str,
) -> WorkflowResearchLedgerEntry:
    context = _dict(request.input.payload.get("researchLedger"))
    report = _latest_coverage_report(events) or {}
    revision = _latest_revision(events)
    gate = _latest_metrics(events, "publishAllowed")
    decision = report.get("decision")
    publish_allowed = gate.get("publishAllowed")
    if projection.status == "failed" or not projection.valid:
        research_status = "failed"
    elif publish_allowed is True:
        research_status = "final"
    elif decision == "collect_more":
        research_status = "needs_evidence"
    elif decision == "stop_incomplete":
        research_status = "incomplete"
    elif publish_allowed is False:
        research_status = "blocked"
    else:
        research_status = "running"
    evidence_refs: dict[str, dict[str, Any]] = {}
    for artifact in [
        *_dict_list(revision.get("currentClaims")),
        *_dict_list(revision.get("currentScenarios")),
    ]:
        for reference in _dict_list(artifact.get("evidenceRefs")):
            evidence_refs[_canonical(reference)] = reference
    return WorkflowResearchLedgerEntry(
        runId=run_id,
        parentRunId=_text(context.get("parentRunId")),
        rootRunId=str(context.get("rootRunId") or root_run_id),
        iteration=int(report.get("iteration") or context.get("iteration") or 1),
        additionalCollectionCount=int(
            report.get("additionalCollectionCount") or context.get("additionalCollectionCount") or 0
        ),
        revisionId=_text(_latest_metrics(events, "revisionId").get("revisionId")),
        parentRevisionId=_text(context.get("parentRevisionId")),
        claimSetHash=_text(report.get("claimSetHash")),
        semanticClaimSetHash=_text(report.get("semanticClaimSetHash")),
        scenarioSetHash=_text(revision.get("scenarioSetHash")),
        decision=decision if decision in {"finalize", "collect_more", "stop_incomplete"} else None,
        researchStatus=research_status,
        stopReason=_text(report.get("stopReason")),
        proposal=_dict(report.get("continuationProposal")) or None,
        gaps=_strings(report.get("gaps")),
        publishAllowed=publish_allowed if isinstance(publish_allowed, bool) else None,
        gateReasons=_strings(gate.get("gateReasons")),
        evidenceRefs=sorted(evidence_refs.values(), key=_canonical),
        createdAt=events[-1].createdAt if events else projection.startedAt,
    )


def _latest_coverage_report(events: list[WorkflowNodeRunEvent]) -> dict[str, Any] | None:
    for event in reversed(events):
        report = _dict(_dict(event.details.get("metrics")).get("coverageReport"))
        if report:
            return report
    return None


def _latest_revision(events: list[WorkflowNodeRunEvent]) -> dict[str, Any]:
    return _dict(_latest_metrics(events, "researchRevision").get("researchRevision"))


def _latest_metrics(events: list[WorkflowNodeRunEvent], key: str) -> dict[str, Any]:
    for event in reversed(events):
        metrics = _dict(event.details.get("metrics"))
        if key in metrics:
            return metrics
    return {}


def _project_for_next_iteration(
    project: WorkflowProject,
    *,
    iteration: int,
    additional_count: int,
    previous_claims: list[dict[str, Any]],
    previous_scenarios: list[dict[str, Any]],
) -> WorkflowProject:
    updated = project.model_copy(deep=True)
    coverage_count = 0
    revision_count = 0
    for node in _walk_project_nodes(updated.nodes):
        operator_id = node.params.get("operatorId")
        if operator_id == "research.coverage-audit":
            config = _dict(node.params.get("config"))
            node.params = {
                **node.params,
                "config": {
                    **config,
                    "iteration": iteration,
                    "additionalCollectionCount": additional_count,
                },
            }
            coverage_count += 1
        elif operator_id == "research.revision-diff":
            config = _dict(node.params.get("config"))
            node.params = {
                **node.params,
                "config": {
                    **config,
                    "previousClaims": previous_claims,
                    "previousScenarios": previous_scenarios,
                },
            }
            revision_count += 1
    if coverage_count != 1 or revision_count != 1:
        raise ResearchContinuationError(
            "ambiguous_research_chain",
            "Research continuation requires exactly one coverage and one revision operator.",
        )
    return updated


def _walk_project_nodes(nodes: list[WorkflowProjectNode]) -> list[WorkflowProjectNode]:
    result: list[WorkflowProjectNode] = []
    for node in nodes:
        result.append(node)
        if node.internals:
            result.extend(_walk_project_nodes(node.internals.nodes))
    return result


def _restart_source_outputs(
    request: WorkflowRunStartRequest,
) -> dict[str, list[dict[str, Any]]]:
    outputs = {
        node_id: [dict(item) for item in items] for node_id, items in request.sourceOutputs.items()
    }
    for node in _walk_project_nodes(request.project.nodes):
        if node.kind != "source" or node.id in outputs:
            continue
        fixture_items = node.params.get("fixtureItems")
        if isinstance(fixture_items, list):
            outputs[node.id] = [dict(item) for item in fixture_items if isinstance(item, dict)]
    return outputs


def _research_project_hash(project: WorkflowProject) -> str:
    payload = project.model_dump(mode="json")

    def scrub(nodes: object) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            params = _dict(node.get("params"))
            config = _dict(params.get("config"))
            operator_id = params.get("operatorId")
            if operator_id == "research.coverage-audit":
                config.pop("iteration", None)
                config.pop("additionalCollectionCount", None)
            elif operator_id == "research.revision-diff":
                config.pop("previousClaims", None)
                config.pop("previousScenarios", None)
            if config or "config" in params:
                params["config"] = config
            node["params"] = params
            scrub(_dict(node.get("internals")).get("nodes"))

    scrub(payload.get("nodes"))
    return _digest(payload)


def _merge_unique_source_outputs(
    existing: dict[str, list[dict[str, Any]]],
    incoming: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    merged = {node_id: [dict(item) for item in items] for node_id, items in existing.items()}
    new_count = 0
    for node_id, items in incoming.items():
        target = merged.setdefault(node_id, [])
        seen = {_canonical(item) for item in target}
        for item in items:
            key = _canonical(item)
            if key in seen:
                continue
            target.append(dict(item))
            seen.add(key)
            new_count += 1
    return merged, new_count


def _int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ResearchContinuationError(
            "invalid_research_budget",
            f"{key} is missing from the parent coverage report.",
        )
    return result


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str)})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


__all__ = [
    "ResearchContinuationError",
    "continue_research_workflow_run",
    "get_research_ledger",
]
