import uuid

import pytest
from sqlalchemy import select

from backend.api.v1 import studio_workflows
from backend.main import app
from backend.models.agent_conversation import AgentConversation
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.intelligence import IntelligenceArtifact
from backend.models.studio import (
    StudioProject,
    StudioWorkflow,
    StudioWorkflowValidationRun,
    StudioWorkflowVersion,
    StudioWorkspace,
)
from backend.models.workflow_run import WorkflowRun
from backend.schemas.workflow import WorkflowRunSourceOutputsRequest
from backend.schemas.workflow_research import WorkflowResearchContinuationRequest
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.opencli_hda_tracer import continue_workflow_run_with_source_outputs
from backend.workflow.research_continuation import (
    continue_research_workflow_run,
    get_research_ledger,
)
from tests.integration.test_workflow_deep_research_api import _project as _research_project


def _native_research_graph(workflow_id: str) -> dict:
    return {
        "id": workflow_id,
        "name": "Conversation provenance workflow",
        "profile": "intelligence",
        "version": 1,
        "nodes": [
            {
                "id": "native-research",
                "kind": "action",
                "capability": "store",
                "params": {
                    "toolCapability": {
                        "id": "tool.intelligence.native.research",
                        "executor": {
                            "mode": "native_intelligence",
                            "params": {"action": "research"},
                        },
                    },
                    "toolParams": {
                        "fixtureId": "native-intelligence-offline-v1",
                        "sourceMode": "offline_fixture",
                    },
                },
                "ui": {"catalogId": "external.tool.capability"},
            }
        ],
        "edges": [],
        "adapters": [],
        "agentPermissions": {
            "canFetchNetwork": False,
            "canSendNotifications": False,
            "canWriteInbox": True,
        },
    }


async def _seed_scope(db_session):
    governed = Workspace(id="origin-governed", name="Origin governed", slug="origin-governed")
    studio = StudioWorkspace(id="origin-studio", name="Origin Studio", slug="origin-studio")
    user = User(id="origin-user", subject="origin-subject")
    other_user = User(id="origin-other", subject="origin-other-subject")
    project = StudioProject(
        id="origin-project",
        workspace_id=studio.id,
        name="Origin project",
        slug="origin-project",
        created_by_user_id=user.id,
    )
    workflow = StudioWorkflow(
        id="origin-workflow",
        project_id=project.id,
        name="Origin workflow",
        current_published_version=1,
    )
    validation = StudioWorkflowValidationRun(
        id="origin-validation",
        workflow_id=workflow.id,
        draft_revision=1,
        status="valid",
        valid=True,
        errors=[],
        warnings=[],
        compile_version="1.1.0",
        resolved_graph=_native_research_graph(workflow.id),
    )
    version = StudioWorkflowVersion(
        id="origin-version",
        workflow_id=workflow.id,
        version=1,
        draft_revision=1,
        graph=_native_research_graph(workflow.id),
        compile_version="1.1.0",
        validation_run_id=validation.id,
        published_by_user_id=user.id,
        reason="origin test",
    )
    binding = {
        "studio_workspace_id": studio.id,
        "project_id": project.id,
        "workflow_id": workflow.id,
    }
    conversations = [
        AgentConversation(
            id="origin-conversation-a",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding=binding,
            revision=3,
        ),
        AgentConversation(
            id="origin-conversation-b",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding=binding,
            revision=1,
        ),
        AgentConversation(
            id="origin-foreign-owner",
            workspace_id=governed.id,
            created_by_user_id=other_user.id,
            context_binding=binding,
        ),
        AgentConversation(
            id="origin-closed",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding=binding,
            status="closed",
        ),
        AgentConversation(
            id="origin-wrong-project",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding={**binding, "project_id": "another-project"},
        ),
        AgentConversation(
            id="origin-wrong-workspace",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding={**binding, "studio_workspace_id": "another-studio"},
        ),
        AgentConversation(
            id="origin-wrong-workflow",
            workspace_id=governed.id,
            created_by_user_id=user.id,
            context_binding={**binding, "workflow_id": "another-workflow"},
        ),
    ]
    db_session.add_all(
        [
            governed,
            studio,
            user,
            other_user,
            WorkspaceMembership(
                workspace_id=governed.id,
                user_id=user.id,
                role=WorkspaceRole.VIEWER,
            ),
            project,
            workflow,
            validation,
            version,
            *conversations,
        ]
    )
    await db_session.commit()
    identity = RequestIdentity(
        subject=user.subject,
        is_platform_admin=True,
        auth_method="local",
    )

    async def override_identity():
        return identity

    app.dependency_overrides[studio_workflows._optional_request_identity] = override_identity
    app.dependency_overrides[get_request_identity] = override_identity
    return {
        "governed": governed,
        "studio": studio,
        "project": project,
        "workflow": workflow,
        "version": version,
        "identity": identity,
    }


def _run_url(scope: dict) -> str:
    return (
        f"/api/v1/workspaces/{scope['studio'].id}/projects/{scope['project'].id}"
        f"/workflows/{scope['workflow'].id}/runs"
    )


def _native_session_id(workflow_id: str, run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/{workflow_id}/{run_id}"))


async def _seed_published_research_scope(db_session):
    scope = await _seed_scope(db_session)
    project = _research_project(["funding", "risk"])
    project["id"] = scope["workflow"].id
    source = next(node for node in project["nodes"] if node["id"] == "fixture-source")
    source["params"]["fixtureItems"] = source["params"]["fixtureItems"][:1]
    project["nodes"].append(_native_research_graph(scope["workflow"].id)["nodes"][0])
    project["edges"].append(
        {
            "id": "e-record-sink-native-research",
            "source": "record-sink",
            "target": "native-research",
            "sourcePort": "stored",
            "targetPort": "in",
        }
    )
    scope["version"].graph = project
    await db_session.commit()
    return scope


@pytest.mark.asyncio
async def test_published_native_artifact_has_reauthorized_conversation_origin(client, db_session):
    scope = await _seed_scope(db_session)
    response = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json={
            "inputs": {"topic": "origin"},
            "user": "studio-run-trace",
            "conversation_id": "origin-conversation-a",
        },
    )

    assert response.status_code == 202, response.text
    run_id = response.json()["data"]["runId"]
    run = await db_session.get(WorkflowRun, run_id)
    assert run is not None
    assert run.request["_serverConversationOrigin"] == {
        "conversation_id": "origin-conversation-a",
        "conversation_revision": 3,
        "governed_workspace_id": scope["governed"].id,
        "studio_workspace_id": scope["studio"].id,
        "project_id": scope["project"].id,
        "workflow_id": scope["workflow"].id,
        "user_id": "origin-user",
    }
    artifact = await db_session.scalar(
        select(IntelligenceArtifact).where(IntelligenceArtifact.kind == "research")
    )
    assert artifact is not None
    assert artifact.provenance["conversation_id"] == "origin-conversation-a"

    artifacts = await client.get(
        f"/api/v1/workspaces/{scope['studio'].id}/projects/{scope['project'].id}/artifacts",
        headers={"Authorization": "Bearer test"},
        params={"workflow_id": scope["workflow"].id, "run_id": run_id},
    )
    assert artifacts.status_code == 200, artifacts.text
    assert artifacts.json()["data"][0]["conversation_id"] == "origin-conversation-a"
    artifact_id = artifacts.json()["data"][0]["id"]
    detail = await client.get(
        f"/api/v1/workspaces/{scope['studio'].id}/projects/{scope['project'].id}/artifacts/{artifact_id}",
        headers={"Authorization": "Bearer test"},
        params={"workflow_id": scope["workflow"].id, "run_id": run_id},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["conversation_id"] == "origin-conversation-a"
    assert detail.json()["data"]["provenance"]["conversation_id"] == ("origin-conversation-a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversation_id", "expected_conversation_id"),
    [("origin-conversation-a", "origin-conversation-a"), (None, None)],
)
async def test_research_continuation_restores_persisted_origin_to_child_native_artifact(
    client, db_session, conversation_id, expected_conversation_id
):
    scope = await _seed_published_research_scope(db_session)
    run_body = {
        "inputs": {},
        "user": "studio-run-trace",
    }
    if conversation_id is not None:
        run_body["conversation_id"] = conversation_id
    started = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json=run_body,
    )
    assert started.status_code == 202, started.text
    parent_run_id = started.json()["data"]["runId"]
    parent = await db_session.get(WorkflowRun, parent_run_id)
    assert parent is not None
    persisted_origin = parent.request.get("_serverConversationOrigin")
    if expected_conversation_id is None:
        assert persisted_origin is None
    else:
        assert persisted_origin["conversation_id"] == expected_conversation_id

    ledger = await get_research_ledger(parent_run_id, session=db_session)
    assert ledger is not None
    parent_entry = ledger.entries[-1]
    assert parent_entry.researchStatus == "needs_evidence"
    assert parent_entry.proposal is not None
    continuation_body = WorkflowResearchContinuationRequest.model_validate(
        {
            "expectedRevisionId": parent_entry.revisionId,
            "proposalId": parent_entry.proposal["proposalId"],
            "idempotencyKey": "origin-followup-v1",
            "conversation_origin": {"conversation_id": "origin-conversation-b"},
            "sourceOutputs": {
                "fixture-source": [
                    {
                        "claimKey": "liquidity",
                        "statement": "Liquidity supports the synthetic market.",
                        "evidenceId": "origin-followup-risk",
                        "stance": "support",
                        "dimension": "risk",
                        "content": "Follow-up risk evidence.",
                        "url": "https://example.test/origin-followup-risk",
                    }
                ]
            },
        }
    )

    continued = await continue_research_workflow_run(
        parent_run_id,
        continuation_body,
        session=db_session,
        plugins=app.state.workflow_plugins,
    )
    assert continued is not None
    child = await db_session.get(WorkflowRun, continued.childRunId)
    assert child is not None
    if persisted_origin is None:
        assert "_serverConversationOrigin" not in child.request
    else:
        assert child.request["_serverConversationOrigin"] == persisted_origin

    child_artifact = await db_session.scalar(
        select(IntelligenceArtifact).where(
            IntelligenceArtifact.session_id
            == _native_session_id(scope["workflow"].id, continued.childRunId),
            IntelligenceArtifact.kind == "research",
        )
    )
    assert child_artifact is not None
    assert child_artifact.provenance.get("conversation_id") == expected_conversation_id


@pytest.mark.asyncio
async def test_published_origin_idempotency_and_continuation_cannot_rebind(client, db_session):
    scope = await _seed_scope(db_session)
    body = {
        "inputs": {},
        "user": "studio-run-trace",
        "idempotency_key": "origin-key",
        "conversation_id": "origin-conversation-a",
    }
    first = await client.post(_run_url(scope), headers={"Authorization": "Bearer test"}, json=body)
    replay = await client.post(_run_url(scope), headers={"Authorization": "Bearer test"}, json=body)
    conflicting = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json={**body, "conversation_id": "origin-conversation-b"},
    )
    omitted = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json={key: value for key, value in body.items() if key != "conversation_id"},
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["data"]["runId"] == replay.json()["data"]["runId"]
    assert conflicting.status_code == 409
    assert omitted.status_code == 409

    run_id = first.json()["data"]["runId"]
    continuation = WorkflowRunSourceOutputsRequest.model_validate(
        {
            "sourceOutputs": {"untrusted": [{"conversation_id": "origin-conversation-b"}]},
            "conversation_origin": {"conversation_id": "origin-conversation-b"},
        }
    )
    await continue_workflow_run_with_source_outputs(
        run_id,
        continuation,
        session=db_session,
        plugins=app.state.workflow_plugins,
    )
    run = await db_session.get(WorkflowRun, run_id)
    assert run.request["_serverConversationOrigin"]["conversation_id"] == ("origin-conversation-a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversation_id", "expected_status"),
    [
        ("origin-foreign-owner", 403),
        ("origin-closed", 409),
        ("origin-wrong-project", 409),
        ("origin-wrong-workspace", 403),
        ("origin-wrong-workflow", 409),
    ],
)
async def test_published_origin_rejects_foreign_or_inactive_bindings(
    client, db_session, conversation_id, expected_status
):
    scope = await _seed_scope(db_session)
    response = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json={
            "inputs": {},
            "user": "studio-run-trace",
            "conversation_id": conversation_id,
        },
    )
    assert response.status_code == expected_status, response.text


@pytest.mark.asyncio
async def test_oidc_identity_cannot_use_studio_conversation_origin(client, db_session):
    scope = await _seed_scope(db_session)

    async def oidc_identity():
        return RequestIdentity(subject="origin-subject", auth_method="oidc")

    app.dependency_overrides[studio_workflows._optional_request_identity] = oidc_identity
    response = await client.post(
        _run_url(scope),
        headers={"Authorization": "Bearer test"},
        json={
            "inputs": {},
            "user": "studio-run-trace",
            "conversation_id": "origin-conversation-a",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_same_artifact_id_in_two_sessions_does_not_cross_run_scope(client, db_session):
    scope = await _seed_scope(db_session)
    request = {
        "inputs": {},
        "user": "studio-run-trace",
        "conversation_id": "origin-conversation-a",
    }
    first = await client.post(
        _run_url(scope), headers={"Authorization": "Bearer test"}, json=request
    )
    second = await client.post(
        _run_url(scope), headers={"Authorization": "Bearer test"}, json=request
    )
    first_run_id = first.json()["data"]["runId"]
    second_run_id = second.json()["data"]["runId"]
    artifacts = list(
        (
            await db_session.scalars(
                select(IntelligenceArtifact).where(IntelligenceArtifact.kind == "research")
            )
        ).all()
    )
    assert len(artifacts) == 2
    assert artifacts[0].artifact_id == artifacts[1].artifact_id
    first_list = await client.get(
        f"/api/v1/workspaces/{scope['studio'].id}/projects/{scope['project'].id}/artifacts",
        headers={"Authorization": "Bearer test"},
        params={"workflow_id": scope["workflow"].id, "run_id": first_run_id},
    )
    first_public_id = first_list.json()["data"][0]["id"]
    crossed = await client.get(
        f"/api/v1/workspaces/{scope['studio'].id}/projects/{scope['project'].id}/artifacts/{first_public_id}",
        headers={"Authorization": "Bearer test"},
        params={"workflow_id": scope["workflow"].id, "run_id": second_run_id},
    )
    assert crossed.status_code == 404


@pytest.mark.asyncio
async def test_legacy_and_originless_runs_cannot_forge_artifact_conversation(client, db_session):
    scope = await _seed_scope(db_session)
    originless = await client.post(
        _run_url(scope),
        json={"inputs": {}, "user": "external-scheduler"},
    )
    assert originless.status_code == 202, originless.text
    originless_run = await db_session.get(WorkflowRun, originless.json()["data"]["runId"])
    assert "_serverConversationOrigin" not in originless_run.request
    originless_artifact = await db_session.scalar(
        select(IntelligenceArtifact).where(
            IntelligenceArtifact.session_id
            == _native_session_id(scope["workflow"].id, originless_run.id)
        )
    )
    assert originless_artifact is not None
    assert originless_artifact.provenance.get("conversation_id") is None

    graph = _native_research_graph("legacy-origin-forgery")
    graph["nodes"][0]["params"]["conversation_id"] = "origin-conversation-a"
    legacy = await client.post(
        "/api/v1/workflows/runs",
        json={
            "project": graph,
            "runId": "legacy-origin-forgery-run",
            "input": {
                "payload": {"conversation_id": "origin-conversation-a"},
                "source": "agent",
                "sourceId": "origin-conversation-a",
            },
            "conversation_origin": {"conversation_id": "origin-conversation-a"},
            "_serverConversationOrigin": {
                "conversation_id": "origin-conversation-a"
            },
        },
    )
    assert legacy.status_code == 202, legacy.text
    legacy_artifact = await db_session.scalar(
        select(IntelligenceArtifact).where(
            IntelligenceArtifact.session_id
            == _native_session_id("legacy-origin-forgery", "legacy-origin-forgery-run")
        )
    )
    assert legacy_artifact is not None
    assert legacy_artifact.provenance.get("conversation_id") is None
