from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend import ws_agent_manager
from backend.api.v1.operations_agents import router
from backend.database import get_db
from backend.models.agent_conversation import AgentConversation, AgentConversationTurn
from backend.models.automation import Automation
from backend.models.edge_node import EdgeNode
from backend.models.identity import Team, User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.operations_agent import (
    AgentPermissionProfile,
    OperationsAgentIdentity,
    OperationsAgentRun,
    PublishedOperationsAgentVersion,
)
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services.agent_work_health import get_agent_work_health
from backend.services.automation_schedule_service import create_bound_automation_run

PROJECT_ID = "12345678-1234-1234-1234-123456789012"


def _model_configuration() -> dict:
    return {
        "agent_contract": {
            "schema_version": "agent.contract.v2",
            "role": "operations_reviewer",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "state_schema": {"type": "object"},
            "required_capabilities": ["streaming"],
            "tool_policy": {},
            "budget": {},
            "quality_gates": [],
            "evidence_requirements": [],
        },
        "runtime_binding": {
            "schema_version": "agent.runtime-binding.v2",
            "workflow": "operations-agent",
            "preferred_agent_urls": ["http://agent-health.test:19823"],
            "preferred_runtimes": ["pi"],
            "model_binding": None,
            "config": {},
        },
    }


async def _seed_health_records(db_session, role: WorkspaceRole = WorkspaceRole.ADMIN):
    user = User(subject=f"health-{role.value}")
    workspace = Workspace(name="Health workspace", slug=f"health-{role.value}")
    db_session.add_all((user, workspace))
    await db_session.flush()
    team = Team(workspace_id=workspace.id, name="Operations", slug="operations")
    db_session.add_all(
        (
            team,
            WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role),
            EdgeNode(
                url="http://agent-health.test:19823",
                label="Health runtime",
                protocol="ws",
                mode="bridge",
                node_type="shell",
                status="online",
                runtimes=["pi"],
                runtime_capabilities={"pi": ["streaming"]},
            ),
        )
    )
    await db_session.flush()
    agent = OperationsAgentIdentity(
        workspace_id=workspace.id,
        owning_team_id=team.id,
        name="Health agent",
        current_profile_version=1,
        current_published_version=1,
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add_all(
        (
            PublishedOperationsAgentVersion(
                operations_agent_id=agent.id,
                version=1,
                draft_revision=1,
                instructions="Inspect work health",
                model_configuration=_model_configuration(),
                tool_configuration={},
                published_by_user_id=user.id,
                reason="Health contract",
            ),
            AgentPermissionProfile(
                operations_agent_id=agent.id,
                version=1,
                mode="observe_only",
                tool_scope=[],
                resource_scope=[],
                action_scope=[],
                assigned_by_user_id=user.id,
                reason="Health contract",
            ),
        )
    )
    automation = Automation(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        operations_agent_version=1,
        name="Daily health",
        prompt="Inspect the project",
        executor="pi",
        schedule="daily@09:00",
        timezone="UTC",
        session_mode="fresh",
        approval_mode="observe_only",
        project={"project_id": PROJECT_ID},
        enabled=True,
        created_by_user_id=user.id,
    )
    conversation = AgentConversation(
        workspace_id=workspace.id,
        title="Project investigation",
        created_by_user_id=user.id,
        context_binding={"project_id": PROJECT_ID},
        status="active",
    )
    db_session.add_all((automation, conversation))
    await db_session.flush()
    failed = OperationsAgentRun(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        published_version=1,
        profile_version=1,
        trigger_type="scheduled",
        trigger_reference=f"automation:{automation.id}:20260906T0900Z",
        automation_id=automation.id,
        automation_revision=automation.revision,
        automation_snapshot={"id": automation.id, "revision": automation.revision},
        scheduled_for=datetime(2026, 9, 6, 9, tzinfo=UTC),
        schedule_timezone="UTC",
        target_resource_type="automation",
        target_resource_id=automation.id,
        input_payload={},
        state_payload={},
        execution_binding={
            "runtime": "pi",
            "agent_url": "http://agent-health.test:19823",
        },
        evidence_payload={
            "events": [
                {
                    "type": "error",
                    "payload": {"error_type": "missing_binary"},
                }
            ]
        },
        error_message="Pi executable is not installed",
        status="failed",
        started_by_user_id=user.id,
    )
    paused = OperationsAgentRun(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        published_version=1,
        profile_version=1,
        trigger_type="manual",
        target_resource_type="project",
        target_resource_id=PROJECT_ID,
        input_payload={},
        state_payload={},
        status="paused",
        started_by_user_id=user.id,
    )
    turn = AgentConversationTurn(
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        sequence=1,
        request_id="health-proposal",
        user_content="Inspect this project",
        response={"summary": "Review required"},
        context_binding={"project_id": PROJECT_ID},
        tool_trace=[],
        status="proposal",
    )
    db_session.add_all((failed, paused, turn))
    await db_session.commit()
    return user, workspace, agent, automation


async def test_health_combines_runtime_failure_pause_and_conversation_evidence(
    db_session,
    monkeypatch,
):
    _, workspace, _, automation = await _seed_health_records(db_session)
    monkeypatch.setattr(ws_agent_manager, "is_connected", lambda _url: True)

    health = await get_agent_work_health(
        db_session,
        workspace_id=workspace.id,
        project_id=PROJECT_ID,
        can_run=True,
        can_manage=True,
        now=datetime(2026, 9, 6, 10, tzinfo=UTC),
    )

    items = {item.kind: item for item in health.items}
    assert items["automation"].state == "failed"
    assert items["automation"].reason_code == "runtime_not_installed"
    assert items["automation"].binding.published_version == 1
    assert items["automation"].binding.automation_revision == automation.revision
    assert {action.kind for action in items["automation"].actions} == {
        "retry_automation",
        "configure",
    }
    assert items["run"].state == "paused"
    assert items["run"].resume_supported is False
    assert all(action.kind != "retry_automation" for action in items["run"].actions)
    assert items["conversation"].state == "blocked"
    assert items["conversation"].reason_code == "proposal_review_required"
    assert [action.kind for action in items["conversation"].actions] == [
        "open_conversation"
    ]
    assert health.counts["needs_attention"] == 3


async def test_manual_run_keeps_binding_and_does_not_claim_scheduled_occurrence(
    db_session,
    monkeypatch,
):
    user, _, agent, automation = await _seed_health_records(db_session)
    monkeypatch.setattr(ws_agent_manager, "is_connected", lambda _url: True)
    scheduled_for = datetime(2026, 9, 7, 9, tzinfo=UTC)

    manual, manual_created = await create_bound_automation_run(
        db_session,
        automation,
        trigger_type="manual",
        started_by_user_id=user.id,
    )
    scheduled, scheduled_created = await create_bound_automation_run(
        db_session,
        automation,
        trigger_type="scheduled",
        started_by_user_id=user.id,
        scheduled_for=scheduled_for,
    )

    assert manual_created is True
    assert scheduled_created is True
    assert manual.operations_agent_id == scheduled.operations_agent_id == agent.id
    assert manual.published_version == scheduled.published_version == 1
    assert manual.automation_revision == scheduled.automation_revision == automation.revision
    assert manual.trigger_reference is None
    assert manual.scheduled_for is None
    assert scheduled.trigger_reference == f"automation:{automation.id}:20260907T0900Z"
    assert scheduled.scheduled_for == scheduled_for


async def test_health_surfaces_configuration_permission_and_runtime_failure_blockers(
    db_session,
    monkeypatch,
):
    user, workspace, agent, automation = await _seed_health_records(db_session)
    monkeypatch.setattr(ws_agent_manager, "is_connected", lambda _url: True)
    automation.operations_agent_version = 2
    permission_blocked = Automation(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        operations_agent_version=1,
        name="Permission mismatch",
        prompt="Inspect the project",
        executor="pi",
        schedule="hourly",
        timezone="UTC",
        session_mode="fresh",
        approval_mode="suggest_changes",
        project={"project_id": PROJECT_ID},
        enabled=True,
        created_by_user_id=user.id,
    )
    failed_run = OperationsAgentRun(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        published_version=1,
        profile_version=1,
        trigger_type="manual",
        target_resource_type="project",
        target_resource_id=PROJECT_ID,
        input_payload={},
        state_payload={},
        execution_binding={
            "runtime": "pi",
            "agent_url": "http://agent-health.test:19823",
        },
        evidence_payload={
            "events": [
                {
                    "type": "error",
                    "payload": {"error_type": "RuntimeInvocationError"},
                }
            ]
        },
        error_message="Runtime process exited with code 2",
        status="failed",
        started_by_user_id=user.id,
    )
    db_session.add_all((permission_blocked, failed_run))
    await db_session.commit()

    health = await get_agent_work_health(
        db_session,
        workspace_id=workspace.id,
        project_id=PROJECT_ID,
        can_run=True,
        can_manage=True,
        now=datetime(2026, 9, 6, 10, tzinfo=UTC),
    )

    reasons = {item.title: item.reason_code for item in health.items}
    assert reasons["Daily health"] == "runtime_not_configured"
    assert reasons["Permission mismatch"] == "permission_profile_required"
    assert any(item.kind == "run" and item.reason_code == "run_failed" for item in health.items)


async def test_viewer_health_api_exposes_no_mutating_actions(db_session, monkeypatch):
    user, workspace, _, _ = await _seed_health_records(db_session, WorkspaceRole.VIEWER)
    monkeypatch.setattr(ws_agent_manager, "is_connected", lambda _url: True)
    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield db_session

    async def override_identity():
        return RequestIdentity(subject=user.subject)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_request_identity] = override_identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/workspaces/{workspace.id}/operations-agents/work-health",
            params={"project_id": PROJECT_ID},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["permissions"] == {"can_run": False, "can_manage": False}
    actions = [action for item in data["items"] for action in item["actions"]]
    assert {action["kind"] for action in actions} == {"open_conversation"}
