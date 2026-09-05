from __future__ import annotations

import pytest

from backend.api.v1 import chat
from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import agent_conversation_service
from tests.fixtures.workflow_conformance import workflow_conformance_project


def _project_args() -> dict:
    graph = workflow_conformance_project()
    graph["name"] = "OpenAlice journey draft"
    return {
        "project": {
            "name": "OpenAlice Journey",
            "slug": "openalice-journey",
            "description": "Created through a confirmed Agent proposal",
            "app_type": "agent",
        },
        "workflow": {
            "name": "OpenAlice Primary Workflow",
            "description": "Remains an unpublished draft",
            "graph": graph,
        },
    }


@pytest.mark.asyncio
async def test_agent_project_creation_confirm_reload_and_continue_journey(
    client,
    db_session,
    monkeypatch,
):
    subject = "openalice-journey@example.test"
    identity = RequestIdentity(subject=subject)
    user = User(subject=subject, email=subject, display_name="OpenAlice Journey Admin")
    workspace = Workspace(name="OpenAlice Journey", slug="openalice-journey")
    db_session.add_all([user, workspace])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.commit()

    async def identity_override():
        return identity

    app.dependency_overrides[get_request_identity] = identity_override
    calls = 0

    async def governed_chat(
        model_db,
        body,
        request_identity,
        *,
        tool_trace=None,
        proposal_provenance=None,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert request_identity == identity
            assert body.workspace_id == workspace.id
            assert proposal_provenance is not None
            proposal = await chat._build_proposal(
                model_db,
                "create_project",
                _project_args(),
                identity=request_identity,
                workspace_id=body.workspace_id,
                provenance=proposal_provenance,
            )
            if tool_trace is not None:
                tool_trace.append({"name": "create_project", "kind": "write", "status": "proposal"})
            return ApiResponse.ok(chat.ChatReply(type="proposal", proposal=proposal))

        assert body.context == {
            "surface": "studio",
            "project_id": confirmed["project_id"],
            "workflow_id": confirmed["workflow_id"],
        }
        return ApiResponse.ok(chat.ChatReply(type="message", content="Continued in project"))

    monkeypatch.setattr(agent_conversation_service.chat, "run_chat_request", governed_chat)

    created = await client.post(
        "/api/v1/chat/sessions",
        json={"workspace_id": workspace.id, "context": {"surface": "studio"}},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["data"]["id"]

    proposed = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "create-project",
            "content": "Create the OpenAlice project",
            "context": {"surface": "studio"},
        },
    )
    assert proposed.status_code == 200, proposed.text
    proposal_turn = proposed.json()["data"]["turn"]
    assert proposal_turn["status"] == "proposal"
    proposal = proposal_turn["response"]["proposal"]
    assert proposal["workspace_id"] == workspace.id
    assert proposal["work_item_id"]
    assert proposal["proposal_version"]

    confirmation = await client.post("/api/v1/chat/confirm", json={"proposal": proposal})
    assert confirmation.status_code == 200, confirmation.text
    confirmed = confirmation.json()["data"]
    assert confirmed["applied"] is True
    assert confirmed["conversation_id"] == conversation_id
    assert confirmed["conversation_turn_id"] == proposal_turn["id"]
    assert confirmed["draft_revision"] == 1

    draft = await client.get(
        f"/api/v1/workspaces/{workspace.id}/projects/{confirmed['project_id']}"
        f"/workflows/{confirmed['workflow_id']}/draft"
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["data"]["revision"] == 1
    assert draft.json()["data"]["graph"]["id"] == confirmed["workflow_id"]

    restored = await client.get(f"/api/v1/chat/sessions/{conversation_id}")
    assert restored.status_code == 200, restored.text
    restored_session = restored.json()["data"]
    assert restored_session["context_binding"] == {
        "surface": "studio",
        "project_id": confirmed["project_id"],
        "workflow_id": confirmed["workflow_id"],
    }
    restored_turn = restored_session["turns"][0]
    assert restored_turn["status"] == "completed"
    assert restored_turn["response"]["type"] == "message"
    assert restored_turn["response"]["proposal"] is None
    assert restored_turn["response"]["execution"] == {
        "status": "applied",
        "result": confirmed,
    }

    inbox = await client.get(
        f"/api/v1/workspaces/{workspace.id}/operations-inbox",
        params={"type": "change_proposal"},
    )
    assert inbox.status_code == 200, inbox.text
    items = inbox.json()["data"]
    assert len(items) == 1
    assert items[0]["status"] == "resolved"
    assert items[0]["evidence"]["conversation_id"] == conversation_id
    assert items[0]["evidence"]["project_id"] == confirmed["project_id"]
    assert items[0]["evidence"]["workflow_id"] == confirmed["workflow_id"]
    assert items[0]["evidence"]["draft_revision"] == 1
    assert items[0]["evidence"]["execution"]["result"] == confirmed

    continued = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={
            "request_id": "continue-project",
            "content": "Continue editing the confirmed project",
            "context": restored_session["context_binding"],
        },
    )
    assert continued.status_code == 200, continued.text
    continued_turn = continued.json()["data"]["turn"]
    assert continued_turn["status"] == "completed"
    assert continued_turn["response"]["content"] == "Continued in project"
    assert calls == 2
