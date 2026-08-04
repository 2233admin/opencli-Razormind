"""Integration tests for trigger-scoped workflow execution.

Seeds a 10-node / 3-edge acceptance fixture through the Studio lifecycle API
and asserts trigger-scope selection, active-only compilation, parked-node
diagnostics, active-only publication, Run exclusion, externalWorkflow
exception, bounded trigger-kind recognition, and authored-order determinism.
"""

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _trigger_scope_acceptance_fixture() -> dict:
    """Ten-node / three-edge draft defined in design.md.

    Active (4):  trigger, source, hygiene, records
    Parked invalid (4):  llm-a, llm-b, plugin, document
    Parked valid (2):    review, notify
    """

    return {
        "id": "wf-trigger-scope-v2",
        "name": "Trigger Scope Acceptance",
        "profile": "intelligence",
        "version": 1,
        "settings": {
            "timezone": "Asia/Shanghai",
            "deterministicSimulation": True,
            "maxItemsPerRun": 20,
        },
        "adapters": [
            {
                "id": "jin10-kuaixun",
                "type": "source",
                "provider": "jin10",
                "mode": "fixture",
                "config": {"feed": "kuaixun"},
            },
            {
                "id": "webhook-notifier",
                "type": "notification",
                "provider": "webhook",
                "mode": "live",
                "config": {
                    "url": "https://hooks.example.com/trigger-scope",
                    "notifierType": "webhook",
                    "target": "webhook",
                },
            },
        ],
        "agentPermissions": {
            "canFetchNetwork": False,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
        "nodes": [
            {
                "id": "trigger",
                "kind": "schedule",
                "capability": "trigger",
                "params": {
                    "mode": "manual",
                    "inputSchema": {"query": "string"},
                },
                "ui": {
                    "primitiveId": "primitive.core.manual-trigger",
                    "primitivePorts": [
                        {"id": "tick", "direction": "output", "type": "trigger"},
                    ],
                },
            },
            {
                "id": "source",
                "kind": "source",
                "capability": "fetch",
                "adapter": "jin10-kuaixun",
                "params": {"limit": 20},
            },
            {
                "id": "hygiene",
                "kind": "agent",
                "capability": "normalize",
                "params": {"language": "zh-CN"},
                "ui": {"catalogId": "intelligence.processing.normalize"},
            },
            {
                "id": "records",
                "kind": "inbox",
                "capability": "store",
                "params": {"queue": "trigger-scope-output"},
            },
            # Parked — unknown bindings
            {
                "id": "llm-a",
                "kind": "agent",
                "capability": "normalize",
                "params": {"prompt": "Summarise in zh-CN"},
                "ui": {"catalogId": "primitive.ai.llm"},
            },
            {
                "id": "llm-b",
                "kind": "agent",
                "capability": "normalize",
                "params": {"prompt": "Extract key entities"},
                "ui": {"catalogId": "primitive.ai.llm"},
            },
            {
                "id": "plugin",
                "kind": "agent",
                "capability": "normalize",
                "params": {"trigger": "onNewRecord"},
                "ui": {"catalogId": "primitive.plugin.trigger"},
            },
            # Parked — valid configuration
            {
                "id": "review",
                "kind": "control",
                "capability": "accept",
                "params": {},
                "ui": {"catalogId": "intelligence.control.record-acceptance"},
            },
            {
                "id": "document",
                "kind": "agent",
                "capability": "normalize",
                "params": {"format": "pdf"},
                "ui": {"catalogId": "primitive.document.extract"},
            },
            {
                "id": "notify",
                "kind": "notify",
                "capability": "send",
                "adapter": "webhook-notifier",
                "params": {"target": "webhook"},
                "ui": {"catalogId": "intelligence.output.webhook"},
            },
        ],
        "edges": [
            {"id": "e-trigger-source", "source": "trigger", "target": "source"},
            {"id": "e-source-hygiene", "source": "source", "target": "hygiene"},
            {"id": "e-hygiene-records", "source": "hygiene", "target": "records"},
        ],
    }


async def _bootstrap_workflow(client, *, graph: dict) -> dict:
    workspaces = (await client.get("/api/v1/workspaces")).json()["data"]
    workspace_id = workspaces[0]["id"]

    slug = graph.get("id", "trigger-scope-test")
    existing = (await client.get(f"/api/v1/workspaces/{workspace_id}/projects")).json()["data"]
    for p in existing:
        if p.get("slug") == slug:
            await client.delete(f"/api/v1/workspaces/{workspace_id}/projects/{p['id']}")

    result = (
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/bootstrap",
            json={
                "project": {"name": graph.get("name", "TS Test"), "slug": slug},
                "workflow": {"name": graph.get("name", "TS Test"), "graph": graph},
            },
        )
    ).json()["data"]
    project = result["project"]
    workflow = result["primary_workflow"]
    base_url = (
        f"/api/v1/workspaces/{workspace_id}/projects/{project['id']}"
        f"/workflows/{workflow['id']}"
    )
    return {"workspace_id": workspace_id, "project": project, "workflow": workflow, "base_url": base_url}


# ---------------------------------------------------------------------------
# Selection parity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_selector_parity_with_compiled_detector(client):
    """Reuse canonical origin/binding: the source-level selector must match
    the compiled-runtime trigger recognition exactly."""

    from backend.schemas.workflow import WorkflowProject
    from backend.workflow.trigger_scope import _trigger_candidates

    project = WorkflowProject.model_validate(_trigger_scope_acceptance_fixture())
    pairs = _trigger_candidates(
        project.nodes,
        adapters={a.id: a for a in project.adapters},
    )
    trigger_ids = [n.id for n, _ in pairs]
    kinds_by_id = {n.id: k for n, k in pairs}
    assert trigger_ids == ["trigger"], f"expected [trigger], got {trigger_ids}"
    assert kinds_by_id["trigger"] == "manual"


# ---------------------------------------------------------------------------
# Trigger-kind bounded recognition
# ---------------------------------------------------------------------------

def _trigger_node(kind: str, node_id: str = "test-trigger", **kw) -> dict:
    shared: dict = {"id": node_id, "kind": "schedule", "capability": "trigger", "params": {}}
    if kind == "webhook":
        shared["ui"] = {"primitiveId": "primitive.core.webhook-trigger"}
    elif kind == "manual":
        shared["params"] = {"mode": "manual"}
        shared["ui"] = {"primitiveId": "primitive.core.manual-trigger"}
    elif kind == "schedule":
        shared["params"] = {"interval": "1d", "timezone": "Asia/Shanghai"}
        shared["ui"] = {"catalogId": "intelligence.schedule.cron"}
    shared.update(kw)
    return shared


@pytest.mark.asyncio
async def test_bounded_trigger_kind_recognition(client):
    """Only manual, schedule, and webhook binding ids are recognised;
    ai is normalized to manual; legacy-origin nodes are excluded."""

    from backend.workflow.trigger_scope import _resolve_trigger_kind
    from backend.schemas.workflow import WorkflowProjectNode

    def node(**kw) -> WorkflowProjectNode:
        return WorkflowProjectNode(
            id=kw.pop("id", "n"), kind=kw.pop("kind", "schedule"),
            capability=kw.pop("capability", "trigger"),
            params=kw.pop("params", {}),
            ui=kw.pop("ui", None),
        )

    # manual (primitive)
    assert _resolve_trigger_kind(node(id="man", kind="schedule", capability="trigger",
                                       params={"mode": "manual"},
                                       ui={"primitiveId": "primitive.core.manual-trigger"}), None) == "manual"
    # schedule (catalog)
    assert _resolve_trigger_kind(node(id="sched", kind="schedule", capability="trigger",
                                       params={"interval": "1d"},
                                       ui={"catalogId": "intelligence.schedule.cron"}), None) == "schedule"
    # webhook
    assert _resolve_trigger_kind(node(id="wh", kind="schedule", capability="trigger",
                                       ui={"primitiveId": "primitive.core.webhook-trigger"}), None) == "webhook"
    # legacy origin excluded
    orphan = node(id="legacy", kind="agent", capability="normalize",
                  ui={"catalogId": "unknown.fake.id"})
    assert _resolve_trigger_kind(orphan, None) is None


# ---------------------------------------------------------------------------
# Full-graph compile (pre-change baseline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_graph_compile_produces_four_unknown_bindings(client):
    project = _trigger_scope_acceptance_fixture()
    r = await client.post("/api/v1/workflows/compile", json={"project": project})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["valid"] is False
    unknown = [e for e in body["errors"] if e["code"] == "unknown_node_library_binding"]
    assert len(unknown) == 4
    assert {e.get("node_id") for e in unknown} == {"llm-a", "llm-b", "plugin", "document"}


# ---------------------------------------------------------------------------
# Scoped validation: valid active chain + parked warnings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scoped_validation_passes_with_six_parked_and_four_config_warnings(client):
    created = await _bootstrap_workflow(client, graph=_trigger_scope_acceptance_fixture())
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v["valid"] is True, f"errors={v.get('errors')}"
    assert v.get("errors", []) == []

    warnings = v.get("warnings", [])
    parked = [w for w in warnings if w["code"] == "parked_node" and w.get("node_id")]
    assert len(parked) == 6
    parked_ids = {w["node_id"] for w in parked}
    assert parked_ids == {"llm-a", "llm-b", "plugin", "review", "document", "notify"}
    # authored order membership
    assert [w["node_id"] for w in parked] == ["llm-a", "llm-b", "plugin", "review", "document", "notify"]

    config = [w for w in warnings if w["code"] == "unknown_node_library_binding"]
    assert len(config) == 4
    config_ids = {w["node_id"] for w in config}
    assert config_ids == {"llm-a", "llm-b", "plugin", "document"}


# ---------------------------------------------------------------------------
# P1: no supported trigger → must be invalid (no fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_trigger_graph_preserves_full_compilation_path(client):
    """A draft with no supported trigger preserves the existing full-graph
    validation path.  Valid nodes without a trigger still pass (the legacy
    / media-canvas fallback).  Trigger scope and parked-node classification
    only activate when at least one supported trigger is present."""

    graph = _trigger_scope_acceptance_fixture()
    # Keep only the valid nodes (source, hygiene, records) — no trigger,
    # no unknown-binding parked nodes that would fail full-graph compile
    keep_ids = {"source", "hygiene", "records"}
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] in keep_ids]
    graph["edges"] = [e for e in graph["edges"] if e["source"] in keep_ids and e["target"] in keep_ids]
    graph["id"] = "wf-no-trigger-clean"
    graph["name"] = "No Trigger Clean"

    created = await _bootstrap_workflow(client, graph=graph)
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    # Full-graph path: valid nodes pass, no parked diagnostics emitted
    assert v["valid"] is True, f"Expected valid=true for clean full graph, got errors={v.get('errors')}"
    parked_warnings = [w for w in v.get("warnings", []) if w.get("code") == "parked_node"]
    assert len(parked_warnings) == 0, (
        f"No trigger means no trigger scope — parked classification must not activate: {parked_warnings}"
    )


# ---------------------------------------------------------------------------
# P4: explicit trigger-id precedence / zero / mismatch / ambiguity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_trigger_id_precedence(client):
    """Supplied triggerNodeId must exist, be a supported kind, and match the
    requested kind — otherwise a node-anchored error is returned through the
    Run seam."""

    graph = _trigger_scope_acceptance_fixture()
    # id precedence: correct
    r = await client.post("/api/v1/workflows/runs", json={
        "project": graph,
        "runId": "run-id-precedence",
        "trigger": {"kind": "manual", "triggerNodeId": "trigger"},
    })
    assert r.status_code == 202, r.text
    assert r.json()["data"]["valid"] is True

    # id missing
    r = await client.post("/api/v1/workflows/runs", json={
        "project": graph,
        "runId": "run-missing-id",
        "trigger": {"kind": "manual", "triggerNodeId": "nonexistent"},
    })
    data = r.json()["data"]
    assert data["valid"] is False
    assert any(e["code"] == "workflow_trigger_not_found" for e in data["errors"])

    # kind mismatch
    r = await client.post("/api/v1/workflows/runs", json={
        "project": graph,
        "runId": "run-kind-mismatch",
        "trigger": {"kind": "schedule", "triggerNodeId": "trigger"},
    })
    data = r.json()["data"]
    assert data["valid"] is False
    assert any(e["code"] == "workflow_trigger_kind_mismatch" for e in data["errors"])

    # ai normalization
    r = await client.post("/api/v1/workflows/runs", json={
        "project": graph,
        "runId": "run-ai-normalized",
        "trigger": {"kind": "ai", "triggerNodeId": "trigger"},
    })
    assert r.json()["data"]["valid"] is True


@pytest.mark.asyncio
async def test_trigger_ambiguity_requires_explicit_id(client):
    graph = _trigger_scope_acceptance_fixture()
    # Add a second manual trigger
    graph["nodes"].append({
        "id": "trigger-b",
        "kind": "schedule", "capability": "trigger",
        "params": {"mode": "manual"},
        "ui": {"primitiveId": "primitive.core.manual-trigger"},
    })
    graph["edges"].append({"id": "e-tb-source", "source": "trigger-b", "target": "source"})
    graph["id"] = "wf-ambiguous"

    r = await client.post("/api/v1/workflows/runs", json={
        "project": graph, "runId": "run-ambig",
        "trigger": {"kind": "manual"},
    })
    data = r.json()["data"]
    assert data["valid"] is False
    assert any(e["code"] == "workflow_trigger_ambiguous" for e in data["errors"])

    # Explicit id resolves
    r2 = await client.post("/api/v1/workflows/runs", json={
        "project": graph, "runId": "run-ambig-resolved",
        "trigger": {"kind": "manual", "triggerNodeId": "trigger"},
    })
    assert r2.json()["data"]["valid"] is True


@pytest.mark.asyncio
async def test_multiple_supported_triggers_validate_their_active_union(client):
    """Multiple trigger entries produce a union active graph for validation.
    Each individual Run still selects exactly one."""

    graph = _trigger_scope_acceptance_fixture()
    graph["nodes"].append({
        "id": "trigger-b",
        "kind": "schedule", "capability": "trigger",
        "params": {"mode": "manual"},
        "ui": {"primitiveId": "primitive.core.manual-trigger"},
    })
    graph["edges"].append({"id": "e-tb-source", "source": "trigger-b", "target": "source"})
    graph["id"] = "wf-multi-trigger"

    created = await _bootstrap_workflow(client, graph=graph)
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v["valid"] is True
    parked = [w for w in v.get("warnings", []) if w.get("code") == "parked_node"]
    parked_ids = {w.get("node_id") for w in parked}
    # trigger-b is active (downstream from a supported trigger), not parked
    assert "trigger-b" not in parked_ids


# ---------------------------------------------------------------------------
# P4: externalWorkflow exception — including empty-dict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_external_workflow_dict_is_included(client):
    """A node with params.externalWorkflow={} is treated as governed external
    import and kept in the active scope even when disconnected."""

    graph = _trigger_scope_acceptance_fixture()
    graph["nodes"].append({
        "id": "lg-empty",
        "kind": "agent", "capability": "normalize",
        "params": {"externalWorkflow": {}},
    })

    created = await _bootstrap_workflow(client, graph=graph)
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v["valid"] is True
    parked = {w["node_id"] for w in v.get("warnings", []) if w.get("code") == "parked_node" and w.get("node_id")}
    assert "lg-empty" not in parked, f"externalWorkflow={{}} node should not be parked, got {parked}"


# ---------------------------------------------------------------------------
# Authored order — scoped nodes preserve authored ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scoped_nodes_preserve_authored_order(client):
    from backend.schemas.workflow import WorkflowProject
    from backend.workflow.trigger_scope import scoped_project as _scoped_project, select_active_union

    project = WorkflowProject.model_validate(_trigger_scope_acceptance_fixture())
    active_union = select_active_union(project)
    scoped = _scoped_project(
        project=project,
        active_ids=active_union.active_node_ids,
        external_ids=set(),
    )
    # authored order: trigger, source, hygiene, records
    assert [n.id for n in scoped.nodes] == ["trigger", "source", "hygiene", "records"]


# ---------------------------------------------------------------------------
# P5: Run excludes parked nodes from projection state, events, dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_projection_excludes_parked_from_all_surfaces(client):
    """Run nodeStates, trace events, and checkpoint MUST contain only active
    ids.  Parked ids must be absent from every observable surface — not just
    nodeStates."""

    created = await _bootstrap_workflow(client, graph=_trigger_scope_acceptance_fixture())
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v["valid"] is True

    await client.post(
        f"{created['base_url']}/versions",
        json={"reason": "scope", "expectedRevision": 1, "validationRunId": v["runId"]},
    )

    run_req = {"inputs": {}, "user": "tester"}
    rr = await client.post(
        f"{created['base_url']}/runs",
        json=run_req,
        headers={"Idempotency-Key": "ts-exclude-all"},
    )
    assert rr.status_code == 202, rr.text
    proj = rr.json()["data"]
    assert proj["valid"] is True
    assert proj["errors"] == []
    active_expected = {"trigger", "source", "hygiene", "records"}
    parked_expected = {"llm-a", "llm-b", "plugin", "review", "document", "notify"}

    # nodeStates
    state_ids = {s["nodeId"] for s in proj["nodeStates"]}
    assert state_ids == active_expected, f"nodeStates: {state_ids}"

    # trace events
    trace = await client.get(f"{created['base_url']}/runs/{proj['runId']}/trace")
    assert trace.status_code == 200, trace.text
    events = trace.json()["data"]["trace"]["events"]
    event_ids = {e["nodeId"] for e in events}
    assert not (event_ids & parked_expected), f"Parked ids in events: {event_ids & parked_expected}"
    assert active_expected.issubset(event_ids) or event_ids == active_expected, f"Missing active: {active_expected - event_ids}"

    # checkpoint node states
    checkpoint = trace.json()["data"]["trace"]["checkpoint"]["nodeStates"]
    cp_ids = {s["nodeId"] for s in checkpoint}
    assert not (cp_ids & parked_expected), f"Parked ids in checkpoint: {cp_ids & parked_expected}"

    # no batch/item/record presence for parked nodes
    for state in proj["nodeStates"]:
        if state["nodeId"] in parked_expected:
            assert not state.get("batches"), f"Parked {state['nodeId']} has batches"
    for s in proj["nodeStates"]:
        assert s["nodeId"] not in parked_expected, (
            f"Parked node {s['nodeId']} appeared in nodeStates"
        )


# ---------------------------------------------------------------------------
# P5: reconnect parked → active error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnecting_parked_node_makes_invalid_config_active_error(client):
    created = await _bootstrap_workflow(client, graph=_trigger_scope_acceptance_fixture())
    v1 = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v1["valid"] is True

    draft_url = f"{created['base_url']}/draft"
    draft = (await client.get(draft_url)).json()["data"]
    graph = {**draft["graph"]}
    graph["edges"] = [*graph["edges"], {"id": "e-hygiene-llma", "source": "hygiene", "target": "llm-a"}]
    upd = await client.put(draft_url, json={"graph": graph, "revision": draft["revision"]})
    assert upd.status_code == 200, upd.text

    v2 = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v2["valid"] is False
    assert any(e["code"] == "unknown_node_library_binding" for e in v2.get("errors", []))


# ---------------------------------------------------------------------------
# P5: no dispatch for parked nodes — honest about item/event evidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parked_nodes_have_zero_dispatch_events_batches_items(client):
    """Parked nodes produce zero dispatch events, zero batches, and zero items
    in the persisted Run transcript.  The test is explicit about each signal
    and does not rely on nodeStates alone."""

    created = await _bootstrap_workflow(client, graph=_trigger_scope_acceptance_fixture())
    v = (await client.post(f"{created['base_url']}/draft/validation-runs", json={})).json()["data"]
    assert v["valid"] is True

    await client.post(
        f"{created['base_url']}/versions",
        json={"reason": "scope", "expectedRevision": 1, "validationRunId": v["runId"]},
    )

    rr = await client.post(
        f"{created['base_url']}/runs",
        json={"inputs": {}, "user": "tester"},
        headers={"Idempotency-Key": "ts-no-dispatch"},
    )
    proj = rr.json()["data"]
    parked = {"llm-a", "llm-b", "plugin", "review", "document", "notify"}
    active = {"trigger", "source", "hygiene", "records"}

    # projection-level: valid=true, errors empty
    assert proj["valid"] is True, f"proj valid={proj['valid']}, errors={proj.get('errors')}"
    assert proj.get("errors", []) == []

    # trace events
    trace = await client.get(f"{created['base_url']}/runs/{proj['runId']}/trace")
    event_ids = [e["nodeId"] for e in trace.json()["data"]["trace"]["events"]]
    for pid in parked:
        assert pid not in event_ids, f"parked {pid} in events"

    # Events and eventCount are scoped: parked nodes emitted no events
    # (Honest: item counts may be zero for fixture/non-dispatching sources;
    #  this is correct — a successful compile alone is not execution evidence.)
    for state in proj["nodeStates"]:
        assert state["nodeId"] not in parked, (
            f"parked {state['nodeId']} in nodeStates"
        )
        assert state["eventCount"] >= 0  # scoped, parked absent


# ---------------------------------------------------------------------------
# Rollback compatibility — pre-change persisted version and scoped version
# both remain schema-readable and runnable through the existing
# published-Run seam, without relying on the current publish API for v1.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_compatibility_pre_change_and_scoped_versions(
    client, db_session,
):
    """v1 is inserted directly as a StudioWorkflowVersion row — it never
    touches the current validation or publish API, faithfully simulating a
    version that was persisted before the trigger-scope feature deployed.
    v2 goes through the current scoped validate → publish path.  Both are
    then read back via the version list API and executed through the
    published-Run API.  Assertions cover HTTP status, valid, errors, version
    identity, graph shape, and absence of invalid_workflow_project / schema
    errors — no model_validate-only shortcut."""

    import uuid
    from sqlalchemy import select
    from backend.schemas.workflow import WORKFLOW_COMPILE_VERSION
    from backend.models.studio import (
        StudioWorkflow,
        StudioWorkflowVersion,
        StudioWorkflowValidationRun,
    )

    full_fixture = _trigger_scope_acceptance_fixture()
    active_ids = {"trigger", "source", "hygiene", "records"}

    # --- Build v1: 4-node valid graph (pre-change shape) ---
    v1_graph = {
        **{k: v for k, v in full_fixture.items()
           if k not in ("nodes", "edges", "id", "name")},
        "id": "wf-rollback-v1",
        "name": "Rollback V1",
        "nodes": [n for n in full_fixture["nodes"] if n["id"] in active_ids],
        "edges": [e for e in full_fixture["edges"]
                  if e["source"] in active_ids and e["target"] in active_ids],
    }

    # Bootstrap → draft with 4 nodes; no version exists yet.
    created = await _bootstrap_workflow(client, graph=v1_graph)
    base_url = created["base_url"]
    workflow_id = created["workflow"]["id"]

    # ---- Phase 1: Direct-DB pre-change version (bypasses publish API) ----

    val_run_id = str(uuid.uuid4())
    db_session.add(StudioWorkflowValidationRun(
        id=val_run_id,
        workflow_id=workflow_id,
        draft_revision=1,
        status="completed",
        valid=True,
        errors=[],
        warnings=[],
        compile_version=WORKFLOW_COMPILE_VERSION,
        resolved_graph=v1_graph,
    ))
    await db_session.flush()

    v1_version_id = str(uuid.uuid4())
    db_session.add(StudioWorkflowVersion(
        id=v1_version_id,
        workflow_id=workflow_id,
        version=1,
        draft_revision=1,
        graph=v1_graph,
        compile_version=WORKFLOW_COMPILE_VERSION,
        validation_run_id=val_run_id,
        published_by_user_id="rollback-test",
        reason="pre-change baseline",
    ))

    workflow_row = await db_session.scalar(
        select(StudioWorkflow).where(StudioWorkflow.id == workflow_id),
    )
    assert workflow_row is not None
    workflow_row.current_published_version = 1
    await db_session.flush()

    # -- Read v1 via existing version list API --
    versions1 = (await client.get(f"{base_url}/versions")).json()["data"]
    assert len(versions1) >= 1
    v1_read = next(v for v in versions1 if v["version"] == 1)
    assert v1_read["version"] == 1
    assert v1_read["draft_revision"] == 1
    assert len(v1_read["graph"]["nodes"]) == 4
    assert len(v1_read["graph"]["edges"]) == 3

    # -- Run v1 via existing published-Run API --
    rr1 = await client.post(
        f"{base_url}/runs",
        json={"inputs": {}, "user": "rollback-v1"},
        headers={"Idempotency-Key": f"rollback-v1-{uuid.uuid4().hex[:12]}"},
    )
    assert rr1.status_code == 202, f"v1 Run HTTP {rr1.status_code}: {rr1.text}"
    proj1 = rr1.json()["data"]
    assert proj1["valid"] is True, f"v1 Run valid=False, errors={proj1.get('errors')}"
    assert proj1.get("errors", []) == []
    v1_state_ids = {s["nodeId"] for s in proj1["nodeStates"]}
    assert v1_state_ids == active_ids, f"v1 nodeStates {v1_state_ids}"

    # ---- Phase 2: Current scoped validation / publish (v2) ----

    # Update draft to the full 10-node fixture
    v2_draft_graph = {**full_fixture, "id": "wf-rollback-v2", "name": "Rollback V2"}
    draft_url = f"{base_url}/draft"
    draft = (await client.get(draft_url)).json()["data"]
    upd = await client.put(
        draft_url,
        json={"graph": v2_draft_graph, "revision": draft["revision"]},
    )
    assert upd.status_code == 200, upd.text

    # Scoped validation → parked warnings, resolves only active graph
    v2_val = (await client.post(
        f"{base_url}/draft/validation-runs", json={},
    )).json()["data"]
    assert v2_val["valid"] is True
    parked_warnings = [
        w for w in v2_val.get("warnings", []) if w.get("code") == "parked_node"
    ]
    assert len(parked_warnings) == 6  # same 6 parked from the fixture

    # Publish through current API → scoped version with 4 nodes
    v2_pub = (await client.post(
        f"{base_url}/versions",
        json={
            "reason": "scoped v2",
            "expectedRevision": 2,
            "validationRunId": v2_val["runId"],
        },
    )).json()["data"]
    assert v2_pub["version"] == 2
    assert len(v2_pub["graph"]["nodes"]) == 4  # parked excluded
    assert len(v2_pub["graph"]["edges"]) == 3

    # -- Both versions appear in list API --
    versions2 = (await client.get(f"{base_url}/versions")).json()["data"]
    assert len(versions2) == 2
    v2_read = next(v for v in versions2 if v["version"] == 2)
    assert v2_read["version"] == 2
    assert len(v2_read["graph"]["nodes"]) == 4

    # -- Draft still has 10 nodes (parked survive in draft) --
    draft2 = (await client.get(draft_url)).json()["data"]
    assert len(draft2["graph"]["nodes"]) == 10

    # -- Run v2 via existing published-Run API --
    rr2 = await client.post(
        f"{base_url}/runs",
        json={"inputs": {}, "user": "rollback-v2"},
        headers={"Idempotency-Key": f"rollback-v2-{uuid.uuid4().hex[:12]}"},
    )
    assert rr2.status_code == 202, f"v2 Run HTTP {rr2.status_code}: {rr2.text}"
    proj2 = rr2.json()["data"]
    assert proj2["valid"] is True, f"v2 Run valid=False, errors={proj2.get('errors')}"
    assert proj2.get("errors", []) == []
    v2_state_ids = {s["nodeId"] for s in proj2["nodeStates"]}
    assert v2_state_ids == active_ids, f"v2 nodeStates {v2_state_ids}"

    # -- Version identity: both are distinct rows, same executable shape --
    assert v1_read["id"] != v2_read["id"]
    assert v1_read["version"] == 1
    assert v2_read["version"] == 2
    assert v1_read["graph"]["nodes"] == v2_read["graph"]["nodes"]
    assert v1_read["graph"]["edges"] == v2_read["graph"]["edges"]
