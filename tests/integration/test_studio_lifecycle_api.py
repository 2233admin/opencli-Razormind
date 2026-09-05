import asyncio
import json

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.v1 import studio_workflows
from backend.config import get_settings
from backend.database import Base, get_db
from backend.main import app
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowVersion,
    StudioWorkspace,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas import workflow as workflow_schemas
from backend.services import image_studio_service
from tests.fixtures.workflow_conformance import workflow_conformance_project


async def _create_studio_workflow(client, *, graph: dict | None = None) -> dict:
    workspace_id = (await client.get("/api/v1/workspaces")).json()["data"][0]["id"]
    result = (
        await client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/bootstrap",
            json={
                "project": {
                    "name": "Lifecycle project",
                    "slug": "lifecycle-project",
                },
                "workflow": {
                    "name": "Lifecycle workflow",
                    "graph": graph or workflow_conformance_project(),
                },
            },
        )
    ).json()["data"]
    project = result["project"]
    workflow = result["primary_workflow"]
    base_url = (
        f"/api/v1/workspaces/{workspace_id}/projects/{project['id']}/workflows/{workflow['id']}"
    )
    return {"workflow": workflow, "base_url": base_url}


async def _publish_studio_workflow(client, created: dict) -> dict:
    validation = (
        await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    ).json()["data"]
    response = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Publish for API execution",
            "expectedRevision": 1,
            "validationRunId": validation["runId"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _gaojixing_four_node_project() -> dict:
    shared = {
        "sourceMode": "offline_fixture",
        "fixtureId": "gaojixing-doubao-offline-v1",
    }
    return {
        "id": "wf-gaojixing-published-upload",
        "name": "Gaojixing published upload",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "trigger",
                "kind": "schedule",
                "capability": "trigger",
                "params": {"mode": "manual", "timezone": "Asia/Shanghai"},
                "ui": {"catalogId": "intelligence.schedule.cron"},
            },
            {
                "id": "batch",
                "kind": "agent",
                "capability": "normalize",
                "params": {"template": "gaojixing-doubao-batch", **shared},
                "ui": {"catalogId": "package.gaojixing.doubao-batch"},
            },
            {
                "id": "certify",
                "kind": "agent",
                "capability": "normalize",
                "params": {"template": "gaojixing-batch-certification", **shared},
                "ui": {"catalogId": "package.gaojixing.batch-certification"},
            },
            {
                "id": "delivery",
                "kind": "inbox",
                "capability": "store",
                "params": {"queue": "gaojixing-doubao-certified", "archive": True},
                "ui": {"catalogId": "intelligence.output.inbox"},
            },
        ],
        "edges": [
            {"id": "trigger-batch", "source": "trigger", "target": "batch"},
            {"id": "batch-certify", "source": "batch", "target": "certify"},
            {"id": "certify-delivery", "source": "certify", "target": "delivery"},
        ],
        "adapters": [],
        "agentPermissions": {
            "canFetchNetwork": False,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }


@pytest.mark.asyncio
async def test_studio_legacy_graph_extensions_and_nested_nulls_round_trip(client):
    graph = workflow_conformance_project()
    graph["legacyExtension"] = {
        "schema": "legacy-extension.v0",
        "nullableValue": None,
    }
    graph["nodes"][0]["legacyNodeExtension"] = {
        "owner": "legacy-canvas",
        "nullableValue": None,
    }
    graph["nodes"][0]["sourceAnchor"] = None

    created = await _create_studio_workflow(client, graph=graph)
    draft_url = f"{created['base_url']}/draft"
    first = await client.get(draft_url)

    assert first.status_code == 200, first.text
    first_draft = first.json()["data"]
    first_graph = first_draft["graph"]
    assert first_graph["legacyExtension"] == graph["legacyExtension"]
    assert (
        first_graph["nodes"][0]["legacyNodeExtension"] == (graph["nodes"][0]["legacyNodeExtension"])
    )
    assert "sourceAnchor" not in first_graph["nodes"][0]

    first_graph["legacyExtension"]["revisionNote"] = "round-trip"
    first_graph["nodes"][0]["legacyNodeExtension"]["revision"] = 2
    updated = await client.put(
        draft_url,
        json={"graph": first_graph, "revision": first_draft["revision"]},
    )

    assert updated.status_code == 200, updated.text
    updated_graph = updated.json()["data"]["graph"]
    assert updated_graph["legacyExtension"] == first_graph["legacyExtension"]
    assert (
        updated_graph["nodes"][0]["legacyNodeExtension"]
        == (first_graph["nodes"][0]["legacyNodeExtension"])
    )

    reloaded = await client.get(draft_url)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["data"]["graph"] == updated_graph


@pytest.mark.asyncio
async def test_studio_workflow_draft_validation_run_is_persisted(client):
    created = await _create_studio_workflow(client)
    response = await client.post(
        f"{created['base_url']}/draft/validation-runs",
        json={},
    )

    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["workflowId"] == created["workflow"]["id"]
    assert run["status"] == "completed"
    assert run["valid"] is True
    assert run["draftRevision"] == 1
    assert run["errors"] == []
    assert run["warnings"] == []
    assert run["runId"]


@pytest.mark.asyncio
async def test_studio_validation_rejects_an_isolated_source_node(client):
    graph = workflow_conformance_project()
    graph["nodes"].append(
        {
            "id": "isolated-http-source",
            "kind": "source",
            "capability": "fetch",
            "adapter": "isolated-http-adapter",
            "params": {
                "channelType": "http",
                "endpoint": "https://example.com/data",
                "method": "GET",
            },
            "ui": {"catalogId": "intelligence.source.http"},
        }
    )
    graph["adapters"].append(
        {
            "id": "isolated-http-adapter",
            "type": "source",
            "provider": "http",
            "mode": "live",
            "config": {"channelType": "http"},
        }
    )
    created = await _create_studio_workflow(client, graph=graph)

    response = await client.post(
        f"{created['base_url']}/draft/validation-runs",
        json={},
    )

    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["status"] == "failed"
    assert run["valid"] is False
    assert run["errors"] == [
        {
            "code": "isolated_source_node",
            "message": (
                'Workflow source node "isolated-http-source" is not connected to a downstream node'
            ),
            "node_id": "isolated-http-source",
            "edge_id": None,
            "path": ["nodes", "isolated-http-source"],
        }
    ]


@pytest.mark.asyncio
async def test_studio_workflow_current_validated_revision_can_be_published(client):
    created = await _create_studio_workflow(client)
    validation = (
        await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    ).json()["data"]
    response = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Release validated workflow",
            "expectedRevision": 1,
            "validationRunId": validation["runId"],
        },
    )

    assert response.status_code == 201, response.text
    version = response.json()["data"]
    assert version["workflow_id"] == created["workflow"]["id"]
    assert version["version"] == 1
    assert version["draft_revision"] == 1
    assert version["graph"]["id"] == created["workflow"]["id"]
    assert version["compile_version"] == "1.1.0"
    assert version["published_by_user_id"] == "local-development-user"
    assert version["reason"] == "Release validated workflow"

    listed = await client.get(f"{created['base_url']}/versions")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"] == [version]
    workflows_url = created["base_url"].rsplit("/", 1)[0]
    workflow = (await client.get(workflows_url)).json()["data"][0]
    assert workflow["current_published_version"] == 1


@pytest.mark.asyncio
async def test_studio_api_run_requires_a_published_version(client):
    created = await _create_studio_workflow(client)

    response = await client.post(
        f"{created['base_url']}/runs",
        json={"inputs": {"topic": "OpenCLI"}, "user": "server-worker"},
    )

    assert response.status_code == 409, response.text
    assert "published" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_published_question_bank_run_is_managed_idempotent_and_path_private(
    client, db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    created = await _create_studio_workflow(client, graph=_gaojixing_four_node_project())
    published = await _publish_studio_workflow(client, created)
    question_bank = json.dumps(
        {
            "phase1": [{"id": "G0001", "question": "孕妇 DHA 怎么选？"}],
            "phase2": [],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    async def start(payload: bytes):
        return await client.post(
            f"{created['base_url']}/runs/question-bank",
            data={
                "request": json.dumps({"user": "server-worker", "idempotency_key": "upload-key-1"})
            },
            files={"questionBank": ("questions.json", payload, "application/json")},
        )

    first = await start(question_bank)
    repeated = await start(question_bank)

    assert first.status_code == 202, first.text
    assert repeated.status_code == 202, repeated.text
    assert first.json()["data"]["status"] == "waiting"
    run_id = first.json()["data"]["runId"]
    assert repeated.json()["data"]["runId"] == run_id
    row = await db_session.get(WorkflowRun, run_id)
    assert row is not None
    assert row.studio_workflow_version_id == published["id"]
    trace = await client.get(f"{created['base_url']}/runs/{run_id}/trace")
    assert trace.status_code == 200, trace.text
    assert set(trace.json()["data"]["inputs"]) == {"questionBatchRef"}
    assert trace.json()["data"]["inputs"]["questionBatchRef"].startswith("qbr1.")
    assert str(tmp_path) not in trace.text
    assert "projectRoot" not in trace.text
    assert "questionBankPath" not in trace.text
    batch_waiting = next(
        event
        for event in trace.json()["data"]["trace"]["events"]
        if event["nodeId"] == "batch::tool" and event["eventType"] == "waiting"
    )
    assert "gaojixing.collection-run.v1" in json.dumps(batch_waiting["details"], ensure_ascii=False)

    changed = json.dumps(
        {
            "phase1": [{"id": "G0001", "question": "内容已经变化"}],
            "phase2": [],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    collision = await start(changed)
    assert collision.status_code == 409, collision.text


@pytest.mark.asyncio
async def test_concurrent_published_idempotent_requests_return_the_same_run(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'published-idempotency.db'}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    setup_session = session_factory()
    first_session = session_factory()
    second_session = session_factory()
    sessions = {
        "setup": setup_session,
        "first": first_session,
        "second": second_session,
    }

    async def override_get_db(request: Request):
        yield sessions[request.headers.get("X-Test-Session", "setup")]

    app.dependency_overrides[get_db] = override_get_db
    real_start = studio_workflows.start_workflow_run
    arrival_lock = asyncio.Lock()
    both_arrived = asyncio.Event()
    winner_committed = asyncio.Event()
    arrival_count = 0

    async def race_at_insert(body, **kwargs):
        nonlocal arrival_count
        async with arrival_lock:
            arrival_count += 1
            arrival_number = arrival_count
            if arrival_count == 2:
                both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)
        if arrival_number == 1:
            try:
                projection = await real_start(body, **kwargs)
                await kwargs["session"].commit()
                return projection
            finally:
                winner_committed.set()
        await asyncio.wait_for(winner_committed.wait(), timeout=5)
        raise IntegrityError(
            "INSERT INTO workflow_runs ...",
            {},
            RuntimeError("UNIQUE constraint failed: workflow_runs.id"),
        )

    monkeypatch.setattr(studio_workflows, "start_workflow_run", race_at_insert)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as concurrent_client:
            created = await _create_studio_workflow(concurrent_client)
            await _publish_studio_workflow(concurrent_client, created)
            await setup_session.commit()
            url = f"{created['base_url']}/runs"
            request_body = {
                "inputs": {"batch": {"questions": ["same question"]}},
                "response_mode": "async",
                "user": "server-worker",
            }

            first, second = await asyncio.gather(
                concurrent_client.post(
                    url,
                    json=request_body,
                    headers={
                        "Idempotency-Key": "concurrent-batch",
                        "X-Test-Session": "first",
                    },
                ),
                concurrent_client.post(
                    url,
                    json=request_body,
                    headers={
                        "Idempotency-Key": "concurrent-batch",
                        "X-Test-Session": "second",
                    },
                ),
            )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert first.json()["data"] == second.json()["data"]
    finally:
        app.dependency_overrides.clear()
        await setup_session.close()
        await first_session.close()
        await second_session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_idempotent_question_bank_request_preserves_shared_run_directory(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    created = await _create_studio_workflow(client, graph=_gaojixing_four_node_project())
    await _publish_studio_workflow(client, created)

    async def fail_start(**_kwargs):
        raise RuntimeError("controlled run-start failure")

    monkeypatch.setattr(studio_workflows, "_start_published_version_run", fail_start)
    with pytest.raises(RuntimeError, match="controlled run-start failure"):
        await client.post(
            f"{created['base_url']}/runs/question-bank",
            data={
                "request": json.dumps(
                    {"user": "server-worker", "idempotency_key": "shared-upload-key"}
                )
            },
            files={
                "questionBank": (
                    "questions.json",
                    b'{"phase1":[{"id":"G0001","question":"question"}],"phase2":[]}',
                    "application/json",
                )
            },
        )

    run_directories = [path for path in (tmp_path / "runs").iterdir() if path.is_dir()]
    assert len(run_directories) == 1
    assert (run_directories[0] / "question-bank.json").is_file()


@pytest.mark.asyncio
async def test_failed_unique_question_bank_request_cleans_its_run_directory(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    created = await _create_studio_workflow(client, graph=_gaojixing_four_node_project())
    await _publish_studio_workflow(client, created)

    async def fail_start(**_kwargs):
        raise RuntimeError("controlled run-start failure")

    monkeypatch.setattr(studio_workflows, "_start_published_version_run", fail_start)
    with pytest.raises(RuntimeError, match="controlled run-start failure"):
        await client.post(
            f"{created['base_url']}/runs/question-bank",
            data={"request": json.dumps({"user": "server-worker"})},
            files={
                "questionBank": (
                    "questions.json",
                    b'{"phase1":[{"id":"G0001","question":"question"}],"phase2":[]}',
                    "application/json",
                )
            },
        )

    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.asyncio
async def test_published_question_bank_run_rejects_a_non_gaojixing_workflow(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "gaojixing_run_storage_path", str(tmp_path))
    created = await _create_studio_workflow(client)
    await _publish_studio_workflow(client, created)

    response = await client.post(
        f"{created['base_url']}/runs/question-bank",
        data={"request": json.dumps({"user": "server-worker"})},
        files={
            "questionBank": (
                "questions.json",
                b'{"phase1":[{"id":"G0001","question":"question"}],"phase2":[]}',
                "application/json",
            )
        },
    )

    assert response.status_code == 422, response.text
    assert "Gaojixing" in response.text
    assert not (tmp_path / "runs").exists()


@pytest.mark.asyncio
async def test_studio_api_run_is_version_bound_idempotent_and_visible_in_logs(
    client,
    db_session,
):
    created = await _create_studio_workflow(client)
    published = await _publish_studio_workflow(client, created)
    request = {
        "inputs": {
            "topic": "OpenCLI ecosystem",
            "batch": {
                "questions": ["first question", "second question"],
                "options": {"language": "zh-CN", "evidence": True},
            },
        },
        "response_mode": "async",
        "initiated_by": "server-worker",
    }
    reordered_request = {
        **request,
        "inputs": {
            "batch": {
                "options": {"evidence": True, "language": "zh-CN"},
                "questions": ["first question", "second question"],
            },
            "topic": "OpenCLI ecosystem",
        },
    }
    headers = {
        "Idempotency-Key": "nightly-project-job",
        "X-Request-ID": "request-001",
    }

    first = await client.post(f"{created['base_url']}/runs", json=request, headers=headers)
    replay = await client.post(
        f"{created['base_url']}/runs",
        json=reordered_request,
        headers=headers,
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    projection = first.json()["data"]
    assert replay.json()["data"]["runId"] == projection["runId"]
    row = await db_session.get(WorkflowRun, projection["runId"])
    assert row is not None
    version = await db_session.get(StudioWorkflowVersion, row.studio_workflow_version_id)
    assert version is not None
    assert version.id == published["id"]
    assert row.workflow_version_id is None
    assert row.request["project"] == workflow_schemas.WorkflowProject.model_validate(
        published["graph"]
    ).model_dump(mode="json")
    assert row.request["input"]["payload"] == request["inputs"]
    assert row.request["input"]["source"] == "external"
    assert row.request["input"]["sourceId"] == request["initiated_by"]
    assert row.request["trigger"]["requestId"] == headers["X-Request-ID"]

    project_url = created["base_url"].split("/workflows/", 1)[0]
    summary = await client.get(f"{project_url}/runtime-summary")
    logs = await client.get(
        f"{project_url}/runtime-logs",
        params={"search": projection["runId"], "status": projection["status"]},
    )
    trace = await client.get(f"{created['base_url']}/runs/{projection['runId']}/trace")

    assert summary.status_code == 200, summary.text
    assert summary.json()["data"]["total_runs"] == 1
    assert logs.status_code == 200, logs.text
    assert logs.json()["meta"]["total"] == 1
    log = logs.json()["data"][0]
    assert log["workflow_version"] == published["version"]
    assert log["trace_id"] == projection["traceId"]
    assert log["response_mode"] == request["response_mode"]
    assert trace.status_code == 200, trace.text
    trace_data = trace.json()["data"]
    assert trace_data["workflow_version"] == published["version"]
    assert trace_data["inputs"] == request["inputs"]
    assert trace_data["user"] == request["initiated_by"]
    assert trace_data["trace"]["projection"]["runId"] == projection["runId"]
    paged_trace = await client.get(
        f"{created['base_url']}/runs/{projection['runId']}/trace",
        params={"afterSequence": 0, "limit": 1},
    )
    assert paged_trace.status_code == 200, paged_trace.text
    paged_events = paged_trace.json()["data"]["trace"]["events"]
    assert len(paged_events) == 1
    assert paged_trace.json()["data"]["trace"]["filters"] == {
        "afterSequence": 0,
        "limit": 1,
    }
    assert paged_trace.json()["data"]["trace"]["nextAfterSequence"] == paged_events[0]["sequence"]
    empty_evidence_batches = await client.get(
        f"{created['base_url']}/runs/{projection['runId']}/evidence-batches",
        params={"node_id": "missing-node"},
    )
    assert empty_evidence_batches.status_code == 200, empty_evidence_batches.text
    assert empty_evidence_batches.json()["data"]["batches"] == []
    empty_evidence_projection = await client.get(
        f"{created['base_url']}/runs/{projection['runId']}/projection",
    )
    assert empty_evidence_projection.status_code == 200, empty_evidence_projection.text
    assert empty_evidence_projection.json()["data"]["runId"] == projection["runId"]

    scoped_run_url = f"{created['base_url']}/runs/{projection['runId']}"
    scoped_projection = await client.get(scoped_run_url)
    scoped_events = await client.get(f"{scoped_run_url}/events")
    scoped_batches = await client.get(f"{scoped_run_url}/evidence-batches")
    scoped_evidence = await client.get(f"{scoped_run_url}/projection")
    assert scoped_projection.status_code == 200, scoped_projection.text
    assert scoped_projection.json()["data"]["runId"] == projection["runId"]
    assert scoped_events.status_code == 200, scoped_events.text
    assert scoped_events.json()["data"] == trace_data["trace"]["events"]
    assert scoped_batches.status_code == 200, scoped_batches.text
    assert scoped_evidence.status_code == 200, scoped_evidence.text

    workspace_url = project_url.rsplit("/projects/", 1)[0]
    for wrong_path in (
        scoped_run_url.replace(workspace_url, f"{workspace_url}-other", 1),
        scoped_run_url.replace(
            project_url,
            f"{workspace_url}/projects/not-the-owning-project/workflows/{created['workflow']['id']}",
            1,
        ),
        scoped_run_url.replace(
            created["base_url"],
            f"{project_url}/workflows/not-the-owning-workflow",
            1,
        ),
    ):
        denied = await client.get(wrong_path)
        assert denied.status_code == 404, (wrong_path, denied.text)

    generic_run_url = f"/api/v1/workflows/runs/{projection['runId']}"
    for suffix in (
        "",
        "/evidence-batches",
        "/evidence-batches/hidden-batch",
        "/projection",
        "/research-ledger",
        "/checkpoint",
        "/trace",
        "/events",
    ):
        hidden = await client.get(f"{generic_run_url}{suffix}")
        assert hidden.status_code == 404, (suffix, hidden.text)
    hidden_continuation = await client.post(
        f"{generic_run_url}/research-continuations",
        json={
            "expectedRevisionId": "hidden-revision",
            "proposalId": "hidden-proposal",
            "idempotencyKey": "hidden-continuation",
            "sourceOutputs": {"source": [{"recordId": "hidden-record"}]},
        },
    )
    assert hidden_continuation.status_code == 404, hidden_continuation.text

    deleted = await client.delete(project_url)
    assert deleted.status_code == 200, deleted.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_request", "conflicting_request"),
    [
        pytest.param(
            {
                "inputs": {"batch": {"questions": ["first question"]}},
                "response_mode": "async",
                "user": "server-worker",
            },
            {
                "inputs": {"batch": {"questions": ["different question"]}},
                "response_mode": "async",
                "user": "server-worker",
            },
            id="different-inputs",
        ),
        pytest.param(
            {
                "inputs": {"batch": {"reviewed": True}},
                "response_mode": "async",
                "user": "server-worker",
            },
            {
                "inputs": {"batch": {"reviewed": 1}},
                "response_mode": "async",
                "user": "server-worker",
            },
            id="different-json-types",
        ),
        pytest.param(
            {
                "inputs": {"batch": {"questions": ["same question"]}},
                "response_mode": "async",
                "user": "first-worker",
            },
            {
                "inputs": {"batch": {"questions": ["same question"]}},
                "response_mode": "async",
                "user": "different-worker",
            },
            id="different-user",
        ),
    ],
)
async def test_studio_api_run_rejects_idempotency_key_reused_for_different_work(
    client,
    first_request,
    conflicting_request,
):
    created = await _create_studio_workflow(client)
    await _publish_studio_workflow(client, created)
    headers = {"Idempotency-Key": "dynamic-batch"}

    first = await client.post(
        f"{created['base_url']}/runs",
        json=first_request,
        headers=headers,
    )
    conflicting = await client.post(
        f"{created['base_url']}/runs",
        json=conflicting_request,
        headers=headers,
    )

    assert first.status_code == 202, first.text
    assert conflicting.status_code == 409, conflicting.text
    assert "idempotency" in str(conflicting.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_studio_workflow_rejects_validation_from_an_older_draft_revision(client):
    created = await _create_studio_workflow(client)
    validation = (
        await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    ).json()["data"]
    draft_url = f"{created['base_url']}/draft"
    draft = (await client.get(draft_url)).json()["data"]
    updated = await client.put(
        draft_url,
        json={
            "graph": {**draft["graph"], "name": "Updated after validation"},
            "revision": draft["revision"],
        },
    )
    assert updated.status_code == 200, updated.text

    response = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Attempt stale release",
            "expectedRevision": 2,
            "validationRunId": validation["runId"],
        },
    )
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_studio_workflow_rejects_failed_validation_for_current_revision(client):
    invalid_graph = workflow_conformance_project()
    invalid_graph["nodes"].append(dict(invalid_graph["nodes"][0]))
    created = await _create_studio_workflow(client, graph=invalid_graph)
    validation_response = await client.post(
        f"{created['base_url']}/draft/validation-runs",
        json={},
    )
    assert validation_response.status_code == 201, validation_response.text
    validation = validation_response.json()["data"]
    assert validation["status"] == "failed"
    assert validation["valid"] is False
    assert validation["errors"]

    response = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Invalid release",
            "expectedRevision": 1,
            "validationRunId": validation["runId"],
        },
    )
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_studio_workflow_versions_keep_immutable_graph_snapshots(client):
    original_graph = workflow_conformance_project()
    created = await _create_studio_workflow(client, graph=original_graph)
    first_validation = (
        await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    ).json()["data"]
    first = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "First release",
            "expectedRevision": 1,
            "validationRunId": first_validation["runId"],
        },
    )
    assert first.status_code == 201, first.text

    draft_url = f"{created['base_url']}/draft"
    draft = (await client.get(draft_url)).json()["data"]
    updated = await client.put(
        draft_url,
        json={
            "graph": {**draft["graph"], "name": "Second revision"},
            "revision": draft["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    second_validation = (
        await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    ).json()["data"]
    second = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Second release",
            "expectedRevision": 2,
            "validationRunId": second_validation["runId"],
        },
    )
    assert second.status_code == 201, second.text

    versions = (await client.get(f"{created['base_url']}/versions")).json()["data"]
    assert [(item["version"], item["graph"]["name"]) for item in versions] == [
        (2, "Second revision"),
        (1, original_graph["name"]),
    ]


@pytest.mark.asyncio
async def test_image_canvas_document_is_fixed_to_snapshot_during_validation_and_publish(
    client, db_session
):
    created = await _create_studio_workflow(client)
    workflow_id = created["workflow"]["id"]
    workflow = await db_session.scalar(
        select(StudioWorkflow).where(StudioWorkflow.id == workflow_id)
    )
    assert workflow is not None
    project = await db_session.scalar(
        select(StudioProject).where(StudioProject.id == workflow.project_id)
    )
    assert project is not None
    workspace = await db_session.scalar(
        select(StudioWorkspace).where(StudioWorkspace.id == project.workspace_id)
    )
    assert workspace is not None

    document = await image_studio_service.create_document(
        db_session,
        workspace_id=workspace.id,
        project_id=project.id,
        workflow_id=workflow.id,
        node_id="generate-hero",
        document={"version": 1, "layers": [], "settings": {}},
        updated_by_user_id="test-user",
    )
    snapshot = await image_studio_service.create_snapshot(
        db_session,
        document=document,
        expected_revision=1,
        executable_graph={"nodes": {"image": {"type": "test"}}},
        model_fingerprint="sha256:test-model",
        seed=42,
        lora_revisions=[],
        asset_ids=[],
        created_by_user_id="test-user",
    )
    graph = {
        "id": workflow.id,
        "name": "Published image workflow",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "generate-hero",
                "kind": "media",
                "capability": "generate",
                "params": {"canvasDocumentId": document.id},
                "ui": {"catalogId": "media.image-generation"},
            }
        ],
        "edges": [],
    }
    updated = await client.put(
        f"{created['base_url']}/draft",
        json={"graph": graph, "revision": 1},
    )
    assert updated.status_code == 200, updated.text

    validation = await client.post(f"{created['base_url']}/draft/validation-runs", json={})
    assert validation.status_code == 201, validation.text
    assert validation.json()["data"]["valid"] is True
    published = await client.post(
        f"{created['base_url']}/versions",
        json={
            "reason": "Fix Canvas recipe",
            "expectedRevision": 2,
            "validationRunId": validation.json()["data"]["runId"],
        },
    )

    assert published.status_code == 201, published.text
    params = published.json()["data"]["graph"]["nodes"][0]["params"]
    assert params == {"canvasSnapshotId": snapshot.id}
