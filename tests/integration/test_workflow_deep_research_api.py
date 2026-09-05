"""Synthetic acceptance for the bounded Deep Research operator chain."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.database import get_db
from backend.main import Settings, create_app
from backend.models.record import CollectedRecord


def _project(required_dimensions: list[str] | None = None) -> dict:
    operator_nodes = [
        (
            "claim",
            "refine",
            "research.claim-project",
            {},
        ),
        (
            "coverage",
            "evaluate",
            "research.coverage-audit",
            {
                "requiredDimensions": (
                    required_dimensions if required_dimensions is not None else ["funding", "risk"]
                ),
                "iteration": 1,
                "maxIterations": 2,
                "additionalCollectionCount": 0,
                "maxAdditionalCollections": 1,
            },
        ),
        (
            "counter",
            "generate",
            "research.counter-thesis",
            {},
        ),
        (
            "scenario",
            "generate",
            "research.scenario-simulate",
            {
                "scenarios": [
                    {
                        "scenarioId": "liquidity-support",
                        "label": "Liquidity support persists",
                        "priorScore": 0.5,
                        "drivers": [
                            {"dimension": "funding", "weight": 0.2},
                            {"dimension": "risk", "weight": -0.2},
                        ],
                        "assumptions": ["Synthetic evidence remains current."],
                        "invalidationSignals": ["Funding evidence reverses."],
                    }
                ]
            },
        ),
        (
            "revision",
            "evaluate",
            "research.revision-diff",
            {"previousClaims": [], "previousScenarios": []},
        ),
        (
            "gate",
            "filter",
            "research.publish-gate",
            {},
        ),
    ]
    nodes = [
        {
            "id": "fixture-source",
            "kind": "source",
            "capability": "fetch",
            "adapter": "fixture-adapter",
            "params": {
                "fixtureItems": [
                    {
                        "claimKey": "liquidity",
                        "statement": "Liquidity supports the synthetic market.",
                        "evidenceId": "synthetic-funding-support",
                        "stance": "support",
                        "dimension": "funding",
                        "content": "Synthetic funding evidence.",
                        "url": "https://example.test/funding",
                    },
                    {
                        "claimKey": "liquidity",
                        "statement": "Liquidity supports the synthetic market.",
                        "evidenceId": "synthetic-risk-qualifier",
                        "stance": "qualify",
                        "dimension": "risk",
                        "content": "Synthetic risk qualifier.",
                        "url": "https://example.test/risk",
                    },
                    {
                        "claimKey": "liquidity",
                        "statement": "Liquidity supports the synthetic market.",
                        "evidenceId": "synthetic-risk-contradiction",
                        "stance": "contradict",
                        "dimension": "risk",
                        "content": "Synthetic contradictory evidence.",
                        "url": "https://example.test/counter",
                    },
                ]
            },
        },
        {
            "id": "normalize",
            "kind": "agent",
            "capability": "normalize",
            "params": {},
            "ui": {"catalogId": "intelligence.processing.normalize"},
        },
        *[
            {
                "id": node_id,
                "kind": "agent",
                "capability": "normalize",
                "params": {
                    "operatorId": operator_id,
                    "packVersion": "1.0.0",
                    "config": config,
                },
                "ui": {"catalogId": f"intelligence.data.{kind}"},
            }
            for node_id, kind, operator_id, config in operator_nodes
        ],
        {
            "id": "dedupe",
            "kind": "agent",
            "capability": "dedupe",
            "params": {"key": "contentHash", "window": "24h"},
            "ui": {"catalogId": "intelligence.processing.dedupe"},
        },
        {
            "id": "accept",
            "kind": "control",
            "capability": "accept",
            "params": {
                "mode": "automatic_with_review",
                "schema": "record.v1",
                "dedupe": "required",
                "lineageRequired": True,
                "minQuality": 0,
            },
            "ui": {"catalogId": "intelligence.control.record-acceptance"},
        },
        {
            "id": "record-sink",
            "kind": "sink",
            "capability": "store",
            "params": {
                "target": "records",
                "writeMode": "append",
                "preserveLineage": True,
            },
            "ui": {"catalogId": "intelligence.sink.records"},
        },
    ]
    chain = [
        "fixture-source",
        "normalize",
        "claim",
        "coverage",
        "counter",
        "scenario",
        "revision",
        "gate",
        "dedupe",
        "accept",
        "record-sink",
    ]
    return {
        "id": "wf-synthetic-deep-research",
        "name": "Synthetic Deep Research",
        "profile": "intelligence",
        "version": 1,
        "nodes": nodes,
        "edges": [
            {
                "id": f"e-{source}-{target}",
                "source": source,
                "target": target,
                "sourcePort": "records" if source == "accept" else "out",
                "targetPort": (
                    "candidates"
                    if target == "accept"
                    else "records" if target == "record-sink" else "in"
                ),
            }
            for source, target in zip(chain, chain[1:])
        ],
        "adapters": [
            {
                "id": "fixture-adapter",
                "type": "source",
                "provider": "fixture",
                "mode": "fixture",
                "config": {},
            }
        ],
        "agentPermissions": {},
    }


@pytest.mark.asyncio
async def test_deep_research_chain_compiles_and_runs_with_auditable_metrics(client, db_session):
    project = _project()
    compiled = await client.post("/api/v1/workflows/compile", json={"project": project})

    assert compiled.status_code == 200
    compile_data = compiled.json()["data"]
    assert compile_data["valid"] is True
    runtime_nodes = {node["id"]: node for node in compile_data["plan"]["runtime"]["nodes"]}
    assert runtime_nodes["claim"]["runtime"]["binding"]["binding_id"] == "workflow.data.refine"
    assert runtime_nodes["coverage"]["runtime"]["binding"]["binding_id"] == "workflow.data.evaluate"
    assert runtime_nodes["counter"]["runtime"]["binding"]["binding_id"] == "workflow.data.generate"
    assert runtime_nodes["scenario"]["runtime"]["binding"]["binding_id"] == "workflow.data.generate"
    assert runtime_nodes["revision"]["runtime"]["binding"]["binding_id"] == "workflow.data.evaluate"

    run = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-synthetic-research",
            "traceId": "trace-synthetic-research",
        },
    )
    assert run.status_code == 202
    assert run.json()["data"]["status"] == "completed", run.text

    events = (await client.get("/api/v1/workflows/runs/run-synthetic-research/events")).json()[
        "data"
    ]
    partials = {
        event["nodeId"]: event["details"] for event in events if event["eventType"] == "partial"
    }
    assert partials["claim"]["metrics"]["unverifiedClaimCount"] == 0
    assert partials["coverage"]["metrics"]["decision"] == "finalize"
    assert partials["coverage"]["metrics"]["stopReason"] == "coverage_satisfied"
    assert partials["counter"]["metrics"]["counterThesisCount"] == 1
    assert partials["scenario"]["metrics"]["scenarioCount"] == 1
    assert partials["revision"]["metrics"]["addedClaimCount"] == 1
    assert partials["gate"]["metrics"]["publishAllowed"] is True
    graph = await client.get(
        "/api/v1/workflows/runs/run-synthetic-research/research-graph"
    )
    assert graph.status_code == 200
    # Fixture collection has no sourceId, so automatic lineage must omit it rather
    # than manufacture a source-to-evidence chain.
    assert graph.json()["data"]["entities"] == []
    assert partials["gate"]["outputItemCount"] == 4
    batches_response = await client.get(
        "/api/v1/workflows/runs/run-synthetic-research/evidence-batches"
    )
    assert batches_response.status_code == 200
    batches = batches_response.json()["data"]["batches"]
    normalize_batch = next(
        batch for batch in batches if batch["nodeId"] == "normalize"
    )

    records = (await db_session.execute(select(CollectedRecord))).scalars().all()
    research_records = [
        record for record in records if record.normalized_data.get("researchType")
    ]
    assert {
        record.normalized_data["researchType"]
        for record in research_records
    } == {"claim", "revision"}
    claim_record = next(
        record
        for record in research_records
        if record.normalized_data["researchType"] == "claim"
    )
    references = claim_record.normalized_data["claim"]["evidenceRefs"]
    assert {reference["batchId"] for reference in references} == {normalize_batch["batchId"]}
    assert {reference["runId"] for reference in references} == {"run-synthetic-research"}
    assert {reference["manifestUri"] for reference in references} == {
        normalize_batch["manifestUri"]
    }


@pytest.mark.asyncio
async def test_enabled_plugin_derives_complete_graph_lineage_from_real_source_outputs(
    client,
):
    project = _project()
    source_node = next(node for node in project["nodes"] if node["id"] == "fixture-source")
    source_node["params"]["sourceId"] = "deterministic-deep-research-source"
    run_id = "run-plugin-lineage-tracer"
    trace_id = "trace-plugin-lineage-tracer"
    fixture_items = source_node["params"]["fixtureItems"]
    source_outputs = {
        "fixture-source": [
            {
                **item,
                "id": f"deterministic-{item['evidenceId']}",
                "sourceId": "deterministic-deep-research-source",
            }
            for item in fixture_items
        ]
    }

    started = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": run_id,
            "traceId": trace_id,
            "sourceOutputs": source_outputs,
        },
    )
    assert started.status_code == 202, started.text
    assert started.json()["data"]["status"] == "completed", started.text

    replay = await client.get(f"/api/v1/workflows/runs/{run_id}/events")
    assert replay.status_code == 200, replay.text
    graph_events = [
        event["details"]["researchGraph"]
        for event in replay.json()["data"]
        if "researchGraph" in event["details"]
    ]
    event_types = [event["eventType"] for event in graph_events]
    assert event_types[0] == "source/recorded"
    assert event_types[-1] == "claim/projected"
    evidence_events = [
        event for event in graph_events if event["eventType"] == "evidence/linked"
    ]
    assert len(evidence_events) == len(source_outputs["fixture-source"])
    for event in graph_events:
        assert event["runId"] == run_id
        assert event["traceId"] == trace_id
        assert event["nodeId"]
    for evidence_event in evidence_events:
        lineage = evidence_event["lineage"]
        assert lineage["sourceId"] == "deterministic-deep-research-source"
        assert lineage["evidenceId"]
        assert lineage["itemKey"]
        assert lineage["batchId"]
        assert lineage["manifestUri"]

    graph_response = await client.get(
        f"/api/v1/workflows/runs/{run_id}/research-graph"
    )
    assert graph_response.status_code == 200, graph_response.text
    graph = graph_response.json()["data"]
    assert graph["runId"] == run_id
    assert graph["traceId"] == trace_id
    source_entities = [entity for entity in graph["entities"] if entity["kind"] == "source"]
    evidence_entities = [entity for entity in graph["entities"] if entity["kind"] == "evidence"]
    claim_entity = next(entity for entity in graph["entities"] if entity["kind"] == "claim")
    assert len(source_entities) == 1
    assert len(evidence_entities) == len(evidence_events)
    assert all(entity["sourceIds"] == [source_entities[0]["id"]] for entity in evidence_entities)
    assert set(claim_entity["evidenceIds"]) == {
        entity["id"] for entity in evidence_entities
    }
    assert all(entity["lineage"]["batchId"] for entity in evidence_entities)

    proposal = {
        "schemaVersion": 1,
        "idempotencyKey": "plugin-lineage-proposal",
        "expectedRevision": graph["currentRevision"],
        "expectedSequence": graph["lastSequence"],
        "action": "propose",
        "traceId": trace_id,
        "nodeId": claim_entity["nodeId"],
        "entity": {
            "id": "plugin-lineage-proposal",
            "kind": "claim",
            "evidenceIds": [evidence_entities[0]["id"]],
        },
    }
    proposed = await client.post(
        f"/api/v1/workflows/runs/{run_id}/research-graph/mutations",
        json=proposal,
    )
    assert proposed.status_code == 200, proposed.text
    proposed_graph = proposed.json()["data"]["graph"]
    assert proposed_graph["entities"][-1]["state"] == "proposed"

    verified = await client.post(
        f"/api/v1/workflows/runs/{run_id}/research-graph/mutations",
        json={
            "schemaVersion": 1,
            "idempotencyKey": "plugin-lineage-verify",
            "expectedRevision": proposed_graph["currentRevision"],
            "expectedSequence": proposed_graph["lastSequence"],
            "action": "verify",
            "traceId": trace_id,
            "nodeId": claim_entity["nodeId"],
            "targetId": "plugin-lineage-proposal",
        },
    )
    assert verified.status_code == 200, verified.text
    verified_graph = verified.json()["data"]["graph"]
    assert verified_graph["entities"][-1]["state"] == "verified"

    retracted = await client.post(
        f"/api/v1/workflows/runs/{run_id}/research-graph/mutations",
        json={
            "schemaVersion": 1,
            "idempotencyKey": "plugin-lineage-retract",
            "expectedRevision": verified_graph["currentRevision"],
            "expectedSequence": verified_graph["lastSequence"],
            "action": "retract",
            "traceId": trace_id,
            "nodeId": claim_entity["nodeId"],
            "targetId": "plugin-lineage-proposal",
        },
    )
    assert retracted.status_code == 200, retracted.text
    assert retracted.json()["data"]["graph"]["entities"][-1]["state"] == "retracted"


@pytest.mark.asyncio
async def test_disabled_plugin_keeps_ordinary_workflow_run_without_graph_contributions(
    db_session,
):
    app = create_app(app_settings=Settings(workflow_plugins=""))

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as disabled_client:
            run_id = "run-plugin-disabled-tracer"
            started = await disabled_client.post(
                "/api/v1/workflows/runs",
                json={
                    "project": _project(),
                    "runId": run_id,
                    "traceId": "trace-plugin-disabled-tracer",
                },
            )
            assert started.status_code == 202, started.text
            assert started.json()["data"]["status"] == "completed", started.text

            replay = await disabled_client.get(f"/api/v1/workflows/runs/{run_id}/events")
            assert replay.status_code == 200, replay.text
            assert all(
                "researchGraph" not in event["details"]
                for event in replay.json()["data"]
            )

            graph = await disabled_client.get(
                f"/api/v1/workflows/runs/{run_id}/research-graph"
            )
            assert graph.status_code == 404
    finally:
        app.dependency_overrides.clear()



@pytest.mark.asyncio
async def test_unscoped_research_graph_mutations_are_guarded_and_replayable(client):
    project = _project()
    run_id = "run-graph-mutation"
    trace_id = "trace-graph-mutation"
    run = await client.post(
        "/api/v1/workflows/runs",
        json={"project": project, "runId": run_id, "traceId": trace_id},
    )
    assert run.status_code == 202
    initial = (
        await client.get(f"/api/v1/workflows/runs/{run_id}/research-graph")
    ).json()["data"]
    proposal = {
        "schemaVersion": 1,
        "idempotencyKey": "unscoped-proposal-1",
        "expectedRevision": None,
        "expectedSequence": initial["lastSequence"],
        "action": "propose",
        "traceId": trace_id,
        "nodeId": "claim",
        "entity": {"id": "manual-source", "kind": "source"},
    }
    endpoint = f"/api/v1/workflows/runs/{run_id}/research-graph/mutations"
    first = await client.post(endpoint, json=proposal)
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["events"][0]["id"] == proposal["idempotencyKey"]
    assert first_data["graph"]["entities"][-1]["state"] == "proposed"
    replay = await client.post(endpoint, json=proposal)
    assert replay.status_code == 200
    assert replay.json()["data"]["graph"]["history"] == first_data["graph"]["history"]
    stale = await client.post(
        endpoint,
        json={**proposal, "idempotencyKey": "unscoped-proposal-stale"},
    )
    assert stale.status_code == 409
    verified = await client.post(
        endpoint,
        json={
            "schemaVersion": 1,
            "idempotencyKey": "unscoped-verify-1",
            "expectedRevision": None,
            "expectedSequence": first_data["graph"]["lastSequence"],
            "action": "verify",
            "traceId": trace_id,
            "nodeId": "claim",
            "targetId": "manual-source",
        },
    )
    assert verified.status_code == 200
    verify_graph = verified.json()["data"]["graph"]
    assert verify_graph["entities"][-1]["authoritative"] is True
    retracted = await client.post(
        endpoint,
        json={
            "schemaVersion": 1,
            "idempotencyKey": "unscoped-retract-1",
            "expectedRevision": None,
            "expectedSequence": verify_graph["lastSequence"],
            "action": "retract",
            "traceId": trace_id,
            "nodeId": "claim",
            "targetId": "manual-source",
        },
    )
    assert retracted.status_code == 200
    graph = retracted.json()["data"]["graph"]
    assert graph["entities"][-1]["state"] == "retracted"
    assert [entry["action"] for entry in graph["history"]] == [
        "propose",
        "verify",
        "retract",
    ]

@pytest.mark.asyncio
async def test_publish_gate_blocks_incomplete_research_before_sink(
    client, db_session
):
    project = _project(["missing-dimension"])
    run = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-synthetic-research-blocked",
            "traceId": "trace-synthetic-research-blocked",
        },
    )
    assert run.status_code == 202
    assert run.json()["data"]["status"] == "completed"

    events = (
        await client.get(
            "/api/v1/workflows/runs/run-synthetic-research-blocked/events"
        )
    ).json()["data"]
    gate = next(
        event["details"]
        for event in events
        if event["nodeId"] == "gate" and event["eventType"] == "partial"
    )
    assert gate["metrics"]["publishAllowed"] is False
    assert gate["metrics"]["gateReasons"] == ["coverage_not_satisfied"]

    records = (await db_session.execute(select(CollectedRecord))).scalars().all()
    assert not [
        record
        for record in records
        if record.normalized_data.get("researchType")
    ]


@pytest.mark.asyncio
async def test_collect_more_starts_one_budgeted_child_run_and_projects_revision_ledger(
    client,
):
    project = _project(["funding", "risk"])
    initial_evidence = {
        "fixture-source": [
            {
                "claimKey": "liquidity",
                "statement": "Liquidity supports the synthetic market.",
                "evidenceId": "initial-funding",
                "stance": "support",
                "dimension": "funding",
                "content": "Initial funding evidence.",
                "url": "https://example.test/initial-funding",
            }
        ]
    }
    started = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-research-parent",
            "traceId": "trace-research-parent",
            "sourceOutputs": initial_evidence,
        },
    )
    assert started.status_code == 202

    parent_ledger_response = await client.get(
        "/api/v1/workflows/runs/run-research-parent/research-ledger"
    )
    assert parent_ledger_response.status_code == 200
    parent_ledger = parent_ledger_response.json()["data"]
    parent_entry = parent_ledger["entries"][0]
    assert parent_entry["researchStatus"] == "needs_evidence"
    assert parent_entry["gaps"] == ["risk"]
    assert parent_entry["proposal"]["action"] == "collect_more"

    continuation_body = {
        "expectedRevisionId": parent_entry["revisionId"],
        "proposalId": parent_entry["proposal"]["proposalId"],
        "idempotencyKey": "risk-evidence-v1",
        "sourceOutputs": {
            "fixture-source": [
                {
                    "claimKey": "liquidity",
                    "statement": "Liquidity supports the synthetic market.",
                    "evidenceId": "followup-risk",
                    "stance": "support",
                    "dimension": "risk",
                    "content": "Follow-up risk evidence.",
                    "url": "https://example.test/followup-risk",
                }
            ]
        },
    }
    continued = await client.post(
        "/api/v1/workflows/runs/run-research-parent/research-continuations",
        json=continuation_body,
    )
    assert continued.status_code == 202, continued.text
    continuation = continued.json()["data"]
    assert continuation["childRunId"] != "run-research-parent"
    assert continuation["iteration"] == 2
    assert continuation["additionalCollectionCount"] == 1
    assert continuation["researchStatus"] == "final"
    assert continuation["replayed"] is False

    child_ledger_response = await client.get(
        f"/api/v1/workflows/runs/{continuation['childRunId']}/research-ledger"
    )
    assert child_ledger_response.status_code == 200
    child_ledger = child_ledger_response.json()["data"]
    assert [entry["researchStatus"] for entry in child_ledger["entries"]] == [
        "needs_evidence",
        "final",
    ]
    assert child_ledger["entries"][1]["parentRunId"] == "run-research-parent"
    assert child_ledger["entries"][1]["parentRevisionId"] == parent_entry["revisionId"]
    assert child_ledger["entries"][1]["evidenceRefs"]

    replayed = await client.post(
        "/api/v1/workflows/runs/run-research-parent/research-continuations",
        json=continuation_body,
    )
    assert replayed.status_code == 202
    assert replayed.json()["data"]["childRunId"] == continuation["childRunId"]
    assert replayed.json()["data"]["replayed"] is True

    duplicate = await client.post(
        "/api/v1/workflows/runs/run-research-parent/research-continuations",
        json={
            **continuation_body,
            "idempotencyKey": "duplicate-existing-evidence",
            "sourceOutputs": initial_evidence,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "no_new_evidence"


@pytest.mark.asyncio
async def test_research_continuation_stops_when_new_input_makes_no_progress(client):
    project = _project(["funding", "risk"])
    coverage = next(node for node in project["nodes"] if node["id"] == "coverage")
    coverage["params"]["config"].update(
        {"maxIterations": 3, "maxAdditionalCollections": 2}
    )
    started = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-research-no-progress",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "funding",
                        "statement": "Funding is supported.",
                        "evidenceId": "funding-no-progress",
                        "dimension": "funding",
                        "content": "Funding evidence.",
                    }
                ]
            },
        },
    )
    assert started.status_code == 202
    parent_entry = (
        await client.get(
            "/api/v1/workflows/runs/run-research-no-progress/research-ledger"
        )
    ).json()["data"]["entries"][-1]

    continued = await client.post(
        "/api/v1/workflows/runs/run-research-no-progress/research-continuations",
        json={
            "expectedRevisionId": parent_entry["revisionId"],
            "proposalId": parent_entry["proposal"]["proposalId"],
            "idempotencyKey": "no-progress-round",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "evidenceId": "unusable-followup",
                        "metadata": "This item adds no claim or required dimension.",
                    }
                ]
            },
        },
    )
    assert continued.status_code == 202, continued.text
    child_id = continued.json()["data"]["childRunId"]
    child_ledger = (
        await client.get(
            f"/api/v1/workflows/runs/{child_id}/research-ledger"
        )
    ).json()["data"]
    assert child_ledger["entries"][-1]["researchStatus"] == "incomplete"
    assert child_ledger["entries"][-1]["stopReason"] == "no_progress"

    rejected = await client.post(
        f"/api/v1/workflows/runs/{child_id}/research-continuations",
        json={
            "expectedRevisionId": child_ledger["entries"][-1]["revisionId"],
            "proposalId": child_ledger["entries"][-1]["proposal"]["proposalId"],
            "idempotencyKey": "must-not-spin",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "risk",
                        "statement": "Risk is covered.",
                        "evidenceId": "late-risk",
                        "dimension": "risk",
                        "content": "Risk evidence.",
                    }
                ]
            },
        },
    )
    assert rejected.status_code == 409
    assert (
        rejected.json()["detail"]["code"]
        == "research_continuation_not_available"
    )


@pytest.mark.asyncio
async def test_research_continuation_rejects_untrusted_or_conflicting_requests(client):
    project = _project(["funding", "risk"])
    started = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-research-continuation-guards",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "funding",
                        "statement": "Funding is supported.",
                        "evidenceId": "guard-funding",
                        "dimension": "funding",
                        "content": "Funding evidence.",
                    }
                ]
            },
        },
    )
    assert started.status_code == 202
    entry = (
        await client.get(
            "/api/v1/workflows/runs/run-research-continuation-guards/research-ledger"
        )
    ).json()["data"]["entries"][-1]
    base = {
        "expectedRevisionId": entry["revisionId"],
        "proposalId": entry["proposal"]["proposalId"],
        "idempotencyKey": "guard-request",
    }

    stale = await client.post(
        "/api/v1/workflows/runs/run-research-continuation-guards/research-continuations",
        json={
            **base,
            "expectedRevisionId": "stale-revision",
            "sourceOutputs": {"fixture-source": [{"evidenceId": "stale"}]},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_research_revision"

    unknown_source = await client.post(
        "/api/v1/workflows/runs/run-research-continuation-guards/research-continuations",
        json={
            **base,
            "sourceOutputs": {"arbitrary-web": [{"evidenceId": "unknown-source"}]},
        },
    )
    assert unknown_source.status_code == 409
    assert unknown_source.json()["detail"]["code"] == "unknown_research_source"

    oversized = await client.post(
        "/api/v1/workflows/runs/run-research-continuation-guards/research-continuations",
        json={
            **base,
            "sourceOutputs": {
                "fixture-source": [
                    {"evidenceId": f"oversized-{index}"} for index in range(201)
                ]
            },
        },
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "research_continuation_too_large"

    accepted_body = {
        **base,
        "sourceOutputs": {
            "fixture-source": [
                {
                    "claimKey": "risk",
                    "statement": "Risk is covered.",
                    "evidenceId": "guard-risk",
                    "dimension": "risk",
                    "content": "Risk evidence.",
                }
            ]
        },
    }
    accepted = await client.post(
        "/api/v1/workflows/runs/run-research-continuation-guards/research-continuations",
        json=accepted_body,
    )
    assert accepted.status_code == 202

    conflict = await client.post(
        "/api/v1/workflows/runs/run-research-continuation-guards/research-continuations",
        json={
            **accepted_body,
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "risk",
                        "statement": "Risk is covered differently.",
                        "evidenceId": "guard-risk-conflict",
                        "dimension": "risk",
                        "content": "Different risk evidence.",
                    }
                ]
            },
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "research_idempotency_conflict"


@pytest.mark.asyncio
async def test_research_continuation_budget_advances_across_three_immutable_runs(client):
    project = _project(["funding", "risk"])
    coverage = next(node for node in project["nodes"] if node["id"] == "coverage")
    coverage["params"]["config"].update(
        {"maxIterations": 3, "maxAdditionalCollections": 2}
    )
    started = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-research-root-three",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "funding",
                        "statement": "Funding is supported.",
                        "evidenceId": "funding-1",
                        "dimension": "funding",
                        "content": "Funding evidence.",
                    }
                ]
            },
        },
    )
    assert started.status_code == 202

    root_entry = (
        await client.get(
            "/api/v1/workflows/runs/run-research-root-three/research-ledger"
        )
    ).json()["data"]["entries"][-1]
    first = await client.post(
        "/api/v1/workflows/runs/run-research-root-three/research-continuations",
        json={
            "expectedRevisionId": root_entry["revisionId"],
            "proposalId": root_entry["proposal"]["proposalId"],
            "idempotencyKey": "round-two",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "macro",
                        "statement": "Macro context is available.",
                        "evidenceId": "macro-1",
                        "dimension": "macro",
                        "content": "Additional macro evidence.",
                    }
                ]
            },
        },
    )
    assert first.status_code == 202, first.text
    first_data = first.json()["data"]
    assert first_data["iteration"] == 2
    assert first_data["researchStatus"] == "needs_evidence"

    second_entry = (
        await client.get(
            f"/api/v1/workflows/runs/{first_data['childRunId']}/research-ledger"
        )
    ).json()["data"]["entries"][-1]
    second = await client.post(
        f"/api/v1/workflows/runs/{first_data['childRunId']}/research-continuations",
        json={
            "expectedRevisionId": second_entry["revisionId"],
            "proposalId": second_entry["proposal"]["proposalId"],
            "idempotencyKey": "round-three",
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "risk",
                        "statement": "Risk is covered.",
                        "evidenceId": "risk-1",
                        "dimension": "risk",
                        "content": "Risk evidence.",
                    }
                ]
            },
        },
    )
    assert second.status_code == 202, second.text
    assert second.json()["data"]["iteration"] == 3
    assert second.json()["data"]["additionalCollectionCount"] == 2
    assert second.json()["data"]["researchStatus"] == "final"


@pytest.mark.asyncio
async def test_capability_catalog_discovers_research_pack_without_frontend_hardcoding(client):
    response = await client.get("/api/v1/workflows/capabilities")

    assert response.status_code == 200
    catalog = {item["id"]: item for item in response.json()["data"]["catalog"]}
    operator_ids = {
        operator["operatorId"]
        for row in catalog.values()
        for operator in row.get("manifest", {}).get("operators", [])
    }
    assert {
        "research.claim-project",
        "research.coverage-audit",
        "research.counter-thesis",
        "research.scenario-simulate",
        "research.revision-diff",
        "research.publish-gate",
    } <= operator_ids


@pytest.mark.asyncio
async def test_demand_draft_adds_only_explicit_bounded_research_chain(client, db_session):
    base_project = {
        "id": "wf-demand-research",
        "name": "Demand Research",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "existing-fixture-source",
                "kind": "source",
                "capability": "fetch",
                "adapter": "existing-fixture-adapter",
                "params": {"fixtureItems": [{"content": "Existing input"}]},
            }
        ],
        "edges": [],
        "adapters": [
            {
                "id": "existing-fixture-adapter",
                "type": "source",
                "provider": "fixture",
                "mode": "fixture",
                "config": {},
            }
        ],
        "agentPermissions": {"canWriteInbox": True},
    }
    response = await client.post(
        "/api/v1/workflows/demand-draft",
        json={
            "project": base_project,
            "text": "抓小红书热帖 深度调研 维度: 资金,风险",
            "locale": "zh-CN",
        },
    )
    assert response.status_code == 200, response.text
    draft = response.json()["data"]
    assert draft["valid"] is True, draft
    research_nodes = [
        node
        for node in draft["project"]["nodes"]
        if str(node.get("params", {}).get("operatorId", "")).startswith("research.")
    ]
    assert [node["params"]["operatorId"] for node in research_nodes] == [
        "research.claim-project",
        "research.coverage-audit",
        "research.counter-thesis",
        "research.scenario-simulate",
        "research.revision-diff",
        "research.publish-gate",
    ]
    assert research_nodes[1]["params"]["config"] == {
        "requiredDimensions": ["资金", "风险"],
        "iteration": 1,
        "maxIterations": 2,
        "additionalCollectionCount": 0,
        "maxAdditionalCollections": 1,
    }
    project = draft["project"]
    source = next(
        node
        for node in project["nodes"]
        if node["id"] == "source-xiaohongshu"
    )
    source["params"]["fixtureItems"] = [
        {
            "claimKey": "liquidity",
            "statement": "Liquidity supports the demand-drafted market.",
            "evidenceId": "demand-funding",
            "stance": "support",
            "dimension": "资金",
            "content": "Funding evidence is long enough for the quality chain.",
        },
        {
            "claimKey": "liquidity",
            "statement": "Liquidity supports the demand-drafted market.",
            "evidenceId": "demand-risk",
            "stance": "qualify",
            "dimension": "风险",
            "content": "Risk evidence is also long enough for the quality chain.",
        },
    ]
    run = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": project,
            "runId": "run-demand-research",
            "traceId": "trace-demand-research",
        },
    )
    assert run.status_code == 202
    assert run.json()["data"]["status"] == "completed"

    batches = (
        await client.get(
            "/api/v1/workflows/runs/run-demand-research/evidence-batches"
        )
    ).json()["data"]["batches"]
    normalize_batch = next(
        batch for batch in batches if batch["nodeId"] == "normalize-xiaohongshu"
    )
    records = (await db_session.execute(select(CollectedRecord))).scalars().all()
    claim_record = next(
        record
        for record in records
        if record.normalized_data.get("researchType") == "claim"
    )
    references = claim_record.normalized_data["claim"]["evidenceRefs"]
    assert {reference["nodeId"] for reference in references} == {
        "normalize-xiaohongshu"
    }
    assert {reference["batchId"] for reference in references} == {
        normalize_batch["batchId"]
    }

    plain = await client.post(
        "/api/v1/workflows/demand-draft",
        json={"project": base_project, "text": "抓小红书 分析", "locale": "zh-CN"},
    )
    assert plain.status_code == 200
    assert not [
        node
        for node in plain.json()["data"]["project"]["nodes"]
        if str(node.get("params", {}).get("operatorId", "")).startswith("research.")
    ]
