"""Published native bindings must not break the authorized chat catalog."""

import pytest

from backend import ws_agent_manager
from backend.main import app
from backend.models.edge_node import EdgeNode
from backend.models.identity import Team, User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.operations_agent import (
    OperationsAgentIdentity,
    PublishedOperationsAgentVersion,
)
from backend.security.identity import RequestIdentity, get_request_identity


async def seed_agent(db, *, slug="native", configuration=None):
    user = User(subject=f"{slug}@example.test")
    workspace = Workspace(name=slug, slug=slug)
    db.add_all([user, workspace])
    await db.flush()
    team = Team(workspace_id=workspace.id, name="Agent owners", slug="owners")
    db.add_all(
        [
            team,
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.ADMIN
            ),
        ]
    )
    await db.flush()
    agent = OperationsAgentIdentity(
        workspace_id=workspace.id,
        owning_team_id=team.id,
        name=f"{slug} assistant",
        current_published_version=1,
    )
    db.add(agent)
    await db.flush()
    version = PublishedOperationsAgentVersion(
        operations_agent_id=agent.id,
        version=1,
        draft_revision=1,
        instructions="Inspect authorized project information.",
        model_configuration=configuration
        if configuration is not None
        else {
            "runtime_binding": {
                "schema_version": "agent.runtime-binding.v2",
                "workflow": "assistant",
                "preferred_agent_urls": ["http://private-runner.example:19823"],
                "preferred_runtimes": ["codex"],
            },
            "agent_contract": {
                "schema_version": "agent.contract.v2",
                "role": "assistant",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "state_schema": {"type": "object"},
                "required_capabilities": ["streaming", "workspace_read"],
            },
        },
        published_by_user_id=user.id,
        reason="Test published native configuration",
    )
    db.add(version)
    await db.commit()
    return user, workspace, agent, version


@pytest.mark.parametrize(
    ("connected", "capabilities", "reason"),
    [
        (True, ["streaming", "workspace_read", "resumable"], "native_session_contract_unavailable"),
        (False, ["streaming", "workspace_read"], "runtime_node_unavailable"),
        (True, ["streaming"], "runtime_node_unavailable"),
    ],
)
async def test_native_v2_catalog_uses_capability_selection_without_claiming_chat_resume(
    client, db_session, monkeypatch, connected, capabilities, reason
):
    user, workspace, agent, _version = await seed_agent(db_session)
    node = EdgeNode(
        url="http://private-runner.example:19823",
        protocol="ws",
        status="online",
        runtimes=["", " ", "codex"],
        runtime_capabilities={
            "": capabilities,
            " ": capabilities,
            "codex": ["", " ", *capabilities],
        },
    )
    db_session.add(node)
    await db_session.commit()
    monkeypatch.setattr(ws_agent_manager, "is_connected", lambda _url: connected)

    async def identity():
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_request_identity] = identity
    response = await client.get(
        "/api/v1/chat/execution-targets", params={"workspace_id": workspace.id}
    )
    assert response.status_code == 200
    (target,) = response.json()["data"]["targets"]
    assert target["id"] == f"native:{agent.id}"
    assert target["readiness"]["status"] == "blocked"
    assert target["readiness"]["reason_code"] == reason
    assert target["setup_url"] == (
        "/providers" if reason == "native_session_contract_unavailable" else "/nodes"
    )
    assert target["runtime"]["resume_by_id"] is False
    assert "" not in target["runtime"]["capabilities"]
    assert " " not in target["runtime"]["capabilities"]
    assert target["runtime"]["name"] == (
        "codex" if reason == "native_session_contract_unavailable" else None
    )
    assert "private-runner" not in response.text
    assert "agent_url" not in response.text
    assert node.id not in response.text
    creation = await client.post(
        "/api/v1/chat/sessions",
        json={"workspace_id": workspace.id, "execution_target_id": target["id"]},
    )
    assert creation.status_code == 409
    assert creation.json()["detail"]["code"] == "native_runtime_blocked"


@pytest.mark.parametrize("configuration", [{}, {"runtime_binding": {"schema_version": "old"}}])
async def test_incomplete_published_binding_has_repair_reason_and_workspace_isolation(
    client, db_session, configuration
):
    user, workspace, agent, _version = await seed_agent(db_session, configuration=configuration)
    _other_user, other_workspace, other_agent, _other_version = await seed_agent(
        db_session, slug="private"
    )

    async def identity():
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_request_identity] = identity
    response = await client.get(
        "/api/v1/chat/execution-targets", params={"workspace_id": workspace.id}
    )
    assert response.status_code == 200
    (target,) = response.json()["data"]["targets"]
    assert target["id"] == f"native:{agent.id}"
    assert target["readiness"]["reason_code"] == "runtime_binding_invalid"
    assert target["setup_url"] == "/operations-agents"
    assert other_agent.id not in response.text
    forbidden = await client.get(
        "/api/v1/chat/execution-targets", params={"workspace_id": other_workspace.id}
    )
    assert forbidden.status_code == 403
