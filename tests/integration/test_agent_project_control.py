from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.api.v1 import agent_conversations, chat
from backend.api.v1.studio_schemas import DraftUpdate, ProjectBootstrapCreate
from backend.control.agent_control import ProposalProvenance, agent_control_service
from backend.models.agent_conversation import (
    AgentConversation,
    AgentConversationTurn,
    AgentConversationTurnStatus,
)
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.operations_work_item import OperationsWorkItem, WorkItemStatus
from backend.models.studio import StudioProject, StudioWorkflow, StudioWorkflowDraft
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity
from backend.services import agent_conversation_service, agent_project_service
from tests.fixtures.workflow_conformance import workflow_conformance_project


async def _actor(
    db_session,
    *,
    subject: str = "agent-project@example.test",
    role: WorkspaceRole = WorkspaceRole.ADMIN,
):
    user = User(subject=subject, email=subject, display_name="Agent Project Admin")
    workspace = Workspace(name="Agent Project Workspace", slug=subject.split("@")[0])
    db_session.add_all([user, workspace])
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=role,
        )
    )
    await db_session.flush()
    return RequestIdentity(subject=subject), user, workspace


def _create_args(*, slug: str = "agent-created") -> dict:
    graph = workflow_conformance_project()
    graph["name"] = "Agent-created draft"
    return {
        "project": {
            "name": "Agent-created Project",
            "slug": slug,
            "description": "Created only after confirmation",
            "app_type": "agent",
        },
        "workflow": {
            "name": "Primary Agent Workflow",
            "description": "Still a draft",
            "graph": graph,
        },
    }


def _tool_parameters(name: str) -> dict:
    return next(
        tool["function"]["parameters"] for tool in chat.TOOLS if tool["function"]["name"] == name
    )


def test_project_write_tools_publish_the_canonical_workflow_project_schema():
    expected = workflow_schemas.WorkflowProject.model_json_schema()
    expected_defs = expected.pop("$defs")

    create_parameters = _tool_parameters("create_project")
    create_graph = create_parameters["properties"]["workflow"]["properties"]["graph"]
    update_parameters = _tool_parameters("update_workflow_draft")
    update_graph = update_parameters["properties"]["graph"]

    assert create_graph == expected
    assert update_graph == expected
    assert create_parameters["$defs"] == expected_defs
    assert update_parameters["$defs"] == expected_defs
    assert {"id", "name", "profile", "nodes"}.issubset(create_graph["required"])
    assert create_graph["properties"]["profile"]["enum"] == [
        "intelligence",
        "agent-debug",
        "sdk-dev",
    ]


@pytest.mark.asyncio
async def test_agent_graph_validation_rejects_malformed_and_accepts_incomplete_draft(
    db_session,
):
    identity, _, workspace = await _actor(
        db_session,
        subject="agent-graph-validation@example.test",
    )
    malformed = _create_args(slug="malformed-agent-graph")
    malformed["workflow"]["graph"] = {
        "id": "malformed",
        "name": "Missing profile and valid nodes",
        "nodes": [],
    }

    with pytest.raises(HTTPException) as rejected:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="create_project",
            args=malformed,
            origin="chat",
        )
    assert rejected.value.status_code == 422
    assert "Invalid Agent Workflow graph" in str(rejected.value.detail)
    assert await db_session.scalar(select(OperationsWorkItem.id)) is None
    assert await db_session.scalar(select(StudioProject.id)) is None

    incomplete = _create_args(slug="incomplete-agent-draft")
    incomplete["workflow"]["graph"] = {
        "id": "incomplete-draft",
        "name": "Structurally valid incomplete draft",
        "profile": "agent-debug",
        "nodes": [
            {
                "id": "draft-agent",
                "kind": "agent",
                "capability": "summarize",
            }
        ],
    }
    proposal = await agent_control_service.create_proposal(
        db_session,
        workspace_id=workspace.id,
        identity=identity,
        action_name="create_project",
        args=incomplete,
        origin="chat",
    )

    assert proposal.preview.args["workflow"]["graph"]["nodes"][0]["params"] == {}
    assert proposal.preview.args["workflow"]["graph"]["edges"] == []
    assert proposal.preview.args["workflow"]["graph"]["adapters"] == []
    assert await db_session.scalar(select(StudioProject.id)) is None


@pytest.mark.asyncio
async def test_agent_draft_update_rejects_malformed_graph_before_proposal(db_session):
    identity, user, workspace = await _actor(
        db_session,
        subject="agent-draft-graph-validation@example.test",
    )
    created = await agent_project_service.create_project_bundle(
        db_session,
        workspace_id=workspace.id,
        body=ProjectBootstrapCreate.model_validate(_create_args(slug="draft-graph-validation")),
        actor_user_id=user.id,
    )
    await db_session.flush()

    with pytest.raises(HTTPException) as rejected:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="update_workflow_draft",
            args={
                "project_id": created.project.id,
                "workflow_id": created.workflow.id,
                "revision": created.draft.revision,
                "graph": {"id": created.workflow.id, "name": "Malformed", "nodes": []},
            },
            origin="chat",
        )

    assert rejected.value.status_code == 422
    assert "Invalid Agent Workflow graph" in str(rejected.value.detail)
    assert await db_session.scalar(select(OperationsWorkItem.id)) is None


@pytest.mark.asyncio
async def test_confirmed_project_creation_persists_real_ids_and_continuation_context(
    db_session,
):
    identity, user, workspace = await _actor(db_session)
    conversation = await agent_conversation_service.create_conversation(
        db_session,
        identity,
        workspace_id=workspace.id,
        title="Create a project",
        context={"surface": "agent-dock"},
    )
    unrelated_turn = AgentConversationTurn(
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        sequence=1,
        request_id="prior-message",
        user_content="Inspect the workspace",
        response={"type": "message", "content": "Workspace inspected", "proposal": None},
        context_binding={"surface": "agent-dock"},
        tool_trace=[{"name": "list_projects", "kind": "read", "status": "completed"}],
        status=AgentConversationTurnStatus.COMPLETED.value,
    )
    turn = AgentConversationTurn(
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        sequence=2,
        request_id="create-project",
        user_content="Create the project",
        context_binding={"surface": "agent-dock"},
        tool_trace=[],
        status=AgentConversationTurnStatus.RUNNING.value,
    )
    db_session.add_all([unrelated_turn, turn])
    await db_session.flush()

    recorded = await agent_control_service.create_proposal(
        db_session,
        workspace_id=workspace.id,
        identity=identity,
        action_name="create_project",
        args=_create_args(),
        origin="agent_conversation",
        provenance=ProposalProvenance(
            conversation_id=conversation.id,
            turn_id=turn.id,
            context={"surface": "agent-dock"},
        ),
    )
    assert await db_session.scalar(select(StudioProject.id)) is None
    turn.response = {
        "type": "proposal",
        "content": None,
        "proposal": {
            "tool": recorded.preview.action_name,
            "args": recorded.preview.args,
            "summary": recorded.preview.summary,
            "diff": recorded.preview.diff,
            "work_item_id": recorded.work_item_id,
            "workspace_id": workspace.id,
            "proposal_version": recorded.proposal_version,
        },
    }
    turn.status = AgentConversationTurnStatus.PROPOSAL.value
    await db_session.flush()

    result = await agent_control_service.execute_confirmed(
        db_session,
        workspace_id=workspace.id,
        identity=identity,
        work_item_id=recorded.work_item_id,
        proposal_version=recorded.proposal_version,
        confirmation_path="test.confirm",
        expected_action="create_project",
    )

    assert result["workspace_id"] == workspace.id
    assert result["draft_revision"] == 1
    project = await db_session.get(StudioProject, result["project_id"])
    workflow = await db_session.get(StudioWorkflow, result["workflow_id"])
    draft = await db_session.scalar(
        select(StudioWorkflowDraft).where(StudioWorkflowDraft.workflow_id == result["workflow_id"])
    )
    assert project is not None and project.created_by_user_id == user.id
    assert project.primary_workflow_id == result["workflow_id"]
    assert workflow is not None and workflow.current_published_version is None
    assert draft is not None and draft.revision == 1
    assert draft.graph["id"] == workflow.id

    item = await db_session.get(OperationsWorkItem, recorded.work_item_id)
    assert item is not None and item.status == WorkItemStatus.RESOLVED
    assert item.evidence["conversation_id"] == conversation.id
    assert item.evidence["project_id"] == project.id
    assert item.evidence["workflow_id"] == workflow.id
    assert item.evidence["draft_revision"] == 1
    assert item.evidence["execution"]["result"] == result

    session_response = await agent_conversations.get_session(
        conversation.id,
        after_sequence=0,
        limit=50,
        identity=identity,
        db=db_session,
    )
    session = session_response.data
    assert session is not None
    assert len(session.turns) == 2
    restored_unrelated, restored_confirmation = session.turns
    assert restored_unrelated.id == unrelated_turn.id
    assert restored_unrelated.status == AgentConversationTurnStatus.COMPLETED.value
    assert restored_unrelated.response == unrelated_turn.response
    assert restored_unrelated.tool_trace == unrelated_turn.tool_trace
    assert restored_confirmation.status == AgentConversationTurnStatus.COMPLETED.value
    assert restored_confirmation.response is not None
    assert restored_confirmation.response["type"] == "message"
    assert restored_confirmation.response["proposal"] is None
    execution = restored_confirmation.response["execution"]
    assert execution["status"] == "applied"
    assert execution["result"] == result
    assert execution["result"]["project_id"] == project.id
    assert execution["result"]["workflow_id"] == workflow.id
    assert execution["result"]["draft_revision"] == 1

    persisted_conversation, _ = await agent_conversation_service.get_conversation(
        db_session,
        identity,
        conversation.id,
    )
    assert persisted_conversation.context_binding == {
        "surface": "agent-dock",
        "project_id": project.id,
        "workflow_id": workflow.id,
    }
    assert (
        await agent_conversation_service.validate_context_binding(
            db_session,
            workspace.id,
            persisted_conversation.context_binding,
        )
        == persisted_conversation.context_binding
    )

    async def reply_runner(*args, **kwargs):
        return ApiResponse.ok(chat.ChatReply(type="message", content="continued"))

    _, next_turn = await agent_conversation_service.send_message(
        db_session,
        identity,
        conversation.id,
        request_id="continue-project",
        content="Continue in the created project",
        context={"project_id": project.id, "workflow_id": workflow.id},
        chat_runner=reply_runner,
    )
    assert next_turn.status == AgentConversationTurnStatus.COMPLETED.value
    assert next_turn.context_binding == {
        "project_id": project.id,
        "workflow_id": workflow.id,
    }

    with pytest.raises(HTTPException) as repeated:
        await agent_control_service.execute_confirmed(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            work_item_id=recorded.work_item_id,
            proposal_version=recorded.proposal_version,
            confirmation_path="test.confirm",
        )
    assert repeated.value.status_code == 409


@pytest.mark.asyncio
async def test_draft_update_rejects_stale_proposal_and_reads_are_workspace_scoped(db_session):
    identity, user, workspace = await _actor(db_session, subject="draft-agent@example.test")
    created = await agent_project_service.create_project_bundle(
        db_session,
        workspace_id=workspace.id,
        body=ProjectBootstrapCreate.model_validate(_create_args(slug="draft-agent")),
        actor_user_id=user.id,
    )
    await db_session.flush()
    changed_graph = {**created.draft.graph, "name": "Proposed graph"}
    proposal = await agent_control_service.create_proposal(
        db_session,
        workspace_id=workspace.id,
        identity=identity,
        action_name="update_workflow_draft",
        args={
            "project_id": created.project.id,
            "workflow_id": created.workflow.id,
            "revision": 1,
            "graph": changed_graph,
        },
        origin="chat",
    )

    manual_graph = {**created.draft.graph, "name": "Manual concurrent edit"}
    manual = await agent_project_service.update_workflow_draft(
        db_session,
        workspace_id=workspace.id,
        project_id=created.project.id,
        workflow_id=created.workflow.id,
        body=DraftUpdate.model_validate({"revision": 1, "graph": manual_graph}),
        actor_user_id="local-development-user",
    )
    assert manual.revision == 2

    with pytest.raises(HTTPException) as stale:
        await agent_control_service.execute_confirmed(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            work_item_id=proposal.work_item_id,
            proposal_version=proposal.proposal_version,
            confirmation_path="test.confirm",
        )
    assert stale.value.status_code == 409
    await db_session.refresh(manual)
    assert manual.revision == 2
    assert manual.graph["name"] == "Manual concurrent edit"

    projects = await chat._run_read_tool(
        db_session,
        "list_projects",
        {},
        identity=identity,
        workspace_id=workspace.id,
    )
    workflows = await chat._run_read_tool(
        db_session,
        "list_workflows",
        {"project_id": created.project.id},
        identity=identity,
        workspace_id=workspace.id,
    )
    draft = await chat._run_read_tool(
        db_session,
        "get_workflow_draft",
        {"project_id": created.project.id, "workflow_id": created.workflow.id},
        identity=identity,
        workspace_id=workspace.id,
    )
    assert [row["id"] for row in projects] == [created.project.id]
    assert [row["id"] for row in workflows] == [created.workflow.id]
    assert draft["revision"] == 2
    assert draft["is_published_version"] is False

    _, _, other_workspace = await _actor(db_session, subject="other-workspace@example.test")
    with pytest.raises(HTTPException) as cross_workspace:
        await chat._run_read_tool(
            db_session,
            "get_workflow_draft",
            {"project_id": created.project.id, "workflow_id": created.workflow.id},
            identity=identity,
            workspace_id=other_workspace.id,
        )
    assert cross_workspace.value.status_code == 403


@pytest.mark.asyncio
async def test_proposal_provenance_cannot_name_another_users_conversation(db_session):
    identity, _, workspace = await _actor(db_session, subject="owner-a@example.test")
    other = User(subject="owner-b@example.test", email="owner-b@example.test")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=other.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    conversation = AgentConversation(
        workspace_id=workspace.id,
        created_by_user_id=other.id,
        title="Other owner's session",
        context_binding={},
        status="active",
    )
    db_session.add(conversation)
    await db_session.flush()
    turn = AgentConversationTurn(
        conversation_id=conversation.id,
        workspace_id=workspace.id,
        sequence=1,
        request_id="other-turn",
        user_content="Other request",
        response=None,
        context_binding={},
        tool_trace=[],
        status=AgentConversationTurnStatus.RUNNING.value,
    )
    db_session.add(turn)
    await db_session.flush()

    with pytest.raises(HTTPException) as forged:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="create_project",
            args=_create_args(slug="forged-origin"),
            origin="agent_conversation",
            provenance=ProposalProvenance(
                conversation_id=conversation.id,
                turn_id=turn.id,
                context={},
            ),
        )
    assert forged.value.status_code == 409
    assert (
        await db_session.scalar(
            select(StudioProject.id).where(StudioProject.slug == "forged-origin")
        )
        is None
    )


@pytest.mark.asyncio
async def test_viewer_cannot_propose_project_mutation(db_session):
    identity, _, workspace = await _actor(
        db_session,
        subject="project-viewer@example.test",
        role=WorkspaceRole.VIEWER,
    )

    with pytest.raises(HTTPException) as denied:
        await agent_control_service.create_proposal(
            db_session,
            workspace_id=workspace.id,
            identity=identity,
            action_name="create_project",
            args=_create_args(slug="viewer-forbidden"),
            origin="chat",
        )
    assert denied.value.status_code == 403
    assert (
        await db_session.scalar(
            select(StudioProject.id).where(StudioProject.slug == "viewer-forbidden")
        )
        is None
    )
