"""Regression checks for trust boundaries introduced by branch integration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.main import app
from backend.api.v1 import geo_acquisition
from backend.models.identity import WorkspaceRole
from backend.schemas.research_graph import WorkflowResearchGraphMutationRequest
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import WorkspaceAccess
from tests.integration.iii_collection_test_support import create_scoped_run


def mutation():
    return dict(schemaVersion=1, idempotencyKey="mutation", expectedRevision=None,
                expectedSequence=0, action="propose", traceId="trace", nodeId="node",
                entity={"id": "source", "kind": "source"})


@pytest.mark.parametrize("field,value", [
    ("traceId", "x" * 256), ("nodeId", "x" * 256),
    ("lineage", {str(i): "value" for i in range(65)}),
    ("entity", {"id": "source", "kind": "source", "sourceIds": [str(i) for i in range(501)]}),
    ("entity", {"id": "source", "kind": "source", "attributes": {"value": "x" * 65537}}),
    ("entity", {"id": "source", "kind": "source", "attributes": {"value": [[[[[[[[[1]]]]]]]]]}}),
])
def test_mutation_payload_is_bounded(field, value):
    with pytest.raises(ValidationError):
        WorkflowResearchGraphMutationRequest.model_validate({**mutation(), field: value})


@pytest.mark.asyncio
async def test_scoped_graph_requires_identity_and_workspace_membership(client, db_session):
    scope = await create_scoped_run(db_session)
    app.dependency_overrides.pop(get_request_identity, None)
    route = (f"/api/v1/workspaces/{scope['workspace'].id}/projects/{scope['project'].id}"
             f"/workflows/{scope['workflow'].id}/runs/{scope['run'].id}/research-graph")
    assert (await client.get(route)).status_code == 401
    assert (await client.post(route + "/mutations", json=mutation())).status_code == 401
    app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(subject="foreign")
    assert (await client.get(route)).status_code == 403
    assert (await client.post(route + "/mutations", json=mutation())).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["propose", "verify", "reject", "retract"])
async def test_graph_viewer_cannot_mutate(client, db_session, monkeypatch, action):
    from backend.api.v1 import research_graph_routes
    app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(subject="viewer")
    monkeypatch.setattr(research_graph_routes, "get_workspace_access",
                        AsyncMock(return_value=WorkspaceAccess(user_id="viewer", role=WorkspaceRole.VIEWER)))
    body = mutation()
    body["action"] = action
    if action != "propose":
        body.pop("entity")
        body["targetId"] = "source"
    response = await client.post("/api/v1/workspaces/w/projects/p/workflows/f/runs/r/research-graph/mutations", json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix", [("get", ""), ("post", "/cancel")])
@pytest.mark.parametrize("foreign", [False, True])
async def test_correlated_acquisition_reads_and_cancel_require_authority(client, monkeypatch, method, suffix, foreign):
    row = SimpleNamespace(workspace_id="private-workspace")
    monkeypatch.setattr(geo_acquisition.acquisition_service, "get_execution", AsyncMock(return_value=row))
    if foreign:
        monkeypatch.setattr(geo_acquisition, "get_request_identity", AsyncMock(return_value=RequestIdentity(subject="foreign")))
    response = await getattr(client, method)("/api/v1/internal/geo-acquisition/executions/private" + suffix)
    assert response.status_code == (403 if foreign else 401)


@pytest.mark.asyncio
async def test_correlated_submit_rejects_foreign_identity(client, monkeypatch):
    monkeypatch.setattr(geo_acquisition, "get_request_identity", AsyncMock(return_value=RequestIdentity(subject="foreign")))
    submission = dict(request_id="request", idempotency_key="attempt", capability={"id": "official-site.observe", "version": "1"},
                      output_schema_version="1", input={}, workflow_run_correlation={"workspace_id": "foreign", "project_id": "p", "workflow_id": "w", "run_id": "r"})
    response = await client.post("/api/v1/internal/geo-acquisition/executions", json=submission)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_plugin_validators_and_contributors_survive_run_continuation(db_session):
    from backend.schemas.workflow import WorkflowRunStartRequest, WorkflowRunSourceOutputsRequest
    from backend.workflow.opencli_hda_tracer import start_workflow_run, continue_workflow_run_with_source_outputs
    from backend.workflow.workflow_plugins import WorkflowPluginRegistry
    from tests.integration.test_dataflow_operator_pipeline_api import _single_operator_project

    class ProbePlugin:
        key = "probe"
        capabilities = frozenset({"event-contribution", "event-validation"})
        def __init__(self):
            self.contributions = []
            self.validations = []
        def contribute_events(self, context):
            self.contributions.append((context.node_id, context.sequence))
            return []
        def validate_events(self, events, *, run_id, trace_id):
            self.validations.append(len(events))

    probe = ProbePlugin()
    registry = WorkflowPluginRegistry([probe])
    project = _single_operator_project("text.clean", "refine", [{"content": "Hello"}], {"fields": ["content"]})
    result = await start_workflow_run(WorkflowRunStartRequest(project=project), session=db_session, plugins=registry)
    assert result.status == "completed"
    assert any(node == "operator" for node, _ in probe.contributions)
    assert len(probe.validations) > 1  # Includes incremental appends, not only final persistence.
    counts = len(probe.contributions), len(probe.validations)
    await continue_workflow_run_with_source_outputs(result.runId, WorkflowRunSourceOutputsRequest(sourceOutputs={"fixture-source": [{"content": "Updated"}]}), session=db_session, plugins=registry)
    assert len(probe.contributions) > counts[0]
    assert len(probe.validations) > counts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", ["platform-admin", {"platform-admin": False}, None, 123])
async def test_direct_browser_invoke_rejects_malformed_admin_claim(monkeypatch, roles):
    from backend.api.v1 import browsers
    from backend.schemas.browser import CapabilityInvokeRequest
    monkeypatch.setattr(browsers, "_browser_instance_or_404", AsyncMock(return_value=SimpleNamespace(id="browser")))
    invoke = AsyncMock(side_effect=HTTPException(403, "Capability gate required"))
    monkeypatch.setattr(browsers.browser_capability_service, "invoke_capability", invoke)
    with pytest.raises(HTTPException) as error:
        await browsers.invoke_runtime_capability("browser", "mutate", CapabilityInvokeRequest(args={}),
            identity=RequestIdentity(subject="caller", claims={"roles": roles}), db=AsyncMock())
    assert error.value.status_code == 403
    assert invoke.await_args.kwargs["gate_authorized"] is False
